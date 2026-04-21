"""Wallet schemas — request/response models for PROMPT 04."""
from datetime import datetime
from decimal import Decimal
from typing import Optional, List
from uuid import UUID

from pydantic import BaseModel, Field


class TransactionSummary(BaseModel):
    id: UUID
    reference_number: str
    type: str
    amount: Decimal
    fee: Decimal
    status: str
    purpose: Optional[str] = None
    description: Optional[str] = None
    created_at: datetime
    counterpart_name: Optional[str] = None

    class Config:
        from_attributes = True


class WalletResponse(BaseModel):
    id: UUID
    balance: Decimal
    currency: str
    is_frozen: bool
    daily_limit: Decimal
    daily_spent: Decimal
    daily_remaining: Decimal
    cashback_pending: Decimal
    cashback_claimed: Decimal
    account_number: str
    tier: int
    recent_transactions: List[TransactionSummary] = []

    class Config:
        from_attributes = True


class DepositRequest(BaseModel):
    amount: Decimal = Field(..., gt=0, le=1_000_000, description="Amount in PKR")
    method: str = Field(..., pattern="^(debit_card|bank_transfer)$")
    card_last_four: Optional[str] = Field(None, min_length=4, max_length=4, pattern="^[0-9]{4}$")
    description: Optional[str] = None


class DepositResponse(BaseModel):
    message: str
    new_balance: Decimal
    transaction_id: UUID
    reference_number: str


class LookupResponse(BaseModel):
    found: bool
    full_name: Optional[str] = None
    masked_name: Optional[str] = None
    masked_phone: Optional[str] = None
    tier: Optional[int] = None
    daily_remaining: Optional[Decimal] = None


class TransferRequest(BaseModel):
    recipient_phone: str
    amount: Decimal = Field(..., gt=0, le=2_000_000)
    purpose: str = Field(
        ...,
        pattern="^(Food|Bill|Shopping|Travel|Family|Medical|Rent|Study|Business|Salary|Zakat|Investment|Insurance|TopUp|Split|Other)$",
    )
    description: Optional[str] = None
    pin: str = Field(..., min_length=6, max_length=6)
    card_id: Optional[UUID] = None


class TransferResponse(BaseModel):
    status: str
    message: str
    reference_number: Optional[str] = None
    transaction_id: Optional[UUID] = None
    pending_tx_token: Optional[str] = None
    cashback_earned: Optional[Decimal] = None
    new_balance: Optional[Decimal] = None


class ConfirmTransferRequest(BaseModel):
    pending_tx_token: str
    biometric_verified: bool = True
