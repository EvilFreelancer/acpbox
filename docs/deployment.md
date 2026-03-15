# Deployment

## Docker image (gateway only)

Build from the repository root:

```bash
docker build -t acp-gateway .
```

The Dockerfile uses `pip install -r requirements.txt`, copies the gateway package and `config.example.yaml` as `config.yaml`, and runs `python -m gateway.main`. Override config with `CONFIG_PATH` or env vars.

## Adding your own ACP agent in the same container

The gateway talks to the ACP agent over **stdio** (one process per request). The image must include the agent binary or interpreter.

Example pattern:

1. Use the gateway image as base, or a Python image with the gateway and your ACP agent installed.
2. Set `acp.command` (or `ACP_COMMAND`) to the agent command, e.g. `["opencode", "acp"]` or `["python", "-m", "my_agent"]`.
3. Set `acp.env` and optionally `acp.cwd` if needed.
4. Set `acp.models` to the list of model ids to expose in `GET /v1/models`.
5. Run the gateway as the container CMD. No global ACP process is started; the gateway spawns the agent per request.

Example Dockerfile (gateway + OpenCode ACP in one image):

```dockerfile
FROM python:3.11-slim
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY gateway ./gateway
COPY config.example.yaml ./config.yaml

# ACP agent (e.g. opencode)
RUN npm install -g opencode  # or install your agent

ENV CONFIG_PATH=/app/config.yaml
EXPOSE 8080
CMD ["python", "-m", "gateway.main"]
```

Example `config.yaml`:

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

## Docker Compose

Use the root `docker-compose.yaml`. Copy `.env.example` to `.env` and set variables:

```bash
cp .env.example .env
docker compose up --build gateway
```

All options (`CONFIG_PATH`, `ACP_*`, `GATEWAY_*`) can be set in `.env`.

## Environment variables in the container

- **CONFIG_PATH** – Path to YAML inside the container (e.g. `/app/config.yaml`).
- **ACP_COMMAND**, **ACP_ENV**, **ACP_MODELS**, **ACP_CWD** – ACP agent command, env, model list, working directory.
- **GATEWAY_HOST**, **GATEWAY_PORT** – Gateway bind address and port.
