"""Fraud detection models — user_behaviour_profiles, wallet_debts,
transaction_disputes, str_reports."""
from sqlalchemy import (
    Column, String, Boolean, Integer, Numeric,
    Text, DateTime, ForeignKey,
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from database import Base


class UserBehaviourProfile(Base):
    __tablename__ = "user_behaviour_profiles"

    user_id                 = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    avg_transaction_pkr     = Column(Numeric(12, 2), server_default="0.00")
    max_transaction_pkr     = Column(Numeric(12, 2), server_default="0.00")
    typical_hour_start      = Column(Integer, nullable=True)
    typical_hour_end        = Column(Integer, nullable=True)
    known_recipients_count  = Column(Integer, server_default="0")
    total_transaction_count = Column(Integer, server_default="0")
    last_calculated_at      = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User", back_populates="behaviour_profile")


class WalletDebt(Base):
    __tablename__ = "wallet_debts"

    id                    = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    user_id               = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    amount_pkr            = Column(Numeric(12, 2), nullable=False)
    reason                = Column(String(255), nullable=False)
    source_transaction_id = Column(UUID(as_uuid=True), ForeignKey("transactions.id"), nullable=True)
    due_at                = Column(DateTime(timezone=True), nullable=False)
    is_settled            = Column(Boolean, server_default="false")
    settled_at            = Column(DateTime(timezone=True), nullable=True)
    created_at            = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="wallet_debts")


class TransactionDispute(Base):
    __tablename__ = "transaction_disputes"

    id              = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    user_id         = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    transaction_id  = Column(UUID(as_uuid=True), ForeignKey("transactions.id"), nullable=False)
    dispute_type    = Column(
        SAEnum("unauthorized", "wrong_amount", "wrong_recipient", "other",
               name="dispute_type_enum"),
        nullable=False,
    )
    reason          = Column(Text, nullable=False)
    status          = Column(
        SAEnum("open", "under_review", "resolved", "dismissed",
               name="dispute_status_enum"),
        server_default="open",
    )
    created_at      = Column(DateTime(timezone=True), server_default=func.now())
    resolved_at     = Column(DateTime(timezone=True), nullable=True)
    resolved_by     = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    resolution_note = Column(Text, nullable=True)

    user = relationship("User", back_populates="disputes", foreign_keys=[user_id])


class StrReport(Base):
    __tablename__ = "str_reports"

    id             = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    user_id        = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    transaction_id = Column(UUID(as_uuid=True), ForeignKey("transactions.id"), nullable=True)
    report_type    = Column(
        SAEnum("STR", "CTR", name="str_report_type_enum"),
        nullable=False,
    )
    amount_pkr     = Column(Numeric(12, 2), nullable=False)
    ai_narrative   = Column(Text, nullable=True)
    status         = Column(
        SAEnum("draft", "reviewed", "submitted", name="str_status_enum"),
        server_default="draft",
    )
    generated_at   = Column(DateTime(timezone=True), server_default=func.now())
    reviewed_by    = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    submitted_at   = Column(DateTime(timezone=True), nullable=True)
    submission_ref = Column(String(100), nullable=True)

    user = relationship("User", back_populates="str_reports", foreign_keys=[user_id])
