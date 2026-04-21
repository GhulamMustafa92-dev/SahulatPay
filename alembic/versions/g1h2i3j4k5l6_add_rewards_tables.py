"""add_rewards_tables

Revision ID: g1h2i3j4k5l6
Revises: b9c0d1e2f3a4
Create Date: 2026-04-21 11:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "g1h2i3j4k5l6"
down_revision: Union[str, None] = "b9c0d1e2f3a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(name: str) -> bool:
    bind = op.get_bind()
    return sa.inspect(bind).has_table(name)


def upgrade() -> None:
    if not _table_exists("rewards"):
        op.create_table(
            "rewards",
            sa.Column("id",           postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
            sa.Column("user_id",      postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("total_earned", sa.Numeric(10, 2), server_default="0.00", nullable=True),
            sa.Column("pending",      sa.Numeric(10, 2), server_default="0.00", nullable=True),
            sa.Column("claimed",      sa.Numeric(10, 2), server_default="0.00", nullable=True),
            sa.Column("created_at",   sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
            sa.Column("updated_at",   sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id"),
        )

    if not _table_exists("offer_templates"):
        op.create_table(
            "offer_templates",
            sa.Column("id",            postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
            sa.Column("title",         sa.String(255), nullable=False),
            sa.Column("description",   sa.String(),    nullable=True),
            sa.Column("category",      sa.String(50),  nullable=False),
            sa.Column("target_amount", sa.Numeric(10, 2), nullable=False),
            sa.Column("reward_amount", sa.Numeric(10, 2), nullable=False),
            sa.Column("duration_days", sa.Integer(),   server_default="30", nullable=True),
            sa.Column("is_active",     sa.Boolean(),   server_default="true", nullable=True),
            sa.Column("created_by",    postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("created_at",    sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )

    if not _table_exists("reward_offers"):
        op.create_table(
            "reward_offers",
            sa.Column("id",            postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
            sa.Column("user_id",       postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("template_id",   postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("title",         sa.String(255), nullable=False),
            sa.Column("category",      sa.String(50),  nullable=False),
            sa.Column("target_amount", sa.Numeric(10, 2), nullable=False),
            sa.Column("current_spent", sa.Numeric(10, 2), server_default="0.00", nullable=True),
            sa.Column("reward_amount", sa.Numeric(10, 2), nullable=False),
            sa.Column("status",        sa.String(20),  server_default="active", nullable=True),
            sa.Column("expires_at",    sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at",  sa.DateTime(timezone=True), nullable=True),
            sa.Column("claimed_at",    sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at",    sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
            sa.ForeignKeyConstraint(["template_id"], ["offer_templates.id"]),
            sa.ForeignKeyConstraint(["user_id"],     ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )

    if not _table_exists("reward_transactions"):
        op.create_table(
            "reward_transactions",
            sa.Column("id",             postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
            sa.Column("user_id",        postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("transaction_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("offer_id",       postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("type",           sa.String(30),  nullable=False),
            sa.Column("amount",         sa.Numeric(10, 2), nullable=False),
            sa.Column("created_at",     sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
            sa.ForeignKeyConstraint(["offer_id"],       ["reward_offers.id"]),
            sa.ForeignKeyConstraint(["transaction_id"], ["transactions.id"]),
            sa.ForeignKeyConstraint(["user_id"],        ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )


def downgrade() -> None:
    if _table_exists("reward_transactions"):
        op.drop_table("reward_transactions")
    if _table_exists("reward_offers"):
        op.drop_table("reward_offers")
    if _table_exists("offer_templates"):
        op.drop_table("offer_templates")
    if _table_exists("rewards"):
        op.drop_table("rewards")
