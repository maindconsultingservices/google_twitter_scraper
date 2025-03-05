from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from .routes import twitter_router, google_router, web_router, email_router, linkedin_router
from .utils import logger

logger.info("Starting the application entry point...")

app = FastAPI()

class LogBodyMiddleware(BaseHTTPMiddleware):
    """
    Middleware that logs the raw request body, then re-injects it
    so FastAPI/Pydantic can parse JSON/form data as usual.
    """
    async def dispatch(self, request: Request, call_next):
        body = await request.body()
        logger.debug(f"Incoming raw body: {body.decode('utf-8', errors='replace')}")

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        request._receive = receive

        response = await call_next(request)
        return response

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    Custom handler for Pydantic validation errors (HTTP 422).
    Logs the exact validation errors so you can see which fields caused the issue.
    """
    logger.error(
        "Pydantic validation error on request",
        extra={
            "errors": exc.errors(),
            "body_hint": "Enable LogBodyMiddleware to see the raw body in logs."
        }
    )

    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors()}
    )

# Attach the middleware
app.add_middleware(LogBodyMiddleware)

# Include routers
app.include_router(twitter_router, prefix="/twitter", tags=["twitter"])
app.include_router(google_router, prefix="/google", tags=["google"])
app.include_router(web_router, prefix="/web", tags=["web"])
app.include_router(email_router, prefix="/email", tags=["email"])
app.include_router(linkedin_router, prefix="/linkedin", tags=["linkedin"])
