# FastAPI Twitter, Google Search, and Web Scraper API

## Overview

This project is a FastAPI application that exposes several API endpoints to interact with Twitter (retrieving tweets, posting tweets, etc.), perform Google searches, scrape web pages, and send emails via Sendgrid. It includes rate limiting for external service calls and an API key based authentication middleware. Additionally, the application integrates with Redis to support distributed rate limiting and caching, making it more scalable for higher request volumes.

## Region

To be deployed on Vercel functions, on several different regions at the same time for multiplicating scraping capacity and to do load balancing.

## Configuration

The application is configured using environment variables (via a `.env` file). Key configuration options include:

- **X_API_KEY**: API key required in request headers.
- **X_API_KEY_2**: Alternative API key that works identically to X_API_KEY. Either key can be used for authentication.
- **TWITTER_COOKIES_JSON**: Twitter cookies in JSON format for authentication.
- **ENABLE_DEBUG**: Enable debug logging.
- **VENICE_API_KEY**, **VENICE_MODEL**, **VENICE_URL**, **VENICE_TEMPERATURE**: Configuration for the Venice.ai API used to summarize text.
- **SENDGRID_API_KEY**: API key for Sendgrid.
- **SENDGRID_FROM_EMAIL**: Default sender email address for emails sent via the `/email/send` endpoint.
- Additional Twitter credentials (such as `twitter_email`, `twitter_username`, `twitter_password`) may be required depending on the authentication method.
- **REDIS_URL**: URL of the Redis server to be used for distributed rate limiting and caching. If not set or if Redis is unreachable, the application falls back to an in-memory implementation.

## Redis Integration

To improve scalability and performance, the application now uses Redis for:

### Distributed Rate Limiting
Instead of relying solely on an in-memory rate limiter, the application uses Redis (when configured via the `REDIS_URL` environment variable) to enforce rate limits across multiple instances. This ensures that even when deployed in a distributed environment (e.g., multiple instances behind a load balancer), the overall request rate is properly controlled.

### Caching
The application caches results for both Google searches and web scraping in Redis for 60 seconds.
- **Google Search Caching:** Frequently requested search queries are cached to reduce the load on the synchronous `googlesearch` library and to deliver faster responses.
- **Web Scraping Caching:** The scraped results for a given URL are cached so that subsequent requests within the caching period return the cached result, reducing redundant external HTTP requests.

If any Redis operation fails (e.g., due to connection issues or a closed TCP transport), the system logs the error and gracefully falls back to the in-memory alternative to ensure continuous operation.

## API Endpoints

> **Note:** All endpoints require a valid API key to be sent in the `x-api-key` header.

### Google Endpoints

#### `GET /google/search`
- **Description:** Performs a Google search.
- **Query Parameters:**
  - `query` (string, required): The search query.
  - `max_results` (integer, optional, default: 10): Maximum number of results (must be between 1 and 1000).
  - `sites` (array of strings, optional): One or more site restrictions. When provided, the query is automatically modified to include the "site:" operator for each specified domain (grouped with an OR operator).
  - `timeframe` (string, optional): A relative time filter for search results. Allowed values are:
    - `24h` – results from the last 24 hours.
    - `week` – results from the last week.
    - `month` – results from the last month.
    - `year` – results from the last year.
    
    Internally, this parameter appends an `after:YYYY-MM-DD` operator to the query.
- **Response:** JSON object containing a list of search results.
- **Redis Integration:** Search results are cached in Redis for 60 seconds to speed up frequent queries and reduce load.

#### `GET /google/search_and_scrape`
- **Description:** Performs a Google search and then scrapes the resulting URLs, combining the functionality of `/google/search` and `/web/scrape` into a single request.
- **Query Parameters:**
  - `query` (string, required): The search query.
  - `max_results` (integer, optional, default: 10): Maximum number of search results to process (must be between 1 and 100).
  - `sites` (array of strings, optional): One or more site restrictions. When provided, the query is automatically modified to include the "site:" operator for each specified domain (grouped with an OR operator).
  - `timeframe` (string, optional): A relative time filter for search results. Allowed values are:
    - `24h` – results from the last 24 hours.
    - `week` – results from the last week.
    - `month` – results from the last month.
    - `year` – results from the last year.
    
    Internally, this parameter appends an `after:YYYY-MM-DD` operator to the query.
- **Response:** JSON object with:
  - `scraped`: Array containing detailed information about each URL, including:
    - `url`: The URL that was scraped.
    - `status`: HTTP status code from the request.
    - `error`: Error message if an error occurred, or null if successful.
    - `title`: The page title.
    - `metaDescription`: Meta description from the page.
    - `textPreview`: Short preview of the page text.
    - `fullText`: Full text content of the page.
    - `Summary`: Summary of the page content, generated using the Venice.ai API.
    - `IsQueryRelated`: Boolean indicating whether the content is related to the search query.
    - `relatedURLs`: Array of related URLs found in the content.
  - `timeframe`: The time filter that was effectively applied to the search.
- **Redis Integration:** Both the search results and the scraped data are cached in Redis for 60 seconds to improve performance and reduce external requests.
- **Notes:** The `max_results` parameter should not exceed 5 to minimize the likelihood of getting a timeout error when using Vercel's free tier.

### Twitter Endpoints

#### `GET /twitter/user/{user_id}/tweets`
- **Description:** Retrieves tweets for the given user ID.
- **Path Parameter:** `user_id` (string): The user ID.
- **Query Parameter:** `count` (integer, optional, default: 10): Number of tweets to retrieve.
- **Response:** JSON object containing tweets.

#### `GET /twitter/home`
- **Description:** Retrieves the home timeline tweets.
- **Query Parameter:** `count` (integer, optional, default: 10): Number of tweets to retrieve.
- **Response:** JSON object containing tweets.

#### `GET /twitter/following`
- **Description:** Retrieves the following timeline tweets.
- **Query Parameter:** `count` (integer, optional, default: 10): Number of tweets to retrieve.
- **Response:** JSON object containing tweets.

#### `GET /twitter/search`
- **Description:** Searches for tweets based on a query.
- **Query Parameters:** Passed directly via the request query parameters.
- **Response:** JSON object containing the search results.

#### `GET /twitter/mentions`
- **Description:** Retrieves tweets that mention the logged-in user.
- **Response:** JSON object containing tweets.

#### `POST /twitter/tweet`
- **Description:** Posts a new tweet.
- **Request Body:** JSON object with `text` (string, required): The tweet content.
- **Response:** JSON object indicating success and the tweet ID.

#### `POST /twitter/reply`
- **Description:** Posts a reply to an existing tweet.
- **Request Body:** JSON object with:
  - `text` (string, required): The reply content.
  - `inReplyToId` (string, required): The tweet ID to reply to.
- **Response:** JSON object indicating success and the tweet ID.

#### `POST /twitter/quote`
- **Description:** Posts a quote tweet.
- **Request Body:** JSON object with:
  - `text` (string, required): The quote tweet content.
  - `quoteId` (string, required): The tweet ID to quote.
- **Response:** JSON object indicating success and the tweet ID.

#### `POST /twitter/retweet`
- **Description:** Retweets a tweet.
- **Request Body:** JSON object with `tweetId` (string, required): The tweet ID to retweet.
- **Response:** JSON object indicating success.

#### `POST /twitter/like`
- **Description:** Likes a tweet.
- **Request Body:** JSON object with `tweetId` (string, required): The tweet ID to like.
- **Response:** JSON object indicating success.

### Web Endpoints

#### `POST /web/scrape`
- **Description:** Scrapes a list of URLs to extract the page title, meta description, a text preview, full text, a summary (via the Venice.ai API), and a boolean flag IsQueryRelated.
- **Request Body:** A JSON object with two required properties:
```json
{
  "urls": [
    "https://xataka.com", 
    "https://g.co/gfd"
  ],
  "query": "latest technology trends"
}
```
- **Response:** A JSON object containing scraped data for each URL:
```json
{
  "scraped": [
    {
      "url": "https://xataka.com",
      "status": 200,
      "error": null,
      "title": "Xataka - Tecnología y gadgets, móviles, informática, electrónica",
      "metaDescription": "Publicación de noticias sobre gadgets y tecnología. Últimas tecnologías en electrónica de consumo y novedades tecnológicas en móviles, tablets, informática, etc",
      "textPreview": "Xataka - Tecnología y gadgets, móviles, informática, electrónica ...",
      "fullText": "Xataka - Tecnología y gadgets, móviles, informática, electrónica ... [full text omitted for brevity]",
      "Summary": "The article covers the latest technology trends in mobile devices and electronics, offering in-depth analysis of current innovations.",
      "IsQueryRelated": true
    }
  ]
}
```
- **Redis Integration:** Scraped results are cached in Redis for 60 seconds.

### Email Endpoints

#### `POST /email/send`
- **Description:** Sends an email using Sendgrid.
- **Request Body:** JSON object with:
  - `to_email` (string, required): The recipient's email address.
  - `subject` (string, required): The email subject.
  - `html_content` (string, required): The HTML content of the email.
- **Response:** JSON object indicating success or failure.
  - On success: `{"status": "success", "message": "Email sent successfully"}`
  - On failure: `{"status": "error", "message": "Failed to send email: <status_code>"}` or HTTP 500 error if an internal error occurs.

## Efficiency Improvements

### Google Search (`/google/search`)
The endpoint leverages the synchronous googlesearch library and runs the search within a thread pool (using run_in_threadpool). This design is now enhanced with Redis caching, which stores frequent query results for 60 seconds. Additionally, a new query parameter (timeframe) enables time-based filtering of search results.

### Google Search and Scrape (`/google/search_and_scrape`)
This endpoint combines the functionality of the `/google/search` and `/web/scrape` endpoints, first performing a search and then automatically scraping all returned URLs. This reduces client-side complexity and network round-trips. The endpoint limits `max_results` to 100 to prevent timeouts on Vercel's 60-second execution limit (on free tier). It is recommended that max_result doesn't exceed 5 either to minimize the likelihood of hitting execution limit.

### Web Scraping (`/web/scrape`)
The scraping logic now executes individual URL scrapes concurrently using asyncio.gather and limits concurrent requests via a semaphore. In addition, scraped results are cached in Redis for 60 seconds to reduce redundant requests and speed up responses.

## Rate Limits and Blacklisting
- **Google Search:** The in-memory (or distributed, if Redis is configured) rate limiter allows up to 10 searches per minute.
- **Web Scraping:** The rate limiter permits up to 5 scrape requests per minute.

**Note:** These limits are enforced within the application. External services (Google or target websites) may impose stricter rate limits or block repeated requests if the thresholds are exceeded.

## Conclusion
This API provides a unified interface for interacting with Twitter, performing Google searches (with optional site restrictions and time-based filtering), scraping web pages, and sending emails via Sendgrid efficiently. With Redis integration, the application supports distributed rate limiting and caching, making it more scalable and capable of handling higher volumes of requests while maintaining low-latency responses. The new `/google/search_and_scrape` endpoint simplifies client implementations by combining search and scrape operations into a single request.

---
