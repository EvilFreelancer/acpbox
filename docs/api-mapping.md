# API mapping

This document describes how each OpenAI-compatible endpoint is translated to ACP and how responses are converted back.

## Models (OpenAI) <-> Agents (ACP)

| OpenAI | ACP | Notes |
|--------|-----|--------|
| `GET /v1/models` | `GET /agents` | Response: `data` = list of `{ id: agent.name, object: "model", created, owned_by: "acp" }`. |
| `GET /v1/models/{model}` | `GET /agents/{name}` | `model` path = agent `name`. 404 from ACP -> 404 with OpenAI error body. |

## Chat Completions (stateless)

| OpenAI | ACP | Notes |
|--------|-----|--------|
| `POST /v1/chat/completions` | `POST /runs` | No `session_id`. `model` -> `agent_name`. `messages` -> ACP `input` (see below). `mode: "sync"`. |

**Request mapping (messages -> ACP input):**

- Each OpenAI message: `role` (system/user/assistant/developer) -> ACP `role`: `user` for system/user/developer, `agent` for assistant.
- `content`: string -> one part `{ content_type: "text/plain", content }`. Array of parts: `type: "text"` -> text/plain part; `type: "image_url"` -> `content_url` from `image_url.url`.

**Response:** From ACP `Run.output`, all agent message parts with `content_type: "text/plain"` are concatenated into a single assistant `content`. One choice, `finish_reason: "stop"`. `usage` is set to zero if ACP does not provide token counts.

**Limitations:** `stream: true` is not supported yet (returns 400).

## Responses (stateful)

| OpenAI | ACP | Notes |
|--------|-----|--------|
| `POST /v1/responses` | `POST /runs` | With `session_id` = `chat_id` (or new UUID if omitted). |
| `DELETE /v1/responses/{response_id}` | (gateway only) | Removes response from session store; returns `{ id, object: "response", deleted: true }`. |
| `GET /v1/responses/{response_id}` | - | Not implemented (501). |
| `DELETE /v1/sessions/{chat_id}` | (gateway only) | Extension: deletes all response_ids for that session. |

**Request mapping:**

- `model` -> `agent_name`.
- `input`: string -> one user message with one text part; list of items -> user/assistant messages, each item `content` (string or list of `input_text` / `input_image` parts) -> ACP parts.
- **chat_id** (optional, extension): if present, used as ACP `session_id`; otherwise the gateway generates a new UUID and returns it as **chat_id** in the response.

**Response:** Response body includes:

- **id** – response_id for this turn (e.g. `resp_<uuid>`).
- **chat_id** – session id to send in the next request for the same conversation.
- **output** – list of messages; text is taken from ACP Run output as for chat completions.

**Session store:** The gateway keeps an in-memory mapping: response_id -> chat_id, and chat_id -> set of response_ids. DELETE response/session only updates this store; the ACP server may still hold session state until it is garbage-collected or restarted.

## Error format

ACP errors (e.g. `{ "code": "invalid_input" | "not_found" | "server_error", "message": "..." }`) are mapped to HTTP status codes:

- `invalid_input` -> 400
- `not_found` -> 404
- `server_error` (or other) -> 500

Response body is always OpenAI-style: `{ "error": { "code": "...", "message": "...", "type": "..." } }`.
