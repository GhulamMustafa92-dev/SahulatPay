"""Zakat-related models — settings, wealth profile, Hawl tracking, metal rate cache."""
from sqlalchemy import (
    Column, String, Boolean, Integer, Numeric,
    DateTime, ForeignKey, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from database import Base


class UserZakatSettings(Base):
    __tablename__ = "user_zakat_settings"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_zakat_settings_user"),
    )

    id               = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    user_id          = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    madhab           = Column(String(20), server_default="hanafi", nullable=False)
    # hanafi | shafi | maliki | hanbali
    nisab_preference = Column(String(20), server_default="lower_of_two", nullable=False)
    # gold | silver | lower_of_two
    created_at       = Column(DateTime(timezone=True), server_default=func.now())
    updated_at       = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="zakat_settings")


class WealthProfile(Base):
    __tablename__ = "wealth_profiles"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_wealth_profile_user"),
    )

    id                        = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    user_id                   = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    external_banks_pkr        = Column(Numeric(12, 2), server_default="0.00")
    other_wallets_pkr         = Column(Numeric(12, 2), server_default="0.00")
    physical_gold_grams       = Column(Numeric(8, 3),  server_default="0.000")
    physical_silver_grams     = Column(Numeric(8, 3),  server_default="0.000")
    receivables_pkr           = Column(Numeric(12, 2), server_default="0.00")
    bad_debts_pkr             = Column(Numeric(12, 2), server_default="0.00")
    business_tradeable_pkr    = Column(Numeric(12, 2), server_default="0.00")
    business_cash_pkr         = Column(Numeric(12, 2), server_default="0.00")
    business_fixed_assets_pkr = Column(Numeric(12, 2), server_default="0.00")
    personal_loans_pkr        = Column(Numeric(12, 2), server_default="0.00")
    credit_card_pkr           = Column(Numeric(12, 2), server_default="0.00")
    car_loan_installments_pkr = Column(Numeric(12, 2), server_default="0.00")
    home_loan_pkr             = Column(Numeric(12, 2), server_default="0.00")
    home_loan_include         = Column(Boolean, server_default="false")
    other_liabilities_pkr     = Column(Numeric(12, 2), server_default="0.00")
    last_verified_at          = Column(DateTime(timezone=True), nullable=True)
    updated_at                = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="wealth_profile")


class HawlTracking(Base):
    __tablename__ = "hawl_tracking"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_hawl_user"),
    )

    id                    = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    user_id               = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    nisab_crossed_at      = Column(DateTime(timezone=True), nullable=True)
    zakat_due_date        = Column(DateTime(timezone=True), nullable=True)
    hawl_active           = Column(Boolean, server_default="false")
    last_reminder_sent_at = Column(DateTime(timezone=True), nullable=True)
    hawl_reset_count      = Column(Integer, server_default="0")
    hawl_reset_at         = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User", back_populates="hawl_tracking")


class MetalRateCache(Base):
    __tablename__ = "metal_rate_cache"

    id               = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    gold_usd_oz      = Column(Numeric(10, 4), nullable=False)
    silver_usd_oz    = Column(Numeric(10, 4), nullable=False)
    usd_to_pkr       = Column(Numeric(10, 4), nullable=False)
    gold_pkr_gram    = Column(Numeric(10, 4), nullable=False)
    silver_pkr_gram  = Column(Numeric(10, 4), nullable=False)
    nisab_gold_pkr   = Column(Numeric(12, 2), nullable=False)
    nisab_silver_pkr = Column(Numeric(12, 2), nullable=False)
    source           = Column(String(100), server_default="metals.live + er-api.com")
    fetched_at       = Column(DateTime(timezone=True), server_default=func.now())
