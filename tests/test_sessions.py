"""Tests for DELETE /v1/sessions/{chat_id} (extension)."""

import httpx
import pytest


def test_delete_session_ok(client, acp_responses):
    """DELETE /v1/sessions/{chat_id} after creating responses in that session returns 200."""
    acp_responses[("POST", "/runs")] = httpx.Response(
        200,
        json={"output": [{"role": "agent", "parts": [{"content_type": "text/plain", "content": "x"}]}]},
    )
    create_r = client.post(
        "/v1/responses",
        json={"model": "m", "input": "hi", "chat_id": "sess-1"},
    )
    assert create_r.status_code == 200
    assert create_r.json()["chat_id"] == "sess-1"

    r = client.delete("/v1/sessions/sess-1")
    assert r.status_code == 200
    data = r.json()
    assert data["id"] == "sess-1"
    assert data["object"] == "session"
    assert data["deleted"] is True


def test_delete_session_not_found(client):
    """DELETE /v1/sessions/{chat_id} for unknown session returns 404."""
    r = client.delete("/v1/sessions/unknown-session")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"
