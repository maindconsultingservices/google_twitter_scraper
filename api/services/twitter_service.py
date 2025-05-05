import time
import json
import traceback
import asyncio
from typing import List, Dict, Any, Optional
import os

from fastapi.concurrency import run_in_threadpool

from twitter.account import Account
from twitter.scraper import Scraper
from twitter.search import Search

from ..config import config
from ..types import Tweet, QueryTweetsResponse, SearchMode
from ..utils import logger
from .rate_limiter import RateLimiter

class TwitterClientManager:
    def __init__(self):
        self._account = None
        self._scraper = None
        self._search = None
        self._logged_in = False
        self._cookies_store = None

    def _init_account(self) -> Account:
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
        if not self._account:
            logger.debug("No existing Account found; calling _init_account now.")
            self._account = self._init_account()
            logger.info("Account instance created.")
        else:
            logger.debug("Reusing existing Account instance.")
        return self._account

    def get_scraper(self) -> Scraper:
        if not self._scraper:
            logger.debug("No existing Scraper; about to retrieve account/cookies for the Scraper.")
            self.get_account()
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
        if not self._search:
            logger.debug("No existing Search instance; creating a new one.")
            logger.info("Creating Search instance for advanced queries.")
            self.get_account()

            output_dir = "/tmp/twitter_search"
            os.makedirs(output_dir, exist_ok=True)

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
                    logger.info("Search instance created from cookies store.")
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

class TwitterService:
    def __init__(self):
        self.rate_limiter = RateLimiter(15, 60_000)

    async def _ensure_login(self):
        logger.debug("_ensure_login called. Checking is_logged_in() on twitter_client_manager.")
        if not twitter_client_manager.is_logged_in():
            logger.debug("twitter_client_manager reports not logged in. Raising RuntimeError.")
            raise RuntimeError("Not logged into Twitter. Check your cookies or credentials.")
        logger.debug("twitter_client_manager is logged in successfully.")

    def get_profile(self):
        return {"username": getattr(config, "twitter_username", "unknown"), "id": "0"}

    async def get_user_tweets(self, user_id: str, count: int) -> List[Tweet]:
        logger.debug("Service: get_user_tweets invoked", extra={"user_id": user_id, "count": count})
        await self.rate_limiter.check()
        await self._ensure_login()

        scraper = twitter_client_manager.get_scraper()
        numeric_id = int(user_id)
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
            # Ensure writes happen in a writable directory
            scratch_dir = "/tmp/twitter_search"
            os.makedirs(scratch_dir, exist_ok=True)
            previous_cwd = os.getcwd()
            os.chdir(scratch_dir)
            try:
                results = await run_in_threadpool(
                    search_client.run,
                    queries=queries,
                    limit=max_tweets,
                    save=False,
                    debug=False
                )
            finally:
                os.chdir(previous_cwd)

            logger.debug("search_client.run() returned", extra={
                "type": str(type(results)),
                "full_results_str": str(results),
                "count": len(results) if isinstance(results, list) else "N/A"
            })

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

                q_count = int(legacy.get("quote_count", 0))
                r_count = int(legacy.get("reply_count", 0))
                rt_count = int(legacy.get("retweet_count", 0))

                core_user = data.get("core", {}).get("user_results", {}).get("result", {})
                if isinstance(core_user, dict):
                    user_id_str = str(core_user.get("rest_id") or "0")
                    if "legacy" in core_user:
                        uname = core_user["legacy"].get("screen_name", "unknown")
                else:
                    uname = data.get("username") or data.get("user_screen_name") or "unknown"
            else:
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

            if isinstance(item, dict) and "tweets" in item and isinstance(item["tweets"], list):
                extracted_count = len(item["tweets"])
                if config.enable_debug:
                    logger.debug(f"_flatten_search_results: Found {extracted_count} tweets in item index={idx}.")
                flattened_tweets.extend(item["tweets"])
                continue

            elif isinstance(item, dict) and ("entryId" in item or "entries" in item or "data" in item):
                extracted = self._extract_from_new_instructions(item)
                if config.enable_debug:
                    logger.debug(f"_flatten_search_results: Extracted {len(extracted)} from item index={idx} with entryId/data/entries.")
                flattened_tweets.extend(extracted)
                continue

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
        collected = []

        try:
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

            elif "data" in data_item and isinstance(data_item["data"], dict):
                search_obj = data_item["data"].get("search_by_query")
                if search_obj and isinstance(search_obj, dict):
                    instructions = search_obj.get("instructions", [])
                    for inst in instructions:
                        if isinstance(inst, dict) and "entries" in inst:
                            collected.extend(self._collect_entries(inst["entries"]))

            elif "instructions" in data_item and isinstance(data_item["instructions"], list):
                for inst in data_item["instructions"]:
                    if isinstance(inst, dict) and "entries" in inst:
                        collected.extend(self._collect_entries(inst["entries"]))

            elif "entries" in data_item and isinstance(data_item["entries"], list):
                collected.extend(self._collect_entries(data_item["entries"]))

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

            deeper = self._extract_tweets_deep(content)
            results.extend(deeper)

        except Exception as ex:
            logger.debug(
                "Could not extract tweet dict from entry",
                extra={"error": str(ex), "raw": entry}
            )
        return results

    def _extract_tweets_deep(self, node: Any) -> List[dict]:
        found = []

        if isinstance(node, dict):
            if "tweet_results" in node and isinstance(node["tweet_results"], dict):
                maybe_tweet = node["tweet_results"].get("result")
                if isinstance(maybe_tweet, dict):
                    found.append(maybe_tweet)
            for v in node.values():
                found.extend(self._extract_tweets_deep(v))

        elif isinstance(node, list):
            for item in node:
                found.extend(self._extract_tweets_deep(item))

        return found

twitter_service = TwitterService()
