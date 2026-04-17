"""Card schemas — request/response models for PROMPT 05."""
from datetime import datetime
from decimal import Decimal
from typing import Optional, List
from uuid import UUID

from pydantic import BaseModel, Field


class CardIssueRequest(BaseModel):
    card_type: str = Field("virtual", pattern="^(virtual|physical)$")
    card_network: str = Field("visa", pattern="^(visa|mastercard)$")
    card_name: Optional[str] = Field(None, max_length=100)
    gradient_theme: str = Field("blue", pattern="^(blue|purple|green|gold|red|midnight)$")


class CardResponse(BaseModel):
    id: UUID
    card_name: Optional[str]
    card_holder_name: str
    card_type: str
    card_network: str
    last_four: str
    expiry_month: int
    expiry_year: int
    gradient_theme: str
    status: str
    is_frozen: bool
    daily_limit: Decimal
    monthly_limit: Decimal
    monthly_spent: Decimal
    monthly_remaining: Decimal
    spending_limit: Decimal
    monthly_reset_at: Optional[datetime]
    delivery_status: Optional[str]
    is_online_enabled: bool
    is_international_enabled: bool
    is_atm_enabled: bool
    is_contactless: bool
    issued_at: datetime

    class Config:
        from_attributes = True


class CardDetailResponse(BaseModel):
    id: UUID
    card_holder_name: str
    card_number: str
    last_four: str
    cvv: str
    expiry_month: int
    expiry_year: int
    card_network: str
    card_type: str


class CardLimitsRequest(BaseModel):
    daily_limit: Optional[Decimal] = Field(None, gt=0, le=500000)
    monthly_limit: Optional[Decimal] = Field(None, gt=0, le=2000000)
    spending_limit: Optional[Decimal] = Field(None, gt=0, le=500000)


class CardSettingsRequest(BaseModel):
    is_online_enabled: Optional[bool] = None
    is_international_enabled: Optional[bool] = None
    is_atm_enabled: Optional[bool] = None
    is_contactless: Optional[bool] = None


class CardPinChangeRequest(BaseModel):
    old_pin: str = Field(..., min_length=6, max_length=6)
    new_pin: str = Field(..., min_length=6, max_length=6)


class CardPayRequest(BaseModel):
    amount: Decimal = Field(..., gt=0)
    merchant_name: str = Field(..., max_length=100)
    purpose: str = Field("Shopping", pattern="^(Food|Bill|Shopping|Travel|Family|Medical|Rent|Study|Business|Other)$")
    description: Optional[str] = None
    pin: str = Field(..., min_length=6, max_length=6)


class ATMWithdrawRequest(BaseModel):
    amount: Decimal = Field(..., gt=0, le=50000)
    pin: str = Field(..., min_length=6, max_length=6)


class BlockCardRequest(BaseModel):
    pin: str = Field(..., min_length=6, max_length=6)
    reason: Optional[str] = None


class CardTransactionItem(BaseModel):
    id: UUID
    reference_number: str
    type: str
    amount: Decimal
    status: str
    purpose: Optional[str]
    description: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class CardTransactionListResponse(BaseModel):
    items: List[CardTransactionItem]
    total: int
    page: int
    page_size: int


class MessageResponse(BaseModel):
    message: str
