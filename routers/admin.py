"""Admin router — all dashboard APIs + audit logging. PROMPT 14."""
from datetime import datetime, timezone, timedelta, date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, func, update, desc, and_
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_db
from limiter import limiter
from models.ai import AiInsight, ChatSession
from models.card import VirtualCard
from models.finance import HighYieldDeposit, InsurancePolicy, Investment
from models.kyc import BusinessProfile, Document
from models.fraud import StrReport, WalletDebt
from models.other import AdminAction, FraudFlag, Notification, ZakatCalculation
from models.rewards import OfferTemplate, RewardOffer
from models.savings import SavingGoal
from models.social import BillSplit, SplitParticipant
from models.transaction import Transaction
from models.user import DeviceRegistry, User
from models.wallet import Wallet
from services.auth_service import get_current_user, hash_password
from services.kyc_service import get_signed_url
from services.notification_service import send_notification

router = APIRouter()


def _utcnow():
    return datetime.now(timezone.utc)


# ══════════════════════════════════════════════════════════════════════════════
# Auth guard — superuser JWT + X-Admin-Key header
# ══════════════════════════════════════════════════════════════════════════════
async def require_admin(
    request: Request,
    current_user: User = Depends(get_current_user),
    x_admin_key: Optional[str] = Header(default=None, alias="X-Admin-Key"),
) -> User:
    if not current_user.is_superuser:
        raise HTTPException(403, "Superuser access required.")
    if not settings.ADMIN_SECRET_KEY:
        raise HTTPException(503, "ADMIN_SECRET_KEY not configured on server.")
    if x_admin_key != settings.ADMIN_SECRET_KEY:
        raise HTTPException(403, "Invalid X-Admin-Key header.")
    return current_user


# ══════════════════════════════════════════════════════════════════════════════
# Audit logger — INSERT only, never UPDATE/DELETE
# ══════════════════════════════════════════════════════════════════════════════
async def log_admin_action(
    db: AsyncSession,
    admin_id: UUID,
    action_type: str,
    target_id: Optional[UUID] = None,
    target_type: Optional[str] = None,   # "user" | "transaction"
    reason: str = "",
    metadata: Optional[dict] = None,
) -> None:
    db.add(AdminAction(
        admin_id        = admin_id,
        action_type     = action_type,
        target_user_id  = target_id if target_type == "user" else None,
        target_txn_id   = target_id if target_type == "transaction" else None,
        reason          = reason,
        action_metadata = metadata or {},
    ))
    await db.commit()


# ══════════════════════════════════════════════════════════════════════════════
# GET /admin/key  — bootstrap (JWT-only, no X-Admin-Key needed)
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/key")
async def get_admin_key(current_user: User = Depends(get_current_user)):
    """Return ADMIN_SECRET_KEY to superuser for use as X-Admin-Key header."""
    if not current_user.is_superuser:
        raise HTTPException(403, "Superuser access required.")
    if not settings.ADMIN_SECRET_KEY:
        raise HTTPException(503, "ADMIN_SECRET_KEY not configured on server.")
    return {"admin_key": settings.ADMIN_SECRET_KEY}


# ══════════════════════════════════════════════════════════════════════════════
# GET /admin/dashboard
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/dashboard")
async def dashboard(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """High-level platform stats."""
    total_users    = (await db.execute(select(func.count(User.id)))).scalar() or 0
    active_users   = (await db.execute(select(func.count(User.id)).where(User.is_active == True))).scalar() or 0
    locked_users   = (await db.execute(select(func.count(User.id)).where(User.is_locked == True))).scalar() or 0
    kyc_queue      = (await db.execute(select(func.count(Document.id)).where(User.cnic_verified == False).join(User, Document.user_id == User.id))).scalar() or 0
    total_txns     = (await db.execute(select(func.count(Transaction.id)))).scalar() or 0
    total_volume   = (await db.execute(select(func.coalesce(func.sum(Transaction.amount), 0)).where(Transaction.status == "completed"))).scalar() or 0
    open_fraud     = (await db.execute(select(func.count(FraudFlag.id)).where(FraudFlag.is_resolved == False))).scalar() or 0
    pending_biz    = (await db.execute(select(func.count(BusinessProfile.id)).where(BusinessProfile.verification_status == "under_review"))).scalar() or 0
    unread_notifs  = (await db.execute(select(func.count(Notification.id)).where(Notification.is_read == False))).scalar() or 0

    return {
        "total_users":          total_users,
        "active_users":         active_users,
        "locked_users":         locked_users,
        "kyc_queue":            kyc_queue,
        "total_transactions":   total_txns,
        "total_volume_pkr":     float(total_volume),
        "open_fraud_alerts":    open_fraud,
        "pending_business":     pending_biz,
        "unread_notifications": unread_notifs,
        "generated_at":         _utcnow().isoformat(),
    }


# ══════════════════════════════════════════════════════════════════════════════
# USERS
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/users")
async def list_users(
    page: int = 1, per_page: int = 25,
    search: Optional[str] = None,
    tier: Optional[int]   = None,
    is_active: Optional[bool] = None,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    q = select(User)
    if search:
        q = q.where(User.full_name.ilike(f"%{search}%") | User.phone_number.ilike(f"%{search}%"))
    if tier is not None:
        q = q.where(User.verification_tier == tier)
    if is_active is not None:
        q = q.where(User.is_active == is_active)

    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar() or 0
    users = (await db.execute(q.order_by(desc(User.created_at)).offset((page - 1) * per_page).limit(per_page))).scalars().all()

    return {
        "users": [
            {
                "id": u.id, "phone_number": u.phone_number, "full_name": u.full_name,
                "email": u.email, "verification_tier": u.verification_tier,
                "account_type": u.account_type, "is_active": u.is_active,
                "is_locked": u.is_locked, "is_flagged": u.is_flagged,
                "created_at": u.created_at.isoformat() if u.created_at else None,
            }
            for u in users
        ],
        "total": total, "page": page, "per_page": per_page, "has_next": (page * per_page) < total,
    }


@router.get("/users/{user_id}")
async def get_user(
    user_id: UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        raise HTTPException(404, "User not found.")
    wallet = (await db.execute(select(Wallet).where(Wallet.user_id == user_id))).scalar_one_or_none()
    await log_admin_action(db, admin.id, "view_user", user_id, "user", "Admin viewed user profile")
    return {
        "id": user.id, "phone_number": user.phone_number, "full_name": user.full_name,
        "email": user.email, "country": user.country, "age": user.age,
        "account_type": user.account_type, "verification_tier": user.verification_tier,
        "is_active": user.is_active, "is_locked": user.is_locked, "is_superuser": user.is_superuser,
        "is_flagged": user.is_flagged, "risk_score": user.risk_score,
        "cnic_verified": user.cnic_verified, "biometric_verified": user.biometric_verified,
        "fingerprint_verified": user.fingerprint_verified, "nadra_verified": user.nadra_verified,
        "wallet_balance": float(wallet.balance) if wallet else 0.0,
        "wallet_frozen": wallet.is_frozen if wallet else False,
        "member_since": user.member_since.isoformat() if user.member_since else None,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
    }


class UserActionRequest(BaseModel):
    reason: str = Field(..., min_length=5)


@router.post("/users/{user_id}/block")
async def block_user(user_id: UUID, body: UserActionRequest, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        raise HTTPException(404, "User not found.")
    user.is_locked = True
    user.is_active = False
    await db.commit()
    await log_admin_action(db, admin.id, "block_user", user_id, "user", body.reason)
    await send_notification(db, user_id, "Account Suspended", "Your account has been suspended. Contact support.", "security")
    return {"message": f"User {user_id} blocked."}


@router.post("/users/{user_id}/unblock")
async def unblock_user(user_id: UUID, body: UserActionRequest, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        raise HTTPException(404, "User not found.")
    user.is_locked = False
    user.is_active = True
    user.login_attempts = 0
    await db.commit()
    await log_admin_action(db, admin.id, "unblock_user", user_id, "user", body.reason)
    await send_notification(db, user_id, "Account Reinstated", "Your account has been reinstated.", "security")
    return {"message": f"User {user_id} unblocked."}


class TierOverrideRequest(BaseModel):
    tier:   int = Field(..., ge=0, le=4)
    reason: str = Field(..., min_length=5)


@router.patch("/users/{user_id}/tier")
async def override_tier(user_id: UUID, body: TierOverrideRequest, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        raise HTTPException(404, "User not found.")
    user.verification_tier = body.tier
    await db.commit()
    await log_admin_action(db, admin.id, "tier_override", user_id, "user", body.reason, {"new_tier": body.tier})
    return {"message": f"User tier set to {body.tier}.", "user_id": user_id}


# ══════════════════════════════════════════════════════════════════════════════
# KYC — approve / reject + signed document URLs
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/kyc/queue")
async def kyc_queue(
    page: int = 1, per_page: int = 20,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    docs = (await db.execute(
        select(Document)
        .join(User, Document.user_id == User.id)
        .where(User.cnic_verified == False)
        .order_by(Document.uploaded_at).offset((page - 1) * per_page).limit(per_page)
    )).scalars().all()
    return {
        "documents": [
            {
                "id": d.id, "user_id": d.user_id, "document_type": d.document_type,
                "uploaded_at": d.uploaded_at.isoformat() if d.uploaded_at else None,
                "signed_url": get_signed_url(d.cloudinary_public_id),
            }
            for d in docs
        ]
    }


class KycDecisionRequest(BaseModel):
    reason: str = Field(default="")


@router.post("/kyc/{doc_id}/approve")
async def approve_kyc(doc_id: UUID, body: KycDecisionRequest, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    doc = (await db.execute(select(Document).where(Document.id == doc_id))).scalar_one_or_none()
    if not doc:
        raise HTTPException(404, "Document not found.")
    user = (await db.execute(select(User).where(User.id == doc.user_id))).scalar_one_or_none()
    if user:
        if doc.document_type in ("cnic_front", "cnic_back"):
            user.cnic_verified = True
            user.verification_tier = max(user.verification_tier or 0, 2)
        elif doc.document_type == "liveness_selfie":
            user.biometric_verified = True
            user.verification_tier = max(user.verification_tier or 0, 3)
    await db.commit()
    await log_admin_action(db, admin.id, "approve_kyc", doc.user_id, "user", body.reason or "KYC approved", {"doc_id": str(doc_id)})
    await send_notification(db, doc.user_id, "KYC Approved ✅", f"Your {doc.document_type.replace('_', ' ')} has been verified.", "system")
    return {"message": "KYC document approved.", "doc_id": doc_id}


@router.post("/kyc/{doc_id}/reject")
async def reject_kyc(doc_id: UUID, body: UserActionRequest, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    doc = (await db.execute(select(Document).where(Document.id == doc_id))).scalar_one_or_none()
    if not doc:
        raise HTTPException(404, "Document not found.")
    await log_admin_action(db, admin.id, "reject_kyc", doc.user_id, "user", body.reason, {"doc_id": str(doc_id)})
    await send_notification(db, doc.user_id, "KYC Rejected ❌", f"Your document was rejected: {body.reason}", "system")
    return {"message": "KYC document rejected.", "doc_id": doc_id}


# ══════════════════════════════════════════════════════════════════════════════
# TRANSACTIONS — flag + reverse (SELECT FOR UPDATE)
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/transactions")
async def list_transactions(
    page: int = 1, per_page: int = 25, status: Optional[str] = None, is_flagged: Optional[bool] = None,
    admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db),
):
    q = select(Transaction)
    if status:
        q = q.where(Transaction.status == status)
    if is_flagged is not None:
        q = q.where(Transaction.is_flagged == is_flagged)
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar() or 0
    txns  = (await db.execute(q.order_by(desc(Transaction.created_at)).offset((page - 1) * per_page).limit(per_page))).scalars().all()
    return {
        "transactions": [
            {"id": t.id, "reference_number": t.reference_number, "type": t.type,
             "amount": float(t.amount), "status": t.status, "purpose": t.purpose,
             "sender_id": t.sender_id, "recipient_id": t.recipient_id,
             "is_flagged": t.is_flagged, "created_at": t.created_at.isoformat() if t.created_at else None}
            for t in txns
        ],
        "total": total, "page": page,
    }


class FlagTxnRequest(BaseModel):
    reason: str = Field(..., min_length=5)
    severity: str = Field(default="medium", pattern="^(low|medium|high|critical)$")


@router.post("/transactions/{txn_id}/flag")
async def flag_transaction(txn_id: UUID, body: FlagTxnRequest, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    txn = (await db.execute(select(Transaction).where(Transaction.id == txn_id))).scalar_one_or_none()
    if not txn:
        raise HTTPException(404, "Transaction not found.")
    txn.is_flagged  = True
    txn.flag_reason = body.reason
    txn.flagged_by  = admin.id
    txn.flagged_at  = _utcnow()
    db.add(FraudFlag(
        user_id=txn.sender_id or txn.recipient_id,
        transaction_id=txn.id,
        reason=body.reason,
        severity=body.severity,
    ))
    await db.commit()
    await log_admin_action(db, admin.id, "flag_transaction", txn_id, "transaction", body.reason)
    return {"message": "Transaction flagged.", "txn_id": txn_id}


@router.post("/transactions/{txn_id}/reverse")
async def reverse_transaction(txn_id: UUID, body: UserActionRequest, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Reverse a completed transaction. SELECT FOR UPDATE prevents double-reversal.
    Partial reversal if recipient has insufficient balance — creates WalletDebt for shortfall.
    Wallet balance will NEVER go negative.
    """
    txn = (await db.execute(
        select(Transaction).where(Transaction.id == txn_id).with_for_update()
    )).scalar_one_or_none()
    if not txn:
        raise HTTPException(404, "Transaction not found.")
    if txn.status == "reversed":
        raise HTTPException(409, "Transaction already reversed.")
    if txn.status not in ("completed", "under_review"):
        raise HTTPException(400, f"Cannot reverse a transaction with status '{txn.status}'.")

    txn.status = "reversed"
    is_partial  = False
    shortfall   = Decimal("0")

    recv_wallet = None
    if txn.recipient_id:
        recv_wallet = (await db.execute(
            select(Wallet).where(Wallet.user_id == txn.recipient_id).with_for_update()
        )).scalar_one_or_none()

    sender_wallet = None
    if txn.sender_id:
        sender_wallet = (await db.execute(
            select(Wallet).where(Wallet.user_id == txn.sender_id).with_for_update()
        )).scalar_one_or_none()

    if recv_wallet is not None:
        recv_balance = recv_wallet.balance or Decimal("0")
        if recv_balance >= txn.amount:
            recv_wallet.balance  = recv_balance - txn.amount
            if sender_wallet:
                sender_wallet.balance = (sender_wallet.balance or Decimal("0")) + txn.amount
        else:
            available  = recv_balance
            shortfall  = txn.amount - available
            is_partial = True
            recv_wallet.balance = Decimal("0")
            if sender_wallet:
                sender_wallet.balance = (sender_wallet.balance or Decimal("0")) + available
            db.add(WalletDebt(
                user_id=txn.recipient_id,
                amount_pkr=shortfall,
                reason="fraud_reversal",
                source_transaction_id=txn.id,
                due_at=_utcnow() + timedelta(days=30),
            ))
    elif sender_wallet:
        sender_wallet.balance = (sender_wallet.balance or Decimal("0")) + txn.amount

    # ── Mark reviewed_by on the transaction ───────────────────────────────────
    txn.reviewed_by = admin.id

    # ── Auto-resolve linked FraudFlag (confirms fraud) ────────────────────────
    linked_flag = (await db.execute(
        select(FraudFlag)
        .where(FraudFlag.transaction_id == txn_id, FraudFlag.is_resolved == False)
        .order_by(desc(FraudFlag.created_at))
        .limit(1)
    )).scalar_one_or_none()
    if linked_flag:
        linked_flag.is_resolved     = True
        linked_flag.resolved_by     = admin.id
        linked_flag.resolved_at     = _utcnow()
        linked_flag.resolution_note = f"Transaction reversed by admin. Reason: {body.reason}"

    # ── Auto-resolve linked TransactionDispute (if any) ───────────────────────
    from models.fraud import TransactionDispute
    linked_dispute = (await db.execute(
        select(TransactionDispute)
        .where(TransactionDispute.transaction_id == txn_id,
               TransactionDispute.status.in_(["open", "under_review"]))
    )).scalar_one_or_none()
    if linked_dispute:
        linked_dispute.status          = "resolved"
        linked_dispute.resolved_at     = _utcnow()
        linked_dispute.resolved_by     = admin.id
        linked_dispute.resolution_note = f"Transaction reversed. Refund issued. Reason: {body.reason}"

    await db.commit()
    await log_admin_action(db, admin.id, "reverse_transaction", txn_id, "transaction", body.reason)

    # ── Notify sender about refund ────────────────────────────────────────────
    if txn.sender_id:
        refunded_amount = float(txn.amount) if not is_partial else float(available)
        await send_notification(
            db, txn.sender_id,
            "Transaction Reversed — Refund Issued",
            f"PKR {refunded_amount:,.2f} has been refunded to your wallet following a transaction review.",
            "security",
            {"transaction_id": str(txn_id), "refund_amount": str(refunded_amount)},
        )

    msg = (
        f"Partial reversal. PKR {float(available):,.2f} refunded to sender. "
        f"WalletDebt of PKR {float(shortfall):,.2f} created for recipient (due in 30 days)."
        if is_partial else
        "Transaction fully reversed and sender refunded."
    )
    return {"message": msg, "txn_id": txn_id, "partial": is_partial, "shortfall": float(shortfall)}


# ══════════════════════════════════════════════════════════════════════════════
# FRAUD ALERTS
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/fraud-alerts")
async def list_fraud_alerts(
    resolved: bool = False, page: int = 1, per_page: int = 20,
    admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db),
):
    flags = (await db.execute(
        select(FraudFlag).where(FraudFlag.is_resolved == resolved)
        .order_by(desc(FraudFlag.created_at)).offset((page - 1) * per_page).limit(per_page)
    )).scalars().all()
    return {
        "flags": [
            {"id": f.id, "user_id": f.user_id, "transaction_id": f.transaction_id,
             "reason": f.reason, "severity": f.severity, "is_resolved": f.is_resolved,
             "created_at": f.created_at.isoformat() if f.created_at else None}
            for f in flags
        ]
    }


class ResolveFraudRequest(BaseModel):
    resolution_note: str = Field(..., min_length=5)


@router.post("/fraud-alerts/{flag_id}/resolve")
async def resolve_fraud(flag_id: UUID, body: ResolveFraudRequest, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    flag = (await db.execute(select(FraudFlag).where(FraudFlag.id == flag_id))).scalar_one_or_none()
    if not flag:
        raise HTTPException(404, "Fraud flag not found.")
    flag.is_resolved     = True
    flag.resolved_by     = admin.id
    flag.resolved_at     = _utcnow()
    flag.resolution_note = body.resolution_note

    txn_completed = False
    if flag.transaction_id:
        txn = (await db.execute(
            select(Transaction).where(Transaction.id == flag.transaction_id)
        )).scalar_one_or_none()
        if txn and txn.status == "under_review":
            hold_still_valid = txn.hold_expires_at and txn.hold_expires_at > _utcnow()
            if hold_still_valid:
                txn.status       = "completed"
                txn.completed_at = _utcnow()
                txn.reviewed_by  = admin.id
                if txn.recipient_id:
                    recv_wallet = (await db.execute(
                        select(Wallet).where(Wallet.user_id == txn.recipient_id)
                    )).scalar_one_or_none()
                    if recv_wallet:
                        recv_wallet.balance = (recv_wallet.balance or Decimal("0")) + txn.amount
                txn_completed = True

    await db.commit()
    await log_admin_action(db, admin.id, "resolve_fraud", flag.user_id, "user", body.resolution_note, {"flag_id": str(flag_id)})
    msg = "Fraud alert resolved."
    if txn_completed:
        msg += " Held transaction completed and recipient credited."
    return {"message": msg, "flag_id": flag_id, "transaction_completed": txn_completed}


# ══════════════════════════════════════════════════════════════════════════════
# CARDS — list all, block/unblock, delivery status
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/cards")
async def list_all_cards(
    page: int = 1, per_page: int = 25, status: Optional[str] = None,
    admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db),
):
    q = select(VirtualCard)
    if status:
        q = q.where(VirtualCard.status == status)
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar() or 0
    cards = (await db.execute(q.order_by(desc(VirtualCard.created_at)).offset((page - 1) * per_page).limit(per_page))).scalars().all()
    return {
        "cards": [
            {"id": c.id, "user_id": c.user_id, "card_type": c.card_type, "status": c.status,
             "last_four": c.last_four, "network": c.card_network, "delivery_status": c.delivery_status,
             "created_at": c.created_at.isoformat() if c.created_at else None}
            for c in cards
        ],
        "total": total,
    }


@router.post("/cards/{card_id}/block")
async def admin_block_card(card_id: UUID, body: UserActionRequest, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    card = (await db.execute(select(VirtualCard).where(VirtualCard.id == card_id))).scalar_one_or_none()
    if not card:
        raise HTTPException(404, "Card not found.")
    card.status = "blocked"
    await db.commit()
    await log_admin_action(db, admin.id, "block_card", card.user_id, "user", body.reason, {"card_id": str(card_id)})
    await send_notification(db, card.user_id, "Card Blocked", f"Your card ending {card.last_four} has been blocked.", "security")
    return {"message": f"Card {card_id} blocked."}


@router.post("/cards/{card_id}/unblock")
async def admin_unblock_card(card_id: UUID, body: UserActionRequest, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    card = (await db.execute(select(VirtualCard).where(VirtualCard.id == card_id))).scalar_one_or_none()
    if not card:
        raise HTTPException(404, "Card not found.")
    card.status = "active"
    await db.commit()
    await log_admin_action(db, admin.id, "block_card", card.user_id, "user", body.reason, {"card_id": str(card_id), "action": "unblock"})
    await send_notification(db, card.user_id, "Card Unblocked ✅", f"Your card ending {card.last_four} is active again.", "security")
    return {"message": f"Card {card_id} unblocked."}


class DeliveryStatusRequest(BaseModel):
    delivery_status: str = Field(..., pattern="^(processing|dispatched|out_for_delivery|delivered)$")
    reason:          str = Field(default="")


@router.patch("/cards/{card_id}/delivery-status")
async def update_delivery_status(card_id: UUID, body: DeliveryStatusRequest, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    card = (await db.execute(select(VirtualCard).where(VirtualCard.id == card_id))).scalar_one_or_none()
    if not card:
        raise HTTPException(404, "Card not found.")
    card.delivery_status = body.delivery_status
    await db.commit()
    await log_admin_action(db, admin.id, "update_delivery_status", card.user_id, "user", body.reason, {"status": body.delivery_status})
    status_msgs = {
        "processing":       "Your card is being processed.",
        "dispatched":       "Your card has been dispatched.",
        "out_for_delivery": "Your card is out for delivery today!",
        "delivered":        "Your card has been delivered. Activate it in the app.",
    }
    await send_notification(db, card.user_id, "Card Update 💳", status_msgs.get(body.delivery_status, "Card status updated."), "system")
    return {"message": f"Card delivery status updated to '{body.delivery_status}'.", "card_id": card_id}


# ══════════════════════════════════════════════════════════════════════════════
# BUSINESS PROFILES
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/business/pending")
async def list_pending_business(admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    profiles = (await db.execute(
        select(BusinessProfile).where(BusinessProfile.verification_status == "under_review")
        .order_by(BusinessProfile.submitted_at)
    )).scalars().all()
    return {
        "profiles": [
            {"id": p.id, "user_id": p.user_id, "business_name": p.business_name,
             "business_type": p.business_type, "registration_number": p.registration_number,
             "ai_analysis_result": p.ai_analysis_result, "submitted_at": p.submitted_at.isoformat() if p.submitted_at else None}
            for p in profiles
        ]
    }


@router.post("/business/{profile_id}/approve")
async def approve_business(profile_id: UUID, body: KycDecisionRequest, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    profile = (await db.execute(select(BusinessProfile).where(BusinessProfile.id == profile_id))).scalar_one_or_none()
    if not profile:
        raise HTTPException(404, "Business profile not found.")
    profile.verification_status = "verified"
    await db.commit()
    await log_admin_action(db, admin.id, "approve_business", profile.user_id, "user", body.reason or "Approved")
    await send_notification(db, profile.user_id, "Business Verified ✅", f"{profile.business_name} has been verified.", "system")
    return {"message": "Business profile approved."}


@router.post("/business/{profile_id}/reject")
async def reject_business(profile_id: UUID, body: UserActionRequest, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    profile = (await db.execute(select(BusinessProfile).where(BusinessProfile.id == profile_id))).scalar_one_or_none()
    if not profile:
        raise HTTPException(404, "Business profile not found.")
    profile.verification_status = "rejected"
    await db.commit()
    await log_admin_action(db, admin.id, "reject_business", profile.user_id, "user", body.reason)
    await send_notification(db, profile.user_id, "Business Rejected ❌", f"Your business documents were rejected: {body.reason}", "system")
    return {"message": "Business profile rejected."}


# ══════════════════════════════════════════════════════════════════════════════
# OFFER TEMPLATES + ASSIGN
# ══════════════════════════════════════════════════════════════════════════════
class OfferTemplateCreate(BaseModel):
    title:         str            = Field(..., min_length=3, max_length=255)
    description:   Optional[str] = None
    category:      str            = Field(..., min_length=2)
    target_amount: Decimal        = Field(..., gt=0)
    reward_amount: Decimal        = Field(..., gt=0)
    duration_days: int            = Field(default=30, ge=1)


@router.post("/offers/templates", status_code=201)
async def create_offer_template(body: OfferTemplateCreate, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    tmpl = OfferTemplate(
        title=body.title, description=body.description, category=body.category,
        target_amount=body.target_amount, reward_amount=body.reward_amount,
        duration_days=body.duration_days, created_by=admin.id,
    )
    db.add(tmpl)
    await db.commit()
    await db.refresh(tmpl)
    await log_admin_action(db, admin.id, "create_offer_template", metadata={"template_id": str(tmpl.id)})
    return {"message": "Offer template created.", "template_id": tmpl.id}


@router.get("/offers/templates")
async def list_offer_templates(admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    templates = (await db.execute(select(OfferTemplate).where(OfferTemplate.is_active == True))).scalars().all()
    return {
        "templates": [
            {"id": t.id, "title": t.title, "category": t.category,
             "target_amount": float(t.target_amount), "reward_amount": float(t.reward_amount),
             "duration_days": t.duration_days}
            for t in templates
        ]
    }


class AssignOfferRequest(BaseModel):
    user_id:     UUID
    template_id: UUID
    reason:      str = Field(default="Admin assigned offer")


@router.post("/offers/assign", status_code=201)
async def assign_offer(body: AssignOfferRequest, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    from datetime import timedelta
    tmpl = (await db.execute(select(OfferTemplate).where(OfferTemplate.id == body.template_id))).scalar_one_or_none()
    if not tmpl:
        raise HTTPException(404, "Offer template not found.")
    offer = RewardOffer(
        user_id=body.user_id, template_id=body.template_id,
        title=tmpl.title, category=tmpl.category,
        target_amount=tmpl.target_amount, reward_amount=tmpl.reward_amount,
        expires_at=_utcnow() + timedelta(days=tmpl.duration_days),
    )
    db.add(offer)
    await db.commit()
    await log_admin_action(db, admin.id, "assign_offer", body.user_id, "user", body.reason, {"template_id": str(body.template_id)})
    await send_notification(db, body.user_id, "New Offer 🎁", f"You have a new offer: {tmpl.title}!", "rewards")
    return {"message": "Offer assigned.", "offer_id": offer.id}


# ══════════════════════════════════════════════════════════════════════════════
# BROADCAST NOTIFICATIONS
# ══════════════════════════════════════════════════════════════════════════════
class BroadcastRequest(BaseModel):
    title:    str            = Field(..., min_length=3)
    body:     str            = Field(..., min_length=5)
    type:     str            = Field(default="system")
    user_ids: Optional[list[UUID]] = None   # None = all active users


@router.post("/notifications/broadcast", status_code=201)
@limiter.limit("10/hour")
async def broadcast_notification(
    request: Request,
    body: BroadcastRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    if body.user_ids:
        users = (await db.execute(select(User).where(User.id.in_(body.user_ids)))).scalars().all()
    else:
        users = (await db.execute(select(User).where(User.is_active == True))).scalars().all()

    count = 0
    for u in users:
        await send_notification(db, u.id, body.title, body.body, body.type)
        count += 1

    await log_admin_action(db, admin.id, "broadcast_notification", metadata={"title": body.title, "recipient_count": count})
    return {"message": f"Notification sent to {count} users.", "count": count}


# ══════════════════════════════════════════════════════════════════════════════
# INSURANCE
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/insurance")
async def list_all_insurance(page: int = 1, per_page: int = 25, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    policies = (await db.execute(
        select(InsurancePolicy).order_by(desc(InsurancePolicy.activated_at))
        .offset((page - 1) * per_page).limit(per_page)
    )).scalars().all()
    total = (await db.execute(select(func.count(InsurancePolicy.id)))).scalar() or 0
    return {
        "policies": [
            {"id": p.id, "user_id": p.user_id, "policy_type": p.policy_type, "plan_name": p.plan_name,
             "premium": float(p.premium), "coverage": float(p.coverage), "status": p.status,
             "expires_at": p.expires_at.isoformat() if p.expires_at else None}
            for p in policies
        ],
        "total": total,
    }


# ══════════════════════════════════════════════════════════════════════════════
# AUDIT LOG (read-only)
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/audit-log")
async def audit_log(
    page: int = 1, per_page: int = 25, action_type: Optional[str] = None,
    admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db),
):
    q = select(AdminAction)
    if action_type:
        q = q.where(AdminAction.action_type == action_type)
    total  = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar() or 0
    actions = (await db.execute(q.order_by(desc(AdminAction.created_at)).offset((page - 1) * per_page).limit(per_page))).scalars().all()
    return {
        "actions": [
            {"id": a.id, "admin_id": a.admin_id, "action_type": a.action_type,
             "target_user_id": a.target_user_id, "target_txn_id": a.target_txn_id,
             "reason": a.reason, "action_metadata": a.action_metadata,
             "created_at": a.created_at.isoformat() if a.created_at else None}
            for a in actions
        ],
        "total": total, "page": page,
    }


# ══════════════════════════════════════════════════════════════════════════════
# SAVINGS OVERVIEW
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/savings/overview")
async def savings_overview(admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    total_goals      = (await db.execute(select(func.count(SavingGoal.id)))).scalar() or 0
    active_goals     = (await db.execute(select(func.count(SavingGoal.id)).where(SavingGoal.is_completed == False))).scalar() or 0
    completed_goals  = (await db.execute(select(func.count(SavingGoal.id)).where(SavingGoal.is_completed == True))).scalar() or 0
    auto_deduct_on   = (await db.execute(select(func.count(SavingGoal.id)).where(SavingGoal.auto_deduct_enabled == True))).scalar() or 0
    total_saved      = (await db.execute(select(func.coalesce(func.sum(SavingGoal.saved_amount), 0)))).scalar() or 0
    return {
        "total_goals": total_goals, "active_goals": active_goals,
        "completed_goals": completed_goals, "auto_deduct_enabled": auto_deduct_on,
        "total_saved_pkr": float(total_saved),
    }


# ══════════════════════════════════════════════════════════════════════════════
# SPLITS — list all, flag suspicious
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/splits")
async def list_all_splits(page: int = 1, per_page: int = 25, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    splits = (await db.execute(
        select(BillSplit).order_by(desc(BillSplit.created_at)).offset((page - 1) * per_page).limit(per_page)
    )).scalars().all()
    total = (await db.execute(select(func.count(BillSplit.id)))).scalar() or 0
    return {
        "splits": [
            {"id": s.id, "creator_id": s.creator_id, "title": s.title,
             "total_amount": float(s.total_amount), "split_type": s.split_type,
             "status": s.status, "created_at": s.created_at.isoformat() if s.created_at else None}
            for s in splits
        ],
        "total": total,
    }


@router.post("/splits/{split_id}/flag")
async def flag_split(split_id: UUID, body: FlagTxnRequest, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    split = (await db.execute(select(BillSplit).where(BillSplit.id == split_id))).scalar_one_or_none()
    if not split:
        raise HTTPException(404, "Split not found.")
    db.add(FraudFlag(user_id=split.creator_id, reason=body.reason, severity=body.severity))
    await db.commit()
    await log_admin_action(db, admin.id, "flag_split", split.creator_id, "user", body.reason, {"split_id": str(split_id)})
    return {"message": "Split flagged.", "split_id": split_id}


# ══════════════════════════════════════════════════════════════════════════════
# HIGH-YIELD DEPOSITS
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/high-yield")
async def list_high_yield(
    maturing_days: int = 7,
    admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db),
):
    """List all deposits. Flags ones maturing within `maturing_days`."""
    deposits = (await db.execute(select(HighYieldDeposit).where(HighYieldDeposit.status == "active").order_by(HighYieldDeposit.maturity_date))).scalars().all()
    threshold = date.today() + timedelta(days=maturing_days)
    return {
        "deposits": [
            {
                "id": d.id, "user_id": d.user_id, "amount": float(d.amount),
                "interest_rate": float(d.interest_rate), "period_days": d.period_days,
                "maturity_date": d.maturity_date.isoformat() if d.maturity_date else None,
                "expected_interest": float(d.expected_interest or 0),
                "maturing_soon": d.maturity_date and d.maturity_date <= threshold,
            }
            for d in deposits
        ],
        "total": len(deposits),
        "maturing_soon_count": sum(1 for d in deposits if d.maturity_date and d.maturity_date <= threshold),
    }


# ══════════════════════════════════════════════════════════════════════════════
# ZAKAT STATS (aggregate only)
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/zakat/stats")
async def zakat_stats(admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    total_calcs   = (await db.execute(select(func.count(ZakatCalculation.id)))).scalar() or 0
    paid_count    = (await db.execute(select(func.count(ZakatCalculation.id)).where(ZakatCalculation.is_paid == True))).scalar() or 0
    total_paid    = (await db.execute(select(func.coalesce(func.sum(ZakatCalculation.zakat_due_pkr), 0)).where(ZakatCalculation.is_paid == True))).scalar() or 0
    return {
        "total_calculations": total_calcs,
        "paid_count":         paid_count,
        "unpaid_count":       total_calcs - paid_count,
        "total_zakat_paid_pkr": float(total_paid),
    }


# ══════════════════════════════════════════════════════════════════════════════
# FRAUD FEED — live auto-flagged transactions
# ══════════════════════════════════════════════════════════════════════════════
_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


@router.get("/fraud-feed")
async def fraud_feed(
    severity: Optional[str] = None,
    page: int = 1,
    per_page: int = 25,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Auto-flagged transactions from last 24 hours, sorted by severity."""
    from datetime import timedelta
    cutoff = _utcnow() - timedelta(hours=24)
    q = (
        select(FraudFlag)
        .where(FraudFlag.is_resolved == False, FraudFlag.created_at >= cutoff)
    )
    if severity:
        allowed = [s.strip() for s in severity.split(",")]
        q = q.where(FraudFlag.severity.in_(allowed))

    flags = (await db.execute(
        q.order_by(FraudFlag.created_at.desc()).offset((page - 1) * per_page).limit(per_page)
    )).scalars().all()

    rows = []
    for f in flags:
        txn = None
        user_risk = None
        if f.transaction_id:
            txn = (await db.execute(
                select(Transaction).where(Transaction.id == f.transaction_id)
            )).scalar_one_or_none()
        if f.user_id:
            u = (await db.execute(select(User).where(User.id == f.user_id))).scalar_one_or_none()
            if u:
                user_risk = u.risk_score
        rows.append({
            "flag_id":              f.id,
            "user_id":             f.user_id,
            "user_risk_score":     user_risk,
            "transaction_id":      f.transaction_id,
            "amount":              float(txn.amount) if txn else None,
            "fraud_score":         txn.fraud_score   if txn else None,
            "deepseek_score":      txn.deepseek_score if txn else None,
            "deepseek_recommendation": txn.deepseek_recommendation if txn else None,
            "txn_status":          txn.status        if txn else None,
            "hold_expires_at":     txn.hold_expires_at.isoformat() if txn and txn.hold_expires_at else None,
            "severity":            f.severity,
            "reason":              f.reason,
            "created_at":          f.created_at.isoformat() if f.created_at else None,
        })

    rows.sort(key=lambda r: _SEVERITY_ORDER.get(r["severity"], 9))
    return {"feed": rows, "count": len(rows), "page": page}


# ══════════════════════════════════════════════════════════════════════════════
# STR REPORTS — Suspicious Transaction Reports
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/str-reports")
async def list_str_reports(
    status: Optional[str] = None,
    page: int = 1,
    per_page: int = 20,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    q = select(StrReport)
    if status:
        q = q.where(StrReport.status == status)
    reports = (await db.execute(
        q.order_by(desc(StrReport.generated_at)).offset((page - 1) * per_page).limit(per_page)
    )).scalars().all()
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar() or 0
    return {
        "reports": [
            {
                "id":             r.id,
                "user_id":        r.user_id,
                "transaction_id": r.transaction_id,
                "report_type":    r.report_type,
                "amount_pkr":     float(r.amount_pkr),
                "status":         r.status,
                "generated_at":   r.generated_at.isoformat() if r.generated_at else None,
                "reviewed_by":    r.reviewed_by,
                "submitted_at":   r.submitted_at.isoformat() if r.submitted_at else None,
                "submission_ref": r.submission_ref,
            }
            for r in reports
        ],
        "total": total, "page": page,
    }


class StrReviewRequest(BaseModel):
    narrative: str = Field(..., min_length=20)


@router.post("/str-reports/{report_id}/review")
async def review_str_report(
    report_id: UUID,
    body: StrReviewRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    report = (await db.execute(select(StrReport).where(StrReport.id == report_id))).scalar_one_or_none()
    if not report:
        raise HTTPException(404, "STR report not found.")
    if report.status == "submitted":
        raise HTTPException(409, "Cannot edit a submitted report.")
    report.ai_narrative = body.narrative
    report.status       = "reviewed"
    report.reviewed_by  = admin.id
    await db.commit()
    await log_admin_action(db, admin.id, "str_review", report.user_id, "user", "STR narrative reviewed", {"report_id": str(report_id)})
    return {"message": "STR report reviewed.", "report_id": report_id}


class StrSubmitRequest(BaseModel):
    submission_ref: str = Field(..., min_length=3)


@router.post("/str-reports/{report_id}/submit")
async def submit_str_report(
    report_id: UUID,
    body: StrSubmitRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    report = (await db.execute(select(StrReport).where(StrReport.id == report_id))).scalar_one_or_none()
    if not report:
        raise HTTPException(404, "STR report not found.")
    if report.status == "submitted":
        raise HTTPException(409, "Report already submitted.")
    report.status         = "submitted"
    report.submitted_at   = _utcnow()
    report.submission_ref = body.submission_ref
    await db.commit()
    await log_admin_action(db, admin.id, "str_submit", report.user_id, "user", f"STR submitted: {body.submission_ref}", {"report_id": str(report_id)})
    return {"message": "STR report marked as submitted.", "report_id": report_id, "submission_ref": body.submission_ref}


# ══════════════════════════════════════════════════════════════════════════════
# AI MONITOR
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/ai/monitor")
async def ai_monitor(admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    total_sessions  = (await db.execute(select(func.count(ChatSession.id)))).scalar() or 0
    total_insights  = (await db.execute(select(func.count(AiInsight.id)))).scalar() or 0

    # Health score distribution
    score_buckets = {"critical": 0, "poor": 0, "fair": 0, "good": 0, "excellent": 0}
    insights = (await db.execute(select(AiInsight.health_score, AiInsight.health_label))).all()
    for row in insights:
        label = (row[1] or "").lower()
        if label in score_buckets:
            score_buckets[label] += 1

    # Top 10 most active chat users (by message count approximation)
    sessions = (await db.execute(select(ChatSession).order_by(desc(ChatSession.updated_at)).limit(10))).scalars().all()
    top_users = [
        {"user_id": s.user_id, "message_count": len(s.messages or []), "last_active": s.updated_at.isoformat() if s.updated_at else None}
        for s in sessions
    ]

    return {
        "total_chat_sessions":  total_sessions,
        "total_insights_cached": total_insights,
        "health_score_distribution": score_buckets,
        "top_10_chat_users":    top_users,
    }
