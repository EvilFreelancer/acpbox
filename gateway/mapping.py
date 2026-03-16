"""Convert between OpenAI and ACP message/request/response formats."""

import time
import uuid
from typing import Any

from acp import text_block
from acp.schema import TextContentBlock

from gateway.schemas import (
    ChatCompletionChoice,
    ChatCompletionChoiceMessage,
    CreateChatCompletionResponse,
    CreateResponseBody,
    ResponseOutputMessage,
    ResponseOutputMessageContent,
    ResponseUsage,
    Usage,
)


def _apply_metadata_model_prefix(text: str, metadata: dict[str, Any] | None) -> str:
    """
    Apply metadata model prefix for opencode.

    If metadata contains key "model" (for example from OpenAI metadata field),
    prepend a line "model: <value>" before the main prompt text so the ACP
    agent (opencode) can choose underlying LLM model.
    """
    if not metadata:
        return text
    model_value = metadata.get("model")
    if not model_value:
        return text
    prefix = f"model: {model_value}"
    if not text:
        return prefix
    return f"{prefix}\n\n{text}"


def openai_messages_to_acp_prompt_blocks(
    messages: list[dict[str, Any]],
    metadata: dict[str, Any] | None = None,
) -> list[TextContentBlock]:
    """
    Convert OpenAI chat messages to ACP session/prompt ContentBlock list.
    Conversation is concatenated into a single text block for the agent.
    If metadata.model is provided, it is prefixed in the first line for opencode.
    """
    parts: list[str] = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content")
        if content is None:
            continue
        if isinstance(content, str):
            parts.append(f"{role}: {content}")
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text" and part.get("text"):
                    parts.append(f"{role}: {part['text']}")
    if not parts:
        return []
    text = "\n\n".join(parts)
    text = _apply_metadata_model_prefix(text, metadata)
    block = text_block(text)
    # text_block returns a TextContentBlock instance from acp.schema
    return [block]


def openai_response_input_to_acp_prompt_blocks(
    input_value: str | list[dict[str, Any]],
    metadata: dict[str, Any] | None = None,
) -> list[TextContentBlock]:
    """Convert OpenAI Responses API input to ACP session/prompt ContentBlock list."""
    if isinstance(input_value, str):
        text = _apply_metadata_model_prefix(input_value, metadata)
        return [text_block(text)]
    parts: list[str] = []
    for item in input_value:
        if not isinstance(item, dict):
            continue
        role = item.get("role", "user")
        content = item.get("content")
        if content is None:
            continue
        if isinstance(content, str):
            parts.append(f"{role}: {content}")
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "input_text" and part.get("text"):
                    parts.append(f"{role}: {part['text']}")
    if not parts:
        return []
    text = "\n\n".join(parts)
    text = _apply_metadata_model_prefix(text, metadata)
    block = text_block(text)
    return [block]


def openai_messages_to_acp_input(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Convert OpenAI chat completion messages to ACP Run input (list of Message).
    role: system/user/assistant/developer -> user or agent; content -> parts.
    """
    acp_messages: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content")
        if content is None:
            continue
        if isinstance(content, str):
            parts = [{"content_type": "text/plain", "content": content}]
        elif isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, dict):
                    if part.get("type") == "text" and "text" in part:
                        parts.append({"content_type": "text/plain", "content": part["text"]})
                    elif part.get("type") == "image_url" and "image_url" in part:
                        url = part["image_url"]
                        if isinstance(url, dict) and "url" in url:
                            url = url["url"]
                        parts.append({"content_type": "image/url", "content_url": url})
                else:
                    continue
            if not parts:
                continue
        else:
            continue
        acp_role = "user" if role in ("user", "system", "developer") else "agent"
        acp_messages.append({"role": acp_role, "parts": parts})
    return acp_messages


def openai_response_input_to_acp_input(input_value: str | list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert OpenAI Responses API input (text or list of items) to ACP Message list."""
    if isinstance(input_value, str):
        return [{"role": "user", "parts": [{"content_type": "text/plain", "content": input_value}]}]
    acp_messages: list[dict[str, Any]] = []
    for item in input_value:
        if not isinstance(item, dict):
            continue
        role = item.get("role", "user")
        content = item.get("content")
        if content is None:
            continue
        if isinstance(content, str):
            parts = [{"content_type": "text/plain", "content": content}]
        elif isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, dict):
                    if part.get("type") == "input_text" and "text" in part:
                        parts.append({"content_type": "text/plain", "content": part["text"]})
                    elif part.get("type") == "input_image" and "image_url" in part:
                        parts.append({"content_type": "image/url", "content_url": part["image_url"]})
            if not parts:
                continue
        else:
            continue
        acp_role = "user" if role == "user" else "agent"
        acp_messages.append({"role": acp_role, "parts": parts})
    return acp_messages


def acp_aggregated_text_to_chat_completion(
    aggregated_text: str,
    model: str,
    run_id: str | None = None,
    finish_reason: str = "stop",
) -> CreateChatCompletionResponse:
    """Build OpenAI chat.completion from ACP stdio aggregated agent text."""
    choice = ChatCompletionChoice(
        index=0,
        message=ChatCompletionChoiceMessage(role="assistant", content=aggregated_text or None),
        finish_reason="stop" if finish_reason == "end_turn" else "stop",
    )
    created = int(time.time())
    return CreateChatCompletionResponse(
        id=run_id or f"chatcmpl-{uuid.uuid4().hex[:24]}",
        created=created,
        model=model,
        choices=[choice],
        usage=Usage(prompt_tokens=0, completion_tokens=0, total_tokens=0),
    )


def acp_run_output_to_chat_completion(
    run: dict[str, Any],
    model: str,
    run_id: str | None = None,
) -> CreateChatCompletionResponse:
    """Build OpenAI chat.completion response from ACP Run (legacy HTTP-style)."""
    output = run.get("output") or []
    content = ""
    for msg in output:
        if not isinstance(msg, dict):
            continue
        for part in msg.get("parts") or []:
            if isinstance(part, dict) and part.get("content_type") == "text/plain" and part.get("content"):
                content += part["content"]
    return acp_aggregated_text_to_chat_completion(content, model, run_id)


def acp_aggregated_text_to_response_body(
    aggregated_text: str,
    model: str,
    response_id: str,
    chat_id: str | None = None,
) -> CreateResponseBody:
    """Build OpenAI response object from ACP stdio aggregated agent text."""
    out_message = ResponseOutputMessage(
        id=f"msg-{uuid.uuid4().hex[:24]}",
        content=[ResponseOutputMessageContent(type="output_text", text=aggregated_text, annotations=[])],
    )
    return CreateResponseBody(
        id=response_id,
        created_at=int(time.time()),
        model=model,
        output=[out_message],
        usage=ResponseUsage(input_tokens=0, output_tokens=0, total_tokens=0),
        chat_id=chat_id,
    )


def acp_run_output_to_response_body(
    run: dict[str, Any],
    model: str,
    response_id: str,
    chat_id: str | None = None,
) -> CreateResponseBody:
    """Build OpenAI response object from ACP Run (legacy HTTP-style)."""
    output = run.get("output") or []
    text_parts: list[str] = []
    for msg in output:
        if not isinstance(msg, dict):
            continue
        for part in msg.get("parts") or []:
            if isinstance(part, dict) and part.get("content_type") == "text/plain" and part.get("content"):
                text_parts.append(part["content"])
    text = "".join(text_parts)
    return acp_aggregated_text_to_response_body(text, model, response_id, chat_id)


def new_response_id() -> str:
    """Generate OpenAI-style response id."""
    return f"resp_{uuid.uuid4().hex}"


def new_chat_id() -> str:
    """Generate UUID for chat/session (ACP session_id)."""
    return str(uuid.uuid4())
