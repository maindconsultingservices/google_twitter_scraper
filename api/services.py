import time
import json
import traceback
import asyncio
from typing import List, Dict, Any

from fastapi.concurrency import run_in_threadpool
from googlesearch import search
import cloudscraper
from bs4 import BeautifulSoup
import httpx
import re  # Added to enable removal of <think> tokens

from twitter.account import Account
from twitter.scraper import Scraper
from twitter.search import Search

from .config import config
from .types import Tweet, QueryTweetsResponse, SearchMode
from .utils import logger


#
# RateLimiter (merged from rate_limiter.py)
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
                self.redis_client = redis_asyncio.from_url(config.redis_url)
                logger.debug("Distributed rate limiter enabled with Redis.", extra={"redis_url": config.redis_url})
            except Exception as e:
                logger.error("Failed to initialize Redis client for rate limiting. Falling back to in-memory.", extra={"error": str(e)})

    async def check(self):
        if self.redis_client:
            now = int(time.time() * 1000)
            window = now // self.window_ms
            key = f"rate_limiter:{id(self)}:{window}"
            try:
                count = await self.redis_client.incr(key)
                if count == 1:
                    await self.redis_client.expire(key, self.window_ms // 1000)
                if count > self.max_requests:
                    logger.warning("Rate limit exceeded (distributed).", extra={"key": key, "count": count})
                    raise Exception("Rate limit exceeded. Please try again later.")
                return
            except Exception as e:
                logger.error("Error in distributed rate limiter, falling back to in-memory.", extra={"error": str(e)})
                self._in_memory_check(now)
        else:
            self._in_memory_check(int(time.time() * 1000))

    def _in_memory_check(self, now: int):
        # Remove requests older than window_ms
        while self.queue and (now - self.queue[0] > self.window_ms):
            self.queue.pop(0)
        if len(self.queue) >= self.max_requests:
            logger.warning("Rate limit exceeded (in-memory).", extra={"currentQueueLength": len(self.queue)})
            raise Exception("Rate limit exceeded. Please try again later.")
        self.queue.append(now)
        logger.debug("RateLimiter check passed (in-memory).", extra={"newQueueLength": len(self.queue)})


#
# GOOGLE service (merged from google_service.py)
#
class GoogleService:
    """
    Service layer for performing Google searches using the googlesearch library.
    Includes a rate limiter to prevent excessive calls.
    """
    def __init__(self):
        self.rate_limiter_google = RateLimiter(10, 60_000)

    async def google_search(self, query: str, max_results: int) -> List[str]:
        logger.debug("GoogleService: google_search called", extra={"query": query, "max_results": max_results})
        await self.rate_limiter_google.check()
        # Check cache first if Redis is available
        cache_key = f"google_search:{query}:{max_results}"
        if self.rate_limiter_google.redis_client:
            cached = await self.rate_limiter_google.redis_client.get(cache_key)
            if cached:
                logger.debug("Returning cached Google search results", extra={"cache_key": cache_key})
                return json.loads(cached)
        try:
            # googlesearch is synchronous, so we run it in a threadpool
            results = await run_in_threadpool(lambda: list(search(query, num_results=max_results)))
            # Cache the result for 60 seconds if Redis is available
            if self.rate_limiter_google.redis_client:
                await self.rate_limiter_google.redis_client.set(cache_key, json.dumps(results), ex=60)
            return results
        except Exception as e:
            tb = traceback.format_exc()
            logger.error("Error in google_search method", extra={"error": str(e), "traceback": tb})
            raise

google_service = GoogleService()


#
# TWITTER CLIENT MANAGER (merged from twitter_client.py)
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
# TWITTER service (merged from twitter_service.py)
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
    async def post_tweet(self, text: str, in_reply_to_id: str = None) -> str | None:
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

    async def post_quote_tweet(self, text: str, quote_id: str) -> str | None:
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

    def _map_tweet_item(self, data: dict) -> Tweet | None:
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

twitter_service = TwitterService()

#
# WEB service (merged from web_service.py)
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
        # This session will handle CF challenge flows automatically
        self.scraper = cloudscraper.create_scraper()

    async def _scrape_single_url(self, url: str) -> Dict[str, Any]:
        single_result = {
            "url": url,
            "status": None,
            "error": None,
            "title": None,
            "metaDescription": None,
            "textPreview": None,
            "fullText": None,
            "Summary": None
        }
        # Check cache if available using the same Redis client from the rate limiter
        if self.rate_limiter.redis_client:
            cached = await self.rate_limiter.redis_client.get(f"scrape:{url}")
            if cached:
                logger.debug("Returning cached scrape result", extra={"url": url})
                return json.loads(cached)
        try:
            response = await run_in_threadpool(lambda: self.scraper.get(url, timeout=10))
            single_result["status"] = response.status_code
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, "html.parser")
                title_tag = soup.find("title")
                single_result["title"] = title_tag.get_text(strip=True) if title_tag else None
                desc_tag = soup.find("meta", attrs={"name": "description"})
                if desc_tag and desc_tag.get("content"):
                    single_result["metaDescription"] = desc_tag["content"].strip()
                full_text = soup.get_text(separator=" ", strip=True)
                if full_text:
                    single_result["textPreview"] = full_text[:200]
                    single_result["fullText"] = full_text
                    summary = await self.summarize_text(full_text)
                    single_result["Summary"] = summary
            else:
                single_result["error"] = f"Non-200 status code: {response.status_code}"
        except Exception as exc:
            tb = traceback.format_exc()
            logger.error("Error scraping URL",
                         extra={"url": url, "error": str(exc), "traceback": tb})
            single_result["error"] = str(exc)
        # Cache the result for 60 seconds if Redis is available
        if self.rate_limiter.redis_client:
            await self.rate_limiter.redis_client.set(f"scrape:{url}", json.dumps(single_result), ex=60)
        return single_result

    async def scrape_urls(self, urls: List[str]) -> List[Dict[str, Any]]:
        logger.debug("WebService: scrape_urls called", extra={"urls": urls})
        # Rate-limit for scraping calls
        await self.rate_limiter.check()
        # Limit concurrency with a semaphore
        sem = asyncio.Semaphore(10)
        async def sem_scrape(url):
            async with sem:
                return await self._scrape_single_url(url)
        results = await asyncio.gather(*(sem_scrape(url) for url in urls))
        return results

    async def summarize_text(self, text: str) -> str:
        """
        Calls the Venice.ai API to get a concise summary of the provided text.
        If the returned summary contains any <think>...</think> tokens, they are removed.
        """
        if not text or len(text) < 20:
            return ""
        payload = {
            "model": config.venice_model,
            "messages": [
                {"role": "system", "content": config.system_prompt},
                {"role": "user", "content": text}
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
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(config.venice_url, json=payload, headers=headers, timeout=30.0)
            response.raise_for_status()
            data = response.json()
            summary = ""
            if "choices" in data and isinstance(data["choices"], list) and len(data["choices"]) > 0:
                summary = data["choices"][0].get("message", {}).get("content", "")
                # Remove any <think>...</think> tokens if present
                summary = re.sub(r'<think>.*?</think>', '', summary, flags=re.DOTALL).strip()
            return summary
        except Exception as e:
            logger.error("Error summarizing text", extra={"error": str(e)})
            return ""

web_service = WebService()
