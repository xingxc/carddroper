"""Async billing primitives.

All take an AsyncSession; none commit — the caller's transaction commits.
Project layers call these functions; they never write to balance_ledger directly.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing.exceptions import InsufficientBalanceError
from app.billing.reason import Reason
from app.billing.stripe_client import init_stripe, stripe
from app.models.balance_ledger import BalanceLedger
from app.models.user import User


async def create_customer(user: User, db: AsyncSession) -> str:
    """Create a Stripe Customer for the user. Returns the stripe_customer_id string.

    Uses idempotency_key="register:{user.id}" so retried registrations don't
    create duplicate Stripe Customers.
    """
    init_stripe()
    customer = stripe.Customer.create(
        email=user.email,
        name=user.full_name,
        metadata={"user_id": str(user.id)},
        idempotency_key=f"register:{user.id}",
    )
    return customer.id


async def _sum_ledger(user_id: int, db: AsyncSession) -> int:
    """Sum all balance_ledger entries for the user. Returns int (may be negative if ledger is corrupted)."""
    result = await db.execute(
        select(func.coalesce(func.sum(BalanceLedger.amount_micros), 0)).where(
            BalanceLedger.user_id == user_id
        )
    )
    return int(result.scalar_one())


async def get_balance_micros(user_id: int, db: AsyncSession) -> int:
    """Current balance in micros. Sums balance_ledger. Returns int >= 0."""
    return max(0, await _sum_ledger(user_id, db))


async def grant(
    user_id: int,
    amount_micros: int,
    reason: Reason,
    db: AsyncSession,
    *,
    stripe_event_id: str | None = None,
) -> None:
    """Positive ledger entry. reason must be a Reason enum value. Does not commit."""
    db.add(
        BalanceLedger(
            user_id=user_id,
            amount_micros=amount_micros,
            reason=reason.value,
            stripe_event_id=stripe_event_id,
        )
    )


async def debit(
    user_id: int,
    amount_micros: int,
    ref_type: str,
    ref_id: str,
    db: AsyncSession,
) -> None:
    """Negative ledger entry. Raises InsufficientBalanceError if balance insufficient.

    Acquires a row-level lock on the user row before reading the balance to
    serialize concurrent debits per user. Two simultaneous debits will block
    each other at the SELECT ... FOR UPDATE; the second sees the first's ledger
    entry and fails cleanly with InsufficientBalanceError if balance is now too low.

    Must be called inside the caller's active DB transaction.
    """
    # Acquire row-level lock before reading balance — serializes concurrent debits.
    await db.execute(select(User.id).where(User.id == user_id).with_for_update())

    # Read balance within the same transaction (sees all committed + in-txn rows).
    balance = max(0, await _sum_ledger(user_id, db))

    if balance < amount_micros:
        raise InsufficientBalanceError(user_id, balance, amount_micros)

    db.add(
        BalanceLedger(
            user_id=user_id,
            amount_micros=-amount_micros,
            reason=Reason.DEBIT.value,
            ref_type=ref_type,
            ref_id=ref_id,
        )
    )
