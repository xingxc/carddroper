"""Tests for JWT iss/aud claims (ticket 0011, Deliverable B)."""

import time

import pytest
from jose import jwt

from app.config import settings

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mint(payload_overrides: dict) -> str:
    """Mint a raw JWT with the project secret, allowing arbitrary claim overrides."""
    base = {
        "sub": "1",
        "tv": 0,
        "exp": int(time.time()) + 60,
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
    }
    base.update(payload_overrides)
    return jwt.encode(base, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


async def _register(client) -> dict:
    r = await client.post(
        "/auth/register",
        json={"email": "jwt@example.com", "password": "verylongsecret", "full_name": "JWT"},
    )
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------------------
# B-happy: minted token decodes and carries iss + aud
# ---------------------------------------------------------------------------


async def test_access_token_has_iss_and_aud(client):
    body = await _register(client)
    raw_token = body["access_token"]

    # Decode without validation to inspect claims.
    payload = jwt.decode(
        raw_token,
        settings.JWT_SECRET,
        algorithms=[settings.JWT_ALGORITHM],
        audience=settings.JWT_AUDIENCE,
        issuer=settings.JWT_ISSUER,
    )
    assert payload["iss"] == "carddroper"
    assert payload["aud"] == "carddroper-api"


async def test_access_token_accepted_by_me(client):
    body = await _register(client)
    r = await client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {body['access_token']}"},
    )
    assert r.status_code == 200
    # /auth/me returns envelope {user, expires_in}
    assert r.json()["user"]["email"] == "jwt@example.com"
    assert isinstance(r.json()["expires_in"], int) and r.json()["expires_in"] > 0


# ---------------------------------------------------------------------------
# B-wrong-audience
# ---------------------------------------------------------------------------


async def test_wrong_audience_returns_401(client):
    token = _mint({"aud": "carddroper-other"})
    r = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401
    err = r.json()["error"]
    assert err["code"] == "INVALID_TOKEN"


# ---------------------------------------------------------------------------
# B-wrong-issuer
# ---------------------------------------------------------------------------


async def test_wrong_issuer_returns_401(client):
    token = _mint({"iss": "someone-else"})
    r = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401
    err = r.json()["error"]
    assert err["code"] == "INVALID_TOKEN"


# ---------------------------------------------------------------------------
# B-missing-aud
# ---------------------------------------------------------------------------


async def test_missing_aud_returns_401(client):
    # Encode without aud at all.
    payload = {
        "sub": "1",
        "tv": 0,
        "exp": int(time.time()) + 60,
        "iss": settings.JWT_ISSUER,
    }
    token = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
    r = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401
    err = r.json()["error"]
    assert err["code"] == "INVALID_TOKEN"


# ---------------------------------------------------------------------------
# B-missing-iss
# ---------------------------------------------------------------------------


async def test_missing_iss_returns_401(client):
    payload = {
        "sub": "1",
        "tv": 0,
        "exp": int(time.time()) + 60,
        "aud": settings.JWT_AUDIENCE,
    }
    token = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
    r = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401
    err = r.json()["error"]
    assert err["code"] == "INVALID_TOKEN"


# ---------------------------------------------------------------------------
# 0001 gap: exp is an integer epoch (tz-aware datetime encoding contract)
# ---------------------------------------------------------------------------


async def test_exp_is_integer_epoch(client):
    r = await client.post(
        "/auth/register",
        json={
            "email": "test+exp-epoch@example.com",
            "password": "verylongsecret",
            "full_name": "Exp Test",
        },
    )
    assert r.status_code == 200, r.text
    raw_token = r.json()["access_token"]

    payload = jwt.decode(
        raw_token,
        settings.JWT_SECRET,
        algorithms=[settings.JWT_ALGORITHM],
        options={"verify_signature": False, "verify_aud": False, "verify_iss": False},
    )
    assert isinstance(payload["exp"], int), f"exp should be int, got {type(payload['exp'])}"
    assert payload["exp"] > time.time(), "exp should be in the future"
