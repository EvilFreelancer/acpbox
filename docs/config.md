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
| `workspace` | string | `./workspace` | Project directory for ACP `session/new` as `cwd` (resolved to absolute). Override with **`ACPBOX_ACP_WORKSPACE`**. Docker default `/workspace` (see Dockerfile). |

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
| `ACPBOX_CONFIG_PATH` | - | Path to YAML config. If empty or unset, no file is loaded. |
| `ACPBOX_ACP_COMMAND` | acp | JSON array of strings, e.g. `["opencode", "acp"]`. If empty or unset, the gateway starts without an agent. |
| `ACPBOX_ACP_ENV` | acp | JSON object for extra env. |
| `ACPBOX_ACP_WORKSPACE` | acp | Project directory for ACP `session/new` cwd. Default `./workspace`; Docker image and compose use `/workspace`. |
| `ACPBOX_GATEWAY_HOST` | gateway | Host to bind. |
| `ACPBOX_GATEWAY_PORT` | gateway | Port. |
| `ACPBOX_GATEWAY_WORKERS` | gateway | Uvicorn worker process count. |
| `ACPBOX_GATEWAY_THREADS` | gateway | Used only if **`uvicorn.run`** in your environment accepts **`threads`**. |

## Examples

**Minimal (env only):**

```bash
export ACPBOX_ACP_COMMAND='["opencode","acp"]'
export ACPBOX_ACP_WORKSPACE=./workspace
export ACPBOX_GATEWAY_PORT=8080
acpbox
```

**File + override:**

```bash
ACPBOX_CONFIG_PATH=config.yaml ACPBOX_GATEWAY_PORT=9090 python -m acpbox.main
```

**Docker Compose** (reads `.env`):

```bash
cp .env.example .env
docker compose up --build acpbox
```
