"""Tests for POST /billing/portal-session endpoint.

Ticket 0025 Phase 0a.

Stripe API calls are mocked via unittest.mock — no real Stripe calls made.
All tests use the autouse _reset_schema fixture from conftest.py.
spec=-restricted MagicMock per 0024.12 discipline to prevent false-positive
attribute auto-vivification on Stripe response objects.

Kind-2 isolation: entire module skipped when BILLING_ENABLED=false.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import engine
from app.models import User

pytestmark = pytest.mark.skipif(
    not settings.BILLING_ENABLED,
    reason="test_billing_portal requires BILLING_ENABLED=true — feature-gated at app-init time",
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _register_and_login(client, email: str = "portal@example.com") -> tuple[str, int]:
    """Register a user (no verification required for billing by default) and return
    (access_token, user_id)."""
    reg = await client.post(
        "/auth/register",
        json={"email": email, "password": "StrongPassword99!", "full_name": "Portal User"},
    )
    assert reg.status_code == 200, reg.text
    data = reg.json()

    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(select(User).where(User.email == email))
        user = result.scalar_one()

    return data["access_token"], user.id


async def _create_user_with_customer(
    session,
    email: str,
    stripe_customer_id: str = "cus_existing_001",
) -> User:
    """Create a User row directly with a pre-existing stripe_customer_id."""
    from app.services.auth_service import hash_password

    async with session.begin():
        user = User(
            email=email,
            password_hash=hash_password("Password123!"),
            full_name="Portal User",
            stripe_customer_id=stripe_customer_id,
            verified_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        session.add(user)
        await session.flush()
        user_id = user.id

    async with AsyncSession(engine, expire_on_commit=False) as fresh:
        result = await fresh.execute(select(User).where(User.id == user_id))
        return result.scalar_one()


def _mock_portal_session(url: str = "https://billing.stripe.com/session/test_abc") -> MagicMock:
    """Build a spec=-restricted mock Stripe Portal Session.

    spec= lists exactly the attributes the endpoint reads — prevents false-positive
    attribute auto-vivification if the endpoint were to accidentally access an
    unintended attribute.
    """
    session = MagicMock(spec=["url", "id"])
    session.url = url
    session.id = "bps_test_001"
    return session


def _mock_customer(customer_id: str = "cus_lazy_created_001") -> MagicMock:
    """Build a spec=-restricted mock Stripe Customer."""
    customer = MagicMock(spec=["id", "email"])
    customer.id = customer_id
    return customer


# ---------------------------------------------------------------------------
# POST /billing/portal-session — tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_portal_session_requires_auth(client):
    """No auth token → 401 (or 404 if billing not mounted)."""
    resp = await client.post("/billing/portal-session", json={})
    assert resp.status_code in (401, 404)


@pytest.mark.asyncio
async def test_portal_session_creates_session_with_default_return_url(client, db_session):
    """No return_url in body → uses {FRONTEND_BASE_URL}/app/subscribe as default.

    Verifies the Stripe Session.create call receives the default return_url.
    """
    mock_customer = _mock_customer("cus_default_url_test")
    mock_session = _mock_portal_session("https://billing.stripe.com/session/default_url")

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch("app.billing.primitives.stripe") as mock_prim_stripe,
    ):
        mock_prim_stripe.Customer.create.return_value = mock_customer
        access_token, user_id = await _register_and_login(client, "portal_default@example.com")

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch.object(settings, "BILLING_PORTAL_CONFIGURATION_ID", ""),
        patch("app.routes.billing.stripe") as mock_route_stripe,
    ):
        mock_route_stripe.billing_portal.Session.create.return_value = mock_session
        mock_route_stripe.error = __import__("stripe").error

        resp = await client.post(
            "/billing/portal-session",
            json={},
            headers=_auth_headers(access_token),
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["url"] == "https://billing.stripe.com/session/default_url"

    # Verify default return_url was passed to Stripe
    call_kwargs = mock_route_stripe.billing_portal.Session.create.call_args
    assert call_kwargs is not None
    assert f"{settings.FRONTEND_BASE_URL}/app/subscribe" in str(call_kwargs)


@pytest.mark.asyncio
async def test_portal_session_validates_return_url_against_frontend_base_url(client, db_session):
    """return_url pointing to a foreign domain → 422 (open-redirect prevention)."""
    mock_customer = _mock_customer("cus_validate_url_test")

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch("app.billing.primitives.stripe") as mock_prim_stripe,
    ):
        mock_prim_stripe.Customer.create.return_value = mock_customer
        access_token, user_id = await _register_and_login(client, "portal_validate@example.com")

    with (
        patch.object(settings, "BILLING_ENABLED", True),
    ):
        resp = await client.post(
            "/billing/portal-session",
            json={"return_url": "https://evil.example.com/steal"},
            headers=_auth_headers(access_token),
        )

    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_portal_session_lazy_creates_customer(client, db_session):
    """User with no stripe_customer_id → customer is lazy-created and persisted to DB."""
    mock_customer = _mock_customer("cus_newly_created_999")
    mock_session = _mock_portal_session("https://billing.stripe.com/session/lazy_create")

    # Register without pre-existing customer (billing disabled so no auto-create at register)
    with (
        patch.object(settings, "BILLING_ENABLED", False),
    ):
        reg = await client.post(
            "/auth/register",
            json={
                "email": "portal_lazy@example.com",
                "password": "StrongPassword99!",
                "full_name": "Lazy User",
            },
        )
        assert reg.status_code == 200, reg.text
        access_token = reg.json()["access_token"]

    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(
            select(User).where(User.email == "portal_lazy@example.com")
        )
        user = result.scalar_one()
        # Confirm no stripe_customer_id (billing was off during register)
        assert user.stripe_customer_id is None

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch.object(settings, "BILLING_PORTAL_CONFIGURATION_ID", ""),
        patch("app.billing.primitives.stripe") as mock_prim_stripe,
        patch("app.routes.billing.stripe") as mock_route_stripe,
    ):
        mock_prim_stripe.Customer.create.return_value = mock_customer
        mock_route_stripe.billing_portal.Session.create.return_value = mock_session
        mock_route_stripe.error = __import__("stripe").error

        resp = await client.post(
            "/billing/portal-session",
            json={},
            headers=_auth_headers(access_token),
        )

    assert resp.status_code == 200, resp.text
    # Verify customer was created
    mock_prim_stripe.Customer.create.assert_called_once()

    # Verify stripe_customer_id was persisted to DB
    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(
            select(User).where(User.email == "portal_lazy@example.com")
        )
        updated_user = result.scalar_one()
    assert updated_user.stripe_customer_id == "cus_newly_created_999"


@pytest.mark.asyncio
async def test_portal_session_uses_existing_customer(client, db_session):
    """User with pre-existing stripe_customer_id → no new customer is created; existing ID used."""
    existing_customer_id = "cus_already_exists_123"
    mock_session = _mock_portal_session("https://billing.stripe.com/session/existing_cus")

    # Create user directly with a stripe_customer_id
    await _create_user_with_customer(
        db_session, "portal_existing@example.com", existing_customer_id
    )

    # Login to get a token
    login_resp = await client.post(
        "/auth/login",
        json={"email": "portal_existing@example.com", "password": "Password123!"},
    )
    assert login_resp.status_code == 200, login_resp.text
    access_token = login_resp.json()["access_token"]

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch.object(settings, "BILLING_PORTAL_CONFIGURATION_ID", ""),
        patch("app.billing.primitives.stripe") as mock_prim_stripe,
        patch("app.routes.billing.stripe") as mock_route_stripe,
    ):
        mock_route_stripe.billing_portal.Session.create.return_value = mock_session
        mock_route_stripe.error = __import__("stripe").error

        resp = await client.post(
            "/billing/portal-session",
            json={},
            headers=_auth_headers(access_token),
        )

    assert resp.status_code == 200, resp.text
    # Customer.create should NOT have been called (user already has one)
    mock_prim_stripe.Customer.create.assert_not_called()

    # Verify the existing customer ID was passed to Session.create
    assert existing_customer_id in str(
        mock_route_stripe.billing_portal.Session.create.call_args
    )


@pytest.mark.asyncio
async def test_portal_session_passes_configuration_id_when_set(client, db_session):
    """When BILLING_PORTAL_CONFIGURATION_ID is set, it is passed as configuration= to Stripe."""
    existing_customer_id = "cus_config_test_456"
    mock_session = _mock_portal_session("https://billing.stripe.com/session/config_set")

    await _create_user_with_customer(
        db_session, "portal_config@example.com", existing_customer_id
    )

    login_resp = await client.post(
        "/auth/login",
        json={"email": "portal_config@example.com", "password": "Password123!"},
    )
    assert login_resp.status_code == 200, login_resp.text
    access_token = login_resp.json()["access_token"]

    config_id = "bpc_1ABC123DEF456"

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch.object(settings, "BILLING_PORTAL_CONFIGURATION_ID", config_id),
        patch("app.routes.billing.stripe") as mock_route_stripe,
    ):
        mock_route_stripe.billing_portal.Session.create.return_value = mock_session
        mock_route_stripe.error = __import__("stripe").error

        resp = await client.post(
            "/billing/portal-session",
            json={},
            headers=_auth_headers(access_token),
        )

    assert resp.status_code == 200, resp.text

    # Verify configuration= was passed to Stripe Session.create
    call_kwargs = mock_route_stripe.billing_portal.Session.create.call_args
    assert call_kwargs is not None
    all_kwargs = call_kwargs[1] if call_kwargs[1] else {}
    assert all_kwargs.get("configuration") == config_id


@pytest.mark.asyncio
async def test_portal_session_omits_configuration_id_when_empty(client, db_session):
    """When BILLING_PORTAL_CONFIGURATION_ID is empty, configuration= is NOT passed to Stripe."""
    existing_customer_id = "cus_no_config_789"
    mock_session = _mock_portal_session("https://billing.stripe.com/session/no_config")

    await _create_user_with_customer(
        db_session, "portal_noconfig@example.com", existing_customer_id
    )

    login_resp = await client.post(
        "/auth/login",
        json={"email": "portal_noconfig@example.com", "password": "Password123!"},
    )
    assert login_resp.status_code == 200, login_resp.text
    access_token = login_resp.json()["access_token"]

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch.object(settings, "BILLING_PORTAL_CONFIGURATION_ID", ""),
        patch("app.routes.billing.stripe") as mock_route_stripe,
    ):
        mock_route_stripe.billing_portal.Session.create.return_value = mock_session
        mock_route_stripe.error = __import__("stripe").error

        resp = await client.post(
            "/billing/portal-session",
            json={},
            headers=_auth_headers(access_token),
        )

    assert resp.status_code == 200, resp.text

    # Verify configuration= was NOT passed to Stripe Session.create
    call_kwargs = mock_route_stripe.billing_portal.Session.create.call_args
    assert call_kwargs is not None
    all_kwargs = call_kwargs[1] if call_kwargs[1] else {}
    assert "configuration" not in all_kwargs


@pytest.mark.asyncio
async def test_portal_session_returns_url_from_stripe(client, db_session):
    """Response body contains the URL returned by Stripe."""
    existing_customer_id = "cus_url_return_test"
    stripe_url = "https://billing.stripe.com/session/actual_stripe_url_xyz"
    mock_session = _mock_portal_session(stripe_url)

    await _create_user_with_customer(
        db_session, "portal_urlreturn@example.com", existing_customer_id
    )

    login_resp = await client.post(
        "/auth/login",
        json={"email": "portal_urlreturn@example.com", "password": "Password123!"},
    )
    assert login_resp.status_code == 200, login_resp.text
    access_token = login_resp.json()["access_token"]

    with (
        patch.object(settings, "BILLING_ENABLED", True),
        patch.object(settings, "BILLING_PORTAL_CONFIGURATION_ID", ""),
        patch("app.routes.billing.stripe") as mock_route_stripe,
    ):
        mock_route_stripe.billing_portal.Session.create.return_value = mock_session
        mock_route_stripe.error = __import__("stripe").error

        resp = await client.post(
            "/billing/portal-session",
            json={"return_url": f"{settings.FRONTEND_BASE_URL}/app/subscribe"},
            headers=_auth_headers(access_token),
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data == {"url": stripe_url}
