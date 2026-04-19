"""Transaction router — P2P, QR, topup, bills, history (PROMPT 07)."""
import json
import secrets
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, desc, or_, and_
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from limiter import limiter
from models.fraud import TransactionDispute
from models.other import FraudFlag
from models.user import User
from models.wallet import Wallet
from models.transaction import Transaction
from services.auth_service import get_current_user, normalize_phone
from services.notification_service import send_notification
from services.wallet_service import doTransfer, decode_pending_tx_token, generate_reference, TIER_LIMITS
from schemas.transaction import (
    SendRequest, SendResponse,
    ConfirmBiometricRequest,
    QRSendRequest,
    TopupRequest, TopupResponse,
    BillCategory, BillPayRequest, BillPayResponse,
    TransactionItem, TransactionHistoryResponse,
)

router = APIRouter()


def _utcnow():
    return datetime.now(timezone.utc)


def _mask_name(name: str) -> str:
    parts = name.strip().split()
    if len(parts) == 1:
        return parts[0][:2] + "****"
    return parts[0] + " " + parts[1][0] + "****"


BILL_CATEGORIES = [
    BillCategory(code="ssgc",       name="SSGC (Sui Southern Gas)",       icon="gas",        description="Karachi & Sindh gas bills"),
    BillCategory(code="sngpl",      name="SNGPL (Sui Northern Gas)",      icon="gas",        description="Punjab & KPK gas bills"),
    BillCategory(code="kelectric",  name="K-Electric",                    icon="electricity",description="Karachi electricity"),
    BillCategory(code="lesco",      name="LESCO",                         icon="electricity",description="Lahore electricity"),
    BillCategory(code="iesco",      name="IESCO",                         icon="electricity",description="Islamabad electricity"),
    BillCategory(code="fesco",      name="FESCO",                         icon="electricity",description="Faisalabad electricity"),
    BillCategory(code="mepco",      name="MEPCO",                         icon="electricity",description="Multan electricity"),
    BillCategory(code="wapda",      name="WAPDA",                         icon="electricity",description="National electricity grid"),
    BillCategory(code="ptcl",       name="PTCL",                          icon="internet",   description="Landline & DSL broadband"),
    BillCategory(code="stormfiber", name="StormFiber",                    icon="internet",   description="Fiber internet"),
    BillCategory(code="nayatel",    name="Nayatel",                       icon="internet",   description="Triple play services"),
    BillCategory(code="water",      name="WASA / Water Board",            icon="water",      description="Water & sanitation"),
]


# ══════════════════════════════════════════════════════════════════════════════
# POST /transactions/send
# ══════════════════════════════════════════════════════════════════════════════
@router.post("/send", response_model=SendResponse, status_code=201)
@limiter.limit("10/minute")
async def send_p2p(
    request: Request,
    body: SendRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        normalized = normalize_phone(body.recipient_phone)
    except ValueError:
        raise HTTPException(400, "Invalid recipient phone format")
    if normalized == current_user.phone_number:
        raise HTTPException(400, "Cannot send money to yourself")
    recipient = (await db.execute(select(User).where(User.phone_number == normalized))).scalar_one_or_none()
    if not recipient:
        raise HTTPException(404, "Recipient not found")
    result = await doTransfer(
        db=db, sender_id=current_user.id, recipient_id=recipient.id,
        amount=body.amount, purpose=body.purpose,
        description=body.description, pin=body.pin,
    )
    if result["status"] == "pending_biometric":
        return SendResponse(status="pending_biometric",
                            message="Amount ≥ PKR 1,000. Biometric confirmation required.",
                            pending_tx_token=result["pending_tx_token"])
    if result["status"] == "under_review":
        return SendResponse(
            status="under_review",
            message=result.get("message", "Transaction is under review."),
            reference_number=result["reference_number"],
            transaction_id=result["transaction_id"],
            cashback_earned=result["cashback_earned"],
            new_balance=result["new_balance"],
        )
    return SendResponse(
        status="completed",
        message=f"PKR {body.amount:,.2f} sent to {_mask_name(recipient.full_name)}",
        reference_number=result["reference_number"],
        transaction_id=result["transaction_id"],
        cashback_earned=result["cashback_earned"],
        new_balance=result["new_balance"],
    )


# ══════════════════════════════════════════════════════════════════════════════
# POST /transactions/confirm-biometric
# ══════════════════════════════════════════════════════════════════════════════
@router.post("/confirm-biometric", response_model=SendResponse, status_code=201)
@limiter.limit("10/minute")
async def confirm_biometric(
    request: Request,
    body: ConfirmBiometricRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from jose import JWTError
    try:
        payload = decode_pending_tx_token(body.pending_tx_token)
    except JWTError:
        raise HTTPException(400, "Transfer token invalid or expired")
    if payload.get("type") != "pending_tx":
        raise HTTPException(400, "Invalid token type")
    if str(current_user.id) != payload.get("sender_id"):
        raise HTTPException(403, "Token does not belong to current user")
    if not body.biometric_verified:
        raise HTTPException(400, "Biometric verification was not completed")
    result = await doTransfer(
        db=db,
        sender_id=UUID(payload["sender_id"]),
        recipient_id=UUID(payload["recipient_id"]),
        amount=Decimal(payload["amount"]),
        purpose=payload["purpose"],
        description=payload.get("description") or None,
        card_id=UUID(payload["card_id"]) if payload.get("card_id") else None,
        biometric_confirmed=True,
    )
    if result["status"] == "under_review":
        return SendResponse(
            status="under_review",
            message=result.get("message", "Transaction is under review."),
            reference_number=result["reference_number"],
            transaction_id=result["transaction_id"],
            cashback_earned=result["cashback_earned"],
            new_balance=result["new_balance"],
        )
    return SendResponse(
        status="completed",
        message=f"PKR {payload['amount']} sent (biometric confirmed)",
        reference_number=result["reference_number"],
        transaction_id=result["transaction_id"],
        cashback_earned=result["cashback_earned"],
        new_balance=result["new_balance"],
    )


# ══════════════════════════════════════════════════════════════════════════════
# POST /transactions/send-qr
# ══════════════════════════════════════════════════════════════════════════════
@router.post("/send-qr", response_model=SendResponse, status_code=201)
@limiter.limit("10/minute")
async def send_qr(
    request: Request,
    body: QRSendRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        payload = json.loads(body.qr_payload)
        phone = payload.get("phone") or payload.get("qr_id")
        qr_amount = payload.get("amount")
    except (json.JSONDecodeError, AttributeError):
        raise HTTPException(400, "Invalid QR payload — must be valid JSON")
    if not phone:
        raise HTTPException(400, "QR code does not contain a phone number")
    try:
        normalized = normalize_phone(phone)
    except ValueError:
        raise HTTPException(400, "QR phone format invalid")
    if normalized == current_user.phone_number:
        raise HTTPException(400, "Cannot pay yourself")
    amount = Decimal(str(body.amount or qr_amount or 0))
    if amount <= 0:
        raise HTTPException(400, "Amount required — QR is open-amount, specify amount in request")
    recipient = (await db.execute(select(User).where(User.phone_number == normalized))).scalar_one_or_none()
    if not recipient:
        raise HTTPException(404, "QR recipient not found")
    result = await doTransfer(
        db=db, sender_id=current_user.id, recipient_id=recipient.id,
        amount=amount, purpose=body.purpose,
        description=body.description or payload.get("description"),
        pin=body.pin,
    )
    if result["status"] == "pending_biometric":
        return SendResponse(status="pending_biometric",
                            message="Amount ≥ PKR 1,000. Biometric confirmation required.",
                            pending_tx_token=result["pending_tx_token"])
    if result["status"] == "under_review":
        return SendResponse(
            status="under_review",
            message=result.get("message", "Transaction is under review."),
            reference_number=result["reference_number"],
            transaction_id=result["transaction_id"],
            cashback_earned=result["cashback_earned"],
            new_balance=result["new_balance"],
        )
    return SendResponse(
        status="completed",
        message=f"QR payment of PKR {amount:,.2f} sent to {_mask_name(recipient.full_name)}",
        reference_number=result["reference_number"],
        transaction_id=result["transaction_id"],
        cashback_earned=result["cashback_earned"],
        new_balance=result["new_balance"],
    )


# ══════════════════════════════════════════════════════════════════════════════
# POST /transactions/topup
# ══════════════════════════════════════════════════════════════════════════════
@router.post("/topup", response_model=TopupResponse, status_code=201)
@limiter.limit("10/hour")
async def mobile_topup(
    request: Request,
    body: TopupRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from mock_servers.topup import detect_network
    network = body.network or detect_network(body.phone)
    if network == "unknown":
        raise HTTPException(400, f"Cannot detect network for {body.phone}. Provide network manually.")
    wallet = (await db.execute(select(Wallet).where(Wallet.user_id == current_user.id))).scalar_one_or_none()
    if not wallet:
        raise HTTPException(404, "Wallet not found")
    if wallet.is_frozen:
        raise HTTPException(403, "Wallet is frozen")
    if wallet.balance < body.amount:
        raise HTTPException(400, f"Insufficient balance. Available: PKR {wallet.balance:,.2f}")
    if not current_user.pin_hash:
        raise HTTPException(400, "PIN not set")
    import bcrypt
    if not bcrypt.checkpw(body.pin.encode(), current_user.pin_hash.encode()):
        raise HTTPException(401, "Incorrect PIN")
    wallet.balance -= body.amount
    ref = generate_reference()
    txn = Transaction(
        reference_number=ref,
        type="topup",
        amount=body.amount,
        fee=Decimal("0"),
        status="completed",
        sender_id=current_user.id,
        purpose="TopUp",
        description=f"Mobile top-up {network.capitalize()} {body.phone}",
        tx_metadata={"phone": body.phone, "network": network, "method": "topup"},
    )
    db.add(txn)
    await db.commit()
    await db.refresh(wallet)
    return TopupResponse(
        status="completed",
        message=f"PKR {body.amount:,.0f} top-up sent to {body.phone} ({network.capitalize()})",
        network=network.capitalize(),
        phone=body.phone,
        amount=body.amount,
        reference_number=ref,
        new_balance=wallet.balance,
    )


# ══════════════════════════════════════════════════════════════════════════════
# GET /transactions/bills/categories
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/bills/categories")
async def bill_categories():
    return {"categories": [c.model_dump() for c in BILL_CATEGORIES]}


# ══════════════════════════════════════════════════════════════════════════════
# POST /transactions/bills/pay
# ══════════════════════════════════════════════════════════════════════════════
@router.post("/bills/pay", response_model=BillPayResponse, status_code=201)
@limiter.limit("10/hour")
async def pay_bill(
    request: Request,
    body: BillPayRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    wallet = (await db.execute(select(Wallet).where(Wallet.user_id == current_user.id))).scalar_one_or_none()
    if not wallet or wallet.balance < body.amount:
        raise HTTPException(400, f"Insufficient balance")
    import bcrypt
    if not current_user.pin_hash or not bcrypt.checkpw(body.pin.encode(), current_user.pin_hash.encode()):
        raise HTTPException(401, "Incorrect PIN")
    wallet.balance -= body.amount
    ref = generate_reference()
    txn = Transaction(
        reference_number=ref,
        type="bill",
        amount=body.amount,
        fee=Decimal("0"),
        status="completed",
        sender_id=current_user.id,
        purpose="Bill",
        description=body.description or f"Bill payment — {body.category} {body.consumer_id}",
        tx_metadata={"category": body.category, "consumer_id": body.consumer_id},
    )
    db.add(txn)
    await db.commit()
    await db.refresh(wallet)
    return BillPayResponse(
        status="completed",
        message=f"Bill paid for {body.category.upper()} consumer {body.consumer_id}",
        reference_number=ref,
        consumer_id=body.consumer_id,
        amount=body.amount,
        new_balance=wallet.balance,
    )


# ══════════════════════════════════════════════════════════════════════════════
# GET /transactions/history
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/history", response_model=TransactionHistoryResponse)
async def transaction_history(
    type: Optional[str] = None,
    status: Optional[str] = None,
    purpose: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    search: Optional[str] = None,
    page: int = 1,
    per_page: int = 20,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from datetime import date
    q = select(Transaction).where(
        or_(Transaction.sender_id == current_user.id, Transaction.recipient_id == current_user.id)
    )
    if type:    q = q.where(Transaction.type == type)
    if status:  q = q.where(Transaction.status == status)
    if purpose: q = q.where(Transaction.purpose == purpose)
    if date_from:
        q = q.where(Transaction.created_at >= datetime.fromisoformat(date_from))
    if date_to:
        q = q.where(Transaction.created_at <= datetime.fromisoformat(date_to))
    if search:
        q = q.where(or_(
            Transaction.description.ilike(f"%{search}%"),
            Transaction.reference_number.ilike(f"%{search}%"),
        ))
    q = q.order_by(desc(Transaction.created_at))
    total_result = await db.execute(q)
    total = len(total_result.scalars().all())
    q = q.offset((page - 1) * per_page).limit(per_page)
    result = await db.execute(q)
    txns = result.scalars().all()
    items = []
    for t in txns:
        cp_id = t.recipient_id if t.sender_id == current_user.id else t.sender_id
        cp_name = cp_phone = None
        if cp_id:
            cp = (await db.execute(select(User).where(User.id == cp_id))).scalar_one_or_none()
            if cp:
                cp_name  = _mask_name(cp.full_name)
                cp_phone = cp.phone_number[:6] + "****" + cp.phone_number[-2:]
        items.append(TransactionItem(
            id=t.id, reference_number=t.reference_number,
            type=t.type, amount=t.amount, fee=t.fee,
            cashback_earned=t.cashback_earned, status=t.status,
            purpose=t.purpose, description=t.description,
            counterpart_name=cp_name, counterpart_phone=cp_phone,
            created_at=t.created_at, completed_at=t.completed_at,
        ))
    return TransactionHistoryResponse(
        items=items, total=total, page=page, per_page=per_page,
        has_next=(page * per_page) < total,
    )


# ══════════════════════════════════════════════════════════════════════════════
# POST /transactions/{txn_id}/dispute
# ══════════════════════════════════════════════════════════════════════════════
class DisputeRequest(BaseModel):
    dispute_type: str = Field(..., pattern="^(unauthorized|wrong_amount|wrong_recipient|other)$")
    reason: str = Field(..., min_length=10)


@router.post("/{txn_id}/dispute", status_code=201)
@limiter.limit("3/day")
async def file_dispute(
    request: Request,
    txn_id: UUID,
    body: DisputeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """User reports a suspicious/unauthorized transaction. Rate-limited to 3 per day."""
    txn = (await db.execute(select(Transaction).where(Transaction.id == txn_id))).scalar_one_or_none()
    if not txn:
        raise HTTPException(404, "Transaction not found")
    if txn.sender_id != current_user.id and txn.recipient_id != current_user.id:
        raise HTTPException(403, "Transaction does not belong to you")

    existing = (await db.execute(
        select(TransactionDispute).where(TransactionDispute.transaction_id == txn_id)
    )).scalar_one_or_none()
    if existing:
        raise HTTPException(409, "A dispute already exists for this transaction")

    dispute = TransactionDispute(
        user_id=current_user.id,
        transaction_id=txn_id,
        dispute_type=body.dispute_type,
        reason=body.reason,
        status="open",
    )
    db.add(dispute)

    txn.is_flagged  = True
    txn.flag_reason = f"user_dispute: {body.dispute_type}"
    txn.flagged_at  = _utcnow()
    txn.flagged_by  = current_user.id

    db.add(FraudFlag(
        user_id=current_user.id,
        transaction_id=txn_id,
        reason=f"user_reported_dispute: {body.dispute_type} — {body.reason[:200]}",
        severity="high",
    ))

    await db.commit()
    await db.refresh(dispute)

    await send_notification(
        db, current_user.id,
        "Dispute Registered",
        "Your dispute has been registered. We will respond within 24 hours.",
        "security",
        {"dispute_id": str(dispute.id), "transaction_id": str(txn_id)},
    )

    from services.fraud_scoring import schedule_admin_notify
    schedule_admin_notify(
        "🚩 User Dispute Filed",
        f"User {current_user.phone_number} disputed PKR {float(txn.amount):,.0f} — {body.dispute_type}",
        {"dispute_id": str(dispute.id), "txn_id": str(txn_id)},
    )

    return {
        "dispute_id": dispute.id,
        "status":     "open",
        "message":    "Dispute registered. We will review and respond within 24 hours.",
    }


# ══════════════════════════════════════════════════════════════════════════════
# GET /transactions/{tx_id}
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/{tx_id}")
async def get_transaction(
    tx_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    txn = (await db.execute(select(Transaction).where(Transaction.id == tx_id))).scalar_one_or_none()
    if not txn:
        raise HTTPException(404, "Transaction not found")
    if txn.sender_id != current_user.id and txn.recipient_id != current_user.id:
        raise HTTPException(403, "Access denied")
    return {
        "id":               txn.id,
        "reference_number": txn.reference_number,
        "type":             txn.type,
        "amount":           txn.amount,
        "fee":              txn.fee,
        "cashback_earned":  txn.cashback_earned,
        "status":           txn.status,
        "purpose":          txn.purpose,
        "description":      txn.description,
        "sender_id":        txn.sender_id,
        "recipient_id":     txn.recipient_id,
        "tx_metadata":      txn.tx_metadata,
        "created_at":       txn.created_at,
        "completed_at":     txn.completed_at,
    }
