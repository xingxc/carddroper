from __future__ import annotations

import hashlib

import httpx

from app.config import settings
from app.logging import get_logger

logger = get_logger(__name__)

_HIBP_RANGE_URL = "https://api.pwnedpasswords.com/range/{prefix}"


async def is_password_pwned(password: str) -> bool:
    """Check HIBP k-anonymity API.

    Returns True if the password appears in known breaches. Returns False on
    network errors (fails open — bcrypt remains the primary defense).
    """
    if not settings.HIBP_ENABLED:
        return False

    sha1 = hashlib.sha1(password.encode("utf-8")).hexdigest().upper()
    prefix, suffix = sha1[:5], sha1[5:]

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(
                _HIBP_RANGE_URL.format(prefix=prefix),
                headers={"Add-Padding": "true", "User-Agent": "carddroper-auth"},
            )
            resp.raise_for_status()
    except Exception as e:
        logger.warning("HIBP lookup failed, failing open", extra={"error": str(e)})
        return False

    for line in resp.text.splitlines():
        candidate, _, _count = line.partition(":")
        if candidate.strip().upper() == suffix:
            return True
    return False


async def validate_password(password: str) -> tuple[bool, str | None]:
    """Return (ok, error_message)."""
    if len(password) < settings.PASSWORD_MIN_LENGTH:
        return False, f"Password must be at least {settings.PASSWORD_MIN_LENGTH} characters."
    if await is_password_pwned(password):
        return False, "This password has appeared in a known data breach. Choose a different one."
    return True, None
