"""End-to-end HTTP and ACP stdio request/response logging tests."""

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from acp.schema import TextContentBlock
from starlette.testclient import TestClient

from acpbox.acp_stdio import AcpRunner
from acpbox.main import create_app
from acpbox.request_logger import RequestLogger, current_request_id

_FAKE_AGENT_SCRIPT = '''
import json
import sys


def send(obj):
    sys.stdout.write(json.dumps(obj) + "\\n")
    sys.stdout.flush()


for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    method = msg.get("method")
    msg_id = msg.get("id")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": msg_id, "result": {"protocolVersion": 1, "agentInfo": {"name": "fake"}}})
    elif method == "session/new":
        send({
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"sessionId": "sess-1", "modes": {"availableModes": [{"id": "build"}]}},
        })
    elif method == "session/set_mode":
        send({"jsonrpc": "2.0", "id": msg_id, "result": {}})
    elif method == "session/prompt":
        send({
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {
                "sessionId": "sess-1",
                "update": {
                    "sessionUpdate": "tool_call",
                    "toolCallId": "tc-1",
                    "title": "Run ls",
                    "kind": "execute",
                    "status": "in_progress",
                    "rawInput": {"command": "ls -la"},
                },
            },
        })
        send({
            "jsonrpc": "2.0",
            "id": 99,
            "method": "session/request_permission",
            "params": {
                "sessionId": "sess-1",
                "toolCall": {"toolCallId": "tc-1"},
                "options": [{"optionId": "allow_once", "kind": "allow_once"}],
            },
        })
        # Wait for gateway permission reply before completing tool call
        reply_line = sys.stdin.readline()
        if not reply_line:
            break
        send({
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {
                "sessionId": "sess-1",
                "update": {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": "tc-1",
                    "status": "completed",
                    "rawOutput": {"stdout": "README.md\\n"},
                },
            },
        })
        send({
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {
                "sessionId": "sess-1",
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "Done!"},
                },
            },
        })
        send({"jsonrpc": "2.0", "id": msg_id, "result": {"stopReason": "end_turn"}})
'''


def _write_fake_agent(tmp_path: Path) -> Path:
    script = tmp_path / "fake_acp_agent.py"
    script.write_text(_FAKE_AGENT_SCRIPT, encoding="utf-8")
    return script


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _configure_env(
    monkeypatch: pytest.MonkeyPatch,
    script: Path,
    workspace: Path,
    log_file: Path | None,
) -> None:
    monkeypatch.delenv("ACPBOX_CONFIG_PATH", raising=False)
    monkeypatch.delenv("CONFIG_PATH", raising=False)
    monkeypatch.delenv("GATEWAY_REQUEST_LOG_FILE", raising=False)
    monkeypatch.setenv("ACPBOX_ACP_COMMAND", json.dumps([sys.executable, str(script)]))
    monkeypatch.setenv("ACPBOX_ACP_WORKSPACE", str(workspace))
    if log_file is not None:
        monkeypatch.setenv("ACPBOX_GATEWAY_REQUEST_LOG_FILE", str(log_file))
    else:
        monkeypatch.delenv("ACPBOX_GATEWAY_REQUEST_LOG_FILE", raising=False)


def test_chat_completion_non_stream_logs_http_and_acp_traffic(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Non-streaming POST /v1/chat/completions logs correlated HTTP + raw ACP tool traffic."""
    script = _write_fake_agent(tmp_path)
    log_file = tmp_path / "logs" / "traffic.jsonl"
    _configure_env(monkeypatch, script, tmp_path, log_file)

    app = create_app()
    req_payload = {
        "model": "build",
        "messages": [{"role": "user", "content": "List workspace files"}],
        "tools": [{"type": "function", "function": {"name": "list_dir", "parameters": {"type": "object"}}}],
        "tool_choice": "auto",
    }
    with TestClient(app) as client:
        resp = client.post("/v1/chat/completions", json=req_payload)

    assert resp.status_code == 200
    body = resp.json()
    assert body["choices"][0]["message"]["content"] == "Done!"
    assert body["acp"]["steps"][0]["type"] == "command"

    records = _read_jsonl(log_file)
    http_reqs = [r for r in records if r["event"] == "http_request"]
    http_resps = [r for r in records if r["event"] == "http_response"]
    assert len(http_reqs) == 1
    assert len(http_resps) == 1

    request_id = http_reqs[0]["request_id"]
    assert request_id
    assert http_reqs[0]["body"]["tools"] == req_payload["tools"]
    assert http_reqs[0]["body"]["tool_choice"] == "auto"
    assert http_resps[0]["request_id"] == request_id
    assert http_resps[0]["status_code"] == 200
    assert http_resps[0]["body"]["acp"]["steps"][0]["type"] == "command"
    assert http_resps[0]["duration_ms"] >= 0

    req_events = [r for r in records if r["request_id"] == request_id]
    assert req_events[0]["event"] == "http_request"
    assert req_events[-1]["event"] == "http_response"

    sent_methods = [r["message"].get("method") for r in req_events if r["event"] == "acp_send"]
    assert "session/new" in sent_methods
    assert "session/set_mode" in sent_methods
    assert "session/prompt" in sent_methods
    prompt_send = next(
        r["message"] for r in req_events if r["event"] == "acp_send" and r["message"].get("method") == "session/prompt"
    )
    validated_blocks = [TextContentBlock.model_validate(b) for b in prompt_send["params"]["prompt"]]
    assert len(validated_blocks) == 1
    assert "List workspace files" in validated_blocks[0].text
    assert any(
        r["event"] == "acp_send"
        and r["message"].get("id") == 99
        and r["message"].get("result") == {"outcome": "allow_once"}
        for r in req_events
    )

    tool_kinds = [r.get("kind") for r in req_events if r["event"] == "acp_recv" and r.get("tool_event")]
    assert tool_kinds == ["tool_call", "permission_request", "tool_call_update"]


def test_chat_completion_stream_logs_sse_and_acp_traffic(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Streaming POST /v1/chat/completions preserves SSE delivery and logs sse_events + ACP tools."""
    script = _write_fake_agent(tmp_path)
    log_file = tmp_path / "stream.jsonl"
    _configure_env(monkeypatch, script, tmp_path, log_file)

    app = create_app()
    with TestClient(app) as client:
        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "build",
                "messages": [{"role": "user", "content": "Run ls"}],
                "stream": True,
            },
        )

    assert resp.status_code == 200
    assert "data: [DONE]" in resp.text
    assert "Done!" in resp.text

    records = _read_jsonl(log_file)
    http_req = next(r for r in records if r["event"] == "http_request")
    http_resp = next(r for r in records if r["event"] == "http_response")
    request_id = http_req["request_id"]
    assert request_id
    assert http_resp["request_id"] == request_id
    assert http_resp["status_code"] == 200
    assert "data: [DONE]" in http_resp["raw_body"]

    sse_acp_updates = [
        ev["acp"]["update"]["sessionUpdate"]
        for ev in http_resp["sse_events"]
        if isinstance(ev, dict) and isinstance(ev.get("acp"), dict)
    ]
    assert "tool_call" in sse_acp_updates
    assert "tool_call_update" in sse_acp_updates

    tool_kinds = [
        r.get("kind")
        for r in records
        if r["request_id"] == request_id and r["event"] == "acp_recv" and r.get("tool_event")
    ]
    assert tool_kinds == ["tool_call", "permission_request", "tool_call_update"]
    prompt_send = next(
        r["message"]
        for r in records
        if r["request_id"] == request_id
        and r["event"] == "acp_send"
        and r["message"].get("method") == "session/prompt"
    )
    validated_blocks = [TextContentBlock.model_validate(b) for b in prompt_send["params"]["prompt"]]
    assert len(validated_blocks) == 1
    assert "Run ls" in validated_blocks[0].text


def test_get_models_logs_http_request_before_acp_traffic(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Bodyless GET /v1/models logs http_request before correlated ACP session/new messages."""
    script = _write_fake_agent(tmp_path)
    log_file = tmp_path / "models.jsonl"
    _configure_env(monkeypatch, script, tmp_path, log_file)

    app = create_app()
    with TestClient(app) as client:
        resp = client.get("/v1/models")

    assert resp.status_code == 200
    assert resp.json()["data"][0]["id"] == "build"

    records = _read_jsonl(log_file)
    http_req = next(r for r in records if r["event"] == "http_request")
    request_id = http_req["request_id"]
    req_events = [r for r in records if r["request_id"] == request_id]
    assert req_events[0]["event"] == "http_request"
    assert any(r["event"] == "acp_send" and r["message"].get("method") == "session/new" for r in req_events)
    assert req_events[-1]["event"] == "http_response"
    assert req_events[-1]["status_code"] == 200


def test_request_logging_disabled_by_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When request_log_file is unset, request_logger is None and no log file is written."""
    script = _write_fake_agent(tmp_path)
    _configure_env(monkeypatch, script, tmp_path, log_file=None)

    app = create_app()
    assert app.state.request_logger is None
    with TestClient(app) as client:
        resp = client.get("/v1/models")
    assert resp.status_code == 200
    assert list(tmp_path.glob("*.jsonl")) == []


@pytest.mark.asyncio
async def test_acp_runner_message_hook_captures_all_messages(tmp_path: Path) -> None:
    """AcpRunner forwards every sent and received JSON-RPC message to message_hook."""
    script = _write_fake_agent(tmp_path)
    log_file = tmp_path / "runner.jsonl"
    req_logger = RequestLogger(log_file)

    runner = AcpRunner(
        command=[sys.executable, str(script)],
        env={},
        workspace=str(tmp_path),
        message_hook=req_logger.log_acp_message,
    )
    token = current_request_id.set("direct-runner-req")
    try:
        await runner.start()
        text, stop_reason, updates = await runner.run_turn(
            prompt_blocks=[{"type": "text", "text": "hello"}],
            mode_id="build",
        )
    finally:
        await runner.stop()
        current_request_id.reset(token)

    assert text == "Done!"
    assert stop_reason == "end_turn"
    assert len(updates) == 2

    records = _read_jsonl(log_file)
    assert all(r["request_id"] == "direct-runner-req" for r in records)
    sent_methods = [r["message"].get("method") for r in records if r["event"] == "acp_send"]
    assert "initialize" in sent_methods
    assert "session/new" in sent_methods
    assert "session/set_mode" in sent_methods
    assert "session/prompt" in sent_methods
    prompt_send = next(
        r["message"] for r in records if r["event"] == "acp_send" and r["message"].get("method") == "session/prompt"
    )
    validated_blocks = [TextContentBlock.model_validate(b) for b in prompt_send["params"]["prompt"]]
    assert validated_blocks == [TextContentBlock(type="text", text="hello")]
    tool_kinds = [r.get("kind") for r in records if r["event"] == "acp_recv" and r.get("tool_event")]
    assert tool_kinds == ["tool_call", "permission_request", "tool_call_update"]


@pytest.mark.asyncio
async def test_acp_runner_survives_raising_message_hook(tmp_path: Path) -> None:
    """A failing message_hook never breaks ACP stdio framing or turn execution."""
    script = _write_fake_agent(tmp_path)

    def broken_hook(_direction: str, _msg: dict) -> None:
        raise RuntimeError("hook exploded")

    runner = AcpRunner(
        command=[sys.executable, str(script)],
        env={},
        workspace=str(tmp_path),
        message_hook=broken_hook,
    )
    try:
        await runner.start()
        text, stop_reason, _updates = await runner.run_turn(
            prompt_blocks=[{"type": "text", "text": "still works"}],
            mode_id="build",
        )
    finally:
        await runner.stop()

    assert text == "Done!"
    assert stop_reason == "end_turn"
