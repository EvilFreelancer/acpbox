"""Unit tests for acpbox.mapping (OpenAI <-> ACP message conversion)."""

import json

import pytest

from acp.schema import TextContentBlock

from acpbox.mapping import (
    TOOL_CALL_CLOSE_TAG,
    TOOL_CALL_OPEN_TAG,
    StreamingToolCallParser,
    acp_aggregated_text_to_chat_completion,
    acp_aggregated_text_to_response_body,
    acp_run_output_to_chat_completion,
    acp_run_output_to_response_body,
    extract_tool_calls_from_text,
    has_active_tools,
    new_chat_id,
    new_response_id,
    openai_messages_to_acp_input,
    openai_messages_to_acp_prompt_blocks,
    openai_response_input_to_acp_input,
    openai_response_input_to_acp_prompt_blocks,
    resolve_allowed_tool_names,
    sanitize_acp_tool_title,
    summarize_acp_session_for_non_stream,
)
from acpbox.schemas import CreateChatCompletionRequest


class TestOpenaiMessagesToAcpInput:
    def test_single_user_string(self):
        out = openai_messages_to_acp_input([{"role": "user", "content": "Hi"}])
        assert out == [{"role": "user", "parts": [{"content_type": "text/plain", "content": "Hi"}]}]

    def test_system_becomes_user(self):
        out = openai_messages_to_acp_input([{"role": "system", "content": "You are helpful"}])
        assert out[0]["role"] == "user"
        assert out[0]["parts"][0]["content"] == "You are helpful"

    def test_assistant_becomes_agent(self):
        out = openai_messages_to_acp_input([{"role": "assistant", "content": "Hello"}])
        assert out[0]["role"] == "agent"
        assert out[0]["parts"][0]["content"] == "Hello"

    def test_content_parts_text(self):
        out = openai_messages_to_acp_input([
            {"role": "user", "content": [{"type": "text", "text": "Hello"}]},
        ])
        assert out[0]["parts"] == [{"content_type": "text/plain", "content": "Hello"}]

    def test_content_parts_image_url(self):
        out = openai_messages_to_acp_input([
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "https://example.com/img.png"}}]},
        ])
        assert out[0]["parts"] == [{"content_type": "image/url", "content_url": "https://example.com/img.png"}]

    def test_skip_none_content(self):
        out = openai_messages_to_acp_input([{"role": "user", "content": None}])
        assert out == []

    def test_empty_list_returns_empty(self):
        out = openai_messages_to_acp_input([])
        assert out == []


class TestOpenaiResponseInputToAcpInput:
    def test_string_input(self):
        out = openai_response_input_to_acp_input("Hello")
        assert out == [{"role": "user", "parts": [{"content_type": "text/plain", "content": "Hello"}]}]

    def test_list_input(self):
        out = openai_response_input_to_acp_input([
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Bye"},
        ])
        assert len(out) == 2
        assert out[0]["role"] == "user" and out[0]["parts"][0]["content"] == "Hi"
        assert out[1]["role"] == "agent" and out[1]["parts"][0]["content"] == "Bye"


class TestAcpRunOutputToChatCompletion:
    def test_single_part(self):
        run = {"output": [{"role": "agent", "parts": [{"content_type": "text/plain", "content": "Hi"}]}]}
        resp = acp_run_output_to_chat_completion(run, "my-model")
        assert resp.object == "chat.completion"
        assert resp.model == "my-model"
        assert len(resp.choices) == 1
        assert resp.choices[0].message.content == "Hi"
        assert resp.choices[0].message.role == "assistant"
        assert resp.choices[0].finish_reason == "stop"

    def test_multiple_parts_concatenated(self):
        run = {
            "output": [
                {"parts": [{"content_type": "text/plain", "content": "A"}]},
                {"parts": [{"content_type": "text/plain", "content": "B"}]},
            ],
        }
        resp = acp_run_output_to_chat_completion(run, "m")
        assert resp.choices[0].message.content == "AB"

    def test_skips_non_text_parts(self):
        run = {"output": [{"parts": [{"content_type": "image/url", "content_url": "x"}, {"content_type": "text/plain", "content": "y"}]}]}
        resp = acp_run_output_to_chat_completion(run, "m")
        assert resp.choices[0].message.content == "y"

    def test_empty_output(self):
        resp = acp_run_output_to_chat_completion({}, "m")
        assert resp.choices[0].message.content is None


class TestAcpRunOutputToResponseBody:
    def test_basic(self):
        run = {"output": [{"parts": [{"content_type": "text/plain", "content": "Reply"}]}]}
        resp = acp_run_output_to_response_body(run, "my-model", "resp_123", "chat_456")
        assert resp.id == "resp_123"
        assert resp.chat_id == "chat_456"
        assert resp.model == "my-model"
        assert resp.object == "response"
        assert len(resp.output) == 1
        assert resp.output[0].content[0].text == "Reply"


class TestOpenaiMessagesToAcpPromptBlocks:
    def test_single_user(self):
        out = openai_messages_to_acp_prompt_blocks([{"role": "user", "content": "Hi"}])
        assert len(out) == 1
        assert isinstance(out[0], TextContentBlock)
        assert out[0].type == "text"
        assert out[0].text == "user: Hi"

    def test_multiple_messages(self):
        out = openai_messages_to_acp_prompt_blocks([
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
            {"role": "user", "content": "Bye"},
        ])
        assert len(out) == 1
        assert isinstance(out[0], TextContentBlock)
        assert out[0].text == "user: Hello\n\nassistant: Hi\n\nuser: Bye"

    def test_metadata_model_prefix_applied(self):
        out = openai_messages_to_acp_prompt_blocks(
            [{"role": "user", "content": "Hi"}],
            metadata={"model": "gpt-4.1-mini"},
        )
        assert len(out) == 1
        assert isinstance(out[0], TextContentBlock)
        assert out[0].text == "model: gpt-4.1-mini\n\nuser: Hi"

    def test_empty_returns_empty(self):
        assert openai_messages_to_acp_prompt_blocks([]) == []
        assert openai_messages_to_acp_prompt_blocks([{"role": "user", "content": None}]) == []


class TestOpenaiResponseInputToAcpPromptBlocks:
    def test_string(self):
        out = openai_response_input_to_acp_prompt_blocks("Hello")
        assert len(out) == 1
        assert isinstance(out[0], TextContentBlock)
        assert out[0].text == "Hello"

    def test_list(self):
        out = openai_response_input_to_acp_prompt_blocks([
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Bye"},
        ])
        assert len(out) == 1
        assert isinstance(out[0], TextContentBlock)
        assert out[0].text == "user: Hi\n\nassistant: Bye"

    def test_response_metadata_model_prefix_applied(self):
        out = openai_response_input_to_acp_prompt_blocks(
            "Hello",
            metadata={"model": "gpt-4.1-mini"},
        )
        assert len(out) == 1
        assert isinstance(out[0], TextContentBlock)
        assert out[0].text == "model: gpt-4.1-mini\n\nHello"


class TestAcpAggregatedTextToChatCompletion:
    def test_basic(self):
        resp = acp_aggregated_text_to_chat_completion("Hello", "my-model")
        assert resp.choices[0].message.content == "Hello"
        assert resp.model == "my-model"
        assert resp.acp is None

    def test_acp_summarized_tool(self):
        raw = [
            {
                "sessionId": "x",
                "update": {"sessionUpdate": "tool_call", "toolCallId": "t1", "title": "bash"},
            },
        ]
        resp = acp_aggregated_text_to_chat_completion("Hi", "m", acp_raw=raw)
        assert resp.acp == {
            "steps": [
                {
                    "type": "command",
                    "tool_call_id": "t1",
                    "title": "bash",
                    "kind": None,
                    "status": None,
                    "command": None,
                    "description": None,
                    "output": None,
                    "exit_code": None,
                },
            ],
        }


class TestSanitizeAcpToolTitle:
    def test_strips_harmony_channel_suffix(self):
        assert (
            sanitize_acp_tool_title("atlassian_confluence_search<|channel|>commentary")
            == "atlassian_confluence_search"
        )

    def test_plain_title_unchanged(self):
        assert sanitize_acp_tool_title("atlassian_confluence_search") == "atlassian_confluence_search"

    def test_none_unchanged(self):
        assert sanitize_acp_tool_title(None) is None


class TestSummarizeAcpNonStream:
    def test_merges_adjacent_thought_chunks(self):
        raw = [
            {"update": {"sessionUpdate": "agent_thought_chunk", "content": {"type": "text", "text": "A"}}},
            {"update": {"sessionUpdate": "agent_thought_chunk", "content": {"type": "text", "text": "B"}}},
        ]
        out = summarize_acp_session_for_non_stream(raw)
        assert out == {"steps": [{"type": "reasoning", "text": "AB"}]}

    def test_sanitizes_gpt_oss_harmony_tool_title(self):
        raw = [
            {
                "update": {
                    "sessionUpdate": "tool_call",
                    "toolCallId": "c1",
                    "title": "atlassian_confluence_search<|channel|>commentary",
                    "status": "pending",
                },
            },
        ]
        out = summarize_acp_session_for_non_stream(raw)
        assert out["steps"][0]["title"] == "atlassian_confluence_search"

    def test_merges_tool_call_updates_into_one_step(self):
        raw = [
            {
                "update": {
                    "sessionUpdate": "tool_call",
                    "toolCallId": "c1",
                    "title": "bash",
                    "status": "pending",
                },
            },
            {
                "update": {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": "c1",
                    "status": "in_progress",
                    "rawInput": {"command": "echo 1", "description": "d"},
                },
            },
            {
                "update": {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": "c1",
                    "status": "completed",
                    "rawOutput": {"output": "1\n", "metadata": {"exit": 0}},
                },
            },
        ]
        out = summarize_acp_session_for_non_stream(raw)
        assert len(out["steps"]) == 1
        s = out["steps"][0]
        assert s["type"] == "command"
        assert s["command"] == "echo 1"
        assert s["output"] == "1\n"
        assert s["exit_code"] == 0
        assert s["status"] == "completed"

    def test_drops_usage_update(self):
        raw = [{"update": {"sessionUpdate": "usage_update", "used": 1}}]
        assert summarize_acp_session_for_non_stream(raw) is None


class TestAcpAggregatedTextToResponseBody:
    def test_basic(self):
        resp = acp_aggregated_text_to_response_body("Reply", "m", "resp_1", "chat_1")
        assert resp.output[0].content[0].text == "Reply"
        assert resp.chat_id == "chat_1"
        assert resp.acp is None


class TestNewIds:
    def test_new_response_id_prefix(self):
        rid = new_response_id()
        assert rid.startswith("resp_")
        assert len(rid) > 10

    def test_new_chat_id_uuid_format(self):
        cid = new_chat_id()
        assert len(cid) == 36
        assert cid.count("-") == 4


SAMPLE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_documents",
            "description": "List documents filtered by status.",
            "parameters": {
                "type": "object",
                "properties": {"status": {"type": "string"}},
                "required": ["status"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_document",
            "description": "Fetch a document by ID.",
            "parameters": {
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
            },
        },
    },
]


class TestToolCallingSchemas:
    def test_request_accepts_tools_tool_choice_and_tool_messages(self):
        """CreateChatCompletionRequest validates OpenAI tools, tool_choice, assistant.tool_calls, and tool role."""
        req = CreateChatCompletionRequest.model_validate(
            {
                "model": "build",
                "messages": [
                    {"role": "user", "content": "Show pending docs"},
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "list_documents",
                                    "arguments": '{"status": "awaiting_review"}',
                                },
                            },
                        ],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": "call_1",
                        "name": "list_documents",
                        "content": '[{"id": "doc-1"}]',
                    },
                ],
                "tools": SAMPLE_TOOLS,
                "tool_choice": "auto",
            },
        )
        assert req.tools is not None
        assert len(req.tools) == 2
        assert req.tools[0].function.name == "list_documents"
        assert req.messages[1].tool_calls is not None
        assert req.messages[1].tool_calls[0].id == "call_1"
        assert req.messages[2].role == "tool"
        assert req.messages[2].tool_call_id == "call_1"


class TestHasActiveTools:
    def test_false_when_tools_none_or_empty(self):
        """has_active_tools returns False when tools are absent or empty."""
        assert has_active_tools(None, None) is False
        assert has_active_tools([], "auto") is False

    def test_false_when_tool_choice_none(self):
        """has_active_tools returns False when tool_choice is 'none'."""
        assert has_active_tools(SAMPLE_TOOLS, "none") is False

    def test_true_for_active_modes(self):
        """has_active_tools returns True for None, auto, required, or forced tool_choice."""
        assert has_active_tools(SAMPLE_TOOLS, None) is True
        assert has_active_tools(SAMPLE_TOOLS, "auto") is True
        assert has_active_tools(SAMPLE_TOOLS, "required") is True
        assert has_active_tools(
            SAMPLE_TOOLS,
            {"type": "function", "function": {"name": "list_documents"}},
        ) is True

    def test_false_when_forced_tool_choice_is_undeclared(self):
        """has_active_tools returns False when forced tool_choice names an undeclared tool."""
        assert has_active_tools(
            SAMPLE_TOOLS,
            {"type": "function", "function": {"name": "not_declared"}},
        ) is False


class TestResolveAllowedToolNames:
    def test_returns_none_when_inactive(self):
        """Returns None when tools are absent/empty or tool_choice is 'none'."""
        assert resolve_allowed_tool_names(None, None) is None
        assert resolve_allowed_tool_names([], "auto") is None
        assert resolve_allowed_tool_names(SAMPLE_TOOLS, "none") is None

    def test_returns_all_declared_names_for_auto_and_required(self):
        """Returns all declared tool names for None, 'auto', and 'required'."""
        expected = {"list_documents", "get_document"}
        assert resolve_allowed_tool_names(SAMPLE_TOOLS, None) == expected
        assert resolve_allowed_tool_names(SAMPLE_TOOLS, "auto") == expected
        assert resolve_allowed_tool_names(SAMPLE_TOOLS, "required") == expected

    def test_narrows_to_forced_function_when_declared(self):
        """Forced function tool_choice narrows allowed names to that single function."""
        choice = {"type": "function", "function": {"name": "get_document"}}
        assert resolve_allowed_tool_names(SAMPLE_TOOLS, choice) == {"get_document"}

    def test_returns_empty_set_when_forced_function_not_declared(self):
        """Forced function not present in tools resolves to an empty allowed set."""
        choice = {"type": "function", "function": {"name": "not_declared"}}
        assert resolve_allowed_tool_names(SAMPLE_TOOLS, choice) == set()


class TestPromptBlocksWithTools:
    def test_tools_auto_prepends_bridge_instructions(self):
        """Prompt prepends OpenAI tool bridge instructions and available_tools JSON."""
        out = openai_messages_to_acp_prompt_blocks(
            [{"role": "user", "content": "Show pending docs"}],
            tools=SAMPLE_TOOLS,
            tool_choice="auto",
        )
        assert len(out) == 1
        text = out[0].text
        assert TOOL_CALL_OPEN_TAG in text
        assert TOOL_CALL_CLOSE_TAG in text
        assert "<available_tools>" in text
        assert "</available_tools>" in text
        assert "list_documents" in text
        assert "user: Show pending docs" in text

    def test_tools_required_policy_included(self):
        """tool_choice='required' instructs the agent it must invoke at least one external tool."""
        out = openai_messages_to_acp_prompt_blocks(
            [{"role": "user", "content": "Check docs"}],
            tools=SAMPLE_TOOLS,
            tool_choice="required",
        )
        text = out[0].text
        assert "MUST call at least one" in text

    def test_tools_forced_function_policy_included(self):
        """Forced function tool_choice names the required external tool."""
        out = openai_messages_to_acp_prompt_blocks(
            [{"role": "user", "content": "Check docs"}],
            tools=SAMPLE_TOOLS,
            tool_choice={"type": "function", "function": {"name": "get_document"}},
        )
        text = out[0].text
        assert 'MUST call the external tool "get_document"' in text

    def test_tools_none_preserves_plain_prompt(self):
        """tool_choice='none' skips tool bridge instructions entirely."""
        out = openai_messages_to_acp_prompt_blocks(
            [{"role": "user", "content": "Hi"}],
            tools=SAMPLE_TOOLS,
            tool_choice="none",
        )
        assert len(out) == 1
        assert out[0].text == "user: Hi"

    def test_tools_undeclared_forced_choice_skips_bridge_instructions(self):
        """Undeclared forced tool_choice skips contradictory tool bridge instructions."""
        out = openai_messages_to_acp_prompt_blocks(
            [{"role": "user", "content": "Hi"}],
            tools=SAMPLE_TOOLS,
            tool_choice={"type": "function", "function": {"name": "not_declared"}},
        )
        assert len(out) == 1
        assert out[0].text == "user: Hi"
        assert "You MUST call" not in out[0].text

    def test_metadata_model_stays_on_first_line_with_tools(self):
        """metadata.model remains on line 1 even when tool bridge instructions are prepended."""
        out = openai_messages_to_acp_prompt_blocks(
            [{"role": "user", "content": "Hi"}],
            metadata={"model": "gpt-4.1-mini"},
            tools=SAMPLE_TOOLS,
            tool_choice="auto",
        )
        assert out[0].text.startswith("model: gpt-4.1-mini\n\n")
        assert "<available_tools>" in out[0].text

    def test_multi_turn_assistant_tool_calls_and_tool_role(self):
        """Assistant messages with tool_calls (and content=None) plus role='tool' serialize cleanly."""
        messages = [
            {"role": "user", "content": "Show pending docs"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_abc",
                        "type": "function",
                        "function": {
                            "name": "list_documents",
                            "arguments": '{"status": "awaiting_review"}',
                        },
                    },
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_abc",
                "name": "list_documents",
                "content": '[{"id": "doc-1"}]',
            },
        ]
        out = openai_messages_to_acp_prompt_blocks(messages)
        assert len(out) == 1
        expected = (
            "user: Show pending docs\n\n"
            'assistant (tool_call id=call_abc): list_documents({"status": "awaiting_review"})\n\n'
            'tool (tool_call_id=call_abc, name=list_documents): [{"id": "doc-1"}]'
        )
        assert out[0].text == expected

    def test_tool_role_without_name(self):
        """role='tool' without optional name omits name from header."""
        messages = [
            {"role": "tool", "tool_call_id": "call_xyz", "content": "ok"},
        ]
        out = openai_messages_to_acp_prompt_blocks(messages)
        assert out[0].text == "tool (tool_call_id=call_xyz): ok"

    def test_tool_role_list_content_preserves_empty_string_part(self):
        """role='tool' with list text part containing '' preserves the empty tool output."""
        messages = [
            {
                "role": "tool",
                "tool_call_id": "call_empty",
                "content": [{"type": "text", "text": ""}],
            },
        ]
        out = openai_messages_to_acp_prompt_blocks(messages)
        assert len(out) == 1
        assert out[0].text == "tool (tool_call_id=call_empty): "


class TestExtractToolCallsFromText:
    def test_single_tool_call(self):
        """Extracts a single tool call block and returns empty remaining text."""
        raw = (
            f'{TOOL_CALL_OPEN_TAG}'
            '{"name": "list_documents", "arguments": {"status": "awaiting_review"}}'
            f'{TOOL_CALL_CLOSE_TAG}'
        )
        remaining, calls = extract_tool_calls_from_text(raw, {"list_documents"})
        assert remaining == ""
        assert len(calls) == 1
        assert calls[0]["id"].startswith("call_")
        assert calls[0]["type"] == "function"
        assert calls[0]["function"]["name"] == "list_documents"
        assert json.loads(calls[0]["function"]["arguments"]) == {"status": "awaiting_review"}

    def test_multiple_parallel_tool_calls(self):
        """Extracts multiple parallel tool calls in order."""
        raw = (
            f'{TOOL_CALL_OPEN_TAG}{{"name": "list_documents", "arguments": {{"status": "open"}}}}'
            f'{TOOL_CALL_CLOSE_TAG}\n'
            f'{TOOL_CALL_OPEN_TAG}{{"name": "get_document", "arguments": {{"id": "42"}}}}'
            f'{TOOL_CALL_CLOSE_TAG}'
        )
        remaining, calls = extract_tool_calls_from_text(raw, {"list_documents", "get_document"})
        assert remaining == ""
        assert [c["function"]["name"] for c in calls] == ["list_documents", "get_document"]
        assert json.loads(calls[1]["function"]["arguments"]) == {"id": "42"}

    def test_text_plus_tool_call(self):
        """Preserves surrounding prose while removing the tool call block."""
        raw = (
            "Let me check your documents.\n\n"
            f'{TOOL_CALL_OPEN_TAG}{{"name": "list_documents", "arguments": {{}}}}{TOOL_CALL_CLOSE_TAG}'
        )
        remaining, calls = extract_tool_calls_from_text(raw, {"list_documents"})
        assert remaining == "Let me check your documents."
        assert len(calls) == 1
        assert calls[0]["function"]["arguments"] == "{}"

    def test_multiline_json_and_list_payload(self):
        """Handles multiline JSON and a JSON array of calls inside a single tag."""
        raw = (
            f"{TOOL_CALL_OPEN_TAG}\n"
            '[\n'
            '  {"name": "list_documents", "arguments": {"status": "draft"}},\n'
            '  {"name": "get_document", "arguments": "{\\"id\\": \\"7\\"}"}\n'
            ']\n'
            f"{TOOL_CALL_CLOSE_TAG}"
        )
        remaining, calls = extract_tool_calls_from_text(raw, {"list_documents", "get_document"})
        assert remaining == ""
        assert len(calls) == 2
        assert calls[1]["function"]["arguments"] == '{"id": "7"}'

    def test_unknown_tool_name_filtered_out_and_stripped(self):
        """Filters out and strips valid tagged tool call blocks whose name is not allowed."""
        raw = f'{TOOL_CALL_OPEN_TAG}{{"name": "delete_everything", "arguments": {{}}}}{TOOL_CALL_CLOSE_TAG}'
        remaining, calls = extract_tool_calls_from_text(raw, {"list_documents"})
        assert calls == []
        assert remaining == ""
        assert TOOL_CALL_OPEN_TAG not in remaining

        with_prose = (
            "Checking.\n\n"
            f'{TOOL_CALL_OPEN_TAG}{{"name": "delete_everything", "arguments": {{}}}}{TOOL_CALL_CLOSE_TAG}'
        )
        remaining_prose, calls_prose = extract_tool_calls_from_text(with_prose, {"list_documents"})
        assert calls_prose == []
        assert remaining_prose == "Checking."

    def test_malformed_and_unclosed_tags_remain_plain_text(self):
        """Malformed JSON or unclosed tool call tags remain untouched as plain text."""
        raw = f"Oops {TOOL_CALL_OPEN_TAG}{{bad json{TOOL_CALL_CLOSE_TAG}"
        remaining, calls = extract_tool_calls_from_text(raw, {"list_documents"})
        assert calls == []
        assert remaining == raw

        unclosed = f'Prefix {TOOL_CALL_OPEN_TAG}{{"name": "list_documents", "arguments": {{}}'
        remaining_unclosed, calls_unclosed = extract_tool_calls_from_text(unclosed, {"list_documents"})
        assert calls_unclosed == []
        assert remaining_unclosed == unclosed

    def test_markdown_fence_around_tag_stripped_without_breaking_code_blocks(self):
        """Strips accidental markdown fences around a tool call while keeping unrelated code blocks."""
        raw = (
            "Example:\n"
            "```python\n"
            "x = 1\n"
            "```\n\n"
            "```json\n"
            f'{TOOL_CALL_OPEN_TAG}{{"name": "list_documents", "arguments": {{}}}}{TOOL_CALL_CLOSE_TAG}\n'
            "```"
        )
        remaining, calls = extract_tool_calls_from_text(raw, {"list_documents"})
        assert len(calls) == 1
        assert "```python\nx = 1\n```" in remaining
        assert "```json" not in remaining
        assert TOOL_CALL_OPEN_TAG not in remaining

    def test_multiple_parallel_tool_calls_inside_single_markdown_fence(self):
        """Strips a single markdown fence wrapping multiple parallel tool calls."""
        raw = (
            "```json\n"
            f'{TOOL_CALL_OPEN_TAG}{{"name": "list_documents", "arguments": {{"status": "open"}}}}'
            f"{TOOL_CALL_CLOSE_TAG}\n\n"
            f'{TOOL_CALL_OPEN_TAG}{{"name": "get_document", "arguments": {{"id": "5"}}}}'
            f"{TOOL_CALL_CLOSE_TAG}\n"
            "```"
        )
        remaining, calls = extract_tool_calls_from_text(raw, {"list_documents", "get_document"})
        assert remaining == ""
        assert [c["function"]["name"] for c in calls] == ["list_documents", "get_document"]

    def test_close_tag_inside_json_string_arguments(self):
        """Parses tool call correctly even when arguments contain a literal </acpbox_tool_call> string."""
        raw = (
            f"{TOOL_CALL_OPEN_TAG}"
            '{"name": "list_documents", "arguments": {"status": "literal </acpbox_tool_call> text"}}'
            f"{TOOL_CALL_CLOSE_TAG}"
        )
        remaining, calls = extract_tool_calls_from_text(raw, {"list_documents"})
        assert remaining == ""
        assert len(calls) == 1
        assert json.loads(calls[0]["function"]["arguments"]) == {
            "status": "literal </acpbox_tool_call> text",
        }

    def test_prose_mentioning_open_tag_followed_by_real_tool_call(self):
        """Prose mentioning <acpbox_tool_call> does not swallow a subsequent real tool call."""
        raw = (
            f"Note: use {TOOL_CALL_OPEN_TAG} syntax carefully.\n\n"
            f'{TOOL_CALL_OPEN_TAG}{{"name": "list_documents", "arguments": {{}}}}{TOOL_CALL_CLOSE_TAG}'
        )
        remaining, calls = extract_tool_calls_from_text(raw, {"list_documents"})
        assert remaining == f"Note: use {TOOL_CALL_OPEN_TAG} syntax carefully."
        assert len(calls) == 1
        assert calls[0]["function"]["name"] == "list_documents"

    def test_string_arguments_validation_and_empty_default(self):
        """Empty/whitespace string arguments default to '{}' and invalid JSON string arguments are rejected."""
        raw_empty = (
            f'{TOOL_CALL_OPEN_TAG}{{"name": "list_documents", "arguments": "   "}}{TOOL_CALL_CLOSE_TAG}'
        )
        remaining, calls = extract_tool_calls_from_text(raw_empty, {"list_documents"})
        assert remaining == ""
        assert len(calls) == 1
        assert calls[0]["function"]["arguments"] == "{}"

        raw_bad = (
            f'{TOOL_CALL_OPEN_TAG}{{"name": "list_documents", "arguments": "not-json"}}{TOOL_CALL_CLOSE_TAG}'
        )
        remaining_bad, calls_bad = extract_tool_calls_from_text(raw_bad, {"list_documents"})
        assert calls_bad == []
        assert remaining_bad == ""


class TestStreamingToolCallParser:
    def test_split_across_single_char_chunks(self):
        """Streams plain text before tag and parses tool call split 1 character at a time."""
        parser = StreamingToolCallParser({"list_documents"})
        full = (
            "Checking now. "
            f'{TOOL_CALL_OPEN_TAG}{{"name": "list_documents", "arguments": {{"status": "open"}}}}'
            f'{TOOL_CALL_CLOSE_TAG}'
        )
        events = []
        for ch in full:
            events.extend(parser.feed(ch))
        events.extend(parser.finish())

        text_out = "".join(val for kind, val in events if kind == "text")
        tool_out = [val for kind, val in events if kind == "tool_call"]
        assert text_out == "Checking now. "
        assert TOOL_CALL_OPEN_TAG not in text_out
        assert len(tool_out) == 1
        assert tool_out[0]["function"]["name"] == "list_documents"
        assert json.loads(tool_out[0]["function"]["arguments"]) == {"status": "open"}

    def test_plain_text_streamed_before_tag_and_multiple_calls(self):
        """Streams plain text immediately and emits multiple tool calls in sequence."""
        parser = StreamingToolCallParser({"list_documents", "get_document"})
        e1 = parser.feed("Working on it")
        assert e1 == [("text", "Working on it")]

        payload = (
            f'{TOOL_CALL_OPEN_TAG}{{"name": "list_documents", "arguments": {{}}}}{TOOL_CALL_CLOSE_TAG}'
            f'{TOOL_CALL_OPEN_TAG}{{"name": "get_document", "arguments": {{"id": "9"}}}}{TOOL_CALL_CLOSE_TAG}'
        )
        events = []
        for i in range(0, len(payload), 3):
            events.extend(parser.feed(payload[i : i + 3]))
        events.extend(parser.finish())

        tool_out = [val for kind, val in events if kind == "tool_call"]
        text_out = "".join(val for kind, val in events if kind == "text")
        assert text_out == ""
        assert [t["function"]["name"] for t in tool_out] == ["list_documents", "get_document"]

    def test_false_alarm_angle_brackets_flushed_as_text(self):
        """HTML tags, comparisons, and partial tag prefixes at EOF flush cleanly as text."""
        parser = StreamingToolCallParser({"list_documents"})
        events = []
        for chunk in ["<div>a <", " b</div> ", "<acpb"]:
            events.extend(parser.feed(chunk))
        events.extend(parser.finish())

        text_out = "".join(val for kind, val in events if kind == "text")
        tool_out = [val for kind, val in events if kind == "tool_call"]
        assert text_out == "<div>a < b</div> <acpb"
        assert tool_out == []

    def test_unknown_and_malformed_tags_never_leak_into_text(self):
        """Invalid or unknown tool call tags do not crash and never leak tag markup into text."""
        parser = StreamingToolCallParser({"list_documents"})
        raw = (
            "Hello "
            f'{TOOL_CALL_OPEN_TAG}{{"name": "unknown_tool", "arguments": {{}}}}{TOOL_CALL_CLOSE_TAG}'
            f"{TOOL_CALL_OPEN_TAG}{{not json{TOOL_CALL_CLOSE_TAG}"
            "world"
        )
        events = parser.feed(raw) + parser.finish()
        text_out = "".join(val for kind, val in events if kind == "text")
        tool_out = [val for kind, val in events if kind == "tool_call"]
        assert text_out == "Hello world"
        assert tool_out == []
        assert TOOL_CALL_OPEN_TAG not in text_out

    def test_unclosed_tag_flushed_as_text_on_finish(self):
        """Unclosed <acpbox_tool_call> at stream end is flushed as text rather than silently dropped."""
        parser = StreamingToolCallParser({"list_documents"})
        raw = f'Prefix {TOOL_CALL_OPEN_TAG}{{"name": "list_documents", "arguments": '
        events = parser.feed(raw) + parser.finish()
        text_out = "".join(val for kind, val in events if kind == "text")
        tool_out = [val for kind, val in events if kind == "tool_call"]
        assert text_out == raw
        assert tool_out == []

    def test_non_json_after_open_tag_bails_out_early_and_parses_later_real_call(self):
        """Prose mentioning <acpbox_tool_call> bails out early so a real call afterward still parses."""
        parser = StreamingToolCallParser({"list_documents"})
        raw = (
            f"Use {TOOL_CALL_OPEN_TAG} to invoke tools. "
            f'{TOOL_CALL_OPEN_TAG}{{"name": "list_documents", "arguments": {{}}}}{TOOL_CALL_CLOSE_TAG}'
        )
        events = []
        for i in range(0, len(raw), 4):
            events.extend(parser.feed(raw[i : i + 4]))
        events.extend(parser.finish())
        text_out = "".join(val for kind, val in events if kind == "text")
        tool_out = [val for kind, val in events if kind == "tool_call"]
        assert text_out == f"Use {TOOL_CALL_OPEN_TAG} to invoke tools. "
        assert len(tool_out) == 1
        assert tool_out[0]["function"]["name"] == "list_documents"

    def test_close_tag_inside_json_string_arguments_streaming(self):
        """Streams a tool call whose JSON string arguments contain a literal </acpbox_tool_call>."""
        parser = StreamingToolCallParser({"list_documents"})
        raw = (
            f"{TOOL_CALL_OPEN_TAG}"
            '{"name": "list_documents", "arguments": {"status": "a </acpbox_tool_call> b"}}'
            f"{TOOL_CALL_CLOSE_TAG}"
        )
        events = []
        for i in range(0, len(raw), 3):
            events.extend(parser.feed(raw[i : i + 3]))
        events.extend(parser.finish())
        text_out = "".join(val for kind, val in events if kind == "text")
        tool_out = [val for kind, val in events if kind == "tool_call"]
        assert text_out == ""
        assert len(tool_out) == 1
        assert json.loads(tool_out[0]["function"]["arguments"]) == {
            "status": "a </acpbox_tool_call> b",
        }

    def test_whitespace_between_and_after_parallel_tool_calls_is_suppressed(self):
        """Drops whitespace-only gaps between/after tool calls, but preserves spacing before real text."""
        parser = StreamingToolCallParser({"list_documents", "get_document"})
        raw = (
            f'{TOOL_CALL_OPEN_TAG}{{"name": "list_documents", "arguments": {{}}}}{TOOL_CALL_CLOSE_TAG}'
            "\n\n"
            f'{TOOL_CALL_OPEN_TAG}{{"name": "get_document", "arguments": {{"id": "1"}}}}{TOOL_CALL_CLOSE_TAG}'
            "\n  "
        )
        events = []
        for i in range(0, len(raw), 4):
            events.extend(parser.feed(raw[i : i + 4]))
        events.extend(parser.finish())
        text_events = [val for kind, val in events if kind == "text"]
        tool_events = [val for kind, val in events if kind == "tool_call"]
        assert text_events == []
        assert [t["function"]["name"] for t in tool_events] == ["list_documents", "get_document"]

        parser2 = StreamingToolCallParser({"list_documents"})
        raw2 = (
            f'{TOOL_CALL_OPEN_TAG}{{"name": "list_documents", "arguments": {{}}}}{TOOL_CALL_CLOSE_TAG}'
            "\n\nFollowing note."
        )
        events2 = parser2.feed(raw2) + parser2.finish()
        text_out2 = "".join(val for kind, val in events2 if kind == "text")
        assert text_out2 == "\n\nFollowing note."


class TestAcpAggregatedTextToChatCompletionToolCalls:
    def test_extracts_tool_calls_and_sets_finish_reason(self):
        """With allowed_tool_names, extracted calls populate message.tool_calls and finish_reason='tool_calls'."""
        raw = (
            f'{TOOL_CALL_OPEN_TAG}'
            '{"name": "list_documents", "arguments": {"status": "awaiting_review"}}'
            f'{TOOL_CALL_CLOSE_TAG}'
        )
        resp = acp_aggregated_text_to_chat_completion(
            raw,
            "my-model",
            allowed_tool_names={"list_documents"},
        )
        choice = resp.choices[0]
        assert choice.finish_reason == "tool_calls"
        assert choice.message.content is None
        assert choice.message.tool_calls is not None
        assert len(choice.message.tool_calls) == 1
        assert choice.message.tool_calls[0].function.name == "list_documents"
        assert json.loads(choice.message.tool_calls[0].function.arguments) == {
            "status": "awaiting_review",
        }
        dumped_msg = resp.model_dump()["choices"][0]["message"]
        assert dumped_msg["content"] is None
        assert "tool_calls" in dumped_msg

    def test_without_allowed_tool_names_leaves_raw_text_unchanged_and_omits_tool_calls_key(self):
        """When tool_calls is None, serialized message omits 'tool_calls' key."""
        raw = f'{TOOL_CALL_OPEN_TAG}{{"name": "list_documents", "arguments": {{}}}}{TOOL_CALL_CLOSE_TAG}'
        resp = acp_aggregated_text_to_chat_completion(raw, "my-model")
        choice = resp.choices[0]
        assert choice.finish_reason == "stop"
        assert choice.message.content == raw
        assert choice.message.tool_calls is None
        dumped_msg = resp.model_dump()["choices"][0]["message"]
        assert dumped_msg == {"role": "assistant", "content": raw}
        assert "tool_calls" not in dumped_msg

    def test_rejected_tool_call_block_is_stripped_from_content(self):
        """Valid tagged tool call blocks rejected by allowed_tool_names never leak into message.content."""
        raw = (
            "Unable to run that.\n"
            f'{TOOL_CALL_OPEN_TAG}{{"name": "delete_everything", "arguments": {{}}}}{TOOL_CALL_CLOSE_TAG}'
        )
        resp = acp_aggregated_text_to_chat_completion(
            raw,
            "my-model",
            allowed_tool_names={"list_documents"},
        )
        choice = resp.choices[0]
        assert choice.finish_reason == "stop"
        assert choice.message.tool_calls is None
        assert choice.message.content == "Unable to run that."
        assert TOOL_CALL_OPEN_TAG not in (choice.message.content or "")

        raw_only = f'{TOOL_CALL_OPEN_TAG}{{"name": "delete_everything", "arguments": {{}}}}{TOOL_CALL_CLOSE_TAG}'
        resp_only = acp_aggregated_text_to_chat_completion(
            raw_only,
            "my-model",
            allowed_tool_names={"list_documents"},
        )
        assert resp_only.choices[0].finish_reason == "stop"
        assert resp_only.choices[0].message.tool_calls is None
        assert resp_only.choices[0].message.content is None
