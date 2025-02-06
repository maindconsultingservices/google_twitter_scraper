# File: api/services/twitter_client.py

import json
import traceback
from api.config.env import config
from api.utils.logger import logger

# Import from the library
from twitter.account import Account
from twitter.scraper import Scraper
from twitter.search import Search


class TwitterClientManager:
    def __init__(self):
        self._account = None
        self._scraper = None
        self._search = None
        self._logged_in = False
        # We'll store the cookies dict here if we successfully parse them.
        self._cookies_store = None

    def _init_account(self) -> Account:
        """
        Initialize the Account object using cookies (recommended)
        or fallback to username/password if no cookies are provided.
        Adds extra debug logs for troubleshooting.
        """
        logger.debug("Entering _init_account to set up Account instance...")
        try:
            if config.twitter_cookies_json:
                logger.info("Loading cookies from JSON in env...")
                try:
                    cookies_dict = json.loads(config.twitter_cookies_json)
                    # Store them in self._cookies_store for reuse in Scraper / Search
                    self._cookies_store = cookies_dict

                    # Create the Account with cookies
                    acct = Account(cookies=cookies_dict)
                    logger.debug("Successfully created Account from inline JSON cookies.")
                except Exception as e:
                    tb = traceback.format_exc()
                    logger.error(
                        "Failed to parse TWITTER_COOKIES_JSON; falling back to username/password",
                        extra={"error": str(e), "traceback": tb}
                    )
                    self._cookies_store = None
                    acct = Account(
                        email=config.twitter_email,
                        username=config.twitter_username,
                        password=config.twitter_password
                    )
            else:
                logger.warning("No cookies provided. Falling back to username/password approach (less stable).")
                acct = Account(
                    email=config.twitter_email,
                    username=config.twitter_username,
                    password=config.twitter_password
                )
        except Exception as ex:
            tb = traceback.format_exc()
            logger.error(
                "Exception occurred while initializing Account object.",
                extra={"error": str(ex), "traceback": tb}
            )
            raise

        logger.debug("Leaving _init_account.")
        return acct

    def get_account(self) -> Account:
        """
        Returns the cached Account instance (or initializes it if needed).
        Added extra debug logs.
        """
        if not self._account:
            logger.debug("No existing Account found; calling _init_account now.")
            self._account = self._init_account()
            logger.info("Account instance created.")
        else:
            logger.debug("Reusing existing Account instance.")
        return self._account

    def get_scraper(self) -> Scraper:
        """
        For advanced read operations, we use the Scraper.
        We'll pass in the stored cookies if available, otherwise fallback to user/pass.
        Added debug logs.
        """
        if not self._scraper:
            logger.debug("No existing Scraper; about to retrieve account/cookies for the Scraper.")
            self.get_account()  # ensure Account is initialized
            # If we have parsed cookies, use them
            if self._cookies_store:
                logger.debug("Detected cookies store; creating Scraper with it now.")
                try:
                    self._scraper = Scraper(cookies=self._cookies_store)
                    logger.info("Scraper instance created from cookies store.")
                except Exception as e:
                    tb = traceback.format_exc()
                    logger.error("Exception creating Scraper with stored cookies", extra={"error": str(e), "traceback": tb})
                    raise
            else:
                # Otherwise, fallback to user/pass.
                logger.warning("No cookies store found. Attempting Scraper with fallback credentials.")
                try:
                    self._scraper = Scraper(
                        email=config.twitter_email,
                        username=config.twitter_username,
                        password=config.twitter_password
                    )
                    logger.debug("Scraper created using fallback user/pass.")
                except Exception as e:
                    tb = traceback.format_exc()
                    logger.error("Exception creating Scraper with user/pass fallback", extra={"error": str(e), "traceback": tb})
                    raise
        else:
            logger.debug("Reusing existing Scraper instance.")
        return self._scraper

    def get_search(self) -> Search:
        """
        For robust searching. Search can save results, handle advanced queries, etc.
        We'll reuse the stored cookies if available, otherwise fallback to user/pass.
        """
        if not self._search:
            logger.debug("No existing Search instance; creating a new one.")
            logger.info("Creating Search instance for advanced queries.")
            self.get_account()  # ensure Account is initialized

            # Our custom logger config: console only, no file handler
            console_only_logger = {
                "version": 1,
                "disable_existing_loggers": False,
                "handlers": {
                    "console": {
                        "class": "logging.StreamHandler",
                        "level": "DEBUG"
                    }
                },
                "root": {
                    "handlers": ["console"],
                    "level": "DEBUG"
                }
            }

            # We'll pick /tmp because it's the only writable location in many serverless envs
            output_dir = "/tmp/twitter_search"

            if self._cookies_store:
                logger.debug("Detected cookies store; creating Search with cookies.")
                try:
                    self._search = Search(
                        cookies=self._cookies_store,
                        save=False,       # do not attempt to persist data
                        debug=False,      # no file-based logs
                        output_dir=output_dir,
                        data_dir=output_dir,  # forcibly override the default
                        cfg=console_only_logger
                    )
                except Exception as e:
                    tb = traceback.format_exc()
                    logger.error("Exception creating Search with stored cookies", extra={"error": str(e), "traceback": tb})
                    raise
            else:
                logger.warning("No cookies store found. Attempting Search fallback approach with user/pass.")
                try:
                    self._search = Search(
                        email=config.twitter_email,
                        username=config.twitter_username,
                        password=config.twitter_password,
                        save=False,
                        debug=False,
                        output_dir=output_dir,
                        data_dir=output_dir,
                        cfg=console_only_logger
                    )
                    logger.debug("Search created with fallback user/pass.")
                except Exception as e:
                    tb = traceback.format_exc()
                    logger.error("Exception creating Search with user/pass fallback", extra={"error": str(e), "traceback": tb})
                    raise
        else:
            logger.debug("Reusing existing Search instance.")
        return self._search

    def is_logged_in(self) -> bool:
        """
        We consider ourselves logged in if the account can fetch
        the home timeline without error.
        """
        logger.debug("Checking if we are logged in via home_timeline call.")
        if not self._logged_in:
            try:
                logger.debug("Calling home_timeline(limit=1) to verify login status.")
                self.get_account().home_timeline(limit=1)
                logger.debug("home_timeline succeeded; marking _logged_in = True.")
                self._logged_in = True
            except Exception as e:
                tb = traceback.format_exc()
                logger.error("Login check failed", extra={"error": str(e), "traceback": tb})
                self._logged_in = False
        else:
            logger.debug("Already marked as logged in (self._logged_in == True).")
        return self._logged_in


twitter_client_manager = TwitterClientManager()
