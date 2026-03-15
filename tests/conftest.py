"""Pytest fixtures for gateway tests. ACP is mocked via run_single_turn (stdio)."""

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from gateway.config import Config
from gateway.routes import chat, models, responses

# Reduce noise in tests
logging.getLogger("gateway").setLevel(logging.WARNING)


@pytest.fixture
def app() -> FastAPI:
    """Create FastAPI app with test lifespan (no ACP process)."""
    @asynccontextmanager
    async def test_lifespan(app_instance: FastAPI) -> AsyncGenerator[None, None]:
        yield

    config = Config.load()
    app_instance = FastAPI(
        title="ACP OpenAI API Gateway",
        description="OpenAI-compatible API that translates to ACP over stdio.",
        lifespan=test_lifespan,
    )
    app_instance.state.config = config

    @app_instance.exception_handler(HTTPException)
    async def openai_style_http_exception(request: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail
        if isinstance(detail, dict) and "message" in detail:
            return JSONResponse(status_code=exc.status_code, content={"error": detail})
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": "api_error", "message": str(detail)}},
        )

    app_instance.include_router(models.router)
    app_instance.include_router(chat.router)
    app_instance.include_router(responses.router_responses)
    app_instance.include_router(responses.router_sessions)

    return app_instance


@pytest.fixture
def client(app: FastAPI) -> Any:
    """Starlette TestClient for the test app."""
    from starlette.testclient import TestClient
    with TestClient(app) as c:
        yield c
