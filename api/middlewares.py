from fastapi import Request, HTTPException
from .config import config
from .utils import logger

async def require_api_key(request: Request):
    logger.debug("Auth middleware triggered for API key check.")
    header_key = request.headers.get("x-api-key")
    
    # Check if header matches either of the API keys
    if not header_key or (header_key != config.x_api_key and header_key != config.x_api_key_2):
        logger.warning("Unauthorized request - invalid or missing X-API-KEY header.")
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    # Additional logging to track which key was used (if debug is enabled)
    if config.enable_debug:
        if header_key == config.x_api_key:
            logger.debug("Request authenticated with primary API key.")
        elif header_key == config.x_api_key_2:
            logger.debug("Request authenticated with secondary API key.")
    else:
        logger.debug("API key validated successfully.")
