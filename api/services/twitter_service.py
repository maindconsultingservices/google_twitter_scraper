# File: api/services/twitter_service.py

import time
import json
import traceback
from typing import List, Any

# Added import for run_in_threadpool to fix asyncio.run() conflict
from fastapi.concurrency import run_in_threadpool

from api.config.env import config
from api.types import Tweet, QueryTweetsResponse, SearchMode
from api.services.rate_limiter import RateLimiter
from api.utils.logger import logger

from api.services.twitter_client import twitter_client_manager


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
        return {"username": config.twitter_username or "unknown", "id": "0"}

    # ================== READ METHODS =====================
    async def get_user_tweets(self, user_id: str, count: int) -> List[Tweet]:
        """
        Retrieve tweets from a given user using the Scraper interface.
        This uses `scraper.tweets(...)` which fetches a user's timeline.
        """
        logger.debug("Service: get_user_tweets invoked", extra={"user_id": user_id, "count": count})
        self.rate_limiter.check()
        await self._ensure_login()

        scraper = twitter_client_manager.get_scraper()
        numeric_id = int(user_id)  # might fail if not numeric

        raw_tweets = scraper.tweets([numeric_id], limit=count)
        return self._parse_tweets(raw_tweets)

    async def fetch_home_timeline(self, count: int) -> List[Tweet]:
        """
        Retrieve home timeline tweets using `account.home_timeline()`.
        """
        logger.debug("Service: fetch_home_timeline invoked", extra={"count": count})
        self.rate_limiter.check()
        await self._ensure_login()

        account = twitter_client_manager.get_account()
        timeline_data = account.home_timeline(limit=count) or []

        if config.enable_debug:
            # Attempt to pretty-print as JSON; fallback to repr if needed
            try:
                logger.debug("Raw home_timeline data:\n%s",
                             json.dumps(timeline_data, ensure_ascii=False, indent=2))
            except Exception:
                logger.debug("Raw home_timeline data (repr): %r", timeline_data)

        return self._parse_account_timeline(timeline_data)

    async def fetch_following_timeline(self, count: int) -> List[Tweet]:
        """
        Retrieve the "Following" (latest) timeline using `account.home_latest_timeline()`.
        """
        logger.debug("Service: fetch_following_timeline invoked", extra={"count": count})
        self.rate_limiter.check()
        await self._ensure_login()

        account = twitter_client_manager.get_account()
        timeline_data = account.home_latest_timeline(limit=count) or []

        if config.enable_debug:
            # Attempt to pretty-print as JSON; fallback to repr if needed
            try:
                logger.debug("Raw home_latest_timeline data:\n%s",
                             json.dumps(timeline_data, ensure_ascii=False, indent=2))
            except Exception:
                logger.debug("Raw home_latest_timeline data (repr): %r", timeline_data)

        return self._parse_account_timeline(timeline_data)

    async def fetch_search_tweets(self, query: str, max_tweets: int, mode: str) -> QueryTweetsResponse:
        """
        Perform a search using the `Search` interface from twitter-api-client.
        """
        logger.debug("Service: fetch_search_tweets called", extra={"query": query, "max_tweets": max_tweets, "mode": mode})
        self.rate_limiter.check()
        await self._ensure_login()

        # Acquire the search client
        search_client = twitter_client_manager.get_search()

        # Convert our 'mode' to the library's 'category' field
        category_map = {
            SearchMode.Latest.value: "Latest",
            SearchMode.Top.value: "Top",
            SearchMode.People.value: "People",
            SearchMode.Photos.value: "Photos",
            SearchMode.Videos.value: "Videos"
        }
        category = category_map.get(mode, "Top")

        # The library typically wants queries=[{'category': 'Latest', 'query': '...'}]
        queries = [
            {
                "category": category,
                "query": query
            }
        ]

        logger.debug("About to call search_client.run() with a single query entry", extra={
            "queries": queries,
            "limit": max_tweets
        })

        results = None
        try:
            # Use run_in_threadpool to avoid asyncio.run() conflict
            results = await run_in_threadpool(
                search_client.run,
                queries=queries,
                limit=max_tweets,
                save=False,                 # ensure it doesn't save to 'data/search_results'
                debug=False,                # no file-based logs
                output_dir="/tmp/twitter_search"  # point to a writable /tmp directory
            )

            # Log with full detail
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

        # Flatten out tweets from any known shapes:
        all_tweets_data = self._flatten_search_results(results)

        # Map each raw tweet dict to our schema
        found = []
        for tweet_dict in all_tweets_data:
            parsed = self._map_tweet_item(tweet_dict)
            if parsed:
                found.append(parsed)

        return QueryTweetsResponse(tweets=found)

    async def fetch_mentions(self) -> QueryTweetsResponse:
        """
        Demonstration for 'mentions' by searching '@username' in Latest mode.
        """
        profile = self.get_profile()
        username = profile["username"] if profile else ""
        if not username:
            logger.warning("fetchMentions: Twitter not logged in.")
            return QueryTweetsResponse(tweets=[])

        count = 10
        search_results = await self.fetch_search_tweets(f"@{username}", count, SearchMode.Latest.value)
        return search_results

    # ================== WRITE METHODS =====================
    async def post_tweet(self, text: str, in_reply_to_id: str = None) -> str | None:
        """
        Post a new tweet (or reply to an existing tweet).
        """
        logger.debug("Service: post_tweet called", extra={"text": text, "inReplyToId": in_reply_to_id})
        self.rate_limiter.check()
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
        """
        Post a quote tweet with the given text referencing an existing tweet.
        """
        logger.debug("Service: post_quote_tweet called", extra={"text": text, "quoteId": quote_id})
        self.rate_limiter.check()
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
        """
        Retweet the tweet specified by `tweet_id`.
        """
        logger.debug("Service: retweet called", extra={"tweetId": tweet_id})
        self.rate_limiter.check()
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
        """
        Like the tweet specified by `tweet_id`.
        """
        logger.debug("Service: like_tweet called", extra={"tweetId": tweet_id})
        self.rate_limiter.check()
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

    # ================== HELPERS =====================
    def _parse_tweets(self, raw_items) -> List[Tweet]:
        """Convert the scraper.tweets() output into our Tweet schema."""
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
        """
        Convert the account.home_timeline() or account.home_latest_timeline() output.
        Some responses include instructions-based data, so we reuse the same flatten logic.
        """
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
        """
        Attempts to parse a tweet-like dict into our Tweet model, including
        conversation ID, stats like replyCount, retweetCount, quoteCount, etc.
        """
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
                # Fallback for older shapes
                raw_text = data.get("text", "")
                if not raw_text and "note_tweet" in data:
                    raw_text = self._extract_note_tweet_text(data["note_tweet"])

                text = raw_text
                uname = data.get("username") or data.get("user_screen_name") or "unknown"
                user_id_str = str(data.get("user_id") or "0")
                conv_id = str(data.get("conversation_id") or "0")
                # If stats exist in this older shape, parse them as well
                q_count = int(data.get("quote_count", 0))
                r_count = int(data.get("reply_count", 0))
                rt_count = int(data.get("retweet_count", 0))

            # Just store "now" in seconds if no direct timestamp
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
        """
        Some tweets store their full text in note_tweet -> note_tweet_results -> result -> text
        for extended tweets. We'll parse that if needed.
        """
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

        # If the library returned a JSON string for us, parse it here:
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
            # -- 1) If top-level item is a dict with "entryId" that starts with "tweet-",
            #       parse it directly:
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

            # -- 2) Possibly older search shapes: "tweets" => [...]
            if isinstance(item, dict) and "tweets" in item and isinstance(item["tweets"], list):
                extracted_count = len(item["tweets"])
                if config.enable_debug:
                    logger.debug(f"_flatten_search_results: Found {extracted_count} tweets in item index={idx}.")
                flattened_tweets.extend(item["tweets"])
                continue

            # -- 3) If we detect "entryId" or "entries" or "data", handle instructions or single entry
            elif isinstance(item, dict) and ("entryId" in item or "entries" in item or "data" in item):
                extracted = self._extract_from_new_instructions(item)
                if config.enable_debug:
                    logger.debug(f"_flatten_search_results: Extracted {len(extracted)} from item index={idx} with entryId/data/entries.")
                flattened_tweets.extend(extracted)
                continue

            # -- 4) Possibly nested arrays
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
                # fallback raw
                flattened_tweets.append(item)

        if config.enable_debug:
            logger.debug(f"_flatten_search_results: total flattened tweets={len(flattened_tweets)}")
        return flattened_tweets

    def _extract_from_new_instructions(self, data_item):
        """
        Attempt to parse instructions-based structures or single "entryId" structures.

        Adjusted to handle data->home->home_timeline_urt->instructions,
        as used by the Home/Following timeline.
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

            # 2) data->search_by_query->instructions  (original logic)
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
        """
        Gather potential tweets from an array of 'entry' objects. Some are cursors, some are tweets.
        """
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
        A fallback to recursively search for any 'tweet_results' -> 'result' inside a dict/array,
        used when the standard 'itemContent' path fails.
        """
        found = []

        if isinstance(node, dict):
            # If we see "tweet_results" -> "result"
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
