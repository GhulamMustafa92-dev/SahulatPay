"""Add missing review_type column to kyc_review_requests.

revision: i3j4k5l6m7n8
Revises: 7247335907fe
Create Date: 2026-04-23 22:50:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "i3j4k5l6m7n8"
down_revision = "7247335907fe"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "kyc_review_requests",
        sa.Column(
            "review_type",
            sa.String(20),
            nullable=False,
            server_default="cnic",
        ),
    )


def downgrade():
    op.drop_column("kyc_review_requests", "review_type")
