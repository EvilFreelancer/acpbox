# Codex bridge for Cursor rules

This repository keeps detailed project rules in `.cursor/rules/*.mdc`, and that directory
is their single source of truth (`.claude/rules/*.md` mirrors the same bodies for Claude Code).
Codex does not read `.mdc` files on its own, so `.codex/hooks/attach_rules.py` delivers them.

## How delivery works

| Trigger | What is attached |
|---------|------------------|
| `SessionStart` | every rule with `alwaysApply: true` |
| `PreToolUse` on `apply_patch` / `Edit` / `Write` | rules whose `globs` cover the files in the patch, once per rule per session |

The hook parses `.mdc` frontmatter (`description`, `globs`, `alwaysApply`) directly, so a
new rule file is picked up with no wiring. It fails open: a malformed rule exits quietly
instead of blocking an edit. Configuration lives in `.codex/hooks.json`.

Codex tracks hooks by content hash and skips untrusted ones without a hard error. Run
`/hooks` once per clone, and again after any edit to `attach_rules.py` or `hooks.json`.
Project-local hooks load only when the `.codex/` layer is trusted. The hook needs `python3`.

To see what a given patch would pull in, no Codex session needed:

```bash
echo '{"hook_event_name":"PreToolUse","session_id":"probe","tool_input":{"command":"*** Begin Patch\n*** Update File: acpbox/routes/chat.py\n*** End Patch"}}' | python3 .codex/hooks/attach_rules.py
```

## Rule index

| Cursor rule | Applies to | Attachment |
|-------------|------------|------------|
| [workflow.mdc](../.cursor/rules/workflow.mdc) | BDD/TDD for features and bugs, docs sync, final checks, Rules Sync | always |
| [testing.mdc](../.cursor/rules/testing.mdc) | pytest layout, fixtures, `MockRunner`, fake stdio agents, isolation | always |
| [architecture.mdc](../.cursor/rules/architecture.mdc) | Runtime invariants, layers L0-L4, allowed dependencies | always |
| [code-style.mdc](../.cursor/rules/code-style.mdc) | Typing, async, errors, logging, language of comments and rules | always |
| [implementation-order.mdc](../.cursor/rules/implementation-order.mdc) | Layer-by-layer order for new behavior | `acpbox/**/*.py` |
| [api-layer.mdc](../.cursor/rules/api-layer.mdc) | FastAPI routes, error codes, SSE contract | `acpbox/routes/**/*.py`, `acpbox/main.py`, `acpbox/schemas.py`, `acpbox/errors.py` |
| [core-modules.mdc](../.cursor/rules/core-modules.mdc) | ACP stdio transport, mapping, agent adapters, config, session store, deployment files | `acpbox/acp_stdio.py`, `acpbox/mapping.py`, `acpbox/config.py`, `acpbox/session_store.py`, `acpbox/agents/**/*.py`, `config.example.yaml`, `.env.example`, `docker-compose.yaml`, `Dockerfile`, `entrypoint.sh` |

The always-on set is workflow, testing, architecture, and code-style; the rest attach by
glob. Codex caps model-visible hook output (roughly 2500 tokens by default;
`additionalContextLimit` in `hooks.json` raises it to 8000 at session start and 6000 per
patch), so keep always-on rules compact.

## Operating rule

Keep `.cursor/rules/` as the single source of truth. The hook needs no update when rules
change, but this index and the rules table in `AGENTS.md` do: refresh both in the same
change that adds, renames, removes, or re-scopes a rule file. A rule is only reachable from
Codex if its frontmatter carries `globs` or `alwaysApply: true`.
