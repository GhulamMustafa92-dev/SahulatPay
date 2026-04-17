"""Cards router — issue, manage, pay, ATM withdraw (PROMPT 05)."""
from __future__ import annotations

import hashlib
import random
import secrets
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Optional
from uuid import UUID

import bcrypt
from cryptography.fernet import Fernet
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, desc, cast
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_db
from limiter import limiter
from models.card import VirtualCard
from models.transaction import Transaction
from models.user import User
from schemas.card import (
    ATMWithdrawRequest, BlockCardRequest,
    CardDetailResponse, CardIssueRequest,
    CardLimitsRequest, CardPayRequest,
    CardPinChangeRequest, CardResponse,
    CardSettingsRequest, CardTransactionItem,
    CardTransactionListResponse, MessageResponse,
)
from services.auth_service import get_current_user
from services.wallet_service import doTransfer, generate_reference, TIER_LIMITS

router = APIRouter()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _fernet() -> Fernet:
    key = settings.ENCRYPTION_KEY
    if not key:
        key = Fernet.generate_key().decode()
    if isinstance(key, str):
        key = key.encode()
    return Fernet(key)


def _encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def _decrypt(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()


def _hash_pin(pin: str) -> str:
    return bcrypt.hashpw(pin.encode(), bcrypt.gensalt(rounds=12)).decode()


def _verify_pin(pin: str, pin_hash: str) -> bool:
    return bcrypt.checkpw(pin.encode(), pin_hash.encode())


# ── Luhn card number generation ───────────────────────────────────────────────
def generate_luhn_card_number(network: str) -> str:
    """Generate a valid 16-digit Luhn card number for visa or mastercard."""
    if network == "visa":
        prefix = "4"
    else:
        prefix = str(random.randint(51, 55))

    partial = prefix + "".join([str(random.randint(0, 9)) for _ in range(16 - len(prefix) - 1)])
    digits = [int(d) for d in partial]

    for i in range(len(digits) - 2, -1, -2):
        digits[i] *= 2
        if digits[i] > 9:
            digits[i] -= 9

    total = sum(digits)
    check = (10 - (total % 10)) % 10
    return partial + str(check)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _next_month_first() -> datetime:
    now = _utcnow()
    if now.month == 12:
        return now.replace(year=now.year + 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    return now.replace(month=now.month + 1, day=1, hour=0, minute=0, second=0, microsecond=0)


def _card_to_response(card: VirtualCard) -> CardResponse:
    monthly_spent     = card.monthly_spent or Decimal("0")
    monthly_limit     = card.monthly_limit or Decimal("500000")
    monthly_remaining = max(monthly_limit - monthly_spent, Decimal("0"))
    return CardResponse(
        id=card.id,
        card_name=card.card_name,
        card_holder_name=card.card_holder_name,
        card_type=card.card_type,
        card_network=card.card_network,
        last_four=card.last_four,
        expiry_month=card.expiry_month,
        expiry_year=card.expiry_year,
        gradient_theme=card.gradient_theme,
        status=card.status,
        is_frozen=card.is_frozen,
        daily_limit=card.daily_limit or Decimal("25000"),
        monthly_limit=monthly_limit,
        monthly_spent=monthly_spent,
        monthly_remaining=monthly_remaining,
        spending_limit=card.spending_limit or Decimal("25000"),
        monthly_reset_at=card.monthly_reset_at,
        delivery_status=card.delivery_status,
        is_online_enabled=card.is_online_enabled,
        is_international_enabled=card.is_international_enabled,
        is_atm_enabled=card.is_atm_enabled,
        is_contactless=card.is_contactless,
        issued_at=card.issued_at,
    )


async def _get_card_or_404(card_id: UUID, user_id: UUID, db: AsyncSession) -> VirtualCard:
    card = (await db.execute(
        select(VirtualCard).where(VirtualCard.id == card_id, VirtualCard.user_id == user_id)
    )).scalar_one_or_none()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")
    return card


# ══════════════════════════════════════════════════════════════════════════════
# POST /cards/issue
# ══════════════════════════════════════════════════════════════════════════════
@router.post("/issue", response_model=CardResponse, status_code=201)
@limiter.limit("5/hour")
async def issue_card(
    request: Request,
    body: CardIssueRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Issue a new virtual or physical card. Physical cards deduct PKR 500 fee."""
    from models.wallet import Wallet

    PHYSICAL_FEE = Decimal("500.00")

    if body.card_type == "physical":
        wallet = (await db.execute(
            select(Wallet).where(Wallet.user_id == current_user.id)
        )).scalar_one_or_none()
        if not wallet or wallet.balance < PHYSICAL_FEE:
            raise HTTPException(status_code=400, detail="Insufficient balance. Physical card requires PKR 500 fee.")
        wallet.balance -= PHYSICAL_FEE
        fee_txn = Transaction(
            reference_number=generate_reference(),
            type="topup",
            amount=PHYSICAL_FEE,
            fee=Decimal("0"),
            status="completed",
            sender_id=current_user.id,
            purpose="Bill",
            description="Physical card issuance fee",
            completed_at=_utcnow(),
            tx_metadata={"reason": "physical_card_fee"},
        )
        db.add(fee_txn)

    card_number = generate_luhn_card_number(body.card_network)
    cvv         = str(random.randint(100, 999))
    expiry      = _utcnow() + timedelta(days=365 * 3)

    card = VirtualCard(
        user_id                  = current_user.id,
        card_name                = body.card_name,
        card_holder_name         = current_user.full_name.upper(),
        card_type                = body.card_type,
        card_network             = body.card_network,
        card_number_hash         = _sha256(card_number),
        card_number_encrypted    = _encrypt(card_number),
        last_four                = card_number[-4:],
        cvv_hash                 = bcrypt.hashpw(cvv.encode(), bcrypt.gensalt(rounds=12)).decode(),
        cvv_encrypted            = _encrypt(cvv),
        expiry_month             = expiry.month,
        expiry_year              = expiry.year,
        gradient_theme           = body.gradient_theme,
        status                   = "processing" if body.card_type == "physical" else "active",
        delivery_status          = "processing" if body.card_type == "physical" else None,
        physical_requested       = body.card_type == "physical",
        monthly_reset_at         = _next_month_first(),
    )
    db.add(card)
    await db.commit()
    await db.refresh(card)
    return _card_to_response(card)


# ══════════════════════════════════════════════════════════════════════════════
# GET /cards/my-cards
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/my-cards", response_model=list[CardResponse])
async def my_cards(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    cards = (await db.execute(
        select(VirtualCard)
        .where(VirtualCard.user_id == current_user.id, VirtualCard.status != "blocked")
        .order_by(desc(VirtualCard.issued_at))
    )).scalars().all()
    return [_card_to_response(c) for c in cards]


# ══════════════════════════════════════════════════════════════════════════════
# GET /cards/{id}
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/{card_id}", response_model=CardResponse)
async def get_card(
    card_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    card = await _get_card_or_404(card_id, current_user.id, db)
    if card.monthly_reset_at and _utcnow() >= card.monthly_reset_at:
        card.monthly_spent   = Decimal("0")
        card.monthly_reset_at = _next_month_first()
        await db.commit()
        await db.refresh(card)
    return _card_to_response(card)


# ══════════════════════════════════════════════════════════════════════════════
# GET /cards/{id}/details  (PIN required)
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/{card_id}/details", response_model=CardDetailResponse)
@limiter.limit("10/hour")
async def get_card_details(
    request: Request,
    card_id: UUID,
    pin: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Returns full card number + CVV. Requires card PIN or user PIN."""
    card = await _get_card_or_404(card_id, current_user.id, db)

    pin_ok = False
    if card.pin_hash and _verify_pin(pin, card.pin_hash):
        pin_ok = True
    elif current_user.pin_hash and _verify_pin(pin, current_user.pin_hash):
        pin_ok = True
    if not pin_ok:
        raise HTTPException(status_code=401, detail="Incorrect PIN")

    if not card.card_number_encrypted or not card.cvv_encrypted:
        raise HTTPException(status_code=400, detail="Card details not available for this card")

    return CardDetailResponse(
        id=card.id,
        card_holder_name=card.card_holder_name,
        card_number=_decrypt(card.card_number_encrypted),
        last_four=card.last_four,
        cvv=_decrypt(card.cvv_encrypted),
        expiry_month=card.expiry_month,
        expiry_year=card.expiry_year,
        card_network=card.card_network,
        card_type=card.card_type,
    )


# ══════════════════════════════════════════════════════════════════════════════
# PATCH /cards/{id}/freeze  |  PATCH /cards/{id}/unfreeze
# ══════════════════════════════════════════════════════════════════════════════
@router.patch("/{card_id}/freeze", response_model=MessageResponse)
async def freeze_card(
    card_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    card = await _get_card_or_404(card_id, current_user.id, db)
    if card.status == "blocked":
        raise HTTPException(status_code=400, detail="Blocked cards cannot be frozen")
    card.is_frozen = True
    card.status    = "frozen"
    await db.commit()
    return MessageResponse(message="Card frozen successfully")


@router.patch("/{card_id}/unfreeze", response_model=MessageResponse)
async def unfreeze_card(
    card_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    card = await _get_card_or_404(card_id, current_user.id, db)
    if card.status == "blocked":
        raise HTTPException(status_code=400, detail="Blocked cards cannot be unfrozen")
    card.is_frozen = False
    card.status    = "active"
    await db.commit()
    return MessageResponse(message="Card unfrozen successfully")


# ══════════════════════════════════════════════════════════════════════════════
# POST /cards/{id}/block  (permanent, PIN required)
# ══════════════════════════════════════════════════════════════════════════════
@router.post("/{card_id}/block", response_model=MessageResponse)
async def block_card(
    card_id: UUID,
    body: BlockCardRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    card = await _get_card_or_404(card_id, current_user.id, db)
    if card.status == "blocked":
        raise HTTPException(status_code=400, detail="Card is already blocked")
    if not current_user.pin_hash or not _verify_pin(body.pin, current_user.pin_hash):
        raise HTTPException(status_code=401, detail="Incorrect PIN")
    card.is_frozen = True
    card.status    = "blocked"
    await db.commit()
    return MessageResponse(message="Card permanently blocked. Issue a new card if needed.")


# ══════════════════════════════════════════════════════════════════════════════
# POST /cards/{id}/replace
# ══════════════════════════════════════════════════════════════════════════════
@router.post("/{card_id}/replace", response_model=CardResponse, status_code=201)
async def replace_card(
    card_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    old_card = await _get_card_or_404(card_id, current_user.id, db)
    old_card.status    = "replaced"
    old_card.is_frozen = True

    card_number = generate_luhn_card_number(old_card.card_network)
    cvv         = str(random.randint(100, 999))
    expiry      = _utcnow() + timedelta(days=365 * 3)

    new_card = VirtualCard(
        user_id                  = current_user.id,
        card_name                = old_card.card_name,
        card_holder_name         = old_card.card_holder_name,
        card_type                = old_card.card_type,
        card_network             = old_card.card_network,
        card_number_hash         = _sha256(card_number),
        card_number_encrypted    = _encrypt(card_number),
        last_four                = card_number[-4:],
        cvv_hash                 = bcrypt.hashpw(cvv.encode(), bcrypt.gensalt(rounds=12)).decode(),
        cvv_encrypted            = _encrypt(cvv),
        expiry_month             = expiry.month,
        expiry_year              = expiry.year,
        gradient_theme           = old_card.gradient_theme,
        status                   = "active",
        monthly_reset_at         = _next_month_first(),
    )
    db.add(new_card)
    await db.commit()
    await db.refresh(new_card)
    return _card_to_response(new_card)


# ══════════════════════════════════════════════════════════════════════════════
# PATCH /cards/{id}/limits
# ══════════════════════════════════════════════════════════════════════════════
@router.patch("/{card_id}/limits", response_model=CardResponse)
async def update_limits(
    card_id: UUID,
    body: CardLimitsRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    card = await _get_card_or_404(card_id, current_user.id, db)
    if body.daily_limit   is not None: card.daily_limit   = body.daily_limit
    if body.monthly_limit is not None: card.monthly_limit = body.monthly_limit
    if body.spending_limit is not None: card.spending_limit = body.spending_limit
    await db.commit()
    await db.refresh(card)
    return _card_to_response(card)


# ══════════════════════════════════════════════════════════════════════════════
# PATCH /cards/{id}/settings
# ══════════════════════════════════════════════════════════════════════════════
@router.patch("/{card_id}/settings", response_model=CardResponse)
async def update_settings(
    card_id: UUID,
    body: CardSettingsRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    card = await _get_card_or_404(card_id, current_user.id, db)
    if body.is_online_enabled        is not None: card.is_online_enabled        = body.is_online_enabled
    if body.is_international_enabled is not None: card.is_international_enabled = body.is_international_enabled
    if body.is_atm_enabled           is not None: card.is_atm_enabled           = body.is_atm_enabled
    if body.is_contactless           is not None: card.is_contactless           = body.is_contactless
    await db.commit()
    await db.refresh(card)
    return _card_to_response(card)


# ══════════════════════════════════════════════════════════════════════════════
# PUT /cards/{id}/change-pin
# ══════════════════════════════════════════════════════════════════════════════
@router.put("/{card_id}/change-pin", response_model=MessageResponse)
async def change_card_pin(
    card_id: UUID,
    body: CardPinChangeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    card = await _get_card_or_404(card_id, current_user.id, db)
    if card.pin_hash and not _verify_pin(body.old_pin, card.pin_hash):
        raise HTTPException(status_code=401, detail="Old card PIN is incorrect")
    card.pin_hash = _hash_pin(body.new_pin)
    await db.commit()
    return MessageResponse(message="Card PIN updated successfully")


# ══════════════════════════════════════════════════════════════════════════════
# POST /cards/{id}/pay  (simulated merchant payment)
# ══════════════════════════════════════════════════════════════════════════════
@router.post("/{card_id}/pay", status_code=201)
@limiter.limit("20/minute")
async def card_pay(
    request: Request,
    card_id: UUID,
    body: CardPayRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Simulated merchant payment using a virtual card."""
    card = await _get_card_or_404(card_id, current_user.id, db)

    if card.status != "active":
        raise HTTPException(status_code=400, detail=f"Card is {card.status}. Cannot process payment.")
    if card.is_frozen:
        raise HTTPException(status_code=403, detail="Card is frozen")
    if not card.is_online_enabled:
        raise HTTPException(status_code=403, detail="Online payments are disabled for this card")

    if card.monthly_reset_at and _utcnow() >= card.monthly_reset_at:
        card.monthly_spent    = Decimal("0")
        card.monthly_reset_at = _next_month_first()

    spending_limit  = card.spending_limit or Decimal("25000")
    monthly_limit   = card.monthly_limit  or Decimal("500000")
    monthly_spent   = card.monthly_spent  or Decimal("0")

    if body.amount > spending_limit:
        raise HTTPException(status_code=400, detail=f"Amount exceeds per-transaction spending limit of PKR {spending_limit:,.2f}")
    if monthly_spent + body.amount > monthly_limit:
        remaining = monthly_limit - monthly_spent
        raise HTTPException(status_code=400, detail=f"Monthly card limit exceeded. Remaining: PKR {remaining:,.2f}")

    result = await doTransfer(
        db=db,
        sender_id=current_user.id,
        recipient_id=current_user.id,
        amount=body.amount,
        purpose=body.purpose,
        description=f"[CARD] {body.merchant_name} — {body.description or ''}".strip(" —"),
        pin=body.pin,
        card_id=card_id,
        biometric_confirmed=False,
    )

    if result["status"] == "pending_biometric":
        return {
            "status":           "pending_biometric",
            "message":          "Amount ≥ PKR 1,000. Biometric confirmation required.",
            "pending_tx_token": result["pending_tx_token"],
        }

    return {
        "status":           "completed",
        "message":          f"Payment of PKR {body.amount:,.2f} to {body.merchant_name} successful",
        "reference_number": result["reference_number"],
        "transaction_id":   str(result["transaction_id"]),
        "cashback_earned":  str(result["cashback_earned"]),
        "new_balance":      str(result["new_balance"]),
    }


# ══════════════════════════════════════════════════════════════════════════════
# POST /cards/{id}/atm-withdraw
# ══════════════════════════════════════════════════════════════════════════════
@router.post("/{card_id}/atm-withdraw", status_code=201)
@limiter.limit("5/hour")
async def atm_withdraw(
    request: Request,
    card_id: UUID,
    body: ATMWithdrawRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from models.wallet import Wallet
    card = await _get_card_or_404(card_id, current_user.id, db)

    if card.status != "active" or card.is_frozen:
        raise HTTPException(status_code=403, detail="Card is inactive or frozen")
    if not card.is_atm_enabled:
        raise HTTPException(status_code=403, detail="ATM withdrawals are disabled for this card")
    if not current_user.pin_hash or not _verify_pin(body.pin, current_user.pin_hash):
        raise HTTPException(status_code=401, detail="Incorrect PIN")

    wallet = (await db.execute(
        select(Wallet).where(Wallet.user_id == current_user.id)
    )).scalar_one_or_none()
    if not wallet or wallet.balance < body.amount:
        raise HTTPException(status_code=400, detail=f"Insufficient balance. Available: PKR {wallet.balance if wallet else 0:,.2f}")

    wallet.balance  -= body.amount
    ref = generate_reference()
    txn = Transaction(
        reference_number=ref,
        type="atm_withdrawal",
        amount=body.amount,
        fee=Decimal("0"),
        cashback_earned=Decimal("0"),
        status="completed",
        sender_id=current_user.id,
        purpose="Other",
        description=f"ATM withdrawal — card ****{card.last_four}",
        completed_at=_utcnow(),
        tx_metadata={"card_id": str(card_id), "last_four": card.last_four, "card_network": card.card_network},
    )
    db.add(txn)
    await db.commit()
    await db.refresh(wallet)

    return {
        "status":           "completed",
        "message":          f"PKR {body.amount:,.2f} withdrawn via ATM",
        "reference_number": ref,
        "new_balance":      str(wallet.balance),
    }


# ══════════════════════════════════════════════════════════════════════════════
# GET /cards/{id}/transactions  (paginated)
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/{card_id}/transactions", response_model=CardTransactionListResponse)
async def card_transactions(
    card_id: UUID,
    page: int = 1,
    page_size: int = 20,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_card_or_404(card_id, current_user.id, db)

    offset = (page - 1) * page_size
    result = await db.execute(
        select(Transaction)
        .where(
            Transaction.sender_id == current_user.id,
            Transaction.tx_metadata["card_id"].astext == str(card_id),
        )
        .order_by(desc(Transaction.created_at))
        .offset(offset)
        .limit(page_size)
    )
    txns = result.scalars().all()

    count_result = await db.execute(
        select(Transaction)
        .where(
            Transaction.sender_id == current_user.id,
            Transaction.tx_metadata["card_id"].astext == str(card_id),
        )
    )
    total = len(count_result.scalars().all())

    return CardTransactionListResponse(
        items=[CardTransactionItem(
            id=t.id,
            reference_number=t.reference_number,
            type=t.type,
            amount=t.amount,
            status=t.status,
            purpose=t.purpose,
            description=t.description,
            created_at=t.created_at,
        ) for t in txns],
        total=total,
        page=page,
        page_size=page_size,
    )


# ══════════════════════════════════════════════════════════════════════════════
# DELETE /cards/{id}  (PIN required)
# ══════════════════════════════════════════════════════════════════════════════
@router.delete("/{card_id}", response_model=MessageResponse)
async def delete_card(
    card_id: UUID,
    pin: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    card = await _get_card_or_404(card_id, current_user.id, db)
    if not current_user.pin_hash or not _verify_pin(pin, current_user.pin_hash):
        raise HTTPException(status_code=401, detail="Incorrect PIN")
    await db.delete(card)
    await db.commit()
    return MessageResponse(message=f"Card ****{card.last_four} deleted permanently")
