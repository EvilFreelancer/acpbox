# Configuration

The gateway is configured via a YAML file and/or environment variables. Env overrides YAML. Use `config.example.yaml` as a template. Every option can be set via env; see `.env.example`.

## YAML structure

```yaml
acp:
  command: ["opencode", "acp"]
  env: {}
  models: ["default"]
  cwd: null
gateway:
  host: "0.0.0.0"
  port: 8080
```

## Fields

### `acp`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `command` | list of strings | `["opencode", "acp"]` | Command to start the ACP agent (subprocess argv). Runs once per chat/responses request; communication is over stdio. |
| `env` | map string -> string | `{}` | Extra environment variables for the ACP process. Merged over current env. |
| `models` | list of strings | `["default"]` | Model ids returned by `GET /v1/models`. No agent is started for listing. |
| `cwd` | string or null | null | Working directory for `session/new`. If null, the gateway process cwd is used. |

### `gateway`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `host` | string | `0.0.0.0` | Host to bind the gateway HTTP server. |
| `port` | integer | 8080 | Port for the gateway. |

## Environment variables

| Env var | Section | Description |
|---------|---------|-------------|
| `CONFIG_PATH` | - | Path to YAML config. If unset, no file is loaded. |
| `ACP_COMMAND` | acp | JSON array of strings, e.g. `["opencode", "acp"]`. |
| `ACP_ENV` | acp | JSON object for extra env. |
| `ACP_MODELS` | acp | JSON array of model ids, e.g. `["default"]`. |
| `ACP_CWD` | acp | Working directory for session/new (optional). |
| `GATEWAY_HOST` | gateway | Host to bind. |
| `GATEWAY_PORT` | gateway | Port. |

## Examples

**Minimal (env only):**

```bash
export ACP_COMMAND='["opencode","acp"]'
export GATEWAY_PORT=8080
python -m gateway.main
```

**File + override:**

```bash
CONFIG_PATH=config.yaml GATEWAY_PORT=9090 python -m gateway.main
```

**Docker Compose** (reads `.env`):

```bash
cp .env.example .env
docker compose up --build gateway
```
