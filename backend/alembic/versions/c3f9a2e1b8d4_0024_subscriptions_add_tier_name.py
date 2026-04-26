"""0024 subscriptions add tier_name column

Adds tier_name VARCHAR(64) NOT NULL to the subscriptions table.
This column mirrors Price.metadata.tier_name at subscribe time and is
updated on customer.subscription.updated when the user changes plans.
Storing it avoids a Stripe API call on every GET /billing/subscription.

Audit finding 2026-04-26: payments.md schema lacked tier_name but the
chassis response shape needs it. Pragmatic fix: store at subscribe time +
sync on update.

Revision ID: c3f9a2e1b8d4
Revises: 84b050bfae31
Create Date: 2026-04-26

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "c3f9a2e1b8d4"
down_revision: Union[str, None] = "84b050bfae31"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add tier_name with a server_default of '' so existing rows (if any) get a
    # non-null value, then remove the default so future inserts must supply it.
    op.add_column(
        "subscriptions",
        sa.Column("tier_name", sa.String(length=64), nullable=False, server_default=""),
    )
    op.alter_column("subscriptions", "tier_name", server_default=None)


def downgrade() -> None:
    op.drop_column("subscriptions", "tier_name")
