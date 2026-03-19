"""Tests for GET /v1/models and GET /v1/models/{model_id} (from agent modes)."""

import pytest

from acpbox.acp_stdio import AcpStdioError


def test_list_models_from_agent_modes(client):
    """GET /v1/models returns agent availableModes (e.g. plan, build) as model list."""
    r = client.get("/v1/models")
    assert r.status_code == 200
    data = r.json()
    assert data["object"] == "list"
    assert len(data["data"]) == 2
    ids = [m["id"] for m in data["data"]]
    assert ids == ["plan", "build"]
    assert data["data"][0]["object"] == "model"
    assert data["data"][0]["owned_by"] == "acp"


def test_list_models_503_when_agent_fails(client, monkeypatch):
    """GET /v1/models returns 503 when runner.get_agent_models raises."""
    async def _mock_fail():
        raise AcpStdioError("agent not found")
    monkeypatch.setattr(client.app.state.runner, "get_agent_models", _mock_fail)
    r = client.get("/v1/models")
    assert r.status_code == 503
    assert "error" in r.json()


def test_get_model_ok(client):
    """GET /v1/models/{model_id} returns model when in agent modes."""
    r = client.get("/v1/models/plan")
    assert r.status_code == 200
    data = r.json()
    assert data["id"] == "plan"
    assert data["object"] == "model"
    assert data["owned_by"] == "acp"


def test_get_model_not_found(client):
    """GET /v1/models/{model_id} returns 404 when not in agent modes."""
    r = client.get("/v1/models/missing")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"
    assert "does not exist" in r.json()["error"]["message"]
