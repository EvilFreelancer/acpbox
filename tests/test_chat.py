"""Tests for POST /v1/chat/completions (stateless) via mocked per-worker runner."""

import pytest

from gateway.acp_stdio import AcpStdioError


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


def test_chat_completion_stream_not_supported(client):
    """POST /v1/chat/completions with stream=true returns 400."""
    r = client.post(
        "/v1/chat/completions",
        json={
            "model": "my-agent",
            "messages": [{"role": "user", "content": "Hi"}],
            "stream": True,
        },
    )
    assert r.status_code == 400
    assert "stream" in r.json()["error"]["message"].lower() or "not yet supported" in r.json()["error"]["message"]


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
        return ("Hello world", "end_turn")
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
