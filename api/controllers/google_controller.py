from fastapi import HTTPException
from api.utils.logger import logger
from api.services.google_service import google_service

async def google_search_controller(query: str, max_results: int):
    """
    Controller to handle the Google search request.
    Ensures validation, logs, and handles exceptions.
    """
    logger.info("Controller: google_search_controller called",
                extra={"query": query, "max_results": max_results})

    if not query:
        raise HTTPException(status_code=400, detail="Missing query parameter.")

    if max_results < 1 or max_results > 1000:
        raise HTTPException(status_code=400, detail="max_results must be between 1 and 1000.")

    try:
        results = await google_service.google_search(query, max_results)
        return {"results": results}
    except Exception as e:
        logger.error("Error in google_search_controller",
                     exc_info=True,
                     extra={"error": str(e)})
        raise HTTPException(status_code=500, detail="Failed to perform google search.")
