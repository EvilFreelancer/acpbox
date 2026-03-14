"""OpenAI /v1/chat/completions (stateless) -> ACP POST /runs without session."""

from fastapi import APIRouter, Depends, HTTPException, Request

import httpx

from gateway.errors import acp_code_to_http_status, openai_error_body
from gateway.mapping import (
    acp_run_output_to_chat_completion,
    openai_messages_to_acp_input,
)
from gateway.schemas import CreateChatCompletionRequest, CreateChatCompletionResponse

router = APIRouter(prefix="/v1/chat/completions", tags=["Chat"])


def get_client(request: Request) -> httpx.AsyncClient:
    return request.app.state.acp_client


@router.post("", response_model=CreateChatCompletionResponse)
async def create_chat_completion(
    body: CreateChatCompletionRequest,
    request: Request,
    client: httpx.AsyncClient = Depends(get_client),
) -> CreateChatCompletionResponse:
    """POST /v1/chat/completions -> POST /runs (no session_id). Stateless."""
    base_url = request.app.state.acp_base_url
    if body.stream:
        raise HTTPException(
            status_code=400,
            detail=openai_error_body("stream=true is not yet supported", "invalid_input"),
        )
    acp_input = openai_messages_to_acp_input([m.model_dump() for m in body.messages])
    if not acp_input:
        raise HTTPException(
            status_code=400,
            detail=openai_error_body("messages cannot be empty", "invalid_input"),
        )
    payload = {
        "agent_name": body.model,
        "input": acp_input,
        "mode": "sync",
    }
    try:
        r = await client.post(f"{base_url}/runs", json=payload)
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=openai_error_body(str(e), "server_error"),
        ) from e
    if r.status_code not in (200, 202):
        resp_body = r.json() if r.content else {}
        code = resp_body.get("code", "server_error")
        message = resp_body.get("message", r.text or "ACP error")
        raise HTTPException(
            status_code=acp_code_to_http_status(code),
            detail=openai_error_body(message, code),
        )
    run = r.json()
    return acp_run_output_to_chat_completion(run, body.model)
