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
    monkeypatch.delenv("ACPBOX_GATEWAY_REQUEST_LOG_FILE", raising=False)
    monkeypatch.delenv("GATEWAY_REQUEST_LOG_FILE", raising=False)
    config = Config.load()
    assert config.gateway.port == 8080
    assert config.gateway.host == "0.0.0.0"
    assert config.gateway.workers == 1
    assert config.gateway.threads == 1
    assert config.gateway.request_log_file is None
    assert config.acp.command
    assert config.acp.workspace == "./workspace"
    assert config.acp.stderr == "inherit"
    assert config.acp.stderr_pipe_split is False


def test_gateway_workers_from_env(monkeypatch):
    """GATEWAY_WORKERS overrides defaults."""
    monkeypatch.delenv("ACPBOX_CONFIG_PATH", raising=False)
    monkeypatch.delenv("ACPBOX_GATEWAY_THREADS", raising=False)
    monkeypatch.setenv("ACPBOX_GATEWAY_WORKERS", "4")
    config = Config.load()
    assert config.gateway.workers == 4


def test_acp_stderr_from_env(monkeypatch):
    """ACPBOX_ACP_STDERR and ACPBOX_ACP_STDERR_PIPE_SPLIT override acp stderr settings."""
    monkeypatch.delenv("ACPBOX_CONFIG_PATH", raising=False)
    monkeypatch.setenv("ACPBOX_ACP_STDERR", "pipe")
    monkeypatch.setenv("ACPBOX_ACP_STDERR_PIPE_SPLIT", "1")
    config = Config.load()
    assert config.acp.stderr == "pipe"
    assert config.acp.stderr_pipe_split is True


def test_gateway_threads_from_env(monkeypatch):
    """GATEWAY_THREADS overrides defaults."""
    monkeypatch.delenv("ACPBOX_CONFIG_PATH", raising=False)
    monkeypatch.delenv("ACPBOX_GATEWAY_WORKERS", raising=False)
    monkeypatch.setenv("ACPBOX_GATEWAY_THREADS", "8")
    config = Config.load()
    assert config.gateway.threads == 8


def test_gateway_request_log_file_from_yaml(monkeypatch, tmp_path):
    """gateway.request_log_file is loaded from YAML config."""
    monkeypatch.delenv("ACPBOX_CONFIG_PATH", raising=False)
    monkeypatch.delenv("ACPBOX_GATEWAY_REQUEST_LOG_FILE", raising=False)
    monkeypatch.delenv("GATEWAY_REQUEST_LOG_FILE", raising=False)
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("gateway:\n  request_log_file: ./logs/traffic.jsonl\n", encoding="utf-8")
    config = Config.load(cfg_file)
    assert config.gateway.request_log_file == "./logs/traffic.jsonl"


def test_gateway_request_log_file_env_override(monkeypatch, tmp_path):
    """ACPBOX_GATEWAY_REQUEST_LOG_FILE overrides YAML and ignores empty values."""
    monkeypatch.delenv("ACPBOX_CONFIG_PATH", raising=False)
    monkeypatch.delenv("GATEWAY_REQUEST_LOG_FILE", raising=False)
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("gateway:\n  request_log_file: ./logs/from-yaml.jsonl\n", encoding="utf-8")

    monkeypatch.setenv("ACPBOX_GATEWAY_REQUEST_LOG_FILE", "  ")
    config_empty = Config.load(cfg_file)
    assert config_empty.gateway.request_log_file == "./logs/from-yaml.jsonl"

    monkeypatch.setenv("ACPBOX_GATEWAY_REQUEST_LOG_FILE", " /tmp/from-env.jsonl ")
    config_env = Config.load(cfg_file)
    assert config_env.gateway.request_log_file == "/tmp/from-env.jsonl"


def test_config_request_log_file_whitespace_yaml_normalized_to_none(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Whitespace-only gateway.request_log_file in YAML normalizes to None."""
    monkeypatch.delenv("ACPBOX_GATEWAY_REQUEST_LOG_FILE", raising=False)
    monkeypatch.delenv("GATEWAY_REQUEST_LOG_FILE", raising=False)
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("gateway:\n  request_log_file: '   '\n")
    config = Config.load(cfg_file)
    assert config.gateway.request_log_file is None
