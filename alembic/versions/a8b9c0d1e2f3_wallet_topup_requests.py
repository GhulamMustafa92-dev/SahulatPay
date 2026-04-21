"""wallet_topup_requests

Revision ID: a8b9c0d1e2f3
Revises: f6a7b8c9d0e1
Create Date: 2026-04-20 14:00:00.000000

Creates:
  - wallet_topup_requests table for pull-payment/top-up flows
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "a8b9c0d1e2f3"
down_revision = "718158a7b79b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wallet_topup_requests",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("requester_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("recipient_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("wallet_type", sa.String(50), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), server_default="pending"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_topup_recipient", "wallet_topup_requests", ["recipient_id", "status"])
    op.create_index("idx_topup_requester", "wallet_topup_requests", ["requester_id", "status"])


def downgrade() -> None:
    op.drop_index("idx_topup_recipient", table_name="wallet_topup_requests")
    op.drop_index("idx_topup_requester", table_name="wallet_topup_requests")
    op.drop_table("wallet_topup_requests")
