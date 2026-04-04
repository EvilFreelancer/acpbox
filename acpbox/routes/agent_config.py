"""API routes for managing agent permissions at runtime."""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from acpbox.agents import AgentConfigAdapter

router = APIRouter(prefix="/v1/agent", tags=["Agent Config"])
logger = logging.getLogger(__name__)


class AgentInfoResponse(BaseModel):
    agent_type: str
    config_path: str
    writable: bool
    known_permissions: list[str]
    allowed_values: list[str]


class PermissionsResponse(BaseModel):
    agent_type: str
    writable: bool
    permissions: dict[str, str]


class SetPermissionsRequest(BaseModel):
    preset: str | None = Field(
        default=None,
        description="Named preset applied before explicit permissions (allow_all, deny_all, ask_all).",
    )
    permissions: dict[str, str] | None = Field(
        default=None,
        description="Explicit permission map. Merged on top of preset when both are given.",
    )


class UpdatePermissionsRequest(BaseModel):
    permissions: dict[str, str] = Field(
        description="Permissions to merge into the current state.",
    )


def _adapter(request: Request) -> AgentConfigAdapter:
    adapter: AgentConfigAdapter | None = getattr(request.app.state, "agent_adapter", None)
    if adapter is None:
        raise HTTPException(
            status_code=501,
            detail={
                "code": "agent_not_supported",
                "message": "Agent type is not recognised or config management is not supported for the current ACP command.",
            },
        )
    return adapter


def _require_writable(adapter: AgentConfigAdapter) -> None:
    if not adapter.is_writable:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "config_not_writable",
                "message": f"Config file is not writable: {adapter.config_path}",
            },
        )


def _validate_values(adapter: AgentConfigAdapter, permissions: dict[str, str]) -> None:
    allowed = set(adapter.allowed_values())
    bad = {k: v for k, v in permissions.items() if v not in allowed}
    if bad:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_permission_value",
                "message": f"Invalid values: {bad}. Allowed: {sorted(allowed)}.",
            },
        )


@router.get("", response_model=AgentInfoResponse)
async def get_agent_info(request: Request) -> AgentInfoResponse:
    """Agent type, config path, and available permission names/values."""
    adapter = _adapter(request)
    return AgentInfoResponse(
        agent_type=adapter.agent_type,
        config_path=str(adapter.config_path),
        writable=adapter.is_writable,
        known_permissions=adapter.known_permissions(),
        allowed_values=adapter.allowed_values(),
    )


@router.get("/permissions", response_model=PermissionsResponse)
async def get_permissions(request: Request) -> PermissionsResponse:
    """Current permissions from the agent config file."""
    adapter = _adapter(request)
    try:
        perms = adapter.get_permissions()
    except Exception as exc:
        logger.error("Failed to read agent permissions: %s", exc)
        raise HTTPException(status_code=500, detail={"code": "config_read_error", "message": str(exc)}) from exc
    return PermissionsResponse(agent_type=adapter.agent_type, writable=adapter.is_writable, permissions=perms)


@router.put("/permissions", response_model=PermissionsResponse)
async def set_permissions(body: SetPermissionsRequest, request: Request) -> PermissionsResponse:
    """
    Replace agent permissions.

    If ``preset`` is given it is applied first (allow_all / deny_all / ask_all).
    ``permissions`` (if any) are then merged on top so you can do things like
    "deny everything except atlassian_*".
    """
    adapter = _adapter(request)
    _require_writable(adapter)

    if body.preset is None and body.permissions is None:
        raise HTTPException(
            status_code=422,
            detail={"code": "empty_request", "message": "Provide preset and/or permissions."},
        )

    try:
        if body.preset:
            adapter.apply_preset(body.preset)

        if body.permissions:
            _validate_values(adapter, body.permissions)
            if body.preset:
                adapter.update_permissions(body.permissions)
            else:
                adapter.set_permissions(body.permissions)

        perms = adapter.get_permissions()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "invalid_preset", "message": str(exc)}) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to set agent permissions: %s", exc)
        raise HTTPException(status_code=500, detail={"code": "config_write_error", "message": str(exc)}) from exc

    return PermissionsResponse(agent_type=adapter.agent_type, writable=adapter.is_writable, permissions=perms)


@router.patch("/permissions", response_model=PermissionsResponse)
async def update_permissions(body: UpdatePermissionsRequest, request: Request) -> PermissionsResponse:
    """Merge permissions into the current state (partial update)."""
    adapter = _adapter(request)
    _require_writable(adapter)
    _validate_values(adapter, body.permissions)

    try:
        perms = adapter.update_permissions(body.permissions)
    except Exception as exc:
        logger.error("Failed to update agent permissions: %s", exc)
        raise HTTPException(status_code=500, detail={"code": "config_write_error", "message": str(exc)}) from exc

    return PermissionsResponse(agent_type=adapter.agent_type, writable=adapter.is_writable, permissions=perms)
