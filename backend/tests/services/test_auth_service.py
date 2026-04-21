"""Unit tests for bcrypt password helpers in app.services.auth_service (ticket 0003 gap)."""

from app.services.auth_service import hash_password, verify_password


def test_hash_and_verify_password_roundtrip():
    password = "verylongsecret"
    hashed = hash_password(password)
    assert verify_password(password, hashed) is True


def test_wrong_password_returns_false():
    hashed = hash_password("verylongsecret")
    assert verify_password("wrongpassword", hashed) is False


def test_hash_produces_bcrypt_prefix():
    hashed = hash_password("verylongsecret")
    assert hashed.startswith("$2b$"), f"Expected bcrypt prefix $2b$, got: {hashed[:10]}"
    assert len(hashed) >= 59, f"Expected bcrypt length ~60, got: {len(hashed)}"
