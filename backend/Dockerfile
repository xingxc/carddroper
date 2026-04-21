# ── Stage 1: builder ────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc build-essential \
    && rm -rf /var/lib/apt/lists/*

# Build an isolated virtualenv so the runtime stage gets a clean copy
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

WORKDIR /build
COPY pyproject.toml .
# pip install . needs the package source; copy the app package
COPY app/ ./app/

RUN pip install --no-cache-dir .

# ── Stage 2: runtime ─────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

# libpq5 (runtime only, not -dev), ca-certificates for TLS, tini for PID 1
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 ca-certificates tini \
    && rm -rf /var/lib/apt/lists/*

# Pull the pre-built venv from the builder stage
COPY --from=builder /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

WORKDIR /app

# Application source — alembic kept because the Cloud Build migrate step uses it
COPY app/ ./app/
COPY alembic/ ./alembic/
COPY alembic.ini ./alembic.ini

# Non-root system user (no home dir, no login shell)
RUN adduser --system --no-create-home --group appuser

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)" || exit 1

ENTRYPOINT ["tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
