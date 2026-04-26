"""Tests for billing chassis foundation (ticket 0021).

Covers: balance/ledger primitives, format_balance, settings validators,
auth-layer billing hooks, webhook signature verification and idempotency.
Stripe SDK calls are monkey-patched — no real Stripe calls made.

Fixture notes (from conftest.py):
- _reset_schema: autouse — drops/recreates schema for each test.
- client: httpx.AsyncClient pointed at the FastAPI app.
- db_session: bare AsyncSession for direct DB inspection.
"""

import hashlib
import hmac
import json
import time
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing.exceptions import InsufficientBalanceError
from app.billing.format import format_balance
from app.database import engine
from app.models import BalanceLedger, User
from app.models.stripe_event import StripeEvent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SETTINGS_BASE = {
    "DATABASE_URL": "postgresql+asyncpg://test@localhost/test",
    "JWT_SECRET": "a-secret-for-unit-tests-only-not-prod",
    "FRONTEND_BASE_URL": "http://localhost:3000",
    "CORS_ORIGINS": "http://localhost:3000",
    # SENDGRID_SANDBOX=True prevents validate_sendgrid_production from firing in unit tests.
    "SENDGRID_SANDBOX": True,
}


def _make_settings(**overrides):
    return {**_SETTINGS_BASE, **overrides}


def _make_stripe_header(payload: str | bytes, secret: str) -> str:
    """Build a valid Stripe webhook signature header for testing.

    Stripe header format: t=<timestamp>,v1=<hmac_sha256(secret, "{t}.{payload}")>
    """
    if isinstance(payload, str):
        payload = payload.encode()
    ts = int(time.time())
    signed_payload = f"{ts}.".encode() + payload
    sig = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


async def _create_user(session, email="test@example.com") -> User:
    """Insert a bare user row and return the flushed User (id populated)."""
    from app.services.auth_service import hash_password

    user = User(email=email, password_hash=hash_password("TestPassword1!"), full_name="Test User")
    session.add(user)
    await session.flush()
    return user


# ---------------------------------------------------------------------------
# Format tests (pure — no DB)
# ---------------------------------------------------------------------------


def test_format_balance_zero():
    assert format_balance(0) == "$0.00"


def test_format_balance_whole_cents():
    assert format_balance(1_230_000) == "$1.23"


def test_format_balance_sub_cent():
    assert format_balance(3_400) == "$0.0034"


def test_format_balance_large():
    assert format_balance(1_000_000_000) == "$1000.00"


# ---------------------------------------------------------------------------
# Settings validator tests (no DB)
# ---------------------------------------------------------------------------


def test_settings_requires_stripe_secret_when_billing_enabled():
    with pytest.raises(ValidationError) as exc_info:
        __import__("app.config", fromlist=["Settings"]).Settings(
            **_make_settings(
                BILLING_ENABLED=True,
                STRIPE_SECRET_KEY=None,
                STRIPE_WEBHOOK_SECRET="whsec_test",
            )
        )
    assert "STRIPE_SECRET_KEY" in str(exc_info.value)


def test_settings_requires_stripe_webhook_secret_when_billing_enabled():
    with pytest.raises(ValidationError) as exc_info:
        __import__("app.config", fromlist=["Settings"]).Settings(
            **_make_settings(
                BILLING_ENABLED=True,
                STRIPE_SECRET_KEY="sk_test_abc",
                STRIPE_WEBHOOK_SECRET=None,
            )
        )
    assert "STRIPE_WEBHOOK_SECRET" in str(exc_info.value)


def test_settings_allows_empty_stripe_keys_when_billing_disabled():
    s = __import__("app.config", fromlist=["Settings"]).Settings(
        **_make_settings(
            BILLING_ENABLED=False,
            STRIPE_SECRET_KEY=None,
            STRIPE_WEBHOOK_SECRET=None,
        )
    )
    assert s.BILLING_ENABLED is False


# ---------------------------------------------------------------------------
# Balance / ledger primitive tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_balance_zero_for_new_user():
    from app import billing

    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            user = await _create_user(session)
        balance = await billing.get_balance_micros(user.id, session)
    assert balance == 0


@pytest.mark.asyncio
async def test_grant_increases_balance():
    from app import billing

    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            user = await _create_user(session)
            await billing.grant(
                user_id=user.id,
                amount_micros=1_000_000,
                reason=billing.Reason.TOPUP,
                db=session,
            )
        balance = await billing.get_balance_micros(user.id, session)
    assert balance == 1_000_000


@pytest.mark.asyncio
async def test_debit_decreases_balance():
    from app import billing

    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            user = await _create_user(session)
            await billing.grant(
                user_id=user.id,
                amount_micros=1_000_000,
                reason=billing.Reason.TOPUP,
                db=session,
            )

    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            await billing.debit(
                user_id=user.id,
                amount_micros=400,
                ref_type="test",
                ref_id="1",
                db=session,
            )
        balance = await billing.get_balance_micros(user.id, session)
    assert balance == 999_600


@pytest.mark.asyncio
async def test_debit_insufficient_balance_raises():
    from app import billing

    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            user = await _create_user(session)
            await billing.grant(
                user_id=user.id,
                amount_micros=100,
                reason=billing.Reason.TOPUP,
                db=session,
            )

    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            with pytest.raises(InsufficientBalanceError) as exc_info:
                await billing.debit(
                    user_id=user.id,
                    amount_micros=200,
                    ref_type="test",
                    ref_id="1",
                    db=session,
                )
            exc = exc_info.value
            assert exc.user_id == user.id
            assert exc.balance_micros == 100
            assert exc.requested_micros == 200

    # Balance must be unchanged after the failed debit.
    async with AsyncSession(engine, expire_on_commit=False) as session:
        balance = await billing.get_balance_micros(user.id, session)
    assert balance == 100


@pytest.mark.asyncio
async def test_balance_sums_multiple_entries():
    from app import billing

    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            user = await _create_user(session)
            await billing.grant(
                user_id=user.id, amount_micros=500_000, reason=billing.Reason.TOPUP, db=session
            )
            await billing.grant(
                user_id=user.id,
                amount_micros=1_000_000,
                reason=billing.Reason.SIGNUP_BONUS,
                db=session,
            )

    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            await billing.debit(
                user_id=user.id,
                amount_micros=200_000,
                ref_type="test",
                ref_id="2",
                db=session,
            )
        balance = await billing.get_balance_micros(user.id, session)
    assert balance == 1_300_000  # 500_000 + 1_000_000 - 200_000


@pytest.mark.asyncio
async def test_ledger_stripe_event_id_unique_constraint():
    """Two balance_ledger rows with the same stripe_event_id must raise IntegrityError."""
    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            user = await _create_user(session)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            session.add(
                BalanceLedger(
                    user_id=user.id,
                    amount_micros=1_000_000,
                    reason="topup",
                    stripe_event_id="evt_duplicate_test",
                )
            )

    with pytest.raises(IntegrityError):
        async with AsyncSession(engine, expire_on_commit=False) as session:
            async with session.begin():
                session.add(
                    BalanceLedger(
                        user_id=user.id,
                        amount_micros=1_000_000,
                        reason="topup",
                        stripe_event_id="evt_duplicate_test",
                    )
                )


# ---------------------------------------------------------------------------
# Auth integration tests (HTTP)
# ---------------------------------------------------------------------------

_REGISTER_BODY = {
    "email": "billing@example.com",
    "password": "StrongPassword99!",
    "full_name": "Billing User",
}


@pytest.mark.asyncio
async def test_register_does_not_create_customer_when_billing_disabled(client):
    """BILLING_ENABLED=false (explicit patch) — no Stripe calls, stripe_customer_id stays NULL."""
    from app.config import settings

    with (
        patch.object(settings, "BILLING_ENABLED", False),
        patch("app.billing.primitives.stripe") as mock_stripe,
    ):
        resp = await client.post("/auth/register", json=_REGISTER_BODY)
    assert resp.status_code == 200
    mock_stripe.Customer.create.assert_not_called()

    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(select(User).where(User.email == _REGISTER_BODY["email"]))
        user = result.scalar_one_or_none()
    assert user is not None
    assert user.stripe_customer_id is None


@pytest.mark.asyncio
async def test_register_creates_customer_when_billing_enabled(client):
    """BILLING_ENABLED=true, mocked Stripe — stripe_customer_id stored on user."""
    from app.config import settings

    mock_customer = MagicMock()
    mock_customer.id = "cus_test_abc123"
    email = "billing2@example.com"

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch("app.billing.primitives.stripe") as mock_stripe,
    ):
        mock_stripe.Customer.create.return_value = mock_customer
        resp = await client.post(
            "/auth/register",
            json={**_REGISTER_BODY, "email": email},
        )

    assert resp.status_code == 200
    mock_stripe.Customer.create.assert_called_once()

    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
    assert user is not None
    assert user.stripe_customer_id == "cus_test_abc123"


@pytest.mark.asyncio
async def test_register_grants_signup_bonus_when_configured(client):
    """BILLING_ENABLED=true + BILLING_SIGNUP_BONUS_MICROS=1_000_000 — ledger entry created."""
    from app.config import settings

    mock_customer = MagicMock()
    mock_customer.id = "cus_bonus_test"
    email = "bonus@example.com"

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch.object(settings, "BILLING_SIGNUP_BONUS_MICROS", 1_000_000),
        patch("app.billing.primitives.stripe") as mock_stripe,
    ):
        mock_stripe.Customer.create.return_value = mock_customer
        resp = await client.post(
            "/auth/register",
            json={**_REGISTER_BODY, "email": email},
        )

    assert resp.status_code == 200

    async with AsyncSession(engine, expire_on_commit=False) as session:
        user_result = await session.execute(select(User).where(User.email == email))
        user = user_result.scalar_one()
        ledger_result = await session.execute(
            select(BalanceLedger).where(BalanceLedger.user_id == user.id)
        )
        entries = ledger_result.scalars().all()

    assert len(entries) == 1
    assert entries[0].amount_micros == 1_000_000
    assert entries[0].reason == "signup_bonus"


@pytest.mark.asyncio
async def test_register_skips_bonus_when_zero(client):
    """BILLING_ENABLED=true but BILLING_SIGNUP_BONUS_MICROS=0 (default) — no ledger entry."""
    from app.config import settings

    mock_customer = MagicMock()
    mock_customer.id = "cus_no_bonus"
    email = "nobonus@example.com"

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch.object(settings, "BILLING_SIGNUP_BONUS_MICROS", 0),
        patch("app.billing.primitives.stripe") as mock_stripe,
    ):
        mock_stripe.Customer.create.return_value = mock_customer
        resp = await client.post(
            "/auth/register",
            json={**_REGISTER_BODY, "email": email},
        )

    assert resp.status_code == 200

    async with AsyncSession(engine, expire_on_commit=False) as session:
        user_result = await session.execute(select(User).where(User.email == email))
        user = user_result.scalar_one()
        ledger_result = await session.execute(
            select(BalanceLedger).where(BalanceLedger.user_id == user.id)
        )
        entries = ledger_result.scalars().all()

    assert len(entries) == 0


@pytest.mark.asyncio
async def test_register_survives_stripe_failure(client):
    """BILLING_ENABLED=true but Stripe raises — register returns 200, user exists, customer_id is None."""
    from app.config import settings

    email = "stripefail@example.com"

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch("app.billing.primitives.stripe") as mock_stripe,
    ):
        mock_stripe.Customer.create.side_effect = Exception("Stripe is down")
        resp = await client.post(
            "/auth/register",
            json={**_REGISTER_BODY, "email": email},
        )

    assert resp.status_code == 200

    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()

    assert user is not None
    assert user.stripe_customer_id is None


@pytest.mark.asyncio
async def test_verify_email_grants_verify_bonus_when_configured(client):
    """BILLING_ENABLED=true + BILLING_VERIFY_BONUS_MICROS=500_000 — ledger entry after verification."""
    from app.config import settings
    from app.services.auth_service import create_verify_token

    mock_customer = MagicMock()
    mock_customer.id = "cus_verify_bonus"
    email = "verifybonus@example.com"

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch.object(settings, "BILLING_SIGNUP_BONUS_MICROS", 0),
        patch.object(settings, "BILLING_VERIFY_BONUS_MICROS", 500_000),
        patch("app.billing.primitives.stripe") as mock_stripe,
    ):
        mock_stripe.Customer.create.return_value = mock_customer
        reg_resp = await client.post(
            "/auth/register",
            json={**_REGISTER_BODY, "email": email},
        )
        assert reg_resp.status_code == 200

    # Get the user to construct verify token
    async with AsyncSession(engine, expire_on_commit=False) as session:
        user_result = await session.execute(select(User).where(User.email == email))
        user = user_result.scalar_one()

    token = create_verify_token(user.id, user.token_version)

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch.object(settings, "BILLING_VERIFY_BONUS_MICROS", 500_000),
    ):
        verify_resp = await client.post("/auth/verify-email", json={"token": token})

    assert verify_resp.status_code == 200

    async with AsyncSession(engine, expire_on_commit=False) as session:
        ledger_result = await session.execute(
            select(BalanceLedger).where(BalanceLedger.user_id == user.id)
        )
        entries = ledger_result.scalars().all()

    verify_entries = [e for e in entries if e.reason == "verify_bonus"]
    assert len(verify_entries) == 1
    assert verify_entries[0].amount_micros == 500_000


# ---------------------------------------------------------------------------
# Webhook tests
# ---------------------------------------------------------------------------

_WEBHOOK_SECRET = "whsec_test_signing_secret_for_tests"


def _build_event_payload(
    event_id: str = "evt_test_001", event_type: str = "payment_intent.succeeded"
) -> str:
    return json.dumps(
        {
            "id": event_id,
            "object": "event",
            "type": event_type,
            "data": {"object": {}},
            "livemode": False,
            "pending_webhooks": 0,
            "request": None,
            "api_version": "2023-10-16",
        }
    )


def _make_billing_test_app() -> FastAPI:
    """Create a minimal FastAPI app with the billing router mounted."""
    from app.routes.billing import router as billing_router

    test_app = FastAPI()
    test_app.include_router(billing_router)
    return test_app


def _make_app_with_billing_disabled() -> FastAPI:
    """Construct a fresh FastAPI app with BILLING_ENABLED=false to verify route mounting.

    Mirrors the conditional-mount logic from main.py without using importlib.reload
    (which would destabilise the shared `client` fixture for subsequent tests).
    The calling test patches settings.BILLING_ENABLED=False before calling this helper,
    so the `if settings.BILLING_ENABLED` check below evaluates to False and the billing
    router is not included.
    """
    from app.config import settings

    app = FastAPI()
    if settings.BILLING_ENABLED:
        from app.routes.billing import router as billing_router

        app.include_router(billing_router)
    return app


@pytest.mark.asyncio
async def test_webhook_not_mounted_when_billing_disabled():
    """BILLING_ENABLED=false (explicit patch) — billing router not mounted on fresh app."""
    from app.config import settings

    with patch.object(settings, "BILLING_ENABLED", False):
        app = _make_app_with_billing_disabled()
        mounted_paths = {route.path for route in app.routes}

    assert not any(path.startswith("/billing") for path in mounted_paths), (
        f"Billing routes should not be mounted when BILLING_ENABLED=false; "
        f"found paths starting with /billing in: {sorted(mounted_paths)}"
    )


@pytest.mark.asyncio
async def test_webhook_rejects_invalid_signature():
    """POST with bad stripe-signature header — returns 400."""
    from app.config import settings

    with (
        patch.object(settings, "STRIPE_WEBHOOK_SECRET", _WEBHOOK_SECRET),
    ):
        test_app = _make_billing_test_app()
        transport = httpx.ASGITransport(app=test_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post(
                "/billing/webhook",
                content=b'{"id": "evt_bad"}',
                headers={"stripe-signature": "t=1,v1=invalidsig"},
            )

    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_webhook_accepts_valid_signature_unhandled_type():
    """Valid signature for an unhandled event type — returns 200, stripe_events row inserted."""
    from app.config import settings

    payload = _build_event_payload("evt_valid_001", "payment_intent.succeeded")
    header = _make_stripe_header(payload, _WEBHOOK_SECRET)

    with (
        patch.object(settings, "STRIPE_WEBHOOK_SECRET", _WEBHOOK_SECRET),
    ):
        test_app = _make_billing_test_app()
        transport = httpx.ASGITransport(app=test_app)

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post(
                "/billing/webhook",
                content=payload.encode(),
                headers={"stripe-signature": header, "content-type": "application/json"},
            )

    assert resp.status_code == 200

    # Verify the stripe_events row was inserted.
    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(select(StripeEvent).where(StripeEvent.id == "evt_valid_001"))
        event_row = result.scalar_one_or_none()
    assert event_row is not None
    assert event_row.event_type == "payment_intent.succeeded"


@pytest.mark.asyncio
async def test_webhook_idempotent_on_replay():
    """Same event id posted twice — both return 200, only one stripe_events row."""
    from app.config import settings

    payload = _build_event_payload("evt_idempotent_001", "payment_intent.succeeded")

    with (
        patch.object(settings, "STRIPE_WEBHOOK_SECRET", _WEBHOOK_SECRET),
    ):
        test_app = _make_billing_test_app()
        transport = httpx.ASGITransport(app=test_app)

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            header1 = _make_stripe_header(payload, _WEBHOOK_SECRET)
            resp1 = await c.post(
                "/billing/webhook",
                content=payload.encode(),
                headers={"stripe-signature": header1, "content-type": "application/json"},
            )
            # Second POST with same event — fresh header (timestamp can change, same event id)
            header2 = _make_stripe_header(payload, _WEBHOOK_SECRET)
            resp2 = await c.post(
                "/billing/webhook",
                content=payload.encode(),
                headers={"stripe-signature": header2, "content-type": "application/json"},
            )

    assert resp1.status_code == 200
    assert resp2.status_code == 200

    # Only one stripe_events row must exist.
    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(
            select(StripeEvent).where(StripeEvent.id == "evt_idempotent_001")
        )
        rows = result.scalars().all()
    assert len(rows) == 1
