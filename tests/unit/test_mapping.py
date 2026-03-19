"""Unit tests for acpbox.mapping (OpenAI <-> ACP message conversion)."""

import pytest

from acp.schema import TextContentBlock

from acpbox.mapping import (
    acp_aggregated_text_to_chat_completion,
    acp_aggregated_text_to_response_body,
    summarize_acp_session_for_non_stream,
    acp_run_output_to_chat_completion,
    acp_run_output_to_response_body,
    openai_messages_to_acp_input,
    openai_messages_to_acp_prompt_blocks,
    openai_response_input_to_acp_input,
    openai_response_input_to_acp_prompt_blocks,
    new_chat_id,
    new_response_id,
)


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


class TestSummarizeAcpNonStream:
    def test_merges_adjacent_thought_chunks(self):
        raw = [
            {"update": {"sessionUpdate": "agent_thought_chunk", "content": {"type": "text", "text": "A"}}},
            {"update": {"sessionUpdate": "agent_thought_chunk", "content": {"type": "text", "text": "B"}}},
        ]
        out = summarize_acp_session_for_non_stream(raw)
        assert out == {"steps": [{"type": "reasoning", "text": "AB"}]}

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
