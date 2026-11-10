# Image for the Kubernetes batch Jobs: the dendro CLI in /opt/venv, plus prompts,
# knowledge, evals and the batch scripts in /app. Build from the repository root:
#   docker build -f deploy/k8s/Dockerfile .
# Tests are not run here; CI runs the full suite on every push and pull request.
FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /src
COPY pyproject.toml README.md LICENSE NOTICE ./
COPY src/ src/
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install ".[openai,anthropic,images]"

FROM python:3.12-slim
# The commit this image was built from, for the run trace (no .git inside the image).
ARG DENDRO_CODE_COMMIT_SHA=""
ENV DENDRO_CODE_COMMIT_SHA=${DENDRO_CODE_COMMIT_SHA}     PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DENDRO_TRACE_DIR=/tmp/traces
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin dendro
COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
COPY prompts/ prompts/
COPY knowledge/ knowledge/
COPY evals/ evals/
COPY scripts/batch/ scripts/batch/
USER 10001
ENTRYPOINT ["dendro"]
CMD ["--help"]
