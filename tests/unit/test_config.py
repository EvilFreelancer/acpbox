"""Unit tests for acpbox.config."""

import os
from pathlib import Path

import pytest

from acpbox.config import Config, load_yaml_config


def test_load_yaml_config_missing_file():
    """Missing file returns empty dict."""
    assert load_yaml_config(Path("/nonexistent/path/config.yaml")) == {}


def test_config_load_defaults(monkeypatch):
    """Config.load() with no file uses defaults from env/pydantic."""
    monkeypatch.delenv("ACPBOX_CONFIG_PATH", raising=False)
    monkeypatch.delenv("ACPBOX_GATEWAY_WORKERS", raising=False)
    monkeypatch.delenv("ACPBOX_GATEWAY_THREADS", raising=False)
    config = Config.load()
    assert config.gateway.port == 8080
    assert config.gateway.host == "0.0.0.0"
    assert config.gateway.workers == 1
    assert config.gateway.threads == 1
    assert config.acp.command
    assert config.acp.workspace == "./workspace"


def test_gateway_workers_from_env(monkeypatch):
    """GATEWAY_WORKERS overrides defaults."""
    monkeypatch.delenv("ACPBOX_CONFIG_PATH", raising=False)
    monkeypatch.delenv("ACPBOX_GATEWAY_THREADS", raising=False)
    monkeypatch.setenv("ACPBOX_GATEWAY_WORKERS", "4")
    config = Config.load()
    assert config.gateway.workers == 4


def test_gateway_threads_from_env(monkeypatch):
    """GATEWAY_THREADS overrides defaults."""
    monkeypatch.delenv("ACPBOX_CONFIG_PATH", raising=False)
    monkeypatch.delenv("ACPBOX_GATEWAY_WORKERS", raising=False)
    monkeypatch.setenv("ACPBOX_GATEWAY_THREADS", "8")
    config = Config.load()
    assert config.gateway.threads == 8
