"""Convert between OpenAI and ACP message/request/response formats."""

import json
import re
import time
import uuid
from typing import Any, Literal

from acp.schema import TextContentBlock

from acpbox.schemas import (
    ChatCompletionChoice,
    ChatCompletionChoiceMessage,
    ChatCompletionMessageToolCall,
    CreateChatCompletionResponse,
    CreateResponseBody,
    ResponseOutputMessage,
    ResponseOutputMessageContent,
    ResponseUsage,
    Usage,
)

# gpt-oss (Harmony) may leak channel markers into tool titles, e.g.
# "atlassian_confluence_search<|channel|>commentary". Strip for API summaries.
_HARMONY_TOOL_TITLE_SUFFIX = re.compile(r"<\|channel\|>.*$", re.DOTALL)

TOOL_CALL_OPEN_TAG = "<acpbox_tool_call>"
TOOL_CALL_CLOSE_TAG = "</acpbox_tool_call>"

_PARTIAL_NUMBER_TAIL_RE = re.compile(r"^(?:-|\.|[eE][+-]?)$")
_PARTIAL_UNICODE_ESCAPE_RE = re.compile(r"^u[0-9a-fA-F]{0,3}$")


def sanitize_acp_tool_title(title: str | None) -> str | None:
    """Remove Harmony <|channel|>... suffix from ACP tool titles when present."""
    if not title or not isinstance(title, str):
        return title
    cleaned = _HARMONY_TOOL_TITLE_SUFFIX.sub("", title).strip()
    return cleaned if cleaned else title


def has_active_tools(
    tools: list[dict[str, Any]] | None,
    tool_choice: str | dict[str, Any] | None,
) -> bool:
    """Return True when external client tools are declared and not disabled via tool_choice='none'."""
    return bool(tools) and tool_choice != "none"


def resolve_allowed_tool_names(
    tools: list[dict[str, Any]] | None,
    tool_choice: str | dict[str, Any] | None,
) -> set[str] | None:
    """Resolve the set of allowed tool names for a request, or None when tool bridging is inactive."""
    if not has_active_tools(tools, tool_choice):
        return None
    declared: set[str] = set()
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        fn = tool.get("function")
        if isinstance(fn, dict):
            name = fn.get("name")
            if isinstance(name, str) and name:
                declared.add(name)
    if not declared:
        return None
    if isinstance(tool_choice, dict):
        fn = tool_choice.get("function")
        forced_name = fn.get("name") if isinstance(fn, dict) else None
        if isinstance(forced_name, str) and forced_name:
            return {forced_name} if forced_name in declared else set()
    return declared


def _format_tool_choice_policy(tool_choice: str | dict[str, Any] | None) -> str:
    """Describe the OpenAI tool_choice policy in clear natural language."""
    if isinstance(tool_choice, dict):
        fn = tool_choice.get("function")
        forced_name = fn.get("name") if isinstance(fn, dict) else None
        if isinstance(forced_name, str) and forced_name:
            return f'You MUST call the external tool "{forced_name}" before answering.'
    if tool_choice == "required":
        return "You MUST call at least one of the external tools below before answering."
    return (
        "You may call one or more external tools when needed to fulfill the user's request, "
        "or answer directly if no tool is needed."
    )


def _format_tool_bridge_instructions(
    tools: list[dict[str, Any]],
    tool_choice: str | dict[str, Any] | None,
) -> str:
    """Build the prompt header instructing the ACP agent how to invoke OpenAI client tools."""
    compact_tools: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        fn = tool.get("function")
        if not isinstance(fn, dict) or not fn.get("name"):
            continue
        entry: dict[str, Any] = {"name": fn["name"]}
        if fn.get("description") is not None:
            entry["description"] = fn["description"]
        if fn.get("parameters") is not None:
            entry["parameters"] = fn["parameters"]
        compact_tools.append(entry)

    policy = _format_tool_choice_policy(tool_choice)
    tools_json = json.dumps(compact_tools, ensure_ascii=False, separators=(",", ":"))
    return (
        "[OpenAI Tool Bridge Instructions]\n"
        "You are serving a LIVE end-user request for an external application through an OpenAI-compatible "
        "API gateway. This is NOT a pasted prompt test or a local workspace debugging request.\n"
        "Follow the application's system instructions directly. Do NOT use your own local filesystem, shell, "
        "web, or MCP coding tools to answer the user.\n"
        f"Tool policy: {policy}\n"
        "To invoke one or more external tools, output one block per call using the EXACT format below with "
        "NO markdown code fences:\n"
        f'{TOOL_CALL_OPEN_TAG}{{"name": "<tool_name>", "arguments": {{<JSON object>}}}}{TOOL_CALL_CLOSE_TAG}\n'
        "After emitting tool call block(s), STOP immediately and wait for the calling application to execute "
        "the tool(s) and provide the results in the next turn. Never fabricate or guess tool results.\n"
        f"<available_tools>\n{tools_json}\n</available_tools>"
    )


def _build_tool_calls_from_parsed(
    parsed: Any,
    allowed_tool_names: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Convert parsed JSON (dict or list of dicts) into validated OpenAI tool_call dicts."""
    if isinstance(parsed, dict):
        items = [parsed]
    elif isinstance(parsed, list):
        items = [item for item in parsed if isinstance(item, dict)]
    else:
        return []

    calls: list[dict[str, Any]] = []
    for item in items:
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        if allowed_tool_names is not None and name not in allowed_tool_names:
            continue
        args = item.get("arguments")
        if args is None:
            arguments_str = "{}"
        elif isinstance(args, str):
            if not args.strip():
                arguments_str = "{}"
            else:
                try:
                    json.loads(args)
                except json.JSONDecodeError:
                    continue
                arguments_str = args
        else:
            arguments_str = json.dumps(args, ensure_ascii=False)
        calls.append(
            {
                "id": f"call_{uuid.uuid4().hex[:24]}",
                "type": "function",
                "function": {"name": name, "arguments": arguments_str},
            },
        )
    return calls


def _is_possibly_incomplete_json(text: str, err: json.JSONDecodeError) -> bool:
    """Return True when a JSONDecodeError may simply reflect a truncated streaming prefix."""
    if err.pos >= len(text):
        return True
    if err.msg.startswith("Unterminated string"):
        return True
    tail = text[err.pos :]
    if err.msg.startswith("Invalid \\uXXXX escape") and _PARTIAL_UNICODE_ESCAPE_RE.match(tail):
        return True
    if tail in ("t", "tr", "tru", "f", "fa", "fal", "fals", "n", "nu", "nul"):
        return True
    if _PARTIAL_NUMBER_TAIL_RE.match(tail):
        return True
    return False


def _longest_tag_prefix_suffix(text: str, tag: str) -> int:
    """Return length of the longest suffix of text that is a strict prefix of tag."""
    max_len = min(len(text), len(tag) - 1)
    for length in range(max_len, 0, -1):
        if text.endswith(tag[:length]):
            return length
    return 0


def extract_tool_calls_from_text(
    text: str,
    allowed_tool_names: set[str] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Extract valid <acpbox_tool_call> blocks from text and return (remaining_text, tool_calls)."""
    if not text or TOOL_CALL_OPEN_TAG not in text:
        return (text, [])

    decoder = json.JSONDecoder()
    marker = f"\x00ACPBOX_REMOVED_{uuid.uuid4().hex}\x00"
    out_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    pos = 0
    text_len = len(text)

    while pos < text_len:
        open_idx = text.find(TOOL_CALL_OPEN_TAG, pos)
        if open_idx == -1:
            out_parts.append(text[pos:])
            break

        after_open = open_idx + len(TOOL_CALL_OPEN_TAG)
        ws_end = after_open
        while ws_end < text_len and text[ws_end].isspace():
            ws_end += 1

        if ws_end >= text_len or text[ws_end] not in "{[":
            out_parts.append(text[pos:after_open])
            pos = after_open
            continue

        try:
            parsed, json_end = decoder.raw_decode(text, ws_end)
        except json.JSONDecodeError:
            out_parts.append(text[pos:after_open])
            pos = after_open
            continue

        close_start = json_end
        while close_start < text_len and text[close_start].isspace():
            close_start += 1

        if not text.startswith(TOOL_CALL_CLOSE_TAG, close_start):
            out_parts.append(text[pos:after_open])
            pos = after_open
            continue

        parsed_calls = _build_tool_calls_from_parsed(parsed, allowed_tool_names)
        if not parsed_calls:
            out_parts.append(text[pos:after_open])
            pos = after_open
            continue

        out_parts.append(text[pos:open_idx])
        out_parts.append(marker)
        tool_calls.extend(parsed_calls)
        pos = close_start + len(TOOL_CALL_CLOSE_TAG)

    if not tool_calls:
        return (text, [])

    remaining = "".join(out_parts)
    fence_with_markers = re.compile(
        rf"```[a-zA-Z0-9_-]*[ \t]*\r?\n(?:[\s]*{re.escape(marker)})+[\s]*```",
    )
    remaining = fence_with_markers.sub("", remaining)
    remaining = remaining.replace(marker, "")
    remaining = re.sub(r"\n{3,}", "\n\n", remaining).strip()
    return (remaining, tool_calls)


class StreamingToolCallParser:
    """Incremental parser that separates streamed plain text from <acpbox_tool_call> blocks."""

    def __init__(self, allowed_tool_names: set[str] | None = None) -> None:
        self._allowed_tool_names = allowed_tool_names
        self._in_tag = False
        self._buf = ""
        self._after_tool_call = False
        self._held_ws = ""
        self._decoder = json.JSONDecoder()

    def _emit_text(
        self,
        chunk: str,
        events: list[tuple[Literal["text", "tool_call"], Any]],
    ) -> None:
        if not chunk:
            return
        if self._after_tool_call:
            if chunk.isspace():
                self._held_ws += chunk
                return
            combined = self._held_ws + chunk
            self._held_ws = ""
            self._after_tool_call = False
            events.append(("text", combined))
            return
        events.append(("text", chunk))

    def _emit_tool_calls(
        self,
        calls: list[dict[str, Any]],
        events: list[tuple[Literal["text", "tool_call"], Any]],
    ) -> None:
        self._held_ws = ""
        self._after_tool_call = True
        for tc in calls:
            events.append(("tool_call", tc))

    def feed(self, chunk: str) -> list[tuple[Literal["text", "tool_call"], Any]]:
        """Process one streamed text chunk and return emitted ('text'|'tool_call', value) events."""
        if not chunk:
            return []
        self._buf += chunk
        events: list[tuple[Literal["text", "tool_call"], Any]] = []

        while self._buf:
            if not self._in_tag:
                open_idx = self._buf.find(TOOL_CALL_OPEN_TAG)
                if open_idx != -1:
                    if open_idx > 0:
                        self._emit_text(self._buf[:open_idx], events)
                    self._buf = self._buf[open_idx + len(TOOL_CALL_OPEN_TAG) :]
                    self._in_tag = True
                    continue

                keep = _longest_tag_prefix_suffix(self._buf, TOOL_CALL_OPEN_TAG)
                emit_len = len(self._buf) - keep
                if emit_len > 0:
                    self._emit_text(self._buf[:emit_len], events)
                    self._buf = self._buf[emit_len:]
                break

            buf_len = len(self._buf)
            ws_end = 0
            while ws_end < buf_len and self._buf[ws_end].isspace():
                ws_end += 1

            if ws_end == buf_len:
                break

            if self._buf[ws_end] not in "{[":
                self._emit_text(TOOL_CALL_OPEN_TAG, events)
                self._in_tag = False
                continue

            try:
                parsed, json_end = self._decoder.raw_decode(self._buf, ws_end)
            except json.JSONDecodeError as err:
                if _is_possibly_incomplete_json(self._buf, err):
                    break
                close_idx = self._buf.find(TOOL_CALL_CLOSE_TAG)
                next_open_idx = self._buf.find(TOOL_CALL_OPEN_TAG)
                if close_idx != -1 and (next_open_idx == -1 or close_idx < next_open_idx):
                    self._buf = self._buf[close_idx + len(TOOL_CALL_CLOSE_TAG) :]
                    self._in_tag = False
                    self._held_ws = ""
                    self._after_tool_call = True
                    continue
                if next_open_idx != -1:
                    self._emit_text(TOOL_CALL_OPEN_TAG, events)
                    self._in_tag = False
                    continue
                break

            close_start = json_end
            while close_start < buf_len and self._buf[close_start].isspace():
                close_start += 1

            rem = self._buf[close_start:]
            if rem.startswith(TOOL_CALL_CLOSE_TAG):
                self._buf = self._buf[close_start + len(TOOL_CALL_CLOSE_TAG) :]
                self._in_tag = False
                calls = _build_tool_calls_from_parsed(parsed, self._allowed_tool_names)
                if calls:
                    self._emit_tool_calls(calls, events)
                else:
                    self._held_ws = ""
                    self._after_tool_call = True
                continue

            if TOOL_CALL_CLOSE_TAG.startswith(rem):
                break

            self._emit_text(TOOL_CALL_OPEN_TAG, events)
            self._in_tag = False

        return events

    def finish(self) -> list[tuple[Literal["text", "tool_call"], Any]]:
        """Flush any remaining buffered text at stream end without dropping unclosed tags."""
        events: list[tuple[Literal["text", "tool_call"], Any]] = []
        if self._in_tag:
            self._emit_text(TOOL_CALL_OPEN_TAG + self._buf, events)
        elif self._buf:
            self._emit_text(self._buf, events)
        self._buf = ""
        self._in_tag = False
        self._held_ws = ""
        self._after_tool_call = False
        return events


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
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
) -> list[TextContentBlock]:
    """
    Convert OpenAI chat messages to ACP session/prompt ContentBlock list.
    Conversation is concatenated into a single text block for the agent.
    If tools are active, OpenAI tool bridge instructions are prepended.
    If metadata.model is provided, it is prefixed in the first line for opencode.
    """
    parts: list[str] = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content")
        tool_calls = m.get("tool_calls")

        if role == "tool":
            if content is None:
                continue
            if isinstance(content, str):
                content_text = content
            elif isinstance(content, list):
                text_items = [
                    part["text"]
                    for part in content
                    if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str)
                ]
                if not text_items:
                    continue
                content_text = "\n".join(text_items)
            else:
                continue

            header_attrs: list[str] = []
            if m.get("tool_call_id"):
                header_attrs.append(f"tool_call_id={m['tool_call_id']}")
            if m.get("name"):
                header_attrs.append(f"name={m['name']}")
            header = f"tool ({', '.join(header_attrs)})" if header_attrs else "tool"
            parts.append(f"{header}: {content_text}")
            continue

        if isinstance(content, str):
            if content or not tool_calls:
                parts.append(f"{role}: {content}")
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text" and part.get("text"):
                    parts.append(f"{role}: {part['text']}")

        if role == "assistant" and isinstance(tool_calls, list):
            for tc in tool_calls:
                if not isinstance(tc, dict):
                    continue
                tc_id = tc.get("id") or ""
                fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
                fn_name = fn.get("name") or ""
                if not fn_name:
                    continue
                fn_args = fn.get("arguments")
                if fn_args is None:
                    fn_args_str = "{}"
                elif isinstance(fn_args, str):
                    fn_args_str = fn_args
                else:
                    fn_args_str = json.dumps(fn_args, ensure_ascii=False)
                parts.append(f"assistant (tool_call id={tc_id}): {fn_name}({fn_args_str})")

    if not parts:
        return []
    text = "\n\n".join(parts)
    if has_active_tools(tools, tool_choice):
        bridge_instructions = _format_tool_bridge_instructions(tools or [], tool_choice)
        text = f"{bridge_instructions}\n\n{text}"
    text = _apply_metadata_model_prefix(text, metadata)
    block = TextContentBlock(type="text", text=text)
    return [block]


def openai_response_input_to_acp_prompt_blocks(
    input_value: str | list[dict[str, Any]],
    metadata: dict[str, Any] | None = None,
) -> list[TextContentBlock]:
    """Convert OpenAI Responses API input to ACP session/prompt ContentBlock list."""
    if isinstance(input_value, str):
        text = _apply_metadata_model_prefix(input_value, metadata)
        return [TextContentBlock(type="text", text=text)]
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
    block = TextContentBlock(type="text", text=text)
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
                    "title": sanitize_acp_tool_title(seed.get("title")),
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
            st["title"] = sanitize_acp_tool_title(update["title"])
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
    allowed_tool_names: set[str] | None = None,
) -> CreateChatCompletionResponse:
    """Build OpenAI chat.completion from ACP stdio aggregated agent text."""
    tool_calls: list[ChatCompletionMessageToolCall] | None = None
    content: str | None = aggregated_text or None
    resolved_finish: Literal["stop", "length", "content_filter", "tool_calls"] = (
        "stop" if finish_reason == "end_turn" else "stop"
    )

    if allowed_tool_names is not None:
        remaining_text, extracted = extract_tool_calls_from_text(
            aggregated_text,
            allowed_tool_names=allowed_tool_names,
        )
        if extracted:
            tool_calls = [ChatCompletionMessageToolCall(**tc) for tc in extracted]
            content = remaining_text or None
            resolved_finish = "tool_calls"

    choice = ChatCompletionChoice(
        index=0,
        message=ChatCompletionChoiceMessage(
            role="assistant",
            content=content,
            tool_calls=tool_calls,
        ),
        finish_reason=resolved_finish,
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
