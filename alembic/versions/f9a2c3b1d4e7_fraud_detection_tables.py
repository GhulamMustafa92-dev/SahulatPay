"""fraud_detection_tables

Revision ID: f9a2c3b1d4e7
Revises: a7745e2c81ea
Create Date: 2026-04-30 00:00:00.000000

Adds:
  - user_behaviour_profiles  (new table)
  - wallet_debts             (new table)
  - transaction_disputes     (new table)
  - str_reports              (new table)
  - transactions             (7 new nullable columns)
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f9a2c3b1d4e7"
down_revision: Union[str, None] = "e3b1c7d2f5a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── New enum types ────────────────────────────────────────────────────────
    op.execute("CREATE TYPE dispute_type_enum   AS ENUM ('unauthorized','wrong_amount','wrong_recipient','other')")
    op.execute("CREATE TYPE dispute_status_enum AS ENUM ('open','under_review','resolved','dismissed')")
    op.execute("CREATE TYPE str_report_type_enum AS ENUM ('STR','CTR')")
    op.execute("CREATE TYPE str_status_enum      AS ENUM ('draft','reviewed','submitted')")

    # ── user_behaviour_profiles ───────────────────────────────────────────────
    op.create_table(
        "user_behaviour_profiles",
        sa.Column("user_id",                 sa.UUID(),        nullable=False),
        sa.Column("avg_transaction_pkr",     sa.Numeric(12, 2), server_default="0.00", nullable=True),
        sa.Column("max_transaction_pkr",     sa.Numeric(12, 2), server_default="0.00", nullable=True),
        sa.Column("typical_hour_start",      sa.Integer(),      nullable=True),
        sa.Column("typical_hour_end",        sa.Integer(),      nullable=True),
        sa.Column("known_recipients_count",  sa.Integer(),      server_default="0", nullable=True),
        sa.Column("total_transaction_count", sa.Integer(),      server_default="0", nullable=True),
        sa.Column("last_calculated_at",      sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )

    # ── wallet_debts ──────────────────────────────────────────────────────────
    op.create_table(
        "wallet_debts",
        sa.Column("id",                    sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id",               sa.UUID(), nullable=False),
        sa.Column("amount_pkr",            sa.Numeric(12, 2), nullable=False),
        sa.Column("reason",                sa.String(255), nullable=False),
        sa.Column("source_transaction_id", sa.UUID(), nullable=True),
        sa.Column("due_at",                sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_settled",            sa.Boolean(), server_default="false", nullable=True),
        sa.Column("settled_at",            sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at",            sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["user_id"],               ["users.id"],        ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_transaction_id"], ["transactions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # ── transaction_disputes ──────────────────────────────────────────────────
    op.create_table(
        "transaction_disputes",
        sa.Column("id",              sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id",         sa.UUID(), nullable=False),
        sa.Column("transaction_id",  sa.UUID(), nullable=False),
        sa.Column("dispute_type",    postgresql.ENUM("unauthorized","wrong_amount","wrong_recipient","other", name="dispute_type_enum", create_type=False), nullable=False),
        sa.Column("reason",          sa.Text(), nullable=False),
        sa.Column("status",          postgresql.ENUM("open","under_review","resolved","dismissed", name="dispute_status_enum", create_type=False), server_default="open", nullable=True),
        sa.Column("created_at",      sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("resolved_at",     sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by",     sa.UUID(), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"],        ["users.id"],        ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["transaction_id"], ["transactions.id"]),
        sa.ForeignKeyConstraint(["resolved_by"],    ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # ── str_reports ───────────────────────────────────────────────────────────
    op.create_table(
        "str_reports",
        sa.Column("id",              sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id",         sa.UUID(), nullable=False),
        sa.Column("transaction_id",  sa.UUID(), nullable=True),
        sa.Column("report_type",     postgresql.ENUM("STR","CTR", name="str_report_type_enum", create_type=False), nullable=False),
        sa.Column("amount_pkr",      sa.Numeric(12, 2), nullable=False),
        sa.Column("ai_narrative",    sa.Text(), nullable=True),
        sa.Column("status",          postgresql.ENUM("draft","reviewed","submitted", name="str_status_enum", create_type=False), server_default="draft", nullable=True),
        sa.Column("generated_at",    sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("reviewed_by",     sa.UUID(), nullable=True),
        sa.Column("submitted_at",    sa.DateTime(timezone=True), nullable=True),
        sa.Column("submission_ref",  sa.String(100), nullable=True),
        sa.ForeignKeyConstraint(["user_id"],        ["users.id"],        ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["transaction_id"], ["transactions.id"]),
        sa.ForeignKeyConstraint(["reviewed_by"],    ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # ── New columns on transactions ───────────────────────────────────────────
    op.add_column("transactions", sa.Column("hold_reason",             sa.Text(),    nullable=True))
    op.add_column("transactions", sa.Column("held_at",                 sa.DateTime(timezone=True), nullable=True))
    op.add_column("transactions", sa.Column("hold_expires_at",         sa.DateTime(timezone=True), nullable=True))
    op.add_column("transactions", sa.Column("reviewed_by",             sa.UUID(),    nullable=True))
    op.add_column("transactions", sa.Column("deepseek_score",          sa.Integer(), nullable=True))
    op.add_column("transactions", sa.Column("deepseek_recommendation", sa.String(20), nullable=True))
    op.add_column("transactions", sa.Column("fraud_score",             sa.Integer(), server_default="0", nullable=True))

    op.create_foreign_key(
        "fk_transactions_reviewed_by",
        "transactions", "users",
        ["reviewed_by"], ["id"],
    )

    # ── Index for fraud feed queries ──────────────────────────────────────────
    op.create_index("idx_transactions_fraud_score",  "transactions", ["fraud_score"])
    op.create_index("idx_transactions_hold_expires", "transactions", ["hold_expires_at"])
    op.create_index("idx_transactions_status",       "transactions", ["status"])


def downgrade() -> None:
    op.drop_index("idx_transactions_status",       table_name="transactions")
    op.drop_index("idx_transactions_hold_expires", table_name="transactions")
    op.drop_index("idx_transactions_fraud_score",  table_name="transactions")
    op.drop_constraint("fk_transactions_reviewed_by", "transactions", type_="foreignkey")

    for col in ("fraud_score", "deepseek_recommendation", "deepseek_score",
                "reviewed_by", "hold_expires_at", "held_at", "hold_reason"):
        op.drop_column("transactions", col)

    op.drop_table("str_reports")
    op.drop_table("transaction_disputes")
    op.drop_table("wallet_debts")
    op.drop_table("user_behaviour_profiles")

    op.execute("DROP TYPE IF EXISTS str_status_enum")
    op.execute("DROP TYPE IF EXISTS str_report_type_enum")
    op.execute("DROP TYPE IF EXISTS dispute_status_enum")
    op.execute("DROP TYPE IF EXISTS dispute_type_enum")
