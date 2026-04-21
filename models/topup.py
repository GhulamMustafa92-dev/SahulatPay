"""WalletTopUpRequest — pull-payment request sent via FCM; recipient approves with PIN."""
from sqlalchemy import Column, String, Numeric, DateTime, ForeignKey, Text, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from database import Base


class WalletTopUpRequest(Base):
    __tablename__ = "wallet_topup_requests"
    __table_args__ = (
        Index("idx_topup_recipient", "recipient_id", "status"),
        Index("idx_topup_requester", "requester_id", "status"),
    )

    id           = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    requester_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    recipient_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    wallet_type  = Column(String(50), nullable=False)
    # sadapay | nayapay | upaisa | easypaisa | jazzcash | sahulatpay
    amount       = Column(Numeric(12, 2), nullable=False)
    description  = Column(Text)
    status       = Column(String(20), server_default="pending")
    # pending | approved | rejected | expired
    expires_at   = Column(DateTime(timezone=True), nullable=False)
    created_at   = Column(DateTime(timezone=True), server_default=func.now())
    updated_at   = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    requester = relationship("User", foreign_keys=[requester_id])
    recipient = relationship("User", foreign_keys=[recipient_id])
