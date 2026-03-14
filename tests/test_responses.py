"""Tests for POST /v1/responses, DELETE /v1/responses/{id}, GET /v1/responses/{id}."""

import httpx
import pytest


def test_create_response_ok(client, acp_responses):
    """POST /v1/responses with model and input returns response with id and chat_id."""
    acp_responses[("POST", "/runs")] = httpx.Response(
        200,
        json={
            "output": [
                {"role": "agent", "parts": [{"content_type": "text/plain", "content": "Reply"}]},
            ],
        },
    )
    r = client.post(
        "/v1/responses",
        json={
            "model": "my-agent",
            "input": "Hello",
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["object"] == "response"
    assert data["model"] == "my-agent"
    assert data["id"].startswith("resp_")
    assert data["chat_id"] is not None
    assert len(data["output"]) == 1
    assert data["output"][0]["content"][0]["text"] == "Reply"


def test_create_response_with_chat_id(client, acp_responses):
    """POST /v1/responses with chat_id sends session_id to ACP and returns same chat_id."""
    acp_responses[("POST", "/runs")] = httpx.Response(
        200,
        json={"output": [{"role": "agent", "parts": [{"content_type": "text/plain", "content": "Hi"}]}]},
    )
    r = client.post(
        "/v1/responses",
        json={
            "model": "my-agent",
            "input": "Hi",
            "chat_id": "session-123",
        },
    )
    assert r.status_code == 200
    assert r.json()["chat_id"] == "session-123"


def test_create_response_empty_input(client, acp_responses):
    """POST /v1/responses with empty input returns 400."""
    r = client.post(
        "/v1/responses",
        json={
            "model": "my-agent",
            "input": [],
        },
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_input"


def test_delete_response_ok(client, acp_responses):
    """DELETE /v1/responses/{id} after creating one returns 200 and deleted body."""
    acp_responses[("POST", "/runs")] = httpx.Response(
        200,
        json={"output": [{"role": "agent", "parts": [{"content_type": "text/plain", "content": "x"}]}]},
    )
    create_r = client.post("/v1/responses", json={"model": "m", "input": "hi"})
    assert create_r.status_code == 200
    response_id = create_r.json()["id"]

    r = client.delete(f"/v1/responses/{response_id}")
    assert r.status_code == 200
    assert r.json()["id"] == response_id
    assert r.json()["deleted"] is True


def test_delete_response_not_found(client):
    """DELETE /v1/responses/{id} for unknown id returns 404."""
    r = client.delete("/v1/responses/resp_nonexistent")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_get_response_not_implemented(client):
    """GET /v1/responses/{id} returns 501."""
    r = client.get("/v1/responses/resp_any")
    assert r.status_code == 501
    assert "not implemented" in r.json()["error"]["message"].lower() or "not stored" in r.json()["error"]["message"].lower()
