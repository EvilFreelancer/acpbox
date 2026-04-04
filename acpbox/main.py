"""FastAPI app: one ACP process per uvicorn worker (lifespan), OpenAI-compatible routes."""

import inspect
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from acpbox.acp_stdio import AcpRunner, AcpStdioError
from acpbox.agents import create_adapter
from acpbox.config import Config
from acpbox.routes import agent_config, chat, models, responses

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Start one ACP agent process per worker; stop on shutdown. With 8 workers you get 8 ACP instances."""
    config = app.state.config
    runner = AcpRunner(
        command=config.acp.command,
        env=config.acp.env,
        workspace=config.acp.workspace,
    )
    try:
        await runner.start()
    except AcpStdioError as e:
        logger.error("ACP agent failed to start: %s", e)
        raise
    app.state.runner = runner
    yield
    await runner.stop()
    app.state.runner = None


def create_app(config_path: str | None = None) -> FastAPI:
    """Create FastAPI application and load config from ACPBOX_CONFIG_PATH or config_path."""
    config = Config.load(config_path)
    app = FastAPI(
        title="ACP OpenAI API Gateway",
        description="OpenAI-compatible API that translates to Agent Client Protocol (ACP) over stdio.",
        lifespan=lifespan,
    )
    app.state.config = config
    app.state.agent_adapter = create_adapter(config.acp.command, config.acp.workspace)

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
    app.include_router(agent_config.router)

    return app


def _uvicorn_extra_kwargs(config: Config) -> dict[str, Any]:
    """Build optional uvicorn.run keyword args that exist in the installed uvicorn version."""
    import uvicorn

    out: dict[str, Any] = {}
    sig = inspect.signature(uvicorn.run)
    if "threads" in sig.parameters:
        out["threads"] = config.gateway.threads
    return out


def run(config_path: str | None = None) -> None:
    """Load config and run uvicorn. Used by __main__, acpbox CLI, and Docker."""
    if config_path is not None:
        os.environ["ACPBOX_CONFIG_PATH"] = str(Path(config_path).resolve())

    config = Config.load(None)
    import uvicorn

    extra = _uvicorn_extra_kwargs(config)
    if config.gateway.threads != 1 and "threads" not in extra:
        logger.warning(
            "ACPBOX_GATEWAY_THREADS=%s is ignored: this uvicorn has no threads= argument (ASGI uses asyncio).",
            config.gateway.threads,
        )

    if config.gateway.workers > 1:
        uvicorn.run(
            "acpbox.main:create_app",
            factory=True,
            host=config.gateway.host,
            port=config.gateway.port,
            workers=config.gateway.workers,
            **extra,
        )
    else:
        app = create_app(None)
        uvicorn.run(
            app,
            host=config.gateway.host,
            port=config.gateway.port,
            **extra,
        )


if __name__ == "__main__":
    run()
