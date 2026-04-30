# syntax=docker/dockerfile:1.7
# Multi-stage Dockerfile for the Dayforce NL2SQL Data Agent.
#
# Stage 1 (builder): pull `uv`, sync deps into /opt/venv from uv.lock.
# Stage 2 (runtime): slim python:3.12 + the venv + the source + employees.db.
#
# Both `cli` and `ui` are served from this image; the docker-compose profiles
# decide which entrypoint runs.

# ─── builder ────────────────────────────────────────────────────────────────
FROM ghcr.io/astral-sh/uv:0.5.4-python3.12-bookworm-slim AS builder

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app

# Copy dep manifests first for layer caching.
COPY pyproject.toml uv.lock README.md ./

# Sync core + ui extras (cli mode is a subset).
RUN --mount=type=cache,id=uv-cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --extra ui

# Now copy source so changes don't bust the deps cache.
COPY src/ ./src/
COPY streamlit_app.py ./
COPY employees.db ./

# Final install with the project itself.
RUN --mount=type=cache,id=uv-cache,target=/root/.cache/uv \
    uv sync --frozen --extra ui


# ─── runtime ────────────────────────────────────────────────────────────────
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    NL2SQL_DB_PATH=/app/employees.db

WORKDIR /app

# Non-root user (uid matches distroless `nonroot`)
RUN groupadd -g 65532 nonroot && useradd -m -u 65532 -g 65532 nonroot

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /app/src /app/src
COPY --from=builder /app/streamlit_app.py /app/streamlit_app.py
COPY --from=builder /app/employees.db /app/employees.db

RUN mkdir -p /app/logs /app/.cache && chown -R nonroot:nonroot /app

USER nonroot

EXPOSE 8501

# Default entrypoint = CLI; docker-compose overrides for the UI profile.
ENTRYPOINT ["python", "-m", "nl2sql"]
