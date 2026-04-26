"""Tests for billing topup + balance endpoints + payment_intent.succeeded handler.

Ticket 0023 Phase 0a — 18 tests.

Stripe API calls are mocked via monkeypatch / unittest.mock — no real Stripe calls.
All tests use the autouse _reset_schema fixture from conftest.py.
"""

import asyncio
import hashlib
import hmac
import json
import time
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import engine
from app.models import BalanceLedger, User
from app.models.stripe_event import StripeEvent

pytestmark = pytest.mark.skipif(
    not settings.BILLING_ENABLED,
    reason="test_billing_topup requires BILLING_ENABLED=true — feature-gated at app-init time",
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_WEBHOOK_SECRET = "whsec_topup_test_secret"


def _make_stripe_header(payload: str | bytes, secret: str) -> str:
    """Build a valid Stripe webhook signature header for testing."""
    if isinstance(payload, str):
        payload = payload.encode()
    ts = int(time.time())
    signed_payload = f"{ts}.".encode() + payload
    sig = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


async def _register_and_verify(client, email: str = "topup@example.com") -> dict:
    """Register a user and verify their email; returns login response JSON."""
    from app.services.auth_service import create_verify_token

    reg = await client.post(
        "/auth/register",
        json={"email": email, "password": "StrongPassword99!", "full_name": "Topup User"},
    )
    assert reg.status_code == 200, reg.text

    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(select(User).where(User.email == email))
        user = result.scalar_one()

    token = create_verify_token(user.id, user.token_version)
    verify = await client.post("/auth/verify-email", json={"token": token})
    assert verify.status_code == 200, verify.text

    return reg.json(), user.id


async def _register_unverified(client, email: str = "unverified@example.com") -> dict:
    """Register a user WITHOUT verifying; returns reg response JSON."""
    reg = await client.post(
        "/auth/register",
        json={"email": email, "password": "StrongPassword99!", "full_name": "Unverified User"},
    )
    assert reg.status_code == 200, reg.text
    return reg.json()


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _build_pi_event(
    event_id: str = "evt_pi_001",
    user_id: str = "42",
    amount: int = 2000,
    event_type: str = "payment_intent.succeeded",
) -> str:
    """Build a payment_intent.succeeded event JSON with metadata."""
    return json.dumps(
        {
            "id": event_id,
            "object": "event",
            "type": event_type,
            "data": {
                "object": {
                    "id": "pi_test",
                    "object": "payment_intent",
                    "amount": amount,
                    "currency": "usd",
                    "metadata": {"user_id": user_id},
                }
            },
            "livemode": False,
            "pending_webhooks": 0,
            "request": None,
            "api_version": "2023-10-16",
        }
    )


def _build_pi_event_no_metadata(event_id: str = "evt_pi_no_meta") -> str:
    return json.dumps(
        {
            "id": event_id,
            "object": "event",
            "type": "payment_intent.succeeded",
            "data": {
                "object": {
                    "id": "pi_test2",
                    "object": "payment_intent",
                    "amount": 2000,
                    "currency": "usd",
                    "metadata": {},
                }
            },
            "livemode": False,
            "pending_webhooks": 0,
            "request": None,
            "api_version": "2023-10-16",
        }
    )


def _build_unregistered_event(event_id: str = "evt_cust_001") -> str:
    return json.dumps(
        {
            "id": event_id,
            "object": "event",
            "type": "customer.updated",
            "data": {"object": {}},
            "livemode": False,
            "pending_webhooks": 0,
            "request": None,
            "api_version": "2023-10-16",
        }
    )


def _make_billing_test_app():
    """Create a minimal FastAPI app with the billing router mounted."""
    from fastapi import FastAPI

    from app.routes.billing import router as billing_router

    test_app = FastAPI()
    test_app.include_router(billing_router)
    return test_app


def _build_pi_succeeded_event(
    event_id: str,
    user_id: int,
    amount_cents: int,
) -> str:
    """Alias for _build_pi_event with explicit parameter names matching ticket usage."""
    return _build_pi_event(
        event_id=event_id,
        user_id=str(user_id),
        amount=amount_cents,
        event_type="payment_intent.succeeded",
    )


def _sign_event(payload: str) -> tuple[bytes, str]:
    """Return (encoded payload, stripe-signature header) signed with the test webhook secret."""
    encoded = payload.encode()
    header = _make_stripe_header(encoded, _WEBHOOK_SECRET)
    return encoded, header


async def _make_verified_user(session, email: str) -> User:
    """Create and persist a verified User row; returns the User ORM object."""
    from datetime import datetime, timezone

    from app.services.auth_service import hash_password

    async with session.begin():
        user = User(
            email=email,
            password_hash=hash_password("Password123!"),
            full_name="Test User",
            verified_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        session.add(user)
        await session.flush()
    return user


# ---------------------------------------------------------------------------
# POST /billing/topup — auth / verification gates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_topup_endpoint_requires_auth(client):
    """No token → 401."""
    resp = await client.post("/billing/topup", json={"amount_micros": 5_000_000})
    # Route not mounted when BILLING_ENABLED=false; treat 404 same as 401 for unauthed
    # (the route isn't even reachable). The key property: no 200.
    assert resp.status_code in (401, 404)


@pytest.mark.asyncio
async def test_topup_endpoint_requires_verified(client):
    """Unverified user → 403 when BILLING_REQUIRE_VERIFIED=True (Kind-1 isolation)."""
    from app.config import settings

    mock_customer = MagicMock()
    mock_customer.id = "cus_unverified"
    mock_intent = MagicMock()
    mock_intent.client_secret = "pi_secret_unverified"

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch("app.billing.primitives.stripe") as _mock_stripe,
    ):
        _mock_stripe.Customer.create.return_value = mock_customer
        reg_resp = await _register_unverified(client, "unveri@example.com")

    access_token = reg_resp.get("access_token")

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch.object(settings, "BILLING_REQUIRE_VERIFIED", True),
        patch("app.routes.billing.stripe") as mock_stripe,
    ):
        mock_stripe.PaymentIntent.create.return_value = mock_intent
        resp = await client.post(
            "/billing/topup",
            json={"amount_micros": 5_000_000},
            headers=_auth_headers(access_token),
        )

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_topup_allows_unverified_user_when_flag_off(client):
    """Unverified user + BILLING_REQUIRE_VERIFIED=False (default) → 200; chassis is permissive."""
    from app.config import settings

    mock_customer = MagicMock()
    mock_customer.id = "cus_unverified_flagoff"
    mock_intent = MagicMock()
    mock_intent.client_secret = "pi_secret_flagoff"

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch("app.billing.primitives.stripe") as _mock_stripe,
    ):
        _mock_stripe.Customer.create.return_value = mock_customer
        reg_resp = await _register_unverified(client, "topup_flagoff@example.com")

    access_token = reg_resp.get("access_token")

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch.object(settings, "BILLING_REQUIRE_VERIFIED", False),
        patch("app.routes.billing.stripe") as mock_stripe,
        patch("app.billing.primitives.stripe") as mock_prim_stripe,
    ):
        mock_prim_stripe.Customer.create.return_value = mock_customer
        mock_stripe.PaymentIntent.create.return_value = mock_intent
        resp = await client.post(
            "/billing/topup",
            json={"amount_micros": 5_000_000},
            headers=_auth_headers(access_token),
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["client_secret"] == "pi_secret_flagoff"
    assert body["amount_micros"] == 5_000_000


@pytest.mark.asyncio
async def test_topup_endpoint_creates_payment_intent_with_metadata(client):
    """Verified user + valid amount → 200; PI created with correct metadata, amount, idempotency_key."""
    from app.config import settings

    mock_customer = MagicMock()
    mock_customer.id = "cus_meta_test"
    mock_intent = MagicMock()
    mock_intent.client_secret = "pi_secret_meta"

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch("app.billing.primitives.stripe") as mock_p_stripe,
    ):
        mock_p_stripe.Customer.create.return_value = mock_customer
        reg_resp, user_id = await _register_and_verify(client, "meta@example.com")

    access_token = reg_resp.get("access_token")

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch("app.routes.billing.stripe") as mock_stripe,
        patch("app.billing.primitives.stripe") as _,
    ):
        mock_stripe.PaymentIntent.create.return_value = mock_intent
        resp = await client.post(
            "/billing/topup",
            json={"amount_micros": 2_000_000},
            headers=_auth_headers(access_token),
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["client_secret"] == "pi_secret_meta"
    assert body["amount_micros"] == 2_000_000

    call_kwargs = mock_stripe.PaymentIntent.create.call_args
    assert call_kwargs is not None
    _, kw = call_kwargs
    assert kw["metadata"] == {"user_id": str(user_id)}
    assert kw["amount"] == 200  # 2_000_000 micros / 10_000 = 200 cents
    assert kw["currency"] == "usd"
    assert "topup:" in kw["idempotency_key"]


@pytest.mark.asyncio
async def test_topup_endpoint_rejects_amount_below_min(client):
    """amount_micros=100_000 ($0.10) < min $0.50 → 422."""
    from app.config import settings

    mock_customer = MagicMock()
    mock_customer.id = "cus_min_test"

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch("app.billing.primitives.stripe") as mock_p_stripe,
    ):
        mock_p_stripe.Customer.create.return_value = mock_customer
        reg_resp, _ = await _register_and_verify(client, "mintest@example.com")

    access_token = reg_resp.get("access_token")

    with (
        patch.object(settings, "BILLING_ENABLED", True),
    ):
        resp = await client.post(
            "/billing/topup",
            json={"amount_micros": 100_000},
            headers=_auth_headers(access_token),
        )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_topup_endpoint_rejects_amount_above_max(client):
    """amount_micros=600_000_000 ($600) > max $500 → 422."""
    from app.config import settings

    mock_customer = MagicMock()
    mock_customer.id = "cus_max_test"

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch("app.billing.primitives.stripe") as mock_p_stripe,
    ):
        mock_p_stripe.Customer.create.return_value = mock_customer
        reg_resp, _ = await _register_and_verify(client, "maxtest@example.com")

    access_token = reg_resp.get("access_token")

    with (
        patch.object(settings, "BILLING_ENABLED", True),
    ):
        resp = await client.post(
            "/billing/topup",
            json={"amount_micros": 600_000_000},
            headers=_auth_headers(access_token),
        )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_topup_endpoint_lazy_creates_customer_if_missing(client):
    """User with stripe_customer_id=None → topup calls Customer.create; id stored on user."""
    from app.config import settings

    mock_customer = MagicMock()
    mock_customer.id = "cus_lazy_created"
    mock_intent = MagicMock()
    mock_intent.client_secret = "pi_secret_lazy"

    # Register + verify with BILLING_ENABLED=False so no Customer is created at register time.
    with patch.object(settings, "BILLING_ENABLED", False):
        reg_resp, user_id = await _register_and_verify(client, "lazy@example.com")
    access_token = reg_resp.get("access_token")

    # Confirm stripe_customer_id is None.
    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one()
    assert user.stripe_customer_id is None

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch("app.routes.billing.stripe") as mock_stripe,
        patch("app.billing.primitives.stripe") as mock_prim_stripe,
    ):
        mock_prim_stripe.Customer.create.return_value = mock_customer
        mock_stripe.PaymentIntent.create.return_value = mock_intent
        resp = await client.post(
            "/billing/topup",
            json={"amount_micros": 5_000_000},
            headers=_auth_headers(access_token),
        )

    assert resp.status_code == 200, resp.text
    mock_prim_stripe.Customer.create.assert_called_once()

    # Stripe customer id must be persisted.
    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(select(User).where(User.id == user_id))
        updated_user = result.scalar_one()
    assert updated_user.stripe_customer_id == "cus_lazy_created"


@pytest.mark.asyncio
async def test_topup_endpoint_uses_existing_customer_id_if_present(client):
    """User with stripe_customer_id already set → Customer.create is NOT called."""
    from app.config import settings

    mock_customer = MagicMock()
    mock_customer.id = "cus_existing_at_register"
    mock_intent = MagicMock()
    mock_intent.client_secret = "pi_secret_existing"

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch("app.billing.primitives.stripe") as mock_p_stripe,
    ):
        mock_p_stripe.Customer.create.return_value = mock_customer
        reg_resp, user_id = await _register_and_verify(client, "existing@example.com")

    access_token = reg_resp.get("access_token")

    # Confirm stripe_customer_id was set by register hook.
    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one()
    assert user.stripe_customer_id == "cus_existing_at_register"

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch("app.routes.billing.stripe") as mock_stripe,
        patch("app.billing.primitives.stripe") as mock_prim_stripe,
    ):
        mock_stripe.PaymentIntent.create.return_value = mock_intent
        resp = await client.post(
            "/billing/topup",
            json={"amount_micros": 5_000_000},
            headers=_auth_headers(access_token),
        )

    assert resp.status_code == 200, resp.text
    # create_customer must NOT have been called a second time.
    mock_prim_stripe.Customer.create.assert_not_called()


# ---------------------------------------------------------------------------
# GET /billing/balance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_balance_endpoint_requires_auth(client):
    """No token → 401 or 404 (route not mounted when billing disabled)."""
    resp = await client.get("/billing/balance")
    assert resp.status_code in (401, 404)


@pytest.mark.asyncio
async def test_balance_endpoint_returns_zero_for_new_user(client):
    """Authed new user with no ledger entries → {balance_micros: 0, formatted: '$0.00'}."""
    from app.config import settings

    reg_resp, _ = await _register_and_verify(client, "balzero@example.com")
    access_token = reg_resp.get("access_token")

    with patch.object(settings, "BILLING_ENABLED", True):
        resp = await client.get("/billing/balance", headers=_auth_headers(access_token))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["balance_micros"] == 0
    assert body["formatted"] == "$0.00"


@pytest.mark.asyncio
async def test_balance_endpoint_returns_correct_format_for_whole_cents(client):
    """Ledger grant of $1.23 → formatted='$1.23'."""
    from app import billing
    from app.config import settings

    reg_resp, user_id = await _register_and_verify(client, "bal123@example.com")
    access_token = reg_resp.get("access_token")

    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            await billing.grant(
                user_id=user_id,
                amount_micros=1_230_000,
                reason=billing.Reason.TOPUP,
                db=session,
            )

    with patch.object(settings, "BILLING_ENABLED", True):
        resp = await client.get("/billing/balance", headers=_auth_headers(access_token))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["balance_micros"] == 1_230_000
    assert body["formatted"] == "$1.23"


@pytest.mark.asyncio
async def test_balance_endpoint_returns_correct_format_for_sub_cent(client):
    """Ledger grant of $0.0034 → formatted='$0.0034'."""
    from app import billing
    from app.config import settings

    reg_resp, user_id = await _register_and_verify(client, "balsub@example.com")
    access_token = reg_resp.get("access_token")

    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            await billing.grant(
                user_id=user_id,
                amount_micros=3_400,
                reason=billing.Reason.TOPUP,
                db=session,
            )

    with patch.object(settings, "BILLING_ENABLED", True):
        resp = await client.get("/billing/balance", headers=_auth_headers(access_token))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["balance_micros"] == 3_400
    assert body["formatted"] == "$0.0034"


@pytest.mark.asyncio
async def test_balance_endpoint_sums_multiple_entries(client):
    """Two grants + one debit → balance reflects sum."""
    from app import billing
    from app.config import settings

    reg_resp, user_id = await _register_and_verify(client, "balsum@example.com")
    access_token = reg_resp.get("access_token")

    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            await billing.grant(
                user_id=user_id,
                amount_micros=5_000_000,
                reason=billing.Reason.TOPUP,
                db=session,
            )
            await billing.grant(
                user_id=user_id,
                amount_micros=2_000_000,
                reason=billing.Reason.SIGNUP_BONUS,
                db=session,
            )

    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            await billing.debit(
                user_id=user_id,
                amount_micros=1_000_000,
                ref_type="test",
                ref_id="test-1",
                db=session,
            )

    with patch.object(settings, "BILLING_ENABLED", True):
        resp = await client.get("/billing/balance", headers=_auth_headers(access_token))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["balance_micros"] == 6_000_000  # 5M + 2M - 1M


# ---------------------------------------------------------------------------
# Handler unit tests (direct invocation, no HTTP)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_payment_intent_succeeded_grants_balance():
    """Mock PI event with user_id + amount → ledger row inserted with reason=topup."""
    from app.billing.handlers.topup import handle_payment_intent_succeeded

    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            from app.services.auth_service import hash_password

            user = User(
                email="handler@example.com",
                password_hash=hash_password("Password123!"),
                full_name="Handler Test",
            )
            session.add(user)
            await session.flush()
            user_id = user.id

    # Build a mock Stripe event.
    event = MagicMock()
    event.id = "evt_handler_001"
    event.data.object.metadata = {"user_id": str(user_id)}
    event.data.object.amount = 500  # 500 cents = $5.00

    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            await handle_payment_intent_succeeded(event, session)

    # Verify ledger row.
    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(
            select(BalanceLedger).where(BalanceLedger.user_id == user_id)
        )
        entries = result.scalars().all()

    assert len(entries) == 1
    assert entries[0].amount_micros == 5_000_000  # 500 cents * 10_000
    assert entries[0].reason == "topup"
    assert entries[0].stripe_event_id == "evt_handler_001"


@pytest.mark.asyncio
async def test_handle_payment_intent_succeeded_skips_missing_metadata():
    """Event without metadata.user_id → handler returns; no ledger row."""
    from app.billing.handlers.topup import handle_payment_intent_succeeded

    event = MagicMock()
    event.id = "evt_no_meta"
    event.data.object.metadata = {}
    event.data.object.amount = 500

    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            await handle_payment_intent_succeeded(event, session)
            result = await session.execute(select(BalanceLedger))
            entries = result.scalars().all()

    assert len(entries) == 0


@pytest.mark.asyncio
async def test_handle_payment_intent_succeeded_skips_invalid_user_id():
    """metadata.user_id='abc' (non-int) → handler returns; no ledger row."""
    from app.billing.handlers.topup import handle_payment_intent_succeeded

    event = MagicMock()
    event.id = "evt_bad_userid"
    event.data.object.metadata = {"user_id": "abc"}
    event.data.object.amount = 500

    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            await handle_payment_intent_succeeded(event, session)
            result = await session.execute(select(BalanceLedger))
            entries = result.scalars().all()

    assert len(entries) == 0


@pytest.mark.asyncio
async def test_handle_payment_intent_succeeded_skips_zero_amount():
    """amount=0 → handler returns; no ledger row."""
    from app.billing.handlers.topup import handle_payment_intent_succeeded

    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            from app.services.auth_service import hash_password

            user = User(
                email="zeroamt@example.com",
                password_hash=hash_password("Password123!"),
                full_name="Zero Amt",
            )
            session.add(user)
            await session.flush()
            user_id = user.id

    event = MagicMock()
    event.id = "evt_zero_amt"
    event.data.object.metadata = {"user_id": str(user_id)}
    event.data.object.amount = 0

    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            await handle_payment_intent_succeeded(event, session)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(
            select(BalanceLedger).where(BalanceLedger.user_id == user_id)
        )
        entries = result.scalars().all()

    assert len(entries) == 0


# ---------------------------------------------------------------------------
# Webhook dispatch integration tests (HTTP via minimal test app)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_webhook_dispatches_to_registered_handler():
    """Valid signed payment_intent.succeeded → handler invoked; stripe_events row inserted."""
    import httpx

    from app.config import settings

    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            from app.services.auth_service import hash_password

            user = User(
                email="wh_dispatch@example.com",
                password_hash=hash_password("Password123!"),
                full_name="WH Dispatch",
            )
            session.add(user)
            await session.flush()
            user_id = user.id

    payload = _build_pi_event("evt_dispatch_001", str(user_id), 1000)
    header = _make_stripe_header(payload, _WEBHOOK_SECRET)

    with patch.object(settings, "STRIPE_WEBHOOK_SECRET", _WEBHOOK_SECRET):
        test_app = _make_billing_test_app()
        transport = httpx.ASGITransport(app=test_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post(
                "/billing/webhook",
                content=payload.encode(),
                headers={"stripe-signature": header, "content-type": "application/json"},
            )

    assert resp.status_code == 200

    # stripe_events row inserted.
    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(
            select(StripeEvent).where(StripeEvent.id == "evt_dispatch_001")
        )
        row = result.scalar_one_or_none()
    assert row is not None
    assert row.event_type == "payment_intent.succeeded"

    # Ledger entry created.
    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(
            select(BalanceLedger).where(BalanceLedger.user_id == user_id)
        )
        entries = result.scalars().all()
    assert len(entries) == 1
    assert entries[0].amount_micros == 10_000_000  # 1000 cents * 10_000
    assert entries[0].reason == "topup"
    assert entries[0].stripe_event_id == "evt_dispatch_001"


@pytest.mark.asyncio
async def test_webhook_duplicate_event_skips_handler_call():
    """Same event.id posted twice → handler invoked exactly once; single stripe_events row."""
    import httpx

    from app.config import settings

    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            from app.services.auth_service import hash_password

            user = User(
                email="wh_dedup@example.com",
                password_hash=hash_password("Password123!"),
                full_name="WH Dedup",
            )
            session.add(user)
            await session.flush()
            user_id = user.id

    payload = _build_pi_event("evt_dedup_001", str(user_id), 500)

    with patch.object(settings, "STRIPE_WEBHOOK_SECRET", _WEBHOOK_SECRET):
        test_app = _make_billing_test_app()
        transport = httpx.ASGITransport(app=test_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            header1 = _make_stripe_header(payload, _WEBHOOK_SECRET)
            resp1 = await c.post(
                "/billing/webhook",
                content=payload.encode(),
                headers={"stripe-signature": header1, "content-type": "application/json"},
            )
            header2 = _make_stripe_header(payload, _WEBHOOK_SECRET)
            resp2 = await c.post(
                "/billing/webhook",
                content=payload.encode(),
                headers={"stripe-signature": header2, "content-type": "application/json"},
            )

    assert resp1.status_code == 200
    assert resp2.status_code == 200

    # Only one stripe_events row.
    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(select(StripeEvent).where(StripeEvent.id == "evt_dedup_001"))
        rows = result.scalars().all()
    assert len(rows) == 1

    # Handler invoked exactly once → only one ledger entry.
    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(
            select(BalanceLedger).where(BalanceLedger.user_id == user_id)
        )
        entries = result.scalars().all()
    assert len(entries) == 1


@pytest.mark.asyncio
async def test_webhook_unregistered_event_type_still_records():
    """customer.updated (no handler registered) → logs warning, inserts stripe_events row, 200."""
    import httpx

    from app.config import settings

    payload = _build_unregistered_event("evt_unreg_001")
    header = _make_stripe_header(payload, _WEBHOOK_SECRET)

    with patch.object(settings, "STRIPE_WEBHOOK_SECRET", _WEBHOOK_SECRET):
        test_app = _make_billing_test_app()
        transport = httpx.ASGITransport(app=test_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post(
                "/billing/webhook",
                content=payload.encode(),
                headers={"stripe-signature": header, "content-type": "application/json"},
            )

    assert resp.status_code == 200

    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(select(StripeEvent).where(StripeEvent.id == "evt_unreg_001"))
        row = result.scalar_one_or_none()
    assert row is not None
    assert row.event_type == "customer.updated"


@pytest.mark.asyncio
async def test_webhook_concurrent_delivery_no_race():
    """Two concurrent POSTs with the same event.id → both return 200, handler runs once."""
    import httpx

    from app.config import settings

    # Set up: create a verified user (topup handler needs metadata.user_id)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = await _make_verified_user(session, email="concurrent@example.com")
        user_id = user.id

    payload = _build_pi_succeeded_event(
        event_id="evt_concurrent_001",
        user_id=user_id,
        amount_cents=2000,
    )
    encoded, sig_header = _sign_event(payload)

    with patch.object(settings, "STRIPE_WEBHOOK_SECRET", _WEBHOOK_SECRET):
        test_app = _make_billing_test_app()
        transport = httpx.ASGITransport(app=test_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            # Fire two concurrent POSTs — asyncio.gather runs both before either completes
            responses = await asyncio.gather(
                c.post(
                    "/billing/webhook",
                    content=encoded,
                    headers={"stripe-signature": sig_header, "content-type": "application/json"},
                ),
                c.post(
                    "/billing/webhook",
                    content=encoded,
                    headers={"stripe-signature": sig_header, "content-type": "application/json"},
                ),
            )

    # Both must succeed
    assert all(r.status_code == 200 for r in responses), [r.status_code for r in responses]

    # Exactly one stripe_events row
    async with AsyncSession(engine, expire_on_commit=False) as session:
        stripe_event_rows = (
            await session.execute(select(StripeEvent).where(StripeEvent.id == "evt_concurrent_001"))
        ).all()
    assert len(stripe_event_rows) == 1

    # Exactly one balance_ledger row tied to this stripe_event_id (handler ran once)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        ledger_rows = (
            await session.execute(
                select(BalanceLedger).where(BalanceLedger.stripe_event_id == "evt_concurrent_001")
            )
        ).all()
    assert len(ledger_rows) == 1
    assert ledger_rows[0][0].amount_micros == 20_000_000  # 2000 cents * 10_000


@pytest.mark.asyncio
async def test_webhook_handler_failure_does_not_record_event(monkeypatch):
    """Handler exception → transaction rolls back → no stripe_events row → Stripe will retry."""
    import httpx

    from app.billing.handlers import EVENT_HANDLERS
    from app.config import settings

    # Monkey-patch the topup handler to raise an unexpected exception.
    async def _failing_handler(event, db):
        raise RuntimeError("simulated handler bug")

    monkeypatch.setitem(EVENT_HANDLERS, "payment_intent.succeeded", _failing_handler)

    payload = _build_pi_succeeded_event(event_id="evt_fail_001", user_id=1, amount_cents=1000)
    encoded, sig_header = _sign_event(payload)

    with patch.object(settings, "STRIPE_WEBHOOK_SECRET", _WEBHOOK_SECRET):
        test_app = _make_billing_test_app()
        # raise_app_exceptions=False so httpx converts unhandled app errors → 500
        # rather than propagating them as Python exceptions into the test.
        transport = httpx.ASGITransport(app=test_app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post(
                "/billing/webhook",
                content=encoded,
                headers={"stripe-signature": sig_header, "content-type": "application/json"},
            )

    assert resp.status_code >= 500  # handler raised → uncaught → 5xx

    # No stripe_events row — Stripe will retry the event later
    async with AsyncSession(engine, expire_on_commit=False) as session:
        rows = (
            await session.execute(select(StripeEvent).where(StripeEvent.id == "evt_fail_001"))
        ).all()
    assert len(rows) == 0

    # No balance_ledger row either
    async with AsyncSession(engine, expire_on_commit=False) as session:
        ledger_rows = (
            await session.execute(
                select(BalanceLedger).where(BalanceLedger.stripe_event_id == "evt_fail_001")
            )
        ).all()
    assert len(ledger_rows) == 0
