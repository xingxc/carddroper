"""Tests for GET /billing/tiers endpoint.

Ticket 0024.1 Phase 0a.

Stripe API calls are mocked via unittest.mock — no real Stripe calls.
All tests use the autouse _reset_schema fixture from conftest.py.

Kind-2 isolation: entire module skipped when BILLING_ENABLED=false.
"""

import logging
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import engine
from app.models import User

pytestmark = pytest.mark.skipif(
    not settings.BILLING_ENABLED,
    reason="test_billing_tiers requires BILLING_ENABLED=true — feature-gated at app-init time",
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _register_and_login(client, email: str) -> str:
    """Register + verify a user; return access_token."""
    from app.services.auth_service import create_verify_token

    mock_customer = MagicMock()
    mock_customer.id = f"cus_{email.split('@')[0]}"

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch("app.billing.primitives.stripe") as mock_p,
    ):
        mock_p.Customer.create.return_value = mock_customer
        reg = await client.post(
            "/auth/register",
            json={"email": email, "password": "StrongPassword99!", "full_name": "Tiers User"},
        )
    assert reg.status_code == 200, reg.text

    async with AsyncSession(engine, expire_on_commit=False) as session:
        from sqlalchemy import select

        result = await session.execute(select(User).where(User.email == email))
        user = result.scalar_one()

    token = create_verify_token(user.id, user.token_version)
    with patch.object(settings, "BILLING_ENABLED", True):
        verify = await client.post("/auth/verify-email", json={"token": token})
    assert verify.status_code == 200, verify.text

    return reg.json()["access_token"]


def _mock_price(
    lookup_key: str = "starter_monthly",
    tier_name: str = "Starter",
    grant_micros: str = "10000000",
    unit_amount: int = 999,
    currency: str = "usd",
    interval: str = "month",
    interval_count: int = 1,
    description: str | None = "Great starter plan",
) -> MagicMock:
    """Build a mock Stripe Price object with expanded Product."""
    price = MagicMock()
    price.lookup_key = lookup_key
    price.metadata = {"tier_name": tier_name, "grant_micros": grant_micros}
    price.unit_amount = unit_amount
    price.currency = currency

    _recurring_data = {"interval": interval, "interval_count": interval_count}
    recurring = MagicMock()
    recurring.get = lambda k, default=None: _recurring_data.get(k, default)
    recurring.interval = interval
    recurring.interval_count = interval_count
    price.recurring = recurring

    product = MagicMock()
    product.description = description
    price.product = product

    return price


def _mock_prices_list(prices: list) -> MagicMock:
    """Wrap a list of mock Price objects in a Stripe-like list response."""
    mock_list = MagicMock()
    mock_list.data = prices
    return mock_list


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_tiers_empty_param_returns_empty(client):
    """?lookup_keys= (empty string) → 200 []."""
    access_token = await _register_and_login(client, "tiers_empty@example.com")

    with patch.object(settings, "BILLING_ENABLED", True):
        resp = await client.get(
            "/billing/tiers?lookup_keys=",
            headers=_auth_headers(access_token),
        )

    assert resp.status_code == 200, resp.text
    assert resp.json() == []


@pytest.mark.asyncio
async def test_get_tiers_no_param_returns_empty(client):
    """/billing/tiers (no query string) → 200 []."""
    access_token = await _register_and_login(client, "tiers_noparam@example.com")

    with patch.object(settings, "BILLING_ENABLED", True):
        resp = await client.get(
            "/billing/tiers",
            headers=_auth_headers(access_token),
        )

    assert resp.status_code == 200, resp.text
    assert resp.json() == []


@pytest.mark.asyncio
async def test_get_tiers_unknown_lookup_keys_returns_empty(client):
    """Stripe returns no matches for the given keys → 200 []."""
    access_token = await _register_and_login(client, "tiers_unknown@example.com")

    mock_list = _mock_prices_list([])

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch("app.routes.billing.stripe") as mock_stripe,
    ):
        mock_stripe.Price.list.return_value = mock_list
        resp = await client.get(
            "/billing/tiers?lookup_keys=nonexistent_key",
            headers=_auth_headers(access_token),
        )

    assert resp.status_code == 200, resp.text
    assert resp.json() == []


@pytest.mark.asyncio
async def test_get_tiers_returns_enriched_envelope(client):
    """Mocked Stripe returns a valid Price + Product → returns fully-populated TierEnvelope."""
    access_token = await _register_and_login(client, "tiers_envelope@example.com")

    price = _mock_price(
        lookup_key="starter_monthly",
        tier_name="Starter",
        grant_micros="10000000",
        unit_amount=999,
        currency="usd",
        interval="month",
        interval_count=1,
        description="Great starter plan",
    )
    mock_list = _mock_prices_list([price])

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch("app.routes.billing.stripe") as mock_stripe,
    ):
        mock_stripe.Price.list.return_value = mock_list
        resp = await client.get(
            "/billing/tiers?lookup_keys=starter_monthly",
            headers=_auth_headers(access_token),
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data) == 1
    tier = data[0]
    assert tier["lookup_key"] == "starter_monthly"
    assert tier["tier_name"] == "Starter"
    assert tier["description"] == "Great starter plan"
    assert tier["price_display"] == "$9.99/month"
    assert tier["amount_cents"] == 999
    assert tier["currency"] == "usd"
    assert tier["interval"] == "month"
    assert tier["interval_count"] == 1
    assert tier["grant_micros"] == 10_000_000


@pytest.mark.asyncio
async def test_get_tiers_skips_price_missing_grant_micros(client):
    """Price without metadata.grant_micros → log warning + tier excluded; valid tiers still returned."""
    access_token = await _register_and_login(client, "tiers_no_grant@example.com")

    bad_price = MagicMock()
    bad_price.lookup_key = "bad_key"
    bad_price.metadata = {"tier_name": "Bad"}  # missing grant_micros
    bad_price.unit_amount = 999
    bad_price.currency = "usd"
    bad_price.recurring = MagicMock()
    bad_price.recurring.get = lambda k, d=None: {"interval": "month", "interval_count": 1}.get(k, d)

    good_price = _mock_price(lookup_key="starter_monthly", tier_name="Starter")
    mock_list = _mock_prices_list([bad_price, good_price])

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch("app.routes.billing.stripe") as mock_stripe,
    ):
        mock_stripe.Price.list.return_value = mock_list
        resp = await client.get(
            "/billing/tiers?lookup_keys=bad_key,starter_monthly",
            headers=_auth_headers(access_token),
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    # Only the good tier is returned; bad_key skipped
    assert len(data) == 1
    assert data[0]["lookup_key"] == "starter_monthly"


@pytest.mark.asyncio
async def test_get_tiers_skips_price_missing_tier_name(client):
    """Price without metadata.tier_name → log warning + tier excluded."""
    access_token = await _register_and_login(client, "tiers_no_tier_name@example.com")

    bad_price = MagicMock()
    bad_price.lookup_key = "bad_name_key"
    bad_price.metadata = {"grant_micros": "10000000"}  # missing tier_name
    bad_price.unit_amount = 999
    bad_price.currency = "usd"
    bad_price.recurring = MagicMock()
    bad_price.recurring.get = lambda k, d=None: {"interval": "month", "interval_count": 1}.get(k, d)

    mock_list = _mock_prices_list([bad_price])

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch("app.routes.billing.stripe") as mock_stripe,
    ):
        mock_stripe.Price.list.return_value = mock_list
        resp = await client.get(
            "/billing/tiers?lookup_keys=bad_name_key",
            headers=_auth_headers(access_token),
        )

    assert resp.status_code == 200, resp.text
    assert resp.json() == []


@pytest.mark.asyncio
async def test_get_tiers_handles_non_usd_currency(client, caplog):
    """Stripe returns EUR Price → log warning + tier still returned with '$' prefix fallback."""
    access_token = await _register_and_login(client, "tiers_eur@example.com")

    price = _mock_price(
        lookup_key="starter_eur",
        currency="eur",
        unit_amount=999,
    )
    mock_list = _mock_prices_list([price])

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch("app.routes.billing.stripe") as mock_stripe,
        caplog.at_level(logging.WARNING, logger="app.routes.billing"),
    ):
        mock_stripe.Price.list.return_value = mock_list
        resp = await client.get(
            "/billing/tiers?lookup_keys=starter_eur",
            headers=_auth_headers(access_token),
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data) == 1
    # Tier returned with "$" prefix fallback despite non-USD currency
    assert data[0]["price_display"].startswith("$")
    assert data[0]["currency"] == "eur"
    # Warning logged
    assert any("tiers_non_usd_currency" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_get_tiers_preserves_input_order(client):
    """?lookup_keys=b,a,c → response is [b, a, c] regardless of Stripe response order."""
    access_token = await _register_and_login(client, "tiers_order@example.com")

    price_a = _mock_price(lookup_key="tier_a", tier_name="A")
    price_b = _mock_price(lookup_key="tier_b", tier_name="B")
    price_c = _mock_price(lookup_key="tier_c", tier_name="C")

    # Stripe returns them in a different order: a, c, b
    mock_list = _mock_prices_list([price_a, price_c, price_b])

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch("app.routes.billing.stripe") as mock_stripe,
    ):
        mock_stripe.Price.list.return_value = mock_list
        resp = await client.get(
            "/billing/tiers?lookup_keys=tier_b,tier_a,tier_c",
            headers=_auth_headers(access_token),
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data) == 3
    # Must match input order: b, a, c
    assert data[0]["lookup_key"] == "tier_b"
    assert data[1]["lookup_key"] == "tier_a"
    assert data[2]["lookup_key"] == "tier_c"


@pytest.mark.asyncio
async def test_get_tiers_handles_missing_product_description(client):
    """Product without description → tier returned with description=None."""
    access_token = await _register_and_login(client, "tiers_nodesc@example.com")

    price = _mock_price(lookup_key="starter_monthly", description=None)
    price.product.description = None
    mock_list = _mock_prices_list([price])

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch("app.routes.billing.stripe") as mock_stripe,
    ):
        mock_stripe.Price.list.return_value = mock_list
        resp = await client.get(
            "/billing/tiers?lookup_keys=starter_monthly",
            headers=_auth_headers(access_token),
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data) == 1
    assert data[0]["description"] is None


@pytest.mark.asyncio
async def test_get_tiers_handles_yearly_interval(client):
    """Price with recurring.interval='year' → price_display ends with '/year'."""
    access_token = await _register_and_login(client, "tiers_year@example.com")

    price = _mock_price(
        lookup_key="pro_annual",
        unit_amount=9900,
        interval="year",
        interval_count=1,
    )
    mock_list = _mock_prices_list([price])

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch("app.routes.billing.stripe") as mock_stripe,
    ):
        mock_stripe.Price.list.return_value = mock_list
        resp = await client.get(
            "/billing/tiers?lookup_keys=pro_annual",
            headers=_auth_headers(access_token),
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data) == 1
    assert data[0]["price_display"] == "$99/year"


@pytest.mark.asyncio
async def test_get_tiers_requires_auth(client):
    """No token → 401 (or 404 if billing router not mounted)."""
    resp = await client.get("/billing/tiers?lookup_keys=starter_monthly")
    assert resp.status_code in (401, 404)
