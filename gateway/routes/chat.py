"""OpenAI /v1/chat/completions via per-worker ACP runner."""

from fastapi import APIRouter, HTTPException, Request

from gateway.acp_stdio import AcpStdioError
from gateway.errors import openai_error_body
from gateway.mapping import (
    acp_aggregated_text_to_chat_completion,
    openai_messages_to_acp_prompt_blocks,
)
from gateway.schemas import CreateChatCompletionRequest, CreateChatCompletionResponse

router = APIRouter(prefix="/v1/chat/completions", tags=["Chat"])


@router.post("", response_model=CreateChatCompletionResponse)
async def create_chat_completion(
    body: CreateChatCompletionRequest,
    request: Request,
) -> CreateChatCompletionResponse:
    """POST /v1/chat/completions: use per-worker ACP runner, session/prompt, return chat completion."""
    if body.stream:
        raise HTTPException(
            status_code=400,
            detail=openai_error_body("stream=true is not yet supported", "invalid_input"),
        )
    prompt_blocks = openai_messages_to_acp_prompt_blocks([m.model_dump() for m in body.messages])
    if not prompt_blocks:
        raise HTTPException(
            status_code=400,
            detail=openai_error_body("messages cannot be empty", "invalid_input"),
        )
    runner = request.app.state.runner
    try:
        text, stop_reason = await runner.run_turn(
            prompt_blocks=prompt_blocks,
            mode_id=body.model or None,
        )
    except AcpStdioError as e:
        raise HTTPException(
            status_code=503,
            detail=openai_error_body(str(e), "server_error"),
        ) from e
    return acp_aggregated_text_to_chat_completion(text, body.model, finish_reason=stop_reason)
