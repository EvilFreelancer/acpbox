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

## Agent Config (permissions management)

The gateway detects the agent type from `acp.command` and manages its native config file. This allows changing agent permissions at runtime through the API; changes apply to all future requests because each request creates a new ACP session that re-reads the config.

### Agent detection

| Command binary | Agent type | Config file |
|----------------|-----------|-------------|
| `opencode` | opencode | `{workspace}/opencode.json` |
| `claude-agent-acp` | claude | `{workspace}/.claude/settings.json` |
| `codex-acp` | codex | `{workspace}/codex.json` |
| `agent` | cursor | `{workspace}/.cursor/settings.json` |

If the binary is not recognised, agent config endpoints return 501.

### `GET /v1/agent`

Returns agent metadata.

```json
{
  "agent_type": "opencode",
  "config_path": "/workspace/opencode.json",
  "writable": true,
  "known_permissions": ["read", "edit", "bash", "glob", "grep", "list", "task",
    "external_directory", "lsp", "skill", "todowrite", "question",
    "webfetch", "websearch", "codesearch", "doom_loop"],
  "allowed_values": ["allow", "deny", "ask"]
}
```

The `writable` field is `false` when the config file or its parent directory is read-only. When `writable` is `false`, write endpoints (PUT, PATCH) return **403** with `config_not_writable`.

### `GET /v1/agent/permissions`

Returns current permissions from the agent's config file.

```json
{
  "agent_type": "opencode",
  "writable": true,
  "permissions": {
    "read": "allow",
    "edit": "deny",
    "bash": "deny"
  }
}
```

If the config file does not exist, `permissions` is `{}`.

### `PUT /v1/agent/permissions`

Replace permissions entirely. Accepts `preset` and/or explicit `permissions`. If both are given, the preset is applied first, then explicit values are merged on top.

**Presets:**

| Preset | Effect |
|--------|--------|
| `allow_all` | All known permissions set to `allow` |
| `deny_all` | All known permissions set to `deny` |
| `ask_all` | All known permissions set to `ask` (OpenCode only) |

Codex also supports agent-specific presets: `suggest`, `auto-edit`, `full-auto` (maps to its `approval_mode`).

**Examples:**

Lock everything down:

```json
{"preset": "deny_all"}
```

Deny all but allow specific tools (e.g. Atlassian MCP):

```json
{"preset": "deny_all", "permissions": {"atlassian_*": "allow"}}
```

Set explicit permissions without a preset:

```json
{"permissions": {"read": "allow", "edit": "allow", "bash": "deny"}}
```

**Response:** same shape as GET, with the full permissions state after the change.

**Errors:**
- 403 `config_not_writable` - config file is read-only
- 422 `empty_request` - neither preset nor permissions provided
- 422 `invalid_preset` - unknown preset name
- 422 `invalid_permission_value` - value not in `allowed_values`

### `PATCH /v1/agent/permissions`

Partial update: merge into existing permissions. Keys not mentioned are left unchanged.

```json
{"permissions": {"read": "allow", "bash": "allow"}}
```

**Response:** same shape as GET, with the full permissions state after the merge.

### Native config formats

The API uses a flat `{key: value}` permission format. Each adapter translates to the agent's native format:

**OpenCode** (`opencode.json`): direct match, `"permission"` key with flat map.

```json
{
  "permission": {
    "read": "allow",
    "edit": "deny",
    "atlassian_*": "allow"
  }
}
```

**Claude** (`.claude/settings.json`): translated to allow/deny lists.

```json
{
  "permissions": {
    "allow": ["Bash", "Read"],
    "deny": ["WebFetch"]
  }
}
```

Pattern permissions like `Bash(git *)` are preserved as-is in the key.

**Codex** (`codex.json`): flat map plus optional `approval_mode` when using codex-specific presets.

```json
{
  "approval_mode": "full-auto",
  "permission": {
    "read": "allow",
    "write": "allow",
    "bash": "allow",
    "network": "allow"
  }
}
```

Non-permission keys in the config file (model, provider, mcp, etc.) are always preserved when writing.

## Error format

- ACP stdio errors (e.g. from `AcpStdioError`) are mapped to 503 with OpenAI-style body.
- Validation errors (empty messages) -> 400.
- Agent config write errors -> 403 (read-only) or 500 (I/O failure).
- Response body is always OpenAI-style: `{ "error": { "code", "message", "type" } }`.
