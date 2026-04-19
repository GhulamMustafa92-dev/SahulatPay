"""wallet_debt_lifecycle

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-04-19 04:00:00.000000

Adds to wallet_debts:
  - debt_stage_enum  (PG enum: soft | intercept | hard)
  - debt_stage       column with default 'soft'
  - last_notified_at column for notification cooldown tracking
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE TYPE debt_stage_enum AS ENUM ('soft','intercept','hard')")
    op.add_column(
        "wallet_debts",
        sa.Column(
            "debt_stage",
            postgresql.ENUM("soft", "intercept", "hard",
                            name="debt_stage_enum", create_type=False),
            nullable=False,
            server_default="soft",
        ),
    )
    op.add_column(
        "wallet_debts",
        sa.Column("last_notified_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("wallet_debts", "last_notified_at")
    op.drop_column("wallet_debts", "debt_stage")
    op.execute("DROP TYPE IF EXISTS debt_stage_enum")
