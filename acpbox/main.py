"""FastAPI app: one ACP process per uvicorn worker (lifespan), OpenAI-compatible routes."""

import inspect
import logging
import os
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from acpbox.acp_stdio import AcpRunner, AcpStdioError
from acpbox.agents import create_adapter
from acpbox.config import Config
from acpbox.request_logger import RequestLogger, current_request_id
from acpbox.routes import agent_config, chat, models, responses

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_BODYLESS_METHODS = frozenset({"GET", "HEAD", "DELETE", "OPTIONS"})


def _decode_asgi_headers(raw_headers: Sequence[tuple[bytes, bytes]]) -> dict[str, str]:
    """Decode ASGI header byte pairs into a lowercase-keyed string mapping."""
    headers: dict[str, str] = {}
    for raw_name, raw_value in raw_headers:
        name = raw_name.decode("latin-1").lower()
        value = raw_value.decode("latin-1")
        if name in headers:
            headers[name] = f"{headers[name]}, {value}"
        else:
            headers[name] = value
    return headers


class RequestLoggingMiddleware:
    """Pure ASGI middleware logging raw HTTP requests/responses without buffering streams."""

    def __init__(self, app: Any, *, request_logger: RequestLogger) -> None:
        self.app = app
        self._request_logger = request_logger

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[[], Awaitable[dict[str, Any]]],
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        request_id = uuid.uuid4().hex
        token = current_request_id.set(request_id)
        start_time = time.perf_counter()

        method = str(scope.get("method", ""))
        path = str(scope.get("path", ""))
        raw_qs = scope.get("query_string", b"")
        query_string = raw_qs.decode("latin-1") if isinstance(raw_qs, bytes) else str(raw_qs)
        req_headers = _decode_asgi_headers(scope.get("headers") or [])

        req_body_chunks: list[bytes] = []
        request_logged = False

        def _emit_request_log() -> None:
            nonlocal request_logged
            if request_logged:
                return
            request_logged = True
            self._request_logger.log_http_request(
                request_id=request_id,
                method=method,
                path=path,
                query_string=query_string,
                headers=req_headers,
                raw_body=b"".join(req_body_chunks),
            )

        has_body_header = any(
            (k.lower() == "transfer-encoding")
            or (k.lower() == "content-length" and v.strip() not in ("", "0"))
            for k, v in req_headers.items()
        )
        if method.upper() in _BODYLESS_METHODS and not has_body_header:
            _emit_request_log()

        async def receive_wrapper() -> dict[str, Any]:
            message = await receive()
            if message.get("type") == "http.request":
                body = message.get("body", b"")
                if body:
                    req_body_chunks.append(body)
                if not message.get("more_body", False):
                    _emit_request_log()
            return message

        resp_status = 500
        resp_headers: dict[str, str] = {}
        resp_body_chunks: list[bytes] = []

        async def send_wrapper(message: dict[str, Any]) -> None:
            nonlocal resp_status, resp_headers
            msg_type = message.get("type")
            if msg_type == "http.response.start":
                _emit_request_log()
                resp_status = int(message.get("status", 500))
                resp_headers = _decode_asgi_headers(message.get("headers") or [])
            elif msg_type == "http.response.body":
                body = message.get("body", b"")
                if body:
                    resp_body_chunks.append(body)
            await send(message)

        try:
            try:
                await self.app(scope, receive_wrapper, send_wrapper)
            finally:
                _emit_request_log()
                duration_ms = (time.perf_counter() - start_time) * 1000.0
                self._request_logger.log_http_response(
                    request_id=request_id,
                    method=method,
                    path=path,
                    status_code=resp_status,
                    headers=resp_headers,
                    raw_body=b"".join(resp_body_chunks),
                    duration_ms=duration_ms,
                )
        finally:
            current_request_id.reset(token)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Start one ACP agent process per worker; stop on shutdown. With 8 workers you get 8 ACP instances."""
    config = app.state.config
    request_logger: RequestLogger | None = getattr(app.state, "request_logger", None)
    runner = AcpRunner(
        command=config.acp.command,
        env=config.acp.env,
        workspace=config.acp.workspace,
        stderr_mode=config.acp.stderr,
        stderr_pipe_split=config.acp.stderr_pipe_split,
        message_hook=request_logger.log_acp_message if request_logger else None,
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

    request_logger: RequestLogger | None = None
    if config.gateway.request_log_file:
        request_logger = RequestLogger(config.gateway.request_log_file)
        app.add_middleware(RequestLoggingMiddleware, request_logger=request_logger)
    app.state.request_logger = request_logger

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
