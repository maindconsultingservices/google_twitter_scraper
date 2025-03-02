# api/services/web_service.py
import os
import time
import json
import traceback
import asyncio
import random
import re
from typing import List, Dict, Any, Tuple, Optional
from urllib.parse import urlparse

from fastapi.concurrency import run_in_threadpool
import cloudscraper
from bs4 import BeautifulSoup
import httpx

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

from ..config import config
from ..utils import logger
from .rate_limiter import RateLimiter

MAX_TEXT_LENGTH_TO_SUMMARIZE = int(os.getenv("MAX_TEXT_LENGTH_TO_SUMMARIZE", "5000"))

class WebService:
    """
    Service layer for scraping content from given URLs.
    Uses cloudscraper to bypass Cloudflare anti-bot challenges
    and BeautifulSoup for HTML parsing.
    Includes a rate limiter to prevent excessive calls.
    """
    def __init__(self):
        self.rate_limiter = RateLimiter(5, 60_000)
        # This session will handle CF challenge flows automatically and maintain cookies between requests.
        self.scraper = self._create_scraper()
        # Add a dedicated rate limiter for Venice API calls (20 per minute per user)
        self.venice_rate_limiter = RateLimiter(20, 60_000)
        
    def _create_scraper(self):
        """Create a new cloudscraper instance with optimal settings"""
        # Define a list of common user agents to rotate between
        user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.3 Safari/605.1.15",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36 Edg/116.0.1938.69",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/117.0"
        ]
        
        # Select a random user agent
        user_agent = random.choice(user_agents)
        
        # Create the scraper with enhanced settings
        scraper = cloudscraper.create_scraper(
            browser={
                'browser': 'chrome',
                'platform': 'windows',
                'desktop': True,
                'mobile': False
            },
            delay=5  # Allow more time for challenge solving
        )
        
        # Set headers to mimic a real browser more closely
        scraper.headers.update({
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Referer": "https://www.google.com/",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "cross-site",
            "Sec-Fetch-User": "?1",
            "Cache-Control": "max-age=0"
        })
        
        return scraper

    def _is_valid_url(self, url: str) -> bool:
        """
        Checks if the URL is valid.
        """
        try:
            parsed = urlparse(url)
            return bool(parsed.scheme and parsed.netloc)
        except Exception:
            return False

    def _is_readable(self, text: str) -> bool:
        """
        Determines if the extracted text content is readable.
        """
        if not text or len(text.strip()) < 50:  # Require at least 50 chars of content
            return False
            
        # If more than 30% of the characters are the replacement character "�", consider it unreadable
        if text.count("�") / max(len(text), 1) > 0.3:
            return False
            
        # Check for common indicators of failed scraping
        low_content_markers = [
            "access denied",
            "captcha required",
            "please enable javascript",
            "please enable cookies",
            "bot protection",
            "ddos protection",
            "blocked",
            "attention required",
            "cloudflare",
            "human verification",
        ]
        
        # Only consider it unreadable if the text is very short AND contains low content markers
        lower_text = text.lower()
        if len(text) < 500 and any(marker in lower_text for marker in low_content_markers):
            return False
            
        # New: Check for actual content presence
        # If the text has reasonable length and contains sentences, it's likely readable
        if len(text) > 200 and "." in text and " " in text:
            sentence_count = text.count(".") + text.count("!") + text.count("?")
            if sentence_count > 3:  # At least a few sentences found
                return True
                
        # If we have substantial text, consider it readable even without sentences
        if len(text) > 500:
            return True
            
        return True  # Default to accepting content unless explicitly filtered

    async def _scrape_single_url(self, url: str, query: str) -> Dict[str, Any]:
        """
        Scrape a single URL and return structured content.
        Never returns None - always returns a structured result with appropriate error info.
        """
        # Initialize with default values. Note: error is None if no error occurs.
        single_result = {
            "url": url,
            "status": 0,
            "error": None,
            "metaDescription": "",
            "textPreview": "",
            "title": "",
            "fullText": "",
            "Summary": "",
            "IsQueryRelated": False,
            "relatedURLs": []
        }
        
        # Check for empty or invalid URL
        if not url or not isinstance(url, str) or url.strip() == "":
            logger.error("Empty or invalid URL provided for scraping")
            single_result["error"] = "Empty or invalid URL provided"
            return single_result
        
        # Try to get cached result
        if self.rate_limiter.redis_client:
            try:
                cached = await self.rate_limiter.safe_execute('get', f"scrape:{url}")
                if cached:
                    logger.debug("Returning cached scrape result", extra={"url": url})
                    return json.loads(cached)
            except Exception as e:
                if config.enable_debug:
                    logger.exception("Redis error in caching get")
                else:
                    logger.error("Redis error in caching get", extra={"error": str(e)})
        
        try:
            logger.debug("Starting scraping URL", extra={"url": url})
            # Introduce a random delay to mimic human behavior (jitter)
            await asyncio.sleep(random.uniform(0.5, 1.5))
            
            # Attempt to refresh the scraper if needed (every 5-10 requests)
            if random.randint(1, 10) <= 2:  # 20% chance
                self.scraper = self._create_scraper()
            
            max_retries = 2
            current_retry = 0
            
            while current_retry <= max_retries:
                try:
                    start_time = time.time()
                    response = await run_in_threadpool(
                        lambda: self.scraper.get(
                            url, 
                            timeout=15,  # Increased timeout
                            allow_redirects=True
                        )
                    )
                    break  # Exit retry loop on success
                except Exception as e:
                    current_retry += 1
                    if current_retry > max_retries:
                        raise  # Re-raise if we've exhausted retries
                    logger.warning(f"Retry {current_retry}/{max_retries} for URL {url}: {str(e)}")
                    # Create a fresh scraper for the retry
                    self.scraper = self._create_scraper()
                    await asyncio.sleep(1)  # Short delay before retry
            
            # Force correct encoding based on apparent encoding
            response.encoding = response.apparent_encoding
            duration = time.time() - start_time
            logger.debug("Finished scraping URL", extra={"url": url, "duration": duration, "status_code": response.status_code})
            
            single_result["status"] = response.status_code
            
            if response.status_code == 200:
                if not response.text or response.text.strip() == "":
                    logger.error("Empty response text received, possibly due to anti-bot block or network issue", extra={"url": url})
                    single_result["error"] = "Empty response text received"
                else:
                    # Parse HTML content
                    soup = BeautifulSoup(response.text, "html.parser")
                    
                    # Extract title - try multiple approaches
                    title_tag = soup.find("title")
                    if not title_tag:
                        # Try to find the most prominent heading if no title
                        for heading in ["h1", "h2", "h3"]:
                            heading_tag = soup.find(heading)
                            if heading_tag:
                                title_tag = heading_tag
                                break
                    
                    # Extract meta description
                    meta_desc_tag = soup.find("meta", attrs={"name": "description"})
                    if not meta_desc_tag:
                        # Try alternative meta tags
                        meta_desc_tag = soup.find("meta", attrs={"property": "og:description"})
                    
                    # Extract text
                    # Remove script and style tags first to clean up content
                    for script_or_style in soup(["script", "style", "noscript", "iframe"]):
                        script_or_style.extract()
                    
                    # Get text content with sensible spacing
                    full_text = soup.get_text(separator=" ", strip=True)
                    
                    # Check if content is readable
                    if not self._is_readable(full_text):
                        logger.warning("Content from URL is not readable", extra={"url": url})
                        single_result["error"] = "Content not readable or blocked by anti-bot measures"
                        # Set title and description even for unreadable content
                        if title_tag and hasattr(title_tag, 'get_text'):
                            single_result["title"] = title_tag.get_text(strip=True)
                        if meta_desc_tag and meta_desc_tag.get("content"):
                            single_result["metaDescription"] = meta_desc_tag["content"].strip()
                    else:
                        # Set title and description
                        if title_tag and hasattr(title_tag, 'get_text'):
                            single_result["title"] = title_tag.get_text(strip=True)
                        else:
                            # Use URL domain as fallback title
                            domain = urlparse(url).netloc
                            single_result["title"] = f"Content from {domain}"
                            
                        if meta_desc_tag and meta_desc_tag.get("content"):
                            single_result["metaDescription"] = meta_desc_tag["content"].strip()
                        
                        # Set text content
                        single_result["textPreview"] = full_text[:200]
                        single_result["fullText"] = full_text
                        
                        # Generate summary and related info
                        try:
                            # If text is overly long, truncate it
                            text_to_summarize = full_text[:MAX_TEXT_LENGTH_TO_SUMMARIZE]
                            
                            # Only try to summarize if we have meaningful content
                            if len(text_to_summarize) > 100:
                                summary, is_query_related, related_urls = await self.summarize_text(text_to_summarize, query)
                                single_result["Summary"] = summary
                                single_result["IsQueryRelated"] = is_query_related
                                single_result["relatedURLs"] = related_urls
                        except Exception as e:
                            logger.error(f"Error summarizing content for {url}: {str(e)}")
                            # Don't fail the entire operation for summary errors
                            single_result["Summary"] = "Error generating summary"
                            # Make a best guess for query relatedness
                            single_result["IsQueryRelated"] = query.lower() in full_text.lower()
            else:
                single_result["error"] = f"Non-200 status code: {response.status_code}"
                logger.warning("Non-200 response while scraping URL", extra={
                    "url": url,
                    "status_code": response.status_code,
                    "headers": dict(response.headers),
                })
        except Exception as exc:
            tb = traceback.format_exc()
            logger.error("Error scraping URL", extra={"url": url, "error": str(exc), "traceback": tb})
            single_result["error"] = str(exc)
        
        # Cache the result if we have Redis configured
        if self.rate_limiter.redis_client:
            try:
                # Only cache successful or partially successful results
                if single_result["status"] == 200:
                    # Serialize JSON safely
                    try:
                        json_data = json.dumps(single_result)
                        await self.rate_limiter.safe_execute('set', f"scrape:{url}", json_data, ex=60)
                    except Exception as e:
                        logger.error(f"Could not serialize result for caching: {str(e)}")
            except Exception as e:
                if config.enable_debug:
                    logger.exception("Redis error in caching set")
                else:
                    logger.error("Redis error in caching set", extra={"error": str(e)})
                    
        return single_result

    async def scrape_urls(self, urls: List[str], query: str) -> List[Dict[str, Any]]:
        """
        Scrape multiple URLs and return structured content.
        Implements safeguards to prevent timeouts and broken pipes.
        """
        logger.debug("WebService: scrape_urls called", extra={"urls": urls, "query": query})
        
        # Apply rate limiting
        try:
            await self.rate_limiter.check()
        except Exception as e:
            logger.warning(f"Rate limit exceeded: {str(e)}")
            # Return minimal response rather than failing
            return [{"url": url, "status": 0, "error": "Rate limit exceeded", "title": "", 
                     "metaDescription": "", "textPreview": "", "fullText": "", 
                     "Summary": "", "IsQueryRelated": False, "relatedURLs": []} 
                    for url in urls[:5]]  # Just return first 5 URLs with error
        
        # Validate and filter URLs
        valid_urls = [url for url in urls if self._is_valid_url(url)]
        if not valid_urls:
            logger.warning("No valid URLs provided for scraping")
            return []
        
        # Limit number of URLs to prevent timeouts and resource exhaustion
        if len(valid_urls) > 10:
            logger.warning(f"Limiting scrape request from {len(valid_urls)} to 10 URLs")
            valid_urls = valid_urls[:10]
            
        # Use a semaphore to limit concurrent scraping
        sem = asyncio.Semaphore(5)  # Reduced from 10 to 5 to be more conservative
        
        async def sem_scrape(url):
            try:
                async with sem:
                    # Set per-URL timeout to catch hanging requests
                    return await asyncio.wait_for(
                        self._scrape_single_url(url, query),
                        timeout=20  # 20 seconds per URL max
                    )
            except asyncio.TimeoutError:
                logger.error(f"Timeout scraping URL: {url}")
                return {
                    "url": url,
                    "status": 0,
                    "error": "Scraping timed out after 20 seconds",
                    "title": "",
                    "metaDescription": "",
                    "textPreview": "",
                    "fullText": "",
                    "Summary": "",
                    "IsQueryRelated": False,
                    "relatedURLs": []
                }
            except Exception as e:
                logger.error(f"Error during scraping URL {url}: {str(e)}")
                return {
                    "url": url,
                    "status": 0,
                    "error": f"Scraping failed: {str(e)}",
                    "title": "",
                    "metaDescription": "",
                    "textPreview": "",
                    "fullText": "",
                    "Summary": "",
                    "IsQueryRelated": False,
                    "relatedURLs": []
                }
        
        # Execute scraping with overall timeout protection
        try:
            # Set a reasonable timeout for the entire batch
            batch_timeout = min(len(valid_urls) * 5, 60)  # Between 5-60 seconds based on URL count
            
            # Execute all scraping tasks with timeout
            tasks = [sem_scrape(url) for url in valid_urls]
            results = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=False),
                timeout=batch_timeout
            )
            
            # Remove any None results (though this shouldn't happen anymore)
            results = [r for r in results if r is not None]
            
            # Limit the size of responses to prevent payload size issues
            for result in results:
                # Keep summaries and previews reasonable
                if 'Summary' in result and len(result['Summary']) > 1000:
                    result['Summary'] = result['Summary'][:1000] + "..."
                if 'fullText' in result and len(result['fullText']) > 5000:
                    result['fullText'] = result['fullText'][:5000] + "..."
            
            return results
            
        except asyncio.TimeoutError:
            logger.error(f"Batch scraping timed out after {batch_timeout}s for {len(valid_urls)} URLs")
            # For timeout, return partial results for all URLs
            return [
                {
                    "url": url,
                    "status": 0,
                    "error": "Batch processing timed out",
                    "title": "",
                    "metaDescription": "",
                    "textPreview": "",
                    "fullText": "",
                    "Summary": "",
                    "IsQueryRelated": False,
                    "relatedURLs": []
                }
                for url in valid_urls
            ]
        except Exception as e:
            logger.error(f"Error in batch scrape_urls: {str(e)}", exc_info=True)
            # For general errors, return error results for all URLs
            return [
                {
                    "url": url,
                    "status": 0,
                    "error": f"Batch processing error: {str(e)}",
                    "title": "",
                    "metaDescription": "",
                    "textPreview": "",
                    "fullText": "",
                    "Summary": "",
                    "IsQueryRelated": False,
                    "relatedURLs": []
                }
                for url in valid_urls
            ]

    async def summarize_text(self, text: str, query: str) -> Tuple[str, bool, List[str]]:
        """
        Calls the Venice.ai API to get a comprehensive and extensive summary of the provided text, determine
        whether the text is related to the provided query, and extract any URLs within the text that seem related.
        Returns a tuple containing the summary (str), a boolean indicating if the text is related, and a list of related URLs.
        Implements retries and respects Venice rate limits.
        """
        if not text or len(text) < 20:
            return "", False, []
        
        # Truncate the text if it exceeds the maximum allowed length.
        if len(text) > MAX_TEXT_LENGTH_TO_SUMMARIZE:
            text = text[:MAX_TEXT_LENGTH_TO_SUMMARIZE]

        # Respect Venice rate limits
        try:
            await self.venice_rate_limiter.check()
        except Exception as e:
            logger.warning(f"Venice rate limit exceeded: {str(e)}")
            # Make a best effort determination for query relatedness
            is_query_related = query.lower() in text.lower()
            return "Rate limit exceeded, summary unavailable", is_query_related, []

        payload = {
            "model": config.venice_model,
            "messages": [
                {"role": "system", "content": config.system_prompt},
                {"role": "user", "content": (
                    f"""
                    Please provide a comprehensive and extensive summary of the following text: {text},
                    ensuring that all relevant points and conclusions extracted from the text are included,
                    especially those related to the query: {query}.
                    Also, determine whether the text is related to the query: {query}.
                    If there are any URLs present within the text that appear to be relevant to the query, extract them
                    and include them in an array.
                    Set 'isQueryRelated' to true if the content is related to the query, and set 'isQueryRelated' to false 
                    only if the content of the site and the input query have nothing to do with each other.
                    Return a JSON object with three keys:
                    'summary' for the comprehensive summary,
                    'isQueryRelated' as a boolean value,
                    and 'relatedURLs' as an array of URLs (an empty array if none are found).
                    """
                )},
            ],
            "venice_parameters": {
                "include_venice_system_prompt": False
            },
            "temperature": config.venice_temperature
        }
        headers = {
            "Authorization": f"Bearer {config.venice_api_key}",
            "Content-Type": "application/json"
        }
        max_attempts = 3  # Reduced from 4 to 3
        delay = 1
        for attempt in range(max_attempts):
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:  # Reduced timeout from 30s to 15s
                    response = await client.post(config.venice_url, json=payload, headers=headers)
                # If Venice returns 503 or 400, log details and retry if appropriate.
                if response.status_code == 503:
                    reset_time = response.headers.get("x-ratelimit-reset-requests")
                    try:
                        delay = float(reset_time) if reset_time is not None else delay
                    except Exception:
                        delay = delay
                    logger.warning("Venice API 503 Service Unavailable, retrying", extra={"attempt": attempt+1, "delay": delay})
                    if attempt < max_attempts - 1:
                        await asyncio.sleep(delay)
                        delay *= 2
                        continue
                    else:
                        # On last attempt, return default values instead of raising
                        return "Service unavailable, summary not generated", query.lower() in text.lower(), []
                elif response.status_code == 400:
                    logger.error("Venice API 400 Bad Request", extra={"response": response.text})
                    # Do not retry on 400 since it likely indicates a payload issue.
                    return "Bad request, summary not generated", query.lower() in text.lower(), []
                
                response.raise_for_status()
                data = response.json()
                summary = ""
                is_query_related = False
                related_urls = []
                
                if "choices" in data and isinstance(data["choices"], list) and len(data["choices"]) > 0:
                    raw_content = data["choices"][0].get("message", {}).get("content", "")
                    # Clean up the response
                    raw_content = re.sub(r'<think>.*?</think>', '', raw_content, flags=re.DOTALL).strip()
                    
                    # Remove markdown code block delimiters if present
                    if raw_content.startswith("```"):
                        raw_content = re.sub(r'^```(?:json)?\s*', '', raw_content)
                        raw_content = re.sub(r'\s*```$', '', raw_content)
                    
                    try:
                        result_obj = json.loads(raw_content)
                        summary = result_obj.get("summary", "")
                        is_query_related = result_obj.get("isQueryRelated", False)
                        related_urls = result_obj.get("relatedURLs", [])
                        if not isinstance(related_urls, list):
                            related_urls = []
                    except Exception as parse_exc:
                        logger.error("Failed to parse Venice API response as JSON", extra={"error": str(parse_exc), "raw_content": raw_content})
                        summary = raw_content[:1000]  # Use raw content as fallback but limit length
                        is_query_related = query.lower() in text.lower()  # Make best guess
                        related_urls = []
                
                return summary, is_query_related, related_urls
            
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 503 and attempt < max_attempts - 1:
                    logger.warning("Venice API HTTP 503 Service Unavailable, retrying", extra={"attempt": attempt+1})
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                else:
                    logger.error("Venice API HTTP error", extra={"error": str(e)})
                    # Return fallback values on error
                    return "Error generating summary", query.lower() in text.lower(), []
            
            except Exception as e:
                logger.error("Error summarizing text", extra={"error": str(e)})
                # Return fallback values on any other error
                return "Error generating summary", query.lower() in text.lower(), []
        
        # Final fallback if we exhaust retries
        return "Unable to generate summary after multiple attempts", query.lower() in text.lower(), []

class EmailService:
    def __init__(self):
        self.api_key = config.sendgrid_api_key
        self.from_email = config.sendgrid_from_email

    async def send_email(self, to_email: str, subject: str, html_content: str):
        if not self.api_key:
            raise ValueError("Sendgrid API key is not configured")
        if not self.from_email:
            raise ValueError("Sendgrid from email is not configured")
        
        message = Mail(
            from_email=self.from_email,
            to_emails=to_email,
            subject=subject,
            html_content=html_content
        )
        sg = SendGridAPIClient(self.api_key)
        try:
            # Run synchronous Sendgrid call in thread pool to avoid blocking
            response = await run_in_threadpool(sg.send, message)
            if response.status_code == 202:
                return {"status": "success", "message": "Email sent successfully"}
            else:
                return {"status": "error", "message": f"Failed to send email: {response.status_code}"}
        except Exception as e:
            logger.error("Error sending email", exc_info=True, extra={"error": str(e)})
            raise

# Create the singleton instances
web_service = WebService()
email_service = EmailService()
