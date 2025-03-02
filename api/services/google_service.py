import os
import time
import traceback
import asyncio
from typing import List, Tuple, Optional
from urllib.parse import urlparse

from fastapi.concurrency import run_in_threadpool
from googlesearch import search
from requests.exceptions import HTTPError

from ..config import config
from ..utils import logger
from .rate_limiter import RateLimiter

# Global variable for the in-memory approach
_last_google_call = 0

# List of common user-agent strings for Google search requests.
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
]

def is_blacklisted(url: str, blacklisted_domains: List[str]) -> bool:
    """
    Checks if the URL's domain is in the list of blacklisted domains.
    It returns True if the domain matches exactly or is a subdomain of any blacklisted domain.
    """
    try:
        domain = urlparse(url).netloc.lower()
        for b in blacklisted_domains:
            b = b.lower()
            if domain == b or domain.endswith("." + b):
                return True
        return False
    except Exception:
        return False

class GoogleService:
    """
    Service layer for performing Google searches using the googlesearch library.
    Includes a rate limiter to prevent excessive calls and a distributed queue mechanism
    to uniformly space out requests over time.
    """
    def __init__(self):
        # Lower rate limit to reduce chance of being blacklisted
        self.rate_limiter_google = RateLimiter(5, 60_000)

    async def _acquire_google_search_slot(self):
        """
        Uses a Redis-backed (or in-memory fallback) distributed queue mechanism to
        enforce a minimum interval (1 second) between successive Google search calls.
        """
        global _last_google_call
        min_interval = 1.0  # seconds between calls; adjust as needed
        key = "google:next_allowed"
        if self.rate_limiter_google.redis_client:
            client = self.rate_limiter_google.redis_client
            now = time.time()
            script = """
            local now = tonumber(ARGV[1])
            local min_interval = tonumber(ARGV[2])
            local key = KEYS[1]
            local next_allowed = tonumber(redis.call("get", key) or "0")
            if now < next_allowed then
                return next_allowed - now
            else
                local new_next = now + min_interval
                redis.call("set", key, new_next)
                return 0
            end
            """
            wait_time = await client.eval(script, 1, key, now, min_interval)
            wait_time = float(wait_time)
            if wait_time > 0:
                logger.debug("Distributed queue: waiting for %.2f seconds", wait_time)
                await asyncio.sleep(wait_time)
        else:
            # Fallback to in-memory approach
            try:
                last = _last_google_call
            except NameError:
                last = 0
            now = time.time()
            if now - last < min_interval:
                wait_time = min_interval - (now - last)
                logger.debug("In-memory queue: waiting for %.2f seconds", wait_time)
                await asyncio.sleep(wait_time)
            _last_google_call = time.time()

    async def _search_with_retries(self, query: str, max_results: int) -> List[str]:
        max_attempts = 3
        delay = 1
        for attempt in range(max_attempts):
            try:
                return await run_in_threadpool(
                    lambda: list(
                        search(
                            query,
                            num_results=max_results,
                            sleep_interval=2.5
                        )
                    )
                )
            except HTTPError as http_err:
                if http_err.response is not None and http_err.response.status_code == 429:
                    logger.warning("HTTP 429 received from Google search. Attempt %d/%d", attempt + 1, max_attempts)
                    if attempt < max_attempts - 1:
                        await asyncio.sleep(delay)
                        delay *= 2
                        continue
                    else:
                        logger.error("Max retries reached for Google search. Raising error.")
                        raise
                else:
                    raise

    async def google_search(self, query: str, max_results: int, timeframe: str = None) -> Tuple[List[str], str]:
        logger.debug("GoogleService: google_search called", extra={"query": query, "max_results": max_results, "timeframe": timeframe})
        await self.rate_limiter_google.check()
        await self._acquire_google_search_slot()
    
        # Get the list of blacklisted domains from environment variable
        blacklist_env = os.getenv("SEARCH_BLACKLISTED_DOMAINS", "")
        blacklisted_domains = [d.strip().lower() for d in blacklist_env.split(",") if d.strip()]
    
        # Local helper to build query with timeframe
        def build_query(q: str, tf: Optional[str]) -> str:
            from datetime import datetime, timedelta
            if tf is None:
                return q
            if tf.lower() == "24h":
                date = datetime.now() - timedelta(days=1)
            elif tf.lower() == "week":
                date = datetime.now() - timedelta(days=7)
            elif tf.lower() == "month":
                date = datetime.now() - timedelta(days=30)
            elif tf.lower() == "year":
                date = datetime.now() - timedelta(days=365)
            else:
                logger.warning("Invalid timeframe provided, ignoring timeframe filter", extra={"timeframe": tf})
                return q
            return f"{q} after:{date.strftime('%Y-%m-%d')}"
    
        if timeframe and timeframe.lower() == "week":
            # Updated fallback sequence: "week" -> "year" -> None
            fallback_timeframes = ["week", "year", None]
            results = []
            effective_tf = "none"
            for tf in fallback_timeframes:
                mod_query = build_query(query, tf) if tf is not None else query
                try:
                    results = await self._search_with_retries(mod_query, max_results)
                except Exception as e:
                    results = []
                # Filter out invalid URLs, PDFs, and blacklisted domains
                valid_results = [
                    r for r in results
                    if r and r.startswith("http")
                    and not r.lower().endswith(".pdf")
                    and not is_blacklisted(r, blacklisted_domains)
                ]
                if len(valid_results) >= 3:  # Threshold for sufficient results
                    effective_tf = tf if tf is not None else "none"
                    filtered_results = [
                        r for r in results
                        if not r.lower().endswith(".pdf")
                        and not is_blacklisted(r, blacklisted_domains)
                    ]
                    return filtered_results, effective_tf
            # Return whatever results we have after exhausting fallbacks
            filtered_results = [
                r for r in results
                if not r.lower().endswith(".pdf")
                and not is_blacklisted(r, blacklisted_domains)
            ]
            return filtered_results, effective_tf
        else:
            # Handle non-"week" timeframes or no timeframe without fallback
            if timeframe:
                mod_query = build_query(query, timeframe)
                effective_tf = timeframe.lower()
            else:
                mod_query = query
                effective_tf = "none"
            try:
                results = await self._search_with_retries(mod_query, max_results)
            except Exception as e:
                tb = traceback.format_exc()
                logger.error("Error in google_search method", extra={"error": str(e), "traceback": tb})
                raise
            filtered_results = [
                r for r in results
                if not r.lower().endswith(".pdf")
                and not is_blacklisted(r, blacklisted_domains)
            ]
            return filtered_results, effective_tf

# Create the singleton instance
google_service = GoogleService()
