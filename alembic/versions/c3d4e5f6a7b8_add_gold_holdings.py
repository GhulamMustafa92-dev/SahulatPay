"""add_gold_holdings

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-04-19 02:00:00.000000

Creates gold_holdings table — tracks per-user gold/silver ownership with
avg cost basis and total invested for P&L calculation.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "gold_holdings",
        sa.Column("user_id",             sa.UUID(), nullable=False),
        sa.Column("gold_grams",          sa.Numeric(12, 4), nullable=False, server_default="0.0000"),
        sa.Column("silver_grams",        sa.Numeric(12, 4), nullable=False, server_default="0.0000"),
        sa.Column("avg_gold_rate_pkr",   sa.Numeric(12, 4), nullable=True),
        sa.Column("avg_silver_rate_pkr", sa.Numeric(12, 4), nullable=True),
        sa.Column("total_invested_pkr",  sa.Numeric(14, 2), nullable=False, server_default="0.00"),
        sa.Column("last_updated",        sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.CheckConstraint("gold_grams >= 0",   name="ck_gold_grams_non_negative"),
        sa.CheckConstraint("silver_grams >= 0", name="ck_silver_grams_non_negative"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("gold_holdings")
