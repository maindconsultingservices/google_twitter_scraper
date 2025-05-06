# FastAPI Twitter, Google Search, Web Scraper, and LinkedIn API

## Overview

This project is a FastAPI application that exposes several API endpoints to interact with Twitter (retrieving tweets, posting tweets, etc.), perform Google searches, scrape web pages, send emails via Sendgrid, and search for job candidates on LinkedIn. It includes rate limiting for external service calls and an API key based authentication middleware. Additionally, the application integrates with Redis to support distributed rate limiting and caching, making it more scalable for higher request volumes.

## Region

To be deployed on Vercel functions, on several different regions at the same time for multiplicating scraping capacity and to do load balancing.

## Configuration

The application is configured using environment variables (via a `.env` file). Key configuration options include:

- **X_API_KEY**: API key required in request headers.
- **X_API_KEY_2**: Alternative API key that works identically to X_API_KEY. Either key can be used for authentication.
- **TWITTER_COOKIES_JSON**: Twitter cookies in JSON format for authentication.
- **LINKEDIN_COOKIES_LI_AT**: LinkedIn "li_at" cookie value for authenticated session. See the "LinkedIn Authentication" section below for instructions on how to get this value.
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
- **LinkedIn Candidate Search Caching:** Results from LinkedIn candidate searches are cached for 30 minutes to reduce the number of browser automations and improve response times.

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

- **Example Request:**
  ```
  GET /google/search?query=FastAPI&max_results=2&sites=github.com&timeframe=year
  ```
  *(Header: `x-api-key: YOUR_API_KEY`)*

- **Example Response:**
```json
{
  "results": [
    "https://github.com/tiangolo/fastapi",
    "https://github.com/fastapi/fastapi"
  ],
  "effective_timeframe": "year"
}
```

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

- **Example Request:**
  ```
  GET /google/search_and_scrape?query=Python%20web%20frameworks&max_results=1&sites=wikipedia.org&timeframe=month
  ```
  *(Header: `x-api-key: YOUR_API_KEY`)*

- **Example Response:**
```json
{
  "scraped": [
    {
      "url": "https://en.wikipedia.org/wiki/Comparison_of_web_frameworks",
      "status": 200,
      "error": null,
      "title": "Comparison of web frameworks - Wikipedia",
      "metaDescription": "This is a comparison of web frameworks, software used to build and deploy web applications...",
      "textPreview": "Comparison of web frameworks. From Wikipedia, the free encyclopedia. This article needs additional citations for verification...",
      "fullText": "[Full text content of the Wikipedia page...]",
      "Summary": "The Wikipedia page provides a comprehensive comparison of various web frameworks, detailing their programming languages, features, and typical use cases. It serves as a valuable resource for developers selecting a framework for their projects.",
      "IsQueryRelated": true,
      "relatedURLs": [
        "https://en.wikipedia.org/wiki/Web_framework",
        "https://en.wikipedia.org/wiki/Python_(programming_language)"
      ]
    }
  ],
  "timeframe": "month"
}
```

### Twitter Endpoints

#### `GET /twitter/user/{user_id}/tweets`
- **Description:** Retrieves tweets for the given user ID.
- **Path Parameter:** `user_id` (string): The user ID.
- **Query Parameter:** `count` (integer, optional, default: 10): Number of tweets to retrieve.
- **Response:** JSON object containing tweets.

- **Example Request:**
  ```
  GET /twitter/user/2244994945/tweets?count=1
  ```
  *(Header: `x-api-key: YOUR_API_KEY`)*

- **Example Response:**
```json
{
  "tweets": [
    {
      "id": "1787530000000000000",
      "userId": "2244994945",
      "username": "TwitterDev",
      "text": "An example tweet from TwitterDev about the API.",
      "conversationId": "1787530000000000000",
      "timestamp": 1715000000,
      "permanentUrl": "https://x.com/TwitterDev/status/1787530000000000000",
      "quoteCount": 5,
      "replyCount": 10,
      "retweetCount": 20
    }
  ]
}
```

#### `GET /twitter/home`
- **Description:** Retrieves the home timeline tweets.
- **Query Parameter:** `count` (integer, optional, default: 10): Number of tweets to retrieve.
- **Response:** JSON object containing tweets.

- **Example Request:**
  ```
  GET /twitter/home?count=1
  ```
  *(Header: `x-api-key: YOUR_API_KEY`)*

- **Example Response:**
```json
{
  "tweets": [
    {
      "id": "1787530000000000001",
      "userId": "1234567890",
      "username": "someuser",
      "text": "This is a tweet from my home timeline!",
      "conversationId": "1787530000000000001",
      "timestamp": 1715000100,
      "permanentUrl": "https://x.com/someuser/status/1787530000000000001",
      "quoteCount": 1,
      "replyCount": 2,
      "retweetCount": 3
    }
  ]
}
```

#### `GET /twitter/following`
- **Description:** Retrieves the following timeline tweets.
- **Query Parameter:** `count` (integer, optional, default: 10): Number of tweets to retrieve.
- **Response:** JSON object containing tweets.

- **Example Request:**
  ```
  GET /twitter/following?count=1
  ```
  *(Header: `x-api-key: YOUR_API_KEY`)*

- **Example Response:**
```json
{
  "tweets": [
    {
      "id": "1787530000000000002",
      "userId": "0987654321",
      "username": "anotheruser",
      "text": "A tweet from someone I am following.",
      "conversationId": "1787530000000000002",
      "timestamp": 1715000200,
      "permanentUrl": "https://x.com/anotheruser/status/1787530000000000002",
      "quoteCount": 0,
      "replyCount": 0,
      "retweetCount": 1
    }
  ]
}
```

#### `GET /twitter/search`
- **Description:** Searches for tweets based on a query.
- **Query Parameters:** Passed directly via the request query parameters (e.g., `query`, `max_tweets`, `mode`). Supported modes: Latest, Top, People, Photos, Videos.
- **Response:** JSON object containing the search results.

- **Example Request:**
  ```
  GET /twitter/search?query=%23FastAPI&max_tweets=1&mode=Latest
  ```
  *(Header: `x-api-key: YOUR_API_KEY`)*

- **Example Response:**
```json
{
  "tweets": [
    {
      "id": "1787530000000000003",
      "userId": "1122334455",
      "username": "fastapifan",
      "text": "Loving #FastAPI for its speed and ease of use!",
      "conversationId": "1787530000000000003",
      "timestamp": 1715000300,
      "permanentUrl": "https://x.com/fastapifan/status/1787530000000000003",
      "quoteCount": 2,
      "replyCount": 4,
      "retweetCount": 8
    }
  ]
}
```

#### `GET /twitter/mentions`
- **Description:** Retrieves tweets that mention the logged-in user.
- **Response:** JSON object containing tweets.

- **Example Request:**
  ```
  GET /twitter/mentions
  ```
  *(Header: `x-api-key: YOUR_API_KEY`)*
  *(Note: This endpoint derives the username from your authenticated session.)*

- **Example Response:**
```json
{
  "tweets": [
    {
      "id": "1787530000000000004",
      "userId": "5544332211",
      "username": "mentioner",
      "text": "Hey @YourTwitterHandle, check this out!",
      "conversationId": "1787530000000000004",
      "timestamp": 1715000400,
      "permanentUrl": "https://x.com/mentioner/status/1787530000000000004",
      "quoteCount": 0,
      "replyCount": 1,
      "retweetCount": 0
    }
  ]
}
```

#### `POST /twitter/tweet`
- **Description:** Posts a new tweet.
- **Request Body:** JSON object with `text` (string, required): The tweet content.
- **Response:** JSON object indicating success and the tweet ID.

- **Example Request:**
  ```
  POST /twitter/tweet
  ```
  *(Header: `x-api-key: YOUR_API_KEY`)*
  
  Body:
  ```json
  {
    "text": "Hello world from my FastAPI app!"
  }
  ```
  
- **Example Response:**
```json
{
  "success": true,
  "tweet_id": "1787530000000000005"
}
```

#### `POST /twitter/reply`
- **Description:** Posts a reply to an existing tweet.
- **Request Body:** JSON object with:
  - `text` (string, required): The reply content.
  - `inReplyToId` (string, required): The tweet ID to reply to.
- **Response:** JSON object indicating success and the tweet ID.

- **Example Request:**
  ```
  POST /twitter/reply
  ```
  *(Header: `x-api-key: YOUR_API_KEY`)*
  
  Body:
  ```json
  {
    "text": "This is a reply!",
    "inReplyToId": "1787530000000000003"
  }
  ```
  
- **Example Response:**
```json
{
  "success": true,
  "tweet_id": "1787530000000000006"
}
```

#### `POST /twitter/quote`
- **Description:** Posts a quote tweet.
- **Request Body:** JSON object with:
  - `text` (string, required): The quote tweet content.
  - `quoteId` (string, required): The tweet ID to quote.
- **Response:** JSON object indicating success and the tweet ID.

- **Example Request:**
  ```
  POST /twitter/quote
  ```
  *(Header: `x-api-key: YOUR_API_KEY`)*
  
  Body:
  ```json
  {
    "text": "Interesting point!",
    "quoteId": "1787530000000000000"
  }
  ```
  
- **Example Response:**
```json
{
  "success": true,
  "tweet_id": "1787530000000000007"
}
```

#### `POST /twitter/retweet`
- **Description:** Retweets a tweet.
- **Request Body:** JSON object with `tweetId` (string, required): The tweet ID to retweet.
- **Response:** JSON object indicating success.

- **Example Request:**
  ```
  POST /twitter/retweet
  ```
  *(Header: `x-api-key: YOUR_API_KEY`)*
  
  Body:
  ```json
  {
    "tweetId": "1787530000000000001"
  }
  ```
  
- **Example Response:**
```json
{
  "success": true
}
```

#### `POST /twitter/like`
- **Description:** Likes a tweet.
- **Request Body:** JSON object with `tweetId` (string, required): The tweet ID to like.
- **Response:** JSON object indicating success.

- **Example Request:**
  ```
  POST /twitter/like
  ```
  *(Header: `x-api-key: YOUR_API_KEY`)*
  
  Body:
  ```json
  {
    "tweetId": "1787530000000000002"
  }
  ```
  
- **Example Response:**
```json
{
  "success": true
}
```

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

### LinkedIn Endpoints

#### `POST /linkedin/find-candidates`
- **Description:** Searches for job candidates on LinkedIn based on job requirements. The endpoint uses the LinkedIn Jobs Scraper to search for relevant job postings and extracts candidate information.
- **Request Body:** A JSON object with the following properties:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| job_title | string | Yes | The title of the position you're hiring for. More specific titles yield better results (e.g., "Senior Frontend Developer" vs. "Developer"). |
| skills | array of strings | No | A list of specific skills required for the position. Match against profiles or extracted summaries. |
| location | object | No | Geographic preferences. |
| location.country | string | No | Country for filtering (e.g., "United States", "Germany"). |
| location.region | string | No | State, province, or region (e.g., "California", "Ontario"). |
| location.city | string | No | City name (e.g., "San Francisco", "Toronto"). |
| education | object | No | Educational requirements. |
| education.degree | string | No | Degree type (e.g., "Bachelor", "Master", "PhD"). |
| education.field_of_study | string | No | Field of study (e.g., "Computer Science", "Business Administration"). |
| education.school | string | No | Specific university or school name (e.g., "Stanford University"). |
| experience_years_min | integer | No | Minimum years of experience. |
| industry | string | No | Industry filter (e.g., "Technology", "Healthcare", "Finance"). |
| company_size | string | No | Company size classification (e.g., "1-10", "11-50", "51-200", etc.). |
| limit | integer | No | Max number of candidates to return (default 10, max 100). |
| excluded_companies | array of strings | No | Company names to exclude. |
| excluded_profiles | array of strings | No | LinkedIn profile URLs to exclude. |

- **Request Body Example:**
```json
{
  "job_title": "Software Engineer",
  "skills": ["python", "machine learning", "api development"],
  "location": {
    "country": "United States",
    "region": "California"
  },
  "education": {
    "degree": "Bachelor",
    "field_of_study": "Computer Science"
  },
  "experience_years_min": 2,
  "industry": "Technology",
  "company_size": "51-200",
  "limit": 10,
  "excluded_companies": ["Google", "Microsoft"],
  "excluded_profiles": ["linkedin.com/in/profile1", "linkedin.com/in/profile2"]
}
```
- **Response:** A JSON object containing matched candidates, sorted by relevance score:
```json
{
  "candidates": [
    {
      "name": "Candidate at ABC Corp",
      "profile_url": "https://linkedin.com/jobs/view/job-id",
      "current_position": "Senior Software Engineer at ABC Corp",
      "location": "San Francisco, CA",
      "skills": ["Python", "Machine Learning", "API Development"],
      "experience": [
        {
          "title": "Senior Software Engineer",
          "company": "ABC Corp",
          "duration": "Current"
        }
      ],
      "education": [],
      "relevance_score": 0.92
    }
  ],
  "total_found": 45,
  "limit": 10,
  "credits_used": 0,
  "cache_hits": 0
}
```
- **Response Fields:**

| Field | Description |
|-------|-------------|
| candidates | Array of candidate profiles matching your search criteria |
| total_found | Total number of candidates that match your criteria |
| limit | Number of candidates returned (as specified in your request) |
| credits_used | Always 0 (unlike paid ProxyCurl API) |
| cache_hits | Number of cached results used (only when using Redis) |

- **Redis Integration:** Search results are cached in Redis for 30 minutes to reduce browser automation and improve response times.
- **Note on Implementation:** Uses direct web scraping instead of a paid API; some fields (e.g., education details) may not be available.

## Efficiency Improvements

### Google Search (`/google/search`)
The endpoint leverages the synchronous googlesearch library and runs the search within a thread pool (using run_in_threadpool). This design is now enhanced with Redis caching, which stores frequent query results for 60 seconds. Additionally, a new query parameter (timeframe) enables time-based filtering of search results.

### Google Search and Scrape (`/google/search_and_scrape`)
This endpoint combines the functionality of the `/google/search` and `/web/scrape` endpoints, first performing a search and then automatically scraping all returned URLs. This reduces client-side complexity and network round-trips. The endpoint limits max_results to 100 to prevent timeouts on Vercel's 60-second execution limit (on free tier). It is recommended that max_results doesn't exceed 5 to minimize the likelihood of hitting execution limits.

### Web Scraping (`/web/scrape`)
The scraping logic now executes individual URL scrapes concurrently using asyncio.gather and limits concurrent requests via a semaphore. In addition, scraped results are cached in Redis for 60 seconds to reduce redundant requests and speed up responses.

### LinkedIn Candidate Search (`/linkedin/find-candidates`)
The LinkedIn candidate search endpoint uses the linkedin-jobs-scraper library to search for job postings and extract candidate information. It runs the scraper in a thread pool to avoid blocking the async event loop and includes caching to improve performance. The response format is designed to be compatible with the ProxyCurl API, making it easy to switch between implementations.

## Rate Limits and Blacklisting

- **Google Search**: The in-memory (or distributed, if Redis is configured) rate limiter allows up to 10 searches per minute. (Note: google_service.py shows RateLimiter(5, 60_000) which is 5 per minute)
- **Web Scraping**: The rate limiter permits up to 5 scrape requests per minute.
- **LinkedIn Scraping**: The rate limiter permits up to 5 requests per minute to avoid detection and rate limiting by LinkedIn.
- **Twitter API**: The rate limiter permits up to 15 requests per minute (as per twitter_service.py RateLimiter(15, 60_000)).

Note: These limits are enforced within the application. External services (Google, LinkedIn, or target websites) may impose stricter rate limits or block repeated requests if the thresholds are exceeded.

## LinkedIn Authentication

The LinkedIn job scraping functionality requires an authenticated LinkedIn session. To set this up:

1. Login to LinkedIn in your Chrome browser using an account of your choice.
2. Open Chrome DevTools by pressing F12 or right-clicking anywhere on the page and selecting "Inspect".
3. Go to the Application tab in DevTools.
4. In the left panel, expand Storage → Cookies, then click on https://www.linkedin.com.
5. In the cookies list, find the row with the name `li_at`.
6. Copy the entire value from the Value column for the `li_at` cookie.
7. Set the environment variable `LINKEDIN_COOKIES_LI_AT` with the value you copied:

```
LINKEDIN_COOKIES_LI_AT=your_li_at_cookie_value_here
```

Note that LinkedIn cookies may expire after some time, so you may need to repeat this process periodically if you encounter authentication errors.

## Twitter/X Authentication

The Twitter endpoints require a valid `TWITTER_COOKIES_JSON` environment variable, containing your Twitter session cookies in JSON format. To obtain and configure this:

1. Login to Twitter (X) in your browser.
2. Open DevTools (F12), go to the Application (or Storage) tab, and select Cookies → https://twitter.com (and https://api.twitter.com).
3. Export all cookie name/value pairs for these domains. For each cookie, note its name and value.
4. Construct a JSON object mapping cookie names to values, for example:

```json
{
  "auth_token": "ABC...",
  "ct0": "DEF...",
  "twid": "GHI...",
  "guest_id": "JKL..."
  // include all relevant cookies
}
```

5. Minify or properly escape this JSON string for use in environment variables.
6. Set `TWITTER_COOKIES_JSON` in your `.env` file to that JSON string (just one line, no blank spaces). For example:

```
TWITTER_COOKIES_JSON={"auth_token":"ABC...","ct0":"DEF...","twid":"GHI...","guest_id":"JKL..."}
```

Ensure you include every cookie required by the Twitter client library to authenticate your session. If cookies expire or you receive login errors, repeat this process to refresh your `TWITTER_COOKIES_JSON`.

## Conclusion

This API provides a unified interface for interacting with Twitter, performing Google searches (with optional site restrictions and time-based filtering), scraping web pages, searching for job candidates on LinkedIn, and sending emails via Sendgrid efficiently. With Redis integration, the application supports distributed rate limiting and caching, making it more scalable and capable of handling higher volumes of requests while maintaining low-latency responses.
