"""Convert between OpenAI and ACP message/request/response formats."""

import time
import uuid
from typing import Any

from acp import text_block
from acp.schema import TextContentBlock

from acpbox.schemas import (
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


def acp_stop_reason_to_openai_finish(stop_reason: str) -> str:
    """Map ACP session/prompt stopReason to OpenAI chat finish_reason (chunk field)."""
    if stop_reason in ("end_turn",):
        return "stop"
    return "stop"


def summarize_acp_session_for_non_stream(
    raw: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    """
    Collapse raw ACP session/update trace from run_turn into ordered steps for JSON responses
    (stream=false only): merged reasoning segments and one entry per tool call (final fields).
    Omits streaming-style noise (usage_update, mode updates, etc.).
    """
    if not raw:
        return None
    steps: list[dict[str, Any]] = []
    reasoning_buf: list[str] = []
    command_index: dict[str, int] = {}
    ignored = frozenset(
        {
            "usage_update",
            "config_option_update",
            "current_mode_update",
            "available_commands_update",
            "session_info_update",
            "plan",
            "user_message_chunk",
            "agent_message_chunk",
        },
    )

    def flush_reasoning() -> None:
        if not reasoning_buf:
            return
        text = "".join(reasoning_buf).strip()
        reasoning_buf.clear()
        if text:
            steps.append({"type": "reasoning", "text": text})

    def ensure_command(tool_call_id: str, seed: dict[str, Any]) -> int:
        if tool_call_id not in command_index:
            steps.append(
                {
                    "type": "command",
                    "tool_call_id": tool_call_id,
                    "title": seed.get("title"),
                    "kind": seed.get("kind"),
                    "status": seed.get("status"),
                    "command": None,
                    "description": None,
                    "output": None,
                    "exit_code": None,
                },
            )
            command_index[tool_call_id] = len(steps) - 1
        return command_index[tool_call_id]

    def merge_into_command(idx: int, update: dict[str, Any]) -> None:
        st = steps[idx]
        ri = update.get("rawInput")
        if isinstance(ri, dict):
            if ri.get("command"):
                st["command"] = ri["command"]
            if ri.get("description"):
                st["description"] = ri["description"]
        if update.get("title"):
            st["title"] = update["title"]
        if update.get("kind"):
            st["kind"] = update["kind"]
        if update.get("status"):
            st["status"] = update["status"]
        ro = update.get("rawOutput")
        if isinstance(ro, dict):
            out = ro.get("output")
            md = ro.get("metadata")
            if isinstance(md, dict):
                if out is None and md.get("output") is not None:
                    out = md.get("output")
                if "exit" in md:
                    st["exit_code"] = md.get("exit")
            if out is not None:
                st["output"] = out
        for block in update.get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "content":
                continue
            inner = block.get("content") or {}
            if isinstance(inner, dict) and inner.get("type") == "text":
                piece = inner.get("text") or ""
                if piece:
                    prev = st.get("output") or ""
                    st["output"] = prev + piece

    for item in raw:
        if not isinstance(item, dict):
            continue
        update = item.get("update")
        if not isinstance(update, dict):
            continue
        sut = update.get("sessionUpdate")
        if sut == "agent_thought_chunk":
            content = update.get("content") or {}
            if isinstance(content, dict) and content.get("type") == "text":
                reasoning_buf.append(content.get("text") or "")
            continue
        if sut in ignored:
            continue
        flush_reasoning()
        if sut == "tool_call":
            tid = update.get("toolCallId")
            if isinstance(tid, str) and tid:
                idx = ensure_command(tid, update)
                merge_into_command(idx, update)
            continue
        if sut == "tool_call_update":
            tid = update.get("toolCallId")
            if isinstance(tid, str) and tid:
                idx = ensure_command(tid, update)
                merge_into_command(idx, update)
            continue

    flush_reasoning()
    if not steps:
        return None
    return {"steps": steps}


def acp_aggregated_text_to_chat_completion(
    aggregated_text: str,
    model: str,
    run_id: str | None = None,
    finish_reason: str = "stop",
    acp_raw: list[dict[str, Any]] | None = None,
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
        acp=summarize_acp_session_for_non_stream(acp_raw),
    )


def chat_completion_chunk_sse_dict(
    *,
    completion_id: str,
    created: int,
    model: str,
    delta: dict[str, Any],
    finish_reason: str | None = None,
    acp: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One OpenAI chat.completion.chunk object (serialized to JSON for SSE data: lines)."""
    choice: dict[str, Any] = {"index": 0, "delta": delta}
    if finish_reason is not None:
        choice["finish_reason"] = finish_reason
    else:
        choice["finish_reason"] = None
    out: dict[str, Any] = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [choice],
    }
    if acp is not None:
        out["acp"] = acp
    return out


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
    acp_raw: list[dict[str, Any]] | None = None,
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
        acp=summarize_acp_session_for_non_stream(acp_raw),
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
