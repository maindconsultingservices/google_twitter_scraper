import os
import ssl
from dotenv import load_dotenv

load_dotenv()

class Config:
    x_api_key = os.getenv("X_API_KEY", "")
    x_api_key_2 = os.getenv("X_API_KEY_2", "")
    twitter_cookies_json = os.getenv("TWITTER_COOKIES_JSON", "")
    enable_debug = (os.getenv("ENABLE_DEBUG", "false").lower() == "true")
    venice_api_key = os.getenv("VENICE_API_KEY", "")
    venice_model = os.getenv("VENICE_MODEL", "")
    venice_url = os.getenv("VENICE_URL", "")
    venice_temperature = float(os.getenv("VENICE_TEMPERATURE", "0.2"))
    system_prompt = os.getenv("SYSTEM_PROMPT", "Be precise")
    redis_url = os.getenv("REDIS_URL", "")
    sendgrid_api_key = os.getenv("SENDGRID_API_KEY", "")
    sendgrid_from_email = os.getenv("SENDGRID_FROM_EMAIL", "")

config = Config()
