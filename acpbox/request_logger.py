"""JSONL file logger for raw HTTP traffic and raw ACP JSON-RPC messages."""

import contextvars
import json
import logging
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX platforms
    fcntl = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

current_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "acpbox_request_id",
    default=None,
)

_ACP_TOOL_UPDATE_KINDS = frozenset({"tool_call", "tool_call_update"})
_SENSITIVE_HEADERS = frozenset({
    "authorization",
    "proxy-authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
})
_SSE_LINE_SPLIT = re.compile(r"\r\n|\n|\r")


def _utc_timestamp() -> str:
    """Return current UTC timestamp in ISO-8601 format with trailing Z."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _try_parse_json(raw_text: str) -> Any:
    """Parse JSON text or return None when empty or not valid JSON."""
    if not raw_text.strip():
        return None
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        return None


def _redact_headers(headers: dict[str, str]) -> dict[str, str]:
    """Return a copy of HTTP headers with secret/credential values redacted."""
    return {
        k: ("[REDACTED]" if k.lower() in _SENSITIVE_HEADERS else v)
        for k, v in headers.items()
    }


def _parse_sse_events(raw_text: str) -> list[Any]:
    """Extract data payloads from an SSE stream, preserving Unicode line separators and multi-line events."""
    events: list[Any] = []
    data_lines: list[str] = []

    def _flush() -> None:
        if not data_lines:
            return
        data = "\n".join(data_lines).strip()
        data_lines.clear()
        if not data or data == "[DONE]":
            return
        try:
            events.append(json.loads(data))
        except json.JSONDecodeError:
            events.append(data)

    for line in _SSE_LINE_SPLIT.split(raw_text):
        if line == "":
            _flush()
            continue
        if not line.startswith("data:"):
            continue
        chunk = line[len("data:"):]
        if chunk.startswith(" "):
            chunk = chunk[1:]
        data_lines.append(chunk)
    _flush()
    return events


def _detect_acp_tool_kind(message: dict[str, Any]) -> str | None:
    """Return a tool event kind when an incoming ACP message is tool- or permission-related."""
    if not isinstance(message, dict):
        return None
    method = message.get("method")
    if method == "session/request_permission":
        return "permission_request"
    if method == "session/update":
        params = message.get("params")
        if isinstance(params, dict):
            update = params.get("update")
            if isinstance(update, dict):
                sut = update.get("sessionUpdate")
                if sut in _ACP_TOOL_UPDATE_KINDS:
                    return str(sut)
    return None


class RequestLogger:
    """Thread- and process-safe JSONL writer correlating HTTP requests/responses with ACP JSON-RPC traffic."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self._lock = threading.Lock()
        self._dir_ready = False

    def _write_line(self, line: str) -> None:
        if not self._dir_ready:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._dir_ready = True
        try:
            fh = open(self.path, "a", encoding="utf-8", errors="replace")
        except FileNotFoundError:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fh = open(self.path, "a", encoding="utf-8", errors="replace")
        with fh:
            if fcntl is not None:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                fh.write(line)
                fh.flush()
            finally:
                if fcntl is not None:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    def write_event(
        self,
        event: str,
        payload: dict[str, Any],
        *,
        request_id: str | None = None,
    ) -> None:
        """Append one JSONL record; serialization and I/O errors are logged as warnings and never raised."""
        try:
            record: dict[str, Any] = {
                **payload,
                "timestamp": _utc_timestamp(),
                "event": event,
                "request_id": request_id if request_id is not None else current_request_id.get(),
            }
            line = (
                json.dumps(record, ensure_ascii=False, default=str)
                .replace("\u2028", "\\u2028")
                .replace("\u2029", "\\u2029")
                + "\n"
            )
            with self._lock:
                self._write_line(line)
        except Exception as e:
            logger.warning("Failed to write request log event %s to %s: %s", event, self.path, e)

    def log_http_request(
        self,
        *,
        request_id: str,
        method: str,
        path: str,
        query_string: str,
        headers: dict[str, str],
        raw_body: bytes,
    ) -> None:
        """Log a raw incoming HTTP request with both decoded text and parsed JSON body."""
        raw_text = raw_body.decode("utf-8", errors="replace") if raw_body else ""
        body = _try_parse_json(raw_text) if raw_body else None
        self.write_event(
            "http_request",
            {
                "method": method,
                "path": path,
                "query_string": query_string,
                "headers": _redact_headers(headers),
                "body": body,
                "raw_body": raw_text,
            },
            request_id=request_id,
        )

    def log_http_response(
        self,
        *,
        request_id: str,
        method: str,
        path: str,
        status_code: int,
        headers: dict[str, str],
        raw_body: bytes,
        duration_ms: float,
    ) -> None:
        """Log a raw outgoing HTTP response (JSON or SSE stream) with timing metadata."""
        raw_text = raw_body.decode("utf-8", errors="replace") if raw_body else ""
        content_type = ""
        for k, v in headers.items():
            if k.lower() == "content-type":
                content_type = v.lower()
                break

        payload: dict[str, Any] = {
            "method": method,
            "path": path,
            "status_code": status_code,
            "headers": _redact_headers(headers),
            "duration_ms": round(duration_ms, 3),
            "raw_body": raw_text,
        }
        if "text/event-stream" in content_type:
            payload["body"] = None
            payload["sse_events"] = _parse_sse_events(raw_text)
        else:
            payload["body"] = _try_parse_json(raw_text) if raw_body else None

        self.write_event("http_response", payload, request_id=request_id)

    def log_acp_message(self, direction: Literal["send", "recv"], message: dict[str, Any]) -> None:
        """Log one raw ACP JSON-RPC message (send or recv) and tag tool/permission events."""
        event = "acp_send" if direction == "send" else "acp_recv"
        payload: dict[str, Any] = {"message": message}
        if direction == "recv":
            kind = _detect_acp_tool_kind(message)
            if kind is not None:
                payload["tool_event"] = True
                payload["kind"] = kind
        self.write_event(event, payload)
