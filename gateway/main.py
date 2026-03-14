"""FastAPI app: load config, start ACP process, mount OpenAI-compatible routes."""

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from gateway.acp_runner import AcpRunner, AcpRunnerError, run_stdout_logger
from gateway.config import Config
from gateway.routes import chat, models, responses

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Start ACP process, wait for /ping, attach httpx client; on exit stop ACP and close client."""
    config = app.state.config
    runner = AcpRunner(
        command=config.acp.command,
        env=config.acp.env,
        base_url=config.gateway.acp_base_url,
        startup_timeout_seconds=config.acp.startup_timeout_seconds,
    )
    try:
        await runner.start()
    except AcpRunnerError as e:
        logger.error("ACP failed to start: %s", e)
        raise
    app.state.acp_runner = runner
    if runner._process:
        app.state._acp_stdout_task = run_stdout_logger(runner._process)
    else:
        app.state._acp_stdout_task = None

    async with httpx.AsyncClient(timeout=60.0) as client:
        app.state.acp_client = client
        app.state.acp_base_url = config.gateway.acp_base_url
        yield

    if getattr(app.state, "_acp_stdout_task", None):
        app.state._acp_stdout_task.cancel()
        try:
            await app.state._acp_stdout_task
        except Exception:
            pass
    await runner.stop()


def create_app(config_path: str | None = None) -> FastAPI:
    """Create FastAPI application and load config from CONFIG_PATH or config_path."""
    config = Config.load(config_path)
    app = FastAPI(
        title="ACP OpenAI API Gateway",
        description="OpenAI-compatible API that translates to an ACP server.",
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
