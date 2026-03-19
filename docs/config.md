# Configuration

**acpbox** is configured via a YAML file and/or environment variables. Env overrides YAML. Use `config.example.yaml` as a template. Every option can be set via env; see `.env.example`.

## YAML structure

```yaml
acp:
  command: ["opencode", "acp"]
  env: {}
  workspace: "./workspace"
gateway:
  host: "0.0.0.0"
  port: 8080
  workers: 1
  threads: 1
```

## Fields

### `acp`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `command` | list of strings | `["opencode", "acp"]` | Command to start the ACP agent (subprocess argv). Used for every request (including `GET /v1/models`); communication is over stdio. |
| `env` | map string -> string | `{}` | Extra environment variables for the ACP process. Merged over current env. |
| `workspace` | string | `./workspace` | Project directory for ACP `session/new` as `cwd` (resolved to absolute). Override with **`ACP_WORKSPACE`**. Docker default `/workspace` (see Dockerfile). |

**Models:** The list of "models" in `GET /v1/models` is **not** configured here. The gateway gets it from the agent: it spawns the agent, calls `initialize` and `session/new`, and uses `modes.availableModes[].id` (e.g. OpenCode returns `plan`, `build`) as the model list.

### `gateway`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `host` | string | `0.0.0.0` | Host to bind the gateway HTTP server. |
| `port` | integer | 8080 | Port for the gateway. |
| `workers` | integer | 1 | Uvicorn worker **processes** (minimum 1). One ACP agent process per worker. |
| `threads` | integer | 1 | Passed to **`uvicorn.run`** only if supported. Typical uvicorn ASGI builds have **no** `threads=` parameter (asyncio event loop per process). |

## Environment variables

| Env var | Section | Description |
|---------|---------|-------------|
| `CONFIG_PATH` | - | Path to YAML config. If unset, no file is loaded. |
| `ACP_COMMAND` | acp | JSON array of strings, e.g. `["opencode", "acp"]`. |
| `ACP_ENV` | acp | JSON object for extra env. |
| `ACP_WORKSPACE` | acp | Project directory for ACP `session/new` cwd. Default `./workspace`; Docker image sets `/workspace`. |
| `GATEWAY_HOST` | gateway | Host to bind. |
| `GATEWAY_PORT` | gateway | Port. |
| `GATEWAY_WORKERS` | gateway | Uvicorn worker process count. |
| `GATEWAY_THREADS` | gateway | Used only if **`uvicorn.run`** in your environment accepts **`threads`**. |

## Examples

**Minimal (env only):**

```bash
export ACP_COMMAND='["opencode","acp"]'
export ACP_WORKSPACE=./workspace
export GATEWAY_PORT=8080
acpbox
```

**File + override:**

```bash
CONFIG_PATH=config.yaml GATEWAY_PORT=9090 python -m acpbox.main
```

**Docker Compose** (reads `.env`):

```bash
cp .env.example .env
docker compose up --build acpbox
```
