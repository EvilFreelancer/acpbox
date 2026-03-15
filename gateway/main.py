"""FastAPI app: load config, mount OpenAI-compatible routes. ACP agents run per-request over stdio."""

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from gateway.config import Config
from gateway.routes import chat, models, responses

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """No global ACP process; each request spawns its own agent over stdio."""
    yield


def create_app(config_path: str | None = None) -> FastAPI:
    """Create FastAPI application and load config from CONFIG_PATH or config_path."""
    config = Config.load(config_path)
    app = FastAPI(
        title="ACP OpenAI API Gateway",
        description="OpenAI-compatible API that translates to Agent Client Protocol (ACP) over stdio.",
        lifespan=lifespan,
    )
    app.state.config = config

    @app.exception_handler(HTTPException)
    async def openai_style_http_exception(request: Request, exc: HTTPException):
        """Return OpenAI-style error body: { \"error\": { \"code\", \"message\" } }."""
        detail = exc.detail
        if isinstance(detail, dict) and "message" in detail:
            return JSONResponse(
                status_code=exc.status_code,
                content={"error": detail},
            )
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": "api_error", "message": str(detail)}},
        )

    app.include_router(models.router)
    app.include_router(chat.router)
    app.include_router(responses.router_responses)
    app.include_router(responses.router_sessions)

    return app


def run(config_path: str | None = None) -> None:
    """Load config, create app, run uvicorn. Used by __main__ and Docker."""
    config = Config.load(config_path)
    app = create_app(config_path)
    import uvicorn
    uvicorn.run(
        app,
        host=config.gateway.host,
        port=config.gateway.port,
    )


if __name__ == "__main__":
    run()
