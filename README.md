# ACP OpenAI API Gateway

OpenAI-compatible HTTP API that acts as a **gateway to the Agent Client Protocol (ACP)**. Clients use the usual OpenAI endpoints (`/v1/models`, `/v1/chat/completions`, `/v1/responses`); the gateway runs the API with **uvicorn** and keeps **one ACP agent process per worker** over **stdio** (JSON-RPC), not HTTP.

## Problem

Many tools and SDKs expect an OpenAI-style API. ACP agents (e.g. [OpenCode](https://github.com/sst/opencode) via `opencode acp`, or **Cursor Agent** via `agent acp`) speak the [Agent Client Protocol](https://agentclientprotocol.com) over stdin/stdout. This gateway provides a single HTTP entry point: one base URL, OpenAI-shaped API, with one ACP binary instance per uvicorn worker.

## How it works

1. **Config** – YAML and env define the agent command, env vars, and **workspace** (`ACP_WORKSPACE`, default `./workspace`; Docker `/workspace`) passed as ACP `session/new` `cwd`.
2. **One ACP process per worker** – The app runs under **uvicorn**. In lifespan each worker starts **one** ACP agent subprocess and keeps it for the worker's lifetime. With 8 workers you get 8 ACP binary instances. ACP uses **stdio** only (JSON-RPC, newline-delimited).
3. **Reuse per request** – Each request in that worker uses the same process: `session/new` -> optional `session/set_mode` -> `session/prompt`, then the response is returned. The process is not terminated after each request.
4. **Translation** – OpenAI requests are converted to ACP JSON-RPC; ACP content (e.g. `session/update` agent_message_chunk) is converted back to OpenAI chat/responses format.
   - `GET /v1/models` – Uses the worker's ACP process: `initialize` (once) and `session/new`, returns **modes** (`modes.availableModes[].id`, e.g. OpenCode's `plan`, `build`) as the list of models.
   - `POST /v1/chat/completions` – Uses the worker's ACP process; `model` selects the ACP mode. Reply as chat completion.
   - `POST /v1/responses` – Same; optional `chat_id` for client-side continuity.

See [docs/spec.md](docs/spec.md) and [docs/agent-client-protocol/docs/protocol/transports.mdx](docs/agent-client-protocol/docs/protocol/transports.mdx) for details.

```mermaid
sequenceDiagram
    participant Client
    participant Gateway
    participant AgentProcess

    Note over Gateway,AgentProcess: One ACP process per uvicorn worker (started in lifespan)
    Client->>Gateway: GET /v1/models
    Gateway->>AgentProcess: initialize, session/new (reuse process)
    AgentProcess-->>Gateway: modes (e.g. plan, build)
    Gateway-->>Client: list of models

    Client->>Gateway: POST /v1/chat/completions
    Gateway->>AgentProcess: session/new, session/prompt (same process)
    AgentProcess-->>Gateway: session/update, session/prompt result
    Gateway-->>Client: chat completion
```

## Quick setup

1. **Config** – Copy `config.example.yaml` to `config.yaml` and adjust. Every option can also be set via environment (see `.env.example`).

2. **Env** – Copy `.env.example` to `.env` and set values. All options (`CONFIG_PATH`, `ACP_*`, `GATEWAY_*`) can be configured via env.

   **Agent command (OpenCode vs Cursor)** – set `acp.command` in `config.yaml` or `ACP_COMMAND` as a JSON array of strings.

   | Backend | `config.yaml` | Shell (env) |
   |---------|---------------|-------------|
   | OpenCode | `command: ["opencode", "acp"]` | `export ACP_COMMAND='["opencode","acp"]'` |
   | Cursor Agent | `command: ["agent", "acp"]` | `export ACP_COMMAND='["agent","acp"]'` |

   Use an absolute path if the binary is not on `PATH` (e.g. `["/home/you/.local/bin/agent","acp"]`). Cursor Agent must be installed and logged in (`agent login`) so the subprocess can reach your account.

3. **Run** – From the repo root. The app uses **uvicorn** (listed in `requirements.txt`). Default is a single worker (one ACP instance). For production, run uvicorn with `--workers N` to get N ACP agent processes (one per worker):

```bash
pip install -r requirements.txt
CONFIG_PATH=config.yaml python -m gateway.main
# Or explicitly with more workers (e.g. 8 workers = 8 ACP binary instances):
# uvicorn gateway.main:create_app --factory --host 0.0.0.0 --port 8080 --workers 8
```

Or with Docker Compose (reads `.env` and runs the `gateway` service). Set **`AGENTS`** in `.env` for the image build (comma-separated `opencode`, `cursor`); the compose file passes it as a build-arg. After changing `AGENTS`, run `docker compose build --no-cache gateway` so installers run again. Runtime **`ACP_COMMAND`** must match the installed binary (see Agent command table above). The Dockerfile runs the app via `python -m gateway.main`, which starts uvicorn with one worker by default; override the command to use more workers if needed.

4. **Use** – Point any OpenAI client at `http://localhost:8080/v1` (or your host/port). List models, call chat completions or responses; the gateway translates to ACP and back.

## Tests

Tests use a mock ACP over stdio (fake subprocess that responds with JSON-RPC). Route tests: `tests/test_models.py`, `tests/test_chat.py`, `tests/test_responses.py`, `tests/test_sessions.py`. Unit tests for mapping, errors, session_store, config, and stdio client in `tests/unit/`. Fixtures in `tests/conftest.py`.

From repo root:

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

## Adding your own ACP in Docker

Build an image that includes the gateway and your ACP agent binary (e.g. `opencode acp`). Set `acp.command` and `acp.env` in config or `.env`. The server runs with **uvicorn**; each worker starts one ACP process in lifespan. To run 8 ACP instances, use `uvicorn ... --workers 8`. See [docs/deployment.md](docs/deployment.md).

## Specifications

- [docs/spec.md](docs/spec.md) – This gateway: OpenAI HTTP API to Agent Client Protocol (stdio).
- [OpenAI API OpenAPI spec](https://github.com/openai/openai-openapi/tree/manual_spec) – OpenAI REST API specification (OpenAPI).
- [Agent Client Protocol](https://agentclientprotocol.com) – Protocol for agent-client communication over stdio (JSON-RPC); see `docs/agent-client-protocol/`.

## License

This project is licensed under the MIT License, see the [LICENSE](LICENSE) file in the repository root for details.
