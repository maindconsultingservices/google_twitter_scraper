from fastapi import APIRouter, Request, Depends
from typing import List
from pydantic import BaseModel

from api.middlewares.auth import require_api_key
from api.controllers import web_controller
from api.utils.logger import logger

web_router = APIRouter()

class UrlsPayload(BaseModel):
    """
    Pydantic model for the incoming request body to scrape multiple URLs.
    """
    urls: List[str]

@web_router.post("/scrape")
async def scrape_urls_route(
    request: Request,
    body: UrlsPayload,
    _=Depends(require_api_key)
):
    """
    Scrape content from one or several URLs using BeautifulSoup4.
    Expects JSON in the format: {"urls": ["https://site1.com", "https://site2.com", ...]}.
    """
    logger.debug("Route POST /web/scrape called", extra={"urls": body.urls})

    # Extra debugging around the incoming request
    logger.debug(f"Request headers: {dict(request.headers)}")
    if request.client:
        logger.debug(f"Request client host: {request.client.host}")
    raw_body = await request.body()
    logger.debug(f"Raw request body (decoded): {raw_body.decode('utf-8', errors='replace')}")

    return await web_controller.scrape_urls_controller(body.urls)
