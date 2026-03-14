# Deployment

## Docker image (gateway only)

Build from the repository root:

```bash
docker build -t acp-gateway .
```

The Dockerfile uses `pip install -r requirements.txt`, copies the gateway package and `config.example.yaml` as `config.yaml`, and runs `python -m gateway.main`. Override config with `CONFIG_PATH` or env vars.

## Adding your own ACP server in the same container

To run the gateway and your ACP server in one container, the gateway must start the ACP process itself (as it does by default). So the image must contain both the gateway and the ACP server code/entrypoint.

Example pattern:

1. Use the gateway image as base, or a Python image with both gateway and your ACP app installed.
2. Set `acp.command` in config (or `ACP_COMMAND` in env) to start your ACP server, e.g. `["python", "-m", "my_acp_app"]`.
3. Set `acp.env` if needed (e.g. `PORT=8000`).
4. Set `gateway.acp_base_url` (or `GATEWAY_ACP_BASE_URL`) to the URL where your ACP server listens (e.g. `http://127.0.0.1:8000`).
5. Run the gateway as the container CMD; it will start the ACP process on startup.

Example Dockerfile (gateway + custom ACP in one image):

```dockerfile
FROM python:3.11-slim
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY gateway ./gateway
COPY config.example.yaml ./config.yaml

# Your ACP server
COPY my_acp /app/my_acp
RUN pip install -e /app/my_acp

ENV CONFIG_PATH=/app/config.yaml
EXPOSE 8080
CMD ["python", "-m", "gateway.main"]
```

Example `config.yaml` inside the image (or set `ACP_COMMAND` via env):

```yaml
acp:
  command: ["python", "-m", "uvicorn", "my_acp.app:create_app", "--host", "127.0.0.1", "--port", "8000"]
  env:
    PORT: "8000"
  startup_timeout_seconds: 60
gateway:
  host: "0.0.0.0"
  port: 8080
  acp_base_url: "http://127.0.0.1:8000"
```

## Docker Compose

Use the root `docker-compose.yaml`. Copy `.env.example` to `.env` and set variables; they are passed to the `gateway` service:

```bash
cp .env.example .env
docker compose up --build gateway
```

All options (`CONFIG_PATH`, `ACP_*`, `GATEWAY_*`) can be set in `.env`.

## Environment variables in the container

- **CONFIG_PATH** – Path to YAML inside the container (e.g. `/app/config.yaml`).
- **ACP_COMMAND**, **ACP_ENV**, **ACP_STARTUP_TIMEOUT_SECONDS** – ACP options.
- **GATEWAY_HOST**, **GATEWAY_PORT**, **GATEWAY_ACP_BASE_URL** – Gateway bind address/port and ACP URL.
</think>

<｜tool▁calls▁begin｜><｜tool▁call▁begin｜>
StrReplace