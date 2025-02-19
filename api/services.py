# api/services.py

import os
import time
import json
import traceback
import asyncio
import random
from typing import List, Dict, Any, Tuple, Optional
from urllib.parse import urlparse

from fastapi.concurrency import run_in_threadpool
from googlesearch import search
import cloudscraper
from bs4 import BeautifulSoup
import httpx
import re  # Added to enable removal of <think> tokens
from requests.exceptions import HTTPError

from twitter.account import Account
from twitter.scraper import Scraper
from twitter.search import Search

from .config import config
from .types import Tweet, QueryTweetsResponse, SearchMode
from .utils import logger

# New: List of common user-agent strings for Google search requests.
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
]

# Optionally, you can also define a global default:
MAX_TEXT_LENGTH_TO_SUMMARIZE = int(os.getenv("MAX_TEXT_LENGTH_TO_SUMMARIZE", "5000"))

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

#
# RateLimiter
#
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

#
# GOOGLE service
#
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
            global _last_google_call
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
                # Use a randomized user agent and a longer pause to help avoid 429 errors
                return await run_in_threadpool(
                    lambda: list(
                        search(
                            query,
                            num_results=max_results,
                            pause=2.5,
                            user_agent=random.choice(USER_AGENTS)
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
        
        # If the timeframe is "week", apply fallback logic.
        if timeframe and timeframe.lower() == "week":
            fallback_timeframes = ["week", "month", "year", None]
            results = []
            effective_tf = "none"
            for tf in fallback_timeframes:
                mod_query = build_query(query, tf) if tf is not None else query
                try:
                    results = await self._search_with_retries(mod_query, max_results)
                except Exception as e:
                    results = []
                # Filter out invalid URLs, PDF URLs, and blacklisted domains
                valid_results = [
                    r for r in results 
                    if r and r.startswith("http") 
                    and not r.lower().endswith(".pdf") 
                    and not is_blacklisted(r, blacklisted_domains)
                ]
                if len(valid_results) >= 3:
                    effective_tf = tf if tf is not None else "none"
                    filtered_results = [
                        r for r in results 
                        if not r.lower().endswith(".pdf") 
                        and not is_blacklisted(r, blacklisted_domains)
                    ]
                    return filtered_results, effective_tf
            filtered_results = [
                r for r in results 
                if not r.lower().endswith(".pdf") 
                and not is_blacklisted(r, blacklisted_domains)
            ]
            return filtered_results, effective_tf
        else:
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

google_service = GoogleService()

#
# TWITTER CLIENT MANAGER
#
class TwitterClientManager:
    def __init__(self):
        self._account = None
        self._scraper = None
        self._search = None
        self._logged_in = False
        # We'll store the cookies dict here if we successfully parse them
        self._cookies_store = None

    def _init_account(self) -> Account:
        """
        Initialize the Account object using cookies (recommended)
        or fallback to username/password if no cookies are provided.
        """
        logger.debug("Entering _init_account to set up Account instance...")
        try:
            if config.twitter_cookies_json:
                logger.info("Loading cookies from JSON in env...")
                try:
                    cookies_dict = json.loads(config.twitter_cookies_json)
                    self._cookies_store = cookies_dict
                    acct = Account(cookies=cookies_dict)
                    logger.debug("Successfully created Account from inline JSON cookies.")
                except Exception as e:
                    tb = traceback.format_exc()
                    logger.error(
                        "Failed to parse TWITTER_COOKIES_JSON; falling back to username/password",
                        extra={"error": str(e), "traceback": tb}
                    )
                    self._cookies_store = None
                    acct = Account(
                        email=config.twitter_email,
                        username=config.twitter_username,
                        password=config.twitter_password
                    )
            else:
                logger.warning("No cookies provided. Falling back to username/password approach (less stable).")
                acct = Account(
                    email=config.twitter_email,
                    username=config.twitter_username,
                    password=config.twitter_password
                )
        except Exception as ex:
            tb = traceback.format_exc()
            logger.error(
                "Exception occurred while initializing Account object.",
                extra={"error": str(ex), "traceback": tb}
            )
            raise

        logger.debug("Leaving _init_account.")
        return acct

    def get_account(self) -> Account:
        """
        Returns the cached Account instance (or initializes it if needed).
        """
        if not self._account:
            logger.debug("No existing Account found; calling _init_account now.")
            self._account = self._init_account()
            logger.info("Account instance created.")
        else:
            logger.debug("Reusing existing Account instance.")
        return self._account

    def get_scraper(self) -> Scraper:
        """
        Returns a cached Scraper instance. If cookies were loaded, use them;
        otherwise fallback to email/username/password.
        """
        if not self._scraper:
            logger.debug("No existing Scraper; about to retrieve account/cookies for the Scraper.")
            self.get_account()  # ensure the Account is initialized
            if self._cookies_store:
                logger.debug("Detected cookies store; creating Scraper with it now.")
                try:
                    self._scraper = Scraper(cookies=self._cookies_store)
                    logger.info("Scraper instance created from cookies store.")
                except Exception as e:
                    tb = traceback.format_exc()
                    logger.error("Exception creating Scraper with stored cookies", extra={"error": str(e), "traceback": tb})
                    raise
            else:
                logger.warning("No cookies store found. Attempting Scraper with fallback credentials.")
                try:
                    self._scraper = Scraper(
                        email=config.twitter_email,
                        username=config.twitter_username,
                        password=config.twitter_password
                    )
                    logger.debug("Scraper created using fallback user/pass.")
                except Exception as e:
                    tb = traceback.format_exc()
                    logger.error("Exception creating Scraper with user/pass fallback", extra={"error": str(e), "traceback": tb})
                    raise
        else:
            logger.debug("Reusing existing Scraper instance.")
        return self._scraper

    def get_search(self) -> Search:
        """
        Returns a cached Search instance, for advanced queries.
        """
        if not self._search:
            logger.debug("No existing Search instance; creating a new one.")
            logger.info("Creating Search instance for advanced queries.")
            self.get_account()  # ensure the Account is initialized

            console_only_logger = {
                "version": 1,
                "disable_existing_loggers": False,
                "handlers": {
                    "console": {
                        "class": "logging.StreamHandler",
                        "level": "DEBUG"
                    }
                },
                "root": {
                    "handlers": ["console"],
                    "level": "DEBUG"
                }
            }

            output_dir = "/tmp/twitter_search"

            if self._cookies_store:
                logger.debug("Detected cookies store; creating Search with cookies.")
                try:
                    self._search = Search(
                        cookies=self._cookies_store,
                        save=False,
                        debug=False,
                        output_dir=output_dir,
                        data_dir=output_dir,
                        cfg=console_only_logger
                    )
                except Exception as e:
                    tb = traceback.format_exc()
                    logger.error("Exception creating Search with stored cookies", extra={"error": str(e), "traceback": tb})
                    raise
            else:
                logger.warning("No cookies store found. Attempting Search fallback approach with user/pass.")
                try:
                    self._search = Search(
                        email=config.twitter_email,
                        username=config.twitter_username,
                        password=config.twitter_password,
                        save=False,
                        debug=False,
                        output_dir=output_dir,
                        data_dir=output_dir,
                        cfg=console_only_logger
                    )
                    logger.debug("Search created with fallback user/pass.")
                except Exception as e:
                    tb = traceback.format_exc()
                    logger.error("Exception creating Search with user/pass fallback", extra={"error": str(e), "traceback": tb})
                    raise
        else:
            logger.debug("Reusing existing Search instance.")
        return self._search

    def is_logged_in(self) -> bool:
        """
        We consider ourselves logged in if a quick home_timeline call does not fail.
        """
        logger.debug("Checking if we are logged in via home_timeline call.")
        if not self._logged_in:
            try:
                logger.debug("Calling home_timeline(limit=1) to verify login status.")
                self.get_account().home_timeline(limit=1)
                logger.debug("home_timeline succeeded; marking _logged_in = True.")
                self._logged_in = True
            except Exception as e:
                tb = traceback.format_exc()
                logger.error("Login check failed", extra={"error": str(e), "traceback": tb})
                self._logged_in = False
        else:
            logger.debug("Already marked as logged in (self._logged_in == True).")
        return self._logged_in

twitter_client_manager = TwitterClientManager()

#
# TWITTER service
#
class TwitterService:
    def __init__(self):
        self.rate_limiter = RateLimiter(15, 60_000)  # e.g. 15 requests/min

    async def _ensure_login(self):
        """Check that the account is logged in; raises an error if not."""
        logger.debug("_ensure_login called. Checking is_logged_in() on twitter_client_manager.")
        if not twitter_client_manager.is_logged_in():
            logger.debug("twitter_client_manager reports not logged in. Raising RuntimeError.")
            raise RuntimeError("Not logged into Twitter. Check your cookies or credentials.")
        logger.debug("twitter_client_manager is logged in successfully.")

    def get_profile(self):
        """Return minimal profile data from the account object, if needed."""
        return {"username": getattr(config, "twitter_username", "unknown"), "id": "0"}

    # ================== READ methods =====================
    async def get_user_tweets(self, user_id: str, count: int) -> List[Tweet]:
        logger.debug("Service: get_user_tweets invoked", extra={"user_id": user_id, "count": count})
        await self.rate_limiter.check()
        await self._ensure_login()

        scraper = twitter_client_manager.get_scraper()
        numeric_id = int(user_id)  # might fail if not numeric
        raw_tweets = scraper.tweets([numeric_id], limit=count)
        return self._parse_tweets(raw_tweets)

    async def fetch_home_timeline(self, count: int) -> List[Tweet]:
        logger.debug("Service: fetch_home_timeline invoked", extra={"count": count})
        await self.rate_limiter.check()
        await self._ensure_login()

        account = twitter_client_manager.get_account()
        timeline_data = account.home_timeline(limit=count) or []
        if config.enable_debug:
            try:
                logger.debug("Raw home_timeline data:\n%s",
                             json.dumps(timeline_data, ensure_ascii=False, indent=2))
            except Exception:
                logger.debug("Raw home_timeline data (repr): %r", timeline_data)

        return self._parse_account_timeline(timeline_data)

    async def fetch_following_timeline(self, count: int) -> List[Tweet]:
        logger.debug("Service: fetch_following_timeline invoked", extra={"count": count})
        await self.rate_limiter.check()
        await self._ensure_login()

        account = twitter_client_manager.get_account()
        timeline_data = account.home_latest_timeline(limit=count) or []
        if config.enable_debug:
            try:
                logger.debug("Raw home_latest_timeline data:\n%s",
                             json.dumps(timeline_data, ensure_ascii=False, indent=2))
            except Exception:
                logger.debug("Raw home_latest_timeline data (repr): %r", timeline_data)

        return self._parse_account_timeline(timeline_data)

    async def fetch_search_tweets(self, query: str, max_tweets: int, mode: str) -> QueryTweetsResponse:
        logger.debug("Service: fetch_search_tweets called", extra={"query": query, "max_tweets": max_tweets, "mode": mode})
        await self.rate_limiter.check()
        await self._ensure_login()

        search_client = twitter_client_manager.get_search()

        category_map = {
            SearchMode.Latest.value: "Latest",
            SearchMode.Top.value: "Top",
            SearchMode.People.value: "People",
            SearchMode.Photos.value: "Photos",
            SearchMode.Videos.value: "Videos"
        }
        category = category_map.get(mode, "Top")

        queries = [{"category": category, "query": query}]

        logger.debug("About to call search_client.run() with a single query entry", extra={
            "queries": queries,
            "limit": max_tweets
        })

        try:
            results = await run_in_threadpool(
                search_client.run,
                queries=queries,
                limit=max_tweets,
                save=False,
                debug=False,
                output_dir="/tmp/twitter_search"
            )

            logger.debug("search_client.run() returned", extra={
                "type": str(type(results)),
                "full_results_str": str(results),
                "count": len(results) if isinstance(results, list) else "N/A"
            })

            if config.enable_debug:
                try:
                    logger.debug("Full search results:\n%s",
                                 json.dumps(results, ensure_ascii=False, indent=2))
                except Exception:
                    logger.debug("Full search results (repr): %r", results)

        except Exception as exc:
            tb = traceback.format_exc()
            logger.error("Failed to execute search_client.run()",
                         exc_info=True,
                         extra={"error": str(exc), "traceback": tb})
            raise

        if not results:
            logger.debug("search_client.run() returned empty or None results. Returning empty list.")
            return QueryTweetsResponse(tweets=[])

        all_tweets_data = self._flatten_search_results(results)
        found = []
        for tweet_dict in all_tweets_data:
            parsed = self._map_tweet_item(tweet_dict)
            if parsed:
                found.append(parsed)

        return QueryTweetsResponse(tweets=found)

    async def fetch_mentions(self) -> QueryTweetsResponse:
        profile = self.get_profile()
        username = profile["username"] if profile else ""
        if not username:
            logger.warning("fetchMentions: Twitter not logged in.")
            return QueryTweetsResponse(tweets=[])
        count = 10
        search_results = await self.fetch_search_tweets(f"@{username}", count, SearchMode.Latest.value)
        return search_results

    # ================== WRITE methods =====================
    async def post_tweet(self, text: str, in_reply_to_id: str = None) -> Optional[str]:
        logger.debug("Service: post_tweet called", extra={"text": text, "inReplyToId": in_reply_to_id})
        await self.rate_limiter.check()
        await self._ensure_login()

        account = twitter_client_manager.get_account()
        try:
            if in_reply_to_id:
                posted_id = account.reply(text, tweet_id=int(in_reply_to_id))
            else:
                posted_id = account.tweet(text)
            return str(posted_id)
        except Exception as e:
            logger.error("Failed to post tweet",
                         exc_info=True,
                         extra={"error": str(e)})
            return None

    async def post_quote_tweet(self, text: str, quote_id: str) -> Optional[str]:
        logger.debug("Service: post_quote_tweet called", extra={"text": text, "quoteId": quote_id})
        await self.rate_limiter.check()
        await self._ensure_login()

        account = twitter_client_manager.get_account()
        try:
            posted_id = account.quote(text, tweet_id=int(quote_id))
            return str(posted_id)
        except Exception as e:
            logger.error("Failed to quote tweet",
                         exc_info=True,
                         extra={"error": str(e)})
            return None

    async def retweet(self, tweet_id: str) -> bool:
        logger.debug("Service: retweet called", extra={"tweetId": tweet_id})
        await self.rate_limiter.check()
        await self._ensure_login()

        account = twitter_client_manager.get_account()
        try:
            account.retweet(int(tweet_id))
            return True
        except Exception as e:
            logger.error("Failed to retweet",
                         exc_info=True,
                         extra={"error": str(e)})
            return False

    async def like_tweet(self, tweet_id: str) -> bool:
        logger.debug("Service: like_tweet called", extra={"tweetId": tweet_id})
        await self.rate_limiter.check()
        await self._ensure_login()

        account = twitter_client_manager.get_account()
        try:
            account.like(int(tweet_id))
            return True
        except Exception as e:
            logger.error("Failed to like tweet",
                         exc_info=True,
                         extra={"error": str(e)})
            return False

    # ================== HELPER methods =====================
    def _parse_tweets(self, raw_items) -> List[Tweet]:
        logger.debug("_parse_tweets called.", extra={
            "raw_items_count": len(raw_items) if raw_items else 0
        })
        tweets = []
        if not raw_items:
            return tweets

        for item in raw_items:
            mapped = self._map_tweet_item(item)
            if mapped:
                tweets.append(mapped)
        return tweets

    def _parse_account_timeline(self, timeline_data) -> List[Tweet]:
        logger.debug("_parse_account_timeline called.", extra={
            "timeline_items_count": len(timeline_data) if timeline_data else 0
        })
        flattened = self._flatten_search_results(timeline_data)
        logger.debug(f"Flattened timeline items => {len(flattened)} potential tweets.")
        tweets = []
        for item in flattened:
            mapped = self._map_tweet_item(item)
            if mapped:
                tweets.append(mapped)
            else:
                if config.enable_debug:
                    logger.debug("map_tweet_item returned None for item:\n%r", item)
        return tweets

    def _map_tweet_item(self, data: dict) -> Optional[Tweet]:
        try:
            # If "tweet_results" -> "result" is in data, unwrap once more
            if "tweet_results" in data and isinstance(data["tweet_results"], dict):
                data = data["tweet_results"].get("result", data)

            tid = str(
                data.get("rest_id")
                or data.get("id")
                or data.get("id_str")
                or "0"
            )

            text = ""
            uname = "unknown"
            user_id_str = "0"
            conv_id = "0"
            q_count = 0
            r_count = 0
            rt_count = 0

            if "legacy" in data:
                legacy = data["legacy"]
                raw_text = legacy.get("full_text") or legacy.get("text", "")
                if not raw_text and "note_tweet" in data:
                    raw_text = self._extract_note_tweet_text(data["note_tweet"])
                text = raw_text
                conv_id = str(legacy.get("conversation_id_str") or "0")

                # Stats
                q_count = int(legacy.get("quote_count", 0))
                r_count = int(legacy.get("reply_count", 0))
                rt_count = int(legacy.get("retweet_count", 0))

                # Attempt to get the user
                core_user = data.get("core", {}).get("user_results", {}).get("result", {})
                if isinstance(core_user, dict):
                    user_id_str = str(core_user.get("rest_id") or "0")
                    if "legacy" in core_user:
                        uname = core_user["legacy"].get("screen_name", "unknown")
                else:
                    uname = data.get("username") or data.get("user_screen_name") or "unknown"
            else:
                # Fallback
                raw_text = data.get("text", "")
                if not raw_text and "note_tweet" in data:
                    raw_text = self._extract_note_tweet_text(data["note_tweet"])
                text = raw_text
                uname = data.get("username") or data.get("user_screen_name") or "unknown"
                user_id_str = str(data.get("user_id") or "0")
                conv_id = str(data.get("conversation_id") or "0")
                q_count = int(data.get("quote_count", 0))
                r_count = int(data.get("reply_count", 0))
                rt_count = int(data.get("retweet_count", 0))

            timestamp_s = int(time.time())
            tweet = Tweet(
                id=tid,
                userId=user_id_str,
                username=uname,
                text=text,
                conversationId=conv_id,
                timestamp=timestamp_s,
                permanentUrl=f"https://x.com/{uname}/status/{tid}",
                quoteCount=q_count,
                replyCount=r_count,
                retweetCount=rt_count
            )

            if config.enable_debug:
                logger.debug(
                    f"Mapped tweet ID={tid}, user={uname}, textLen={len(text)}, "
                    f"replyCount={r_count}, retweetCount={rt_count}, quoteCount={q_count}"
                )
            return tweet

        except Exception as e:
            logger.error(
                "Failed to map tweet item",
                exc_info=True,
                extra={"error": str(e), "raw": data}
            )
            return None

    def _extract_note_tweet_text(self, note_tweet_block: dict) -> str:
        if not isinstance(note_tweet_block, dict):
            return ""
        try:
            note_results = note_tweet_block.get("note_tweet_results")
            if isinstance(note_results, dict):
                result_obj = note_results.get("result")
                if isinstance(result_obj, dict):
                    return result_obj.get("text", "")
        except Exception as ex:
            logger.debug("Could not extract note_tweet text", extra={"error": str(ex), "raw": note_tweet_block})
        return ""

    def _flatten_search_results(self, results):
        """
        Takes raw 'results' from search_client.run() or timeline calls and attempts to
        flatten them into a list of tweet-like dicts for _map_tweet_item.
        """

        # If the library returned a JSON string, parse it here
        if isinstance(results, str):
            logger.debug("_flatten_search_results received a string. Attempting to parse JSON.")
            try:
                results = json.loads(results)
                logger.debug("Successfully parsed the string into JSON. Proceeding.")
            except Exception as ex:
                logger.error("Could not parse timeline string as JSON", extra={"error": str(ex)})
                return []

        if not isinstance(results, list):
            logger.debug("_flatten_search_results: Non-list results -> returning empty.")
            return []

        flattened_tweets = []

        for idx, item in enumerate(results):
            # 1) Possibly timeline entry with entryId = 'tweet-...'
            if (
                isinstance(item, dict)
                and isinstance(item.get("entryId"), str)
                and item["entryId"].startswith("tweet-")
                and "content" in item
            ):
                single_extracts = self._extract_from_entry(item)
                if single_extracts:
                    flattened_tweets.extend(single_extracts)
                    continue

            # 2) Possibly older shapes with "tweets" => [...]
            if isinstance(item, dict) and "tweets" in item and isinstance(item["tweets"], list):
                extracted_count = len(item["tweets"])
                if config.enable_debug:
                    logger.debug(f"_flatten_search_results: Found {extracted_count} tweets in item index={idx}.")
                flattened_tweets.extend(item["tweets"])
                continue

            # 3) instructions-based or data->entries
            elif isinstance(item, dict) and ("entryId" in item or "entries" in item or "data" in item):
                extracted = self._extract_from_new_instructions(item)
                if config.enable_debug:
                    logger.debug(f"_flatten_search_results: Extracted {len(extracted)} from item index={idx} with entryId/data/entries.")
                flattened_tweets.extend(extracted)
                continue

            # 4) Possibly nested arrays
            elif isinstance(item, list):
                for sub in item:
                    if isinstance(sub, dict) and "tweets" in sub and isinstance(sub["tweets"], list):
                        extracted_count = len(sub["tweets"])
                        if config.enable_debug:
                            logger.debug(f"_flatten_search_results: Found {extracted_count} tweets in nested sub-list.")
                        flattened_tweets.extend(sub["tweets"])
                    else:
                        extracted = self._extract_from_new_instructions(sub)
                        if extracted:
                            flattened_tweets.extend(extracted)
                        else:
                            flattened_tweets.append(sub)
            else:
                flattened_tweets.append(item)

        if config.enable_debug:
            logger.debug(f"_flatten_search_results: total flattened tweets={len(flattened_tweets)}")
        return flattened_tweets

    def _extract_from_new_instructions(self, data_item):
        """
        Attempt to parse instructions-based structures or single "entryId" structures.
        For home timeline or search-based instructions.
        """
        collected = []

        try:
            # 1) data->home->home_timeline_urt->instructions
            if (
                "data" in data_item
                and isinstance(data_item["data"], dict)
                and "home" in data_item["data"]
                and isinstance(data_item["data"]["home"], dict)
            ):
                home_obj = data_item["data"]["home"]
                timeline_urt = home_obj.get("home_timeline_urt", {})
                if isinstance(timeline_urt, dict):
                    instructions = timeline_urt.get("instructions", [])
                    for inst in instructions:
                        if isinstance(inst, dict) and "entries" in inst:
                            collected.extend(self._collect_entries(inst["entries"]))

            # 2) data->search_by_query->instructions
            elif "data" in data_item and isinstance(data_item["data"], dict):
                search_obj = data_item["data"].get("search_by_query")
                if search_obj and isinstance(search_obj, dict):
                    instructions = search_obj.get("instructions", [])
                    for inst in instructions:
                        if isinstance(inst, dict) and "entries" in inst:
                            collected.extend(self._collect_entries(inst["entries"]))

            # 3) if top-level has 'instructions'
            elif "instructions" in data_item and isinstance(data_item["instructions"], list):
                for inst in data_item["instructions"]:
                    if isinstance(inst, dict) and "entries" in inst:
                        collected.extend(self._collect_entries(inst["entries"]))

            # 4) if top-level has 'entries'
            elif "entries" in data_item and isinstance(data_item["entries"], list):
                collected.extend(self._collect_entries(data_item["entries"]))

            # 5) single "entryId" + "content" => parse as timeline entry
            elif "entryId" in data_item and "content" in data_item:
                single_extracts = self._extract_from_entry(data_item)
                if single_extracts:
                    collected.extend(single_extracts)

        except Exception as e:
            logger.error("Error parsing new instructions format",
                         exc_info=True,
                         extra={"error": str(e), "raw": data_item})

        return collected

    def _collect_entries(self, entries):
        found = []
        for e in entries:
            if not isinstance(e, dict):
                continue
            sub = self._extract_from_entry(e)
            if sub:
                found.extend(sub)
        return found

    def _extract_from_entry(self, entry) -> List[dict]:
        """
        Attempt to get one or more tweet dicts from an entry shaped like:
        {
          "entryId": "tweet-...",
          "content": {
            "itemContent": { "tweet_results": { "result": {...} } }
          }
        }
        """
        results = []
        try:
            content = entry.get("content")
            if not isinstance(content, dict):
                return results

            content_type = content.get("entryType") or content.get("__typename") or ""
            if (
                "TimelineTimelineItem" not in content_type
                and "TimelineTimelineModule" not in content_type
                and "VerticalConversation" not in content_type
            ):
                return results

            item_content = content.get("itemContent")
            if isinstance(item_content, dict):
                item_type = item_content.get("itemType") or item_content.get("__typename") or ""
                if "TimelineTweet" in item_type:
                    tweet_results = item_content.get("tweet_results")
                    if isinstance(tweet_results, dict):
                        tweet_data = tweet_results.get("result")
                        if isinstance(tweet_data, dict):
                            results.append(tweet_data)
                            return results

            # fallback: recursively search for any 'tweet_results' -> 'result'
            deeper = self._extract_tweets_deep(content)
            results.extend(deeper)

        except Exception as ex:
            logger.debug(
                "Could not extract tweet dict from entry",
                extra={"error": str(ex), "raw": entry}
            )
        return results

    def _extract_tweets_deep(self, node: Any) -> List[dict]:
        """
        A fallback to recursively search for any 'tweet_results' -> 'result' inside a dict/array.
        """
        found = []

        if isinstance(node, dict):
            if "tweet_results" in node and isinstance(node["tweet_results"], dict):
                maybe_tweet = node["tweet_results"].get("result")
                if isinstance(maybe_tweet, dict):
                    found.append(maybe_tweet)
            # Recurse child values
            for v in node.values():
                found.extend(self._extract_tweets_deep(v))

        elif isinstance(node, list):
            for item in node:
                found.extend(self._extract_tweets_deep(item))

        return found

#
# WEB service
#
class WebService:
    """
    Service layer for scraping content from given URLs.
    Uses cloudscraper to bypass Cloudflare anti-bot challenges
    and BeautifulSoup for HTML parsing.
    Includes a rate limiter to prevent excessive calls.
    """
    def __init__(self):
        self.rate_limiter = RateLimiter(5, 60_000)
        # This session will handle CF challenge flows automatically and maintain cookies between requests.
        self.scraper = cloudscraper.create_scraper()
        self.scraper.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Referer": "https://www.google.com/",
            "Connection": "keep-alive"
        })
        # Add a dedicated rate limiter for Venice API calls (20 per minute per user)
        self.venice_rate_limiter = RateLimiter(20, 60_000)

    def _is_valid_url(self, url: str) -> bool:
        from urllib.parse import urlparse
        try:
            parsed = urlparse(url)
            return bool(parsed.scheme and parsed.netloc)
        except Exception:
            return False

    async def _scrape_single_url(self, url: str, query: str) -> Dict[str, Any]:
        # Check for empty or invalid URL
        if not url or not isinstance(url, str) or url.strip() == "":
            logger.error("Empty or invalid URL provided for scraping")
            return {
                "url": url,
                "status": 0,
                "error": "Empty or invalid URL provided",
                "metaDescription": "",
                "textPreview": "",
                "title": "",
                "fullText": "",
                "Summary": "",
                "IsQueryRelated": False,
                "relatedURLs": []
            }
        # Initialize with default values. Note: error is None if no error occurs.
        single_result = {
            "url": url,
            "status": 0,
            "error": None,
            "metaDescription": "",
            "textPreview": "",
            "title": "",
            "fullText": "",
            "Summary": "",
            "IsQueryRelated": False,
            "relatedURLs": []
        }
        if self.rate_limiter.redis_client:
            try:
                cached = await self.rate_limiter.safe_execute('get', f"scrape:{url}")
            except Exception as e:
                if config.enable_debug:
                    logger.exception("Redis error in caching get")
                else:
                    logger.error("Redis error in caching get", extra={"error": str(e)})
                cached = None
            if cached:
                logger.debug("Returning cached scrape result", extra={"url": url})
                return json.loads(cached)
        try:
            logger.debug("Starting scraping URL", extra={"url": url})
            # Introduce a random delay to mimic human behavior (jitter)
            await asyncio.sleep(random.uniform(0.5, 1.5))
            start_time = time.time()
            response = await run_in_threadpool(lambda: self.scraper.get(url, timeout=10))
            # Force correct encoding based on apparent encoding
            response.encoding = response.apparent_encoding
            duration = time.time() - start_time
            logger.debug("Finished scraping URL", extra={"url": url, "duration": duration, "status_code": response.status_code})
            single_result["status"] = response.status_code
            if response.status_code == 200:
                if not response.text or response.text.strip() == "":
                    logger.error("Empty response text received, possibly due to anti-bot block or network issue", extra={"url": url})
                    single_result["error"] = "Empty response text received"
                else:
                    # Parse HTML content
                    soup = BeautifulSoup(response.text, "html.parser")
                    title_tag = soup.find("title")
                    meta_desc_tag = soup.find("meta", attrs={"name": "description"})
                    full_text = soup.get_text(separator=" ", strip=True)
                    # Check for common anti-bot markers only if title is missing or appears invalid
                    anti_bot_markers = ["access denied", "captcha", "bot check"]
                    lower_text = response.text.lower()
                    if any(marker in lower_text for marker in anti_bot_markers):
                        if not title_tag or len(title_tag.get_text(strip=True)) < 5:
                            logger.error("Response indicates possible anti-bot protection", extra={"url": url, "response_snippet": response.text[:500]})
                            single_result["error"] = "Anti-bot protection triggered"
                        else:
                            single_result["error"] = None
                    else:
                        single_result["error"] = None
                    if not title_tag:
                        logger.warning("No title found in HTML, unexpected HTML structure", extra={"url": url, "html_snippet": response.text[:300]})
                        logger.debug("Full HTML content for debugging", extra={"url": url, "html": response.text})
                    single_result["title"] = title_tag.get_text(strip=True) if title_tag else ""
                    if meta_desc_tag and meta_desc_tag.get("content"):
                        single_result["metaDescription"] = meta_desc_tag["content"].strip()
                    # --- NEW READABILITY CHECK ---
                    if full_text:
                        def is_readable(text: str) -> bool:
                            if not text:
                                return False
                            # If more than 20% of the characters are the replacement character "�", consider it unreadable
                            if text.count("�") / len(text) > 0.2:
                                return False
                            return True
                        if not is_readable(full_text):
                            logger.warning("Content from URL is unreadable, ignoring", extra={"url": url})
                            return None
                        # --------------------------------
                        single_result["textPreview"] = full_text[:200]
                        single_result["fullText"] = full_text
                        summary, is_query_related, related_urls = await self.summarize_text(full_text, query)
                        single_result["Summary"] = summary
                        single_result["IsQueryRelated"] = is_query_related
                        single_result["relatedURLs"] = related_urls
            else:
                single_result["error"] = f"Non-200 status code: {response.status_code}"
                logger.warning("Non-200 response while scraping URL", extra={
                    "url": url,
                    "status_code": response.status_code,
                    "headers": dict(response.headers),
                    "body_snippet": response.text[:500] if response.text else ""
                })
        except Exception as exc:
            tb = traceback.format_exc()
            logger.error("Error scraping URL", extra={"url": url, "error": str(exc), "traceback": tb})
            single_result["error"] = str(exc)
        if self.rate_limiter.redis_client:
            try:
                await self.rate_limiter.safe_execute('set', f"scrape:{url}", json.dumps(single_result), ex=60)
            except Exception as e:
                if config.enable_debug:
                    logger.exception("Redis error in caching set")
                else:
                    logger.error("Redis error in caching set", extra={"error": str(e)})
        return single_result

    async def scrape_urls(self, urls: List[str], query: str) -> List[Dict[str, Any]]:
        logger.debug("WebService: scrape_urls called", extra={"urls": urls, "query": query})
        await self.rate_limiter.check()
        # Filter out invalid URLs to avoid calling the scrape logic on nonsense values.
        urls = [url for url in urls if self._is_valid_url(url)]
        sem = asyncio.Semaphore(10)
        async def sem_scrape(url):
            async with sem:
                return await self._scrape_single_url(url, query)
        results = await asyncio.gather(*(sem_scrape(url) for url in urls))
        # Filter out entries that are None (i.e. unreadable content)
        results = [r for r in results if r is not None]
        return results

    async def summarize_text(self, text: str, query: str) -> Tuple[str, bool, List[str]]:
        """
        Calls the Venice.ai API to get a comprehensive and extensive summary of the provided text, determine
        whether the text is related to the provided query, and extract any URLs within the text that seem related.
        Returns a tuple containing the summary (str), a boolean indicating if the text is related, and a list of related URLs.
        Implements retries and respects Venice rate limits.
        """
        if not text or len(text) < 20:
            return "", False, []
        
        # Truncate the text if it exceeds the maximum allowed length.
        max_text_length = int(os.getenv("MAX_TEXT_LENGTH_TO_SUMMARIZE", "5000"))
        if len(text) > max_text_length:
            text = text[:max_text_length]

        # Respect Venice rate limits
        await self.venice_rate_limiter.check()

        payload = {
            "model": config.venice_model,
            "messages": [
                {"role": "system", "content": config.system_prompt},
                {"role": "user", "content": (
                    f"""
                    Please provide a comprehensive and extensive summary of the following text: {text},
                    ensuring that all relevant points and conclusions extracted from the text are included,
                    especially those related to the query: {query}.
                    Also, determine whether the text is related to the query: {query}.
                    If there are any URLs present within the text that appear to be relevant to the query, extract them
                    and include them in an array.
                    Set 'isQueryRelated' to true if the content is related to the query, and set 'isQueryRelated' to false 
                    only if the content of the site and the input query have nothing to do with each other.
                    Return a JSON object with three keys:
                    'summary' for the comprehensive summary,
                    'isQueryRelated' as a boolean value,
                    and 'relatedURLs' as an array of URLs (an empty array if none are found).
                    """
                )},
            ],
            "venice_parameters": {
                "include_venice_system_prompt": False
            },
            "temperature": config.venice_temperature
        }
        headers = {
            "Authorization": f"Bearer {config.venice_api_key}",
            "Content-Type": "application/json"
        }
        max_attempts = 4
        delay = 1
        for attempt in range(max_attempts):
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(config.venice_url, json=payload, headers=headers, timeout=30.0)
                # If Venice returns 503 or 400, log details and retry if appropriate.
                if response.status_code == 503:
                    reset_time = response.headers.get("x-ratelimit-reset-requests")
                    try:
                        delay = float(reset_time) if reset_time is not None else delay
                    except Exception:
                        delay = delay
                    logger.warning("Venice API 503 Service Unavailable, retrying", extra={"attempt": attempt+1, "delay": delay})
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                elif response.status_code == 400:
                    logger.error("Venice API 400 Bad Request", extra={"response": response.text})
                    # Do not retry on 400 since it likely indicates a payload issue.
                    break
                response.raise_for_status()
                data = response.json()
                summary = ""
                is_query_related = False
                related_urls = []
                if "choices" in data and isinstance(data["choices"], list) and len(data["choices"]) > 0:
                    raw_content = data["choices"][0].get("message", {}).get("content", "")
                    raw_content = re.sub(r'<think>.*?</think>', '', raw_content, flags=re.DOTALL).strip()
                    # Remove markdown code block delimiters if present
                    if raw_content.startswith("```"):
                        raw_content = re.sub(r'^```(?:json)?\s*', '', raw_content)
                        raw_content = re.sub(r'\s*```$', '', raw_content)
                    try:
                        result_obj = json.loads(raw_content)
                        summary = result_obj.get("summary", "")
                        is_query_related = result_obj.get("isQueryRelated", False)
                        related_urls = result_obj.get("relatedURLs", [])
                        if not isinstance(related_urls, list):
                            related_urls = []
                    except Exception as parse_exc:
                        logger.error("Failed to parse Venice API response as JSON", extra={"error": str(parse_exc), "raw_content": raw_content})
                        summary = raw_content
                        is_query_related = False
                        related_urls = []
                return summary, is_query_related, related_urls
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 503:
                    logger.warning("Venice API HTTP 503 Service Unavailable, retrying", extra={"attempt": attempt+1})
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                else:
                    logger.error("Venice API HTTP error", extra={"error": str(e)})
                    break
            except Exception as e:
                logger.error("Error summarizing text", extra={"error": str(e)})
                break
        return "", False, []

web_service = WebService()

# Global service instances
google_service = GoogleService()
twitter_service = TwitterService()
