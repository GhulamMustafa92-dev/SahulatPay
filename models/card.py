from sqlalchemy import Column, String, Boolean, SmallInteger, Numeric, DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from database import Base


class VirtualCard(Base):
    __tablename__ = "virtual_cards"

    id                       = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    user_id                  = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    card_name                = Column(String(100))
    card_holder_name         = Column(String(255), nullable=False)
    card_type                = Column(String(20),  server_default="virtual")   # virtual | physical
    card_network             = Column(String(20),  server_default="visa")      # visa | mastercard
    card_number_hash         = Column(String(255), nullable=False)             # SHA-256 of full number
    last_four                = Column(String(4),   nullable=False)
    cvv_hash                 = Column(String(255), nullable=False)             # bcrypt hashed
    expiry_month             = Column(SmallInteger, nullable=False)
    expiry_year              = Column(SmallInteger, nullable=False)
    gradient_theme           = Column(String(50),  server_default="blue")      # blue|purple|green|gold|red|midnight
    pin_hash                 = Column(String(255))                             # bcrypt card PIN
    spending_limit           = Column(Numeric(10, 2), server_default="25000.00")
    daily_limit              = Column(Numeric(10, 2), server_default="25000.00")
    monthly_limit            = Column(Numeric(10, 2), server_default="500000.00")
    monthly_spent            = Column(Numeric(10, 2), server_default="0.00")   # progress bar tracking
    monthly_reset_at         = Column(DateTime(timezone=True))                 # 1st of next month UTC
    status                   = Column(String(30),  server_default="active")
    # active | frozen | blocked | replaced | processing | dispatched
    # in_transit | out_for_delivery | delivered
    is_frozen                = Column(Boolean, server_default="false")
    is_online_enabled        = Column(Boolean, server_default="true")
    is_international_enabled = Column(Boolean, server_default="false")
    is_atm_enabled           = Column(Boolean, server_default="false")
    is_contactless           = Column(Boolean, server_default="true")
    physical_requested       = Column(Boolean, server_default="false")
    card_number_encrypted    = Column(Text)                                        # Fernet AES-256 — for details display
    cvv_encrypted            = Column(Text)                                        # Fernet AES-256 — for details display
    delivery_status          = Column(String(50))                                  # null=virtual; processing|dispatched|in_transit|out_for_delivery|delivered
    issued_at                = Column(DateTime(timezone=True), server_default=func.now())
    created_at               = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="virtual_cards")
