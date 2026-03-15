"""Tests for POST /v1/chat/completions (stateless) via mocked ACP stdio."""

import pytest

from gateway.acp_stdio import AcpStdioError


@pytest.fixture
def mock_run_single_turn(monkeypatch):
    """Mock run_single_turn to avoid spawning real ACP process."""
    async def _mock(*args, **kwargs):
        return ("Hello back", "end_turn")
    monkeypatch.setattr("gateway.routes.chat.run_single_turn", _mock)
    return _mock


def test_chat_completion_ok(client, mock_run_single_turn):
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
    assert data["choices"][0]["message"]["content"] == "Hello back"
    assert data["choices"][0]["finish_reason"] == "stop"
    assert "usage" in data


def test_chat_completion_stream_not_supported(client, mock_run_single_turn):
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


def test_chat_completion_empty_messages(client, mock_run_single_turn):
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
    """POST /v1/chat/completions when ACP raises maps to 503 and OpenAI error body."""
    async def _mock_err(*args, **kwargs):
        raise AcpStdioError("Invalid input")
    monkeypatch.setattr("gateway.routes.chat.run_single_turn", _mock_err)
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
    """POST /v1/chat/completions returns concatenated text from mock."""
    async def _mock(*args, **kwargs):
        return ("Hello world", "end_turn")
    monkeypatch.setattr("gateway.routes.chat.run_single_turn", _mock)
    r = client.post(
        "/v1/chat/completions",
        json={
            "model": "my-agent",
            "messages": [{"role": "user", "content": "Hi"}],
        },
    )
    assert r.status_code == 200
    assert r.json()["choices"][0]["message"]["content"] == "Hello world"
