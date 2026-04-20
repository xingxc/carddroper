FROM python:3.11-slim AS base

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --no-cache-dir \
    fastapi==0.115.0 \
    "uvicorn[standard]==0.34.0" \
    "sqlalchemy[asyncio]==2.0.36" \
    asyncpg==0.30.0 \
    alembic==1.14.1 \
    "pydantic[email]==2.10.0" \
    pydantic-settings==2.7.0 \
    "python-jose[cryptography]==3.3.0" \
    bcrypt==4.0.1 \
    python-dotenv==1.0.1 \
    stripe==11.4.1 \
    httpx==0.28.1 \
    python-multipart==0.0.20 \
    slowapi==0.1.9 \
    sendgrid==6.11.0

COPY . .

EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
