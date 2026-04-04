"""OpenAI Codex agent config adapter - manages codex.json in workspace."""

import json
from pathlib import Path
from typing import Any

from .base import AgentConfigAdapter

KNOWN_PERMISSIONS = [
    "read",
    "write",
    "bash",
    "network",
]

ALLOWED_VALUES = ["allow", "deny"]

APPROVAL_MODE_MAP = {
    "suggest": {"read": "deny", "write": "deny", "bash": "deny", "network": "deny"},
    "auto-edit": {"read": "allow", "write": "allow", "bash": "deny", "network": "deny"},
    "full-auto": {"read": "allow", "write": "allow", "bash": "allow", "network": "allow"},
}


class CodexAdapter(AgentConfigAdapter):
    """
    Codex CLI uses approval_mode (suggest / auto-edit / full-auto).
    We store a codex.json with an explicit permission map + approval_mode for
    backward compat.  The adapter also honours the preset helper to flip modes.
    """

    agent_type = "codex"

    @property
    def config_path(self) -> Path:
        return self.workspace / "codex.json"

    def read_config(self) -> dict[str, Any]:
        p = self.config_path
        if not p.exists():
            return {}
        with open(p) as f:
            return json.load(f)

    def write_config(self, config: dict[str, Any]) -> None:
        p = self.config_path
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
            f.write("\n")

    def get_permissions(self) -> dict[str, str]:
        config = self.read_config()
        return dict(config.get("permission", {}))

    def set_permissions(self, permissions: dict[str, str]) -> dict[str, str]:
        config = self.read_config()
        config["permission"] = dict(permissions)
        self.write_config(config)
        return dict(permissions)

    def update_permissions(self, permissions: dict[str, str]) -> dict[str, str]:
        config = self.read_config()
        current = dict(config.get("permission", {}))
        current.update(permissions)
        config["permission"] = current
        self.write_config(config)
        return current

    def known_permissions(self) -> list[str]:
        return list(KNOWN_PERMISSIONS)

    def allowed_values(self) -> list[str]:
        return list(ALLOWED_VALUES)

    def apply_preset(self, preset: str) -> dict[str, str]:
        if preset in APPROVAL_MODE_MAP:
            perms = dict(APPROVAL_MODE_MAP[preset])
            config = self.read_config()
            config["approval_mode"] = preset
            config["permission"] = perms
            self.write_config(config)
            return perms
        return super().apply_preset(preset)
