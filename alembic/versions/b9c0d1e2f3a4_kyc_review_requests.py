"""Add kyc_review_requests table for admin-approval workflow.

Revision ID: b9c0d1e2f3a4
Revises: a8b9c0d1e2f3
Create Date: 2026-04-21 01:50:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "b9c0d1e2f3a4"
down_revision = "a8b9c0d1e2f3"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "kyc_review_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("front_doc_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("documents.id")),
        sa.Column("back_doc_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("documents.id")),
        sa.Column("extracted_cnic", sa.String(20)),
        sa.Column("extracted_name", sa.String(255)),
        sa.Column("extracted_dob", sa.String(30)),
        sa.Column("extracted_father", sa.String(255)),
        sa.Column("extracted_address", sa.Text),
        sa.Column("cnic_masked", sa.String(25)),
        sa.Column("cnic_encrypted", sa.Text),
        sa.Column("status", sa.String(20), server_default="pending"),
        sa.Column("rejection_reason", sa.Text),
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("submitted_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("idx_kyc_review_status", "kyc_review_requests", ["status"])
    op.create_index("idx_kyc_review_user", "kyc_review_requests", ["user_id"])


def downgrade():
    op.drop_index("idx_kyc_review_user")
    op.drop_index("idx_kyc_review_status")
    op.drop_table("kyc_review_requests")
