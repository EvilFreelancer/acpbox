# ACP lifecycle

How the gateway talks to the ACP agent over stdio (no HTTP).

## No global ACP process

The gateway does **not** start a long-lived ACP server. It does not call `/ping` or any HTTP endpoint on the agent. ACP uses **stdio** only: the agent is launched as a subprocess and communication is JSON-RPC over stdin/stdout (newline-delimited). See [Agent Client Protocol - Transports](agent-client-protocol/docs/protocol/transports.mdx).

## Per-request flow

For each `POST /v1/chat/completions` or `POST /v1/responses`:

1. **Spawn** – The gateway starts the ACP agent with `asyncio.create_subprocess_exec(*acp.command, env=..., cwd=acp.cwd)`.
2. **Initialize** – Send JSON-RPC `initialize` with protocol version and client capabilities; wait for result.
3. **Session** – Send `session/new` with `cwd` and empty `mcpServers`; receive `sessionId`.
4. **Prompt** – Send `session/prompt` with `sessionId` and prompt content blocks (from OpenAI messages). Read messages from stdout until the `session/prompt` response:
   - On `session/update` with `agent_message_chunk`: append text to the aggregated reply.
   - On `session/request_permission`: reply with `allow_once` so the agent can continue.
5. **Result** – When the `session/prompt` response is received, use aggregated text and `stopReason`.
6. **Terminate** – Terminate the subprocess (SIGTERM, then SIGKILL if needed).

Stderr from the agent is logged at debug level.

## GET /v1/models

No agent is started. The list of models is taken from config `acp.models` (default `["default"]`).

## Shutdown

There is no global ACP process to stop. Each request’s process is terminated at the end of the request.
