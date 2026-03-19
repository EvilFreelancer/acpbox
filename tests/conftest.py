"""Pytest fixtures for acpbox tests. ACP is mocked via app.state.runner (per-worker runner)."""

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from acpbox.config import Config
from acpbox.routes import chat, models, responses

# Reduce noise in tests
logging.getLogger("acpbox").setLevel(logging.WARNING)


class MockRunner:
    """Mock per-worker ACP runner for tests (no real process)."""

    async def get_agent_models(self) -> list[str]:
        return ["plan", "build"]

    async def run_turn(
        self,
        prompt_blocks: list[Any],
        mode_id: str | None = None,
        request_timeout: float = 300.0,
    ) -> tuple[str, str, list[dict[str, Any]]]:
        return ("Reply", "end_turn", [])

    async def run_turn_stream(
        self,
        prompt_blocks: list[Any],
        mode_id: str | None = None,
        request_timeout: float = 300.0,
    ):
        yield ("text", "Reply")
        yield ("done", "end_turn")


@pytest.fixture
def app() -> FastAPI:
    """Create FastAPI app with test lifespan and mock runner (no real ACP process)."""
    @asynccontextmanager
    async def test_lifespan(app_instance: FastAPI) -> AsyncGenerator[None, None]:
        app_instance.state.runner = MockRunner()
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
