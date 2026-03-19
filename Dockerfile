FROM python:3.11-slim

# Comma-separated, case-insensitive: opencode, cursor. Pass at build time (e.g. docker compose build.args from .env).
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
    case "${agents}" in (*,opencode,*) install_opencode=1 ;; esac; \
    case "${agents}" in (*,cursor,*) install_cursor=1 ;; esac; \
    if [ "${install_opencode}" = "0" ] && [ "${install_cursor}" = "0" ]; then \
      echo "AGENTS build-arg empty or without opencode|cursor; image contains acpbox only. Install an ACP agent in a derived image or mount a binary."; \
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
      apt-get purge -y curl; \
      apt-get autoremove -y; \
      rm -rf /var/lib/apt/lists/*; \
    fi

COPY . .
RUN pip install --no-cache-dir . \
 && chown -R user:user /app \
 && mkdir -p /workspace \
 && chown user:user /workspace

ENV ACP_WORKSPACE=/workspace
ENV CONFIG_PATH=/app/config.yaml

EXPOSE 8080

USER user

CMD ["acpbox"]
