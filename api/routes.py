from fastapi import APIRouter, Request, Depends, Query
from typing import List
from pydantic import BaseModel

from .middlewares import require_api_key
from .controllers import (
    google_search_controller,
    get_user_tweets,
    fetch_home_timeline,
    fetch_following_timeline,
    fetch_search_tweets,
    fetch_mentions,
    post_new_tweet,
    reply_to_tweet,
    quote_tweet,
    retweet,
    like_tweet,
    scrape_urls_controller
)
from .utils import logger

#
# We define separate routers here: google_router, twitter_router, web_router
# All are combined in index.py
#

# ------------------ GOOGLE ROUTES ------------------
google_router = APIRouter()

@google_router.get("/search")
async def google_search_route(
    user_request: Request,
    query: str,
    max_results: int = 10,
    sites: List[str] = Query(None),
    timeframe: str = None,
    _=Depends(require_api_key)
):
    """
    GET /google/search => run google_search_controller
    Optionally restricts the search to one or several sites by using the "sites" query parameter.
    When multiple sites are provided, they are grouped using parentheses and joined with the OR operator.
    A new query parameter "timeframe" (allowed values: "24h", "week", "month", "year") enables time-based filtering.
    """
    logger.debug("Route GET /google/search called", extra={"query": query, "max_results": max_results, "sites": sites, "timeframe": timeframe})
    if sites:
        if len(sites) > 1:
            sites_query = "(" + " OR ".join(f"site:{s}" for s in sites) + ")"
        else:
            sites_query = f"site:{sites[0]}"
        query = f"{query} {sites_query}"
    return await google_search_controller(query, max_results, timeframe)

# ------------------ TWITTER ROUTES ------------------
twitter_router = APIRouter()

@twitter_router.get("/user/{user_id}/tweets")
async def get_user_tweets_route(user_id: str, request: Request, _=Depends(require_api_key)):
    logger.debug("Route GET /twitter/user/{user_id}/tweets called.")
    return await get_user_tweets(user_id, request)

@twitter_router.get("/home")
async def fetch_home_timeline_route(request: Request, _=Depends(require_api_key)):
    logger.debug("Route GET /twitter/home called.")
    return await fetch_home_timeline(request)

@twitter_router.get("/following")
async def fetch_following_timeline_route(request: Request, _=Depends(require_api_key)):
    logger.debug("Route GET /twitter/following called.")
    return await fetch_following_timeline(request)

@twitter_router.get("/search")
async def fetch_search_tweets_route(request: Request, _=Depends(require_api_key)):
    logger.debug("Route GET /twitter/search called.")
    return await fetch_search_tweets(request)

@twitter_router.get("/mentions")
async def fetch_mentions_route(request: Request, _=Depends(require_api_key)):
    logger.debug("Route GET /twitter/mentions called.")
    return await fetch_mentions(request)

@twitter_router.post("/tweet")
async def post_new_tweet_route(body: dict, _=Depends(require_api_key)):
    logger.debug("Route POST /twitter/tweet called.")
    return await post_new_tweet(body)

@twitter_router.post("/reply")
async def reply_to_tweet_route(body: dict, _=Depends(require_api_key)):
    logger.debug("Route POST /twitter/reply called.")
    return await reply_to_tweet(body)

@twitter_router.post("/quote")
async def quote_tweet_route(body: dict, _=Depends(require_api_key)):
    logger.debug("Route POST /twitter/quote called.")
    return await quote_tweet(body)

@twitter_router.post("/retweet")
async def retweet_route(body: dict, _=Depends(require_api_key)):
    logger.debug("Route POST /twitter/retweet called.")
    return await retweet(body)

@twitter_router.post("/like")
async def like_tweet_route(body: dict, _=Depends(require_api_key)):
    logger.debug("Route POST /twitter/like called.")
    return await like_tweet(body)

# ------------------ WEB ROUTES ------------------
web_router = APIRouter()

class UrlsPayload(BaseModel):
    """
    Pydantic model for the incoming request body to scrape multiple URLs.
    Expects JSON in the format: {"urls": ["https://site1.com", "https://site2.com", ...]}.
    """
    urls: List[str]

@web_router.post("/scrape")
async def scrape_urls_route(
    request: Request,
    body: UrlsPayload,
    _=Depends(require_api_key)
):
    logger.debug("Route POST /web/scrape called", extra={"urls": body.urls})
    logger.debug(f"Request headers: {dict(request.headers)}")
    if request.client:
        logger.debug(f"Request client host: {request.client.host}")
    raw_body = await request.body()
    logger.debug(f"Raw request body (decoded): {raw_body.decode('utf-8', errors='replace')}")
    return await scrape_urls_controller(body.urls)
