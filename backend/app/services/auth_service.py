from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Optional

import bcrypt
from jose import JWTError, jwt
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.logging import get_logger
from app.models.refresh_token import RefreshToken

logger = get_logger(__name__)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def _utc_naive_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def create_access_token(user_id: int, token_version: int = 0) -> str:
    expire = datetime.now(tz=timezone.utc) + timedelta(
        minutes=settings.JWT_EXPIRATION_MINUTES
    )  # tz-aware: python-jose expects aware datetimes for exp. See doc/issues/0001.
    payload = {
        "sub": str(user_id),
        "tv": token_version,
        "exp": expire,
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def _create_purpose_token(
    user_id: int, token_version: int, purpose: str, expires_delta: timedelta, **extra
) -> str:
    expire = (
        datetime.now(tz=timezone.utc) + expires_delta
    )  # tz-aware: python-jose expects aware datetimes for exp. See doc/issues/0001.
    payload = {
        "sub": str(user_id),
        "tv": token_version,
        "purpose": purpose,
        "exp": expire,
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
        **extra,
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def _decode_purpose_token(token: str, purpose: str) -> Optional[dict]:
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
            audience=settings.JWT_AUDIENCE,
            issuer=settings.JWT_ISSUER,
        )
    except JWTError as e:
        logger.warning("Token decode failed", extra={"purpose": purpose, "error": str(e)})
        return None

    if payload.get("purpose") != purpose:
        return None
    if not payload.get("sub") or payload.get("tv") is None:
        return None
    return payload


def create_reset_token(user_id: int, token_version: int) -> str:
    return _create_purpose_token(
        user_id,
        token_version,
        "reset",
        timedelta(minutes=settings.PASSWORD_RESET_EXPIRY_MINUTES),
    )


def decode_reset_token(token: str) -> Optional[dict]:
    return _decode_purpose_token(token, "reset")


def create_verify_token(user_id: int, token_version: int) -> str:
    return _create_purpose_token(
        user_id,
        token_version,
        "verify",
        timedelta(hours=settings.EMAIL_VERIFY_EXPIRY_HOURS),
    )


def decode_verify_token(token: str) -> Optional[dict]:
    return _decode_purpose_token(token, "verify")


def create_email_change_token(user_id: int, token_version: int, new_email: str) -> str:
    return _create_purpose_token(
        user_id,
        token_version,
        "email_change",
        timedelta(hours=settings.EMAIL_CHANGE_EXPIRY_HOURS),
        new_email=new_email,
    )


def decode_email_change_token(token: str) -> Optional[dict]:
    payload = _decode_purpose_token(token, "email_change")
    if payload is None or not payload.get("new_email"):
        return None
    return payload


def _hash_refresh_token(raw_token: str) -> str:
    return sha256(raw_token.encode()).hexdigest()


async def create_refresh_token(user_id: int, db: AsyncSession) -> tuple[str, RefreshToken]:
    raw_token = secrets.token_urlsafe(48)
    token_hash = _hash_refresh_token(raw_token)
    expires_at = _utc_naive_now() + timedelta(days=settings.REFRESH_TOKEN_DAYS)

    row = RefreshToken(user_id=user_id, token_hash=token_hash, expires_at=expires_at)
    db.add(row)
    await db.flush()
    return raw_token, row


async def verify_refresh_token(raw_token: str, db: AsyncSession) -> Optional[RefreshToken]:
    token_hash = _hash_refresh_token(raw_token)
    result = await db.execute(
        select(RefreshToken).where(
            RefreshToken.token_hash == token_hash,
            RefreshToken.revoked_at.is_(None),
        )
    )
    row = result.scalar_one_or_none()
    if not row:
        return None
    if _utc_naive_now() > row.expires_at:
        return None
    return row


async def revoke_refresh_token(raw_token: str, db: AsyncSession) -> None:
    token_hash = _hash_refresh_token(raw_token)
    result = await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    row = result.scalar_one_or_none()
    if row and row.revoked_at is None:
        row.revoked_at = _utc_naive_now()


async def revoke_all_user_tokens(user_id: int, db: AsyncSession) -> None:
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=_utc_naive_now())
    )
    await db.flush()
