"""add_platform_accounts

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-04-19 01:00:00.000000

Creates:
  - platform_account_type_enum  (PG enum)
  - ledger_direction_enum       (PG enum)
  - platform_accounts           (one row per pool, seeded with 6 rows)
  - platform_ledger_entries     (immutable double-entry log)
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ACCOUNT_TYPES = (
    "savings_pool", "investment_pool", "insurance_pool",
    "gold_platform", "main_float", "platform_revenue",
)


def upgrade() -> None:
    # ── Enums ─────────────────────────────────────────────────────────────────
    op.execute(
        "CREATE TYPE platform_account_type_enum AS ENUM "
        "('savings_pool','investment_pool','insurance_pool',"
        "'gold_platform','main_float','platform_revenue')"
    )
    op.execute(
        "CREATE TYPE ledger_direction_enum AS ENUM ('credit','debit')"
    )

    # ── platform_accounts ─────────────────────────────────────────────────────
    op.create_table(
        "platform_accounts",
        sa.Column("id",         sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("type",       postgresql.ENUM(*ACCOUNT_TYPES,
                                    name="platform_account_type_enum", create_type=False),
                                nullable=False),
        sa.Column("balance",    sa.Numeric(18, 2), nullable=False, server_default="0.00"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.CheckConstraint("balance >= 0.00", name="ck_platform_balance_non_negative"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("type", name="uq_platform_account_type"),
    )

    # ── Seed one row per pool type ────────────────────────────────────────────
    op.execute(
        "INSERT INTO platform_accounts (type, balance) VALUES "
        "('savings_pool',    0.00),"
        "('investment_pool', 0.00),"
        "('insurance_pool',  0.00),"
        "('gold_platform',   0.00),"
        "('main_float',      0.00),"
        "('platform_revenue',0.00)"
    )

    # ── platform_ledger_entries ───────────────────────────────────────────────
    op.create_table(
        "platform_ledger_entries",
        sa.Column("id",              sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("account_id",      sa.UUID(), nullable=False),
        sa.Column("direction",       postgresql.ENUM("credit", "debit",
                                        name="ledger_direction_enum", create_type=False),
                                    nullable=False),
        sa.Column("amount",          sa.Numeric(18, 2), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False,
                                    server_default=sa.text("gen_random_uuid()::text")),
        sa.Column("reference",       sa.String(100), nullable=True),
        sa.Column("transaction_id",  sa.UUID(), nullable=True),
        sa.Column("user_id",         sa.UUID(), nullable=True),
        sa.Column("note",            sa.String(255), nullable=True),
        sa.Column("created_at",      sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["account_id"],     ["platform_accounts.id"]),
        sa.ForeignKeyConstraint(["transaction_id"], ["transactions.id"]),
        sa.ForeignKeyConstraint(["user_id"],        ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_ledger_idempotency_key"),
    )
    op.create_index("idx_ledger_account_id",    "platform_ledger_entries", ["account_id"])
    op.create_index("idx_ledger_user_id",       "platform_ledger_entries", ["user_id"])
    op.create_index("idx_ledger_transaction_id","platform_ledger_entries", ["transaction_id"])
    op.create_index("idx_ledger_created_at",    "platform_ledger_entries", ["created_at"])


def downgrade() -> None:
    op.drop_index("idx_ledger_created_at",     table_name="platform_ledger_entries")
    op.drop_index("idx_ledger_transaction_id", table_name="platform_ledger_entries")
    op.drop_index("idx_ledger_user_id",        table_name="platform_ledger_entries")
    op.drop_index("idx_ledger_account_id",     table_name="platform_ledger_entries")
    op.drop_table("platform_ledger_entries")
    op.drop_table("platform_accounts")
    op.execute("DROP TYPE IF EXISTS ledger_direction_enum")
    op.execute("DROP TYPE IF EXISTS platform_account_type_enum")
