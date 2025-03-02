# api/services/rate_limiter.py
import time
import asyncio

from ..config import config
from ..utils import logger

class RateLimiter:
    """
    Distributed rate limiter that allows `max_requests` in `window_ms` timeframe.
    Uses Redis for distributed rate limiting if REDIS_URL is set in config,
    otherwise falls back to in-memory rate limiting.
    """
    def __init__(self, max_requests: int, window_ms: int):
        self.max_requests = max_requests
        self.window_ms = window_ms
        self.queue = []
        self.redis_client = None
        if config.redis_url:
            try:
                import redis.asyncio as redis_asyncio
                self.redis_client = redis_asyncio.from_url(
                    config.redis_url,
                    decode_responses=True,
                    retry_on_timeout=True,
                    socket_connect_timeout=5
                )
                logger.debug("Distributed rate limiter enabled with Redis.", extra={"redis_url": config.redis_url})
            except Exception as e:
                if config.enable_debug:
                    logger.exception("Failed to initialize Redis client for rate limiting. Falling back to in-memory.")
                else:
                    logger.error("Failed to initialize Redis client for rate limiting. Falling back to in-memory.", extra={"error": str(e)})

    async def safe_execute(self, method_name: str, *args, **kwargs):
        """
        Executes a redis method safely by catching 'closed' connection errors and reinitializing the client.
        """
        if not self.redis_client:
            raise Exception("Redis client not initialized")
        try:
            return await getattr(self.redis_client, method_name)(*args, **kwargs)
        except RuntimeError as e:
            if "closed" in str(e):
                try:
                    import redis.asyncio as redis_asyncio
                    self.redis_client = redis_asyncio.from_url(
                        config.redis_url,
                        decode_responses=True,
                        retry_on_timeout=True,
                        socket_connect_timeout=5
                    )
                    logger.debug("Reinitialized Redis client in safe_execute due to closed connection", extra={"method": method_name})
                    return await getattr(self.redis_client, method_name)(*args, **kwargs)
                except Exception as e2:
                    logger.exception("Failed to reinitialize Redis client", extra={"error": str(e2)})
                    raise e2
            else:
                raise e

    async def check(self):
        if self.redis_client:
            now = int(time.time() * 1000)
            window = now // self.window_ms
            key = f"rate_limiter:{id(self)}:{window}"
            try:
                count = await self.safe_execute('incr', key)
                if count == 1:
                    await self.safe_execute('expire', key, self.window_ms // 1000)
                if count > self.max_requests:
                    logger.warning("Rate limit exceeded (distributed).", extra={"key": key, "count": count})
                    raise Exception("Rate limit exceeded. Please try again later.")
                return
            except Exception as e:
                if config.enable_debug:
                    logger.exception("Error in distributed rate limiter, falling back to in-memory.")
                else:
                    logger.error("Error in distributed rate limiter, falling back to in-memory.", extra={"error": str(e)})
                self._in_memory_check(now)
        else:
            self._in_memory_check(int(time.time() * 1000))

    def _in_memory_check(self, now: int):
        # Remove requests older than windowMs
        while self.queue and (now - self.queue[0] > self.window_ms):
            self.queue.pop(0)
        if len(self.queue) >= self.max_requests:
            logger.warning("Rate limit exceeded (in-memory).", extra={"currentQueueLength": len(self.queue)})
            raise Exception("Rate limit exceeded. Please try again later.")
        self.queue.append(now)
        logger.debug("RateLimiter check passed (in-memory).", extra={"newQueueLength": len(self.queue)})
