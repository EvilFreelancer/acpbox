"""OpenAI /v1/models -> ACP /agents."""

import time

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request

from gateway.errors import acp_code_to_http_status, openai_error_body
from gateway.schemas import ListModelsResponse, ModelObject

router = APIRouter(prefix="/v1/models", tags=["Models"])


def get_client(request: Request) -> httpx.AsyncClient:
    """Get httpx client from app state."""
    return request.app.state.acp_client


@router.get("", response_model=ListModelsResponse)
async def list_models(
    request: Request,
    client: httpx.AsyncClient = Depends(get_client),
) -> ListModelsResponse:
    """GET /v1/models -> GET /agents, map agents to OpenAI model list."""
    base_url = request.app.state.acp_base_url
    try:
        r = await client.get(f"{base_url}/agents")
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=openai_error_body(str(e), "server_error"),
        ) from e
    if r.status_code != 200:
        body = r.json() if r.content else {}
        code = body.get("code", "server_error")
        message = body.get("message", r.text or "ACP error")
        raise HTTPException(
            status_code=acp_code_to_http_status(code),
            detail=openai_error_body(message, code),
        )
    data = r.json()
    agents = data.get("agents") or []
    created = int(time.time())
    return ListModelsResponse(
        data=[
            ModelObject(
                id=ag.get("name", "unknown"),
                created=created,
                owned_by="acp",
            )
            for ag in agents
        ],
    )


@router.get("/{model_id}", response_model=ModelObject)
async def get_model(
    model_id: str,
    request: Request,
    client: httpx.AsyncClient = Depends(get_client),
) -> ModelObject:
    """GET /v1/models/{model} -> GET /agents/{name}."""
    base_url = request.app.state.acp_base_url
    try:
        r = await client.get(f"{base_url}/agents/{model_id}")
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=openai_error_body(str(e), "server_error"),
        ) from e
    if r.status_code == 404:
        raise HTTPException(
            status_code=404,
            detail=openai_error_body("The model does not exist.", "not_found"),
        )
    if r.status_code != 200:
        body = r.json() if r.content else {}
        code = body.get("code", "server_error")
        message = body.get("message", r.text or "ACP error")
        raise HTTPException(
            status_code=acp_code_to_http_status(code),
            detail=openai_error_body(message, code),
        )
    ag = r.json()
    return ModelObject(
        id=ag.get("name", model_id),
        created=int(time.time()),
        owned_by="acp",
    )
