"""Unit tests for gateway.config."""

import os
from pathlib import Path

import pytest

from gateway.config import Config, load_yaml_config


def test_load_yaml_config_missing_file():
    """Missing file returns empty dict."""
    assert load_yaml_config(Path("/nonexistent/path/config.yaml")) == {}


def test_config_load_defaults(monkeypatch):
    """Config.load() with no file uses defaults from env/pydantic."""
    monkeypatch.delenv("CONFIG_PATH", raising=False)
    config = Config.load()
    assert config.gateway.port == 8080
    assert config.gateway.host == "0.0.0.0"
    assert "acp" in config.acp.command or "uvicorn" in config.acp.command
    assert config.acp.startup_timeout_seconds >= 1
