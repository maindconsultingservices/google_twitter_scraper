import os
import ssl
from dotenv import load_dotenv

load_dotenv()

from fastapi import Request, HTTPException
from typing import List
from .services import google_service, twitter_service, web_service
from .utils import logger
from .types import SearchMode

#
# GOOGLE controller
#
async def google_search_controller(query: str, max_results: int, timeframe: str = None):
    """
    Controller to handle the Google search request.
    Ensures validation, logs, and handles exceptions.
    """
    logger.info("Controller: google_search_controller called",
                extra={"query": query, "max_results": max_results, "timeframe": timeframe})
    if not query:
        raise HTTPException(status_code=400, detail="Missing query parameter.")
    if max_results < 1 or max_results > 1000:
        raise HTTPException(status_code=400, detail="max_results must be between 1 and 1000.")
    try:
        results = await google_service.google_search(query, max_results, timeframe)
        return {"results": results}
    except Exception as e:
        logger.error("Error in google_search_controller",
                     exc_info=True,
                     extra={"error": str(e)})
        raise HTTPException(status_code=500, detail="Failed to perform google search.")

#
# TWITTER controller
#
async def get_user_tweets(user_id: str, request: Request):
    logger.info("Controller: get_user_tweets called.", extra={
        "params": {"userId": user_id},
        "query": request.query_params
    })
    try:
        count = int(request.query_params.get("count", "10"))
        tweets = await twitter_service.get_user_tweets(user_id, count)
        return {"tweets": [t.dict(exclude_none=True) for t in tweets]}
    except Exception as e:
        logger.error("Error in get_user_tweets",
                     exc_info=True,
                     extra={"error": str(e)})
        raise HTTPException(status_code=500, detail="Failed to get user tweets.")

async def fetch_home_timeline(request: Request):
    logger.info("Controller: fetch_home_timeline called.")
    try:
        count = int(request.query_params.get("count", "10"))
        tweets = await twitter_service.fetch_home_timeline(count)
        return {"tweets": [t.dict(exclude_none=True) for t in tweets]}
    except Exception as e:
        logger.error("Error fetching home timeline",
                     exc_info=True,
                     extra={"error": str(e)})
        raise HTTPException(status_code=500, detail="Failed to fetch home timeline.")

async def fetch_following_timeline(request: Request):
    logger.info("Controller: fetch_following_timeline called.")
    try:
        count = int(request.query_params.get("count", "10"))
        tweets = await twitter_service.fetch_following_timeline(count)
        return {"tweets": [t.dict(exclude_none=True) for t in tweets]}
    except Exception as e:
        logger.error("Error fetching following timeline",
                     exc_info=True,
                     extra={"error": str(e)})
        raise HTTPException(status_code=500, detail="Failed to fetch following timeline.")

async def fetch_search_tweets(request: Request):
    logger.info("Controller: fetch_search_tweets called.", extra={"query": dict(request.query_params)})
    try:
        query = request.query_params.get("q", "")
        count = int(request.query_params.get("count", "10"))
        mode = request.query_params.get("mode", SearchMode.Latest.value)
        response = await twitter_service.fetch_search_tweets(query, count, mode)
        return {"tweets": [t.dict(exclude_none=True) for t in response.tweets]}
    except Exception as e:
        logger.error("Error fetching search tweets",
                     exc_info=True,
                     extra={"error": str(e)})
        raise HTTPException(status_code=500, detail="Failed to fetch search tweets.")

async def fetch_mentions(request: Request):
    logger.info("Controller: fetchMentions called.")
    try:
        response = await twitter_service.fetch_mentions()
        return {"tweets": [t.dict(exclude_none=True) for t in response.tweets]}
    except Exception as e:
        logger.error("Error fetching mentions",
                     exc_info=True,
                     extra={"error": str(e)})
        raise HTTPException(status_code=500, detail="Failed to fetch mentions.")

#
# WRITE / MUTATE
#
async def post_new_tweet(body: dict):
    logger.info("Controller: postNewTweet called.", extra={"body": body})
    try:
        text = body.get("text")
        if not text:
            raise HTTPException(status_code=400, detail="Missing text field.")
        tweet_id = await twitter_service.post_tweet(text)
        if not tweet_id:
            raise HTTPException(status_code=500, detail="Failed to post tweet.")
        return {"success": True, "tweetId": tweet_id}
    except Exception as e:
        logger.error("Error posting tweet",
                     exc_info=True,
                     extra={"error": str(e)})
        raise HTTPException(status_code=500, detail="Failed to post tweet.")

async def reply_to_tweet(body: dict):
    logger.info("Controller: replyToTweet called.", extra={"body": body})
    try:
        text = body.get("text")
        in_reply_to_id = body.get("inReplyToId")
        if not text or not in_reply_to_id:
            raise HTTPException(status_code=400, detail="Missing text or inReplyToId field.")
        tweet_id = await twitter_service.post_tweet(text, in_reply_to_id)
        if not tweet_id:
            raise HTTPException(status_code=500, detail="Failed to post reply.")
        return {"success": True, "tweetId": tweet_id}
    except Exception as e:
        logger.error("Error replying to tweet",
                     exc_info=True,
                     extra={"error": str(e)})
        raise HTTPException(status_code=500, detail="Failed to reply to tweet.")

async def quote_tweet(body: dict):
    logger.info("Controller: quoteTweet called.", extra={"body": body})
    try:
        text = body.get("text")
        quote_id = body.get("quoteId")
        if not text or not quote_id:
            raise HTTPException(status_code=400, detail="Missing text or quoteId field.")
        tweet_id = await twitter_service.post_quote_tweet(text, quote_id)
        if not tweet_id:
            raise HTTPException(status_code=500, detail="Failed to post quote tweet.")
        return {"success": True, "tweetId": tweet_id}
    except Exception as e:
        logger.error("Error quoting tweet",
                     exc_info=True,
                     extra={"error": str(e)})
        raise HTTPException(status_code=500, detail="Failed to quote tweet.")

async def retweet(body: dict):
    logger.info("Controller: retweet called.", extra={"body": body})
    try:
        tweet_id = body.get("tweetId")
        if not tweet_id:
            raise HTTPException(status_code=400, detail="Missing tweetId field.")
        success = await twitter_service.retweet(tweet_id)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to retweet.")
        return {"success": True}
    except Exception as e:
        logger.error("Error retweeting",
                     exc_info=True,
                     extra={"error": str(e)})
        raise HTTPException(status_code=500, detail="Failed to retweet.")

async def like_tweet(body: dict):
    logger.info("Controller: likeTweet called.", extra={"body": body})
    try:
        tweet_id = body.get("tweetId")
        if not tweet_id:
            raise HTTPException(status_code=400, detail="Missing tweetId field.")
        success = await twitter_service.like_tweet(tweet_id)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to like tweet.")
        return {"success": True}
    except Exception as e:
        logger.error("Error liking tweet",
                     exc_info=True,
                     extra={"error": str(e)})
        raise HTTPException(status_code=500, detail="Failed to like tweet.")

#
# WEB controller
#
async def scrape_urls_controller(urls: List[str], query: str):
    logger.info("Controller: scrape_urls_controller called", extra={"num_urls": len(urls), "query": query})
    if not urls:
        raise HTTPException(status_code=400, detail="No URLs provided.")
    if len(urls) > 100:
        raise HTTPException(status_code=400, detail="Too many URLs. Maximum is 100.")
    try:
        scraped_data = await web_service.scrape_urls(urls, query)
        return {"scraped": scraped_data}
    except Exception as e:
        logger.error("Error in scrape_urls_controller",
                     exc_info=True,
                     extra={"error": str(e)})
        raise HTTPException(status_code=500, detail="Failed to scrape provided URLs.")
