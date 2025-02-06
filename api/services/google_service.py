import traceback
from typing import List

from fastapi.concurrency import run_in_threadpool
from googlesearch import search

from api.services.rate_limiter import RateLimiter
from api.utils.logger import logger


class GoogleService:
    """
    Service layer for performing Google searches using the googlesearch library.
    Includes a rate limiter to prevent excessive calls.
    """
    def __init__(self):
        # Example: 10 requests per 60 seconds for Google queries
        self.rate_limiter_google = RateLimiter(10, 60_000)

    async def google_search(self, query: str, max_results: int) -> List[str]:
        """
        Perform a Google search with the specified query and number of results.
        Uses `run_in_threadpool` because googlesearch is synchronous.
        """
        logger.debug("GoogleService: google_search called",
                     extra={"query": query, "max_results": max_results})

        self.rate_limiter_google.check()

        try:
            # googlesearch.search(...) returns a generator; convert to list
            results = await run_in_threadpool(lambda: list(search(query, num_results=max_results)))
            return results
        except Exception as e:
            tb = traceback.format_exc()
            logger.error("Error in google_search method",
                         extra={"error": str(e), "traceback": tb})
            raise


# Instantiate our service
google_service = GoogleService()
