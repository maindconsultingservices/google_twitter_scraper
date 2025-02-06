import os
import ssl
from dotenv import load_dotenv

load_dotenv()

class Config:
    x_api_key = os.getenv("X_API_KEY", "")
    twitter_cookies_json = os.getenv("TWITTER_COOKIES_JSON", "")
    enable_debug = (os.getenv("ENABLE_DEBUG", "false").lower() == "true")

# Instantiate our config
config = Config()
