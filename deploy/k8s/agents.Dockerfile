# The batch image plus the Claude Code and Codex CLIs and the agent bridge, for runs where
# the bridge answers model calls instead of a hosted provider. Build from the repository root:
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
COPY scripts/agent-provider/ /app/scripts/agent-provider/
USER 10001
