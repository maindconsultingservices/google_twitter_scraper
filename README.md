# FastAPI Twitter, Google Search, and Web Scraper API

## Overview

This project is a FastAPI application that exposes several API endpoints to interact with Twitter (retrieving tweets, posting tweets, etc.), perform Google searches, and scrape web pages. It includes rate limiting for external service calls and an API key based authentication middleware.

## Configuration

The application is configured using environment variables (via a `.env` file). Key configuration options include:

- **X_API_KEY**: API key required in request headers.
- **TWITTER_COOKIES_JSON**: Twitter cookies in JSON format for authentication.
- **ENABLE_DEBUG**: Enable debug logging.
- **VENICE_API_KEY**, **VENICE_MODEL**, **VENICE_URL**, **VENICE_TEMPERATURE**: Configuration for the Venice.ai API used to summarize text.
- Additional Twitter credentials (such as `twitter_email`, `twitter_username`, `twitter_password`) may be required depending on the authentication method.

## API Endpoints

> **Note:** All endpoints require a valid API key to be sent in the `x-api-key` header.

### Google Endpoints

#### `GET /google/search`
- **Description:** Performs a Google search.
- **Query Parameters:**
  - `query` (string, required): The search query.
  - `max_results` (integer, optional, default: 10): Maximum number of results (must be between 1 and 1000).
  - `sites` (array of strings, optional): One or more site restrictions. When provided, the query is automatically modified to include the "site:" operator for each specified domain.
- **Response:** JSON object containing a list of search results.

### Twitter Endpoints

#### `GET /twitter/user/{user_id}/tweets`
- **Description:** Retrieves tweets for the given user ID.
- **Path Parameter:**
  - `user_id` (string): The user ID.
- **Query Parameter:**
  - `count` (integer, optional, default: 10): Number of tweets to retrieve.
- **Response:** JSON object containing tweets.

#### `GET /twitter/home`
- **Description:** Retrieves the home timeline tweets.
- **Query Parameter:**
  - `count` (integer, optional, default: 10): Number of tweets to retrieve.
- **Response:** JSON object containing tweets.

#### `GET /twitter/following`
- **Description:** Retrieves the following timeline tweets.
- **Query Parameter:**
  - `count` (integer, optional, default: 10): Number of tweets to retrieve.
- **Response:** JSON object containing tweets.

#### `GET /twitter/search`
- **Description:** Searches for tweets based on a query.
- **Query Parameters:**  
  - (Passed directly via the request query parameters.)
- **Response:** JSON object containing the search results.

#### `GET /twitter/mentions`
- **Description:** Retrieves tweets that mention the logged-in user.
- **Response:** JSON object containing tweets.

#### `POST /twitter/tweet`
- **Description:** Posts a new tweet.
- **Request Body:** JSON object with:
  - `text` (string, required): The tweet content.
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
- **Request Body:** JSON object with:
  - `tweetId` (string, required): The tweet ID to retweet.
- **Response:** JSON object indicating success.

#### `POST /twitter/like`
- **Description:** Likes a tweet.
- **Request Body:** JSON object with:
  - `tweetId` (string, required): The tweet ID to like.
- **Response:** JSON object indicating success.

### Web Endpoints

#### `POST /web/scrape`
- **Description:** Scrapes a list of URLs to extract the page title, meta description, a text preview, full text, and a summary (via the Venice.ai API).
- **Request Body:** JSON object with:
  - `urls` (array of strings, required): List of URLs to scrape.
- **Response:** JSON object containing scraped data for each URL.

## Efficiency Improvements

- **Google Search (`/google/search`):**  
  The endpoint leverages the synchronous `googlesearch` library and runs the search within a thread pool (using `run_in_threadpool`). This is optimal given the library’s synchronous design. For further performance improvements, you might consider caching frequent queries or using an asynchronous search API.
  
- **Web Scraping (`/web/scrape`):**  
  The scraping logic has been enhanced to execute individual URL scrapes concurrently using `asyncio.gather`, significantly increasing throughput when multiple URLs are provided.

## Rate Limits and Blacklisting

- **Google Search:** The in–memory rate limiter allows up to **10 searches per minute**.
- **Web Scraping:** The rate limiter permits up to **5 scrape requests per minute**.
  
> **Note:** These limits are enforced within the application. External services (Google or target websites) may impose stricter rate limits or block repeated requests if the thresholds are exceeded.

## Restricting Google Search by Site

The `/google/search` endpoint now accepts an optional `sites` query parameter. When supplied, the search query is automatically augmented with the appropriate "site:" operators (for example, appending `site:example.com`) to restrict results to the specified domains.

## Running the Application

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
