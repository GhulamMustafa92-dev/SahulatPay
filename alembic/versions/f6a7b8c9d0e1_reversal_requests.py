"""reversal_requests

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-04-19 05:00:00.000000

Creates:
  - reversal_reason_code_enum  (PG enum)
  - reversal_request_status_enum (PG enum)
  - reversal_requests table — Maker-Checker flow for admin reversals
  - dismissed_disputes_count column on users
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "CREATE TYPE reversal_reason_code_enum AS ENUM "
        "('fraud_confirmed','erroneous_transfer','dispute_resolved')"
    )
    op.execute(
        "CREATE TYPE reversal_request_status_enum AS ENUM "
        "('pending','approved','rejected')"
    )
    op.create_table(
        "reversal_requests",
        sa.Column("id",           sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("txn_id",       sa.UUID(), nullable=False),
        sa.Column("requested_by", sa.UUID(), nullable=False),
        sa.Column("reason_code",
                  postgresql.ENUM("fraud_confirmed", "erroneous_transfer", "dispute_resolved",
                                  name="reversal_reason_code_enum", create_type=False),
                  nullable=False),
        sa.Column("reason_detail", sa.Text(), nullable=True),
        sa.Column("status",
                  postgresql.ENUM("pending", "approved", "rejected",
                                  name="reversal_request_status_enum", create_type=False),
                  nullable=False, server_default="pending"),
        sa.Column("reviewed_by",  sa.UUID(), nullable=True),
        sa.Column("reviewed_at",  sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_note",  sa.Text(), nullable=True),
        sa.Column("created_at",   sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["txn_id"],       ["transactions.id"]),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["reviewed_by"],  ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_reversal_req_txn",    "reversal_requests", ["txn_id"])
    op.create_index("idx_reversal_req_status", "reversal_requests", ["status"])

    # dismissed_disputes_count on users for Step 10
    op.add_column("users",
        sa.Column("dismissed_disputes_count", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("users", "dismissed_disputes_count")
    op.drop_index("idx_reversal_req_status", table_name="reversal_requests")
    op.drop_index("idx_reversal_req_txn",    table_name="reversal_requests")
    op.drop_table("reversal_requests")
    op.execute("DROP TYPE IF EXISTS reversal_request_status_enum")
    op.execute("DROP TYPE IF EXISTS reversal_reason_code_enum")
