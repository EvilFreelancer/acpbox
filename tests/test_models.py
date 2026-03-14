"""Tests for GET /v1/models and GET /v1/models/{model_id}."""

import httpx
import pytest


def test_list_models_empty(client, acp_responses):
    """GET /v1/models when ACP returns no agents returns empty data list."""
    acp_responses[("GET", "/agents")] = httpx.Response(200, json={"agents": []})
    r = client.get("/v1/models")
    assert r.status_code == 200
    data = r.json()
    assert data["object"] == "list"
    assert data["data"] == []


def test_list_models_one_agent(client, acp_responses):
    """GET /v1/models maps ACP agents to OpenAI model list."""
    acp_responses[("GET", "/agents")] = httpx.Response(
        200,
        json={"agents": [{"name": "my-agent"}]},
    )
    r = client.get("/v1/models")
    assert r.status_code == 200
    data = r.json()
    assert len(data["data"]) == 1
    assert data["data"][0]["id"] == "my-agent"
    assert data["data"][0]["object"] == "model"
    assert data["data"][0]["owned_by"] == "acp"
    assert "created" in data["data"][0]


def test_list_models_multiple_agents(client, acp_responses):
    """GET /v1/models returns all agents as models."""
    acp_responses[("GET", "/agents")] = httpx.Response(
        200,
        json={"agents": [{"name": "a1"}, {"name": "a2"}]},
    )
    r = client.get("/v1/models")
    assert r.status_code == 200
    ids = [m["id"] for m in r.json()["data"]]
    assert ids == ["a1", "a2"]


def test_list_models_acp_error_400(client, acp_responses):
    """GET /v1/models when ACP returns invalid_input maps to 400 and OpenAI error body."""
    acp_responses[("GET", "/agents")] = httpx.Response(
        400,
        json={"code": "invalid_input", "message": "Bad request"},
    )
    r = client.get("/v1/models")
    assert r.status_code == 400
    assert "error" in r.json()
    assert r.json()["error"]["code"] == "invalid_input"
    assert r.json()["error"]["message"] == "Bad request"


def test_list_models_acp_error_404(client, acp_responses):
    """GET /v1/models when ACP returns not_found maps to 404."""
    acp_responses[("GET", "/agents")] = httpx.Response(
        404,
        json={"code": "not_found", "message": "Not found"},
    )
    r = client.get("/v1/models")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_list_models_acp_server_error(client, acp_responses):
    """GET /v1/models when ACP returns server_error maps to 500."""
    acp_responses[("GET", "/agents")] = httpx.Response(
        500,
        json={"code": "server_error", "message": "Internal error"},
    )
    r = client.get("/v1/models")
    assert r.status_code == 500
    assert r.json()["error"]["message"] == "Internal error"


def test_get_model_ok(client, acp_responses):
    """GET /v1/models/{model_id} returns model when ACP agent exists."""
    acp_responses[("GET", "/agents/my-agent")] = httpx.Response(
        200,
        json={"name": "my-agent"},
    )
    r = client.get("/v1/models/my-agent")
    assert r.status_code == 200
    data = r.json()
    assert data["id"] == "my-agent"
    assert data["object"] == "model"
    assert data["owned_by"] == "acp"


def test_get_model_not_found(client, acp_responses):
    """GET /v1/models/{model_id} returns 404 when ACP returns 404."""
    acp_responses[("GET", "/agents/missing")] = httpx.Response(
        404,
        json={"code": "not_found", "message": "Agent not found"},
    )
    r = client.get("/v1/models/missing")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"
    assert "does not exist" in r.json()["error"]["message"]
