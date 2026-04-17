from sqlalchemy import (
    Column, String, Boolean, SmallInteger, Numeric,
    Text, DateTime, ForeignKey, Index,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from database import Base


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        Index("idx_notif_user", "user_id", "is_read", "created_at"),
    )

    id         = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    user_id    = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title      = Column(String(255), nullable=False)
    body       = Column(Text, nullable=False)
    type       = Column(String(50), nullable=False)
    # transaction | security | system | ai_insight | admin
    # split | savings | investment | insurance | rewards | zakat
    is_read    = Column(Boolean, server_default="false")
    data       = Column(JSONB, server_default="{}")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="notifications")


class PendingRegistration(Base):
    """Holds registration data until OTP is verified.
    Row is deleted on successful /otp/verify (purpose=registration) → user created.
    Expires after 30 min (cleanup via /register replace or scheduled job)."""
    __tablename__ = "pending_registrations"

    id            = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    phone_number  = Column(String(20),  unique=True, nullable=False)
    email         = Column(String(255))
    full_name     = Column(String(255), nullable=False)
    password_hash = Column(String(255), nullable=False)
    country       = Column(String(100))
    cnic_number   = Column(String(20))
    cnic_masked   = Column(String(20))
    date_of_birth = Column(DateTime(timezone=True))
    age           = Column(SmallInteger)
    account_type  = Column(String(20), server_default="individual")
    expires_at    = Column(DateTime(timezone=True), nullable=False)
    created_at    = Column(DateTime(timezone=True), server_default=func.now())


class OtpCode(Base):
    __tablename__ = "otp_codes"
    __table_args__ = (
        Index("idx_otp_lookup", "phone_number", "purpose", "is_used"),
    )

    id           = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    phone_number = Column(String(15), nullable=False)
    code_hash    = Column(String(255), nullable=False)       # bcrypt hashed 6-digit OTP
    purpose      = Column(String(50), nullable=False)
    # registration | password_reset | security_change | bank_link | new_device
    expires_at   = Column(DateTime(timezone=True), nullable=False)
    is_used      = Column(Boolean, server_default="false")
    attempts     = Column(SmallInteger, server_default="0")  # max 3 → mark is_used=TRUE
    created_at   = Column(DateTime(timezone=True), server_default=func.now())


class FraudFlag(Base):
    __tablename__ = "fraud_flags"

    id              = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    user_id         = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    transaction_id  = Column(UUID(as_uuid=True), ForeignKey("transactions.id"), nullable=True)
    reason          = Column(Text, nullable=False)
    severity        = Column(String(20), server_default="low")   # low | medium | high | critical
    is_resolved     = Column(Boolean, server_default="false")
    resolved_by     = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    resolved_at     = Column(DateTime(timezone=True))
    resolution_note = Column(Text)
    created_at      = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="fraud_flags", foreign_keys=[user_id])


class AdminAction(Base):
    __tablename__ = "admin_actions"
    # IMPORTANT: No UPDATE or DELETE ever allowed at service layer — immutable audit trail

    id              = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    admin_id        = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    action_type     = Column(String(100), nullable=False)
    # approve_kyc | reject_kyc | block_user | unblock_user | flag_transaction
    # reverse_transaction | tier_override | freeze_wallet | resolve_fraud
    # approve_business | reject_business | broadcast_notification
    # assign_offer | reveal_sensitive_data | block_card | update_delivery_status | flag_split
    target_user_id  = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    target_txn_id   = Column(UUID(as_uuid=True), ForeignKey("transactions.id"), nullable=True)
    reason          = Column(Text, nullable=False)
    action_metadata = Column(JSONB, server_default="{}")
    created_at      = Column(DateTime(timezone=True), server_default=func.now())

    admin       = relationship("User", back_populates="admin_actions_done", foreign_keys=[admin_id])
    target_user = relationship("User", foreign_keys=[target_user_id])


class ZakatCalculation(Base):
    __tablename__ = "zakat_calculations"

    id                     = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    user_id                = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    cash_pkr               = Column(Numeric(12, 2), server_default="0.00")
    gold_grams             = Column(Numeric(8, 3),  server_default="0.000")
    silver_grams           = Column(Numeric(8, 3),  server_default="0.000")
    business_inventory_pkr = Column(Numeric(12, 2), server_default="0.00")
    receivables_pkr        = Column(Numeric(12, 2), server_default="0.00")
    gold_rate_per_gram     = Column(Numeric(10, 2))     # PKR at calculation time (metals.live)
    silver_rate_per_gram   = Column(Numeric(10, 2))
    usd_to_pkr_rate        = Column(Numeric(10, 4))     # from er-api.com at calculation time
    total_assets_pkr       = Column(Numeric(12, 2))
    nisab_threshold_pkr    = Column(Numeric(12, 2))
    zakat_due_pkr          = Column(Numeric(10, 2))
    is_paid                = Column(Boolean, server_default="false")
    paid_at                = Column(DateTime(timezone=True))
    created_at             = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="zakat_calculations")
