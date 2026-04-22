"""Add missing columns to kyc_review_requests: review_type, selfie_doc_id, face_confidence.

These columns were added to the KycReviewRequest model for liveness-review support
but were never included in the original table-creation migration (b9c0d1e2f3a4).

Revision ID: h2i3j4k5l6m7
Revises: g1h2i3j4k5l6
Create Date: 2026-04-22 11:30:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "h2i3j4k5l6m7"
down_revision = "g1h2i3j4k5l6"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "kyc_review_requests",
        sa.Column(
            "selfie_doc_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id"),
            nullable=True,
        ),
    )
    op.add_column(
        "kyc_review_requests",
        sa.Column("face_confidence", sa.Float(), nullable=True),
    )


def downgrade():
    op.drop_column("kyc_review_requests", "face_confidence")
    op.drop_column("kyc_review_requests", "selfie_doc_id")
