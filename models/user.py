from sqlalchemy import (
    Column, String, Boolean, SmallInteger, Text,
    DateTime, Date, Index, UniqueConstraint, ForeignKey,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from database import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        Index("idx_users_phone", "phone_number"),
        Index("idx_users_tier",  "verification_tier"),
    )

    id                   = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    phone_number         = Column(String(20),  unique=True, nullable=False)
    email                = Column(String(255), unique=True)
    full_name            = Column(String(255), nullable=False)
    country              = Column(String(100), nullable=False, server_default="Pakistan")
    date_of_birth        = Column(Date)
    age                  = Column(SmallInteger)
    password_hash        = Column(String(255), nullable=False)
    pin_hash             = Column(String(255))
    cnic_number          = Column(String(20))
    cnic_encrypted       = Column(Text)
    cnic_number_masked   = Column(String(20))
    verification_tier    = Column(SmallInteger, server_default="0")
    is_verified          = Column(Boolean, server_default="false")
    is_superuser         = Column(Boolean, server_default="false")
    is_locked            = Column(Boolean, server_default="false")
    is_active            = Column(Boolean, server_default="true")
    is_flagged           = Column(Boolean, server_default="false")
    risk_score           = Column(SmallInteger, server_default="0")
    account_type         = Column(String(20),  server_default="individual")
    biometric_enabled    = Column(Boolean, server_default="false")
    cnic_verified        = Column(Boolean, server_default="false")
    biometric_verified   = Column(Boolean, server_default="false")
    fingerprint_verified = Column(Boolean, server_default="false")
    nadra_verified       = Column(Boolean, server_default="false")
    fcm_token            = Column(Text)
    fcm_token_updated_at = Column(DateTime(timezone=True))
    login_attempts       = Column(SmallInteger, server_default="0")
    profile_photo_url    = Column(Text)
    member_since         = Column(DateTime(timezone=True), server_default=func.now())
    last_login_at        = Column(DateTime(timezone=True))
    created_at           = Column(DateTime(timezone=True), server_default=func.now())
    updated_at           = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    wallet               = relationship("Wallet",           back_populates="user", uselist=False)
    device_registries    = relationship("DeviceRegistry",   back_populates="user", cascade="all, delete-orphan")
    refresh_tokens_rel   = relationship("RefreshToken",     back_populates="user", cascade="all, delete-orphan")
    login_audits         = relationship("LoginAudit",       back_populates="user")
    transactions_sent    = relationship("Transaction",      back_populates="sender",    foreign_keys="Transaction.sender_id")
    transactions_received= relationship("Transaction",      back_populates="recipient", foreign_keys="Transaction.recipient_id")
    virtual_cards        = relationship("VirtualCard",      back_populates="user", cascade="all, delete-orphan")
    documents            = relationship("Document",         back_populates="user", cascade="all, delete-orphan")
    fingerprint_scans    = relationship("FingerprintScan",  back_populates="user", cascade="all, delete-orphan")
    business_profile     = relationship("BusinessProfile",  back_populates="user", uselist=False)
    bank_accounts        = relationship("BankAccount",      back_populates="user", cascade="all, delete-orphan")
    saving_goals         = relationship("SavingGoal",       back_populates="user", cascade="all, delete-orphan")
    investments          = relationship("Investment",       back_populates="user", cascade="all, delete-orphan")
    insurance_policies   = relationship("InsurancePolicy",  back_populates="user", cascade="all, delete-orphan")
    high_yield_deposits  = relationship("HighYieldDeposit", back_populates="user", cascade="all, delete-orphan")
    rewards              = relationship("Reward",           back_populates="user", uselist=False)
    reward_offers        = relationship("RewardOffer",      back_populates="user", cascade="all, delete-orphan")
    reward_transactions  = relationship("RewardTransaction",back_populates="user", cascade="all, delete-orphan")
    trusted_circle       = relationship("TrustedCircle",    back_populates="user",    foreign_keys="TrustedCircle.user_id",    cascade="all, delete-orphan")
    bill_splits_created  = relationship("BillSplit",        back_populates="creator")
    split_participations = relationship("SplitParticipant", back_populates="user")
    ai_insights          = relationship("AiInsight",        back_populates="user", uselist=False)
    chat_session         = relationship("ChatSession",      back_populates="user", uselist=False)
    notifications        = relationship("Notification",     back_populates="user", cascade="all, delete-orphan")
    zakat_calculations   = relationship("ZakatCalculation", back_populates="user", cascade="all, delete-orphan")
    zakat_settings       = relationship("UserZakatSettings",  back_populates="user", uselist=False, cascade="all, delete-orphan")
    wealth_profile       = relationship("WealthProfile",       back_populates="user", uselist=False, cascade="all, delete-orphan")
    hawl_tracking        = relationship("HawlTracking",         back_populates="user", uselist=False, cascade="all, delete-orphan")
    fraud_flags          = relationship("FraudFlag",             back_populates="user",         foreign_keys="FraudFlag.user_id")
    admin_actions_done   = relationship("AdminAction",           back_populates="admin",        foreign_keys="AdminAction.admin_id")
    behaviour_profile    = relationship("UserBehaviourProfile",  back_populates="user",         uselist=False, cascade="all, delete-orphan")
    wallet_debts         = relationship("WalletDebt",            back_populates="user",         cascade="all, delete-orphan")
    disputes             = relationship("TransactionDispute",     back_populates="user",         foreign_keys="TransactionDispute.user_id", cascade="all, delete-orphan")
    str_reports          = relationship("StrReport",             back_populates="user",         foreign_keys="StrReport.user_id",          cascade="all, delete-orphan")


class DeviceRegistry(Base):
    __tablename__ = "device_registry"
    __table_args__ = (
        UniqueConstraint("user_id", "device_fingerprint", name="uq_device_user_fp"),
    )

    id                 = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    user_id            = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    device_fingerprint = Column(String(255), nullable=False)
    device_name        = Column(String(255))
    device_os          = Column(String(100))
    is_trusted         = Column(Boolean, server_default="false")
    trusted_at         = Column(DateTime(timezone=True))
    first_seen_at      = Column(DateTime(timezone=True), server_default=func.now())
    last_seen_at       = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="device_registries")


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id                 = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    user_id            = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash         = Column(String(255), unique=True, nullable=False)
    device_fingerprint = Column(String(255))
    device_info        = Column(Text)       # JSON string
    expires_at         = Column(DateTime(timezone=True), nullable=False)
    is_revoked         = Column(Boolean, server_default="false")
    revoked_at         = Column(DateTime(timezone=True))
    created_at         = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="refresh_tokens_rel")


class LoginAudit(Base):
    __tablename__ = "login_audit"

    id                 = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    user_id            = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    phone_number       = Column(String(15))
    ip_address         = Column(String(45))
    user_agent         = Column(Text)
    device_fingerprint = Column(String(255))
    success            = Column(Boolean, nullable=False)
    failure_reason     = Column(String(255))
    created_at         = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="login_audits")
