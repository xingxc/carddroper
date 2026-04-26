"""Test fixtures.

Each test function gets:
- A fresh test database (carddroper_test) created once per session, schema-reset per test.
- A configured FastAPI `app` with that DB wired in.
- An httpx.AsyncClient pointed at the app via ASGITransport (no real network).
"""

import os

import pytest  # noqa: F401
import pytest_asyncio

# Force test env before the app imports config / database.
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://johnxing@localhost:5432/carddroper_test",
)
os.environ.setdefault("JWT_SECRET", "test-secret-not-for-production-use-only-for-tests")
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ.setdefault("HIBP_ENABLED", "false")
os.environ.setdefault("REGISTER_RATE_LIMIT", "1000/minute")
os.environ.setdefault("LOGIN_RATE_LIMIT", "1000/minute")
os.environ.setdefault("REFRESH_RATE_LIMIT", "1000/minute")
os.environ.setdefault("LOGOUT_RATE_LIMIT", "1000/minute")
os.environ.setdefault("FORGOT_PASSWORD_RATE_LIMIT", "1000/hour")
os.environ.setdefault("RESEND_VERIFICATION_RATE_LIMIT", "1000/hour")
os.environ.setdefault("VERIFY_EMAIL_RATE_LIMIT", "1000/minute")
os.environ.setdefault("CHANGE_EMAIL_RATE_LIMIT", "1000/hour")
os.environ.setdefault("CONFIRM_EMAIL_CHANGE_RATE_LIMIT", "1000/minute")
os.environ.setdefault("SENDGRID_API_KEY", "")
os.environ.setdefault(
    "SENDGRID_SANDBOX", "true"
)  # prevent validate_sendgrid_production from firing in tests

import httpx  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

import app.models  # noqa: E402, F401 — register tables before Base is used
from app.base import Base  # noqa: E402
from app.database import engine  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402


@pytest_asyncio.fixture(autouse=True)
async def _reset_schema():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


@pytest_asyncio.fixture
async def client():
    transport = httpx.ASGITransport(app=fastapi_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def db_session():
    """Bare async DB session for direct row inspection in tests."""
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session
