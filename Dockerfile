FROM python:3.11-slim

# Comma-separated, case-insensitive: opencode, cursor, claude, codex. Pass at build time (e.g. docker compose build.args from .env).
ARG AGENTS=

RUN groupadd --gid 1000 user \
 && useradd --uid 1000 --gid 1000 --create-home --home-dir /home/user user

WORKDIR /app

ENV DEBIAN_FRONTEND=noninteractive
ENV HOME=/home/user
ENV PATH="/home/user/.local/bin:/home/user/.opencode/bin:${PATH}"

RUN set -eux; \
    agents="$(printf '%s' "${AGENTS}" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"; \
    agents=",${agents},"; \
    install_opencode=0; \
    install_cursor=0; \
    install_claude=0; \
    install_codex=0; \
    case "${agents}" in (*,opencode,*) install_opencode=1 ;; esac; \
    case "${agents}" in (*,cursor,*) install_cursor=1 ;; esac; \
    case "${agents}" in (*,claude,*) install_claude=1 ;; esac; \
    case "${agents}" in (*,codex,*) install_codex=1 ;; esac; \
    if [ "${install_opencode}" = "0" ] && [ "${install_cursor}" = "0" ] && [ "${install_claude}" = "0" ] && [ "${install_codex}" = "0" ]; then \
      echo "AGENTS build-arg empty or without opencode|cursor|claude|codex; image contains acpbox only. Install an ACP agent in a derived image or mount a binary."; \
    else \
      apt-get update; \
      apt-get install -y --no-install-recommends curl ca-certificates bash; \
      if [ "${install_opencode}" = "1" ]; then \
        su user -s /bin/bash -c 'curl -fsSL https://opencode.ai/install | bash'; \
        su user -s /bin/bash -c 'command -v opencode'; \
      fi; \
      if [ "${install_cursor}" = "1" ]; then \
        su user -s /bin/bash -c 'curl -fsSL https://cursor.com/install | bash'; \
        su user -s /bin/bash -c 'command -v agent'; \
      fi; \
      if [ "${install_claude}" = "1" ] || [ "${install_codex}" = "1" ]; then \
        apt-get install -y --no-install-recommends nodejs npm; \
      fi; \
      if [ "${install_claude}" = "1" ]; then \
        npm install -g @agentclientprotocol/claude-agent-acp; \
        command -v claude-agent-acp; \
      fi; \
      if [ "${install_codex}" = "1" ]; then \
        npm install -g @openai/codex @zed-industries/codex-acp; \
        command -v codex; \
        command -v codex-acp; \
      fi; \
      apt-get purge -y curl; \
      apt-get autoremove -y; \
      rm -rf /var/lib/apt/lists/*; \
    fi

COPY . .
RUN pip install --no-cache-dir . \
 && chown -R user:user /app \
 && mkdir -p /workspace \
 && chown user:user /workspace

EXPOSE 8080

USER user

CMD ["acpbox"]
