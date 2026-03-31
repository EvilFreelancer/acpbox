# API mapping

How each OpenAI-compatible endpoint is implemented using ACP over stdio.

## Models (OpenAI) <- agent modes

The list of "models" is the list of **agent operating modes** (ACP `session/new` response: `modes.availableModes[].id`). For example OpenCode reports `plan` and `build`. The gateway spawns the agent, calls `initialize` and `session/new`, and exposes these mode ids as OpenAI models.

| OpenAI | Gateway | Notes |
|--------|---------|--------|
| `GET /v1/models` | Spawn agent -> `initialize` -> `session/new` | Returns `data` = list of `{ id: modeId, object: "model", created, owned_by: "acp" }`. 503 if agent fails. |
| `GET /v1/models/{model}` | Same | If `model` is in the agent's availableModes (or fallback from agentInfo.name), return one model object; else 404. |

## Chat Completions (stateless)

| OpenAI | ACP (stdio) | Notes |
|--------|-------------|--------|
| `POST /v1/chat/completions` | Per-worker agent -> `initialize` -> `session/new` -> `session/prompt` | `session/new` **`cwd`** comes from **`ACPBOX_ACP_WORKSPACE`** / `acp.workspace` (default `./workspace`, Docker `/workspace`). `messages` -> ACP prompt content blocks. Reply from `session/update` agent_message_chunk -> assistant `content`. |

**Request mapping (messages -> ACP prompt):**

- Messages are converted to one or more ACP `ContentBlock` values from the python SDK. The gateway uses helpers from `agent-client-protocol` (for example `acp.text_block`) so the resulting JSON matches the official ACP schema.
- System/user/assistant messages are concatenated into a single text block of the form `\"role: content\"` separated by blank lines.

**Non-stream response:** Aggregated text from `session/update` (agent_message_chunk) becomes the assistant message. One choice, `finish_reason` from ACP `stopReason`. `usage` is zero if ACP does not provide token counts. The JSON body may include **`acp`**: `{ "steps": [ ... ] }` where each step is either `{ "type": "reasoning", "text": "<merged agent_thought_chunk text in order>" }` or `{ "type": "command", "tool_call_id", "title", "kind", "status", "command", "description", "output", "exit_code" }` (one row per tool, fields merged from `tool_call` / `tool_call_update`). Streaming noise such as `usage_update`, mode updates, and `plan` is omitted. Omitted or null when there are no steps.

**Streaming (`stream: true`):** Response `Content-Type: text/event-stream`. Each line is `data: ` + JSON object with `object: "chat.completion.chunk"`, same `id` for the request, then a terminal `data: [DONE]`. The gateway maps each ACP `agent_message_chunk` to a chunk with `choices[0].delta.content`. Additional ACP `session/update` kinds (`tool_call`, `tool_call_update`, `agent_thought_chunk`, `plan`, and other variants) are forwarded as separate chunk objects with an empty `choices[0].delta` and an **`acp`** object: `{ "sessionId", "update" }` where **`update`** is the ACP payload (includes **`sessionUpdate`**). Standard OpenAI clients can ignore **`acp`**; curl and custom UIs can show agent progress. The first text delta may be preceded by **`acp`**-only chunks. The first delta with assistant text may include `role: assistant` only; the last chunk has `choices[0].finish_reason` (ACP `end_turn` maps to `stop`). If `session/prompt` fails before the first chunk, the server returns **503** with the usual JSON error body (same as non-stream).

**Limitations:** `/v1/responses` does not support streaming. Only `/v1/chat/completions` honors `stream`.

## Responses (stateful)

| OpenAI | ACP (stdio) | Notes |
|--------|-------------|--------|
| `POST /v1/responses` | Same as chat (same workspace `cwd` for `session/new`) | Optional `chat_id` is stored and returned for client continuity; each HTTP request still opens a new ACP session on the same process. |
| `DELETE /v1/responses/{response_id}` | Gateway only | Removes response from session store; returns `{ id, object: "response", deleted: true }`. |
| `GET /v1/responses/{response_id}` | - | Not implemented (501). |
| `DELETE /v1/sessions/{chat_id}` | Gateway only | Extension: deletes all response_ids for that session. |

**Request mapping:** `input` (string or list of items) -> ACP prompt content blocks (same as chat).

**Response:** Same as chat: aggregated text -> `output[].content[].text`, plus `id`, `chat_id`, `usage`, and optional **`acp`** (same `steps` summary as chat non-stream).

## Error format

- ACP stdio errors (e.g. from `AcpStdioError`) are mapped to 503 with OpenAI-style body.
- Validation errors (empty messages) -> 400.
- Response body is always OpenAI-style: `{ "error": { "code", "message", "type" } }`.
