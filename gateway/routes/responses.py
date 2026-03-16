"""OpenAI /v1/responses (stateful) and /v1/sessions via per-worker ACP runner."""

from fastapi import APIRouter, HTTPException, Request

from gateway.acp_stdio import AcpStdioError
from gateway.errors import openai_error_body
from gateway.mapping import (
    acp_aggregated_text_to_response_body,
    new_response_id,
    openai_response_input_to_acp_prompt_blocks,
)
from gateway.schemas import (
    CreateResponseBody,
    CreateResponseRequest,
    DeletedResponse,
)
from gateway.session_store import (
    chat_id_or_new,
    delete_response,
    delete_session,
    register_response,
)

router = APIRouter(tags=["Responses"])
router_responses = APIRouter(prefix="/v1/responses", tags=["Responses"])
router_sessions = APIRouter(prefix="/v1/sessions", tags=["Sessions"])


@router_responses.post("", response_model=CreateResponseBody)
async def create_response(body: CreateResponseRequest, request: Request) -> CreateResponseBody:
    """POST /v1/responses: use per-worker ACP runner, session/prompt, return response."""
    chat_id = chat_id_or_new(body.chat_id)
    if isinstance(body.input, str):
        input_payload: str | list = body.input
    else:
        input_payload = [x.model_dump() for x in body.input]
    prompt_blocks = openai_response_input_to_acp_prompt_blocks(
        input_payload,
        metadata=body.metadata,
    )
    if not prompt_blocks:
        raise HTTPException(
            status_code=400,
            detail=openai_error_body("input cannot be empty", "invalid_input"),
        )
    runner = request.app.state.runner
    try:
        text, _stop_reason = await runner.run_turn(
            prompt_blocks=prompt_blocks,
            mode_id=body.model or None,
        )
    except AcpStdioError as e:
        raise HTTPException(
            status_code=503,
            detail=openai_error_body(str(e), "server_error"),
        ) from e
    response_id = new_response_id()
    register_response(response_id, chat_id)
    return acp_aggregated_text_to_response_body(text, body.model, response_id, chat_id)


@router_responses.get("/{response_id}")
async def get_response(response_id: str) -> None:
    """GET /v1/responses/{response_id} not implemented."""
    raise HTTPException(
        status_code=501,
        detail=openai_error_body(
            "GET /v1/responses/{id} not implemented (responses are not stored)",
            "server_error",
        ),
    )


@router_responses.delete("/{response_id}", response_model=DeletedResponse)
async def delete_response_endpoint(response_id: str) -> DeletedResponse:
    """DELETE /v1/responses/{response_id}. Remove from session store."""
    found = delete_response(response_id)
    if not found:
        raise HTTPException(
            status_code=404,
            detail=openai_error_body("Response not found", "not_found"),
        )
    return DeletedResponse(id=response_id)


@router_sessions.delete("/{chat_id}")
async def delete_session_endpoint(chat_id: str) -> dict[str, str | bool]:
    """DELETE /v1/sessions/{chat_id}. Extension: remove entire session."""
    found = delete_session(chat_id)
    if not found:
        raise HTTPException(
            status_code=404,
            detail=openai_error_body("Session not found", "not_found"),
        )
    return {"id": chat_id, "object": "session", "deleted": True}
