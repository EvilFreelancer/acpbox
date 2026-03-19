"""OpenAI /v1/chat/completions via per-worker ACP runner."""

import json
import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from acpbox.acp_stdio import AcpStdioError
from acpbox.errors import openai_error_body
from acpbox.mapping import (
    acp_aggregated_text_to_chat_completion,
    acp_stop_reason_to_openai_finish,
    chat_completion_chunk_sse_dict,
    openai_messages_to_acp_prompt_blocks,
)
from acpbox.schemas import CreateChatCompletionRequest, CreateChatCompletionResponse

router = APIRouter(prefix="/v1/chat/completions", tags=["Chat"])


def _sse_data(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


@router.post("")
async def create_chat_completion(
    body: CreateChatCompletionRequest,
    request: Request,
):
    """POST /v1/chat/completions: use per-worker ACP runner, session/prompt, return chat completion or SSE stream."""
    prompt_blocks = openai_messages_to_acp_prompt_blocks(
        [m.model_dump() for m in body.messages],
        metadata=body.metadata,
    )
    if not prompt_blocks:
        raise HTTPException(
            status_code=400,
            detail=openai_error_body("messages cannot be empty", "invalid_input"),
        )
    runner = request.app.state.runner

    if body.stream:
        return await _chat_completion_stream(
            runner=runner,
            body=body,
            prompt_blocks=prompt_blocks,
        )

    try:
        text, stop_reason, acp_updates = await runner.run_turn(
            prompt_blocks=prompt_blocks,
            mode_id=body.model or None,
        )
    except AcpStdioError as e:
        raise HTTPException(
            status_code=503,
            detail=openai_error_body(str(e), "server_error"),
        ) from e
    return acp_aggregated_text_to_chat_completion(
        text,
        body.model,
        finish_reason=stop_reason,
        acp_raw=acp_updates,
    )


async def _chat_completion_stream(
    *,
    runner: Any,
    body: CreateChatCompletionRequest,
    prompt_blocks: list[Any],
) -> StreamingResponse:
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created = int(time.time())
    model = body.model

    agen = runner.run_turn_stream(
        prompt_blocks=prompt_blocks,
        mode_id=body.model or None,
    )
    aiter = agen.__aiter__()
    try:
        first_evt = await aiter.__anext__()
    except StopAsyncIteration:
        first_evt = None
    except AcpStdioError as e:
        raise HTTPException(
            status_code=503,
            detail=openai_error_body(str(e), "server_error"),
        ) from e

    async def sse_body():
        role_sent = False
        if first_evt is None:
            yield _sse_data(
                chat_completion_chunk_sse_dict(
                    completion_id=completion_id,
                    created=created,
                    model=model,
                    delta={"role": "assistant"},
                ),
            )
            yield _sse_data(
                chat_completion_chunk_sse_dict(
                    completion_id=completion_id,
                    created=created,
                    model=model,
                    delta={},
                    finish_reason="stop",
                ),
            )
            yield "data: [DONE]\n\n"
            return

        def role_chunk() -> str:
            return _sse_data(
                chat_completion_chunk_sse_dict(
                    completion_id=completion_id,
                    created=created,
                    model=model,
                    delta={"role": "assistant"},
                ),
            )

        pending = first_evt
        while pending is not None:
            kind, payload = pending
            if kind == "session" and isinstance(payload, dict):
                yield _sse_data(
                    chat_completion_chunk_sse_dict(
                        completion_id=completion_id,
                        created=created,
                        model=model,
                        delta={},
                        acp=payload,
                    ),
                )
            elif kind == "text":
                if not role_sent:
                    yield role_chunk()
                    role_sent = True
                if payload:
                    yield _sse_data(
                        chat_completion_chunk_sse_dict(
                            completion_id=completion_id,
                            created=created,
                            model=model,
                            delta={"content": payload},
                        ),
                    )
            elif kind == "done":
                if not role_sent:
                    yield role_chunk()
                    role_sent = True
                finish = acp_stop_reason_to_openai_finish(payload)
                yield _sse_data(
                    chat_completion_chunk_sse_dict(
                        completion_id=completion_id,
                        created=created,
                        model=model,
                        delta={},
                        finish_reason=finish,
                    ),
                )
                yield "data: [DONE]\n\n"
                return
            try:
                pending = await aiter.__anext__()
            except StopAsyncIteration:
                if not role_sent:
                    yield role_chunk()
                    role_sent = True
                yield _sse_data(
                    chat_completion_chunk_sse_dict(
                        completion_id=completion_id,
                        created=created,
                        model=model,
                        delta={},
                        finish_reason="stop",
                    ),
                )
                yield "data: [DONE]\n\n"
                return

    return StreamingResponse(
        sse_body(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
