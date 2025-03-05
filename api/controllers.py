import os
import ssl
from dotenv import load_dotenv
import unicodedata
load_dotenv()

from fastapi import Request, HTTPException
from typing import List, Tuple
from .services import google_service, twitter_service, web_service
from .services.linkedin_service import linkedin_service
from .utils import logger
from .types import SearchMode
from .config import config

def normalize_query(query: str) -> Tuple[str, str]:
    """
    Normalize query to handle accented characters by providing both versions:
    - original: The original query as provided by the user
    - normalized: A version with accents removed for better compatibility
    """
    # Keep the original query
    original = query
    
    # Create a normalized version (accents removed)
    normalized = ''.join(
        c for c in unicodedata.normalize('NFD', query)
        if not unicodedata.combining(c)
    )
    
    return original, normalized

#
# LINKEDIN controller
#
async def find_candidates_controller(body: dict):
    """
    Controller to handle LinkedIn candidate search requests.
    """
    logger.info("Controller: find_candidates_controller called", extra={"body": body})
    
    if config.enable_debug:
        logger.debug("DEBUG INPUT find_candidates_controller", extra=body)
    
    # Validate required parameters
    if not body.get("job_title"):
        raise HTTPException(status_code=400, detail="Missing job_title parameter.")
    
    # Validate limit
    limit = body.get("limit", 10)
    if limit < 1 or limit > 100:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 100.")
    
    try:
        # Call the LinkedIn service to search for candidates
        result = await linkedin_service.find_candidates(body)
        
        # Check if there was an error
        if "error" in result:
            logger.error(
                f"LinkedIn candidate search failed: {result['error']} - {result['message']}"
            )
            raise HTTPException(status_code=500, detail=result["message"])
        
        if config.enable_debug:
            logger.debug("DEBUG OUTPUT find_candidates_controller", extra=result)
        
        logger.info(
            "LinkedIn candidate search completed",
            extra={
                "total_found": result["total_found"],
                "candidates_returned": len(result["candidates"])
            }
        )
        
        return result
        
    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        logger.error("Error in find_candidates_controller",
                     exc_info=True,
                     extra={"error": str(e)})
        raise HTTPException(status_code=500, detail="Failed to search for candidates.")


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
    if config.enable_debug:
        logger.debug("DEBUG INPUT google_search_controller", extra={
            "query": query,
            "max_results": max_results,
            "timeframe": timeframe
        })
    if not query:
        raise HTTPException(status_code=400, detail="Missing query parameter.")
    if max_results < 1 or max_results > 1000:
        raise HTTPException(status_code=400, detail="max_results must be between 1 and 1000.")
    try:
        # Get both original and normalized versions of the query
        original_query, normalized_query = normalize_query(query)
        
        # First try with original query
        search_results, effective_tf = await google_service.google_search(original_query, max_results, timeframe)
        
        # If no results, try with normalized query
        if not search_results and original_query != normalized_query:
            logger.info("No results with original query, trying normalized version", 
                        extra={"original": original_query, "normalized": normalized_query})
            search_results, effective_tf = await google_service.google_search(normalized_query, max_results, timeframe)
        
        response_payload = {"results": search_results, "timeframe": effective_tf}
        if config.enable_debug:
            logger.debug("DEBUG OUTPUT google_search_controller", extra=response_payload)
        return response_payload
    except Exception as e:
        logger.error("Error in google_search_controller",
                     exc_info=True,
                     extra={"error": str(e)})
        raise HTTPException(status_code=500, detail="Failed to perform google search.")

#
# GOOGLE SEARCH AND SCRAPE controller
#
async def google_search_and_scrape_controller(query: str, max_results: int, timeframe: str = None):
    """
    Controller to handle the combined Google search and web scrape request.
    First performs a Google search, then scrapes the resulting URLs.
    """
    logger.info("Controller: google_search_and_scrape_controller called",
                extra={"query": query, "max_results": max_results, "timeframe": timeframe})
    
    if config.enable_debug:
        logger.debug("DEBUG INPUT google_search_and_scrape_controller", extra={
            "query": query,
            "max_results": max_results,
            "timeframe": timeframe
        })
    
    # Validation checks
    if not query:
        raise HTTPException(status_code=400, detail="Missing query parameter.")
    if max_results < 1 or max_results > 100:  # Lower max than google_search to prevent timeout
        raise HTTPException(status_code=400, detail="max_results must be between 1 and 100.")
        
    try:
        # Step 1: Perform Google search
        original_query, normalized_query = normalize_query(query)
        
        # First try with original query
        search_results, effective_tf = await google_service.google_search(original_query, max_results, timeframe)
        
        # If no results, try with normalized query
        if not search_results and original_query != normalized_query:
            logger.info("No results with original query, trying normalized version", 
                        extra={"original": original_query, "normalized": normalized_query})
            search_results, effective_tf = await google_service.google_search(normalized_query, max_results, timeframe)
        
        if not search_results:
            logger.info("No search results found for query", extra={"query": query})
            return {"scraped": [], "timeframe": effective_tf}
            
        # Step 2: Scrape the URLs returned by the search
        scraped_data = await web_service.scrape_urls(search_results, query)
        
        response_payload = {"scraped": scraped_data, "timeframe": effective_tf}
        if config.enable_debug:
            logger.debug("DEBUG OUTPUT google_search_and_scrape_controller", extra=response_payload)
        
        return response_payload
    except Exception as e:
        logger.error("Error in google_search_and_scrape_controller",
                     exc_info=True,
                     extra={"error": str(e)})
        raise HTTPException(status_code=500, detail="Failed to perform search and scrape operation.")

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
        
        # Apply the same normalization logic to tweet searches
        original_query, normalized_query = normalize_query(query)
        
        # First try with original query
        response = await twitter_service.fetch_search_tweets(original_query, count, mode)
        
        # If no results, try with normalized query
        if not response.tweets and original_query != normalized_query:
            logger.info("No twitter results with original query, trying normalized version", 
                       extra={"original": original_query, "normalized": normalized_query})
            response = await twitter_service.fetch_search_tweets(normalized_query, count, mode)
            
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
    if config.enable_debug:
        logger.debug("DEBUG INPUT scrape_urls_controller", extra={"urls": urls, "query": query})
    if not urls:
        raise HTTPException(status_code=400, detail="No URLs provided.")
    if len(urls) > 100:
        raise HTTPException(status_code=400, detail="Too many URLs. Maximum is 100.")
    try:
        scraped_data = await web_service.scrape_urls(urls, query)
        response_payload = {"scraped": scraped_data}
        if config.enable_debug:
            logger.debug("DEBUG OUTPUT scrape_urls_controller", extra=response_payload)
        return response_payload
    except Exception as e:
        logger.error("Error in scrape_urls_controller",
                     exc_info=True,
                     extra={"error": str(e)})
        raise HTTPException(status_code=500, detail="Failed to scrape provided URLs.")
