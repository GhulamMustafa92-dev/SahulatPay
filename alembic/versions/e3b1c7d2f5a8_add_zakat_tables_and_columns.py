"""add_zakat_tables_and_columns

Revision ID: e3b1c7d2f5a8
Revises: d4e1b9c2f8a6
Create Date: 2026-04-18 00:00:00.000000

Adds:
  - user_zakat_settings  (new table)
  - wealth_profiles      (new table)
  - hawl_tracking        (new table)
  - metal_rate_cache     (new table)
  - zakat_calculations   (14 new nullable columns for enhanced calculation architecture)
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e3b1c7d2f5a8"
down_revision: Union[str, None] = "d4e1b9c2f8a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── user_zakat_settings ───────────────────────────────────────────────────
    op.create_table(
        "user_zakat_settings",
        sa.Column("id",               sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id",          sa.UUID(), nullable=False),
        sa.Column("madhab",           sa.String(20), server_default="hanafi",       nullable=False),
        sa.Column("nisab_preference", sa.String(20), server_default="lower_of_two", nullable=False),
        sa.Column("created_at",       sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at",       sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_zakat_settings_user"),
    )
    op.create_index("idx_user_zakat_settings_user", "user_zakat_settings", ["user_id"], unique=True)

    # ── wealth_profiles ───────────────────────────────────────────────────────
    op.create_table(
        "wealth_profiles",
        sa.Column("id",                        sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id",                   sa.UUID(), nullable=False),
        sa.Column("external_banks_pkr",        sa.Numeric(12, 2), server_default="0.00", nullable=True),
        sa.Column("other_wallets_pkr",         sa.Numeric(12, 2), server_default="0.00", nullable=True),
        sa.Column("physical_gold_grams",       sa.Numeric(8,  3), server_default="0.000", nullable=True),
        sa.Column("physical_silver_grams",     sa.Numeric(8,  3), server_default="0.000", nullable=True),
        sa.Column("receivables_pkr",           sa.Numeric(12, 2), server_default="0.00", nullable=True),
        sa.Column("bad_debts_pkr",             sa.Numeric(12, 2), server_default="0.00", nullable=True),
        sa.Column("business_tradeable_pkr",    sa.Numeric(12, 2), server_default="0.00", nullable=True),
        sa.Column("business_cash_pkr",         sa.Numeric(12, 2), server_default="0.00", nullable=True),
        sa.Column("business_fixed_assets_pkr", sa.Numeric(12, 2), server_default="0.00", nullable=True),
        sa.Column("personal_loans_pkr",        sa.Numeric(12, 2), server_default="0.00", nullable=True),
        sa.Column("credit_card_pkr",           sa.Numeric(12, 2), server_default="0.00", nullable=True),
        sa.Column("car_loan_installments_pkr", sa.Numeric(12, 2), server_default="0.00", nullable=True),
        sa.Column("home_loan_pkr",             sa.Numeric(12, 2), server_default="0.00", nullable=True),
        sa.Column("home_loan_include",         sa.Boolean(),       server_default="false", nullable=True),
        sa.Column("other_liabilities_pkr",     sa.Numeric(12, 2), server_default="0.00", nullable=True),
        sa.Column("last_verified_at",          sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at",                sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_wealth_profile_user"),
    )

    # ── hawl_tracking ─────────────────────────────────────────────────────────
    op.create_table(
        "hawl_tracking",
        sa.Column("id",                    sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id",               sa.UUID(), nullable=False),
        sa.Column("nisab_crossed_at",      sa.DateTime(timezone=True), nullable=True),
        sa.Column("zakat_due_date",        sa.DateTime(timezone=True), nullable=True),
        sa.Column("hawl_active",           sa.Boolean(), server_default="false", nullable=True),
        sa.Column("last_reminder_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("hawl_reset_count",      sa.Integer(), server_default="0", nullable=True),
        sa.Column("hawl_reset_at",         sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_hawl_user"),
    )

    # ── metal_rate_cache ──────────────────────────────────────────────────────
    op.create_table(
        "metal_rate_cache",
        sa.Column("id",              sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("gold_usd_oz",     sa.Numeric(10, 4), nullable=False),
        sa.Column("silver_usd_oz",   sa.Numeric(10, 4), nullable=False),
        sa.Column("usd_to_pkr",      sa.Numeric(10, 4), nullable=False),
        sa.Column("gold_pkr_gram",   sa.Numeric(10, 4), nullable=False),
        sa.Column("silver_pkr_gram", sa.Numeric(10, 4), nullable=False),
        sa.Column("nisab_gold_pkr",  sa.Numeric(12, 2), nullable=False),
        sa.Column("nisab_silver_pkr",sa.Numeric(12, 2), nullable=False),
        sa.Column("source",          sa.String(100), server_default="metals.live + er-api.com", nullable=True),
        sa.Column("fetched_at",      sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    # ── New columns on zakat_calculations ─────────────────────────────────────
    op.add_column("zakat_calculations", sa.Column("madhab_used",             sa.String(20),    nullable=True))
    op.add_column("zakat_calculations", sa.Column("nisab_preference_used",   sa.String(20),    nullable=True))
    op.add_column("zakat_calculations", sa.Column("business_tradeable_pkr",  sa.Numeric(12, 2), server_default="0.00", nullable=True))
    op.add_column("zakat_calculations", sa.Column("business_cash_pkr",       sa.Numeric(12, 2), server_default="0.00", nullable=True))
    op.add_column("zakat_calculations", sa.Column("bad_debts_pkr",           sa.Numeric(12, 2), server_default="0.00", nullable=True))
    op.add_column("zakat_calculations", sa.Column("personal_loans_pkr",      sa.Numeric(12, 2), server_default="0.00", nullable=True))
    op.add_column("zakat_calculations", sa.Column("credit_card_pkr",         sa.Numeric(12, 2), server_default="0.00", nullable=True))
    op.add_column("zakat_calculations", sa.Column("car_loan_installments",   sa.Numeric(12, 2), server_default="0.00", nullable=True))
    op.add_column("zakat_calculations", sa.Column("home_loan_pkr",           sa.Numeric(12, 2), server_default="0.00", nullable=True))
    op.add_column("zakat_calculations", sa.Column("home_loan_included",      sa.Boolean(),       server_default="false", nullable=True))
    op.add_column("zakat_calculations", sa.Column("other_liabilities_pkr",   sa.Numeric(12, 2), server_default="0.00", nullable=True))
    op.add_column("zakat_calculations", sa.Column("total_liabilities_pkr",   sa.Numeric(12, 2), server_default="0.00", nullable=True))
    op.add_column("zakat_calculations", sa.Column("net_zakatable_pkr",       sa.Numeric(12, 2), nullable=True))
    op.add_column("zakat_calculations", sa.Column("wallet_balance_snapshot", sa.Numeric(12, 2), nullable=True))


def downgrade() -> None:
    for col in (
        "wallet_balance_snapshot", "net_zakatable_pkr", "total_liabilities_pkr",
        "other_liabilities_pkr", "home_loan_included", "home_loan_pkr",
        "car_loan_installments", "credit_card_pkr", "personal_loans_pkr",
        "bad_debts_pkr", "business_cash_pkr", "business_tradeable_pkr",
        "nisab_preference_used", "madhab_used",
    ):
        op.drop_column("zakat_calculations", col)

    op.drop_table("metal_rate_cache")
    op.drop_table("hawl_tracking")
    op.drop_table("wealth_profiles")
    op.drop_table("user_zakat_settings")
