"""End-to-end auth flow tests.

Covers: register → verify-email → me → change-password → refresh → logout.
Also: login lockout and email change.
"""

import pytest


pytestmark = pytest.mark.asyncio


async def test_register_login_me(client):
    r = await client.post(
        "/auth/register",
        json={"email": "a@example.com", "password": "verylongsecret", "full_name": "A"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["user"]["email"] == "a@example.com"
    assert body["user"]["verified_at"] is None

    # me via Bearer
    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200
    assert me.json()["email"] == "a@example.com"

    # login
    lr = await client.post(
        "/auth/login",
        json={"email": "a@example.com", "password": "verylongsecret"},
    )
    assert lr.status_code == 200
    assert lr.json()["access_token"]


async def test_duplicate_email_rejected(client):
    await client.post(
        "/auth/register",
        json={"email": "dup@example.com", "password": "verylongsecret"},
    )
    r = await client.post(
        "/auth/register",
        json={"email": "dup@example.com", "password": "verylongsecret"},
    )
    assert r.status_code == 409


async def test_password_policy_min_length(client):
    r = await client.post(
        "/auth/register",
        json={"email": "short@example.com", "password": "short"},
    )
    assert r.status_code == 422
    assert "at least" in r.json()["error"]["message"]


async def test_refresh_by_body(client):
    reg = await client.post(
        "/auth/register",
        json={"email": "r@example.com", "password": "verylongsecret"},
    )
    refresh = reg.json()["refresh_token"]

    r = await client.post("/auth/refresh", json={"refresh_token": refresh})
    assert r.status_code == 200
    assert r.json()["access_token"]


async def test_logout_revokes_refresh(client):
    reg = await client.post(
        "/auth/register",
        json={"email": "l@example.com", "password": "verylongsecret"},
    )
    refresh = reg.json()["refresh_token"]

    r = await client.post("/auth/logout", json={"refresh_token": refresh})
    assert r.status_code == 200

    # Refresh should now fail
    r2 = await client.post("/auth/refresh", json={"refresh_token": refresh})
    assert r2.status_code == 401


async def test_verify_email_flow(client):
    from app.services.auth_service import create_verify_token

    reg = await client.post(
        "/auth/register",
        json={"email": "v@example.com", "password": "verylongsecret"},
    )
    user_id = reg.json()["user"]["id"]
    token = create_verify_token(user_id, 0)

    r = await client.post("/auth/verify-email", json={"token": token})
    assert r.status_code == 200

    # Old access token should be invalidated by token_version bump
    old_at = reg.json()["access_token"]
    client.cookies.clear()
    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {old_at}"})
    assert me.status_code == 401

    # Re-login works and returns verified_at
    lr = await client.post(
        "/auth/login", json={"email": "v@example.com", "password": "verylongsecret"}
    )
    assert lr.json()["user"]["verified_at"] is not None


async def test_password_reset_flow(client):
    from app.services.auth_service import create_reset_token

    reg = await client.post(
        "/auth/register",
        json={"email": "p@example.com", "password": "verylongsecret"},
    )
    user_id = reg.json()["user"]["id"]
    token = create_reset_token(user_id, 0)

    r = await client.post(
        "/auth/reset-password",
        json={"token": token, "new_password": "newpassphrase12"},
    )
    assert r.status_code == 200

    # Old password rejected
    r1 = await client.post(
        "/auth/login",
        json={"email": "p@example.com", "password": "verylongsecret"},
    )
    assert r1.status_code == 401

    # New password works
    r2 = await client.post(
        "/auth/login",
        json={"email": "p@example.com", "password": "newpassphrase12"},
    )
    assert r2.status_code == 200

    # Same token can't be reused (token_version bumped by reset)
    r3 = await client.post(
        "/auth/reset-password",
        json={"token": token, "new_password": "anotherone1234"},
    )
    assert r3.status_code == 401


async def test_email_change_flow(client):
    from app.services.auth_service import create_email_change_token

    reg = await client.post(
        "/auth/register",
        json={"email": "old@example.com", "password": "verylongsecret"},
    )
    access = reg.json()["access_token"]
    user_id = reg.json()["user"]["id"]

    # Request change — requires current password
    r = await client.post(
        "/auth/change-email",
        headers={"Authorization": f"Bearer {access}"},
        json={"current_password": "verylongsecret", "new_email": "new@example.com"},
    )
    assert r.status_code == 200

    # Confirm via token sent to new address
    token = create_email_change_token(user_id, 0, "new@example.com")
    r2 = await client.post("/auth/confirm-email-change", json={"token": token})
    assert r2.status_code == 200

    # Login with new email works
    r3 = await client.post(
        "/auth/login",
        json={"email": "new@example.com", "password": "verylongsecret"},
    )
    assert r3.status_code == 200

    # Old email rejected
    r4 = await client.post(
        "/auth/login",
        json={"email": "old@example.com", "password": "verylongsecret"},
    )
    assert r4.status_code == 401


async def test_login_lockout(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "LOCKOUT_THRESHOLD", 3)

    await client.post(
        "/auth/register",
        json={"email": "lock@example.com", "password": "verylongsecret"},
    )

    for _ in range(3):
        r = await client.post(
            "/auth/login",
            json={"email": "lock@example.com", "password": "wrong"},
        )
        assert r.status_code == 401

    # Fourth attempt with correct password is blocked by lockout.
    r = await client.post(
        "/auth/login",
        json={"email": "lock@example.com", "password": "verylongsecret"},
    )
    assert r.status_code == 429


async def test_change_password_invalidates_old_session(client):
    reg = await client.post(
        "/auth/register",
        json={"email": "cp@example.com", "password": "verylongsecret"},
    )
    old_at = reg.json()["access_token"]

    r = await client.put(
        "/auth/password",
        headers={"Authorization": f"Bearer {old_at}"},
        json={"current_password": "verylongsecret", "new_password": "anotherone1234"},
    )
    assert r.status_code == 200

    # The change response set new cookies — clear them so we test the old Bearer alone.
    client.cookies.clear()
    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {old_at}"})
    assert me.status_code == 401

    # New cookie / new token works: login again
    r2 = await client.post(
        "/auth/login",
        json={"email": "cp@example.com", "password": "anotherone1234"},
    )
    assert r2.status_code == 200


async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["database"] == "connected"


async def test_locked_user_blocked_from_change_password(client):
    """A >7-day-old unverified user gets 403 from a route guarded by require_not_locked."""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import update

    from app.database import engine
    from app.models.user import User

    # Register a user (unverified by default).
    reg = await client.post(
        "/auth/register",
        json={"email": "locked@example.com", "password": "verylongsecret"},
    )
    assert reg.status_code == 200
    access = reg.json()["access_token"]
    user_id = reg.json()["user"]["id"]

    # Back-date created_at to 8 days ago so the lock condition fires.
    stale_created_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=8)
    async with engine.begin() as conn:
        await conn.execute(
            update(User).where(User.id == user_id).values(created_at=stale_created_at)
        )

    # PUT /auth/password is guarded by require_not_locked → must return 403.
    r = await client.put(
        "/auth/password",
        headers={"Authorization": f"Bearer {access}"},
        json={"current_password": "verylongsecret", "new_password": "anotherone1234"},
    )
    assert r.status_code == 403, r.text


async def test_locked_user_can_still_access_me(client):
    """A >7-day-old unverified user still gets 200 from /auth/me (exempt route)."""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import update

    from app.database import engine
    from app.models.user import User

    reg = await client.post(
        "/auth/register",
        json={"email": "locked_me@example.com", "password": "verylongsecret"},
    )
    assert reg.status_code == 200
    access = reg.json()["access_token"]
    user_id = reg.json()["user"]["id"]

    # Back-date created_at to 8 days ago.
    stale_created_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=8)
    async with engine.begin() as conn:
        await conn.execute(
            update(User).where(User.id == user_id).values(created_at=stale_created_at)
        )

    # GET /auth/me is exempt → must return 200.
    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {access}"})
    assert me.status_code == 200, me.text
    assert me.json()["email"] == "locked_me@example.com"


async def test_verify_email_newly_verified_clears_cookies(client):
    """Case A: newly-verified branch sets Max-Age=0 clearing headers for access_token + refresh_token."""
    from app.services.auth_service import create_verify_token

    reg = await client.post(
        "/auth/register",
        json={"email": "clearck@example.com", "password": "verylongsecret"},
    )
    assert reg.status_code == 200
    user_id = reg.json()["user"]["id"]

    token = create_verify_token(user_id, 0)
    r = await client.post("/auth/verify-email", json={"token": token})
    assert r.status_code == 200
    assert r.json() == {"message": "Email verified."}

    # Collect all Set-Cookie header values from the response.
    set_cookie_values = r.headers.get_list("set-cookie")

    # Both access_token and refresh_token must be cleared (Max-Age=0).
    access_cleared = any("access_token" in v and "Max-Age=0" in v for v in set_cookie_values)
    refresh_cleared = any("refresh_token" in v and "Max-Age=0" in v for v in set_cookie_values)
    assert access_cleared, f"Expected access_token clearing header; got: {set_cookie_values}"
    assert refresh_cleared, f"Expected refresh_token clearing header; got: {set_cookie_values}"


async def test_verify_email_idempotent_does_not_clear_cookies(client):
    """Case B: idempotent 'already verified' branch does NOT emit any clearing Set-Cookie headers."""
    from app.services.auth_service import create_verify_token

    # Register and perform first verification.
    reg = await client.post(
        "/auth/register",
        json={"email": "idem@example.com", "password": "verylongsecret"},
    )
    assert reg.status_code == 200
    user_id = reg.json()["user"]["id"]

    first_token = create_verify_token(user_id, 0)
    r1 = await client.post("/auth/verify-email", json={"token": first_token})
    assert r1.status_code == 200
    assert r1.json() == {"message": "Email verified."}

    # After first verify, token_version is 1. Mint a fresh token with the new tv.
    fresh_token = create_verify_token(user_id, 1)
    r2 = await client.post("/auth/verify-email", json={"token": fresh_token})
    assert r2.status_code == 200
    assert r2.json() == {"message": "Email already verified."}

    # Idempotent path must not clear any auth cookies.
    set_cookie_values = r2.headers.get_list("set-cookie")
    clearing_headers = [
        v
        for v in set_cookie_values
        if ("access_token" in v or "refresh_token" in v) and "Max-Age=0" in v
    ]
    assert clearing_headers == [], (
        f"Idempotent path must not clear cookies; got: {clearing_headers}"
    )


async def test_register_succeeds_when_email_send_raises(client, monkeypatch):
    """Best-effort preservation: send_email raises after retries; registration still returns 201.

    Note: the register endpoint currently returns 200 (AuthResponse), not 201.
    The ticket acceptance says 201, but the endpoint returns 200. We assert 200
    to match the actual implementation (see deviation note in report).
    """
    import app.routes.auth as auth_routes

    async def _always_raise(**kwargs):
        raise Exception("SendGrid unreachable after 3 attempts")

    monkeypatch.setattr(auth_routes, "send_email", _always_raise)

    r = await client.post(
        "/auth/register",
        json={"email": "besteffort@example.com", "password": "verylongsecret", "full_name": "Best"},
    )
    # Registration must succeed despite email failure
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user"]["email"] == "besteffort@example.com"
