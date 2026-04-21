"""Wallet router — balance, deposit, lookup, transfer (PROMPT 04)."""
import asyncio
from decimal import Decimal
from datetime import datetime, timezone
from uuid import UUID
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, desc, or_
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from limiter import limiter
from models.user import User
from models.wallet import Wallet
from models.transaction import Transaction
from services.auth_service import get_current_user, normalize_phone
from services.wallet_service import (
    doTransfer,
    decode_pending_tx_token,
    generate_reference,
    TIER_LIMITS,
)
from services.platform_ledger import ledger_credit, make_idem_key
from services.notification_service import send_notification
from schemas.wallet import (
    WalletResponse, TransactionSummary,
    DepositRequest, DepositResponse,
    LookupResponse,
    TransferRequest, TransferResponse,
    ConfirmTransferRequest,
)

router = APIRouter()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _mask_name(name: str) -> str:
    """'Ghulam Mustafa' → 'Ghulam M****'"""
    parts = name.strip().split()
    if not parts:
        return "****"
    if len(parts) == 1:
        return parts[0][:2] + "****"
    return parts[0] + " " + parts[1][0] + "****"


# ══════════════════════════════════════════════════════════════════════════════
# GET /wallets/me
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/me", response_model=WalletResponse)
async def get_my_wallet(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return balance, limits, daily_spent, cashback, and recent 5 transactions."""
    wallet = (await db.execute(
        select(Wallet).where(Wallet.user_id == current_user.id)
    )).scalar_one_or_none()
    if not wallet:
        raise HTTPException(status_code=404, detail="Wallet not found")

    txns = (await db.execute(
        select(Transaction)
        .where(or_(
            Transaction.sender_id    == current_user.id,
            Transaction.recipient_id == current_user.id,
        ))
        .order_by(desc(Transaction.created_at))
        .limit(5)
    )).scalars().all()

    tier       = current_user.verification_tier or 0
    tier_limit = TIER_LIMITS.get(tier, Decimal("0"))
    daily_spent = wallet.daily_spent or Decimal("0")

    recent: list[TransactionSummary] = []
    for t in txns:
        cp_id = t.recipient_id if t.sender_id == current_user.id else t.sender_id
        cp_name: Optional[str] = None
        if cp_id:
            cp = (await db.execute(select(User).where(User.id == cp_id))).scalar_one_or_none()
            if cp:
                cp_name = _mask_name(cp.full_name)
        recent.append(TransactionSummary(
            id=t.id,
            reference_number=t.reference_number,
            type=t.type,
            amount=t.amount,
            fee=t.fee,
            status=t.status,
            purpose=t.purpose,
            description=t.description,
            created_at=t.created_at,
            counterpart_name=cp_name,
        ))

    return WalletResponse(
        id=wallet.id,
        balance=wallet.balance,
        currency=wallet.currency or "PKR",
        is_frozen=wallet.is_frozen,
        daily_limit=tier_limit,
        daily_spent=daily_spent,
        daily_remaining=max(tier_limit - daily_spent, Decimal("0")),
        cashback_pending=wallet.cashback_pending or Decimal("0"),
        cashback_claimed=wallet.cashback_claimed or Decimal("0"),
        account_number=current_user.phone_number,
        tier=tier,
        recent_transactions=recent,
    )


# ══════════════════════════════════════════════════════════════════════════════
# POST /wallets/deposit
# ══════════════════════════════════════════════════════════════════════════════
@router.post("/deposit", response_model=DepositResponse, status_code=201)
@limiter.limit("10/hour")
async def deposit(
    request: Request,
    body: DepositRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Deposit via debit card or bank transfer. Stores card last_four only — never full number."""
    wallet = (await db.execute(
        select(Wallet).where(Wallet.user_id == current_user.id)
    )).scalar_one_or_none()
    if not wallet:
        raise HTTPException(status_code=404, detail="Wallet not found")
    if wallet.is_frozen:
        raise HTTPException(status_code=403, detail="Wallet is frozen. Contact support.")

    wallet.balance = (wallet.balance or Decimal("0")) + body.amount

    ref = generate_reference()
    txn = Transaction(
        reference_number=ref,
        type="deposit",
        amount=body.amount,
        fee=Decimal("0"),
        cashback_earned=Decimal("0"),
        status="completed",
        recipient_id=current_user.id,
        purpose="TopUp",
        description=body.description or f"Deposit via {body.method}",
        completed_at=_utcnow(),
        tx_metadata={
            "method":         body.method,
            "card_last_four": body.card_last_four,
        },
    )
    db.add(txn)
    await ledger_credit(
        db, "main_float", body.amount,
        make_idem_key("deposit", str(current_user.id), ref),
        user_id=current_user.id, reference=ref,
        note=f"Deposit via {body.method}",
    )
    await db.commit()
    await db.refresh(txn)
    await db.refresh(wallet)

    return DepositResponse(
        message=f"PKR {body.amount:,.2f} deposited successfully",
        new_balance=wallet.balance,
        transaction_id=txn.id,
        reference_number=ref,
    )


# ══════════════════════════════════════════════════════════════════════════════
# GET /wallets/lookup?phone=...
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/lookup", response_model=LookupResponse)
@limiter.limit("30/minute")
async def lookup_recipient(
    request: Request,
    phone: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Pre-send lookup — returns masked name + remaining daily limit of recipient."""
    try:
        normalized = normalize_phone(phone)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid phone format. Use +92XXXXXXXXXX or 03XXXXXXXXX")

    if normalized == current_user.phone_number:
        raise HTTPException(status_code=400, detail="Cannot send money to yourself")

    recipient = (await db.execute(
        select(User).where(User.phone_number == normalized)
    )).scalar_one_or_none()

    if not recipient:
        return LookupResponse(found=False)

    tier        = recipient.verification_tier or 0
    tier_limit  = TIER_LIMITS.get(tier, Decimal("0"))

    recipient_wallet = (await db.execute(
        select(Wallet).where(Wallet.user_id == recipient.id)
    )).scalar_one_or_none()

    daily_remaining: Optional[Decimal] = None
    if recipient_wallet:
        daily_spent     = recipient_wallet.daily_spent or Decimal("0")
        daily_remaining = max(tier_limit - daily_spent, Decimal("0"))

    return LookupResponse(
        found=True,
        full_name=recipient.full_name,
        masked_name=_mask_name(recipient.full_name),
        masked_phone=normalized[:6] + "****" + normalized[-2:],
        tier=tier,
        daily_remaining=daily_remaining,
    )


# ══════════════════════════════════════════════════════════════════════════════
# POST /wallets/transfer
# ══════════════════════════════════════════════════════════════════════════════
@router.post("/transfer", response_model=TransferResponse, status_code=201)
@limiter.limit("10/minute")
async def transfer(
    request: Request,
    body: TransferRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Send money to another user.
    - Amount < PKR 1,000  → executes immediately (PIN required).
    - Amount >= PKR 1,000 → returns pending_tx_token (biometric confirm required).
    """
    try:
        normalized = normalize_phone(body.recipient_phone)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid recipient phone format")

    if normalized == current_user.phone_number:
        raise HTTPException(status_code=400, detail="Cannot send money to yourself")

    recipient = (await db.execute(
        select(User).where(User.phone_number == normalized)
    )).scalar_one_or_none()
    if not recipient:
        raise HTTPException(status_code=404, detail="Recipient not found. Check the phone number.")

    result = await doTransfer(
        db=db,
        sender_id=current_user.id,
        recipient_id=recipient.id,
        amount=body.amount,
        purpose=body.purpose,
        description=body.description,
        pin=body.pin,
        card_id=body.card_id,
        biometric_confirmed=False,
    )

    if result["status"] == "pending_biometric":
        return TransferResponse(
            status="pending_biometric",
            message="Amount ≥ PKR 1,000. Confirm with biometrics to proceed.",
            pending_tx_token=result["pending_tx_token"],
        )

    if result.get("status") == "completed":
        ref = result["reference_number"]
        amt = body.amount
        asyncio.create_task(send_notification(
            db, current_user.id,
            title="Money Sent ✓",
            body=f"PKR {amt:,.0f} sent to {_mask_name(recipient.full_name)}. Ref: {ref}",
            type="transaction",
            data={"reference": ref, "amount": str(amt), "direction": "debit"},
        ))
        asyncio.create_task(send_notification(
            db, recipient.id,
            title="Money Received 💰",
            body=f"PKR {amt:,.0f} received from {_mask_name(current_user.full_name)}. Ref: {ref}",
            type="transaction",
            data={"reference": ref, "amount": str(amt), "direction": "credit"},
        ))

    return TransferResponse(
        status="completed",
        message=f"PKR {body.amount:,.2f} sent to {_mask_name(recipient.full_name)}",
        reference_number=result["reference_number"],
        transaction_id=result["transaction_id"],
        cashback_earned=result["cashback_earned"],
        new_balance=result["new_balance"],
    )


# ══════════════════════════════════════════════════════════════════════════════
# POST /wallets/transfer/confirm  (biometric confirmation)
# ══════════════════════════════════════════════════════════════════════════════
@router.post("/transfer/confirm", response_model=TransferResponse, status_code=201)
@limiter.limit("10/minute")
async def confirm_transfer(
    request: Request,
    body: ConfirmTransferRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Execute a large transfer after Android app confirms biometric success."""
    from jose import JWTError
    try:
        payload = decode_pending_tx_token(body.pending_tx_token)
    except JWTError:
        raise HTTPException(status_code=400, detail="Transfer token is invalid or has expired (5-min TTL)")

    if payload.get("type") != "pending_tx":
        raise HTTPException(status_code=400, detail="Invalid token type")
    if str(current_user.id) != payload.get("sender_id"):
        raise HTTPException(status_code=403, detail="Token does not belong to the current user")
    if not body.biometric_verified:
        raise HTTPException(status_code=400, detail="Biometric verification was not completed")

    card_id = UUID(payload["card_id"]) if payload.get("card_id") else None

    result = await doTransfer(
        db=db,
        sender_id=UUID(payload["sender_id"]),
        recipient_id=UUID(payload["recipient_id"]),
        amount=Decimal(payload["amount"]),
        purpose=payload["purpose"],
        description=payload.get("description") or None,
        card_id=card_id,
        biometric_confirmed=True,
    )

    if result.get("status") == "completed":
        ref = result["reference_number"]
        amt = Decimal(payload["amount"])
        s_id = UUID(payload["sender_id"])
        r_id = UUID(payload["recipient_id"])
        asyncio.create_task(send_notification(
            db, s_id,
            title="Money Sent ✓",
            body=f"PKR {amt:,.0f} sent successfully (biometric confirmed). Ref: {ref}",
            type="transaction",
            data={"reference": ref, "amount": str(amt), "direction": "debit"},
        ))
        asyncio.create_task(send_notification(
            db, r_id,
            title="Money Received 💰",
            body=f"PKR {amt:,.0f} received. Ref: {ref}",
            type="transaction",
            data={"reference": ref, "amount": str(amt), "direction": "credit"},
        ))

    return TransferResponse(
        status="completed",
        message=f"PKR {payload['amount']} sent successfully (biometric confirmed)",
        reference_number=result["reference_number"],
        transaction_id=result["transaction_id"],
        cashback_earned=result["cashback_earned"],
        new_balance=result["new_balance"],
    )
