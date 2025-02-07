import os
import ssl
from dotenv import load_dotenv

load_dotenv()

class Config:
    x_api_key = os.getenv("X_API_KEY", "")
    twitter_cookies_json = os.getenv("TWITTER_COOKIES_JSON", "")
    enable_debug = (os.getenv("ENABLE_DEBUG", "false").lower() == "true")
    venice_api_key = os.getenv("VENICE_API_KEY", "")
    venice_model = os.getenv("VENICE_MODEL", "")
    venice_url = os.getenv("VENICE_URL", "")
    venice_temperature = float(os.getenv("VENICE_TEMPERATURE", "0.2"))

config = Config()
