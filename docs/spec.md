# ACP Gateway Specification

## Purpose

This project is an **ACP (Agent Client Protocol) Gateway**: it exposes an OpenAI-compatible HTTP API and translates requests to the [Agent Client Protocol](https://agentclientprotocol.com) over **stdio**. Clients (IDEs, scripts, OpenAI SDKs) call the gateway as if it were an OpenAI API; the gateway runs ACP-compatible agents as subprocesses and speaks JSON-RPC with them over stdin/stdout.

The gateway now uses the official Python SDK `agent-client-protocol` for ACP schema models and helpers. Content blocks for `session/prompt` are built via SDK helpers (for example `acp.text_block`) and validated against `acp.schema` so payloads stay in sync with upstream ACP releases.

## Agent Client Protocol (ACP)

ACP is a standard for communication between AI agents and clients. Key facts:

- **Transport**: The protocol uses **stdio** (standard input/output). The client launches the agent as a subprocess; messages are JSON-RPC 2.0, UTF-8, newline-delimited. There is no HTTP in the base ACP transport (streamable HTTP is a draft).
- **Flow**: Client sends `initialize` -> Agent responds with capabilities and `agentInfo` -> Client sends `session/new` (JSON field `cwd`, MCP servers) -> Agent returns `sessionId` -> Client sends `session/prompt` (sessionId, prompt content blocks) -> Agent streams `session/update` notifications and finally responds to `session/prompt` with `stopReason`. The gateway sets `cwd` from config **`workspace`** (env **`ACP_WORKSPACE`**): default `./workspace` on the host, `/workspace` in the Docker image (see Dockerfile and compose).
- **References**: See `docs/agent-client-protocol/docs/protocol/` for transports, initialization, session-setup, prompt-turn, content.

## Gateway Architecture

- **One ACP process per uvicorn worker**: The API runs under **uvicorn**. In FastAPI lifespan each worker starts **one** ACP agent subprocess and keeps it for the worker's lifetime. With 8 workers you get 8 ACP binary instances. All requests in that worker reuse the same process (session/new + session/prompt per request; the process is not terminated after each request). ACP uses stdio only (JSON-RPC); no HTTP to the agent.
- **Workspace**: `acp.workspace` / **`ACP_WORKSPACE`** is resolved to an absolute path and sent as the ACP `session/new` **`cwd`** (project root for the agent). Defaults: `./workspace` (repo dir `workspace/` with a `.gitignore` that ignores all files except itself); Docker uses **`/workspace`** and Compose mounts `./workspace` there.
- **Models list**: `GET /v1/models` uses the worker's ACP process: `initialize` (once per process) and `session/new`, reads **agent modes** from the response: `modes.availableModes[].id` (e.g. OpenCode returns `plan`, `build`). These mode ids are exposed as OpenAI "models". If the agent does not report modes, the gateway uses `agentInfo.name` from `initialize` or `"default"`. On agent failure, the gateway returns 503.
- **Dependencies**: The app is served with **uvicorn** (in `requirements.txt`). For production, run with `uvicorn ... --workers N` to get N ACP instances.

## API Mapping (summary)

| OpenAI endpoint | Gateway behavior |
|-----------------|------------------|
| `GET /v1/models` | Use worker's ACP process: initialize (if needed), session/new -> `modes.availableModes`; return those ids as models. 503 if agent fails. |
| `GET /v1/models/{id}` | Same; return one model if id is in the agent's mode list; else 404. |
| `POST /v1/chat/completions` | Use worker's ACP process: session/new -> optional session/set_mode -> session/prompt -> aggregate agent text from session/update -> return OpenAI chat completion. |
| `POST /v1/responses` | Same as above; optional `chat_id` for client continuity (each request still uses a new session on the same process). |
| `DELETE /v1/responses/{id}`, `DELETE /v1/sessions/{id}` | Gateway-only session store; no ACP call. |

## Conventions

- Comments in code are in English.
- Specs and user-facing docs may be in any language as needed.
