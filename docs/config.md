# Configuration

The gateway is configured via a YAML file and/or environment variables. Env overrides YAML. Use `config.example.yaml` as a template (copy to `config.yaml`). Every option can be set via env; see `.env.example` for the list.

## YAML structure

See `config.example.yaml` in the repo root. Example:

```yaml
acp:
  command: ["python", "-m", "uvicorn", "acp_sdk.server.app:create_app", "--host", "127.0.0.1", "--port", "8000"]
  env: {}
  startup_timeout_seconds: 30
gateway:
  host: "0.0.0.0"
  port: 8080
  acp_base_url: "http://127.0.0.1:8000"
```

## Fields

### `acp`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `command` | list of strings | (see below) | Command to start the ACP process (subprocess argv). |
| `env` | map string -> string | `{}` | Extra environment variables for the ACP process. Merged over current env. |
| `startup_timeout_seconds` | integer | 30 | Seconds to wait for `GET {acp_base_url}/ping` to return 200 before failing startup. |

Default `command` if omitted:

```yaml
["python", "-m", "uvicorn", "acp_sdk.server.app:create_app", "--host", "127.0.0.1", "--port", "8000"]
```

### `gateway`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `host` | string | `0.0.0.0` | Host to bind the gateway HTTP server. |
| `port` | integer | 8080 | Port for the gateway. |
| `acp_base_url` | string | `http://127.0.0.1:8000` | Base URL where the ACP server listens. Used for /ping and all ACP API calls. No trailing slash. |

## Environment variables

Every option can be set via env. Copy `.env.example` to `.env` and edit.

| Env var | Section | Description |
|---------|---------|-------------|
| `CONFIG_PATH` | - | Path to YAML config. If unset, no file is loaded (env and defaults only). |
| `ACP_COMMAND` | acp | JSON array of strings, e.g. `["python","-m","uvicorn",...]`. |
| `ACP_ENV` | acp | JSON object for extra env passed to ACP process, e.g. `{}`. |
| `GATEWAY_ACP_BASE_URL` | gateway | Base URL where ACP server listens. |
| `ACP_STARTUP_TIMEOUT_SECONDS` | acp | Seconds to wait for /ping. |
| `GATEWAY_HOST` | gateway | Host to bind. |
| `GATEWAY_PORT` | gateway | Port for the gateway. |

## pydantic-settings

The app uses `pydantic-settings`. Nested models use `env_prefix`: `ACP_` for acp, `GATEWAY_` for gateway. `ACP_COMMAND` and `ACP_ENV` are parsed from JSON strings when set via env.

## Examples

**Minimal (env only):**

```bash
export GATEWAY_ACP_BASE_URL=http://127.0.0.1:9000
export GATEWAY_PORT=8080
python -m gateway.main
```

**File + env override:**

Copy `config.example.yaml` to `config.yaml`, then:

```bash
CONFIG_PATH=config.yaml GATEWAY_PORT=9090 python -m gateway.main
```

**Docker Compose** (reads `.env`):

```bash
cp .env.example .env
docker compose up --build gateway
```
