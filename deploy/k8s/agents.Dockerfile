# The batch image plus the Claude Code, Codex and Mistral Vibe CLIs and the agent bridge, for
# runs where the bridge answers model calls instead of a hosted provider. Build from the
# repository root:
#   docker build -f deploy/k8s/agents.Dockerfile --build-arg BASE_IMAGE=<batch image> .
ARG BASE_IMAGE
FROM node:22-bookworm-slim AS node

FROM ${BASE_IMAGE}
USER root
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && npm install -g --no-fund --no-audit \
      @anthropic-ai/claude-code@2.1.319 \
      @openai/codex@0.167.0 \
 && npm cache clean --force \
 && claude --version && codex --version
# Vibe is a Python package that carries its Rust CLI. It gets a venv of its own so its
# dependencies never meet Dendro's in /opt/venv; the launcher finds vibe-app-server beside
# the resolved `vibe`, so the symlink is enough.
RUN /usr/local/bin/python3 -m venv /opt/vibe \
 && /opt/vibe/bin/pip install --no-cache-dir --disable-pip-version-check mistral-vibe==3.1.3 \
 && /opt/vibe/bin/python -m compileall -q /opt/vibe/lib \
 && ln -s /opt/vibe/bin/vibe /usr/local/bin/vibe \
 && vibe --version
COPY scripts/agent-provider/ /app/scripts/agent-provider/
USER 10001
