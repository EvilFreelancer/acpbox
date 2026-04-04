"""Claude Code agent config adapter - manages .claude/settings.json in workspace."""

import json
from pathlib import Path
from typing import Any

from .base import AgentConfigAdapter

KNOWN_PERMISSIONS = [
    "Bash",
    "Read",
    "Write",
    "Edit",
    "MultiEdit",
    "WebFetch",
    "WebSearch",
    "TodoRead",
    "TodoWrite",
    "Glob",
    "Grep",
    "LS",
    "Task",
]

ALLOWED_VALUES = ["allow", "deny"]


class ClaudeAdapter(AgentConfigAdapter):
    """
    Claude Code stores permissions as two lists in .claude/settings.json:

        {"permissions": {"allow": ["Read", "Bash(git *)"], "deny": ["WebFetch"]}}

    This adapter normalises them to flat {tool: value} dicts and back.
    Patterns like "Bash(git *)" are preserved as-is in the key.
    """

    agent_type = "claude"

    @property
    def config_path(self) -> Path:
        return self.workspace / ".claude" / "settings.json"

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

    def _lists_to_dict(self, section: dict[str, Any]) -> dict[str, str]:
        result: dict[str, str] = {}
        for tool in section.get("allow", []):
            result[tool] = "allow"
        for tool in section.get("deny", []):
            result[tool] = "deny"
        return result

    @staticmethod
    def _dict_to_lists(perms: dict[str, str]) -> dict[str, list[str]]:
        allow = [k for k, v in perms.items() if v == "allow"]
        deny = [k for k, v in perms.items() if v == "deny"]
        return {"allow": sorted(allow), "deny": sorted(deny)}

    def get_permissions(self) -> dict[str, str]:
        return self._lists_to_dict(self.read_config().get("permissions", {}))

    def set_permissions(self, permissions: dict[str, str]) -> dict[str, str]:
        config = self.read_config()
        config["permissions"] = self._dict_to_lists(permissions)
        self.write_config(config)
        return dict(permissions)

    def update_permissions(self, permissions: dict[str, str]) -> dict[str, str]:
        current = self.get_permissions()
        current.update(permissions)
        config = self.read_config()
        config["permissions"] = self._dict_to_lists(current)
        self.write_config(config)
        return current

    def known_permissions(self) -> list[str]:
        return list(KNOWN_PERMISSIONS)

    def allowed_values(self) -> list[str]:
        return list(ALLOWED_VALUES)
