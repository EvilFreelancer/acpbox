"""OpenCode agent config adapter - manages opencode.json in workspace."""

import json
from pathlib import Path
from typing import Any

from .base import AgentConfigAdapter

KNOWN_PERMISSIONS = [
    "read",
    "edit",
    "bash",
    "glob",
    "grep",
    "list",
    "task",
    "external_directory",
    "lsp",
    "skill",
    "todowrite",
    "question",
    "webfetch",
    "websearch",
    "codesearch",
    "doom_loop",
]

ALLOWED_VALUES = ["allow", "deny", "ask"]


class OpenCodeAdapter(AgentConfigAdapter):
    agent_type = "opencode"

    @property
    def config_path(self) -> Path:
        return self.workspace / "opencode.json"

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
        return dict(self.read_config().get("permission", {}))

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
