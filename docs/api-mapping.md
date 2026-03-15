# API mapping

How each OpenAI-compatible endpoint is implemented using ACP over stdio.

## Models (OpenAI) <- config

| OpenAI | Gateway | Notes |
|--------|---------|--------|
| `GET /v1/models` | Config `acp.models` | No agent spawn. Returns `data` = list of `{ id, object: "model", created, owned_by: "acp" }`. |
| `GET /v1/models/{model}` | Config `acp.models` | If `model` is in the list, return one model object; else 404. |

## Chat Completions (stateless)

| OpenAI | ACP (stdio) | Notes |
|--------|-------------|--------|
| `POST /v1/chat/completions` | Spawn agent -> `initialize` -> `session/new` -> `session/prompt` | One process per request. `messages` -> ACP prompt content blocks (single text block with conversation). Reply from `session/update` agent_message_chunk -> assistant `content`. |

**Request mapping (messages -> ACP prompt):**

- Messages are converted to one or more ACP ContentBlocks (baseline: `type: "text"`, `text: "role: content\n\n..."`). System/user/assistant are concatenated into one prompt text.

**Response:** Aggregated text from `session/update` (agent_message_chunk) becomes the assistant message. One choice, `finish_reason` from ACP `stopReason`. `usage` is zero if ACP does not provide token counts.

**Limitations:** `stream: true` is not supported (400).

## Responses (stateful)

| OpenAI | ACP (stdio) | Notes |
|--------|-------------|--------|
| `POST /v1/responses` | Same as chat: one process per request | Optional `chat_id` is stored and returned for client continuity; the agent process is still new each time (no in-agent session persistence). |
| `DELETE /v1/responses/{response_id}` | Gateway only | Removes response from session store; returns `{ id, object: "response", deleted: true }`. |
| `GET /v1/responses/{response_id}` | - | Not implemented (501). |
| `DELETE /v1/sessions/{chat_id}` | Gateway only | Extension: deletes all response_ids for that session. |

**Request mapping:** `input` (string or list of items) -> ACP prompt content blocks (same as chat).

**Response:** Same as chat: aggregated text -> `output[].content[].text`, plus `id`, `chat_id`, `usage`.

## Error format

- ACP stdio errors (e.g. from `AcpStdioError`) are mapped to 503 with OpenAI-style body.
- Validation errors (empty messages, stream not supported) -> 400.
- Response body is always OpenAI-style: `{ "error": { "code", "message", "type" } }`.
