"""Tests for /v1/agent/* permission management endpoints and agent adapters."""

import json
import logging
import os
import stat
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.testclient import TestClient

from acpbox.agents import create_adapter, detect_agent_type
from acpbox.agents.claude import ClaudeAdapter
from acpbox.agents.codex import CodexAdapter
from acpbox.agents.cursor import CursorAdapter
from acpbox.agents.opencode import OpenCodeAdapter
from acpbox.routes import agent_config

logging.getLogger("acpbox").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_app(adapter) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app_instance: FastAPI) -> AsyncGenerator[None, None]:
        yield

    app_instance = FastAPI(lifespan=lifespan)
    app_instance.state.agent_adapter = adapter

    @app_instance.exception_handler(HTTPException)
    async def exc_handler(request: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail
        if isinstance(detail, dict) and "message" in detail:
            return JSONResponse(status_code=exc.status_code, content={"error": detail})
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": "api_error", "message": str(detail)}},
        )

    app_instance.include_router(agent_config.router)
    return app_instance


def _client(adapter) -> TestClient:
    return TestClient(_make_app(adapter))


# ===================================================================
# Agent detection
# ===================================================================

class TestDetectAgentType:
    def test_opencode(self):
        assert detect_agent_type(["opencode", "acp"]) == "opencode"

    def test_claude(self):
        assert detect_agent_type(["claude-agent-acp"]) == "claude"

    def test_codex(self):
        assert detect_agent_type(["codex-acp"]) == "codex"

    def test_cursor(self):
        assert detect_agent_type(["agent", "acp"]) == "cursor"

    def test_full_path(self):
        assert detect_agent_type(["/usr/local/bin/opencode", "acp"]) == "opencode"

    def test_unknown(self):
        assert detect_agent_type(["weird-agent"]) == "unknown"

    def test_empty(self):
        assert detect_agent_type([]) == "unknown"


class TestCreateAdapter:
    def test_opencode(self):
        a = create_adapter(["opencode", "acp"], "/tmp/ws")
        assert isinstance(a, OpenCodeAdapter)
        assert a.agent_type == "opencode"

    def test_claude(self):
        a = create_adapter(["claude-agent-acp"], "/tmp/ws")
        assert isinstance(a, ClaudeAdapter)

    def test_codex(self):
        a = create_adapter(["codex-acp"], "/tmp/ws")
        assert isinstance(a, CodexAdapter)

    def test_cursor(self):
        a = create_adapter(["agent", "acp"], "/tmp/ws")
        assert isinstance(a, CursorAdapter)

    def test_full_path_binary(self):
        a = create_adapter(["/home/user/.local/bin/opencode", "acp"], "/tmp/ws")
        assert isinstance(a, OpenCodeAdapter)

    def test_unknown_returns_none(self):
        assert create_adapter(["some-weird-thing"], "/tmp/ws") is None

    def test_empty_returns_none(self):
        assert create_adapter([], "/tmp/ws") is None


# ===================================================================
# OpenCode adapter - unit
# ===================================================================

class TestOpenCodeAdapterUnit:
    def test_config_path(self, tmp_path: Path):
        a = OpenCodeAdapter(str(tmp_path))
        assert a.config_path == tmp_path / "opencode.json"

    def test_read_missing_config(self, tmp_path: Path):
        a = OpenCodeAdapter(str(tmp_path))
        assert a.read_config() == {}

    def test_write_creates_file(self, tmp_path: Path):
        a = OpenCodeAdapter(str(tmp_path))
        a.write_config({"permission": {"read": "allow"}})
        assert (tmp_path / "opencode.json").exists()
        assert json.loads((tmp_path / "opencode.json").read_text())["permission"]["read"] == "allow"

    def test_get_permissions_empty(self, tmp_path: Path):
        a = OpenCodeAdapter(str(tmp_path))
        assert a.get_permissions() == {}

    def test_set_replaces_all(self, tmp_path: Path):
        a = OpenCodeAdapter(str(tmp_path))
        a.set_permissions({"read": "allow", "edit": "deny"})
        a.set_permissions({"bash": "ask"})
        assert a.get_permissions() == {"bash": "ask"}

    def test_update_merges(self, tmp_path: Path):
        a = OpenCodeAdapter(str(tmp_path))
        a.set_permissions({"read": "allow", "edit": "deny"})
        result = a.update_permissions({"edit": "allow", "bash": "ask"})
        assert result == {"read": "allow", "edit": "allow", "bash": "ask"}

    def test_preserves_non_permission_keys(self, tmp_path: Path):
        cfg_path = tmp_path / "opencode.json"
        cfg_path.write_text(json.dumps({
            "$schema": "https://opencode.ai/config.json",
            "model": "rpa/gpt:120b",
            "provider": {"rpa": {}},
        }))
        a = OpenCodeAdapter(str(tmp_path))
        a.set_permissions({"read": "allow"})
        cfg = json.loads(cfg_path.read_text())
        assert cfg["model"] == "rpa/gpt:120b"
        assert cfg["$schema"] == "https://opencode.ai/config.json"
        assert cfg["permission"]["read"] == "allow"

    def test_known_permissions_not_empty(self, tmp_path: Path):
        a = OpenCodeAdapter(str(tmp_path))
        known = a.known_permissions()
        assert len(known) > 10
        assert "read" in known
        assert "bash" in known

    def test_allowed_values(self, tmp_path: Path):
        a = OpenCodeAdapter(str(tmp_path))
        assert set(a.allowed_values()) == {"allow", "deny", "ask"}

    def test_apply_preset_allow_all(self, tmp_path: Path):
        a = OpenCodeAdapter(str(tmp_path))
        result = a.apply_preset("allow_all")
        assert all(v == "allow" for v in result.values())
        assert "read" in result

    def test_apply_preset_deny_all(self, tmp_path: Path):
        a = OpenCodeAdapter(str(tmp_path))
        result = a.apply_preset("deny_all")
        assert all(v == "deny" for v in result.values())

    def test_apply_preset_ask_all(self, tmp_path: Path):
        a = OpenCodeAdapter(str(tmp_path))
        result = a.apply_preset("ask_all")
        assert all(v == "ask" for v in result.values())

    def test_apply_preset_unknown_raises(self, tmp_path: Path):
        a = OpenCodeAdapter(str(tmp_path))
        with pytest.raises(ValueError, match="Unknown preset"):
            a.apply_preset("nope")

    def test_wildcard_keys(self, tmp_path: Path):
        a = OpenCodeAdapter(str(tmp_path))
        a.set_permissions({"read": "deny", "atlassian_*": "allow"})
        assert a.get_permissions()["atlassian_*"] == "allow"


# ===================================================================
# Claude adapter - unit
# ===================================================================

class TestClaudeAdapterUnit:
    def test_config_path(self, tmp_path: Path):
        a = ClaudeAdapter(str(tmp_path))
        assert a.config_path == tmp_path / ".claude" / "settings.json"

    def test_empty_permissions(self, tmp_path: Path):
        a = ClaudeAdapter(str(tmp_path))
        assert a.get_permissions() == {}

    def test_set_creates_allow_deny_lists(self, tmp_path: Path):
        a = ClaudeAdapter(str(tmp_path))
        a.set_permissions({"Read": "allow", "WebFetch": "deny", "Bash": "allow"})
        cfg = json.loads(a.config_path.read_text())
        assert sorted(cfg["permissions"]["allow"]) == ["Bash", "Read"]
        assert cfg["permissions"]["deny"] == ["WebFetch"]

    def test_roundtrip(self, tmp_path: Path):
        a = ClaudeAdapter(str(tmp_path))
        original = {"Read": "allow", "WebFetch": "deny", "Bash(git *)": "allow"}
        a.set_permissions(original)
        assert a.get_permissions() == original

    def test_update_merges(self, tmp_path: Path):
        a = ClaudeAdapter(str(tmp_path))
        a.set_permissions({"Read": "allow", "Bash": "deny"})
        result = a.update_permissions({"Bash": "allow", "WebFetch": "deny"})
        assert result == {"Read": "allow", "Bash": "allow", "WebFetch": "deny"}

    def test_preserves_non_permission_keys(self, tmp_path: Path):
        cfg_dir = tmp_path / ".claude"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "settings.json").write_text(json.dumps({
            "model": "claude-4-opus",
            "customInstructions": "be nice",
        }))
        a = ClaudeAdapter(str(tmp_path))
        a.set_permissions({"Read": "allow"})
        cfg = json.loads(a.config_path.read_text())
        assert cfg["model"] == "claude-4-opus"
        assert cfg["customInstructions"] == "be nice"

    def test_allowed_values(self, tmp_path: Path):
        a = ClaudeAdapter(str(tmp_path))
        assert set(a.allowed_values()) == {"allow", "deny"}

    def test_ask_all_preset_not_supported(self, tmp_path: Path):
        a = ClaudeAdapter(str(tmp_path))
        with pytest.raises(ValueError, match="Unknown preset"):
            a.apply_preset("ask_all")


# ===================================================================
# Codex adapter - unit
# ===================================================================

class TestCodexAdapterUnit:
    def test_config_path(self, tmp_path: Path):
        a = CodexAdapter(str(tmp_path))
        assert a.config_path == tmp_path / "codex.json"

    def test_set_and_get(self, tmp_path: Path):
        a = CodexAdapter(str(tmp_path))
        a.set_permissions({"read": "allow", "bash": "deny"})
        assert a.get_permissions() == {"read": "allow", "bash": "deny"}

    def test_preset_full_auto(self, tmp_path: Path):
        a = CodexAdapter(str(tmp_path))
        result = a.apply_preset("full-auto")
        assert result == {"read": "allow", "write": "allow", "bash": "allow", "network": "allow"}
        cfg = json.loads(a.config_path.read_text())
        assert cfg["approval_mode"] == "full-auto"

    def test_preset_suggest(self, tmp_path: Path):
        a = CodexAdapter(str(tmp_path))
        result = a.apply_preset("suggest")
        assert all(v == "deny" for v in result.values())
        cfg = json.loads(a.config_path.read_text())
        assert cfg["approval_mode"] == "suggest"

    def test_preset_auto_edit(self, tmp_path: Path):
        a = CodexAdapter(str(tmp_path))
        result = a.apply_preset("auto-edit")
        assert result["read"] == "allow"
        assert result["write"] == "allow"
        assert result["bash"] == "deny"

    def test_standard_preset_also_works(self, tmp_path: Path):
        a = CodexAdapter(str(tmp_path))
        result = a.apply_preset("allow_all")
        assert all(v == "allow" for v in result.values())


# ===================================================================
# Cursor adapter - unit
# ===================================================================

class TestCursorAdapterUnit:
    def test_config_path(self, tmp_path: Path):
        a = CursorAdapter(str(tmp_path))
        assert a.config_path == tmp_path / ".cursor" / "settings.json"

    def test_set_and_get(self, tmp_path: Path):
        a = CursorAdapter(str(tmp_path))
        a.set_permissions({"read": "allow", "bash": "deny"})
        assert a.get_permissions() == {"read": "allow", "bash": "deny"}

    def test_update_merges(self, tmp_path: Path):
        a = CursorAdapter(str(tmp_path))
        a.set_permissions({"read": "allow"})
        result = a.update_permissions({"bash": "deny"})
        assert result == {"read": "allow", "bash": "deny"}


# ===================================================================
# is_writable
# ===================================================================

class TestWritability:
    def test_writable_new_file(self, tmp_path: Path):
        a = OpenCodeAdapter(str(tmp_path))
        assert a.is_writable is True

    def test_writable_existing_file(self, tmp_path: Path):
        a = OpenCodeAdapter(str(tmp_path))
        a.set_permissions({"read": "allow"})
        assert a.is_writable is True

    def test_not_writable_readonly_file(self, tmp_path: Path):
        a = OpenCodeAdapter(str(tmp_path))
        a.set_permissions({"read": "allow"})
        cfg = a.config_path
        cfg.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        try:
            assert a.is_writable is False
        finally:
            cfg.chmod(stat.S_IRUSR | stat.S_IWUSR)

    def test_not_writable_readonly_parent(self, tmp_path: Path):
        ro_dir = tmp_path / "readonly"
        ro_dir.mkdir()
        a = OpenCodeAdapter(str(ro_dir))
        ro_dir.chmod(stat.S_IRUSR | stat.S_IXUSR)
        try:
            assert a.is_writable is False
        finally:
            ro_dir.chmod(stat.S_IRWXU)

    def test_writable_nested_nonexistent_parent(self, tmp_path: Path):
        a = ClaudeAdapter(str(tmp_path))
        # .claude/ dir doesn't exist yet but tmp_path is writable
        assert a.is_writable is True


# ===================================================================
# API - OpenCode endpoints
# ===================================================================

@pytest.fixture
def oc_workspace(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def oc_client(oc_workspace: Path) -> TestClient:
    adapter = OpenCodeAdapter(str(oc_workspace))
    with _client(adapter) as c:
        yield c


class TestOpenCodeAPI:
    def test_get_info(self, oc_client: TestClient):
        r = oc_client.get("/v1/agent")
        assert r.status_code == 200
        d = r.json()
        assert d["agent_type"] == "opencode"
        assert d["writable"] is True
        assert "read" in d["known_permissions"]
        assert set(d["allowed_values"]) == {"allow", "deny", "ask"}

    def test_get_permissions_empty(self, oc_client: TestClient):
        r = oc_client.get("/v1/agent/permissions")
        assert r.status_code == 200
        d = r.json()
        assert d["agent_type"] == "opencode"
        assert d["writable"] is True
        assert d["permissions"] == {}

    def test_put_explicit(self, oc_client: TestClient):
        r = oc_client.put("/v1/agent/permissions", json={
            "permissions": {"read": "allow", "edit": "deny"},
        })
        assert r.status_code == 200
        p = r.json()["permissions"]
        assert p == {"read": "allow", "edit": "deny"}

    def test_put_preset_deny_all(self, oc_client: TestClient):
        r = oc_client.put("/v1/agent/permissions", json={"preset": "deny_all"})
        assert r.status_code == 200
        p = r.json()["permissions"]
        assert all(v == "deny" for v in p.values())
        assert "read" in p and "bash" in p

    def test_put_preset_allow_all(self, oc_client: TestClient):
        r = oc_client.put("/v1/agent/permissions", json={"preset": "allow_all"})
        p = r.json()["permissions"]
        assert all(v == "allow" for v in p.values())

    def test_put_preset_ask_all(self, oc_client: TestClient):
        r = oc_client.put("/v1/agent/permissions", json={"preset": "ask_all"})
        p = r.json()["permissions"]
        assert all(v == "ask" for v in p.values())

    def test_put_preset_plus_overrides(self, oc_client: TestClient):
        r = oc_client.put("/v1/agent/permissions", json={
            "preset": "deny_all",
            "permissions": {"read": "allow", "atlassian_*": "allow"},
        })
        p = r.json()["permissions"]
        assert p["read"] == "allow"
        assert p["atlassian_*"] == "allow"
        assert p["edit"] == "deny"
        assert p["bash"] == "deny"

    def test_patch_merges(self, oc_client: TestClient):
        oc_client.put("/v1/agent/permissions", json={"preset": "deny_all"})
        r = oc_client.patch("/v1/agent/permissions", json={
            "permissions": {"read": "allow", "edit": "allow"},
        })
        assert r.status_code == 200
        p = r.json()["permissions"]
        assert p["read"] == "allow"
        assert p["edit"] == "allow"
        assert p["bash"] == "deny"

    def test_invalid_value_rejected_put(self, oc_client: TestClient):
        r = oc_client.put("/v1/agent/permissions", json={
            "permissions": {"read": "yolo"},
        })
        assert r.status_code == 422
        assert "invalid_permission_value" in r.json()["error"]["code"]

    def test_invalid_value_rejected_patch(self, oc_client: TestClient):
        r = oc_client.patch("/v1/agent/permissions", json={
            "permissions": {"read": "nope"},
        })
        assert r.status_code == 422

    def test_empty_body_rejected(self, oc_client: TestClient):
        r = oc_client.put("/v1/agent/permissions", json={})
        assert r.status_code == 422

    def test_unknown_preset_rejected(self, oc_client: TestClient):
        r = oc_client.put("/v1/agent/permissions", json={"preset": "nope"})
        assert r.status_code == 422
        assert "invalid_preset" in r.json()["error"]["code"]

    def test_persisted_to_disk(self, oc_workspace: Path, oc_client: TestClient):
        oc_client.put("/v1/agent/permissions", json={
            "permissions": {"bash": "deny", "read": "allow"},
        })
        cfg = json.loads((oc_workspace / "opencode.json").read_text())
        assert cfg["permission"]["bash"] == "deny"
        assert cfg["permission"]["read"] == "allow"

    def test_preserves_other_config_keys(self, oc_workspace: Path, oc_client: TestClient):
        cfg_path = oc_workspace / "opencode.json"
        cfg_path.write_text(json.dumps({
            "model": "rpa/gpt:120b",
            "provider": {"rpa": {"npm": "@ai-sdk/openai-compatible"}},
            "permission": {"read": "ask"},
        }))
        oc_client.patch("/v1/agent/permissions", json={"permissions": {"edit": "allow"}})
        cfg = json.loads(cfg_path.read_text())
        assert cfg["model"] == "rpa/gpt:120b"
        assert cfg["provider"]["rpa"]["npm"] == "@ai-sdk/openai-compatible"
        assert cfg["permission"]["read"] == "ask"
        assert cfg["permission"]["edit"] == "allow"

    def test_sequential_updates(self, oc_client: TestClient):
        oc_client.put("/v1/agent/permissions", json={"preset": "deny_all"})
        oc_client.patch("/v1/agent/permissions", json={"permissions": {"read": "allow"}})
        oc_client.patch("/v1/agent/permissions", json={"permissions": {"bash": "allow"}})
        r = oc_client.get("/v1/agent/permissions")
        p = r.json()["permissions"]
        assert p["read"] == "allow"
        assert p["bash"] == "allow"
        assert p["edit"] == "deny"

    def test_put_replaces_entirely(self, oc_client: TestClient):
        oc_client.put("/v1/agent/permissions", json={
            "permissions": {"read": "allow", "edit": "allow", "bash": "allow"},
        })
        oc_client.put("/v1/agent/permissions", json={
            "permissions": {"read": "deny"},
        })
        r = oc_client.get("/v1/agent/permissions")
        p = r.json()["permissions"]
        assert p == {"read": "deny"}


# ===================================================================
# API - Claude endpoints
# ===================================================================

@pytest.fixture
def claude_client(tmp_path: Path) -> TestClient:
    adapter = ClaudeAdapter(str(tmp_path))
    with _client(adapter) as c:
        yield c


class TestClaudeAPI:
    def test_get_info(self, claude_client: TestClient):
        r = claude_client.get("/v1/agent")
        assert r.status_code == 200
        d = r.json()
        assert d["agent_type"] == "claude"
        assert d["writable"] is True

    def test_put_and_get(self, claude_client: TestClient):
        claude_client.put("/v1/agent/permissions", json={
            "permissions": {"Read": "allow", "WebFetch": "deny"},
        })
        r = claude_client.get("/v1/agent/permissions")
        p = r.json()["permissions"]
        assert p["Read"] == "allow"
        assert p["WebFetch"] == "deny"

    def test_preset_deny_all(self, claude_client: TestClient):
        r = claude_client.put("/v1/agent/permissions", json={"preset": "deny_all"})
        assert r.status_code == 200
        p = r.json()["permissions"]
        assert all(v == "deny" for v in p.values())
        assert "Read" in p

    def test_preset_allow_all(self, claude_client: TestClient):
        r = claude_client.put("/v1/agent/permissions", json={"preset": "allow_all"})
        p = r.json()["permissions"]
        assert all(v == "allow" for v in p.values())

    def test_ask_all_not_supported(self, claude_client: TestClient):
        r = claude_client.put("/v1/agent/permissions", json={"preset": "ask_all"})
        assert r.status_code == 422

    def test_pattern_permissions(self, claude_client: TestClient):
        r = claude_client.put("/v1/agent/permissions", json={
            "permissions": {"Bash(git *)": "allow", "Bash": "deny"},
        })
        p = r.json()["permissions"]
        assert p["Bash(git *)"] == "allow"
        assert p["Bash"] == "deny"

    def test_native_format_on_disk(self, tmp_path: Path):
        adapter = ClaudeAdapter(str(tmp_path))
        with _client(adapter) as c:
            c.put("/v1/agent/permissions", json={
                "permissions": {"Bash": "allow", "Read": "allow", "WebFetch": "deny"},
            })
        cfg = json.loads(adapter.config_path.read_text())
        assert sorted(cfg["permissions"]["allow"]) == ["Bash", "Read"]
        assert cfg["permissions"]["deny"] == ["WebFetch"]


# ===================================================================
# API - Codex endpoints
# ===================================================================

@pytest.fixture
def codex_client(tmp_path: Path) -> TestClient:
    adapter = CodexAdapter(str(tmp_path))
    with _client(adapter) as c:
        yield c


class TestCodexAPI:
    def test_get_info(self, codex_client: TestClient):
        r = codex_client.get("/v1/agent")
        assert r.status_code == 200
        assert r.json()["agent_type"] == "codex"

    def test_put_and_get(self, codex_client: TestClient):
        codex_client.put("/v1/agent/permissions", json={
            "permissions": {"read": "allow", "bash": "deny"},
        })
        r = codex_client.get("/v1/agent/permissions")
        assert r.json()["permissions"] == {"read": "allow", "bash": "deny"}

    def test_preset_full_auto(self, codex_client: TestClient):
        r = codex_client.put("/v1/agent/permissions", json={"preset": "full-auto"})
        assert r.status_code == 200
        p = r.json()["permissions"]
        assert all(v == "allow" for v in p.values())

    def test_preset_suggest(self, codex_client: TestClient):
        r = codex_client.put("/v1/agent/permissions", json={"preset": "suggest"})
        p = r.json()["permissions"]
        assert all(v == "deny" for v in p.values())


# ===================================================================
# API - Cursor endpoints
# ===================================================================

@pytest.fixture
def cursor_client(tmp_path: Path) -> TestClient:
    adapter = CursorAdapter(str(tmp_path))
    with _client(adapter) as c:
        yield c


class TestCursorAPI:
    def test_get_info(self, cursor_client: TestClient):
        r = cursor_client.get("/v1/agent")
        assert r.status_code == 200
        assert r.json()["agent_type"] == "cursor"

    def test_put_and_get(self, cursor_client: TestClient):
        cursor_client.put("/v1/agent/permissions", json={
            "permissions": {"read": "allow", "bash": "deny"},
        })
        r = cursor_client.get("/v1/agent/permissions")
        assert r.json()["permissions"] == {"read": "allow", "bash": "deny"}


# ===================================================================
# API - writability reflected in responses
# ===================================================================

class TestWritabilityAPI:
    def test_info_writable_true(self, oc_client: TestClient):
        d = oc_client.get("/v1/agent").json()
        assert d["writable"] is True

    def test_permissions_writable_true(self, oc_client: TestClient):
        d = oc_client.get("/v1/agent/permissions").json()
        assert d["writable"] is True

    def test_info_writable_false_readonly_dir(self, tmp_path: Path):
        ro_dir = tmp_path / "ro"
        ro_dir.mkdir()
        adapter = OpenCodeAdapter(str(ro_dir))
        ro_dir.chmod(stat.S_IRUSR | stat.S_IXUSR)
        try:
            with _client(adapter) as c:
                d = c.get("/v1/agent").json()
                assert d["writable"] is False
        finally:
            ro_dir.chmod(stat.S_IRWXU)

    def test_put_blocked_readonly_dir(self, tmp_path: Path):
        ro_dir = tmp_path / "ro"
        ro_dir.mkdir()
        adapter = OpenCodeAdapter(str(ro_dir))
        ro_dir.chmod(stat.S_IRUSR | stat.S_IXUSR)
        try:
            with _client(adapter) as c:
                r = c.put("/v1/agent/permissions", json={"preset": "allow_all"})
                assert r.status_code == 403
                assert "config_not_writable" in r.json()["error"]["code"]
        finally:
            ro_dir.chmod(stat.S_IRWXU)

    def test_patch_blocked_readonly_file(self, tmp_path: Path):
        adapter = OpenCodeAdapter(str(tmp_path))
        adapter.set_permissions({"read": "allow"})
        adapter.config_path.chmod(stat.S_IRUSR)
        try:
            with _client(adapter) as c:
                r = c.patch("/v1/agent/permissions", json={"permissions": {"bash": "deny"}})
                assert r.status_code == 403
        finally:
            adapter.config_path.chmod(stat.S_IRUSR | stat.S_IWUSR)

    def test_get_permissions_shows_writable_false(self, tmp_path: Path):
        adapter = OpenCodeAdapter(str(tmp_path))
        adapter.set_permissions({"read": "allow"})
        adapter.config_path.chmod(stat.S_IRUSR)
        try:
            with _client(adapter) as c:
                r = c.get("/v1/agent/permissions")
                assert r.status_code == 200
                d = r.json()
                assert d["writable"] is False
                assert d["permissions"] == {"read": "allow"}
        finally:
            adapter.config_path.chmod(stat.S_IRUSR | stat.S_IWUSR)


# ===================================================================
# API - no adapter (unknown agent)
# ===================================================================

class TestNoAdapterAPI:
    def _make_no_adapter_client(self) -> TestClient:
        app = _make_app(None)
        return TestClient(app)

    def test_info_501(self):
        with self._make_no_adapter_client() as c:
            r = c.get("/v1/agent")
            assert r.status_code == 501

    def test_get_permissions_501(self):
        with self._make_no_adapter_client() as c:
            r = c.get("/v1/agent/permissions")
            assert r.status_code == 501

    def test_put_permissions_501(self):
        with self._make_no_adapter_client() as c:
            r = c.put("/v1/agent/permissions", json={"preset": "allow_all"})
            assert r.status_code == 501

    def test_patch_permissions_501(self):
        with self._make_no_adapter_client() as c:
            r = c.patch("/v1/agent/permissions", json={"permissions": {"read": "allow"}})
            assert r.status_code == 501
