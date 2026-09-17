---
description: Layer-by-layer implementation order for new behavior in the acpbox package
paths:
  - "acpbox/**/*.py"
---

# Implementation Order: Layer by Layer

Build ACPBox changes like a layered cake: first the units that depend on nothing inside the project, then the layer that depends only on those, and so on up to the app factory. Layers L0-L4 are defined in .claude/rules/architecture.md.

For **new behavior** the test always comes first (.claude/rules/workflow.md). This rule decides **where** code goes and **in which order** layers are touched.

## Steps

1. **Find the lowest layer that must change.** Example: a new field in `acp.steps` starts in `mapping.py` (L1), not in `routes/chat.py` (L3).
2. **One unit at a time.** Write the failing test for that unit, implement it, make it green. Do not start the next unit while the current one is red.
3. **Move one layer up** only when the lower layer is implemented and tested. Upper layers call the lower layer's public functions and never re-implement them.
4. **Wire composition last.** Register routers, app state, and config consumers in `main.py` after the route and its tests exist.
5. **Docs and operator files** follow once behavior is green (Documentation Sync in .claude/rules/workflow.md).

## Typical Orders in This Repository

**New OpenAI-compatible field or endpoint**
1. `schemas.py` (L0) - request/response models.
2. `mapping.py` (L1) - pure conversion functions, tests in `tests/unit/test_mapping.py`.
3. `acp_stdio.py` (L0) - only when new data must be collected from the agent; tests with a fake agent subprocess.
4. `routes/<name>.py` (L3) - thin handler, tests in `tests/test_<name>.py` through `client`; extend `MockRunner` if the runner interface grew.
5. `main.py` (L4) - `include_router`, mirrored in the `app` fixture of `tests/conftest.py`.

**New ACP agent type**
1. `agents/<agent>.py` (L1) - `AgentConfigAdapter` subclass: `config_path`, read/write, native <-> flat permission mapping, `known_permissions`, `allowed_values`, custom presets if any. Unit tests with `tmp_path` in `tests/test_agent_config.py`.
2. `agents/__init__.py` (L2) - binary basename in `AGENT_COMMAND_MAP`, class in `__all__`; detection and `create_adapter` tests.
3. `/v1/agent/*` API tests for the new adapter (L3).
4. Operator surface - `entrypoint.sh` (`has_agent`, `install_agent`, agent id loop), `AGENTS` comment in `.env.example`, README tables, `docs/deployment.md`, `docs/api-mapping.md`.

**New configuration option**
1. `config.py` (L0) - field on `AcpConfig` or `GatewayConfig` whose `description` names the env var, plus the env override in `Config.load()`; tests in `tests/unit/test_config.py`.
2. The consumer (`AcpRunner`, `create_app`, `run`) with its own test.
3. `config.example.yaml`, `.env.example`, `docs/config.md`, and `docker-compose.yaml` when the option should be exposed.

## Forbidden

- Writing a route before the mapping function, runner method, or adapter method it calls exists and is tested.
- Putting JSON-RPC handling in routes, or HTTP concerns in `acp_stdio.py`, `mapping.py`, or `agents/`.
- Changing several layers in one untested jump.
- Relying on a route test alone when the logic lives in a lower layer.

## Allowed

- `MockRunner` and `monkeypatch` fakes for the runner while a lower-layer change is in progress, as long as the fake matches the real interface.
- Refactoring within a layer once its tests are green.

## References

- .claude/rules/architecture.md
- .claude/rules/testing.md
- .claude/rules/workflow.md
