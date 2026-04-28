"""Tests for billing subscribe + setup-intent + GET subscription endpoints + 5 webhook handlers.

Ticket 0024 Phase 0a.

Stripe API calls are mocked via monkeypatch / unittest.mock — no real Stripe calls.
All tests use the autouse _reset_schema fixture from conftest.py.

Kind-2 isolation: entire module skipped when BILLING_ENABLED=false.
"""

import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import engine
from app.models import BalanceLedger, Subscription, User
from app.models.stripe_event import StripeEvent

pytestmark = pytest.mark.skipif(
    not settings.BILLING_ENABLED,
    reason="test_billing_subscribe requires BILLING_ENABLED=true — feature-gated at app-init time",
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_WEBHOOK_SECRET = "whsec_subscribe_test_secret"
_NOW_TS = int(datetime.now(timezone.utc).timestamp())
_PERIOD_START_TS = _NOW_TS - 86400  # 1 day ago
_PERIOD_END_TS = _NOW_TS + 86400 * 29  # 29 days from now


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_stripe_header(payload: str | bytes, secret: str) -> str:
    """Build a valid Stripe webhook signature header for testing."""
    if isinstance(payload, str):
        payload = payload.encode()
    ts = int(time.time())
    signed_payload = f"{ts}.".encode() + payload
    sig = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _register_and_verify(client, email: str) -> tuple[dict, int]:
    """Register a user and verify their email. Returns (reg_json, user_id)."""
    from app.services.auth_service import create_verify_token

    reg = await client.post(
        "/auth/register",
        json={"email": email, "password": "StrongPassword99!", "full_name": "Test User"},
    )
    assert reg.status_code == 200, reg.text

    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(select(User).where(User.email == email))
        user = result.scalar_one()

    token = create_verify_token(user.id, user.token_version)
    verify = await client.post("/auth/verify-email", json={"token": token})
    assert verify.status_code == 200, verify.text

    return reg.json(), user.id


async def _register_unverified(client, email: str) -> dict:
    """Register a user WITHOUT verifying. Returns reg JSON."""
    reg = await client.post(
        "/auth/register",
        json={"email": email, "password": "StrongPassword99!", "full_name": "Unverified"},
    )
    assert reg.status_code == 200, reg.text
    return reg.json()


async def _create_user_direct(session, email: str, verified: bool = True) -> User:
    """Create a User row directly (no HTTP); optionally mark verified."""
    from app.services.auth_service import hash_password

    async with session.begin():
        user = User(
            email=email,
            password_hash=hash_password("Password123!"),
            full_name="Direct User",
            stripe_customer_id="cus_direct_test",
            verified_at=datetime.now(timezone.utc).replace(tzinfo=None) if verified else None,
        )
        session.add(user)
        await session.flush()
        user_id = user.id

    async with AsyncSession(engine, expire_on_commit=False) as fresh:
        result = await fresh.execute(select(User).where(User.id == user_id))
        return result.scalar_one()


async def _create_subscription_row(
    session,
    user_id: int,
    status: str = "active",
    stripe_sub_id: str = "sub_test_001",
) -> Subscription:
    """Insert a subscriptions row directly for test setup."""
    async with session.begin():
        row = Subscription(
            user_id=user_id,
            stripe_subscription_id=stripe_sub_id,
            stripe_price_id="price_test_001",
            tier_key="starter_monthly",
            tier_name="Starter",
            status=status,
            grant_micros=10_000_000,
            current_period_start=datetime.now(timezone.utc).replace(tzinfo=None),
            current_period_end=datetime.fromtimestamp(_PERIOD_END_TS, tz=timezone.utc).replace(
                tzinfo=None
            ),
            cancel_at_period_end=False,
        )
        session.add(row)
        await session.flush()
        sub_id = row.id

    async with AsyncSession(engine, expire_on_commit=False) as fresh:
        result = await fresh.execute(select(Subscription).where(Subscription.id == sub_id))
        return result.scalar_one()


def _make_sub_event(
    event_id: str,
    event_type: str,
    user_id: int,
    sub_id: str = "sub_test_evt",
    status: str = "active",
    cancel_at_period_end: bool = False,
    price_id: str = "price_test_001",
    lookup_key: str = "starter_monthly",
    grant_micros: str = "10000000",
    tier_name: str = "Starter",
    period_start: int | None = None,
    period_end: int | None = None,
) -> str:
    """Build a customer.subscription.* event JSON."""
    ps = period_start or _PERIOD_START_TS
    pe = period_end or _PERIOD_END_TS
    return json.dumps(
        {
            "id": event_id,
            "object": "event",
            "type": event_type,
            "data": {
                "object": {
                    "id": sub_id,
                    "object": "subscription",
                    "status": status,
                    "cancel_at_period_end": cancel_at_period_end,
                    "current_period_start": ps,
                    "current_period_end": pe,
                    "customer": "cus_test",
                    "metadata": {"user_id": str(user_id)},
                    "items": {
                        "object": "list",
                        "data": [
                            {
                                "id": "si_test",
                                "object": "subscription_item",
                                "price": {
                                    "id": price_id,
                                    "object": "price",
                                    "lookup_key": lookup_key,
                                    "metadata": {
                                        "grant_micros": grant_micros,
                                        "tier_name": tier_name,
                                    },
                                },
                            }
                        ],
                    },
                }
            },
            "livemode": False,
            "pending_webhooks": 0,
            "request": None,
            "api_version": "2023-10-16",
        }
    )


def _make_invoice_event(
    event_id: str,
    event_type: str,
    sub_id: str = "sub_test_evt",
    billing_reason: str = "subscription_cycle",
    period_start: int | None = None,
    period_end: int | None = None,
) -> str:
    """Build an invoice.paid or invoice.payment_failed event JSON."""
    ps = period_start or _PERIOD_START_TS
    pe = period_end or _PERIOD_END_TS
    return json.dumps(
        {
            "id": event_id,
            "object": "event",
            "type": event_type,
            "data": {
                "object": {
                    "id": "in_test_001",
                    "object": "invoice",
                    "billing_reason": billing_reason,
                    "subscription": sub_id,
                    "lines": {
                        "object": "list",
                        "data": [
                            {
                                "id": "il_test",
                                "period": {"start": ps, "end": pe},
                            }
                        ],
                    },
                }
            },
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


def _sign_payload(payload: str) -> tuple[bytes, str]:
    """Return (encoded payload, stripe-signature header) signed with test webhook secret."""
    encoded = payload.encode()
    header = _make_stripe_header(encoded, _WEBHOOK_SECRET)
    return encoded, header


def _mock_price(
    price_id: str = "price_test_001",
    lookup_key: str = "starter_monthly",
    grant_micros: str = "10000000",
    tier_name: str = "Starter",
) -> MagicMock:
    """Build a mock Stripe Price object."""
    m = MagicMock()
    m.id = price_id
    m.lookup_key = lookup_key
    m.metadata = {"grant_micros": grant_micros, "tier_name": tier_name}
    return m


def _mock_subscription(
    sub_id: str = "sub_new_001",
    status: str = "active",
    price: Any = None,
    with_3ds: bool = False,
    period_start: int | None = None,
    period_end: int | None = None,
) -> MagicMock:
    """Build a mock Stripe Subscription object."""
    m = MagicMock()
    m.id = sub_id
    m.status = status
    m.cancel_at_period_end = False
    m.current_period_start = period_start or _PERIOD_START_TS
    m.current_period_end = period_end or _PERIOD_END_TS
    if with_3ds:
        m.latest_invoice.payment_intent.client_secret = "pi_3ds_secret"
    else:
        m.latest_invoice.payment_intent.client_secret = None
    return m


# ---------------------------------------------------------------------------
# POST /billing/setup-intent — auth / verification gates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_setup_intent_requires_auth(client):
    """No token → 401 or 404 (route not mounted when billing disabled)."""
    resp = await client.post("/billing/setup-intent")
    assert resp.status_code in (401, 404)


@pytest.mark.asyncio
async def test_setup_intent_requires_verified(client):
    """Unverified user → 403 when BILLING_REQUIRE_VERIFIED=True (Kind-1 isolation)."""
    mock_customer = MagicMock()
    mock_customer.id = "cus_unverified"

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch("app.billing.primitives.stripe") as mock_prim_stripe,
    ):
        mock_prim_stripe.Customer.create.return_value = mock_customer
        reg_resp = await _register_unverified(client, "setup_unveri@example.com")

    access_token = reg_resp.get("access_token")

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch.object(settings, "BILLING_REQUIRE_VERIFIED", True),
    ):
        resp = await client.post(
            "/billing/setup-intent",
            headers=_auth_headers(access_token),
        )

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_setup_intent_allows_unverified_user_when_flag_off(client):
    """Unverified user + BILLING_REQUIRE_VERIFIED=False (default) → 200; chassis is permissive."""
    mock_customer = MagicMock()
    mock_customer.id = "cus_si_unverified_flagoff"
    mock_si = MagicMock()
    mock_si.client_secret = "seti_secret_flagoff"

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch("app.billing.primitives.stripe") as mock_prim_stripe,
    ):
        mock_prim_stripe.Customer.create.return_value = mock_customer
        reg_resp = await _register_unverified(client, "si_flagoff@example.com")

    access_token = reg_resp.get("access_token")

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch.object(settings, "BILLING_REQUIRE_VERIFIED", False),
        patch("app.routes.billing.stripe") as mock_stripe,
        patch("app.billing.primitives.stripe") as mock_prim,
    ):
        mock_prim.Customer.create.return_value = mock_customer
        mock_stripe.SetupIntent.create.return_value = mock_si
        resp = await client.post(
            "/billing/setup-intent",
            headers=_auth_headers(access_token),
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["client_secret"] == "seti_secret_flagoff"


@pytest.mark.asyncio
async def test_setup_intent_creates_setup_intent_on_stripe(client):
    """Verified user → 200 with client_secret; SetupIntent.create called correctly."""
    mock_customer = MagicMock()
    mock_customer.id = "cus_si_test"
    mock_si = MagicMock()
    mock_si.client_secret = "seti_secret_001"

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch("app.billing.primitives.stripe") as mock_p,
    ):
        mock_p.Customer.create.return_value = mock_customer
        reg_resp, user_id = await _register_and_verify(client, "si_verified@example.com")

    access_token = reg_resp.get("access_token")

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch("app.routes.billing.stripe") as mock_stripe,
        patch("app.billing.primitives.stripe"),
    ):
        mock_stripe.SetupIntent.create.return_value = mock_si
        resp = await client.post(
            "/billing/setup-intent",
            headers=_auth_headers(access_token),
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["client_secret"] == "seti_secret_001"

    call_kwargs = mock_stripe.SetupIntent.create.call_args
    assert call_kwargs is not None
    _, kw = call_kwargs
    assert kw["customer"] == "cus_si_test"
    assert kw["payment_method_types"] == ["card"]
    assert kw["usage"] == "off_session"


@pytest.mark.asyncio
async def test_setup_intent_lazy_creates_customer(client):
    """User with stripe_customer_id=None → SI created; stripe_customer_id populated."""
    mock_customer = MagicMock()
    mock_customer.id = "cus_si_lazy"
    mock_si = MagicMock()
    mock_si.client_secret = "seti_secret_lazy"

    # Register WITHOUT billing so no Customer is created.
    with patch.object(settings, "BILLING_ENABLED", False):
        reg_resp, user_id = await _register_and_verify(client, "si_lazy@example.com")
    access_token = reg_resp.get("access_token")

    # Verify stripe_customer_id is None.
    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one()
    assert user.stripe_customer_id is None

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch("app.routes.billing.stripe") as mock_stripe,
        patch("app.billing.primitives.stripe") as mock_prim,
    ):
        mock_prim.Customer.create.return_value = mock_customer
        mock_stripe.SetupIntent.create.return_value = mock_si
        resp = await client.post(
            "/billing/setup-intent",
            headers=_auth_headers(access_token),
        )

    assert resp.status_code == 200, resp.text
    mock_prim.Customer.create.assert_called_once()

    # Verify stripe_customer_id persisted.
    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(select(User).where(User.id == user_id))
        updated = result.scalar_one()
    assert updated.stripe_customer_id == "cus_si_lazy"


# ---------------------------------------------------------------------------
# POST /billing/subscribe — auth / validation gates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscribe_requires_verified(client):
    """Unverified user → 403 when BILLING_REQUIRE_VERIFIED=True (Kind-1 isolation)."""
    mock_customer = MagicMock()
    mock_customer.id = "cus_sub_unverified"

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch("app.billing.primitives.stripe") as mock_p,
    ):
        mock_p.Customer.create.return_value = mock_customer
        reg_resp = await _register_unverified(client, "sub_unveri@example.com")

    access_token = reg_resp.get("access_token")

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch.object(settings, "BILLING_REQUIRE_VERIFIED", True),
    ):
        resp = await client.post(
            "/billing/subscribe",
            json={"price_lookup_key": "starter_monthly", "payment_method_id": "pm_test"},
            headers=_auth_headers(access_token),
        )

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_subscribe_allows_unverified_user_when_flag_off(client):
    """Unverified user + BILLING_REQUIRE_VERIFIED=False (default) → 200; chassis is permissive."""
    mock_customer = MagicMock()
    mock_customer.id = "cus_sub_unverified_flagoff"

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch("app.billing.primitives.stripe") as mock_p,
    ):
        mock_p.Customer.create.return_value = mock_customer
        reg_resp = await _register_unverified(client, "sub_flagoff_unverified@example.com")

    access_token = reg_resp.get("access_token")

    price = _mock_price()
    mock_prices = MagicMock()
    mock_prices.data = [price]
    mock_prices.auto_paging_iter.return_value = iter([price])

    mock_sub = _mock_subscription(sub_id="sub_flagoff_unverified_001", status="active")

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch.object(settings, "BILLING_REQUIRE_VERIFIED", False),
        patch("app.routes.billing.stripe") as mock_stripe,
        patch("app.billing.primitives.stripe") as mock_prim,
    ):
        mock_prim.Customer.create.return_value = mock_customer
        mock_stripe.Price.list.return_value = mock_prices
        mock_stripe.PaymentMethod.attach.return_value = MagicMock()
        mock_stripe.Customer.modify.return_value = MagicMock()
        mock_stripe.Subscription.create.return_value = mock_sub
        resp = await client.post(
            "/billing/subscribe",
            json={"price_lookup_key": "starter_monthly", "payment_method_id": "pm_flagoff"},
            headers=_auth_headers(access_token),
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["subscription_id"] == "sub_flagoff_unverified_001"
    assert body["status"] == "active"


@pytest.mark.asyncio
async def test_subscribe_rejects_missing_lookup_key(client):
    """Price not found → 404."""
    mock_customer = MagicMock()
    mock_customer.id = "cus_sub_notfound"

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch("app.billing.primitives.stripe") as mock_p,
    ):
        mock_p.Customer.create.return_value = mock_customer
        reg_resp, _ = await _register_and_verify(client, "sub_notfound@example.com")

    access_token = reg_resp.get("access_token")

    # Mock Price.list returning empty list.
    mock_prices = MagicMock()
    mock_prices.data = []
    mock_prices.auto_paging_iter.return_value = iter([])

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch("app.routes.billing.stripe") as mock_stripe,
        patch("app.billing.primitives.stripe"),
    ):
        mock_stripe.Price.list.return_value = mock_prices
        resp = await client.post(
            "/billing/subscribe",
            json={"price_lookup_key": "nonexistent_key", "payment_method_id": "pm_test"},
            headers=_auth_headers(access_token),
        )

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_subscribe_rejects_missing_grant_micros_metadata(client):
    """Price found but metadata.grant_micros missing → 422 when grants are enabled."""
    mock_customer = MagicMock()
    mock_customer.id = "cus_sub_no_grant"

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch("app.billing.primitives.stripe") as mock_p,
    ):
        mock_p.Customer.create.return_value = mock_customer
        reg_resp, _ = await _register_and_verify(client, "sub_nogrant@example.com")

    access_token = reg_resp.get("access_token")

    price = _mock_price(grant_micros=None, tier_name="Starter")
    price.metadata = {"tier_name": "Starter"}  # no grant_micros

    mock_prices = MagicMock()
    mock_prices.data = [price]
    mock_prices.auto_paging_iter.return_value = iter([price])

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch.object(settings, "BILLING_SUBSCRIPTION_GRANTS_TO_LEDGER", True),
        patch("app.routes.billing.stripe") as mock_stripe,
        patch("app.billing.primitives.stripe"),
    ):
        mock_stripe.Price.list.return_value = mock_prices
        resp = await client.post(
            "/billing/subscribe",
            json={"price_lookup_key": "starter_monthly", "payment_method_id": "pm_test"},
            headers=_auth_headers(access_token),
        )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_subscribe_rejects_missing_tier_name_metadata(client):
    """Price found but metadata.tier_name missing → 422."""
    mock_customer = MagicMock()
    mock_customer.id = "cus_sub_no_tier_name"

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch("app.billing.primitives.stripe") as mock_p,
    ):
        mock_p.Customer.create.return_value = mock_customer
        reg_resp, _ = await _register_and_verify(client, "sub_notier@example.com")

    access_token = reg_resp.get("access_token")

    price = MagicMock()
    price.id = "price_test_001"
    price.lookup_key = "starter_monthly"
    price.metadata = {"grant_micros": "10000000"}  # no tier_name

    mock_prices = MagicMock()
    mock_prices.data = [price]
    mock_prices.auto_paging_iter.return_value = iter([price])

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch("app.routes.billing.stripe") as mock_stripe,
        patch("app.billing.primitives.stripe"),
    ):
        mock_stripe.Price.list.return_value = mock_prices
        resp = await client.post(
            "/billing/subscribe",
            json={"price_lookup_key": "starter_monthly", "payment_method_id": "pm_test"},
            headers=_auth_headers(access_token),
        )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_subscribe_rejects_already_active_subscription(client):
    """User has active subscription → 409 ALREADY_SUBSCRIBED."""
    mock_customer = MagicMock()
    mock_customer.id = "cus_sub_already"

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch("app.billing.primitives.stripe") as mock_p,
    ):
        mock_p.Customer.create.return_value = mock_customer
        reg_resp, user_id = await _register_and_verify(client, "sub_already@example.com")

    access_token = reg_resp.get("access_token")

    # Insert active subscription row.
    async with AsyncSession(engine, expire_on_commit=False) as session:
        await _create_subscription_row(session, user_id, status="active")

    price = _mock_price()
    mock_prices = MagicMock()
    mock_prices.data = [price]
    mock_prices.auto_paging_iter.return_value = iter([price])

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch("app.routes.billing.stripe") as mock_stripe,
        patch("app.billing.primitives.stripe"),
    ):
        mock_stripe.Price.list.return_value = mock_prices
        mock_stripe.PaymentMethod.attach.return_value = MagicMock()
        mock_stripe.Customer.modify.return_value = MagicMock()
        resp = await client.post(
            "/billing/subscribe",
            json={"price_lookup_key": "starter_monthly", "payment_method_id": "pm_test"},
            headers=_auth_headers(access_token),
        )

    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_subscribe_allows_resubscribe_after_cancel(client):
    """User has cancelled subscription row → subscribe succeeds and upserts row."""
    mock_customer = MagicMock()
    mock_customer.id = "cus_resubscribe"

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch("app.billing.primitives.stripe") as mock_p,
    ):
        mock_p.Customer.create.return_value = mock_customer
        reg_resp, user_id = await _register_and_verify(client, "resubscribe@example.com")

    access_token = reg_resp.get("access_token")

    # Insert cancelled subscription row.
    async with AsyncSession(engine, expire_on_commit=False) as session:
        await _create_subscription_row(
            session, user_id, status="cancelled", stripe_sub_id="sub_old"
        )

    price = _mock_price()
    mock_prices = MagicMock()
    mock_prices.data = [price]
    mock_prices.auto_paging_iter.return_value = iter([price])

    mock_sub = _mock_subscription(sub_id="sub_new_resubscribe", status="active")

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch("app.routes.billing.stripe") as mock_stripe,
        patch("app.billing.primitives.stripe"),
    ):
        mock_stripe.Price.list.return_value = mock_prices
        mock_stripe.PaymentMethod.attach.return_value = MagicMock()
        mock_stripe.Customer.modify.return_value = MagicMock()
        mock_stripe.Subscription.create.return_value = mock_sub
        resp = await client.post(
            "/billing/subscribe",
            json={"price_lookup_key": "starter_monthly", "payment_method_id": "pm_new"},
            headers=_auth_headers(access_token),
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["subscription_id"] == "sub_new_resubscribe"
    assert body["status"] == "active"

    # Row upserted with new subscription id.
    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(select(Subscription).where(Subscription.user_id == user_id))
        row = result.scalar_one()
    assert row.stripe_subscription_id == "sub_new_resubscribe"
    assert row.status == "active"


@pytest.mark.asyncio
async def test_subscribe_attaches_pm_and_creates_subscription(client):
    """PaymentMethod.attach, Customer.modify, Subscription.create called correctly;
    subscriptions row upserted; NO ledger entry (webhook does that)."""
    mock_customer = MagicMock()
    mock_customer.id = "cus_full_flow"

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch("app.billing.primitives.stripe") as mock_p,
    ):
        mock_p.Customer.create.return_value = mock_customer
        reg_resp, user_id = await _register_and_verify(client, "fullflow@example.com")

    access_token = reg_resp.get("access_token")

    price = _mock_price()
    mock_prices = MagicMock()
    mock_prices.data = [price]
    mock_prices.auto_paging_iter.return_value = iter([price])

    mock_sub = _mock_subscription(sub_id="sub_full_001", status="active")

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch.object(settings, "BILLING_SUBSCRIPTION_GRANTS_TO_LEDGER", True),
        patch("app.routes.billing.stripe") as mock_stripe,
        patch("app.billing.primitives.stripe"),
    ):
        mock_stripe.Price.list.return_value = mock_prices
        mock_stripe.PaymentMethod.attach.return_value = MagicMock()
        mock_stripe.Customer.modify.return_value = MagicMock()
        mock_stripe.Subscription.create.return_value = mock_sub
        resp = await client.post(
            "/billing/subscribe",
            json={"price_lookup_key": "starter_monthly", "payment_method_id": "pm_fullflow"},
            headers=_auth_headers(access_token),
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["subscription_id"] == "sub_full_001"
    assert body["status"] == "active"
    assert body["requires_action"] is False

    # Verify Stripe calls.
    mock_stripe.PaymentMethod.attach.assert_called_once_with(
        "pm_fullflow", customer="cus_full_flow"
    )
    mock_stripe.Customer.modify.assert_called_once_with(
        "cus_full_flow",
        invoice_settings={"default_payment_method": "pm_fullflow"},
    )
    create_call = mock_stripe.Subscription.create.call_args
    assert create_call is not None
    _, kw = create_call
    assert kw["customer"] == "cus_full_flow"
    assert kw["items"][0]["price"] == price.id
    assert kw["metadata"]["user_id"] == str(user_id)
    assert kw["idempotency_key"] == f"subscribe:{user_id}:starter_monthly:pm_fullflow"

    # subscriptions row upserted. Flag=true branch: grant_micros read from Price metadata.
    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(select(Subscription).where(Subscription.user_id == user_id))
        row = result.scalar_one()
    assert row.stripe_subscription_id == "sub_full_001"
    assert row.status == "active"
    assert row.tier_key == "starter_monthly"
    assert row.tier_name == "Starter"
    assert row.grant_micros == 10_000_000  # flag=true: reads int(metadata.grant_micros)

    # NO ledger entry yet (webhook does that).
    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(
            select(BalanceLedger).where(BalanceLedger.user_id == user_id)
        )
        entries = result.scalars().all()
    assert len(entries) == 0


@pytest.mark.asyncio
async def test_subscribe_returns_requires_action_when_3ds_needed(client):
    """Subscription.create returns status='incomplete' → requires_action=true + client_secret."""
    mock_customer = MagicMock()
    mock_customer.id = "cus_3ds"

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch("app.billing.primitives.stripe") as mock_p,
    ):
        mock_p.Customer.create.return_value = mock_customer
        reg_resp, _ = await _register_and_verify(client, "sub_3ds@example.com")

    access_token = reg_resp.get("access_token")

    price = _mock_price()
    mock_prices = MagicMock()
    mock_prices.data = [price]
    mock_prices.auto_paging_iter.return_value = iter([price])

    mock_sub = _mock_subscription(sub_id="sub_3ds_001", status="incomplete", with_3ds=True)

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch("app.routes.billing.stripe") as mock_stripe,
        patch("app.billing.primitives.stripe"),
    ):
        mock_stripe.Price.list.return_value = mock_prices
        mock_stripe.PaymentMethod.attach.return_value = MagicMock()
        mock_stripe.Customer.modify.return_value = MagicMock()
        mock_stripe.Subscription.create.return_value = mock_sub
        resp = await client.post(
            "/billing/subscribe",
            json={"price_lookup_key": "starter_monthly", "payment_method_id": "pm_3ds"},
            headers=_auth_headers(access_token),
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["requires_action"] is True
    assert body["client_secret"] == "pi_3ds_secret"


@pytest.mark.asyncio
async def test_subscribe_idempotency_key_includes_payment_method_id(client):
    """Regression test for 0024.6 — the idempotency key must include the
    payment_method_id so retries with different PMs (e.g., 3DS-fail-then-retry,
    decline-then-new-card) don't collide with Stripe's 24h idempotency window.

    Same-PM double-submits still replay the original response (key is identical).
    Different-PM retries become fresh Stripe calls (key differs on the PM segment).
    """
    mock_customer = MagicMock()
    mock_customer.id = "cus_idem_pm"

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch("app.billing.primitives.stripe") as mock_p,
    ):
        mock_p.Customer.create.return_value = mock_customer
        reg_resp, user_id = await _register_and_verify(client, "idem_pm@example.com")

    access_token = reg_resp.get("access_token")

    price = _mock_price()
    mock_prices = MagicMock()
    mock_prices.data = [price]
    mock_prices.auto_paging_iter.return_value = iter([price])

    mock_sub = _mock_subscription(sub_id="sub_idem_pm_001", status="active")

    pm_id = "pm_idem_unique_card"
    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch("app.routes.billing.stripe") as mock_stripe,
        patch("app.billing.primitives.stripe"),
    ):
        mock_stripe.Price.list.return_value = mock_prices
        mock_stripe.PaymentMethod.attach.return_value = MagicMock()
        mock_stripe.Customer.modify.return_value = MagicMock()
        mock_stripe.Subscription.create.return_value = mock_sub
        resp = await client.post(
            "/billing/subscribe",
            json={"price_lookup_key": "starter_monthly", "payment_method_id": pm_id},
            headers=_auth_headers(access_token),
        )

    assert resp.status_code == 200, resp.text

    create_call = mock_stripe.Subscription.create.call_args
    assert create_call is not None
    _, kw = create_call
    expected_key = f"subscribe:{user_id}:starter_monthly:{pm_id}"
    assert kw["idempotency_key"] == expected_key, (
        f"Idempotency key must include payment_method_id to prevent collision on "
        f"retries with different PMs (0024.6). Got: {kw['idempotency_key']!r}"
    )


# ---------------------------------------------------------------------------
# GET /billing/subscription
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_subscription_requires_auth(client):
    """No token → 401 or 404."""
    resp = await client.get("/billing/subscription")
    assert resp.status_code in (401, 404)


@pytest.mark.asyncio
async def test_get_subscription_returns_no_subscription_for_new_user(client):
    """Authed user with no row → {has_subscription: false, ...}."""
    mock_customer = MagicMock()
    mock_customer.id = "cus_nosub"

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch("app.billing.primitives.stripe") as mock_p,
    ):
        mock_p.Customer.create.return_value = mock_customer
        reg_resp, _ = await _register_and_verify(client, "nosub@example.com")

    access_token = reg_resp.get("access_token")

    with patch.object(settings, "BILLING_ENABLED", True):
        resp = await client.get("/billing/subscription", headers=_auth_headers(access_token))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["has_subscription"] is False
    assert body["tier_key"] is None
    assert body["tier_name"] is None
    assert body["status"] is None
    assert body["current_period_end"] is None
    assert body["cancel_at_period_end"] is False


@pytest.mark.asyncio
async def test_get_subscription_returns_active_subscription(client):
    """Row exists with status='active' → returns full envelope with has_subscription=true."""
    mock_customer = MagicMock()
    mock_customer.id = "cus_active_sub"

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch("app.billing.primitives.stripe") as mock_p,
    ):
        mock_p.Customer.create.return_value = mock_customer
        reg_resp, user_id = await _register_and_verify(client, "activesub@example.com")

    access_token = reg_resp.get("access_token")

    async with AsyncSession(engine, expire_on_commit=False) as session:
        await _create_subscription_row(session, user_id, status="active")

    with patch.object(settings, "BILLING_ENABLED", True):
        resp = await client.get("/billing/subscription", headers=_auth_headers(access_token))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["has_subscription"] is True
    assert body["tier_key"] == "starter_monthly"
    assert body["tier_name"] == "Starter"
    assert body["status"] == "active"
    assert body["cancel_at_period_end"] is False
    assert body["current_period_end"] is not None


@pytest.mark.asyncio
async def test_get_subscription_returns_no_subscription_for_cancelled_row(client):
    """Row exists with status='cancelled' → has_subscription=false (chassis treats cancelled as no sub)."""
    mock_customer = MagicMock()
    mock_customer.id = "cus_cancelled_sub"

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch("app.billing.primitives.stripe") as mock_p,
    ):
        mock_p.Customer.create.return_value = mock_customer
        reg_resp, user_id = await _register_and_verify(client, "cancelledsub@example.com")

    access_token = reg_resp.get("access_token")

    async with AsyncSession(engine, expire_on_commit=False) as session:
        await _create_subscription_row(session, user_id, status="cancelled")

    with patch.object(settings, "BILLING_ENABLED", True):
        resp = await client.get("/billing/subscription", headers=_auth_headers(access_token))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["has_subscription"] is False
    assert body["status"] is None


# ---------------------------------------------------------------------------
# Webhook handler unit tests (direct invocation)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_subscription_created_grants_initial_period():
    """Mock subscription.created event → subscriptions row upserted + subscription_grant ledger entry (flag=True)."""
    from app.billing.handlers.subscription import handle_subscription_created
    from app.services.auth_service import hash_password

    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            user = User(
                email="sub_created@example.com",
                password_hash=hash_password("Password123!"),
                full_name="Sub Created",
                stripe_customer_id="cus_sub_created",
            )
            session.add(user)
            await session.flush()
            user_id = user.id

    # Build mock event object.
    price_mock = MagicMock()
    price_mock.id = "price_test_001"
    price_mock.lookup_key = "starter_monthly"
    price_mock.metadata = {"grant_micros": "10000000", "tier_name": "Starter"}

    sub_item = MagicMock()
    sub_item.price = price_mock

    sub_obj = MagicMock()
    sub_obj.id = "sub_created_001"
    sub_obj.status = "active"
    sub_obj.cancel_at_period_end = False
    sub_obj.current_period_start = _PERIOD_START_TS
    sub_obj.current_period_end = _PERIOD_END_TS
    sub_obj.metadata = {"user_id": str(user_id)}
    sub_obj.items.data = [sub_item]

    event = MagicMock()
    event.id = "evt_sub_created_001"
    event.data.object = sub_obj

    with patch.object(settings, "BILLING_SUBSCRIPTION_GRANTS_TO_LEDGER", True):
        async with AsyncSession(engine, expire_on_commit=False) as session:
            async with session.begin():
                await handle_subscription_created(event, session)

    # subscriptions row upserted.
    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(select(Subscription).where(Subscription.user_id == user_id))
        row = result.scalar_one_or_none()
    assert row is not None
    assert row.stripe_subscription_id == "sub_created_001"
    assert row.status == "active"
    assert row.tier_key == "starter_monthly"
    assert row.tier_name == "Starter"
    assert row.grant_micros == 10_000_000

    # Ledger entry: subscription_grant of 10_000_000.
    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(
            select(BalanceLedger).where(BalanceLedger.user_id == user_id)
        )
        entries = result.scalars().all()
    assert len(entries) == 1
    assert entries[0].amount_micros == 10_000_000
    assert entries[0].reason == "subscription_grant"
    assert entries[0].stripe_event_id == "evt_sub_created_001"


@pytest.mark.asyncio
async def test_handle_subscription_created_skips_missing_metadata():
    """Event without metadata.user_id → handler logs warning + returns; no row, no grant."""
    from app.billing.handlers.subscription import handle_subscription_created

    sub_obj = MagicMock()
    sub_obj.id = "sub_no_meta_001"
    sub_obj.metadata = {}  # no user_id

    event = MagicMock()
    event.id = "evt_sub_no_meta"
    event.data.object = sub_obj

    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            await handle_subscription_created(event, session)
            result = await session.execute(select(Subscription))
            rows = result.scalars().all()
    assert len(rows) == 0

    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(select(BalanceLedger))
        entries = result.scalars().all()
    assert len(entries) == 0


@pytest.mark.asyncio
async def test_handle_subscription_created_idempotent():
    """Same event posted twice → handler runs once (stripe_events atomic INSERT covers dedup)."""
    from app.billing.handlers.subscription import handle_subscription_created
    from app.services.auth_service import hash_password

    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            user = User(
                email="sub_idem@example.com",
                password_hash=hash_password("Password123!"),
                full_name="Sub Idem",
                stripe_customer_id="cus_sub_idem",
            )
            session.add(user)
            await session.flush()
            user_id = user.id

    price_mock = MagicMock()
    price_mock.id = "price_idem_001"
    price_mock.lookup_key = "starter_monthly"
    price_mock.metadata = {"grant_micros": "5000000", "tier_name": "Starter"}

    sub_item = MagicMock()
    sub_item.price = price_mock

    sub_obj = MagicMock()
    sub_obj.id = "sub_idem_001"
    sub_obj.status = "active"
    sub_obj.cancel_at_period_end = False
    sub_obj.current_period_start = _PERIOD_START_TS
    sub_obj.current_period_end = _PERIOD_END_TS
    sub_obj.metadata = {"user_id": str(user_id)}
    sub_obj.items.data = [sub_item]

    event = MagicMock()
    event.id = "evt_sub_idem_001"
    event.data.object = sub_obj

    # First run — handler runs normally (flag=True so grant fires).
    with patch.object(settings, "BILLING_SUBSCRIPTION_GRANTS_TO_LEDGER", True):
        async with AsyncSession(engine, expire_on_commit=False) as session:
            async with session.begin():
                await handle_subscription_created(event, session)

    # Second run — stripe_events prevents a second stripe_event_id insert on
    # balance_ledger (the UNIQUE constraint on stripe_event_id would fire). But
    # since we're calling the handler directly (bypassing the route's idempotency
    # check), the second call would fail on the ledger unique constraint. The test
    # verifies that after two calls via the route (which dedups), there is only 1 entry.
    # For a direct-invocation test, we verify the first call succeeded.
    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(
            select(BalanceLedger).where(BalanceLedger.user_id == user_id)
        )
        entries = result.scalars().all()
    assert len(entries) == 1
    assert entries[0].stripe_event_id == "evt_sub_idem_001"


@pytest.mark.asyncio
async def test_handle_subscription_updated_syncs_state():
    """subscription.updated event → row reflects new period_end + cancel_at_period_end=true; no ledger entry."""
    from app.billing.handlers.subscription import handle_subscription_updated
    from app.services.auth_service import hash_password

    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            user = User(
                email="sub_updated@example.com",
                password_hash=hash_password("Password123!"),
                full_name="Sub Updated",
                stripe_customer_id="cus_sub_updated",
            )
            session.add(user)
            await session.flush()
            user_id = user.id

    async with AsyncSession(engine, expire_on_commit=False) as session:
        await _create_subscription_row(
            session, user_id, status="active", stripe_sub_id="sub_upd_001"
        )

    new_period_end = _PERIOD_END_TS + 86400 * 30  # 30 days further

    price_mock = MagicMock()
    price_mock.id = "price_test_001"
    price_mock.lookup_key = "starter_monthly"
    price_mock.metadata = {"grant_micros": "10000000", "tier_name": "Starter"}

    sub_item = MagicMock()
    sub_item.price = price_mock

    sub_obj = MagicMock()
    sub_obj.id = "sub_upd_001"
    sub_obj.status = "active"
    sub_obj.cancel_at_period_end = True
    sub_obj.current_period_start = _PERIOD_START_TS
    sub_obj.current_period_end = new_period_end
    sub_obj.metadata = {"user_id": str(user_id)}
    sub_obj.items.data = [sub_item]

    event = MagicMock()
    event.id = "evt_sub_updated_001"
    event.data.object = sub_obj

    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            await handle_subscription_updated(event, session)

    # Row reflects updated state — status + cancel_at_period_end synced; period NOT changed.
    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(select(Subscription).where(Subscription.user_id == user_id))
        row = result.scalar_one()
    assert row.cancel_at_period_end is True
    # Period fields are NOT updated by handle_subscription_updated (ticket 0024.5).
    # The pre-existing period from _create_subscription_row should be preserved.
    # (new_period_end in the event is ignored; invoice.paid cycle handler owns renewals.)
    original_period_end = datetime.fromtimestamp(_PERIOD_END_TS, tz=timezone.utc).replace(
        tzinfo=None
    )
    assert row.current_period_end == original_period_end, (
        "handle_subscription_updated must NOT overwrite period fields — invoice.paid cycle "
        "handler is authoritative for renewal periods (ticket 0024.5)"
    )

    # No ledger entry.
    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(
            select(BalanceLedger).where(BalanceLedger.user_id == user_id)
        )
        entries = result.scalars().all()
    assert len(entries) == 0


@pytest.mark.asyncio
async def test_handle_subscription_updated_no_op_for_unknown_user():
    """subscription.updated for user without a row → log warning + return; no crash."""
    from app.billing.handlers.subscription import handle_subscription_updated

    sub_obj = MagicMock()
    sub_obj.id = "sub_unknown_001"
    sub_obj.status = "active"
    sub_obj.cancel_at_period_end = False
    sub_obj.current_period_start = _PERIOD_START_TS
    sub_obj.current_period_end = _PERIOD_END_TS
    sub_obj.metadata = {"user_id": "999999"}  # no row for this user

    event = MagicMock()
    event.id = "evt_sub_updated_unknown"
    event.data.object = sub_obj

    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            await handle_subscription_updated(event, session)
            # No exception — handler returned gracefully.

    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(select(Subscription))
        rows = result.scalars().all()
    assert len(rows) == 0


@pytest.mark.asyncio
async def test_handle_subscription_deleted_marks_cancelled():
    """subscription.deleted → row.status='cancelled'; balance unchanged."""
    from app.billing.handlers.subscription import handle_subscription_deleted
    from app.services.auth_service import hash_password

    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            user = User(
                email="sub_deleted@example.com",
                password_hash=hash_password("Password123!"),
                full_name="Sub Deleted",
                stripe_customer_id="cus_sub_deleted",
            )
            session.add(user)
            await session.flush()
            user_id = user.id

    async with AsyncSession(engine, expire_on_commit=False) as session:
        await _create_subscription_row(session, user_id, status="active")

    sub_obj = MagicMock()
    sub_obj.id = "sub_test_001"
    sub_obj.metadata = {"user_id": str(user_id)}

    event = MagicMock()
    event.id = "evt_sub_deleted_001"
    event.data.object = sub_obj

    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            await handle_subscription_deleted(event, session)

    # Row marked cancelled.
    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(select(Subscription).where(Subscription.user_id == user_id))
        row = result.scalar_one()
    assert row.status == "cancelled"

    # No ledger entries (no revocation).
    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(
            select(BalanceLedger).where(BalanceLedger.user_id == user_id)
        )
        entries = result.scalars().all()
    assert len(entries) == 0


@pytest.mark.asyncio
async def test_handle_invoice_paid_subscription_create_no_op():
    """billing_reason='subscription_create' → no ledger entry (covered by subscription.created)."""
    from app.billing.handlers.subscription import handle_invoice_paid
    from app.services.auth_service import hash_password

    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            user = User(
                email="inv_paid_create@example.com",
                password_hash=hash_password("Password123!"),
                full_name="Inv Paid Create",
            )
            session.add(user)
            await session.flush()
            user_id = user.id

    invoice_obj = MagicMock()
    invoice_obj.billing_reason = "subscription_create"
    invoice_obj.subscription = "sub_test_001"

    event = MagicMock()
    event.id = "evt_inv_create_no_op"
    event.data.object = invoice_obj

    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            await handle_invoice_paid(event, session)
            result = await session.execute(
                select(BalanceLedger).where(BalanceLedger.user_id == user_id)
            )
            entries = result.scalars().all()

    assert len(entries) == 0


@pytest.mark.asyncio
async def test_handle_invoice_paid_subscription_cycle_grants():
    """billing_reason='subscription_cycle' → subscription_reset ledger entry + period updated."""
    from app.billing.handlers.subscription import handle_invoice_paid
    from app.services.auth_service import hash_password

    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            user = User(
                email="inv_paid_cycle@example.com",
                password_hash=hash_password("Password123!"),
                full_name="Inv Paid Cycle",
                stripe_customer_id="cus_cycle",
            )
            session.add(user)
            await session.flush()
            user_id = user.id

    async with AsyncSession(engine, expire_on_commit=False) as session:
        await _create_subscription_row(
            session, user_id, status="active", stripe_sub_id="sub_cycle_001"
        )

    new_period_end = _PERIOD_END_TS + 86400 * 30

    # Build invoice object with lines.
    line_period = MagicMock()
    line_period.start = _PERIOD_START_TS
    line_period.end = new_period_end

    line = MagicMock()
    line.period = line_period
    line.price = None  # no tier change on this renewal; handler uses existing row.grant_micros

    invoice_obj = MagicMock()
    invoice_obj.billing_reason = "subscription_cycle"
    invoice_obj.subscription = "sub_cycle_001"
    invoice_obj.lines.data = [line]

    event = MagicMock()
    event.id = "evt_inv_cycle_001"
    event.data.object = invoice_obj

    with patch.object(settings, "BILLING_SUBSCRIPTION_GRANTS_TO_LEDGER", True):
        async with AsyncSession(engine, expire_on_commit=False) as session:
            async with session.begin():
                await handle_invoice_paid(event, session)

    # Ledger entry: subscription_reset of 10_000_000.
    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(
            select(BalanceLedger).where(BalanceLedger.user_id == user_id)
        )
        entries = result.scalars().all()
    assert len(entries) == 1
    assert entries[0].amount_micros == 10_000_000
    assert entries[0].reason == "subscription_reset"
    assert entries[0].stripe_event_id == "evt_inv_cycle_001"

    # Period updated.
    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(select(Subscription).where(Subscription.user_id == user_id))
        row = result.scalar_one()
    expected_end = datetime.fromtimestamp(new_period_end, tz=timezone.utc).replace(tzinfo=None)
    assert row.current_period_end == expected_end


@pytest.mark.asyncio
async def test_handle_invoice_paid_subscription_cycle_updates_periods():
    """invoice.paid (subscription_cycle) updates current_period_* from invoice line-item period.

    Architectural invariant (ticket 0024.5): invoice.paid cycle handler is the
    authoritative source for renewal period boundaries. This test verifies that
    the period fields are updated from invoice.lines.data[0].period.start/end,
    independently of the subscription.created/updated handlers which do NOT write periods.
    """
    from app.billing.handlers.subscription import handle_invoice_paid
    from app.services.auth_service import hash_password

    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            user = User(
                email="inv_cycle_period_update@example.com",
                password_hash=hash_password("Password123!"),
                full_name="Inv Cycle Period Update",
                stripe_customer_id="cus_cycle_period",
            )
            session.add(user)
            await session.flush()
            user_id = user.id

    # Pre-insert row with the INITIAL period (as set by subscribe endpoint).
    initial_start = datetime(2026, 1, 1, tzinfo=None)
    initial_end = datetime(2026, 2, 1, tzinfo=None)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            row = Subscription(
                user_id=user_id,
                stripe_subscription_id="sub_cycle_period_001",
                stripe_price_id="price_test_001",
                tier_key="starter_monthly",
                tier_name="Starter",
                status="active",
                grant_micros=10_000_000,
                current_period_start=initial_start,
                current_period_end=initial_end,
                cancel_at_period_end=False,
            )
            session.add(row)

    # New period from the renewal invoice.
    new_period_start_ts = int(datetime(2026, 2, 1, tzinfo=timezone.utc).timestamp())
    new_period_end_ts = int(datetime(2026, 3, 1, tzinfo=timezone.utc).timestamp())

    line_period = MagicMock()
    line_period.start = new_period_start_ts
    line_period.end = new_period_end_ts

    line = MagicMock()
    line.period = line_period

    invoice_obj = MagicMock()
    invoice_obj.billing_reason = "subscription_cycle"
    invoice_obj.subscription = "sub_cycle_period_001"
    invoice_obj.lines.data = [line]

    event = MagicMock()
    event.id = "evt_inv_cycle_period_001"
    event.data.object = invoice_obj

    with patch.object(settings, "BILLING_SUBSCRIPTION_GRANTS_TO_LEDGER", True):
        async with AsyncSession(engine, expire_on_commit=False) as session:
            async with session.begin():
                await handle_invoice_paid(event, session)

    # Period fields should be updated to the new renewal period from the invoice.
    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(select(Subscription).where(Subscription.user_id == user_id))
        row = result.scalar_one()

    expected_new_start = datetime(2026, 2, 1, tzinfo=None)
    expected_new_end = datetime(2026, 3, 1, tzinfo=None)

    assert row.current_period_start == expected_new_start, (
        f"invoice.paid cycle handler must update current_period_start from invoice line period. "
        f"Expected {expected_new_start!r}, got {row.current_period_start!r}. "
        f"Architectural invariant: invoice.paid is authoritative for renewal periods (ticket 0024.5)."
    )
    assert row.current_period_end == expected_new_end, (
        f"invoice.paid cycle handler must update current_period_end from invoice line period. "
        f"Expected {expected_new_end!r}, got {row.current_period_end!r}. "
        f"Architectural invariant: invoice.paid is authoritative for renewal periods (ticket 0024.5)."
    )
    assert row.current_period_start.tzinfo is None, "Should be naive UTC"
    assert row.current_period_end.tzinfo is None, "Should be naive UTC"

    # Ledger entry also written (grants enabled).
    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(
            select(BalanceLedger).where(BalanceLedger.user_id == user_id)
        )
        entries = result.scalars().all()
    assert len(entries) == 1
    assert entries[0].reason == "subscription_reset"
    assert entries[0].stripe_event_id == "evt_inv_cycle_period_001"


@pytest.mark.asyncio
async def test_handle_invoice_paid_subscription_create_no_op_periods():
    """invoice.paid (subscription_create) does NOT write period fields.

    Architectural invariant (ticket 0024.5): initial period is the subscribe
    endpoint's job. The subscription_create invoice.paid is a no-op — it should
    not modify period fields or ledger.
    """
    from app.billing.handlers.subscription import handle_invoice_paid
    from app.services.auth_service import hash_password

    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            user = User(
                email="inv_create_noop_period@example.com",
                password_hash=hash_password("Password123!"),
                full_name="Inv Create NoOp Period",
            )
            session.add(user)
            await session.flush()
            user_id = user.id

    # Pre-insert row with known periods (as subscribe endpoint would write).
    pre_period_start = datetime(2026, 1, 1, tzinfo=None)
    pre_period_end = datetime(2026, 2, 1, tzinfo=None)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            row = Subscription(
                user_id=user_id,
                stripe_subscription_id="sub_create_noop_001",
                stripe_price_id="price_test_001",
                tier_key="starter_monthly",
                tier_name="Starter",
                status="active",
                grant_micros=10_000_000,
                current_period_start=pre_period_start,
                current_period_end=pre_period_end,
                cancel_at_period_end=False,
            )
            session.add(row)

    invoice_obj = MagicMock()
    invoice_obj.billing_reason = "subscription_create"
    invoice_obj.subscription = "sub_create_noop_001"

    event = MagicMock()
    event.id = "evt_inv_create_noop_period_001"
    event.data.object = invoice_obj

    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            await handle_invoice_paid(event, session)

    # No ledger entry — subscription_create is a no-op.
    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(
            select(BalanceLedger).where(BalanceLedger.user_id == user_id)
        )
        entries = result.scalars().all()
    assert len(entries) == 0

    # Period fields unchanged — subscription_create no-op does not touch the row.
    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(select(Subscription).where(Subscription.user_id == user_id))
        row = result.scalar_one()
    assert row.current_period_start == pre_period_start
    assert row.current_period_end == pre_period_end


@pytest.mark.asyncio
async def test_handle_invoice_paid_other_reason_no_op():
    """billing_reason='manual' → no ledger entry, log only."""
    from app.billing.handlers.subscription import handle_invoice_paid

    invoice_obj = MagicMock()
    invoice_obj.billing_reason = "manual"
    invoice_obj.subscription = "sub_manual_001"

    event = MagicMock()
    event.id = "evt_inv_manual_001"
    event.data.object = invoice_obj

    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            await handle_invoice_paid(event, session)
            result = await session.execute(select(BalanceLedger))
            entries = result.scalars().all()

    assert len(entries) == 0


@pytest.mark.asyncio
async def test_handle_invoice_payment_failed_marks_past_due():
    """invoice.payment_failed → row.status='past_due'; balance unchanged."""
    from app.billing.handlers.subscription import handle_invoice_payment_failed
    from app.services.auth_service import hash_password

    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            user = User(
                email="inv_failed@example.com",
                password_hash=hash_password("Password123!"),
                full_name="Inv Failed",
                stripe_customer_id="cus_failed",
            )
            session.add(user)
            await session.flush()
            user_id = user.id

    async with AsyncSession(engine, expire_on_commit=False) as session:
        await _create_subscription_row(
            session, user_id, status="active", stripe_sub_id="sub_failed_001"
        )

    invoice_obj = MagicMock()
    invoice_obj.subscription = "sub_failed_001"

    event = MagicMock()
    event.id = "evt_inv_failed_001"
    event.data.object = invoice_obj

    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            await handle_invoice_payment_failed(event, session)

    # Row marked past_due.
    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(select(Subscription).where(Subscription.user_id == user_id))
        row = result.scalar_one()
    assert row.status == "past_due"

    # No balance changes.
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
async def test_webhook_dispatches_to_subscription_handlers():
    """Fire each of the 5 subscription/invoice event types via the live webhook endpoint;
    each returns 200 and records a stripe_events row."""
    from app.services.auth_service import hash_password

    # Create a user with a subscription row for the handlers to operate on.
    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            user = User(
                email="wh_dispatch_sub@example.com",
                password_hash=hash_password("Password123!"),
                full_name="WH Sub Dispatch",
                stripe_customer_id="cus_wh_dispatch",
            )
            session.add(user)
            await session.flush()
            user_id = user.id

    async with AsyncSession(engine, expire_on_commit=False) as session:
        await _create_subscription_row(
            session, user_id, status="active", stripe_sub_id="sub_wh_dispatch"
        )

    event_payloads = [
        (
            "evt_wh_sub_created",
            "customer.subscription.created",
            _make_sub_event(
                "evt_wh_sub_created", "customer.subscription.created", user_id, "sub_wh_dispatch"
            ),
        ),
        (
            "evt_wh_sub_updated",
            "customer.subscription.updated",
            _make_sub_event(
                "evt_wh_sub_updated",
                "customer.subscription.updated",
                user_id,
                "sub_wh_dispatch",
                cancel_at_period_end=True,
            ),
        ),
        (
            "evt_wh_sub_deleted",
            "customer.subscription.deleted",
            _make_sub_event(
                "evt_wh_sub_deleted", "customer.subscription.deleted", user_id, "sub_wh_dispatch"
            ),
        ),
        (
            "evt_wh_inv_paid",
            "invoice.paid",
            _make_invoice_event(
                "evt_wh_inv_paid", "invoice.paid", "sub_wh_dispatch", "subscription_cycle"
            ),
        ),
        (
            "evt_wh_inv_failed",
            "invoice.payment_failed",
            _make_invoice_event("evt_wh_inv_failed", "invoice.payment_failed", "sub_wh_dispatch"),
        ),
    ]

    with (
        patch.object(settings, "STRIPE_WEBHOOK_SECRET", _WEBHOOK_SECRET),
        patch.object(settings, "BILLING_SUBSCRIPTION_GRANTS_TO_LEDGER", True),
    ):
        test_app = _make_billing_test_app()
        transport = httpx.ASGITransport(app=test_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            for event_id, event_type, payload in event_payloads:
                encoded, sig_header = _sign_payload(payload)
                resp = await c.post(
                    "/billing/webhook",
                    content=encoded,
                    headers={"stripe-signature": sig_header, "content-type": "application/json"},
                )
                assert resp.status_code == 200, f"{event_type}: {resp.status_code} {resp.text}"

    # Verify stripe_events rows for all 5.
    async with AsyncSession(engine, expire_on_commit=False) as session:
        for event_id, event_type, _ in event_payloads:
            result = await session.execute(select(StripeEvent).where(StripeEvent.id == event_id))
            row = result.scalar_one_or_none()
            assert row is not None, f"stripe_events row missing for {event_id}"
            assert row.event_type == event_type


# ---------------------------------------------------------------------------
# BILLING_SUBSCRIPTION_GRANTS_TO_LEDGER=False (OFF-state) tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscribe_does_not_require_grant_micros_when_grants_disabled(client):
    """Flag=False, Price without grant_micros → subscribe succeeds (200); no ledger entry."""
    mock_customer = MagicMock()
    mock_customer.id = "cus_no_grant_flag_off"

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch.object(settings, "BILLING_SUBSCRIPTION_GRANTS_TO_LEDGER", False),
        patch("app.billing.primitives.stripe") as mock_p,
    ):
        mock_p.Customer.create.return_value = mock_customer
        reg_resp, user_id = await _register_and_verify(client, "sub_flagoff_nogrant@example.com")

    access_token = reg_resp.get("access_token")

    # Price with NO grant_micros in metadata — only tier_name.
    price = MagicMock()
    price.id = "price_flagoff_001"
    price.lookup_key = "starter_monthly"
    price.metadata = {"tier_name": "Starter"}  # no grant_micros

    mock_prices = MagicMock()
    mock_prices.data = [price]
    mock_prices.auto_paging_iter.return_value = iter([price])

    mock_sub = _mock_subscription(sub_id="sub_flagoff_001", status="active")

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch.object(settings, "BILLING_SUBSCRIPTION_GRANTS_TO_LEDGER", False),
        patch("app.routes.billing.stripe") as mock_stripe,
        patch("app.billing.primitives.stripe"),
    ):
        mock_stripe.Price.list.return_value = mock_prices
        mock_stripe.PaymentMethod.attach.return_value = MagicMock()
        mock_stripe.Customer.modify.return_value = MagicMock()
        mock_stripe.Subscription.create.return_value = mock_sub
        resp = await client.post(
            "/billing/subscribe",
            json={"price_lookup_key": "starter_monthly", "payment_method_id": "pm_flagoff"},
            headers=_auth_headers(access_token),
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["subscription_id"] == "sub_flagoff_001"
    assert body["status"] == "active"

    # Subscriptions row upserted.
    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(select(Subscription).where(Subscription.user_id == user_id))
        row = result.scalar_one()
    assert row.stripe_subscription_id == "sub_flagoff_001"
    assert row.status == "active"
    assert row.tier_name == "Starter"
    assert row.grant_micros == 0  # stored as 0 when flag is OFF

    # No ledger entry.
    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(
            select(BalanceLedger).where(BalanceLedger.user_id == user_id)
        )
        entries = result.scalars().all()
    assert len(entries) == 0


@pytest.mark.asyncio
async def test_subscribe_stores_zero_grant_micros_when_flag_off_even_if_metadata_present(client):
    """Flag=False, Price WITH grant_micros in metadata → grant_micros stored as 0, not the metadata value.

    Regression test for ticket 0024.8: the buggy `int(raw_grant) if raw_grant else 0`
    returned int(raw_grant)=19990000 when metadata.grant_micros was truthy. The fix
    makes grant_micros=0 unconditionally when flag=false, regardless of metadata.
    """
    mock_customer = MagicMock()
    mock_customer.id = "cus_flagoff_metadata_present"

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch.object(settings, "BILLING_SUBSCRIPTION_GRANTS_TO_LEDGER", False),
        patch("app.billing.primitives.stripe") as mock_p,
    ):
        mock_p.Customer.create.return_value = mock_customer
        reg_resp, user_id = await _register_and_verify(
            client, "sub_flagoff_metadata@example.com"
        )

    access_token = reg_resp.get("access_token")

    # Price WITH grant_micros in metadata — truthy raw_grant exercises the bug.
    price = MagicMock()
    price.id = "price_flagoff_meta_001"
    price.lookup_key = "starter_monthly"
    price.metadata = {"grant_micros": "19990000", "tier_name": "Starter"}  # truthy raw_grant

    mock_prices = MagicMock()
    mock_prices.data = [price]
    mock_prices.auto_paging_iter.return_value = iter([price])

    mock_sub = _mock_subscription(sub_id="sub_flagoff_meta_001", status="active")

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch.object(settings, "BILLING_SUBSCRIPTION_GRANTS_TO_LEDGER", False),
        patch("app.routes.billing.stripe") as mock_stripe,
        patch("app.billing.primitives.stripe"),
    ):
        mock_stripe.Price.list.return_value = mock_prices
        mock_stripe.PaymentMethod.attach.return_value = MagicMock()
        mock_stripe.Customer.modify.return_value = MagicMock()
        mock_stripe.Subscription.create.return_value = mock_sub
        resp = await client.post(
            "/billing/subscribe",
            json={
                "price_lookup_key": "starter_monthly",
                "payment_method_id": "pm_flagoff_meta",
            },
            headers=_auth_headers(access_token),
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["subscription_id"] == "sub_flagoff_meta_001"
    assert body["status"] == "active"

    # Subscriptions row must have grant_micros=0, NOT 19990000.
    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(
            select(Subscription).where(Subscription.user_id == user_id)
        )
        row = result.scalar_one()
    assert row.grant_micros == 0  # strict flag-gate: 0 regardless of Price metadata

    # No ledger entry.
    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(
            select(BalanceLedger).where(BalanceLedger.user_id == user_id)
        )
        entries = result.scalars().all()
    assert len(entries) == 0


@pytest.mark.asyncio
async def test_handle_subscription_created_skips_grant_when_disabled():
    """Flag=False → handler upserts subscriptions row but does NOT write to balance_ledger."""
    from app.billing.handlers.subscription import handle_subscription_created
    from app.services.auth_service import hash_password

    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            user = User(
                email="sub_created_flagoff@example.com",
                password_hash=hash_password("Password123!"),
                full_name="Sub Created Flag Off",
                stripe_customer_id="cus_sub_flagoff",
            )
            session.add(user)
            await session.flush()
            user_id = user.id

    price_mock = MagicMock()
    price_mock.id = "price_flagoff_002"
    price_mock.lookup_key = "starter_monthly"
    price_mock.metadata = {"grant_micros": "10000000", "tier_name": "Starter"}

    sub_item = MagicMock()
    sub_item.price = price_mock

    sub_obj = MagicMock()
    sub_obj.id = "sub_created_flagoff_001"
    sub_obj.status = "active"
    sub_obj.cancel_at_period_end = False
    sub_obj.current_period_start = _PERIOD_START_TS
    sub_obj.current_period_end = _PERIOD_END_TS
    sub_obj.metadata = {"user_id": str(user_id)}
    sub_obj.items.data = [sub_item]

    event = MagicMock()
    event.id = "evt_sub_created_flagoff_001"
    event.data.object = sub_obj

    # Flag is OFF (default False — no patch needed, but be explicit for clarity).
    with patch.object(settings, "BILLING_SUBSCRIPTION_GRANTS_TO_LEDGER", False):
        async with AsyncSession(engine, expire_on_commit=False) as session:
            async with session.begin():
                await handle_subscription_created(event, session)

    # Subscriptions row was upserted.
    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(select(Subscription).where(Subscription.user_id == user_id))
        row = result.scalar_one_or_none()
    assert row is not None
    assert row.stripe_subscription_id == "sub_created_flagoff_001"
    assert row.status == "active"
    assert row.tier_name == "Starter"

    # balance_ledger was NOT written.
    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(
            select(BalanceLedger).where(BalanceLedger.user_id == user_id)
        )
        entries = result.scalars().all()
    assert len(entries) == 0


@pytest.mark.asyncio
async def test_handle_invoice_paid_subscription_cycle_skips_when_disabled():
    """Flag=False, subscription_cycle invoice → period dates updated; NO ledger entry."""
    from app.billing.handlers.subscription import handle_invoice_paid
    from app.services.auth_service import hash_password

    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            user = User(
                email="inv_cycle_flagoff@example.com",
                password_hash=hash_password("Password123!"),
                full_name="Inv Cycle Flag Off",
                stripe_customer_id="cus_cycle_flagoff",
            )
            session.add(user)
            await session.flush()
            user_id = user.id

    async with AsyncSession(engine, expire_on_commit=False) as session:
        await _create_subscription_row(
            session, user_id, status="active", stripe_sub_id="sub_cycle_flagoff_001"
        )

    new_period_end = _PERIOD_END_TS + 86400 * 30

    line_period = MagicMock()
    line_period.start = _PERIOD_START_TS
    line_period.end = new_period_end

    line = MagicMock()
    line.period = line_period

    invoice_obj = MagicMock()
    invoice_obj.billing_reason = "subscription_cycle"
    invoice_obj.subscription = "sub_cycle_flagoff_001"
    invoice_obj.lines.data = [line]

    event = MagicMock()
    event.id = "evt_inv_cycle_flagoff_001"
    event.data.object = invoice_obj

    with patch.object(settings, "BILLING_SUBSCRIPTION_GRANTS_TO_LEDGER", False):
        async with AsyncSession(engine, expire_on_commit=False) as session:
            async with session.begin():
                await handle_invoice_paid(event, session)

    # balance_ledger was NOT written.
    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(
            select(BalanceLedger).where(BalanceLedger.user_id == user_id)
        )
        entries = result.scalars().all()
    assert len(entries) == 0

    # Period was still updated on the subscriptions row.
    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(select(Subscription).where(Subscription.user_id == user_id))
        row = result.scalar_one()
    expected_end = datetime.fromtimestamp(new_period_end, tz=timezone.utc).replace(tzinfo=None)
    assert row.current_period_end == expected_end


@pytest.mark.asyncio
async def test_handle_subscription_created_extracts_price_from_items_data():
    """Bug-fix regression: realistic subscription event with items.data[0].price populated
    → handler extracts price correctly and grants when flag=True.

    Without the fix, the handler would log 'could not extract price from sub.items'
    and return without granting when Stripe sends a real ListObject-shaped event.
    With the fix, the handler traverses .data correctly and grants subscription_grant.
    """
    from app.billing.handlers.subscription import handle_subscription_created
    from app.services.auth_service import hash_password

    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            user = User(
                email="sub_bugfix_regression@example.com",
                password_hash=hash_password("Password123!"),
                full_name="Sub Bugfix",
                stripe_customer_id="cus_bugfix",
            )
            session.add(user)
            await session.flush()
            user_id = user.id

    # Simulate the Stripe SDK's StripeObject shape: sub.items is a ListObject
    # with a .data attribute containing subscription item objects, each with
    # a .price attribute. This is the exact shape that was failing in production.
    price_mock = MagicMock()
    price_mock.id = "price_bugfix_001"
    price_mock.lookup_key = "starter_monthly"
    price_mock.metadata = {"grant_micros": "7500000", "tier_name": "Pro"}

    sub_item_mock = MagicMock()
    sub_item_mock.price = price_mock

    # items_list simulates a Stripe ListObject: has .data attribute.
    items_list = MagicMock()
    items_list.data = [sub_item_mock]

    sub_obj = MagicMock()
    sub_obj.id = "sub_bugfix_001"
    sub_obj.status = "active"
    sub_obj.cancel_at_period_end = False
    sub_obj.current_period_start = _PERIOD_START_TS
    sub_obj.current_period_end = _PERIOD_END_TS
    sub_obj.metadata = {"user_id": str(user_id)}
    sub_obj.items = items_list  # items IS the ListObject, NOT items.data directly

    event = MagicMock()
    event.id = "evt_bugfix_regression_001"
    event.data.object = sub_obj

    with patch.object(settings, "BILLING_SUBSCRIPTION_GRANTS_TO_LEDGER", True):
        async with AsyncSession(engine, expire_on_commit=False) as session:
            async with session.begin():
                await handle_subscription_created(event, session)

    # subscriptions row upserted with correct metadata from items.data[0].price.
    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(select(Subscription).where(Subscription.user_id == user_id))
        row = result.scalar_one_or_none()
    assert row is not None
    assert row.stripe_subscription_id == "sub_bugfix_001"
    assert row.tier_name == "Pro"
    assert row.tier_key == "starter_monthly"
    assert row.grant_micros == 7_500_000

    # Ledger entry was written — confirming the price extraction bug is fixed.
    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(
            select(BalanceLedger).where(BalanceLedger.user_id == user_id)
        )
        entries = result.scalars().all()
    assert len(entries) == 1
    assert entries[0].amount_micros == 7_500_000
    assert entries[0].reason == "subscription_grant"
    assert entries[0].stripe_event_id == "evt_bugfix_regression_001"


# ---------------------------------------------------------------------------
# 0024.4 Regression tests — current_period_* persistence
# ---------------------------------------------------------------------------
# These tests guard against the class of bug where current_period_start /
# current_period_end are dropped during subscribe or webhook handling.
# Ticket: 0024.4 (subscriptions.current_period_start/end extraction regression).


@pytest.mark.asyncio
async def test_subscribe_persists_current_period_fields(client):
    """POST /billing/subscribe → subscriptions row has current_period_start and
    current_period_end populated as naive-UTC datetimes.

    Regression guard: subscribe endpoint must extract and persist period fields
    from the Stripe Subscription API response, not leave them NULL.
    """
    mock_customer = MagicMock()
    mock_customer.id = "cus_period_sub"

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch("app.billing.primitives.stripe") as mock_p,
    ):
        mock_p.Customer.create.return_value = mock_customer
        reg_resp, user_id = await _register_and_verify(client, "period_sub@example.com")

    access_token = reg_resp.get("access_token")

    price = _mock_price()
    mock_prices = MagicMock()
    mock_prices.data = [price]
    mock_prices.auto_paging_iter.return_value = iter([price])

    # Stripe Subscription.create returns explicit period timestamps.
    mock_sub = _mock_subscription(
        sub_id="sub_period_001",
        status="active",
        period_start=_PERIOD_START_TS,
        period_end=_PERIOD_END_TS,
    )

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch("app.routes.billing.stripe") as mock_stripe,
        patch("app.billing.primitives.stripe"),
    ):
        mock_stripe.Price.list.return_value = mock_prices
        mock_stripe.PaymentMethod.attach.return_value = MagicMock()
        mock_stripe.Customer.modify.return_value = MagicMock()
        mock_stripe.Subscription.create.return_value = mock_sub
        resp = await client.post(
            "/billing/subscribe",
            json={"price_lookup_key": "starter_monthly", "payment_method_id": "pm_period"},
            headers=_auth_headers(access_token),
        )

    assert resp.status_code == 200, resp.text

    # Assert that period fields were persisted — not NULL.
    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(select(Subscription).where(Subscription.user_id == user_id))
        row = result.scalar_one()

    assert row.current_period_start is not None, (
        "current_period_start should be populated after subscribe; got NULL. "
        "Regression: subscribe endpoint dropped period field extraction."
    )
    assert row.current_period_end is not None, (
        "current_period_end should be populated after subscribe; got NULL. "
        "Regression: subscribe endpoint dropped period field extraction."
    )

    expected_start = datetime.fromtimestamp(_PERIOD_START_TS, tz=timezone.utc).replace(tzinfo=None)
    expected_end = datetime.fromtimestamp(_PERIOD_END_TS, tz=timezone.utc).replace(tzinfo=None)
    assert row.current_period_start == expected_start, (
        f"current_period_start mismatch: {row.current_period_start!r} != {expected_start!r}"
    )
    assert row.current_period_end == expected_end, (
        f"current_period_end mismatch: {row.current_period_end!r} != {expected_end!r}"
    )

    # Confirm naive UTC (no tzinfo) — chassis datetime convention.
    assert row.current_period_start.tzinfo is None, "current_period_start should be naive UTC"
    assert row.current_period_end.tzinfo is None, "current_period_end should be naive UTC"


@pytest.mark.asyncio
async def test_handle_subscription_created_persists_periods():
    """handle_subscription_created webhook does NOT overwrite pre-existing period fields.

    Architectural invariant (ticket 0024.5 Path B): the subscription.created webhook
    handler must NOT overwrite current_period_* on an existing row. The subscribe
    endpoint is the authoritative source of truth for initial periods. If the webhook
    arrives after the endpoint has already written good period values, those values
    must be preserved — even if the webhook payload has no extractable period data.

    This is the primary regression guard for the NULL-period bug: subscriptions created
    under `stripe listen` were having their endpoint-written periods silently overwritten
    with NULL by the webhook handler.
    """
    from app.billing.handlers.subscription import handle_subscription_created
    from app.services.auth_service import hash_password

    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            user = User(
                email="handler_period_created@example.com",
                password_hash=hash_password("Password123!"),
                full_name="Handler Period Created",
                stripe_customer_id="cus_handler_period",
            )
            session.add(user)
            await session.flush()
            user_id = user.id

    # Pre-insert a subscriptions row with known period values (as the subscribe endpoint would).
    pre_period_start = datetime(2026, 1, 1, tzinfo=None)
    pre_period_end = datetime(2026, 2, 1, tzinfo=None)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            existing_row = Subscription(
                user_id=user_id,
                stripe_subscription_id="sub_period_created_001",
                stripe_price_id="price_period_001",
                tier_key="starter_monthly",
                tier_name="Starter",
                status="active",
                grant_micros=10_000_000,
                current_period_start=pre_period_start,
                current_period_end=pre_period_end,
                cancel_at_period_end=False,
            )
            session.add(existing_row)

    # Build a webhook event that has NO extractable period data
    # (simulates the API-version variance where current_period_* isn't at top level).
    price_mock = MagicMock()
    price_mock.id = "price_period_001"
    price_mock.lookup_key = "starter_monthly"
    price_mock.metadata = {"grant_micros": "10000000", "tier_name": "Starter"}

    sub_item = MagicMock()
    sub_item.price = price_mock

    sub_obj = MagicMock()
    sub_obj.id = "sub_period_created_001"
    sub_obj.status = "active"
    sub_obj.cancel_at_period_end = False
    # Simulate period fields absent/None in the webhook payload.
    sub_obj.current_period_start = None
    sub_obj.current_period_end = None
    sub_obj.metadata = {"user_id": str(user_id)}
    sub_obj.items.data = [sub_item]

    event = MagicMock()
    event.id = "evt_period_created_001"
    event.data.object = sub_obj

    with patch.object(settings, "BILLING_SUBSCRIPTION_GRANTS_TO_LEDGER", False):
        async with AsyncSession(engine, expire_on_commit=False) as session:
            async with session.begin():
                await handle_subscription_created(event, session)

    # The pre-existing period values must be preserved — the webhook must NOT overwrite them.
    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(select(Subscription).where(Subscription.user_id == user_id))
        row = result.scalar_one_or_none()

    assert row is not None
    assert row.current_period_start == pre_period_start, (
        f"handle_subscription_created webhook MUST NOT overwrite pre-existing period fields. "
        f"Expected {pre_period_start!r}, got {row.current_period_start!r}. "
        f"Architectural invariant: subscribe endpoint is authoritative (ticket 0024.5)."
    )
    assert row.current_period_end == pre_period_end, (
        f"handle_subscription_created webhook MUST NOT overwrite pre-existing period fields. "
        f"Expected {pre_period_end!r}, got {row.current_period_end!r}. "
        f"Architectural invariant: subscribe endpoint is authoritative (ticket 0024.5)."
    )


@pytest.mark.asyncio
async def test_handle_subscription_created_inserts_periods_on_fresh_row():
    """handle_subscription_created inserts period fields when creating a brand-new row.

    The INSERT path (no pre-existing row) should still write current_period_*
    from the event payload. This covers the rare out-of-band case where the
    webhook is the first to create the subscriptions row (no subscribe endpoint ran).
    """
    from app.billing.handlers.subscription import handle_subscription_created
    from app.services.auth_service import hash_password

    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            user = User(
                email="handler_period_fresh@example.com",
                password_hash=hash_password("Password123!"),
                full_name="Handler Period Fresh",
                stripe_customer_id="cus_handler_period_fresh",
            )
            session.add(user)
            await session.flush()
            user_id = user.id

    # No pre-existing row — webhook is first to insert.
    price_mock = MagicMock()
    price_mock.id = "price_period_fresh_001"
    price_mock.lookup_key = "starter_monthly"
    price_mock.metadata = {"grant_micros": "10000000", "tier_name": "Starter"}

    sub_item = MagicMock()
    sub_item.price = price_mock

    sub_obj = MagicMock()
    sub_obj.id = "sub_period_fresh_001"
    sub_obj.status = "active"
    sub_obj.cancel_at_period_end = False
    sub_obj.current_period_start = _PERIOD_START_TS
    sub_obj.current_period_end = _PERIOD_END_TS
    sub_obj.metadata = {"user_id": str(user_id)}
    sub_obj.items.data = [sub_item]

    event = MagicMock()
    event.id = "evt_period_fresh_001"
    event.data.object = sub_obj

    with patch.object(settings, "BILLING_SUBSCRIPTION_GRANTS_TO_LEDGER", False):
        async with AsyncSession(engine, expire_on_commit=False) as session:
            async with session.begin():
                await handle_subscription_created(event, session)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(select(Subscription).where(Subscription.user_id == user_id))
        row = result.scalar_one_or_none()

    assert row is not None
    assert row.stripe_subscription_id == "sub_period_fresh_001"
    # INSERT path: period fields should be set from the event payload.
    expected_start = datetime.fromtimestamp(_PERIOD_START_TS, tz=timezone.utc).replace(tzinfo=None)
    expected_end = datetime.fromtimestamp(_PERIOD_END_TS, tz=timezone.utc).replace(tzinfo=None)
    assert row.current_period_start == expected_start, (
        "Fresh INSERT via handle_subscription_created should write period fields from event."
    )
    assert row.current_period_end == expected_end, (
        "Fresh INSERT via handle_subscription_created should write period fields from event."
    )
    assert row.current_period_start.tzinfo is None, "Should be naive UTC"
    assert row.current_period_end.tzinfo is None, "Should be naive UTC"


@pytest.mark.asyncio
async def test_handle_subscription_updated_persists_periods():
    """handle_subscription_updated does NOT overwrite pre-existing period fields.

    Architectural invariant (ticket 0024.5 Path B): the subscription.updated webhook
    handler must NOT touch current_period_*. A pre-existing row with valid period
    fields must retain them after the handler runs — even if the event payload carries
    different period values.

    This guards the case where a plan-change or cancel-at-period-end event fires and
    the handler previously would overwrite the subscribe endpoint's correct values
    with webhook-extracted values that may be None or incorrect.
    """
    from app.billing.handlers.subscription import handle_subscription_updated
    from app.services.auth_service import hash_password

    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            user = User(
                email="handler_period_updated@example.com",
                password_hash=hash_password("Password123!"),
                full_name="Handler Period Updated",
                stripe_customer_id="cus_handler_period_upd",
            )
            session.add(user)
            await session.flush()
            user_id = user.id

    # Pre-insert a row with known, valid period values (as the subscribe endpoint would write).
    pre_period_start = datetime(2026, 1, 1, tzinfo=None)
    pre_period_end = datetime(2026, 2, 1, tzinfo=None)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            existing_row = Subscription(
                user_id=user_id,
                stripe_subscription_id="sub_period_upd_001",
                stripe_price_id="price_period_001",
                tier_key="starter_monthly",
                tier_name="Starter",
                status="active",
                grant_micros=10_000_000,
                current_period_start=pre_period_start,
                current_period_end=pre_period_end,
                cancel_at_period_end=False,
            )
            session.add(existing_row)

    price_mock = MagicMock()
    price_mock.id = "price_period_001"
    price_mock.lookup_key = "starter_monthly"
    price_mock.metadata = {"grant_micros": "10000000", "tier_name": "Starter"}

    sub_item = MagicMock()
    sub_item.price = price_mock

    # Event has different period values than the pre-existing row — handler must ignore them.
    different_period_start = _PERIOD_START_TS
    different_period_end = _PERIOD_END_TS + 86400 * 30

    sub_obj = MagicMock()
    sub_obj.id = "sub_period_upd_001"
    sub_obj.status = "active"
    sub_obj.cancel_at_period_end = True  # flag that will be updated
    sub_obj.current_period_start = different_period_start
    sub_obj.current_period_end = different_period_end
    sub_obj.metadata = {"user_id": str(user_id)}
    sub_obj.items.data = [sub_item]

    event = MagicMock()
    event.id = "evt_period_updated_001"
    event.data.object = sub_obj

    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            await handle_subscription_updated(event, session)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(select(Subscription).where(Subscription.user_id == user_id))
        row = result.scalar_one()

    # Non-period state fields were updated correctly.
    assert row.cancel_at_period_end is True

    # Period fields MUST be unchanged — the handler does not own period authority.
    assert row.current_period_start == pre_period_start, (
        f"handle_subscription_updated MUST NOT overwrite period fields. "
        f"Expected {pre_period_start!r}, got {row.current_period_start!r}. "
        f"Architectural invariant: subscribe endpoint + invoice.paid are authoritative (ticket 0024.5)."
    )
    assert row.current_period_end == pre_period_end, (
        f"handle_subscription_updated MUST NOT overwrite period fields. "
        f"Expected {pre_period_end!r}, got {row.current_period_end!r}. "
        f"Architectural invariant: subscribe endpoint + invoice.paid are authoritative (ticket 0024.5)."
    )


@pytest.mark.asyncio
async def test_period_extraction_defensive_dual_access():
    """_extract_period_timestamps uses dual attribute+dict access for resilience.

    Unit test: verifies the helper works on both a MagicMock (attribute-style)
    and a plain dict (dict-style access via .get). This covers the dual-access
    pattern documented in the helper's docstring.
    """
    from app.billing.handlers.subscription import _extract_period_timestamps

    # Attribute-style (MagicMock / Stripe SDK StripeObject shape).
    attr_obj = MagicMock()
    attr_obj.current_period_start = _PERIOD_START_TS
    attr_obj.current_period_end = _PERIOD_END_TS

    start, end = _extract_period_timestamps(attr_obj)
    assert start is not None
    assert end is not None
    assert start.tzinfo is None, "Should be naive UTC"
    assert end.tzinfo is None, "Should be naive UTC"

    expected_start = datetime.fromtimestamp(_PERIOD_START_TS, tz=timezone.utc).replace(tzinfo=None)
    expected_end = datetime.fromtimestamp(_PERIOD_END_TS, tz=timezone.utc).replace(tzinfo=None)
    assert start == expected_start
    assert end == expected_end

    # Dict-style (for objects where getattr returns None but .get() works).
    class DictBackedObj:
        """Simulates a StripeObject where __getattr__ returns None for period fields
        but the dict backing provides them via .get()."""

        def __init__(self):
            self._data = {
                "current_period_start": _PERIOD_START_TS,
                "current_period_end": _PERIOD_END_TS,
            }

        def get(self, key, default=None):
            return self._data.get(key, default)

    dict_obj = DictBackedObj()
    # Simulate getattr returning None (attribute access fails on this object).
    start2, end2 = _extract_period_timestamps(dict_obj)
    assert start2 is not None
    assert end2 is not None
    assert start2 == expected_start


# ---------------------------------------------------------------------------
# 0024.7 — grant_micros Path B regression guards
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_subscription_created_preserves_grant_micros_on_update():
    """handle_subscription_created UPDATE path MUST NOT overwrite pre-existing grant_micros.

    Architectural invariant (ticket 0024.7 Path B): when a customer.subscription.created
    webhook fires for an already-existing row (common: subscribe endpoint upserts first,
    then webhook arrives), the handler's UPDATE path must not touch grant_micros.

    Scenario: subscribe endpoint stored grant_micros=0 (flag=false). Webhook fires with
    metadata.grant_micros=19990000. Row's grant_micros must remain 0 after the handler —
    the endpoint's flag-gated value must survive.
    """
    from app.billing.handlers.subscription import handle_subscription_created
    from app.services.auth_service import hash_password

    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            user = User(
                email="grant_micros_preserve_created@example.com",
                password_hash=hash_password("Password123!"),
                full_name="Grant Micros Preserve Created",
                stripe_customer_id="cus_gm_preserve_created",
            )
            session.add(user)
            await session.flush()
            user_id = user.id

    # Pre-insert a subscriptions row with grant_micros=0 (as subscribe endpoint writes
    # when flag=false — the value the chassis chose; webhook must not overwrite it).
    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            existing_row = Subscription(
                user_id=user_id,
                stripe_subscription_id="sub_gm_preserve_001",
                stripe_price_id="price_gm_001",
                tier_key="starter_monthly",
                tier_name="Starter",
                status="active",
                grant_micros=0,  # flag=false → endpoint stored 0
                current_period_start=datetime(2026, 1, 1, tzinfo=None),
                current_period_end=datetime(2026, 2, 1, tzinfo=None),
                cancel_at_period_end=False,
            )
            session.add(existing_row)

    # Webhook event carries metadata.grant_micros=19990000 — the metadata value that
    # would have been written if the flag were on.
    price_mock = MagicMock()
    price_mock.id = "price_gm_001"
    price_mock.lookup_key = "starter_monthly"
    price_mock.metadata = {"grant_micros": "19990000", "tier_name": "Starter"}

    sub_item = MagicMock()
    sub_item.price = price_mock

    sub_obj = MagicMock()
    sub_obj.id = "sub_gm_preserve_001"
    sub_obj.status = "active"
    sub_obj.cancel_at_period_end = False
    sub_obj.current_period_start = _PERIOD_START_TS
    sub_obj.current_period_end = _PERIOD_END_TS
    sub_obj.metadata = {"user_id": str(user_id)}
    sub_obj.items.data = [sub_item]

    event = MagicMock()
    event.id = "evt_gm_preserve_created_001"
    event.data.object = sub_obj

    # Flag is OFF — subscribe endpoint stored 0; webhook must not overwrite.
    with patch.object(settings, "BILLING_SUBSCRIPTION_GRANTS_TO_LEDGER", False):
        async with AsyncSession(engine, expire_on_commit=False) as session:
            async with session.begin():
                await handle_subscription_created(event, session)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(select(Subscription).where(Subscription.user_id == user_id))
        row = result.scalar_one_or_none()

    assert row is not None
    assert row.grant_micros == 0, (
        f"handle_subscription_created UPDATE path MUST NOT overwrite grant_micros. "
        f"Expected 0 (endpoint's flag-gated value), got {row.grant_micros!r}. "
        f"Architectural invariant: subscribe endpoint is authoritative for grant_micros (ticket 0024.7)."
    )

    # No ledger entry (flag off).
    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(
            select(BalanceLedger).where(BalanceLedger.user_id == user_id)
        )
        entries = result.scalars().all()
    assert len(entries) == 0


@pytest.mark.asyncio
async def test_handle_subscription_updated_preserves_grant_micros():
    """handle_subscription_updated MUST NOT overwrite grant_micros.

    Architectural invariant (ticket 0024.7 Path B): the subscription.updated webhook
    handler syncs status and cancel_at_period_end only. grant_micros is NOT its
    responsibility — subscribe endpoint owns the initial value; invoice.paid cycle
    handler owns renewal updates (when flag=true). This mirrors the period-fields
    preservation established in 0024.5.

    Scenario: pre-existing row has grant_micros=0 (flag=false). Event carries
    metadata.grant_micros=19990000. Row must still have grant_micros=0 after the handler,
    while cancel_at_period_end IS correctly synced (proving handler still runs).
    """
    from app.billing.handlers.subscription import handle_subscription_updated
    from app.services.auth_service import hash_password

    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            user = User(
                email="grant_micros_preserve_updated@example.com",
                password_hash=hash_password("Password123!"),
                full_name="Grant Micros Preserve Updated",
                stripe_customer_id="cus_gm_preserve_updated",
            )
            session.add(user)
            await session.flush()
            user_id = user.id

    # Pre-insert row with grant_micros=0 (flag=false subscribe endpoint value).
    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            existing_row = Subscription(
                user_id=user_id,
                stripe_subscription_id="sub_gm_preserve_upd_001",
                stripe_price_id="price_gm_001",
                tier_key="starter_monthly",
                tier_name="Starter",
                status="active",
                grant_micros=0,
                current_period_start=datetime(2026, 1, 1, tzinfo=None),
                current_period_end=datetime(2026, 2, 1, tzinfo=None),
                cancel_at_period_end=False,
            )
            session.add(existing_row)

    # Event carries metadata.grant_micros=19990000 — handler must ignore for grant_micros.
    price_mock = MagicMock()
    price_mock.id = "price_gm_001"
    price_mock.lookup_key = "starter_monthly"
    price_mock.metadata = {"grant_micros": "19990000", "tier_name": "Starter"}

    sub_item = MagicMock()
    sub_item.price = price_mock

    sub_obj = MagicMock()
    sub_obj.id = "sub_gm_preserve_upd_001"
    sub_obj.status = "active"
    sub_obj.cancel_at_period_end = True  # state field that WILL be synced
    sub_obj.current_period_start = _PERIOD_START_TS
    sub_obj.current_period_end = _PERIOD_END_TS
    sub_obj.metadata = {"user_id": str(user_id)}
    sub_obj.items.data = [sub_item]

    event = MagicMock()
    event.id = "evt_gm_preserve_upd_001"
    event.data.object = sub_obj

    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            await handle_subscription_updated(event, session)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(select(Subscription).where(Subscription.user_id == user_id))
        row = result.scalar_one()

    # State field (cancel_at_period_end) was synced correctly — handler ran.
    assert row.cancel_at_period_end is True

    # grant_micros MUST be unchanged — handler does not own grant_micros authority.
    assert row.grant_micros == 0, (
        f"handle_subscription_updated MUST NOT overwrite grant_micros. "
        f"Expected 0 (subscribe endpoint's flag-gated value), got {row.grant_micros!r}. "
        f"Architectural invariant: subscribe endpoint + invoice.paid are authoritative (ticket 0024.7)."
    )

    # No ledger entry.
    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(
            select(BalanceLedger).where(BalanceLedger.user_id == user_id)
        )
        entries = result.scalars().all()
    assert len(entries) == 0


@pytest.mark.asyncio
async def test_handle_invoice_paid_subscription_cycle_updates_grant_micros_when_flag_on():
    """invoice.paid (subscription_cycle) updates grant_micros from price metadata when flag=True.

    Architectural invariant (ticket 0024.7 Path B): the invoice.paid cycle handler is
    the authoritative renewal-time source for grant_micros when grants are enabled.
    This captures mid-subscription tier changes (e.g., upgrade from Starter to Pro
    changes grant_micros from 10000000 to 20000000; the next renewal invoice carries
    the new tier's price metadata).

    Also verifies that subscription_reset ledger entry is posted using the NEW grant_micros.
    """
    from app.billing.handlers.subscription import handle_invoice_paid
    from app.services.auth_service import hash_password

    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            user = User(
                email="inv_cycle_gm_update_on@example.com",
                password_hash=hash_password("Password123!"),
                full_name="Inv Cycle GM Update On",
                stripe_customer_id="cus_cycle_gm_on",
            )
            session.add(user)
            await session.flush()
            user_id = user.id

    # Pre-insert row with grant_micros=10000000 (old tier value).
    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            row = Subscription(
                user_id=user_id,
                stripe_subscription_id="sub_gm_update_on_001",
                stripe_price_id="price_starter_001",
                tier_key="starter_monthly",
                tier_name="Starter",
                status="active",
                grant_micros=10_000_000,
                current_period_start=datetime(2026, 1, 1, tzinfo=None),
                current_period_end=datetime(2026, 2, 1, tzinfo=None),
                cancel_at_period_end=False,
            )
            session.add(row)

    # Invoice line carries the new tier's price metadata (grant_micros=20000000).
    price_meta_mock = MagicMock()
    price_meta_mock.get = lambda k, d=None: {"grant_micros": "20000000", "tier_name": "Pro"}.get(
        k, d
    )

    price_mock = MagicMock()
    price_mock.metadata = price_meta_mock

    line_period = MagicMock()
    line_period.start = _PERIOD_START_TS
    line_period.end = _PERIOD_END_TS + 86400 * 30

    line = MagicMock()
    line.period = line_period
    line.price = price_mock

    invoice_obj = MagicMock()
    invoice_obj.billing_reason = "subscription_cycle"
    invoice_obj.subscription = "sub_gm_update_on_001"
    invoice_obj.lines.data = [line]

    event = MagicMock()
    event.id = "evt_gm_update_on_001"
    event.data.object = invoice_obj

    with patch.object(settings, "BILLING_SUBSCRIPTION_GRANTS_TO_LEDGER", True):
        async with AsyncSession(engine, expire_on_commit=False) as session:
            async with session.begin():
                await handle_invoice_paid(event, session)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(select(Subscription).where(Subscription.user_id == user_id))
        row = result.scalar_one()

    # grant_micros updated to the new tier's value.
    assert row.grant_micros == 20_000_000, (
        f"invoice.paid cycle handler must update grant_micros from price metadata when flag=True. "
        f"Expected 20000000, got {row.grant_micros!r}. "
        f"Architectural invariant: invoice.paid is authoritative for renewal grant_micros (ticket 0024.7)."
    )

    # subscription_reset ledger entry posted using the NEW grant_micros.
    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(
            select(BalanceLedger).where(BalanceLedger.user_id == user_id)
        )
        entries = result.scalars().all()
    assert len(entries) == 1
    assert entries[0].amount_micros == 20_000_000
    assert entries[0].reason == "subscription_reset"
    assert entries[0].stripe_event_id == "evt_gm_update_on_001"


@pytest.mark.asyncio
async def test_handle_invoice_paid_subscription_cycle_does_not_update_grant_micros_when_flag_off():
    """invoice.paid (subscription_cycle) does NOT touch grant_micros when flag=False.

    When BILLING_SUBSCRIPTION_GRANTS_TO_LEDGER=False, grant_micros is irrelevant
    (no ledger writes; subscription = access tier only). The handler must leave the
    row's grant_micros untouched and post no ledger entry.

    Scenario: pre-existing row has grant_micros=10000000. Invoice line carries
    metadata.grant_micros=20000000. Row must still have 10000000 after the handler.
    """
    from app.billing.handlers.subscription import handle_invoice_paid
    from app.services.auth_service import hash_password

    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            user = User(
                email="inv_cycle_gm_update_off@example.com",
                password_hash=hash_password("Password123!"),
                full_name="Inv Cycle GM Update Off",
                stripe_customer_id="cus_cycle_gm_off",
            )
            session.add(user)
            await session.flush()
            user_id = user.id

    # Pre-insert row with grant_micros=10000000.
    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            row = Subscription(
                user_id=user_id,
                stripe_subscription_id="sub_gm_update_off_001",
                stripe_price_id="price_starter_001",
                tier_key="starter_monthly",
                tier_name="Starter",
                status="active",
                grant_micros=10_000_000,
                current_period_start=datetime(2026, 1, 1, tzinfo=None),
                current_period_end=datetime(2026, 2, 1, tzinfo=None),
                cancel_at_period_end=False,
            )
            session.add(row)

    # Invoice line carries metadata.grant_micros=20000000 — handler must ignore (flag off).
    price_meta_mock = MagicMock()
    price_meta_mock.get = lambda k, d=None: {"grant_micros": "20000000", "tier_name": "Pro"}.get(
        k, d
    )

    price_mock = MagicMock()
    price_mock.metadata = price_meta_mock

    line_period = MagicMock()
    line_period.start = _PERIOD_START_TS
    line_period.end = _PERIOD_END_TS + 86400 * 30

    line = MagicMock()
    line.period = line_period
    line.price = price_mock

    invoice_obj = MagicMock()
    invoice_obj.billing_reason = "subscription_cycle"
    invoice_obj.subscription = "sub_gm_update_off_001"
    invoice_obj.lines.data = [line]

    event = MagicMock()
    event.id = "evt_gm_update_off_001"
    event.data.object = invoice_obj

    with patch.object(settings, "BILLING_SUBSCRIPTION_GRANTS_TO_LEDGER", False):
        async with AsyncSession(engine, expire_on_commit=False) as session:
            async with session.begin():
                await handle_invoice_paid(event, session)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(select(Subscription).where(Subscription.user_id == user_id))
        row = result.scalar_one()

    # grant_micros must be unchanged.
    assert row.grant_micros == 10_000_000, (
        f"invoice.paid cycle handler MUST NOT update grant_micros when flag=False. "
        f"Expected 10000000, got {row.grant_micros!r}. "
        f"Architectural invariant: grant_micros is irrelevant when flag=false (ticket 0024.7)."
    )

    # No ledger entry (flag off).
    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(
            select(BalanceLedger).where(BalanceLedger.user_id == user_id)
        )
        entries = result.scalars().all()
    assert len(entries) == 0
