# ACP lifecycle

How the gateway talks to the ACP agent over stdio (no HTTP). **One ACP process per uvicorn worker** (started in lifespan, reused for all requests in that worker).

## One process per worker

The API runs under **uvicorn**. Each worker process has its own FastAPI app and its own lifespan. In lifespan the gateway starts **one** ACP agent subprocess (`asyncio.create_subprocess_exec(*acp.command, ...)`) and stores it in `app.state.runner`. With 8 workers you get 8 ACP binary instances. The process is not terminated after each request; it is reused for the next request in the same worker. On worker shutdown, the gateway calls `runner.stop()` to terminate the ACP process.

ACP uses **stdio** only: the agent is launched as a subprocess and communication is JSON-RPC over stdin/stdout (newline-delimited). The gateway relies on the `agent-client-protocol` Python SDK for ACP schema models and helpers when constructing `session/prompt` content blocks, while the stdio transport remains a simple JSON-RPC loop in the gateway.

## Per-request flow (same process)

For each `POST /v1/chat/completions` or `POST /v1/responses` in that worker:

1. **Lock** – The runner uses an asyncio lock so only one request at a time uses the process (stdio is single-stream).
2. **Initialize** – If not yet done, send JSON-RPC `initialize`; wait for result.
3. **Session** – Send `session/new` with `cwd` and empty `mcpServers`; receive `sessionId`.
4. **Mode** – If the request has a `model` (mode id), send `session/set_mode` with that `modeId`.
5. **Prompt** – Send `session/prompt` with `sessionId` and prompt content blocks (from OpenAI messages). Read messages from stdout until the `session/prompt` response:
   - On `session/update` with `agent_message_chunk`: append text to the aggregated reply.
   - On `session/request_permission`: reply with `allow_once` so the agent can continue.
6. **Result** – When the `session/prompt` response is received, use aggregated text and `stopReason`. The process stays alive for the next request.

Stderr from the agent is logged at debug level.

## GET /v1/models

Uses the worker's ACP process: `initialize` (if not yet done) and `session/new`, then reads **modes.availableModes** from the `session/new` result. The `id` of each mode (e.g. `plan`, `build` for OpenCode) is returned as an OpenAI model. If the agent does not report modes, the gateway uses the agent name from `initialize` or `"default"`. If the agent fails, the gateway returns 503.

## Shutdown

When the worker shuts down (e.g. uvicorn receives SIGTERM), the lifespan context exits and the gateway calls `runner.stop()`: it sends SIGTERM to the ACP process, then SIGKILL if it does not exit within a few seconds.
