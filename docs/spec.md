# ACP Gateway Specification

## Purpose

This project is an **ACP (Agent Client Protocol) Gateway**: it exposes an OpenAI-compatible HTTP API and translates requests to the [Agent Client Protocol](https://agentclientprotocol.com) over **stdio**. Clients (IDEs, scripts, OpenAI SDKs) call the gateway as if it were an OpenAI API; the gateway runs ACP-compatible agents as subprocesses and speaks JSON-RPC with them over stdin/stdout.

## Agent Client Protocol (ACP)

ACP is a standard for communication between AI agents and clients. Key facts:

- **Transport**: The protocol uses **stdio** (standard input/output). The client launches the agent as a subprocess; messages are JSON-RPC 2.0, UTF-8, newline-delimited. There is no HTTP in the base ACP transport (streamable HTTP is a draft).
- **Flow**: Client sends `initialize` -> Agent responds with capabilities and `agentInfo` -> Client sends `session/new` (cwd, MCP servers) -> Agent returns `sessionId` -> Client sends `session/prompt` (sessionId, prompt content blocks) -> Agent streams `session/update` notifications and finally responds to `session/prompt` with `stopReason`.
- **References**: See `docs/agent-client-protocol/docs/protocol/` for transports, initialization, session-setup, prompt-turn, content.

## Gateway Architecture

- **One agent process per request**: Each HTTP request that needs an agent (e.g. `POST /v1/chat/completions`, `POST /v1/responses`) spawns a new ACP agent subprocess, performs the full ACP handshake (initialize, session/new, session/prompt), collects the reply from `session/update` and the `session/prompt` response, then terminates the process. This matches the stdio model (one process, one connection) and works with any ACP agent (e.g. [OpenCode](https://github.com/sst/opencode) via `opencode acp`).
- **Models list**: `GET /v1/models` does not spawn an agent. The list of "models" is taken from configuration (`acp.models`) or, if empty, a single default name derived from the agent command. This avoids starting an agent only to list capabilities.
- **No global ACP process**: The gateway does not start a long-lived ACP server or poll `/ping`. All interaction is per-request over stdio.

## API Mapping (summary)

| OpenAI endpoint | Gateway behavior |
|-----------------|------------------|
| `GET /v1/models` | Return list from config `acp.models` (or default one). |
| `GET /v1/models/{id}` | Return model if id is in configured list; else 404. |
| `POST /v1/chat/completions` | Spawn agent -> initialize -> session/new -> session/prompt (messages as ACP prompt) -> aggregate agent text from session/update -> return OpenAI chat completion. |
| `POST /v1/responses` | Same as above; optional `chat_id` is stored and returned for client continuity but each request still uses a new agent process (no in-agent session persistence across requests). |
| `DELETE /v1/responses/{id}`, `DELETE /v1/sessions/{id}` | Gateway-only session store; no ACP call. |

## Conventions

- Comments in code are in English.
- Specs and user-facing docs may be in any language as needed.
