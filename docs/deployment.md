# Deployment

## Docker image (gateway only)

Build from the repository root:

```bash
docker build -t acp-gateway .
```

The Dockerfile uses `pip install -r requirements.txt` (includes **uvicorn**), copies the gateway package and `config.example.yaml` as `config.yaml`, and runs `python -m gateway.main` (which starts uvicorn). Override config with `CONFIG_PATH` or env vars. **One ACP process per worker**: default CMD uses one worker; for N ACP instances override CMD to run `uvicorn ... --workers N`.

## Adding your own ACP agent in the same container

The gateway talks to the ACP agent over **stdio**. Each **uvicorn worker** starts one ACP process in lifespan and reuses it for all requests. The image must include the agent binary or interpreter.

Example pattern:

1. Use the gateway image as base, or a Python image with the gateway and your ACP agent installed.
2. Set `acp.command` (or `ACP_COMMAND`) to the agent command, e.g. `["opencode", "acp"]` or `["python", "-m", "my_agent"]`.
3. Set `acp.env` and optionally `acp.cwd` if needed.
4. The list of models in `GET /v1/models` comes from the agent (session/new -> modes.availableModes), not from config.
5. Run the gateway as the container CMD. Use **uvicorn** with `--workers N` if you want N ACP binary instances (e.g. `CMD ["uvicorn", "gateway.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--workers", "8"]`).

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
# One worker = one ACP process. For 8 ACP instances: CMD ["uvicorn", "gateway.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--workers", "8"]
CMD ["python", "-m", "gateway.main"]
```

Example `config.yaml`:

```yaml
acp:
  command: ["opencode", "acp"]
  env: {}
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
- **ACP_COMMAND**, **ACP_ENV**, **ACP_CWD** – ACP agent command, env, working directory.
- **GATEWAY_HOST**, **GATEWAY_PORT** – Gateway bind address and port.
