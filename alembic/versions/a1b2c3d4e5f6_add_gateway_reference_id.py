"""add_gateway_reference_id

Revision ID: a1b2c3d4e5f6
Revises: f9a2c3b1d4e7
Create Date: 2026-04-19 00:00:00.000000

Adds gateway_reference_id to transactions table:
  - nullable String(36) — UUID format
  - unique, indexed
  - server_default: gen_random_uuid()::text
  - Backfills existing rows with a unique UUID each
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "f9a2c3b1d4e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "transactions",
        sa.Column(
            "gateway_reference_id",
            sa.String(36),
            nullable=True,
            server_default=sa.text("gen_random_uuid()::text"),
        ),
    )
    # Backfill existing rows — each gets its own unique UUID
    op.execute(
        "UPDATE transactions SET gateway_reference_id = gen_random_uuid()::text "
        "WHERE gateway_reference_id IS NULL"
    )
    # Now add unique constraint and index
    op.create_index(
        "idx_transactions_gateway_ref",
        "transactions",
        ["gateway_reference_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("idx_transactions_gateway_ref", table_name="transactions")
    op.drop_column("transactions", "gateway_reference_id")
