"""Configuration loaded from YAML file and environment variables."""

import json
import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


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


class AcpConfig(BaseModel):
    """ACP agent process settings (stdio). Overridable via ACPBOX_ACP_* env."""

    command: list[str] = Field(
        default_factory=lambda: ["opencode", "acp"],
        description="Command to start the ACP agent (list of strings). Env: ACPBOX_ACP_COMMAND as JSON array.",
    )
    env: dict[str, str] = Field(
        default_factory=dict,
        description="Extra env for ACP process. Env: ACPBOX_ACP_ENV as JSON object.",
    )
    workspace: str = Field(
        default="./workspace",
        description="Directory passed as cwd to ACP session/new (resolved to absolute). Env: ACPBOX_ACP_WORKSPACE.",
    )


class GatewayConfig(BaseModel):
    """Gateway HTTP server settings. Overridable via ACPBOX_GATEWAY_* env."""

    host: str = Field(default="0.0.0.0", description="Host to bind. Env: ACPBOX_GATEWAY_HOST.")
    port: int = Field(default=8080, ge=1, le=65535, description="Port for the gateway. Env: ACPBOX_GATEWAY_PORT.")
    workers: int = Field(
        default=1,
        ge=1,
        description="Uvicorn worker processes (one ACP subprocess per worker). Env: ACPBOX_GATEWAY_WORKERS.",
    )
    threads: int = Field(
        default=1,
        ge=1,
        description=(
            "Forwarded to uvicorn.run only if that release defines a matching parameter "
            "(today ASGI uvicorn uses asyncio, not an OS thread pool). Env: ACPBOX_GATEWAY_THREADS."
        ),
    )


class Config(BaseModel):
    """Root configuration: ACP + Gateway, with optional YAML file and env overrides."""

    acp: AcpConfig = Field(default_factory=AcpConfig)
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)

    @classmethod
    def load(cls, config_path: str | Path | None = None) -> "Config":
        """
        Load config from optional YAML file and environment.
        Env vars take precedence over YAML.
        """
        path_str: str | None
        if config_path is not None:
            path_str = str(config_path)
        else:
            path_str = os.environ.get("ACPBOX_CONFIG_PATH") or os.environ.get("CONFIG_PATH")

        path = Path(path_str).resolve() if path_str else None
        yaml_data = load_yaml_config(path) if path else {}

        acp_data: dict[str, Any] = dict(yaml_data.get("acp", {}) or {})
        gateway_data: dict[str, Any] = dict(yaml_data.get("gateway", {}) or {})

        def _env_get(*names: str) -> str | None:
            for n in names:
                if n in os.environ:
                    return os.environ.get(n)
            return None

        # ACP overrides
        acp_command_raw = _env_get("ACPBOX_ACP_COMMAND", "ACP_COMMAND")
        if acp_command_raw is not None:
            if acp_command_raw.strip():
                acp_data["command"] = _parse_list_from_env(acp_command_raw)

        acp_env_raw = _env_get("ACPBOX_ACP_ENV", "ACP_ENV")
        if acp_env_raw is not None:
            acp_data["env"] = _parse_dict_from_env(acp_env_raw)

        acp_workspace_raw = _env_get("ACPBOX_ACP_WORKSPACE", "ACP_WORKSPACE")
        if acp_workspace_raw is not None:
            acp_data["workspace"] = acp_workspace_raw

        # Gateway overrides
        gateway_host_raw = _env_get("ACPBOX_GATEWAY_HOST", "GATEWAY_HOST")
        if gateway_host_raw is not None:
            gateway_data["host"] = gateway_host_raw

        gateway_port_raw = _env_get("ACPBOX_GATEWAY_PORT", "GATEWAY_PORT")
        if gateway_port_raw is not None and gateway_port_raw.strip():
            gateway_data["port"] = int(gateway_port_raw)

        gateway_workers_raw = _env_get("ACPBOX_GATEWAY_WORKERS", "GATEWAY_WORKERS")
        if gateway_workers_raw is not None and gateway_workers_raw.strip():
            gateway_data["workers"] = int(gateway_workers_raw)

        gateway_threads_raw = _env_get("ACPBOX_GATEWAY_THREADS", "GATEWAY_THREADS")
        if gateway_threads_raw is not None and gateway_threads_raw.strip():
            gateway_data["threads"] = int(gateway_threads_raw)

        return cls(acp=AcpConfig(**acp_data), gateway=GatewayConfig(**gateway_data))
