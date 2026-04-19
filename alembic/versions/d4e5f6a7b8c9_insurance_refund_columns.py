"""insurance_refund_columns

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-04-19 03:00:00.000000

Adds to insurance_policies:
  - premium_paid   (Numeric 10,2) — actual amount collected (used for refund)
  - policy_start   (DateTime)     — policy start timestamp
  - policy_end     (DateTime)     — policy end/expiry timestamp
  - refund_paid    (Numeric 10,2) — how much was refunded on cancellation

Backfills premium_paid = premium for existing rows.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("insurance_policies",
        sa.Column("premium_paid", sa.Numeric(10, 2), nullable=True))
    op.add_column("insurance_policies",
        sa.Column("policy_start", sa.DateTime(timezone=True), nullable=True))
    op.add_column("insurance_policies",
        sa.Column("policy_end", sa.DateTime(timezone=True), nullable=True))
    op.add_column("insurance_policies",
        sa.Column("refund_paid", sa.Numeric(10, 2), nullable=True))

    # Backfill existing rows
    op.execute("UPDATE insurance_policies SET premium_paid = premium WHERE premium_paid IS NULL")
    op.execute("UPDATE insurance_policies SET policy_start = activated_at WHERE policy_start IS NULL")
    op.execute("UPDATE insurance_policies SET policy_end = expires_at WHERE policy_end IS NULL")


def downgrade() -> None:
    op.drop_column("insurance_policies", "refund_paid")
    op.drop_column("insurance_policies", "policy_end")
    op.drop_column("insurance_policies", "policy_start")
    op.drop_column("insurance_policies", "premium_paid")
