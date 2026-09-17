# ACPBox - Agent Brief

ACPBox is an OpenAI-compatible HTTP gateway (FastAPI + uvicorn) for Agent Client Protocol (ACP) agents. Each uvicorn worker spawns one ACP agent subprocess (OpenCode `opencode acp`, Cursor `agent acp`, Claude `claude-agent-acp`, Codex `codex-acp`) and talks newline-delimited JSON-RPC 2.0 to it over stdio. Clients call `/v1/models`, `/v1/chat/completions` (JSON or SSE), and `/v1/responses`; `/v1/agent/*` manages agent permissions by editing the agent's native config file inside the workspace.

`CLAUDE.md` is a symlink to this file. Edit `AGENTS.md` only.

## Repository Map

- `acpbox/` - the package:
  - `config.py`, `schemas.py`, `session_store.py`, `acp_stdio.py`, `agents/base.py` - foundation layer, no project imports;
  - `errors.py`, `mapping.py`, `agents/<agent>.py` - translation and agent config adapters;
  - `agents/__init__.py` - agent registry and detection by command basename;
  - `routes/` - thin FastAPI routers (models, chat, responses and sessions, agent_config);
  - `main.py`, `cli.py` - app factory, lifespan with the per-worker `AcpRunner`, uvicorn entrypoint.
- `tests/` - `conftest.py` (`MockRunner`, `app`, `client`), route tests `test_*.py`, pure unit tests in `tests/unit/`.
- `docs/` - `spec.md`, `api-mapping.md`, `acp-lifecycle.md`, `config.md`, `deployment.md`. `docs/agent-client-protocol` and `docs/openai-openapi` are upstream spec checkouts; do not edit them.
- Operator files - `config.example.yaml`, `.env.example`, `Dockerfile`, `docker-compose.yaml`, `entrypoint.sh`.
- `workspace/` - default ACP `cwd` for local runs; its contents are gitignored.

## Commands

```bash
# Setup (Python >= 3.11)
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

# Full test suite, as CI runs it
.venv/bin/pytest tests/ -v -m "not integration" --cov=acpbox --cov-report=term-missing

# Run the gateway locally
cp config.example.yaml config.yaml
.venv/bin/acpbox --config ./config.yaml

# Docker
cp .env.example .env
docker compose up --build acpbox
```

No linter or `pre-commit` is configured yet.

## Non-Negotiables

- **BDD/TDD.** New behavior and bug fixes start with a failing test that describes the observable outcome; the full suite is green before work is reported.
- **Layered cake.** Implement bottom-up: foundation, then translation and adapters, then registry, then routes, then composition. Routes stay thin; ACP protocol details stay in `acp_stdio.py`.
- **Contract.** OpenAI-shaped responses are a public API; errors are always `{"error": {"code", "message", ...}}`.
- **No real agents in tests.** Use `MockRunner`, `monkeypatch`, `tmp_path`, or a fake stdio agent script. No network, no API keys.
- **Docs in the same change.** Behavior, config, or agent support changes update `docs/`, README, and operator samples together.
- **English** for code comments and every agent rule file.
- **No secrets** in the repository; keys live in `.env` or the environment.
- **Releases are automatic.** Merging a PR into `main` tags the next patch version and publishes to PyPI and Docker Hub. The version comes from git tags; never set it by hand.

## Detailed Rules

`.cursor/rules/` is the source of truth; `.claude/rules/` mirrors it with identical bodies; Codex receives the Cursor files through the hook bridge in `.codex/`.

| Topic | Cursor | Claude Code | Attachment |
|---|---|---|---|
| Workflow (BDD/TDD, docs sync, final checks, Rules Sync) | `.cursor/rules/workflow.mdc` | `.claude/rules/workflow.md` | always |
| Testing (layout, fixtures, fakes, isolation) | `.cursor/rules/testing.mdc` | `.claude/rules/testing.md` | always |
| Architecture (runtime invariants, layers, boundaries) | `.cursor/rules/architecture.mdc` | `.claude/rules/architecture.md` | always |
| Code style (typing, async, errors, language) | `.cursor/rules/code-style.mdc` | `.claude/rules/code-style.md` | always |
| Implementation order (layer by layer) | `.cursor/rules/implementation-order.mdc` | `.claude/rules/implementation-order.md` | `acpbox/**/*.py` |
| API layer (routes, error codes, SSE) | `.cursor/rules/api-layer.mdc` | `.claude/rules/api-layer.md` | `acpbox/routes/**/*.py`, `acpbox/main.py`, `acpbox/schemas.py`, `acpbox/errors.py` |
| Core modules (transport, mapping, adapters, config, deployment) | `.cursor/rules/core-modules.mdc` | `.claude/rules/core-modules.md` | `acpbox/acp_stdio.py`, `acpbox/mapping.py`, `acpbox/config.py`, `acpbox/session_store.py`, `acpbox/agents/**/*.py`, operator files |

## Codex

Codex does not read `.mdc` files and has no glob-based rule attachment, so `.codex/hooks.json` wires `.codex/hooks/attach_rules.py`:

- `SessionStart` injects every rule with `alwaysApply: true`;
- `PreToolUse` on `apply_patch` / `Edit` / `Write` injects rules whose `globs` cover the patched files, once per rule per session.

The index of rules and their attachment is in `.codex/rules.md`. Codex tracks hooks by content hash: run `/hooks` once per clone and again after any change to `.codex/hooks.json` or `.codex/hooks/attach_rules.py`. The hook requires `python3`.

## Keeping Rules in Sync

Any change to a rule file is mirrored to every tree in the same commit: `.cursor/rules/`, `.claude/rules/`, this file (and the `CLAUDE.md` symlink), and `.codex/rules.md` when rules are added, renamed, or re-scoped. The exact procedure is the "Rules Sync" section of the workflow rule.
