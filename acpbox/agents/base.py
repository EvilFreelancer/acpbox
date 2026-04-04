"""Abstract base for agent-specific config adapters."""

import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class AgentConfigAdapter(ABC):
    """Reads/writes agent-specific config files to manage permissions at runtime."""

    agent_type: str = "unknown"

    def __init__(self, workspace: str) -> None:
        self.workspace = Path(workspace).expanduser().resolve()

    @property
    @abstractmethod
    def config_path(self) -> Path:
        ...

    @property
    def is_writable(self) -> bool:
        """True when the config file can be created or overwritten."""
        p = self.config_path
        if p.exists():
            return os.access(p, os.W_OK)
        parent = p.parent
        while not parent.exists():
            parent = parent.parent
        return os.access(parent, os.W_OK)

    @abstractmethod
    def read_config(self) -> dict[str, Any]:
        ...

    @abstractmethod
    def write_config(self, config: dict[str, Any]) -> None:
        ...

    @abstractmethod
    def get_permissions(self) -> dict[str, str]:
        ...

    @abstractmethod
    def set_permissions(self, permissions: dict[str, str]) -> dict[str, str]:
        """Replace all permissions. Returns the new full state."""
        ...

    @abstractmethod
    def update_permissions(self, permissions: dict[str, str]) -> dict[str, str]:
        """Merge into existing permissions. Returns the new full state."""
        ...

    @abstractmethod
    def known_permissions(self) -> list[str]:
        """Well-known permission keys for this agent."""
        ...

    @abstractmethod
    def allowed_values(self) -> list[str]:
        """Valid permission values (e.g. allow, deny, ask)."""
        ...

    def apply_preset(self, preset: str) -> dict[str, str]:
        """Apply a named preset and return the resulting permissions."""
        vals = self.allowed_values()
        keys = self.known_permissions()
        if preset == "allow_all":
            new = {k: "allow" for k in keys}
        elif preset == "deny_all":
            new = {k: "deny" for k in keys}
        elif preset == "ask_all" and "ask" in vals:
            new = {k: "ask" for k in keys}
        else:
            raise ValueError(f"Unknown preset: {preset}")
        return self.set_permissions(new)
