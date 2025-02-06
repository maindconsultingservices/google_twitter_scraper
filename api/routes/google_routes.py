from fastapi import APIRouter, Request, Depends
from api.middlewares.auth import require_api_key
from api.controllers import google_controller
from api.utils.logger import logger

google_router = APIRouter()

@google_router.get("/search")
async def google_search_route(
    user_request: Request,
    query: str,
    max_results: int = 10,
    _=Depends(require_api_key)
):
    """
    Scrape Google results for the given 'query' using the googlesearch library,
    returning up to 'max_results' URLs.
    Protected by the X-API-KEY check via require_api_key.
    """
    logger.debug("Route GET /google/search called", extra={"query": query, "max_results": max_results})
    return await google_controller.google_search_controller(query, max_results)
