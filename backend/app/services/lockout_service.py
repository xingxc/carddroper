from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.login_attempt import LoginAttempt


def _utc_naive_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def is_locked_out(email: str, db: AsyncSession) -> bool:
    """True if the email has too many recent failures to attempt another login."""
    window_start = _utc_naive_now() - timedelta(minutes=settings.LOCKOUT_WINDOW_MINUTES)
    result = await db.execute(
        select(func.count(LoginAttempt.id)).where(
            LoginAttempt.email == email,
            LoginAttempt.attempted_at >= window_start,
            LoginAttempt.success.is_(False),
        )
    )
    failures = result.scalar_one()
    return failures >= settings.LOCKOUT_THRESHOLD


async def record_attempt(email: str, ip: str | None, success: bool, db: AsyncSession) -> None:
    db.add(LoginAttempt(email=email, ip=ip, success=success, attempted_at=_utc_naive_now()))
    await db.flush()


async def record_attempt_isolated(email: str, ip: str | None, success: bool) -> None:
    """Record a login attempt in its own transaction.

    Used on the failed-login path so the row survives even when the request
    transaction is rolled back by the 401 response.
    """
    async with AsyncSessionLocal() as session:
        session.add(
            LoginAttempt(email=email, ip=ip, success=success, attempted_at=_utc_naive_now())
        )
        await session.commit()


async def clear_failures_for(email: str, db: AsyncSession) -> None:
    """Wipe the failure counter after a successful login."""
    window_start = _utc_naive_now() - timedelta(minutes=settings.LOCKOUT_WINDOW_MINUTES)
    await db.execute(
        delete(LoginAttempt).where(
            LoginAttempt.email == email,
            LoginAttempt.attempted_at >= window_start,
            LoginAttempt.success.is_(False),
        )
    )
    await db.flush()
