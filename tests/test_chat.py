"""Tests for POST /v1/chat/completions (stateless)."""

import httpx
import pytest


def test_chat_completion_ok(client, acp_responses):
    """POST /v1/chat/completions maps to ACP POST /runs and returns OpenAI format."""
    acp_responses[("POST", "/runs")] = httpx.Response(
        200,
        json={
            "run_id": "run-123",
            "output": [
                {
                    "role": "agent",
                    "parts": [{"content_type": "text/plain", "content": "Hello back"}],
                },
            ],
        },
    )
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


def test_chat_completion_stream_not_supported(client, acp_responses):
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


def test_chat_completion_empty_messages(client, acp_responses):
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


def test_chat_completion_acp_error(client, acp_responses):
    """POST /v1/chat/completions when ACP returns error maps to HTTP and OpenAI error body."""
    acp_responses[("POST", "/runs")] = httpx.Response(
        400,
        json={"code": "invalid_input", "message": "Invalid input"},
    )
    r = client.post(
        "/v1/chat/completions",
        json={
            "model": "my-agent",
            "messages": [{"role": "user", "content": "Hi"}],
        },
    )
    assert r.status_code == 400
    assert r.json()["error"]["message"] == "Invalid input"


def test_chat_completion_multiple_text_parts(client, acp_responses):
    """POST /v1/chat/completions concatenates multiple text/plain parts from ACP output."""
    acp_responses[("POST", "/runs")] = httpx.Response(
        200,
        json={
            "output": [
                {"role": "agent", "parts": [{"content_type": "text/plain", "content": "Hello "}]},
                {"role": "agent", "parts": [{"content_type": "text/plain", "content": "world"}]},
            ],
        },
    )
    r = client.post(
        "/v1/chat/completions",
        json={
            "model": "my-agent",
            "messages": [{"role": "user", "content": "Hi"}],
        },
    )
    assert r.status_code == 200
    assert r.json()["choices"][0]["message"]["content"] == "Hello world"
