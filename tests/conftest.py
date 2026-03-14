"""Pytest fixtures for gateway tests. Test app without starting ACP process."""

import logging
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from gateway.config import Config
from gateway.routes import chat, models, responses

# Reduce noise in tests
logging.getLogger("gateway").setLevel(logging.WARNING)


def _make_mock_handler(responses_map: dict[tuple[str, str], httpx.Response]) -> Callable[[httpx.Request], httpx.Response]:
    """Build a sync handler that returns responses from (method, path) map."""

    def handler(request: httpx.Request) -> httpx.Response:
        key = (request.method, request.url.path)
        if key in responses_map:
            return responses_map[key]
        return httpx.Response(404, json={"code": "not_found", "message": f"No mock for {key}"})

    return handler


@pytest.fixture
def acp_responses() -> dict[tuple[str, str], httpx.Response]:
    """Mutable map (method, path) -> httpx.Response for ACP mock. Tests can add entries."""
    return {}


@pytest.fixture
def app(acp_responses: dict[tuple[str, str], httpx.Response]) -> FastAPI:
    """Create FastAPI app with test lifespan: no ACP process, mock ACP HTTP via acp_responses."""

    @asynccontextmanager
    async def test_lifespan(app_instance: FastAPI) -> AsyncGenerator[None, None]:
        transport = httpx.MockTransport(_make_mock_handler(acp_responses))
        app_instance.state.acp_client = httpx.AsyncClient(transport=transport)
        app_instance.state.acp_base_url = "http://acp.test"
        yield
        await app_instance.state.acp_client.aclose()

    config = Config.load()
    app_instance = FastAPI(
        title="ACP OpenAI API Gateway",
        description="OpenAI-compatible API that translates to an ACP server.",
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
    """Starlette TestClient for the test app. Uses context manager so lifespan runs."""
    from starlette.testclient import TestClient
    with TestClient(app) as c:
        yield c

