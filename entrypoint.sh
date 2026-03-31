#!/usr/bin/env bash
set -euo pipefail

export HOME="${HOME:-/home/user}"
export PATH="$HOME/.local/bin:$HOME/.opencode/bin:$PATH"

normalize_agents() {
  local raw="${1:-}"
  raw="$(printf '%s' "$raw" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"
  printf ',%s,' "$raw"
}

has_agent() {
  local id="$1"
  case "$id" in
    opencode) command -v opencode >/dev/null 2>&1 ;;
    cursor) command -v agent >/dev/null 2>&1 ;;
    claude) command -v claude >/dev/null 2>&1 && command -v claude-agent-acp >/dev/null 2>&1 ;;
    codex) command -v codex >/dev/null 2>&1 && command -v codex-acp >/dev/null 2>&1 ;;
    *) return 1 ;;
  esac
}

install_agent() {
  local id="$1"
  case "$id" in
    opencode)
      npm install -g --prefix "$HOME/.local" opencode-ai
      command -v opencode >/dev/null
      ;;
    cursor)
      curl -fsSL https://cursor.com/install | bash
      command -v agent >/dev/null
      ;;
    claude)
      npm install -g --prefix "$HOME/.local" @anthropic-ai/claude-code @agentclientprotocol/claude-agent-acp
      command -v claude >/dev/null
      command -v claude-agent-acp >/dev/null
      ;;
    codex)
      npm install -g --prefix "$HOME/.local" @openai/codex @zed-industries/codex-acp
      command -v codex >/dev/null
      command -v codex-acp >/dev/null
      ;;
    *)
      echo "Unknown agent id $id" >&2
      return 2
      ;;
  esac
}

ensure_agents_installed() {
  local agents_csv="${AGENTS:-}"
  if [ -z "${agents_csv}" ]; then
    return 0
  fi

  local agents
  agents="$(normalize_agents "$agents_csv")"

  local id
  for id in opencode cursor claude codex; do
    case "$agents" in
      *,"$id",*)
        if has_agent "$id"; then
          continue
        fi
        install_agent "$id"
        ;;
    esac
  done
}

ensure_agents_installed
if [ "$#" -eq 0 ]; then
  set -- acpbox
fi

case "${1:-}" in
  -*)
    set -- acpbox "$@"
    ;;
esac

exec "$@"
