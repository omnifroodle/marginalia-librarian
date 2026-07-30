# Serve-only librarian image for the demo compose stack. Ships
# config.docker.yaml as its config; the vault is bind-mounted at /vault and
# OpenSearch is reached via the OPENSEARCH_HOST env override. Works with no
# LLM key (browse/read only) — searches then report llm_auth, typed.
FROM python:3.12-slim

WORKDIR /app

# Install the package first so source edits don't bust the dependency layer.
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir ".[api]"

# Never bake the local config.yaml (gitignored, holds interpolated secrets);
# .dockerignore enforces this. The docker config is checked in and keyless.
COPY config.docker.yaml ./config.yaml

RUN mkdir -p /app/logs

ENV LIBRARIAN_VAULT_ROOT=/vault
EXPOSE 8000

CMD ["librarian", "serve", "--host", "0.0.0.0", "--port", "8000"]
