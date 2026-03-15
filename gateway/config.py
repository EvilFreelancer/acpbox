"""Configuration loaded from YAML file and environment variables."""

import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def load_yaml_config(path: Path) -> dict[str, Any]:
    """Load YAML file into a dict. Returns {} if file does not exist."""
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _parse_list_from_env(v: list[str] | str) -> list[str]:
    """Parse list from env (JSON array string) or return as-is."""
    if isinstance(v, str):
        return json.loads(v)
    return v


def _parse_dict_from_env(v: dict[str, str] | str) -> dict[str, str]:
    """Parse dict from env (JSON object string) or return as-is."""
    if isinstance(v, str):
        return json.loads(v) if v.strip() else {}
    return v


class AcpConfig(BaseSettings):
    """ACP agent process settings (stdio, one process per request). All fields overridable via ACP_* env."""

    model_config = SettingsConfigDict(env_prefix="ACP_", extra="ignore")

    command: list[str] = Field(
        default_factory=lambda: ["opencode", "acp"],
        description="Command to start the ACP agent (list of strings). Env: ACP_COMMAND as JSON array.",
    )
    env: dict[str, str] = Field(default_factory=dict, description="Extra env for ACP process. Env: ACP_ENV as JSON object.")
    models: list[str] = Field(
        default_factory=lambda: ["default"],
        description="List of model ids for GET /v1/models (no agent spawn). Env: ACP_MODELS as JSON array.",
    )
    cwd: str | None = Field(default=None, description="Working directory for session/new. Env: ACP_CWD. If unset, current process cwd.")

    @field_validator("command", mode="before")
    @classmethod
    def command_from_env(cls, v: Any) -> list[str]:
        return _parse_list_from_env(v) if isinstance(v, (list, str)) else v

    @field_validator("env", mode="before")
    @classmethod
    def env_from_env(cls, v: Any) -> dict[str, str]:
        return _parse_dict_from_env(v) if isinstance(v, (dict, str)) else v or {}

    @field_validator("models", mode="before")
    @classmethod
    def models_from_env(cls, v: Any) -> list[str]:
        return _parse_list_from_env(v) if isinstance(v, (list, str)) else (v if isinstance(v, list) else ["default"])


class GatewayConfig(BaseSettings):
    """Gateway HTTP server settings. All fields overridable via GATEWAY_* env."""

    model_config = SettingsConfigDict(env_prefix="GATEWAY_", extra="ignore")

    host: str = Field(default="0.0.0.0", description="Host to bind. Env: GATEWAY_HOST.")
    port: int = Field(default=8080, ge=1, le=65535, description="Port for the gateway. Env: GATEWAY_PORT.")


class Config(BaseSettings):
    """Root configuration: ACP + Gateway, with optional YAML file and env overrides."""

    model_config = SettingsConfigDict(
        env_nested_delimiter="__",
        extra="ignore",
    )

    acp: AcpConfig = Field(default_factory=AcpConfig)
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)

    @classmethod
    def load(cls, config_path: str | Path | None = None) -> "Config":
        """
        Load config from optional YAML file and environment.
        Env vars take precedence over YAML. CONFIG_PATH can override path.
        """
        path_str = config_path
        if path_str is None:
            from os import environ
            path_str = environ.get("CONFIG_PATH")
        path = Path(path_str).resolve() if path_str else None
        yaml_data = load_yaml_config(path) if path else {}

        acp_data = yaml_data.get("acp", {})
        gateway_data = yaml_data.get("gateway", {})

        return cls(
            acp=AcpConfig(**acp_data),
            gateway=GatewayConfig(**gateway_data),
        )
