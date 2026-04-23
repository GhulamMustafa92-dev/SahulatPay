from sqlalchemy import (
    Column, String, Boolean, Numeric, Integer,
    Date, DateTime, ForeignKey, CheckConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from database import Base


class Investment(Base):
    __tablename__ = "investments"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_investment_amount_positive"),
    )

    id              = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    user_id         = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    plan_name       = Column(String(255), nullable=False)
    amount          = Column(Numeric(12, 2), nullable=False)
    return_rate     = Column(Numeric(5, 2),  nullable=False)   # annual % rate
    status          = Column(String(20), server_default="active")   # active | matured | withdrawn
    maturity_date   = Column(Date)
    expected_return = Column(Numeric(12, 2))
    actual_return   = Column(Numeric(12, 2))
    created_at      = Column(DateTime(timezone=True), server_default=func.now())
    withdrawn_at    = Column(DateTime(timezone=True))

    user = relationship("User", back_populates="investments")


class InsurancePolicy(Base):
    __tablename__ = "insurance_policies"

    id                  = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    user_id             = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    policy_type         = Column(String(100), nullable=False)
    plan_name           = Column(String(255), nullable=False)
    policy_number       = Column(String(50), nullable=True)          # e.g. POL-12345678
    premium             = Column(Numeric(10, 2), nullable=False)
    premium_paid        = Column(Numeric(10, 2), nullable=True)
    coverage            = Column(Numeric(12, 2), nullable=False)
    status              = Column(String(20), server_default="active")  # active | cancelled | expired
    policy_start        = Column(DateTime(timezone=True), server_default=func.now())
    policy_end          = Column(DateTime(timezone=True), nullable=True)
    activated_at        = Column(DateTime(timezone=True), server_default=func.now())
    expires_at          = Column(DateTime(timezone=True))
    cancelled_at        = Column(DateTime(timezone=True))
    refund_paid         = Column(Numeric(10, 2), nullable=True)
    auto_deduct_enabled = Column(Boolean, server_default="false")
    auto_deduct_freq    = Column(String(20), server_default="monthly")  # monthly | weekly
    next_deduction_at   = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User", back_populates="insurance_policies")


class HighYieldDeposit(Base):
    __tablename__ = "high_yield_deposits"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_hyd_amount_positive"),
    )

    id                = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    user_id           = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    amount            = Column(Numeric(12, 2), nullable=False)
    interest_rate     = Column(Numeric(5, 2),  nullable=False)
    period_days       = Column(Integer, nullable=False)
    status            = Column(String(20), server_default="active")  # active | matured | withdrawn
    maturity_date     = Column(Date, nullable=False)
    expected_interest = Column(Numeric(10, 2))
    early_withdrawal  = Column(Boolean, server_default="false")      # interest forfeited if true
    created_at        = Column(DateTime(timezone=True), server_default=func.now())
    matured_at        = Column(DateTime(timezone=True))

    user = relationship("User", back_populates="high_yield_deposits")
