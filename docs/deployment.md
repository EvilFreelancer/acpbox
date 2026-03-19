# Deployment

## Docker image (acpbox only)

Build from the repository root:

```bash
docker build -t acpbox .
```

Optional **build-arg** `AGENTS` installs ACP binaries into the image (comma-separated, case-insensitive): `opencode` (official install script from [opencode.ai/install](https://opencode.ai/install), binary under `/home/user/.opencode/bin`), `cursor` (official script from [cursor.com/install](https://cursor.com/install), `agent` under `/home/user/.local/bin`). The image runs **acpbox** as user **`user`** (UID 1000, GID 1000); agents install into that home directory. Examples:

```bash
docker build -t acpbox --build-arg AGENTS=opencode .
docker build -t acpbox --build-arg AGENTS=cursor .
docker build -t acpbox --build-arg AGENTS=opencode,cursor .
```

With Docker Compose, set `AGENTS` in `.env`; `docker-compose.yaml` passes it as `build.args`. At **runtime**, set `ACP_COMMAND` to match, e.g. `["opencode","acp"]` or `["agent","acp"]`, and provide credentials (OpenCode auth data, `CURSOR_API_KEY` or mounted Cursor config) as required by each agent.

If `AGENTS` is empty, the image contains only the Python **acpbox** package; install an agent in a derived image or bind-mount a binary.

The Dockerfile runs **`pip install .`** (includes **uvicorn**), copies the repository, and sets **CMD** to **`acpbox`**, which calls **`uvicorn`** from **`acpbox.main.run`**. Override config with `CONFIG_PATH` or env vars. **One ACP process per worker** - set **`GATEWAY_WORKERS`** (or **`gateway.workers`** in YAML) for N parallel ACP processes.

## Adding your own ACP agent in the same container

**acpbox** talks to the ACP agent over **stdio**. Each **uvicorn worker** starts one ACP process in lifespan and reuses it for all requests. The image must include the agent binary or interpreter.

Example pattern:

1. Use the **acpbox** image as base, or a Python image with **acpbox** and your ACP agent installed.
2. Set `acp.command` (or `ACP_COMMAND`) to the agent command, e.g. `["opencode", "acp"]` or `["python", "-m", "my_agent"]`.
3. Set `acp.env` and optionally `acp.workspace` (**`ACP_WORKSPACE`**) if needed.
4. The list of models in `GET /v1/models` comes from the agent (session/new -> modes.availableModes), not from config.
5. Run **acpbox** as the container CMD (image default). Set **`GATEWAY_WORKERS=N`** (or **`gateway.workers`**) for N ACP binary instances.

Example Dockerfile (**acpbox** + OpenCode ACP in one image):

```dockerfile
FROM python:3.11-slim
WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY acpbox ./acpbox
RUN pip install --no-cache-dir .

# ACP agent (e.g. opencode)
RUN npm install -g opencode  # or install your agent

ENV CONFIG_PATH=/app/config.yaml
EXPOSE 8080
ENV GATEWAY_WORKERS=1
CMD ["acpbox"]
```

Example `config.yaml`:

```yaml
acp:
  command: ["opencode", "acp"]
  env: {}
  workspace: "./workspace"
gateway:
  host: "0.0.0.0"
  port: 8080
  workers: 1
```

## Docker Compose

Use the root `docker-compose.yaml`. Copy `.env.example` to `.env` and set variables:

```bash
cp .env.example .env
docker compose up --build acpbox
```

All options (`CONFIG_PATH`, `ACP_*`, `GATEWAY_*`) can be set in `.env`.

The compose file mounts **`./workspace` from the host to `/workspace` in the container** and sets **`ACP_WORKSPACE=/workspace`** so ACP `session/new` uses that directory as the agent project root. The repo `workspace/` directory ships a `.gitignore` (`*`, `!.gitignore`) so the folder can be tracked in git without committing local project files. The Dockerfile creates `/workspace` at build time and sets **`ENV ACP_WORKSPACE=/workspace`** for non-Compose runs.

## Bind-mounting OpenCode and Cursor data (Compose)

The **acpbox** container runs as **`user`** (UID 1000, GID 1000), so in-container paths for OpenCode and Cursor data are under `/home/user`.

### OpenCode

| On the host (typical Linux) | In the container | Purpose |
|-----------------------------|------------------|---------|
| `~/.config/opencode` | `/home/user/.config/opencode` | Global config (for example `opencode.json`, editor-related settings). Match `OPENCODE_CONFIG_DIR` if you override it on the host. |
| `~/.local/share/opencode` | `/home/user/.local/share/opencode` | Provider auth (`auth.json`), sessions, caches. See OpenCode docs for `opencode auth login`. |

1. In `.env`, set absolute paths, for example `OPENCODE_CONFIG_HOST=/home/you/.config/opencode` and `OPENCODE_DATA_HOST=/home/you/.local/share/opencode`.
2. Uncomment the matching lines under `volumes` in `docker-compose.yaml`.
3. Use `:ro` on the config mount if the container must not change your global config; keep the data mount **read-write** if providers refresh tokens.

The OpenCode binary in the image lives under `/home/user/.opencode/bin`, so these mounts do not replace the CLI.

### Cursor Agent

| Method | Notes |
|--------|-------|
| **`CURSOR_API_KEY` in `.env`** | Best default for Docker. Passed into the container via `env_file` and the `environment` section in `docker-compose.yaml`. See Cursor CLI [authentication](https://docs.cursor.com/en/cli/reference/authentication) and [headless](https://cursor.com/docs/cli/headless) docs. |
| **Bind-mount `~/.cursor`** | Optional. Host path → `/home/user/.cursor` in the container. Useful for `mcp.json`, rules, or when you rely on **`agent login`** on the host instead of an API key. Set `CURSOR_DOT_CURSOR_HOST` in `.env`, uncomment the volume in `docker-compose.yaml`. Use read-write if the CLI must update auth files. |

**Do not** bind-mount the host directory `~/.local/share/cursor-agent` onto `/home/user/.local/share/cursor-agent` when the Cursor CLI is **installed inside the image**. The Dockerfile install layout places the agent binary and versioned files there; a host mount would hide that tree and break the `agent` entrypoint.

## Environment variables in the container

- **CONFIG_PATH** – Path to YAML inside the container (e.g. `/app/config.yaml`).
- **ACP_COMMAND**, **ACP_ENV**, **ACP_WORKSPACE** – ACP agent command, env, project directory for `session/new` (ACP `cwd`).
- **CURSOR_API_KEY** – Optional. Cursor Agent API key for headless use inside the container.
- **GATEWAY_HOST**, **GATEWAY_PORT** – Gateway bind address and port.
- **GATEWAY_WORKERS**, **GATEWAY_THREADS** – Uvicorn worker processes and optional **`threads=`** when supported (see [config.md](config.md)).
