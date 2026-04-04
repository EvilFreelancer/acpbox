"""Agent config adapters - detect agent type from ACP command and manage its config."""

from .base import AgentConfigAdapter
from .claude import ClaudeAdapter
from .codex import CodexAdapter
from .cursor import CursorAdapter
from .opencode import OpenCodeAdapter

AGENT_COMMAND_MAP: dict[str, type[AgentConfigAdapter]] = {
    "opencode": OpenCodeAdapter,
    "claude-agent-acp": ClaudeAdapter,
    "codex-acp": CodexAdapter,
    "agent": CursorAdapter,
}


def detect_agent_type(command: list[str]) -> str:
    """Infer agent type from the ACP command list."""
    if not command:
        return "unknown"
    binary = command[0].rsplit("/", 1)[-1]
    for key in AGENT_COMMAND_MAP:
        if binary == key:
            return AGENT_COMMAND_MAP[key].agent_type
    return "unknown"


def create_adapter(command: list[str], workspace: str) -> AgentConfigAdapter | None:
    """Create the right adapter for the configured ACP command. Returns None if unknown."""
    if not command:
        return None
    binary = command[0].rsplit("/", 1)[-1]
    cls = AGENT_COMMAND_MAP.get(binary)
    if cls is None:
        return None
    return cls(workspace)


__all__ = [
    "AgentConfigAdapter",
    "ClaudeAdapter",
    "CodexAdapter",
    "CursorAdapter",
    "OpenCodeAdapter",
    "create_adapter",
    "detect_agent_type",
]
