from sqlalchemy import (
    Column, String, Boolean, SmallInteger, Text,
    DateTime, ForeignKey, CheckConstraint,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from database import Base


class Document(Base):
    __tablename__ = "documents"

    id                   = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    user_id              = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    document_type        = Column(String(50), nullable=False)
    # cnic_front | cnic_back | liveness_selfie | left_hand | right_hand | business_doc
    cloudinary_public_id = Column(Text, nullable=False)    # used to generate signed URL (15-min expiry)
    uploaded_at          = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="documents")


class FingerprintScan(Base):
    __tablename__ = "fingerprint_scans"
    __table_args__ = (
        CheckConstraint("finger_index BETWEEN 1 AND 8", name="ck_finger_index_range"),
    )

    id           = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    user_id      = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    finger_index = Column(SmallInteger, nullable=False)
    feature_hash = Column(String(255), nullable=False)    # SHA-256 of feature vector ONLY — raw image never stored
    scanned_at   = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="fingerprint_scans")


class BusinessProfile(Base):
    __tablename__ = "business_profiles"

    id                  = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    user_id             = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    business_name       = Column(String(255), nullable=False)
    registration_number = Column(String(100))
    business_type       = Column(String(100))
    ntn_number          = Column(String(50))                # National Tax Number (Pakistan)
    verification_status = Column(String(30), server_default="pending")
    # pending | under_review | verified | rejected
    rejection_reasons   = Column(JSONB, server_default="[]")
    ai_analysis_result  = Column(Text)                      # DeepSeek analysis of uploaded docs
    submitted_at        = Column(DateTime(timezone=True), server_default=func.now())
    verified_at         = Column(DateTime(timezone=True))

    user = relationship("User", back_populates="business_profile")


class KycReviewRequest(Base):
    __tablename__ = "kyc_review_requests"

    id                = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    user_id           = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    front_doc_id      = Column(UUID(as_uuid=True), ForeignKey("documents.id"))
    back_doc_id       = Column(UUID(as_uuid=True), ForeignKey("documents.id"))
    # AI-extracted fields
    extracted_cnic    = Column(String(20))
    extracted_name    = Column(String(255))
    extracted_dob     = Column(String(30))
    extracted_father  = Column(String(255))
    extracted_address = Column(Text)
    cnic_masked       = Column(String(25))
    cnic_encrypted    = Column(Text)
    # Review workflow
    status            = Column(String(20), server_default="pending")   # pending | approved | rejected
    rejection_reason  = Column(Text)
    reviewed_by       = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    reviewed_at       = Column(DateTime(timezone=True))
    submitted_at      = Column(DateTime(timezone=True), server_default=func.now())

    user     = relationship("User", foreign_keys=[user_id], backref="kyc_reviews")
    reviewer = relationship("User", foreign_keys=[reviewed_by])
