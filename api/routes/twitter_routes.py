from fastapi import APIRouter, Request, Depends
from api.middlewares.auth import require_api_key
from api.controllers import twitter_controller
from api.utils.logger import logger

twitter_router = APIRouter()

@twitter_router.get("/user/{user_id}/tweets")
async def get_user_tweets_route(user_id: str, request: Request, _=Depends(require_api_key)):
    logger.debug("Route GET /twitter/user/{user_id}/tweets called.")
    return await twitter_controller.get_user_tweets(user_id, request)

@twitter_router.get("/home")
async def fetch_home_timeline_route(request: Request, _=Depends(require_api_key)):
    logger.debug("Route GET /twitter/home called.")
    return await twitter_controller.fetch_home_timeline(request)

@twitter_router.get("/following")
async def fetch_following_timeline_route(request: Request, _=Depends(require_api_key)):
    logger.debug("Route GET /twitter/following called.")
    return await twitter_controller.fetch_following_timeline(request)

@twitter_router.get("/search")
async def fetch_search_tweets_route(request: Request, _=Depends(require_api_key)):
    logger.debug("Route GET /twitter/search called.")
    return await twitter_controller.fetch_search_tweets(request)

@twitter_router.get("/mentions")
async def fetch_mentions_route(request: Request, _=Depends(require_api_key)):
    logger.debug("Route GET /twitter/mentions called.")
    return await twitter_controller.fetch_mentions(request)

# REMOVED: conversation thread & list timeline endpoints for full alignment with twitter-api-client

@twitter_router.post("/tweet")
async def post_new_tweet_route(body: dict, _=Depends(require_api_key)):
    logger.debug("Route POST /twitter/tweet called.")
    return await twitter_controller.post_new_tweet(body)

@twitter_router.post("/reply")
async def reply_to_tweet_route(body: dict, _=Depends(require_api_key)):
    logger.debug("Route POST /twitter/reply called.")
    return await twitter_controller.reply_to_tweet(body)

@twitter_router.post("/quote")
async def quote_tweet_route(body: dict, _=Depends(require_api_key)):
    logger.debug("Route POST /twitter/quote called.")
    return await twitter_controller.quote_tweet(body)

@twitter_router.post("/retweet")
async def retweet_route(body: dict, _=Depends(require_api_key)):
    logger.debug("Route POST /twitter/retweet called.")
    return await twitter_controller.retweet(body)

@twitter_router.post("/like")
async def like_tweet_route(body: dict, _=Depends(require_api_key)):
    logger.debug("Route POST /twitter/like called.")
    return await twitter_controller.like_tweet(body)
