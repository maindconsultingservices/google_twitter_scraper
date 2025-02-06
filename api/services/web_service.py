# File: api/services/web_service.py

import traceback
from typing import List, Dict, Any

import cloudscraper
from bs4 import BeautifulSoup
from fastapi.concurrency import run_in_threadpool

from api.services.rate_limiter import RateLimiter
from api.utils.logger import logger


class WebService:
    """
    Service layer for scraping content from given URLs.
    Uses cloudscraper to bypass Cloudflare anti-bot challenges
    and BeautifulSoup for HTML parsing.
    Includes a rate limiter to prevent excessive calls.
    """
    def __init__(self):
        # Example: 5 requests per 60 seconds for direct URL scraping
        self.rate_limiter = RateLimiter(5, 60_000)

        # Pre-create a cloudscraper session
        # This session will handle all CF challenge flows automatically
        self.scraper = cloudscraper.create_scraper(
            # You can tweak these if needed:
            # interpreter='native',
            # delay=5,
            # browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True},
        )

    async def scrape_urls(self, urls: List[str]) -> List[Dict[str, Any]]:
        """
        Fetch and parse the given URLs, returning basic structured info (e.g. title, meta description, etc.).
        We use cloudscraper to handle Cloudflare, then parse the HTML with BeautifulSoup.
        """
        logger.debug("WebService: scrape_urls called", extra={"urls": urls})

        # Rate-limit for scraping calls
        self.rate_limiter.check()

        results = []

        for url in urls:
            single_result = {
                "url": url,
                "status": None,
                "error": None,
                "title": None,
                "metaDescription": None,
                "textPreview": None,
                "fullText": None,
            }

            try:
                # Because cloudscraper is synchronous, run it in a threadpool
                response = await run_in_threadpool(
                    lambda: self.scraper.get(url, timeout=10)
                )
                single_result["status"] = response.status_code

                if response.status_code == 200:
                    soup = BeautifulSoup(response.text, "html.parser")

                    # Extract title if present
                    title_tag = soup.find("title")
                    single_result["title"] = title_tag.get_text(strip=True) if title_tag else None

                    # Extract meta description if present
                    desc_tag = soup.find("meta", attrs={"name": "description"})
                    if desc_tag and desc_tag.get("content"):
                        single_result["metaDescription"] = desc_tag["content"].strip()

                    # Get the entire text (separator=" ", strip=True removes extra whitespace)
                    full_text = soup.get_text(separator=" ", strip=True)

                    # Keep a short preview for quick scanning
                    if full_text:
                        single_result["textPreview"] = full_text[:200]
                        single_result["fullText"] = full_text
                else:
                    single_result["error"] = f"Non-200 status code: {response.status_code}"

            except Exception as exc:
                tb = traceback.format_exc()
                logger.error("Error scraping URL",
                             extra={"url": url, "error": str(exc), "traceback": tb})
                single_result["error"] = str(exc)

            results.append(single_result)

        return results


# Instantiate our web scraping service
web_service = WebService()
