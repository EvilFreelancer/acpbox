"""Unit tests for gateway.mapping (OpenAI <-> ACP message conversion)."""

import pytest

from gateway.mapping import (
    acp_run_output_to_chat_completion,
    acp_run_output_to_response_body,
    openai_messages_to_acp_input,
    openai_response_input_to_acp_input,
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


class TestNewIds:
    def test_new_response_id_prefix(self):
        rid = new_response_id()
        assert rid.startswith("resp_")
        assert len(rid) > 10

    def test_new_chat_id_uuid_format(self):
        cid = new_chat_id()
        assert len(cid) == 36
        assert cid.count("-") == 4
