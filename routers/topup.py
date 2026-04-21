"""Wallet top-up router — pull-payment via FCM approval.
Endpoints:
  GET  /api/v1/topup/lookup?phone=&wallet=   — find recipient by phone
  POST /api/v1/topup/wallet-request           — requester initiates; FCM sent to recipient
  GET  /api/v1/topup/pending                  — recipient sees pending requests
  POST /api/v1/topup/wallet-approve           — recipient approves/rejects with PIN
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from passlib.hash import bcrypt as bc
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from config import settings
from database import get_db
from models.topup import WalletTopUpRequest
from models.transaction import Transaction
from models.user import User
from models.wallet import Wallet
from services.auth_service import get_current_user
from services.notification_service import send_notification
from services.wallet_service import generate_reference

router = APIRouter()

WALLET_LABELS: dict[str, str] = {
    "sadapay":   "SadaPay",
    "nayapay":   "NayaPay",
    "upaisa":    "Upaisa",
    "easypaisa": "EasyPaisa",
    "jazzcash":  "JazzCash",
    "sahulatpay": "SahulatPay",
}

REQUEST_TTL_MINUTES = 15


# ── GET /topup/lookup ─────────────────────────────────────────────────────────
@router.get("/lookup")
async def lookup_user(
    phone: str,
    wallet: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Find a registered SahulatPay user by phone for top-up."""
    user = (await db.execute(
        select(User).where(User.phone_number == phone)
    )).scalar_one_or_none()
    if not user:
        raise HTTPException(404, "No SahulatPay account linked to this number")
    if user.id == current_user.id:
        raise HTTPException(400, "You cannot request a top-up from yourself")
    return {
        "user_id":    str(user.id),
        "name":       user.full_name,
        "phone":      user.phone_number,
        "wallet":     wallet or "sahulatpay",
    }


# ── POST /topup/wallet-request ────────────────────────────────────────────────
class WalletTopUpRequestBody(BaseModel):
    recipient_phone: str
    wallet_type:     str
    amount:          float
    description:     Optional[str] = None


@router.post("/wallet-request")
async def create_wallet_topup_request(
    body: WalletTopUpRequestBody,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Requester initiates a pull-payment; FCM notification fired to recipient."""
    if body.amount <= 0:
        raise HTTPException(400, "Amount must be positive")
    if body.wallet_type not in WALLET_LABELS:
        raise HTTPException(400, f"Unsupported wallet type: {body.wallet_type}")

    recipient = (await db.execute(
        select(User).where(User.phone_number == body.recipient_phone)
    )).scalar_one_or_none()
    if not recipient:
        raise HTTPException(404, "Recipient not found on SahulatPay")
    if recipient.id == current_user.id:
        raise HTTPException(400, "Cannot request a top-up from yourself")

    wallet_label = WALLET_LABELS[body.wallet_type]
    req = WalletTopUpRequest(
        requester_id = current_user.id,
        recipient_id = recipient.id,
        wallet_type  = body.wallet_type,
        amount       = Decimal(str(body.amount)),
        description  = body.description,
        status       = "pending",
        expires_at   = datetime.now(timezone.utc) + timedelta(minutes=REQUEST_TTL_MINUTES),
    )
    db.add(req)
    await db.commit()
    await db.refresh(req)

    await send_notification(
        db       = db,
        user_id  = recipient.id,
        title    = "💰 Top-Up Request",
        body     = (
            f"{current_user.full_name} wants to top-up PKR {body.amount:,.0f} "
            f"from your {wallet_label} account"
        ),
        type     = "topup_request",
        data     = {
            "topup_request_id": str(req.id),
            "requester_name":   current_user.full_name,
            "amount":           str(body.amount),
            "wallet_type":      body.wallet_type,
            "deep_link":        f"topup/approve/{req.id}",
        },
    )

    return {
        "request_id": str(req.id),
        "status":     "pending",
        "expires_in": f"{REQUEST_TTL_MINUTES} minutes",
        "message":    f"Request sent to {recipient.full_name}. Waiting for approval.",
    }


# ── GET /topup/pending ────────────────────────────────────────────────────────
@router.get("/pending")
async def get_pending_requests(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Recipient sees all pending top-up requests (not yet expired)."""
    now = datetime.now(timezone.utc)
    rows = (await db.execute(
        select(WalletTopUpRequest).where(
            WalletTopUpRequest.recipient_id == current_user.id,
            WalletTopUpRequest.status == "pending",
            WalletTopUpRequest.expires_at > now,
        ).order_by(WalletTopUpRequest.created_at.desc())
    )).scalars().all()

    items = []
    for r in rows:
        req_user = (await db.execute(
            select(User).where(User.id == r.requester_id)
        )).scalar_one_or_none()
        items.append({
            "id":               str(r.id),
            "requester_name":   req_user.full_name if req_user else "Unknown",
            "requester_phone":  req_user.phone_number if req_user else "",
            "wallet_type":      r.wallet_type,
            "wallet_label":     WALLET_LABELS.get(r.wallet_type, r.wallet_type),
            "amount":           str(r.amount),
            "description":      r.description,
            "expires_at":       r.expires_at.isoformat(),
            "created_at":       r.created_at.isoformat(),
        })
    return {"requests": items, "count": len(items)}


# ── POST /topup/wallet-approve ────────────────────────────────────────────────
class ApproveTopUpBody(BaseModel):
    request_id: str
    pin:        str
    action:     str = "approve"   # approve | reject


@router.post("/wallet-approve")
async def approve_wallet_topup(
    body: ApproveTopUpBody,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Recipient approves (with PIN) or rejects the top-up request."""
    if body.action not in ("approve", "reject"):
        raise HTTPException(400, "action must be 'approve' or 'reject'")

    req = (await db.execute(
        select(WalletTopUpRequest).where(
            WalletTopUpRequest.id == UUID(body.request_id),
            WalletTopUpRequest.recipient_id == current_user.id,
        )
    )).scalar_one_or_none()
    if not req:
        raise HTTPException(404, "Request not found")
    if req.status != "pending":
        raise HTTPException(400, f"Request is already {req.status}")
    if datetime.now(timezone.utc) > req.expires_at:
        req.status = "expired"
        await db.commit()
        raise HTTPException(400, "Request has expired")

    # ── Reject path ────────────────────────────────────────────────────────────
    if body.action == "reject":
        req.status = "rejected"
        await db.commit()
        await send_notification(
            db      = db,
            user_id = req.requester_id,
            title   = "Top-Up Declined",
            body    = f"Your top-up request of PKR {req.amount:,.0f} was declined.",
            type    = "topup_result",
            data    = {"topup_request_id": str(req.id), "status": "rejected"},
        )
        return {"success": True, "status": "rejected"}

    # ── Approve path ───────────────────────────────────────────────────────────
    if not current_user.pin_hash:
        raise HTTPException(400, "PIN not set. Please set your transaction PIN first.")
    if not bc.verify(body.pin, current_user.pin_hash):
        raise HTTPException(400, "Incorrect PIN")

    recipient_wallet = (await db.execute(
        select(Wallet).where(Wallet.user_id == current_user.id)
    )).scalar_one_or_none()
    if not recipient_wallet:
        raise HTTPException(404, "Your wallet not found")
    if recipient_wallet.is_frozen:
        raise HTTPException(400, "Your wallet is frozen and cannot process transfers")
    if recipient_wallet.balance < req.amount:
        raise HTTPException(400, f"Insufficient balance. Available: PKR {recipient_wallet.balance:,.2f}")

    requester_wallet = (await db.execute(
        select(Wallet).where(Wallet.user_id == req.requester_id)
    )).scalar_one_or_none()
    if not requester_wallet:
        raise HTTPException(404, "Requester wallet not found")

    amount = req.amount
    recipient_wallet.balance  -= amount
    requester_wallet.balance  += amount

    ref = generate_reference()
    wallet_label = WALLET_LABELS.get(req.wallet_type, req.wallet_type)
    txn = Transaction(
        reference_number = ref,
        type             = "topup",
        amount           = amount,
        fee              = Decimal("0"),
        status           = "completed",
        sender_id        = current_user.id,
        recipient_id     = req.requester_id,
        purpose          = "TopUp",
        description      = req.description or f"Wallet top-up via {wallet_label}",
        tx_metadata      = {
            "wallet_type":       req.wallet_type,
            "topup_request_id":  str(req.id),
            "method":            "wallet_pull",
        },
    )
    db.add(txn)
    req.status = "approved"
    await db.commit()

    await send_notification(
        db      = db,
        user_id = req.requester_id,
        title   = "✅ Top-Up Successful!",
        body    = (
            f"PKR {amount:,.0f} added to your wallet from "
            f"{current_user.full_name}'s {wallet_label}"
        ),
        type    = "topup_result",
        data    = {
            "topup_request_id": str(req.id),
            "status":           "approved",
            "amount":           str(amount),
            "reference":        ref,
        },
    )

    return {
        "success":   True,
        "status":    "approved",
        "amount":    str(amount),
        "reference": ref,
        "message":   f"PKR {amount:,.0f} transferred successfully",
    }
