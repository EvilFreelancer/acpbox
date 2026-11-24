"""Unit tests for acpbox.request_logger."""

import json
import logging
from pathlib import Path
from typing import Any

from acpbox.request_logger import RequestLogger, current_request_id


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


class TestRequestLogger:
    """Behavior of JSONL RequestLogger for HTTP and ACP events."""

    def test_log_http_request_json_with_tools(self, tmp_path: Path) -> None:
        """HTTP JSON request preserves parsed body (including tools) and raw text."""
        log_path = tmp_path / "traffic.jsonl"
        req_logger = RequestLogger(log_path)
        payload = {
            "model": "build",
            "messages": [{"role": "user", "content": "list files"}],
            "tools": [
                {
                    "type": "function",
                    "function": {"name": "run_shell", "parameters": {"type": "object"}},
                }
            ],
            "tool_choice": "auto",
        }
        raw_bytes = json.dumps(payload).encode("utf-8")

        req_logger.log_http_request(
            request_id="req-123",
            method="POST",
            path="/v1/chat/completions",
            query_string="debug=1",
            headers={"content-type": "application/json"},
            raw_body=raw_bytes,
        )

        records = _read_jsonl(log_path)
        assert len(records) == 1
        rec = records[0]
        assert rec["event"] == "http_request"
        assert rec["request_id"] == "req-123"
        assert rec["timestamp"].endswith("Z")
        assert rec["method"] == "POST"
        assert rec["path"] == "/v1/chat/completions"
        assert rec["query_string"] == "debug=1"
        assert rec["headers"] == {"content-type": "application/json"}
        assert rec["body"] == payload
        assert rec["raw_body"] == raw_bytes.decode("utf-8")

    def test_log_http_request_non_json_and_empty_body(self, tmp_path: Path) -> None:
        """Non-JSON and empty request bodies set body=None while keeping raw_body."""
        log_path = tmp_path / "traffic.jsonl"
        req_logger = RequestLogger(log_path)

        req_logger.log_http_request(
            request_id="req-raw",
            method="POST",
            path="/v1/chat/completions",
            query_string="",
            headers={"content-type": "text/plain"},
            raw_body=b"not-json-\xff",
        )
        req_logger.log_http_request(
            request_id="req-empty",
            method="GET",
            path="/v1/models",
            query_string="",
            headers={},
            raw_body=b"",
        )

        records = _read_jsonl(log_path)
        assert len(records) == 2
        assert records[0]["body"] is None
        assert records[0]["raw_body"] == "not-json-\ufffd"
        assert records[1]["body"] is None
        assert records[1]["raw_body"] == ""

    def test_log_http_response_json(self, tmp_path: Path) -> None:
        """JSON HTTP responses store parsed body, raw_body, status, and duration."""
        log_path = tmp_path / "traffic.jsonl"
        req_logger = RequestLogger(log_path)
        resp_body = {"id": "chatcmpl-1", "choices": [{"message": {"content": "ok"}}]}
        raw_bytes = json.dumps(resp_body).encode("utf-8")

        req_logger.log_http_response(
            request_id="req-json",
            method="POST",
            path="/v1/chat/completions",
            status_code=200,
            headers={"Content-Type": "application/json"},
            raw_body=raw_bytes,
            duration_ms=12.3456,
        )

        records = _read_jsonl(log_path)
        assert len(records) == 1
        rec = records[0]
        assert rec["event"] == "http_response"
        assert rec["request_id"] == "req-json"
        assert rec["status_code"] == 200
        assert rec["duration_ms"] == 12.346
        assert rec["body"] == resp_body
        assert rec["raw_body"] == raw_bytes.decode("utf-8")
        assert "sse_events" not in rec

    def test_log_http_response_sse_stream(self, tmp_path: Path) -> None:
        """SSE responses parse data JSON events into sse_events alongside raw_body."""
        log_path = tmp_path / "traffic.jsonl"
        req_logger = RequestLogger(log_path)
        tool_chunk = {
            "id": "chatcmpl-1",
            "choices": [{"index": 0, "delta": {}, "finish_reason": None}],
            "acp": {"sessionId": "s1", "update": {"sessionUpdate": "tool_call", "toolCallId": "tc-1"}},
        }
        text_chunk = {
            "id": "chatcmpl-1",
            "choices": [{"index": 0, "delta": {"content": "Hi"}, "finish_reason": None}],
        }
        sse_raw = (
            f"data: {json.dumps(tool_chunk)}\n\n"
            f"data: {json.dumps(text_chunk)}\n\n"
            "data: [DONE]\n\n"
        )

        req_logger.log_http_response(
            request_id="req-sse",
            method="POST",
            path="/v1/chat/completions",
            status_code=200,
            headers={"content-type": "text/event-stream; charset=utf-8"},
            raw_body=sse_raw.encode("utf-8"),
            duration_ms=42.5,
        )

        records = _read_jsonl(log_path)
        assert len(records) == 1
        rec = records[0]
        assert rec["event"] == "http_response"
        assert rec["body"] is None
        assert rec["sse_events"] == [tool_chunk, text_chunk]
        assert rec["raw_body"] == sse_raw

    def test_log_acp_message_correlates_request_id_and_tags_tool_events(self, tmp_path: Path) -> None:
        """ACP send/recv messages inherit current_request_id and tag tool/permission events."""
        log_path = tmp_path / "traffic.jsonl"
        req_logger = RequestLogger(log_path)
        token = current_request_id.set("req-acp-1")
        try:
            req_logger.log_acp_message(
                "send",
                {"jsonrpc": "2.0", "id": 1, "method": "session/prompt", "params": {"sessionId": "s1"}},
            )
            req_logger.log_acp_message(
                "recv",
                {
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {
                        "sessionId": "s1",
                        "update": {
                            "sessionUpdate": "tool_call",
                            "toolCallId": "tc-1",
                            "title": "Run shell",
                            "rawInput": {"command": "ls"},
                        },
                    },
                },
            )
            req_logger.log_acp_message(
                "recv",
                {
                    "jsonrpc": "2.0",
                    "id": 99,
                    "method": "session/request_permission",
                    "params": {
                        "sessionId": "s1",
                        "toolCall": {"toolCallId": "tc-1"},
                        "options": [{"optionId": "allow_once", "kind": "allow_once"}],
                    },
                },
            )
            req_logger.log_acp_message(
                "recv",
                {
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {
                        "sessionId": "s1",
                        "update": {
                            "sessionUpdate": "tool_call_update",
                            "toolCallId": "tc-1",
                            "status": "completed",
                            "rawOutput": {"stdout": "file.txt"},
                        },
                    },
                },
            )
            req_logger.log_acp_message(
                "recv",
                {
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {
                        "sessionId": "s1",
                        "update": {
                            "sessionUpdate": "agent_message_chunk",
                            "content": {"type": "text", "text": "done"},
                        },
                    },
                },
            )
        finally:
            current_request_id.reset(token)

        records = _read_jsonl(log_path)
        assert len(records) == 5
        assert all(r["request_id"] == "req-acp-1" for r in records)

        assert records[0]["event"] == "acp_send"
        assert "tool_event" not in records[0]

        assert records[1]["event"] == "acp_recv"
        assert records[1]["tool_event"] is True
        assert records[1]["kind"] == "tool_call"
        assert records[1]["message"]["params"]["update"]["rawInput"] == {"command": "ls"}

        assert records[2]["event"] == "acp_recv"
        assert records[2]["tool_event"] is True
        assert records[2]["kind"] == "permission_request"
        assert records[2]["message"]["params"]["options"][0]["optionId"] == "allow_once"

        assert records[3]["event"] == "acp_recv"
        assert records[3]["tool_event"] is True
        assert records[3]["kind"] == "tool_call_update"
        assert records[3]["message"]["params"]["update"]["rawOutput"] == {"stdout": "file.txt"}

        assert records[4]["event"] == "acp_recv"
        assert "tool_event" not in records[4]

    def test_write_event_creates_parent_dirs_and_handles_os_error(
        self,
        tmp_path: Path,
        caplog: Any,
    ) -> None:
        """Parent directories are created automatically and OSError is logged without raising."""
        nested_path = tmp_path / "deep" / "nested" / "traffic.jsonl"
        req_logger = RequestLogger(nested_path)
        req_logger.write_event("custom", {"ok": True}, request_id="r1")
        assert nested_path.exists()
        assert _read_jsonl(nested_path)[0]["ok"] is True

        dir_path = tmp_path / "already_a_dir"
        dir_path.mkdir()
        broken_logger = RequestLogger(dir_path)
        with caplog.at_level(logging.WARNING, logger="acpbox.request_logger"):
            broken_logger.write_event("custom", {"ok": False}, request_id="r2")
        assert any("Failed to write request log event" in rec.message for rec in caplog.records)

    def test_sensitive_headers_are_redacted(self, tmp_path: Path) -> None:
        """Authorization, cookie, set-cookie, and x-api-key headers are redacted before writing."""
        log_path = tmp_path / "traffic.jsonl"
        req_logger = RequestLogger(log_path)
        req_logger.log_http_request(
            request_id="req-secret",
            method="POST",
            path="/v1/chat/completions",
            query_string="",
            headers={
                "authorization": "Bearer sk-secret",
                "X-API-Key": "top-secret",
                "cookie": "sid=abc",
                "content-type": "application/json",
            },
            raw_body=b"{}",
        )
        req_logger.log_http_response(
            request_id="req-secret",
            method="POST",
            path="/v1/chat/completions",
            status_code=200,
            headers={"set-cookie": "sid=xyz", "content-type": "application/json"},
            raw_body=b"{}",
            duration_ms=1.0,
        )

        req_rec, resp_rec = _read_jsonl(log_path)
        assert req_rec["headers"]["authorization"] == "[REDACTED]"
        assert req_rec["headers"]["X-API-Key"] == "[REDACTED]"
        assert req_rec["headers"]["cookie"] == "[REDACTED]"
        assert req_rec["headers"]["content-type"] == "application/json"
        assert resp_rec["headers"]["set-cookie"] == "[REDACTED]"

    def test_lone_surrogate_does_not_raise(self, tmp_path: Path) -> None:
        """Records containing lone UTF-16 surrogates write cleanly instead of raising UnicodeEncodeError."""
        log_path = tmp_path / "surrogate.jsonl"
        req_logger = RequestLogger(log_path)
        req_logger.write_event("acp_recv", {"text": "bad-\ud800-char"}, request_id="r-sur")
        records = _read_jsonl(log_path)
        assert len(records) == 1
        assert records[0]["request_id"] == "r-sur"

    def test_sse_preserves_unicode_line_separators_and_multiline_events(self, tmp_path: Path) -> None:
        """SSE parsing splits strictly on CR/LF and joins multi-line data blocks."""
        log_path = tmp_path / "sse.jsonl"
        req_logger = RequestLogger(log_path)
        chunk_with_u2028 = {"choices": [{"delta": {"content": "hello\u2028world"}}]}
        sse_raw = (
            f"data: {json.dumps(chunk_with_u2028, ensure_ascii=False)}\n\n"
            "data: {\"a\":\n"
            "data: 1}\n\n"
            "data: [DONE]\n\n"
        )
        req_logger.log_http_response(
            request_id="req-sse-edge",
            method="POST",
            path="/v1/chat/completions",
            status_code=200,
            headers={"content-type": "text/event-stream"},
            raw_body=sse_raw.encode("utf-8"),
            duration_ms=5.0,
        )
        rec = _read_jsonl(log_path)[0]
        assert rec["sse_events"] == [chunk_with_u2028, {"a": 1}]
