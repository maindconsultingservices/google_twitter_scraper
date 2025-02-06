from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from api.routes.twitter_routes import twitter_router
from api.routes.google_routes import google_router
from api.routes.web_routes import web_router
from api.utils.logger import logger

logger.info("Starting the application entry point...")

app = FastAPI()


class LogBodyMiddleware(BaseHTTPMiddleware):
    """
    Middleware that logs the raw request body, then re-injects it so FastAPI/Pydantic
    can still parse JSON/form data as usual.
    """
    async def dispatch(self, request: Request, call_next):
        # 1) Read the raw body
        body = await request.body()
        logger.debug(f"Incoming raw body: {body.decode('utf-8', errors='replace')}")

        # 2) Re-inject the body into the request so the route handler (and Pydantic) can parse it
        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        request._receive = receive

        # 3) Pass the request downstream
        response = await call_next(request)
        return response


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    Custom handler for Pydantic validation errors (HTTP 422).
    Logs the exact validation errors so you can see which field(s) caused the issue.
    """
    # Log the errors at ERROR level (or use DEBUG if you prefer).
    logger.error(
        "Pydantic validation error on request",
        extra={
            "errors": exc.errors(),  # List of validation error details
            "body_hint": "Enable LogBodyMiddleware to see the raw body in logs."
        }
    )

    # Return the typical 422 JSON error structure (FastAPI default style).
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors()}
    )


# Add the middleware
app.add_middleware(LogBodyMiddleware)

# Include routes as before
app.include_router(twitter_router, prefix="/twitter", tags=["twitter"])
app.include_router(google_router, prefix="/google", tags=["google"])
app.include_router(web_router, prefix="/web", tags=["web"])
