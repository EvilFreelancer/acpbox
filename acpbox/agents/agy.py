"""Antigravity CLI agent config adapter - manages .gemini/antigravity-cli/settings.json in workspace."""

import json
from pathlib import Path
from typing import Any

from .base import AgentConfigAdapter

KNOWN_PERMISSIONS = [
    "read_file(*)",
    "write_file(*)",
    "command(*)",
    "read_url(*)",
    "execute_url(*)",
    "mcp(*)",
]

ALLOWED_VALUES = ["allow", "deny", "ask"]


class AgyAdapter(AgentConfigAdapter):
    """
    Antigravity CLI stores permissions as three lists in .gemini/antigravity-cli/settings.json:

        {"permissions": {"allow": ["command(git)", "read_file(*)"], "ask": ["command(*)"], "deny": ["command(rm -rf)"]}}

    This adapter normalises them to flat {rule: value} dicts and back.
    When a rule appears in multiple lists, native precedence (deny > ask > allow) wins.
    Pattern keys like "command(git *)" or "mcp(linter/*)" are preserved verbatim.
    """

    agent_type = "agy"

    @property
    def config_path(self) -> Path:
        return self.workspace / ".gemini" / "antigravity-cli" / "settings.json"

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

    @staticmethod
    def _lists_to_dict(section: dict[str, Any] | None) -> dict[str, str]:
        if not isinstance(section, dict):
            return {}
        result: dict[str, str] = {}
        for decision in ("allow", "ask", "deny"):
            for rule in section.get(decision) or []:
                result[rule] = decision
        return result

    @staticmethod
    def _dict_to_lists(perms: dict[str, str]) -> dict[str, list[str]]:
        invalid = [v for v in perms.values() if v not in ALLOWED_VALUES]
        if invalid:
            raise ValueError(f"Invalid permission value: {invalid[0]!r}")
        allow = [k for k, v in perms.items() if v == "allow"]
        ask = [k for k, v in perms.items() if v == "ask"]
        deny = [k for k, v in perms.items() if v == "deny"]
        return {"allow": sorted(allow), "ask": sorted(ask), "deny": sorted(deny)}

    def get_permissions(self) -> dict[str, str]:
        return self._lists_to_dict(self.read_config().get("permissions"))

    def set_permissions(self, permissions: dict[str, str]) -> dict[str, str]:
        config = self.read_config()
        config["permissions"] = self._dict_to_lists(permissions)
        self.write_config(config)
        return dict(permissions)

    def update_permissions(self, permissions: dict[str, str]) -> dict[str, str]:
        config = self.read_config()
        current = self._lists_to_dict(config.get("permissions"))
        current.update(permissions)
        config["permissions"] = self._dict_to_lists(current)
        self.write_config(config)
        return current

    def known_permissions(self) -> list[str]:
        return list(KNOWN_PERMISSIONS)

    def allowed_values(self) -> list[str]:
        return list(ALLOWED_VALUES)
