"""FastAPI auth dependencies.

Provides:
  - get_current_user_optional  — User or None
  - get_current_user           — User or raises 401
  - require_verified           — User with verified_at, else 403
  - require_not_locked         — User within 7-day unverified grace, else 403

Token source order: access_token cookie → Authorization: Bearer header.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, Request
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.errors import forbidden, invalid_token, missing_auth
from app.logging import get_logger
from app.models.user import User

logger = get_logger(__name__)

UNVERIFIED_LOCK_DAYS = 7


def _extract_token(request: Request) -> Optional[str]:
    token = request.cookies.get("access_token")
    if token:
        return token
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[len("Bearer ") :]
        return token or None
    return None


def _authenticate(request: Request) -> tuple[Optional[str], Optional[dict]]:
    token = _extract_token(request)
    if not token:
        return None, None
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
            audience=settings.JWT_AUDIENCE,
            issuer=settings.JWT_ISSUER,
        )
    except JWTError as e:
        logger.warning("JWT decode failed", extra={"error": str(e)})
        return None, None
    user_id = payload.get("sub")
    if not user_id:
        return None, None
    request.state.access_token_exp = payload.get("exp")
    return user_id, payload


async def _load_user(user_id: str, payload: dict, db: AsyncSession) -> Optional[User]:
    try:
        result = await db.execute(select(User).where(User.id == int(user_id)))
        user = result.scalar_one_or_none()
    except Exception as e:
        logger.warning("User lookup failed", extra={"user_id": user_id, "error": str(e)})
        return None
    if not user:
        return None
    if payload.get("tv", 0) != user.token_version:
        return None
    return user


async def get_current_user_optional(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Optional[User]:
    user_id, payload = _authenticate(request)
    if not user_id:
        return None
    user = await _load_user(user_id, payload, db)
    if user is not None:
        request.state.user_id = user.id
    return user


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    user_id, payload = _authenticate(request)
    if not user_id:
        if _extract_token(request):
            raise invalid_token("Invalid or expired token.")
        raise missing_auth()
    user = await _load_user(user_id, payload, db)
    if not user:
        raise invalid_token("Session invalidated. Please log in again.")
    request.state.user_id = user.id
    return user


def require_verified(user: User = Depends(get_current_user)) -> User:
    if user.verified_at is None:
        raise forbidden("Please verify your email before taking this action.")
    return user


def require_billing_user(user: User = Depends(get_current_user)) -> User:
    """Conditional verified-gate for billing endpoints.

    - When BILLING_REQUIRE_VERIFIED=False (chassis default): any authed user
      can call billing mutation endpoints. Permissive posture matches
      industry-standard SaaS UX (let people pay without friction).
    - When BILLING_REQUIRE_VERIFIED=True: unverified users get 403 FORBIDDEN —
      restores the legacy chassis behavior for adopters with stricter posture
      needs. Error format is identical to require_verified so existing frontend
      error-mapping (TopupForm + SubscribeForm 403 handlers) continues to work.
    """
    if settings.BILLING_REQUIRE_VERIFIED and user.verified_at is None:
        raise forbidden("Please verify your email before taking this action.")
    return user


def require_not_locked(user: User = Depends(get_current_user)) -> User:
    """Block unverified users past the 7-day grace window from most endpoints.

    Reachable routes for locked-out users are enforced at the route level
    (verify-email, resend-verification, change-email, me, logout) — they
    simply don't apply this dependency.
    """
    if user.verified_at is not None:
        return user
    if user.created_at is None:
        return user
    created_naive = user.created_at
    if created_naive.tzinfo is not None:
        created_naive = created_naive.astimezone(timezone.utc).replace(tzinfo=None)
    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    if now_naive - created_naive >= timedelta(days=UNVERIFIED_LOCK_DAYS):
        raise forbidden("Account locked. Please verify your email to unlock.")
    return user
