"""Tests for GET /v1/models and GET /v1/models/{model_id}."""

import pytest


def test_list_models_empty(client):
    """GET /v1/models when config has no models returns empty data list."""
    client.app.state.config.acp.models = []
    r = client.get("/v1/models")
    assert r.status_code == 200
    data = r.json()
    assert data["object"] == "list"
    assert data["data"] == []


def test_list_models_one_agent(client):
    """GET /v1/models returns config models as OpenAI model list."""
    client.app.state.config.acp.models = ["my-agent"]
    r = client.get("/v1/models")
    assert r.status_code == 200
    data = r.json()
    assert len(data["data"]) == 1
    assert data["data"][0]["id"] == "my-agent"
    assert data["data"][0]["object"] == "model"
    assert data["data"][0]["owned_by"] == "acp"
    assert "created" in data["data"][0]


def test_list_models_multiple_agents(client):
    """GET /v1/models returns all configured models."""
    client.app.state.config.acp.models = ["a1", "a2"]
    r = client.get("/v1/models")
    assert r.status_code == 200
    ids = [m["id"] for m in r.json()["data"]]
    assert ids == ["a1", "a2"]


def test_get_model_ok(client):
    """GET /v1/models/{model_id} returns model when in config."""
    client.app.state.config.acp.models = ["my-agent"]
    r = client.get("/v1/models/my-agent")
    assert r.status_code == 200
    data = r.json()
    assert data["id"] == "my-agent"
    assert data["object"] == "model"
    assert data["owned_by"] == "acp"


def test_get_model_not_found(client):
    """GET /v1/models/{model_id} returns 404 when not in config."""
    client.app.state.config.acp.models = ["other"]
    r = client.get("/v1/models/missing")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"
    assert "does not exist" in r.json()["error"]["message"]
