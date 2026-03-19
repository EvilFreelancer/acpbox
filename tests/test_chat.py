"""Tests for POST /v1/chat/completions (stateless) via mocked per-worker runner."""

import json

import pytest

from acpbox.acp_stdio import AcpStdioError


def test_chat_completion_ok(client):
    """POST /v1/chat/completions returns OpenAI format from mocked ACP."""
    r = client.post(
        "/v1/chat/completions",
        json={
            "model": "my-agent",
            "messages": [{"role": "user", "content": "Hello"}],
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["object"] == "chat.completion"
    assert data["model"] == "my-agent"
    assert len(data["choices"]) == 1
    assert data["choices"][0]["message"]["role"] == "assistant"
    assert data["choices"][0]["message"]["content"] == "Reply"
    assert data["choices"][0]["finish_reason"] == "stop"
    assert "usage" in data


def _parse_sse_chat_chunks(response) -> list[dict]:
    """Collect JSON objects from text/event-stream lines (excluding [DONE])."""
    chunks: list[dict] = []
    for line in response.iter_lines():
        if not line:
            continue
        text = line.decode("utf-8") if isinstance(line, (bytes, bytearray)) else line
        if not text.startswith("data: "):
            continue
        payload = text[len("data: ") :].strip()
        if payload == "[DONE]":
            continue
        chunks.append(json.loads(payload))
    return chunks


def test_chat_completion_stream_sse_and_content(client):
    """POST /v1/chat/completions with stream=true returns SSE chunks and full assistant text."""
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "my-agent",
            "messages": [{"role": "user", "content": "Hi"}],
            "stream": True,
        },
    ) as r:
        assert r.status_code == 200
        assert "text/event-stream" in (r.headers.get("content-type") or "").lower()
        parts = _parse_sse_chat_chunks(r)
        assert len(parts) >= 2
        assert all(p.get("object") == "chat.completion.chunk" for p in parts)
        assert parts[0]["choices"][0]["delta"].get("role") == "assistant"
        assembled = "".join(
            (c["choices"][0]["delta"].get("content") or "")
            for c in parts
            if c["choices"][0].get("finish_reason") is None
        )
        assert assembled == "Reply"
        final = [c for c in parts if c["choices"][0].get("finish_reason") is not None]
        assert len(final) == 1
        assert final[0]["choices"][0]["finish_reason"] == "stop"


def test_chat_completion_stream_multipart_text(client, monkeypatch):
    """Stream merges multiple text parts from run_turn_stream."""

    async def _mock_stream(*args, **kwargs):
        yield ("text", "Hel")
        yield ("text", "lo")
        yield ("done", "end_turn")

    monkeypatch.setattr(client.app.state.runner, "run_turn_stream", _mock_stream)
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "my-agent",
            "messages": [{"role": "user", "content": "Hi"}],
            "stream": True,
        },
    ) as r:
        assert r.status_code == 200
        parts = _parse_sse_chat_chunks(r)
        assembled = "".join(
            (c["choices"][0]["delta"].get("content") or "")
            for c in parts
            if c["choices"][0].get("finish_reason") is None
        )
        assert assembled == "Hello"


def test_chat_completion_stream_acp_session_updates(client, monkeypatch):
    """Streaming chunks may include acp.sessionId and acp.update (e.g. tool_call) from ACP."""

    async def _mock_stream(*args, **kwargs):
        yield (
            "session",
            {
                "sessionId": "sess-1",
                "update": {
                    "sessionUpdate": "tool_call",
                    "toolCallId": "tc_1",
                    "title": "run shell",
                    "status": "in_progress",
                },
            },
        )
        yield ("text", "Done.")
        yield ("done", "end_turn")

    monkeypatch.setattr(client.app.state.runner, "run_turn_stream", _mock_stream)
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "my-agent",
            "messages": [{"role": "user", "content": "Hi"}],
            "stream": True,
        },
    ) as r:
        assert r.status_code == 200
        parts = _parse_sse_chat_chunks(r)
        acp_parts = [p for p in parts if "acp" in p]
        assert len(acp_parts) == 1
        assert acp_parts[0]["acp"]["sessionId"] == "sess-1"
        assert acp_parts[0]["acp"]["update"]["sessionUpdate"] == "tool_call"
        assert acp_parts[0]["acp"]["update"]["toolCallId"] == "tc_1"
        assembled = "".join(
            (c["choices"][0]["delta"].get("content") or "")
            for c in parts
            if c["choices"][0].get("finish_reason") is None
        )
        assert assembled == "Done."


def test_chat_completion_stream_acp_error(client, monkeypatch):
    """When run_turn_stream fails before producing data, the response is an error status."""

    async def _mock_err(*args, **kwargs):
        raise AcpStdioError("boom")
        yield  # pragma: no cover

    monkeypatch.setattr(client.app.state.runner, "run_turn_stream", _mock_err)
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "my-agent",
            "messages": [{"role": "user", "content": "Hi"}],
            "stream": True,
        },
    ) as r:
        assert r.status_code == 503
        body = r.read().decode("utf-8")
        assert "boom" in body


def test_chat_completion_empty_messages(client):
    """POST /v1/chat/completions with empty messages returns 400."""
    r = client.post(
        "/v1/chat/completions",
        json={
            "model": "my-agent",
            "messages": [],
        },
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_input"


def test_chat_completion_acp_error(client, monkeypatch):
    """POST /v1/chat/completions when runner.run_turn raises maps to 503."""
    async def _mock_err(*args, **kwargs):
        raise AcpStdioError("Invalid input")
    monkeypatch.setattr(client.app.state.runner, "run_turn", _mock_err)
    r = client.post(
        "/v1/chat/completions",
        json={
            "model": "my-agent",
            "messages": [{"role": "user", "content": "Hi"}],
        },
    )
    assert r.status_code == 503
    assert r.json()["error"]["message"] == "Invalid input"


def test_chat_completion_multiple_text_parts(client, monkeypatch):
    """POST /v1/chat/completions returns text from mock run_turn."""
    async def _mock(*args, **kwargs):
        return ("Hello world", "end_turn", [])
    monkeypatch.setattr(client.app.state.runner, "run_turn", _mock)
    r = client.post(
        "/v1/chat/completions",
        json={
            "model": "my-agent",
            "messages": [{"role": "user", "content": "Hi"}],
        },
    )
    assert r.status_code == 200
    assert r.json()["choices"][0]["message"]["content"] == "Hello world"


def test_chat_completion_non_stream_includes_acp_trace(client, monkeypatch):
    """POST /v1/chat/completions with stream=false includes acp.steps (summarized trace)."""

    async def _mock(*args, **kwargs):
        return (
            "Done.",
            "end_turn",
            [
                {"sessionId": "s1", "update": {"sessionUpdate": "agent_thought_chunk", "content": {"type": "text", "text": "think"}}},
                {
                    "sessionId": "s1",
                    "update": {"sessionUpdate": "tool_call", "toolCallId": "t1", "title": "bash", "kind": "execute"},
                },
                {
                    "sessionId": "s1",
                    "update": {
                        "sessionUpdate": "tool_call_update",
                        "toolCallId": "t1",
                        "status": "completed",
                        "rawInput": {"command": "true", "description": "noop"},
                        "rawOutput": {"output": "", "metadata": {"exit": 0}},
                    },
                },
            ],
        )

    monkeypatch.setattr(client.app.state.runner, "run_turn", _mock)
    r = client.post(
        "/v1/chat/completions",
        json={
            "model": "my-agent",
            "messages": [{"role": "user", "content": "Hi"}],
            "stream": False,
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["choices"][0]["message"]["content"] == "Done."
    steps = data["acp"]["steps"]
    assert steps[0] == {"type": "reasoning", "text": "think"}
    assert steps[1]["type"] == "command"
    assert steps[1]["tool_call_id"] == "t1"
    assert steps[1]["command"] == "true"
    assert steps[1]["status"] == "completed"
    assert steps[1]["exit_code"] == 0
