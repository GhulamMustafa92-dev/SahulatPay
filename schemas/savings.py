"""Pydantic schemas for savings goals."""
from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class SavingGoalCreate(BaseModel):
    goal_name:     str     = Field(..., min_length=1, max_length=255)
    target_amount: Decimal = Field(..., gt=0)
    icon:          Optional[str]  = None
    deadline:      Optional[date] = None


class AutoDeductUpdate(BaseModel):
    enabled:   bool
    amount:    Optional[Decimal] = Field(default=None, gt=0)
    frequency: Optional[str]     = Field(default=None, pattern="^(weekly|monthly)$")

    @model_validator(mode="after")
    def validate_if_enabling(self):
        if self.enabled:
            if not self.amount:
                raise ValueError("amount is required when enabling auto-deduct")
            if not self.frequency:
                raise ValueError("frequency (weekly|monthly) is required when enabling auto-deduct")
        return self


class DepositRequest(BaseModel):
    amount: Decimal = Field(..., gt=0)
    pin:    str


class WithdrawRequest(BaseModel):
    amount: Decimal = Field(..., gt=0)
    pin:    str


class SavingGoalResponse(BaseModel):
    id:                 UUID
    goal_name:          str
    icon:               Optional[str]
    target_amount:      Decimal
    saved_amount:       Decimal
    deadline:           Optional[date]
    is_completed:       bool
    goal_achieved:      bool
    withdraw_count:     int
    auto_deduct_enabled: bool
    auto_deduct_amount: Optional[Decimal]
    auto_deduct_freq:   Optional[str]
    next_deduction_at:  Optional[datetime]
    last_deduction_at:  Optional[datetime]
    created_at:         datetime
    # Computed fields
    progress_percent:   float
    daily_saving_needed: Optional[float]

    model_config = {"from_attributes": True}


class SavingGoalListResponse(BaseModel):
    goals:       list[SavingGoalResponse]
    total:       int
    active_count: int
