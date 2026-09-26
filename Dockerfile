# syntax=docker/dockerfile:1.7

# ─────────────────────────────────────────────────────────────────────────────
# Two stages, `fanmoot`'s shape (HAD-10): Node builds the SPA, Python serves it
# beside /ws and /healthz from one FastAPI process.
# ─────────────────────────────────────────────────────────────────────────────

# ── Stage 1 — the SPA ────────────────────────────────────────────────────────
FROM node:22-alpine AS frontend
WORKDIR /build/web

# The lockfile first, in its own layer: an edit to src/ reuses the install.
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund

# vite.config.ts writes to ../app/static/web — the path FastAPI serves — so
# the output lands at /build/app/static/web.
COPY web ./
RUN npm run build && echo "built: $(find /build/app/static/web -type f | wc -l) files"

# ── Stage 2 — runtime ────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# curl is the healthcheck's only requirement. No build-essential: everything
# installed — ADK, LiteLLM, the MCP SDK, boto3 — ships prebuilt wheels for
# this image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first, in their own layer: they change far less often than the
# source, so an edit to app/ reuses the cached install.
COPY pyproject.toml README.md ./
RUN pip install --upgrade pip && pip install .

COPY app ./app
COPY tests ./tests
# The evidence capture (W1-10), runnable where the deployment's AWS credentials are.
COPY scripts ./scripts
COPY --from=frontend /build/app/static/web ./app/static/web

# Drop privileges. This process writes nothing to disk — a visitor's session
# lives in memory — so the code tree stays root-owned and read-only to it.
RUN useradd --create-home --uid 10001 fanmoot
USER fanmoot

# 8090: `fanmoot` takes 8000 and `fanmoot-mcp` 8080 on the same Dokploy host.
EXPOSE 8090

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8090/healthz || exit 1

# **One worker, deliberately**, for the reason `fanmoot-mcp` gives for its own:
# a visitor's MCP session, agent state and WebSocket all live in this
# process's memory, and a second worker would be a second, disjoint memory —
# a resumed socket landing on the other one would find no session.
# W6-2 measured it: twenty visitors, each with an MCP session and a turn, were
# answered as fast as one, so one worker it stays (docs/runbook.md, Load).
CMD ["gunicorn", "app.main:app", \
    "-k", "uvicorn.workers.UvicornWorker", \
    "-b", "0.0.0.0:8090", \
    "--workers", "1"]
