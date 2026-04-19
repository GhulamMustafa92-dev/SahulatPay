"""Gold holdings model — tracks user's physical gold/silver ownership."""
from sqlalchemy import Column, Numeric, DateTime, ForeignKey, CheckConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from database import Base


class GoldHolding(Base):
    __tablename__ = "gold_holdings"
    __table_args__ = (
        CheckConstraint("gold_grams >= 0",   name="ck_gold_grams_non_negative"),
        CheckConstraint("silver_grams >= 0", name="ck_silver_grams_non_negative"),
    )

    user_id          = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
                              primary_key=True)
    gold_grams       = Column(Numeric(12, 4), nullable=False, server_default="0.0000")
    silver_grams     = Column(Numeric(12, 4), nullable=False, server_default="0.0000")
    avg_gold_rate_pkr= Column(Numeric(12, 4), nullable=True)
    avg_silver_rate_pkr = Column(Numeric(12, 4), nullable=True)
    total_invested_pkr  = Column(Numeric(14, 2), nullable=False, server_default="0.00")
    last_updated     = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="gold_holding")
