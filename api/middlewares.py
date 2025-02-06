from fastapi import Request, HTTPException
from .config import config
from .utils import logger

async def require_api_key(request: Request):
    logger.debug("Auth middleware triggered for API key check.")
    header_key = request.headers.get("x-api-key")
    if not header_key or header_key != config.x_api_key:
        logger.warning("Unauthorized request - invalid or missing X-API-KEY header.")
        raise HTTPException(status_code=401, detail="Unauthorized")
    logger.debug("API key validated successfully.")
