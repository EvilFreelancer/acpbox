"""Tests for POST /v1/chat/completions (stateless) via mocked per-worker runner."""

import json

import pytest

from acpbox.acp_stdio import AcpStdioError


def test_chat_completion_ok(client):
    """POST /v1/chat/completions returns OpenAI format from mocked ACP."""
    r = client.post(
        "/v1/chat/completions",
        json={
            "model": "my-agent",
            "messages": [{"role": "user", "content": "Hello"}],
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["object"] == "chat.completion"
    assert data["model"] == "my-agent"
    assert len(data["choices"]) == 1
    assert data["choices"][0]["message"]["role"] == "assistant"
    assert data["choices"][0]["message"]["content"] == "Reply"
    assert data["choices"][0]["finish_reason"] == "stop"
    assert "usage" in data


def _parse_sse_chat_chunks(response) -> list[dict]:
    """Collect JSON objects from text/event-stream lines (excluding [DONE])."""
    chunks: list[dict] = []
    for line in response.iter_lines():
        if not line:
            continue
        text = line.decode("utf-8") if isinstance(line, (bytes, bytearray)) else line
        if not text.startswith("data: "):
            continue
        payload = text[len("data: ") :].strip()
        if payload == "[DONE]":
            continue
        chunks.append(json.loads(payload))
    return chunks


def test_chat_completion_stream_sse_and_content(client):
    """POST /v1/chat/completions with stream=true returns SSE chunks and full assistant text."""
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "my-agent",
            "messages": [{"role": "user", "content": "Hi"}],
            "stream": True,
        },
    ) as r:
        assert r.status_code == 200
        assert "text/event-stream" in (r.headers.get("content-type") or "").lower()
        parts = _parse_sse_chat_chunks(r)
        assert len(parts) >= 2
        assert all(p.get("object") == "chat.completion.chunk" for p in parts)
        assert parts[0]["choices"][0]["delta"].get("role") == "assistant"
        assembled = "".join(
            (c["choices"][0]["delta"].get("content") or "")
            for c in parts
            if c["choices"][0].get("finish_reason") is None
        )
        assert assembled == "Reply"
        final = [c for c in parts if c["choices"][0].get("finish_reason") is not None]
        assert len(final) == 1
        assert final[0]["choices"][0]["finish_reason"] == "stop"


def test_chat_completion_stream_multipart_text(client, monkeypatch):
    """Stream merges multiple text parts from run_turn_stream."""

    async def _mock_stream(*args, **kwargs):
        yield ("text", "Hel")
        yield ("text", "lo")
        yield ("done", "end_turn")

    monkeypatch.setattr(client.app.state.runner, "run_turn_stream", _mock_stream)
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "my-agent",
            "messages": [{"role": "user", "content": "Hi"}],
            "stream": True,
        },
    ) as r:
        assert r.status_code == 200
        parts = _parse_sse_chat_chunks(r)
        assembled = "".join(
            (c["choices"][0]["delta"].get("content") or "")
            for c in parts
            if c["choices"][0].get("finish_reason") is None
        )
        assert assembled == "Hello"


def test_chat_completion_stream_acp_session_updates(client, monkeypatch):
    """Streaming chunks may include acp.sessionId and acp.update (e.g. tool_call) from ACP."""

    async def _mock_stream(*args, **kwargs):
        yield (
            "session",
            {
                "sessionId": "sess-1",
                "update": {
                    "sessionUpdate": "tool_call",
                    "toolCallId": "tc_1",
                    "title": "run shell",
                    "status": "in_progress",
                },
            },
        )
        yield ("text", "Done.")
        yield ("done", "end_turn")

    monkeypatch.setattr(client.app.state.runner, "run_turn_stream", _mock_stream)
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "my-agent",
            "messages": [{"role": "user", "content": "Hi"}],
            "stream": True,
        },
    ) as r:
        assert r.status_code == 200
        parts = _parse_sse_chat_chunks(r)
        acp_parts = [p for p in parts if "acp" in p]
        assert len(acp_parts) == 1
        assert acp_parts[0]["acp"]["sessionId"] == "sess-1"
        assert acp_parts[0]["acp"]["update"]["sessionUpdate"] == "tool_call"
        assert acp_parts[0]["acp"]["update"]["toolCallId"] == "tc_1"
        assembled = "".join(
            (c["choices"][0]["delta"].get("content") or "")
            for c in parts
            if c["choices"][0].get("finish_reason") is None
        )
        assert assembled == "Done."


def test_chat_completion_stream_acp_error(client, monkeypatch):
    """When run_turn_stream fails before producing data, the response is an error status."""

    async def _mock_err(*args, **kwargs):
        raise AcpStdioError("boom")
        yield  # pragma: no cover

    monkeypatch.setattr(client.app.state.runner, "run_turn_stream", _mock_err)
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "my-agent",
            "messages": [{"role": "user", "content": "Hi"}],
            "stream": True,
        },
    ) as r:
        assert r.status_code == 503
        body = r.read().decode("utf-8")
        assert "boom" in body


def test_chat_completion_empty_messages(client):
    """POST /v1/chat/completions with empty messages returns 400."""
    r = client.post(
        "/v1/chat/completions",
        json={
            "model": "my-agent",
            "messages": [],
        },
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_input"


def test_chat_completion_acp_error(client, monkeypatch):
    """POST /v1/chat/completions when runner.run_turn raises maps to 503."""
    async def _mock_err(*args, **kwargs):
        raise AcpStdioError("Invalid input")
    monkeypatch.setattr(client.app.state.runner, "run_turn", _mock_err)
    r = client.post(
        "/v1/chat/completions",
        json={
            "model": "my-agent",
            "messages": [{"role": "user", "content": "Hi"}],
        },
    )
    assert r.status_code == 503
    assert r.json()["error"]["message"] == "Invalid input"


def test_chat_completion_multiple_text_parts(client, monkeypatch):
    """POST /v1/chat/completions returns text from mock run_turn."""
    async def _mock(*args, **kwargs):
        return ("Hello world", "end_turn", [])
    monkeypatch.setattr(client.app.state.runner, "run_turn", _mock)
    r = client.post(
        "/v1/chat/completions",
        json={
            "model": "my-agent",
            "messages": [{"role": "user", "content": "Hi"}],
        },
    )
    assert r.status_code == 200
    assert r.json()["choices"][0]["message"]["content"] == "Hello world"


def test_chat_completion_non_stream_includes_acp_trace(client, monkeypatch):
    """POST /v1/chat/completions with stream=false includes acp.steps (summarized trace)."""

    async def _mock(*args, **kwargs):
        return (
            "Done.",
            "end_turn",
            [
                {"sessionId": "s1", "update": {"sessionUpdate": "agent_thought_chunk", "content": {"type": "text", "text": "think"}}},
                {
                    "sessionId": "s1",
                    "update": {"sessionUpdate": "tool_call", "toolCallId": "t1", "title": "bash", "kind": "execute"},
                },
                {
                    "sessionId": "s1",
                    "update": {
                        "sessionUpdate": "tool_call_update",
                        "toolCallId": "t1",
                        "status": "completed",
                        "rawInput": {"command": "true", "description": "noop"},
                        "rawOutput": {"output": "", "metadata": {"exit": 0}},
                    },
                },
            ],
        )

    monkeypatch.setattr(client.app.state.runner, "run_turn", _mock)
    r = client.post(
        "/v1/chat/completions",
        json={
            "model": "my-agent",
            "messages": [{"role": "user", "content": "Hi"}],
            "stream": False,
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["choices"][0]["message"]["content"] == "Done."
    steps = data["acp"]["steps"]
    assert steps[0] == {"type": "reasoning", "text": "think"}
    assert steps[1]["type"] == "command"
    assert steps[1]["tool_call_id"] == "t1"
    assert steps[1]["command"] == "true"
    assert steps[1]["status"] == "completed"
    assert steps[1]["exit_code"] == 0


_TEST_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_documents",
            "description": "List documents by review status.",
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
            "description": "Fetch a document by id.",
            "parameters": {
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
            },
        },
    },
]


def test_chat_completion_non_stream_tool_calls(client, monkeypatch):
    """POST /v1/chat/completions (stream=false) parses bridged tool calls into OpenAI format."""
    captured: dict[str, object] = {}

    async def _mock_run_turn(prompt_blocks, mode_id=None, request_timeout=300.0):
        captured["prompt_text"] = prompt_blocks[0].text
        return (
            '<acpbox_tool_call>{"name": "list_documents", '
            '"arguments": {"status": "awaiting_review"}}</acpbox_tool_call>',
            "end_turn",
            [],
        )

    monkeypatch.setattr(client.app.state.runner, "run_turn", _mock_run_turn)
    r = client.post(
        "/v1/chat/completions",
        json={
            "model": "my-agent",
            "messages": [{"role": "user", "content": "Show docs awaiting review"}],
            "tools": _TEST_TOOLS,
            "stream": False,
        },
    )
    assert r.status_code == 200
    assert "<available_tools>" in str(captured["prompt_text"])
    assert "list_documents" in str(captured["prompt_text"])
    choice = r.json()["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    assert choice["message"]["content"] is None
    tool_calls = choice["message"]["tool_calls"]
    assert len(tool_calls) == 1
    assert tool_calls[0]["id"].startswith("call_")
    assert tool_calls[0]["type"] == "function"
    assert tool_calls[0]["function"]["name"] == "list_documents"
    assert tool_calls[0]["function"]["arguments"] == '{"status": "awaiting_review"}'


def test_chat_completion_stream_tool_calls_split_chunks(client, monkeypatch):
    """POST /v1/chat/completions (stream=true) emits OpenAI delta.tool_calls without leaking tags."""
    raw = (
        '<acpbox_tool_call>{"name": "list_documents", '
        '"arguments": {"status": "awaiting_review"}}</acpbox_tool_call>'
    )

    async def _mock_stream(*args, **kwargs):
        for i in range(0, len(raw), 5):
            yield ("text", raw[i : i + 5])
        yield ("done", "end_turn")

    monkeypatch.setattr(client.app.state.runner, "run_turn_stream", _mock_stream)
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "my-agent",
            "messages": [{"role": "user", "content": "Show docs awaiting review"}],
            "tools": _TEST_TOOLS,
            "stream": True,
        },
    ) as r:
        assert r.status_code == 200
        raw_lines = [
            line.decode("utf-8") if isinstance(line, (bytes, bytearray)) else line
            for line in r.iter_lines()
            if line
        ]

    assert any(line.strip() == "data: [DONE]" for line in raw_lines)
    parts = [
        json.loads(line[len("data: ") :])
        for line in raw_lines
        if line.startswith("data: ") and line[len("data: ") :].strip() != "[DONE]"
    ]
    assert parts[0]["choices"][0]["delta"] == {"role": "assistant"}
    content_text = "".join((c["choices"][0]["delta"].get("content") or "") for c in parts)
    assert "<acpbox_tool_call>" not in content_text
    assert content_text == ""
    tool_chunks = [c for c in parts if "tool_calls" in c["choices"][0]["delta"]]
    assert len(tool_chunks) == 1
    tc = tool_chunks[0]["choices"][0]["delta"]["tool_calls"][0]
    assert tc["index"] == 0
    assert tc["id"].startswith("call_")
    assert tc["type"] == "function"
    assert tc["function"]["name"] == "list_documents"
    assert tc["function"]["arguments"] == '{"status": "awaiting_review"}'
    final = [c for c in parts if c["choices"][0].get("finish_reason") is not None]
    assert len(final) == 1
    assert final[0]["choices"][0]["finish_reason"] == "tool_calls"


def test_chat_completion_stream_text_and_parallel_tool_calls(client, monkeypatch):
    """Streaming with tools forwards prose text and increments delta.tool_calls index per call."""
    raw = (
        "Checking now.\n"
        '<acpbox_tool_call>{"name": "list_documents", "arguments": {"status": "open"}}</acpbox_tool_call>'
        '<acpbox_tool_call>{"name": "get_document", "arguments": {"id": "7"}}</acpbox_tool_call>'
    )

    async def _mock_stream(*args, **kwargs):
        for i in range(0, len(raw), 7):
            yield ("text", raw[i : i + 7])
        yield ("done", "end_turn")

    monkeypatch.setattr(client.app.state.runner, "run_turn_stream", _mock_stream)
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "my-agent",
            "messages": [{"role": "user", "content": "Check open docs and doc 7"}],
            "tools": _TEST_TOOLS,
            "stream": True,
        },
    ) as r:
        assert r.status_code == 200
        parts = _parse_sse_chat_chunks(r)

    assembled = "".join((c["choices"][0]["delta"].get("content") or "") for c in parts)
    assert assembled == "Checking now.\n"
    emitted_calls = [
        tc
        for c in parts
        for tc in (c["choices"][0]["delta"].get("tool_calls") or [])
    ]
    assert [tc["index"] for tc in emitted_calls] == [0, 1]
    assert [tc["function"]["name"] for tc in emitted_calls] == ["list_documents", "get_document"]
    final = [c for c in parts if c["choices"][0].get("finish_reason") is not None]
    assert final[0]["choices"][0]["finish_reason"] == "tool_calls"


def test_chat_completion_multi_turn_tool_result_forwarded(client, monkeypatch):
    """Follow-up turn with assistant.tool_calls and role='tool' forwards tool results to runner."""
    captured: dict[str, object] = {}

    async def _mock_run_turn(prompt_blocks, mode_id=None, request_timeout=300.0):
        captured["prompt_text"] = prompt_blocks[0].text
        return ("Found 1 document awaiting review.", "end_turn", [])

    monkeypatch.setattr(client.app.state.runner, "run_turn", _mock_run_turn)
    r = client.post(
        "/v1/chat/completions",
        json={
            "model": "my-agent",
            "tools": _TEST_TOOLS,
            "messages": [
                {"role": "user", "content": "Show docs awaiting review"},
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
        },
    )
    assert r.status_code == 200
    prompt_text = str(captured["prompt_text"])
    assert 'assistant (tool_call id=call_1): list_documents({"status": "awaiting_review"})' in prompt_text
    assert 'tool (tool_call_id=call_1, name=list_documents): [{"id": "doc-1"}]' in prompt_text
    choice = r.json()["choices"][0]
    assert choice["message"]["content"] == "Found 1 document awaiting review."
    assert "tool_calls" not in choice["message"]
    assert choice["finish_reason"] == "stop"


def test_chat_completion_tool_choice_none_disables_parsing(client, monkeypatch):
    """tool_choice='none' skips bridge instructions and returns literal tag text with finish_reason='stop'."""
    literal = '<acpbox_tool_call>{"name": "list_documents", "arguments": {}}</acpbox_tool_call>'
    captured: dict[str, object] = {}

    async def _mock_run_turn(prompt_blocks, mode_id=None, request_timeout=300.0):
        captured["prompt_text"] = prompt_blocks[0].text
        return (literal, "end_turn", [])

    async def _mock_stream(prompt_blocks, mode_id=None, request_timeout=300.0):
        captured["stream_prompt_text"] = prompt_blocks[0].text
        yield ("text", literal)
        yield ("done", "end_turn")

    monkeypatch.setattr(client.app.state.runner, "run_turn", _mock_run_turn)
    monkeypatch.setattr(client.app.state.runner, "run_turn_stream", _mock_stream)

    r_non_stream = client.post(
        "/v1/chat/completions",
        json={
            "model": "my-agent",
            "messages": [{"role": "user", "content": "Say hi"}],
            "tools": _TEST_TOOLS,
            "tool_choice": "none",
        },
    )
    assert r_non_stream.status_code == 200
    assert "<available_tools>" not in str(captured["prompt_text"])
    choice = r_non_stream.json()["choices"][0]
    assert choice["finish_reason"] == "stop"
    assert choice["message"]["content"] == literal
    assert "tool_calls" not in choice["message"]

    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "my-agent",
            "messages": [{"role": "user", "content": "Say hi"}],
            "tools": _TEST_TOOLS,
            "tool_choice": "none",
            "stream": True,
        },
    ) as r_stream:
        assert r_stream.status_code == 200
        parts = _parse_sse_chat_chunks(r_stream)

    assert "<available_tools>" not in str(captured["stream_prompt_text"])
    assembled = "".join((c["choices"][0]["delta"].get("content") or "") for c in parts)
    assert assembled == literal
    assert all("tool_calls" not in c["choices"][0]["delta"] for c in parts)
    final = [c for c in parts if c["choices"][0].get("finish_reason") is not None]
    assert final[0]["choices"][0]["finish_reason"] == "stop"


def test_chat_completion_forced_tool_choice_rejects_other_declared_tools(client, monkeypatch):
    """Forced tool_choice narrows allowed tool names to the forced function in both modes."""
    raw = (
        '<acpbox_tool_call>{"name": "list_documents", "arguments": {"status": "open"}}</acpbox_tool_call>\n'
        '<acpbox_tool_call>{"name": "get_document", "arguments": {"id": "7"}}</acpbox_tool_call>'
    )

    async def _mock_run_turn(*args, **kwargs):
        return (raw, "end_turn", [])

    async def _mock_stream(*args, **kwargs):
        yield ("text", raw)
        yield ("done", "end_turn")

    monkeypatch.setattr(client.app.state.runner, "run_turn", _mock_run_turn)
    monkeypatch.setattr(client.app.state.runner, "run_turn_stream", _mock_stream)
    forced_choice = {"type": "function", "function": {"name": "get_document"}}

    r = client.post(
        "/v1/chat/completions",
        json={
            "model": "my-agent",
            "messages": [{"role": "user", "content": "Fetch doc 7"}],
            "tools": _TEST_TOOLS,
            "tool_choice": forced_choice,
        },
    )
    assert r.status_code == 200
    choice = r.json()["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    assert [tc["function"]["name"] for tc in choice["message"]["tool_calls"]] == ["get_document"]

    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "my-agent",
            "messages": [{"role": "user", "content": "Fetch doc 7"}],
            "tools": _TEST_TOOLS,
            "tool_choice": forced_choice,
            "stream": True,
        },
    ) as r_stream:
        assert r_stream.status_code == 200
        parts = _parse_sse_chat_chunks(r_stream)

    emitted_calls = [
        tc
        for c in parts
        for tc in (c["choices"][0]["delta"].get("tool_calls") or [])
    ]
    assert [tc["function"]["name"] for tc in emitted_calls] == ["get_document"]
    content_chunks = [c["choices"][0]["delta"]["content"] for c in parts if "content" in c["choices"][0]["delta"]]
    assert content_chunks == []


def test_chat_completion_stream_parallel_tool_calls_suppress_inter_call_whitespace(client, monkeypatch):
    """Streaming parallel tool calls separated by newlines emits no stray whitespace delta.content chunks."""
    raw = (
        '<acpbox_tool_call>{"name": "list_documents", "arguments": {"status": "open"}}</acpbox_tool_call>\n\n'
        '<acpbox_tool_call>{"name": "get_document", "arguments": {"id": "9"}}</acpbox_tool_call>\n'
    )

    async def _mock_stream(*args, **kwargs):
        for i in range(0, len(raw), 6):
            yield ("text", raw[i : i + 6])
        yield ("done", "end_turn")

    monkeypatch.setattr(client.app.state.runner, "run_turn_stream", _mock_stream)
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "my-agent",
            "messages": [{"role": "user", "content": "Run both"}],
            "tools": _TEST_TOOLS,
            "stream": True,
        },
    ) as r:
        assert r.status_code == 200
        parts = _parse_sse_chat_chunks(r)

    content_chunks = [c["choices"][0]["delta"]["content"] for c in parts if "content" in c["choices"][0]["delta"]]
    assert content_chunks == []
    emitted_calls = [
        tc
        for c in parts
        for tc in (c["choices"][0]["delta"].get("tool_calls") or [])
    ]
    assert [tc["index"] for tc in emitted_calls] == [0, 1]
    assert [tc["function"]["name"] for tc in emitted_calls] == ["list_documents", "get_document"]
