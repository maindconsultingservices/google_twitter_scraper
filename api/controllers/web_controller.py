from fastapi import HTTPException
from typing import List

from api.utils.logger import logger
from api.services.web_service import web_service


async def scrape_urls_controller(urls: List[str]):
    """
    Controller to handle scraping of one or several URLs via BeautifulSoup4 + cloudscraper.
    Uses the new web_service's 'scrape_urls' method.
    """
    logger.info("Controller: scrape_urls_controller called",
                extra={"num_urls": len(urls)})

    if not urls:
        raise HTTPException(status_code=400, detail="No URLs provided.")

    if len(urls) > 100:
        raise HTTPException(status_code=400, detail="Too many URLs. Maximum is 100.")

    try:
        scraped_data = await web_service.scrape_urls(urls)
        return {"scraped": scraped_data}
    except Exception as e:
        logger.error("Error in scrape_urls_controller",
                     exc_info=True,
                     extra={"error": str(e)})
        raise HTTPException(status_code=500, detail="Failed to scrape provided URLs.")
