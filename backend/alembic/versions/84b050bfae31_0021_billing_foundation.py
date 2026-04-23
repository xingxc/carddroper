"""0021 billing foundation

Adds subscriptions, balance_ledger, and stripe_events tables.
users.stripe_customer_id is already present from the initial migration.

Revision ID: 84b050bfae31
Revises: ee2ded47d8da
Create Date: 2026-04-23

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "84b050bfae31"
down_revision: Union[str, None] = "ee2ded47d8da"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # subscriptions — one per user, optional (user may not subscribe)
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("stripe_subscription_id", sa.String(length=64), nullable=False),
        sa.Column("stripe_price_id", sa.String(length=64), nullable=False),
        sa.Column("tier_key", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("grant_micros", sa.BigInteger(), nullable=False),
        sa.Column("current_period_start", sa.DateTime(), nullable=True),
        sa.Column("current_period_end", sa.DateTime(), nullable=True),
        sa.Column(
            "cancel_at_period_end",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stripe_subscription_id"),
        sa.UniqueConstraint("user_id"),
    )

    # balance_ledger — append-only; balance = SUM(amount_micros) WHERE user_id = ?
    op.create_table(
        "balance_ledger",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("amount_micros", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("stripe_event_id", sa.String(length=64), nullable=True),
        sa.Column("ref_type", sa.String(length=32), nullable=True),
        sa.Column("ref_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_balance_ledger_user_id_created_at",
        "balance_ledger",
        ["user_id", "created_at"],
        unique=False,
    )
    # Partial unique index — the webhook idempotency guarantee.
    # Prevents two balance_ledger rows for the same Stripe event id.
    op.create_index(
        "ix_balance_ledger_stripe_event_id",
        "balance_ledger",
        ["stripe_event_id"],
        unique=True,
        postgresql_where=sa.text("stripe_event_id IS NOT NULL"),
    )

    # stripe_events — records every processed webhook event id
    op.create_table(
        "stripe_events",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column(
            "processed_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("stripe_events")
    op.drop_index("ix_balance_ledger_stripe_event_id", table_name="balance_ledger")
    op.drop_index("ix_balance_ledger_user_id_created_at", table_name="balance_ledger")
    op.drop_table("balance_ledger")
    op.drop_table("subscriptions")
