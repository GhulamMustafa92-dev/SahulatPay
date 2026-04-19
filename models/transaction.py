from sqlalchemy import (
    Column, String, Boolean, Integer, Numeric, Text,
    DateTime, ForeignKey, CheckConstraint, Index,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from database import Base


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_txn_amount_positive"),
        Index("idx_txn_sender",    "sender_id",    "created_at"),
        Index("idx_txn_recipient", "recipient_id", "created_at"),
        Index("idx_txn_status",    "status"),
        Index("idx_txn_purpose",   "purpose"),
    )

    id               = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    reference_number = Column(String(30), unique=True, nullable=False)
    type             = Column(String(40), nullable=False)
    # send | receive | deposit | topup | bill | split | investment | insurance
    # reward | bank_transfer | external_wallet | refund | reversed | high_yield | zakat | atm_withdrawal
    amount           = Column(Numeric(12, 2), nullable=False)
    fee              = Column(Numeric(10, 2), server_default="0.00")
    cashback_earned  = Column(Numeric(10, 2), server_default="0.00")
    status           = Column(String(20), server_default="pending")
    # pending | completed | failed | reversed
    sender_id        = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    recipient_id     = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    purpose          = Column(String(50))
    # Food | Bill | Shopping | Travel | Family | Medical | Rent | Study
    # Business | Salary | Zakat | Investment | Insurance | TopUp | Split | Other
    description      = Column(Text)
    is_flagged       = Column(Boolean, server_default="false")
    flag_reason      = Column(Text)
    flagged_by       = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    flagged_at       = Column(DateTime(timezone=True))
    tx_metadata      = Column(JSONB, server_default="{}")
    # {card_id, last_four, merchant_name, card_network, provider_ref, ...}
    hold_reason              = Column(Text, nullable=True)
    held_at                  = Column(DateTime(timezone=True), nullable=True)
    hold_expires_at          = Column(DateTime(timezone=True), nullable=True)
    reviewed_by              = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    deepseek_score           = Column(Integer, nullable=True)
    deepseek_recommendation  = Column(String(20), nullable=True)
    fraud_score              = Column(Integer, server_default="0")
    completed_at             = Column(DateTime(timezone=True))
    created_at               = Column(DateTime(timezone=True), server_default=func.now())

    sender    = relationship("User", back_populates="transactions_sent",     foreign_keys=[sender_id])
    recipient = relationship("User", back_populates="transactions_received",  foreign_keys=[recipient_id])
