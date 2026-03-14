# ACP lifecycle

How the gateway starts, checks, and stops the ACP server.

## Start

1. **Load config** – On application startup, config is loaded from `CONFIG_PATH` (YAML) and env.
2. **Run process** – The gateway runs the ACP server as a subprocess via `asyncio.create_subprocess_exec(*acp.command, env=os.environ | acp.env)`. Stdout/stderr are captured and logged line-by-line in the gateway process.
3. **Wait for ready** – The gateway polls `GET {gateway.acp_base_url}/ping` until the response status is 200 or `startup_timeout_seconds` elapses. If the process exits before that or the timeout is reached, startup fails (the gateway does not start serving).
4. **Serving** – After /ping succeeds, the gateway starts accepting HTTP requests and forwards them to the ACP server at `gateway.acp_base_url`.

## Requirements for the ACP server

- **HTTP server** – Must listen on the host/port implied by `gateway.acp_base_url` (e.g. `http://127.0.0.1:8000`). The gateway does not bind the ACP port; the ACP process does.
- **Endpoints** – Must implement at least:
  - `GET /ping` – returns 200 (body optional).
  - `GET /agents` – list agents (used for `/v1/models`).
  - `GET /agents/{name}` – agent manifest (used for `/v1/models/{model}`).
  - `POST /runs` – create run with `agent_name`, `input` (list of messages), optional `session_id`, `mode` (e.g. "sync").
- **Stateful** – For `/v1/responses` the gateway sends `session_id` (UUID string). The ACP server should support stateful runs (session history) when `session_id` is present, as per ACP spec.

Port for ACP can be fixed in the ACP app or set via `acp.env` (e.g. `PORT=8000`). The gateway only needs to know the final `acp_base_url` to call /ping and the API.

## Shutdown

- On gateway shutdown (SIGTERM or process exit), the gateway calls `process.terminate()` on the ACP subprocess.
- It then waits up to 5 seconds for the process to exit.
- If it does not exit, the gateway sends SIGKILL and waits again.
- The HTTP client used for ACP requests is closed as part of the FastAPI lifespan.

## Virtual interface / localhost

The plan allowed for the ACP server to run on a virtual interface or localhost. The implementation uses localhost by default (`gateway.acp_base_url: http://127.0.0.1:8000`). The ACP process is started with the same env as the gateway (plus `acp.env`); the command can bind to `127.0.0.1` or `0.0.0.0` as required. No virtual network interface is configured by the gateway.
