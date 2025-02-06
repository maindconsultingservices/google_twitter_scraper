import time
from api.utils.logger import logger

class RateLimiter:
    """
    Simple in-memory rate limiter that allows `max_requests` in `window_ms` timeframe.
    If the limit is exceeded, it raises an Exception.
    """
    def __init__(self, max_requests: int, window_ms: int):
        self.max_requests = max_requests
        self.window_ms = window_ms
        self.queue = []

    def check(self):
        logger.debug("RateLimiter check initiated.", extra={
            "maxRequests": self.max_requests,
            "windowMs": self.window_ms,
            "currentQueueLength": len(self.queue),
        })

        now = int(time.time() * 1000)
        # Remove requests older than windowMs
        while self.queue and (now - self.queue[0] > self.window_ms):
            self.queue.pop(0)

        if len(self.queue) >= self.max_requests:
            logger.warning("Rate limit exceeded.")
            raise Exception("Rate limit exceeded. Please try again later.")

        self.queue.append(now)
        logger.debug("RateLimiter check passed.", extra={
            "newQueueLength": len(self.queue)
        })
