"""Platform double-entry models — PlatformAccount and PlatformLedgerEntry."""
from sqlalchemy import (
    Column, String, Numeric, DateTime, ForeignKey,
    CheckConstraint, Enum as SAEnum, text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from database import Base


ACCOUNT_TYPES = (
    "savings_pool",
    "investment_pool",
    "insurance_pool",
    "gold_platform",
    "main_float",
    "platform_revenue",
)


class PlatformAccount(Base):
    """One row per pool type. Balance is the authoritative total held in that pool."""
    __tablename__ = "platform_accounts"
    __table_args__ = (
        CheckConstraint("balance >= 0.00", name="ck_platform_balance_non_negative"),
    )

    id      = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    type    = Column(
        SAEnum(*ACCOUNT_TYPES, name="platform_account_type_enum"),
        unique=True,
        nullable=False,
    )
    balance = Column(Numeric(18, 2), nullable=False, server_default="0.00")
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    ledger_entries = relationship("PlatformLedgerEntry", back_populates="account")


class PlatformLedgerEntry(Base):
    """Immutable double-entry record for every platform account movement.
    direction: 'credit' = money flowing IN to the pool,
               'debit'  = money flowing OUT of the pool.
    """
    __tablename__ = "platform_ledger_entries"

    id               = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    account_id       = Column(UUID(as_uuid=True), ForeignKey("platform_accounts.id"), nullable=False)
    direction        = Column(SAEnum("credit", "debit", name="ledger_direction_enum"), nullable=False)
    amount           = Column(Numeric(18, 2), nullable=False)
    idempotency_key  = Column(String(64), unique=True, nullable=False,
                               server_default=text("gen_random_uuid()::text"))
    reference        = Column(String(100), nullable=True)
    transaction_id   = Column(UUID(as_uuid=True), ForeignKey("transactions.id"), nullable=True)
    user_id          = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    note             = Column(String(255), nullable=True)
    created_at       = Column(DateTime(timezone=True), server_default=func.now())

    account = relationship("PlatformAccount", back_populates="ledger_entries")
