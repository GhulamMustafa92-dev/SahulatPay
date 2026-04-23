"""Admin router — all dashboard APIs + audit logging. PROMPT 14."""
from datetime import datetime, timezone, timedelta, date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, func, update, desc, and_, cast, Date
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_db
from limiter import limiter
from models.ai import AiInsight, ChatSession
from models.card import VirtualCard
from models.finance import HighYieldDeposit, InsurancePolicy, Investment
from models.kyc import BusinessProfile, Document, KycReviewRequest
from models.fraud import StrReport, WalletDebt
from models.other import AdminAction, FraudFlag, Notification, ZakatCalculation
from models.rewards import OfferTemplate, RewardOffer
from models.savings import SavingGoal
from models.social import BillSplit, SplitParticipant
from models.transaction import Transaction
from models.user import DeviceRegistry, User
from models.wallet import Wallet
from models.platform import PlatformAccount, PlatformLedgerEntry
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
    days: int = 7,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """High-level platform stats. Pass ?days=7|30|90 for time series range."""
    days = max(1, min(days, 365))

    total_users    = (await db.execute(select(func.count(User.id)))).scalar() or 0
    active_users   = (await db.execute(select(func.count(User.id)).where(User.is_active == True))).scalar() or 0
    locked_users   = (await db.execute(select(func.count(User.id)).where(User.is_locked == True))).scalar() or 0
    kyc_queue      = (await db.execute(select(func.count(Document.id)).where(User.cnic_verified == False).join(User, Document.user_id == User.id))).scalar() or 0
    total_txns     = (await db.execute(select(func.count(Transaction.id)))).scalar() or 0
    total_volume   = (await db.execute(select(func.coalesce(func.sum(Transaction.amount), 0)).where(Transaction.status == "completed"))).scalar() or 0
    open_fraud     = (await db.execute(select(func.count(FraudFlag.id)).where(FraudFlag.is_resolved == False))).scalar() or 0
    pending_biz    = (await db.execute(select(func.count(BusinessProfile.id)).where(BusinessProfile.verification_status == "under_review"))).scalar() or 0
    unread_notifs  = (await db.execute(select(func.count(Notification.id)).where(Notification.is_read == False))).scalar() or 0

    since = _utcnow() - timedelta(days=days)
    today = _utcnow().date()

    # Date label format: short name for 7D, "Apr 23" for 30D+
    date_fmt = "%a" if days <= 7 else "%b %d"

    # Build full date range so every day in the period appears (even zeros)
    all_dates = [today - timedelta(days=i) for i in range(days - 1, -1, -1)]

    def _fill_series(db_rows, value_attr: str) -> list:
        row_map = {r.date: float(getattr(r, value_attr)) for r in db_rows}
        return [{"date": d.strftime(date_fmt), "value": row_map.get(d, 0.0)} for d in all_dates]

    # Time series — transaction volume over selected period
    q_vol = select(
        cast(Transaction.created_at, Date).label("date"),
        func.sum(Transaction.amount).label("total")
    ).where(
        Transaction.created_at >= since,
        Transaction.status == "completed"
    ).group_by(cast(Transaction.created_at, Date)).order_by(cast(Transaction.created_at, Date))
    res_vol = (await db.execute(q_vol)).all()
    time_series = _fill_series(res_vol, "total")

    # Weekly revenue — transaction fees over selected period
    q_rev = select(
        cast(Transaction.created_at, Date).label("date"),
        func.coalesce(func.sum(Transaction.fee), 0).label("total")
    ).where(
        Transaction.created_at >= since,
        Transaction.status == "completed"
    ).group_by(cast(Transaction.created_at, Date)).order_by(cast(Transaction.created_at, Date))
    res_rev = (await db.execute(q_rev)).all()
    weekly_revenue = _fill_series(res_rev, "total")

    # Category breakdown — transaction type distribution
    q_cat = select(Transaction.type, func.count(Transaction.id)).group_by(Transaction.type)
    res_cat = (await db.execute(q_cat)).all()
    category_data = [{"name": (r.type or "Unknown").replace("_", " ").title(), "value": r[1]} for r in res_cat]

    # Purpose breakdown
    q_pur = select(Transaction.purpose, func.count(Transaction.id)).where(Transaction.purpose != None).group_by(Transaction.purpose).order_by(desc(func.count(Transaction.id))).limit(6)
    res_pur = (await db.execute(q_pur)).all()
    purpose_breakdown = [{"name": r.purpose.replace("_", " ").title(), "count": r[1]} for r in res_pur]

    # Transaction health — all-time status breakdown
    q_sts = select(Transaction.status, func.count(Transaction.id)).group_by(Transaction.status)
    res_sts = (await db.execute(q_sts)).all()
    health_data = []
    color_map = {
        "completed":    "#22c55e",
        "failed":       "#f87171",
        "blocked":      "#ef4444",
        "pending":      "#facc15",
        "under_review": "#fb923c",
        "reversed":     "#60a5fa",
    }
    for r in res_sts:
        health_data.append({
            "name":  (r.status or "unknown").replace("_", " ").title(),
            "value": r[1],
            "color": color_map.get(r.status, "#94a3b8"),
        })

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
        "days":                 days,
        "time_series":          time_series,
        "weekly_revenue":       weekly_revenue,
        "category_data":        category_data,
        "purpose_breakdown":    purpose_breakdown,
        "health_data":          health_data,
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
# KYC Review Requests — admin approval workflow
# ══════════════════════════════════════════════════════════════════════════════
_KYC_TIER_LIMITS = {2: 100_000, 3: 500_000, 4: 2_000_000}


@router.get("/kyc-reviews")
async def list_kyc_reviews(
    status: Optional[str] = "pending",
    review_type: Optional[str] = None,   # "cnic" | "liveness" | None (all)
    page: int = 1, per_page: int = 20,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """List CNIC and liveness review requests with AI-extracted data + signed document URLs."""
    q = select(KycReviewRequest).order_by(KycReviewRequest.submitted_at.desc())
    if status:
        q = q.where(KycReviewRequest.status == status)
    if review_type:
        q = q.where(KycReviewRequest.review_type == review_type)
    q = q.offset((page - 1) * per_page).limit(per_page)
    reviews = (await db.execute(q)).scalars().all()

    result = []
    for r in reviews:
        # Fetch user info
        user = (await db.execute(select(User).where(User.id == r.user_id))).scalar_one_or_none()
        # Build signed URLs for front/back.
        # For liveness/fingerprint reviews the review row has no front_doc_id —
        # fall back to the user's most recently uploaded CNIC documents so the
        # admin can compare the selfie against the ID photos.
        front_url = None
        back_url  = None
        if r.front_doc_id:
            front_doc = (await db.execute(select(Document).where(Document.id == r.front_doc_id))).scalar_one_or_none()
            if front_doc:
                front_url = get_signed_url(front_doc.cloudinary_public_id)
        if r.back_doc_id:
            back_doc = (await db.execute(select(Document).where(Document.id == r.back_doc_id))).scalar_one_or_none()
            if back_doc:
                back_url = get_signed_url(back_doc.cloudinary_public_id)
        if not front_url:
            fb = (await db.execute(
                select(Document).where(
                    Document.user_id       == r.user_id,
                    Document.document_type == "cnic_front",
                ).order_by(Document.uploaded_at.desc()).limit(1)
            )).scalars().first()
            if fb:
                front_url = get_signed_url(fb.cloudinary_public_id)
        if not back_url:
            bb = (await db.execute(
                select(Document).where(
                    Document.user_id       == r.user_id,
                    Document.document_type == "cnic_back",
                ).order_by(Document.uploaded_at.desc()).limit(1)
            )).scalars().first()
            if bb:
                back_url = get_signed_url(bb.cloudinary_public_id)

        # Build selfie URL for liveness reviews
        selfie_url = None
        if r.selfie_doc_id:
            selfie_doc = (await db.execute(select(Document).where(Document.id == r.selfie_doc_id))).scalar_one_or_none()
            if selfie_doc:
                selfie_url = get_signed_url(selfie_doc.cloudinary_public_id)

        result.append({
            "id":               str(r.id),
            "review_type":      r.review_type or "cnic",
            "user_id":          str(r.user_id),
            "user_name":        user.full_name if user else None,
            "user_phone":       user.phone_number if user else None,
            "account_cnic":     user.cnic_number if user else None,
            "status":           r.status,
            "extracted_cnic":   r.extracted_cnic,
            "extracted_name":   r.extracted_name,
            "extracted_dob":    r.extracted_dob,
            "extracted_father": r.extracted_father,
            "extracted_address": r.extracted_address,
            "cnic_masked":      r.cnic_masked,
            "face_confidence":  r.face_confidence,
            "front_image_url":  front_url,
            "back_image_url":   back_url,
            "selfie_url":       selfie_url,
            "rejection_reason": r.rejection_reason,
            "submitted_at":     r.submitted_at.isoformat() if r.submitted_at else None,
            "reviewed_at":      r.reviewed_at.isoformat() if r.reviewed_at else None,
        })

    total = (await db.execute(
        select(func.count(KycReviewRequest.id)).where(KycReviewRequest.status == status) if status
        else select(func.count(KycReviewRequest.id))
    )).scalar() or 0

    return {"reviews": result, "total": total, "page": page}


class KycDecisionRequest(BaseModel):
    reason: str = Field(default="")


@router.post("/kyc-reviews/{review_id}/approve")
async def approve_kyc_review(
    review_id: UUID,
    body: KycDecisionRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Approve a CNIC (→ Tier 2), Liveness (→ Tier 3), or Fingerprint (→ Tier 4) review."""
    review = (await db.execute(select(KycReviewRequest).where(KycReviewRequest.id == review_id))).scalar_one_or_none()
    if not review:
        raise HTTPException(404, "Review request not found.")
    if review.status != "pending":
        raise HTTPException(400, f"Review already {review.status}.")

    user = (await db.execute(select(User).where(User.id == review.user_id))).scalar_one_or_none()
    if not user:
        raise HTTPException(404, "User not found.")

    wallet = (await db.execute(select(Wallet).where(Wallet.user_id == user.id))).scalar_one_or_none()

    # Update review
    review.status      = "approved"
    review.reviewed_by = admin.id
    review.reviewed_at = _utcnow()

    rtype = review.review_type or "cnic"

    if rtype == "fingerprint":
        # Fingerprint approval → Tier 4
        user.fingerprint_verified = True
        user.nadra_verified       = True
        user.verification_tier    = max(user.verification_tier or 0, 4)
        if wallet:
            wallet.daily_limit = _KYC_TIER_LIMITS.get(4, wallet.daily_limit)
        await db.commit()
        await log_admin_action(db, admin.id, "approve_fingerprint_review", user.id, "user",
                               body.reason or "Fingerprint review approved", {"review_id": str(review_id)})
        await send_notification(
            db, user.id, "Fingerprint Verified ✅",
            "Your biometric fingerprint has been approved by admin. Tier 4 unlocked — PKR 20,00,000/day limit.",
            "system"
        )
        return {
            "message": "Fingerprint approved. User upgraded to Tier 4.",
            "review_id": str(review_id),
            "user_id": str(user.id),
        }
    elif rtype == "liveness":
        # Liveness approval → Tier 3
        user.biometric_verified = True
        user.verification_tier  = max(user.verification_tier or 0, 3)
        if wallet:
            wallet.daily_limit = _KYC_TIER_LIMITS.get(3, wallet.daily_limit)
        await db.commit()
        await log_admin_action(db, admin.id, "approve_liveness_review", user.id, "user",
                               body.reason or "Liveness review approved", {"review_id": str(review_id)})
        await send_notification(
            db, user.id, "Liveness Verified ✅",
            "Your face verification has been approved. Tier 3 unlocked — PKR 5,00,000/day limit.",
            "system"
        )
        return {
            "message": "Liveness approved. User upgraded to Tier 3.",
            "review_id": str(review_id),
            "user_id": str(user.id),
        }
    else:
        # CNIC approval → Tier 2
        user.cnic_encrypted     = review.cnic_encrypted
        user.cnic_number_masked = review.cnic_masked
        user.cnic_verified      = True
        user.verification_tier  = max(user.verification_tier or 0, 2)
        if wallet:
            wallet.daily_limit = _KYC_TIER_LIMITS.get(2, wallet.daily_limit)
        await db.commit()
        await log_admin_action(db, admin.id, "approve_kyc_review", user.id, "user",
                               body.reason or "CNIC review approved", {"review_id": str(review_id)})
        await send_notification(db, user.id, "KYC Approved ✅",
                                "Your CNIC has been verified. Tier 2 unlocked — PKR 1,00,000/day limit.", "system")
        return {"message": "CNIC approved. User upgraded to Tier 2.", "review_id": str(review_id), "user_id": str(user.id)}


@router.post("/kyc-reviews/{review_id}/reject")
async def reject_kyc_review(
    review_id: UUID,
    body: KycDecisionRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Reject a KYC review request (CNIC / Liveness / Fingerprint) with a reason."""
    review = (await db.execute(select(KycReviewRequest).where(KycReviewRequest.id == review_id))).scalar_one_or_none()
    if not review:
        raise HTTPException(404, "Review request not found.")
    if review.status != "pending":
        raise HTTPException(400, f"Review already {review.status}.")

    review.status           = "rejected"
    review.rejection_reason = body.reason or "Rejected by admin"
    review.reviewed_by      = admin.id
    review.reviewed_at      = _utcnow()
    await db.commit()

    rtype = review.review_type or "cnic"
    notif_map = {
        "cnic":        ("KYC Rejected ❌",         f"Your CNIC verification was rejected: {review.rejection_reason}. Please re-upload."),
        "liveness":    ("Liveness Rejected ❌",     f"Your face scan was rejected: {review.rejection_reason}. Please redo the verification."),
        "fingerprint": ("Fingerprint Rejected ❌",  f"Your fingerprint scan was rejected: {review.rejection_reason}. Please redo biometric registration."),
    }
    title, message = notif_map.get(rtype, notif_map["cnic"])

    await log_admin_action(db, admin.id, f"reject_{rtype}_review", review.user_id, "user",
                           body.reason or f"{rtype} review rejected", {"review_id": str(review_id)})
    await send_notification(db, review.user_id, title, message, "system")

    return {"message": f"{rtype.title()} review rejected.", "review_id": str(review_id)}


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


@router.post("/cards/{card_id}/approve")
async def admin_approve_card(card_id: UUID, body: UserActionRequest, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    card = (await db.execute(select(VirtualCard).where(VirtualCard.id == card_id))).scalar_one_or_none()
    if not card:
        raise HTTPException(404, "Card not found.")
    if card.status != "pending_approval":
        raise HTTPException(400, f"Card is not pending approval (current status: {card.status}).")
    card.status = "processing" if card.physical_requested else "active"
    await db.commit()
    await log_admin_action(db, admin.id, "approve_card", card.user_id, "user", body.reason, {"card_id": str(card_id)})
    msg = "Your card is being processed." if card.physical_requested else "Your virtual card has been approved and is now active! 🎉"
    await send_notification(db, card.user_id, "Card Approved ✅", msg, "system")
    return {"message": f"Card {card_id} approved.", "new_status": card.status}


@router.post("/cards/{card_id}/reject")
async def admin_reject_card(card_id: UUID, body: UserActionRequest, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    card = (await db.execute(select(VirtualCard).where(VirtualCard.id == card_id))).scalar_one_or_none()
    if not card:
        raise HTTPException(404, "Card not found.")
    if card.status != "pending_approval":
        raise HTTPException(400, f"Card is not pending approval (current status: {card.status}).")
    card.status = "blocked"
    await db.commit()
    await log_admin_action(db, admin.id, "reject_card", card.user_id, "user", body.reason, {"card_id": str(card_id)})
    reason_msg = f" Reason: {body.reason}" if body.reason else ""
    await send_notification(db, card.user_id, "Card Request Rejected", f"Your card request was not approved.{reason_msg}", "security")
    return {"message": f"Card {card_id} rejected."}


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
def _tmpl_to_dict(t: OfferTemplate) -> dict:
    from datetime import date, timedelta
    expiry = (t.created_at.date() + timedelta(days=t.duration_days)) if t.created_at else date.today()
    return {
        "id":                str(t.id),
        "name":              t.title,
        "type":              t.category,
        "discount_type":     "flat",
        "value":             float(t.reward_amount),
        "min_spend":         float(t.target_amount),
        "expiry_date":       expiry.isoformat(),
        "description":       t.description or "",
        "is_active":         t.is_active,
        "completion_rate":   0,
        "assignments_count": 0,
        "created_at":        t.created_at.isoformat() if t.created_at else "",
    }


class OfferTemplateCreate(BaseModel):
    name:          str            = Field(..., min_length=3, max_length=255)
    type:          str            = Field(..., min_length=2)
    discount_type: str            = "flat"
    value:         Decimal        = Field(..., gt=0)
    min_spend:     Decimal        = Field(default=Decimal("0"), ge=0)
    expiry_date:   str
    description:   Optional[str] = None
    is_active:     Optional[bool] = True


def _parse_duration(expiry_date_str: str) -> int:
    from datetime import date as _date
    try:
        exp = _date.fromisoformat(expiry_date_str)
        return max(1, (exp - _date.today()).days)
    except Exception:
        return 30


@router.post("/offers/templates", status_code=201)
async def create_offer_template(body: OfferTemplateCreate, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    tmpl = OfferTemplate(
        title=body.name, description=body.description, category=body.type.lower(),
        target_amount=body.min_spend, reward_amount=body.value,
        duration_days=_parse_duration(body.expiry_date),
        is_active=body.is_active if body.is_active is not None else True,
        created_by=admin.id,
    )
    db.add(tmpl)
    await db.commit()
    await db.refresh(tmpl)
    await log_admin_action(db, admin.id, "create_offer_template", metadata={"template_id": str(tmpl.id)})
    return _tmpl_to_dict(tmpl)


@router.get("/offers/templates")
async def list_offer_templates(admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    from sqlalchemy import func as sqlfunc
    templates = (await db.execute(
        select(OfferTemplate).order_by(OfferTemplate.created_at.desc())
    )).scalars().all()
    dicts = [_tmpl_to_dict(t) for t in templates]
    return {
        "templates":          dicts,
        "total":              len(dicts),
        "active_count":       sum(1 for t in templates if t.is_active),
        "inactive_count":     sum(1 for t in templates if not t.is_active),
        "assignments_this_month": 0,
    }


@router.patch("/offers/templates/{template_id}", status_code=200)
async def update_offer_template(template_id: UUID, body: OfferTemplateCreate, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    tmpl = (await db.execute(select(OfferTemplate).where(OfferTemplate.id == template_id))).scalar_one_or_none()
    if not tmpl:
        raise HTTPException(404, "Offer template not found.")
    tmpl.title         = body.name
    tmpl.category      = body.type.lower()
    tmpl.target_amount = body.min_spend
    tmpl.reward_amount = body.value
    tmpl.duration_days = _parse_duration(body.expiry_date)
    tmpl.description   = body.description
    if body.is_active is not None:
        tmpl.is_active = body.is_active
    await db.commit()
    await db.refresh(tmpl)
    await log_admin_action(db, admin.id, "update_offer_template", metadata={"template_id": str(tmpl.id)})
    return _tmpl_to_dict(tmpl)


@router.delete("/offers/templates/{template_id}", status_code=200)
async def delete_offer_template(template_id: UUID, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    tmpl = (await db.execute(select(OfferTemplate).where(OfferTemplate.id == template_id))).scalar_one_or_none()
    if not tmpl:
        raise HTTPException(404, "Offer template not found.")
    await db.delete(tmpl)
    await db.commit()
    await log_admin_action(db, admin.id, "delete_offer_template", metadata={"template_id": str(template_id)})
    return {"message": "Offer template deleted."}


class AssignOfferRequest(BaseModel):
    template_id: UUID
    user_ids:    list[UUID]
    reason:      str = Field(default="Admin assigned offer")


@router.post("/offers/assign", status_code=201)
async def assign_offer(body: AssignOfferRequest, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    from datetime import timedelta
    tmpl = (await db.execute(select(OfferTemplate).where(OfferTemplate.id == body.template_id))).scalar_one_or_none()
    if not tmpl:
        raise HTTPException(404, "Offer template not found.")
    assigned = []
    for uid in body.user_ids:
        offer = RewardOffer(
            user_id=uid, template_id=body.template_id,
            title=tmpl.title, category=tmpl.category,
            target_amount=tmpl.target_amount, reward_amount=tmpl.reward_amount,
            status="active",
            expires_at=_utcnow() + timedelta(days=tmpl.duration_days),
        )
        db.add(offer)
        assigned.append(str(uid))
    await db.commit()
    for uid in body.user_ids:
        await log_admin_action(db, admin.id, "assign_offer", uid, "user", body.reason, {"template_id": str(body.template_id)})
        await send_notification(db, uid, "New Offer 🎁", f"You have a new offer: {tmpl.title}!", "rewards")
    return {"message": f"Offer assigned to {len(assigned)} user(s).", "assigned_user_ids": assigned}


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
# INVESTMENTS
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/investments")
async def list_all_investments(
    page: int = 1, per_page: int = 25,
    status: Optional[str] = None,
    search: Optional[str] = None,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    from datetime import date as dt_date

    q = (
        select(Investment, User.full_name, User.phone_number)
        .join(User, User.id == Investment.user_id)
    )
    if status:
        q = q.where(Investment.status == status)
    if search:
        like = f"%{search}%"
        q = q.where((User.full_name.ilike(like)) | (User.phone_number.ilike(like)))

    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar() or 0
    rows  = (await db.execute(q.order_by(Investment.created_at.desc()).offset((page - 1) * per_page).limit(per_page))).all()

    total_invested = (await db.execute(
        select(func.coalesce(func.sum(Investment.amount), 0)).where(Investment.status == "active")
    )).scalar() or 0
    active_plans = (await db.execute(
        select(func.count()).where(Investment.status == "active")
    )).scalar() or 0
    total_returns = (await db.execute(
        select(func.coalesce(func.sum(Investment.expected_return), 0)).where(Investment.status == "active")
    )).scalar() or 0

    today = dt_date.today()
    first_day = today.replace(day=1)
    matured_this_month = (await db.execute(
        select(func.count()).where(
            Investment.maturity_date >= first_day,
            Investment.maturity_date <= today,
        )
    )).scalar() or 0

    return {
        "investments": [
            {
                "id":             str(inv.id),
                "user_id":        str(inv.user_id),
                "user_name":      full_name or "—",
                "user_phone":     phone or "—",
                "plan_name":      inv.plan_name,
                "amount":         float(inv.amount),
                "returns":        float(inv.expected_return or 0),
                "roi_percentage": float(inv.return_rate or 0),
                "status":         inv.status,
                "start_date":     inv.created_at.isoformat(),
                "maturity_date":  str(inv.maturity_date) if inv.maturity_date else None,
            }
            for inv, full_name, phone in rows
        ],
        "total":              total,
        "total_invested":     float(total_invested),
        "active_plans":       active_plans,
        "matured_this_month": matured_this_month,
        "total_returns":      float(total_returns),
    }


# ══════════════════════════════════════════════════════════════════════════════
# INSURANCE
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/insurance")
async def list_all_insurance(
    page: int = 1, per_page: int = 25,
    status: Optional[str] = None,
    search: Optional[str] = None,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    thirty_days = now + timedelta(days=30)

    q = (
        select(InsurancePolicy, User.full_name, User.phone_number)
        .join(User, User.id == InsurancePolicy.user_id)
    )
    if status:
        q = q.where(InsurancePolicy.status == status)
    if search:
        like = f"%{search}%"
        q = q.where((User.full_name.ilike(like)) | (User.phone_number.ilike(like)))

    total   = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar() or 0
    rows    = (await db.execute(q.order_by(desc(InsurancePolicy.activated_at)).offset((page - 1) * per_page).limit(per_page))).all()

    active_count   = (await db.execute(select(func.count(InsurancePolicy.id)).where(InsurancePolicy.status == "active"))).scalar() or 0
    total_premium  = (await db.execute(select(func.coalesce(func.sum(InsurancePolicy.premium), 0)).where(InsurancePolicy.status == "active"))).scalar() or 0
    expiring_count = (await db.execute(
        select(func.count(InsurancePolicy.id))
        .where(InsurancePolicy.status == "active")
        .where(InsurancePolicy.expires_at <= thirty_days)
        .where(InsurancePolicy.expires_at >= now)
    )).scalar() or 0

    return {
        "policies": [
            {
                "id":            str(p.id),
                "user_id":       str(p.user_id),
                "user_name":     full_name or "—",
                "user_phone":    phone or "—",
                "policy_type":   p.policy_type,
                "plan_name":     p.plan_name,
                "policy_number": p.policy_number,
                "premium":       float(p.premium),
                "coverage":      float(p.coverage),
                "status":        p.status,
                "start_date":    p.policy_start.isoformat() if p.policy_start else (p.activated_at.isoformat() if p.activated_at else None),
                "expiry_date":   p.expires_at.isoformat() if p.expires_at else None,
                "activated_at":  p.activated_at.isoformat() if p.activated_at else None,
                "auto_deduct_enabled": p.auto_deduct_enabled,
                "auto_deduct_freq":    p.auto_deduct_freq,
            }
            for p, full_name, phone in rows
        ],
        "total":                 total,
        "active_policies":       active_count,
        "total_premium_monthly": float(total_premium),
        "expiring_in_30_days":   expiring_count,
        "claims_this_month":     0,
    }


@router.post("/insurance/{policy_id}/cancel")
async def admin_cancel_insurance(
    policy_id: UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession  = Depends(get_db),
):
    from datetime import datetime, timezone
    from decimal import Decimal
    from models.wallet import Wallet
    from models.transaction import Transaction
    from services.wallet_service import generate_reference
    from services.platform_ledger import ledger_debit, make_idem_key

    policy = (await db.execute(select(InsurancePolicy).where(InsurancePolicy.id == policy_id))).scalar_one_or_none()
    if not policy:
        raise HTTPException(404, "Policy not found")
    if policy.status != "active":
        raise HTTPException(400, f"Policy already {policy.status}")

    now          = datetime.now(timezone.utc)
    premium_paid = policy.premium_paid or policy.premium or Decimal("0")
    policy_start = policy.policy_start or policy.activated_at or now
    policy_end   = policy.policy_end or policy.expires_at

    if (now - policy_start).days <= 15:
        refund = premium_paid
    elif policy_end:
        total_d  = max(1, (policy_end - policy_start).days)
        remain_d = max(0, (policy_end - now).days)
        refund   = (Decimal(str(remain_d)) / Decimal(str(total_d))) * premium_paid
        refund   = refund.quantize(Decimal("0.01"))
    else:
        refund = Decimal("0")

    policy.status       = "cancelled"
    policy.cancelled_at = now
    policy.refund_paid  = refund

    if refund > 0:
        wallet = (await db.execute(select(Wallet).where(Wallet.user_id == policy.user_id))).scalar_one_or_none()
        if wallet:
            wallet.balance += refund
        ref = generate_reference()
        db.add(Transaction(
            reference_number=ref, type="bill", amount=refund,
            fee=Decimal("0"), status="completed", recipient_id=policy.user_id,
            purpose="Insurance", description=f"Admin cancel — pro-rata refund",
            tx_metadata={"policy_id": str(policy_id), "admin_id": str(admin.id)},
            completed_at=now,
        ))
        await ledger_debit(db, "insurance_pool", refund,
                           make_idem_key("admin_ins_refund", str(admin.id), str(policy_id)),
                           user_id=admin.id, reference=ref, note="Admin insurance cancel refund")

    await log_admin_action(db, admin.id, "cancel_insurance",
                           target_user_id=policy.user_id,
                           metadata={"policy_id": str(policy_id), "refund": str(refund)})
    await db.commit()
    return {"status": "cancelled", "policy_id": str(policy_id), "refund_amount": str(refund),
            "message": f"Policy cancelled. PKR {refund:,.2f} refunded to user."}


# ══════════════════════════════════════════════════════════════════════════════
# AUDIT LOG (read-only)
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/audit-log")
async def audit_log(
    page: int = 1, per_page: int = 25,
    action_type: Optional[str] = None,
    admin_id: Optional[UUID] = None,
    target_user_id: Optional[UUID] = None,
    admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db),
):
    q = select(AdminAction)
    if action_type:
        q = q.where(AdminAction.action_type == action_type)
    if admin_id:
        q = q.where(AdminAction.admin_id == admin_id)
    if target_user_id:
        q = q.where(AdminAction.target_user_id == target_user_id)
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


@router.get("/login-audit")
async def login_audit(
    page: int = 1, per_page: int = 25,
    user_id: Optional[UUID] = None,
    success: Optional[bool] = None,
    failure_reason: Optional[str] = None,
    admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db),
):
    """Browse the login_audit table. Filterable by user, success/failure, and failure reason."""
    from models.user import LoginAudit
    q = select(LoginAudit)
    if user_id:
        q = q.where(LoginAudit.user_id == user_id)
    if success is not None:
        q = q.where(LoginAudit.success == success)
    if failure_reason:
        q = q.where(LoginAudit.failure_reason == failure_reason)
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar() or 0
    rows  = (await db.execute(
        q.order_by(desc(LoginAudit.created_at)).offset((page - 1) * per_page).limit(per_page)
    )).scalars().all()
    return {
        "attempts": [
            {
                "id":                str(r.id),
                "user_id":           str(r.user_id) if r.user_id else None,
                "phone_number":      r.phone_number,
                "ip_address":        r.ip_address,
                "user_agent":        r.user_agent,
                "device_fingerprint": r.device_fingerprint,
                "success":           r.success,
                "failure_reason":    r.failure_reason,
                "created_at":        r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
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


# ══════════════════════════════════════════════════════════════════════════════
# MAKER-CHECKER: Reversal Requests
# ══════════════════════════════════════════════════════════════════════════════
from models.fraud import ReversalRequest, TransactionDispute

class ReversalRequestBody(BaseModel):
    reason_code: str = Field(..., pattern="^(fraud_confirmed|erroneous_transfer|dispute_resolved)$")
    reason_detail: Optional[str] = None


class ReviewReversalBody(BaseModel):
    decision:    str  = Field(..., pattern="^(approved|rejected)$")
    review_note: Optional[str] = None


@router.post("/transactions/{txn_id}/request-reversal", status_code=201)
async def request_reversal(
    txn_id: UUID,
    body: ReversalRequestBody,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Maker step — any admin requests a reversal. A second admin must approve."""
    txn = (await db.execute(select(Transaction).where(Transaction.id == txn_id))).scalar_one_or_none()
    if not txn:
        raise HTTPException(404, "Transaction not found")

    # STEP 2 — Status whitelist: completed + under_review (fraud-held)
    if txn.status not in ("completed", "under_review"):
        raise HTTPException(400, f"Cannot request reversal for txn in status '{txn.status}'")

    # STEP 3 — 90-day reversal window
    if txn.created_at and (_utcnow() - txn.created_at).days > 90:
        raise HTTPException(
            400,
            "Reversal window expired. Transactions older than 90 days cannot be reversed.",
        )

    # STEP 4 — Duplicate pending request guard
    existing = (await db.execute(
        select(ReversalRequest)
        .where(ReversalRequest.txn_id == txn_id,
               ReversalRequest.status == "pending")
    )).scalar_one_or_none()
    if existing:
        raise HTTPException(409, "A pending reversal request already exists for this transaction.")

    req = ReversalRequest(
        txn_id=txn_id,
        requested_by=admin.id,
        reason_code=body.reason_code,
        reason_detail=body.reason_detail,
        status="pending",
    )
    db.add(req)
    await db.commit()
    await db.refresh(req)
    await log_admin_action(db, admin.id, "request_reversal", txn_id, "transaction",
                           f"{body.reason_code}: {body.reason_detail or ''}")
    return {
        "reversal_request_id": str(req.id),
        "txn_id":              str(txn_id),
        "status":              "pending",
        "message":             "Reversal request submitted. Awaiting second-admin approval.",
    }


# ── STEP 6: Side-effects helper (FraudFlag + Dispute resolve + sender push) ──
async def _execute_reversal_side_effects(
    db: AsyncSession,
    txn: Transaction,
    approving_admin: User,
    refunded_amount: Decimal,
    shortfall: Decimal,
) -> None:
    """Runs inside the caller's DB transaction — do NOT commit here."""

    # 6a. Auto-resolve linked FraudFlag
    linked_flag = (await db.execute(
        select(FraudFlag)
        .where(FraudFlag.transaction_id == txn.id, FraudFlag.is_resolved == False)
        .order_by(desc(FraudFlag.created_at))
        .limit(1)
    )).scalar_one_or_none()
    if linked_flag:
        linked_flag.is_resolved     = True
        linked_flag.resolved_by     = approving_admin.id
        linked_flag.resolved_at     = _utcnow()
        linked_flag.resolution_note = "reversal_approved"

    # 6b. Auto-resolve linked TransactionDispute
    linked_dispute = (await db.execute(
        select(TransactionDispute)
        .where(
            TransactionDispute.transaction_id == txn.id,
            TransactionDispute.status != "resolved",
        )
    )).scalar_one_or_none()
    if linked_dispute:
        linked_dispute.status          = "resolved"
        linked_dispute.resolved_at     = _utcnow()
        linked_dispute.resolved_by     = approving_admin.id
        linked_dispute.resolution_note = (
            f"Refund of PKR {refunded_amount} processed via admin reversal."
        )

    # 6c. Push notification to sender (failure must not roll back financials)
    if txn.sender_id:
        try:
            if shortfall == Decimal("0"):
                body_text = (
                    f"PKR {float(refunded_amount):,.2f} has been refunded to your wallet "
                    "following a transaction reversal."
                )
            else:
                body_text = (
                    f"PKR {float(refunded_amount):,.2f} has been refunded to your wallet. "
                    f"A partial recovery of PKR {float(shortfall):,.2f} is pending from the recipient."
                )
            await send_notification(
                db, txn.sender_id,
                "Transaction Reversed — Refund Issued",
                body_text,
                "security",
                {
                    "transaction_id":  str(txn.id),
                    "refunded_amount": str(refunded_amount),
                    "shortfall":       str(shortfall),
                },
            )
        except Exception as notify_err:
            print(f"[reversal] sender notification failed (non-fatal): {notify_err}")


@router.post("/reversal-requests/{req_id}/review")
async def review_reversal_request(
    req_id: UUID,
    body: ReviewReversalBody,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Checker step — a *different* admin approves or rejects the reversal request."""
    from models.wallet import Wallet
    from models.fraud import WalletDebt
    from services.platform_ledger import ledger_credit, ledger_debit, make_idem_key

    req = (await db.execute(
        select(ReversalRequest).where(ReversalRequest.id == req_id)
    )).scalar_one_or_none()
    if not req:
        raise HTTPException(404, "Reversal request not found")
    if req.status != "pending":
        raise HTTPException(400, f"Request already {req.status}")

    # Step 8.1 — Maker ≠ Checker
    if req.requested_by == admin.id:
        raise HTTPException(403, "Maker and Checker must be different admins")

    # ── APPROVED path ─────────────────────────────────────────────────────────
    if body.decision == "approved":

        # Step 8.2 — SELECT FOR UPDATE on txn + both wallets simultaneously
        txn = (await db.execute(
            select(Transaction).where(Transaction.id == req.txn_id).with_for_update()
        )).scalar_one_or_none()
        if not txn:
            raise HTTPException(404, "Transaction not found")

        sender_wallet = None
        if txn.sender_id:
            sender_wallet = (await db.execute(
                select(Wallet).where(Wallet.user_id == txn.sender_id).with_for_update()
            )).scalar_one_or_none()

        recipient_wallet = None
        if txn.recipient_id:
            recipient_wallet = (await db.execute(
                select(Wallet).where(Wallet.user_id == txn.recipient_id).with_for_update()
            )).scalar_one_or_none()

        # Step 8.3 — Double-reversal guard
        if txn.status == "reversed":
            raise HTTPException(409, "Transaction already reversed")

        # Step 8.4 — Status guard
        if txn.status not in ("completed", "under_review"):
            raise HTTPException(
                400,
                f"Cannot reverse a transaction in status '{txn.status}'",
            )

        # Step 8.5 — Wallet math
        available = Decimal("0")
        if recipient_wallet:
            available = min(txn.amount, recipient_wallet.balance or Decimal("0"))

        is_partial      = available < txn.amount
        shortfall       = txn.amount - available
        refunded_amount = available

        if recipient_wallet and available > 0:
            recipient_wallet.balance = (recipient_wallet.balance or Decimal("0")) - available
        if sender_wallet:
            sender_wallet.balance = (sender_wallet.balance or Decimal("0")) + available

        if is_partial and txn.recipient_id:
            db.add(WalletDebt(
                user_id=txn.recipient_id,
                amount_pkr=shortfall,
                reason=f"Reversal shortfall ({req.reason_code})",
                source_transaction_id=txn.id,
                due_at=_utcnow() + timedelta(days=30),
            ))

        # Step 8.6 — Mark transaction reversed
        txn.status      = "reversed"
        txn.reviewed_by = admin.id

        # Step 8.7 — Platform ledger entries (STEP 7)
        if not is_partial:
            await ledger_credit(
                db, "main_float", txn.amount,
                f"reversal-{req_id}-reversal_full",
                transaction_id=txn.id,
                note="Full reversal — funds returned to sender from recipient wallet.",
            )
        else:
            await ledger_credit(
                db, "main_float", refunded_amount,
                f"reversal-{req_id}-reversal_partial_refund",
                transaction_id=txn.id,
                note="Partial reversal — available balance returned to sender.",
            )
            await ledger_credit(
                db, "main_float", shortfall,
                f"reversal-{req_id}-reversal_partial_shortfall",
                transaction_id=txn.id,
                note="Partial reversal — shortfall recorded as WalletDebt against recipient. Platform absorbs risk.",
            )
            try:
                await ledger_debit(
                    db, "platform_revenue", shortfall,
                    f"reversal-{req_id}-platform_revenue_debit",
                    transaction_id=txn.id,
                    note=f"Platform absorbs reversal shortfall PKR {shortfall}.",
                )
            except ValueError:
                print(
                    f"[reversal] platform_revenue balance insufficient for shortfall "
                    f"PKR {shortfall} — skipping debit (ops alert needed)"
                )

        # Step 8.8 — Side effects (FraudFlag, Dispute, push notification)
        await _execute_reversal_side_effects(db, txn, admin, refunded_amount, shortfall)

        # Step 8.9 — Stamp the ReversalRequest
        req.status      = "approved"
        req.reviewed_by = admin.id
        req.reviewed_at = _utcnow()
        req.review_note = body.review_note

        # Step 8.10 — Single commit for all the above
        await db.commit()

        # Step 8.11 — Dual audit log (maker + checker)
        await log_admin_action(
            db, req.requested_by, "reversal_requested", req.txn_id, "transaction",
            f"{req.reason_code}: {req.reason_detail or ''}",
        )
        await log_admin_action(
            db, admin.id, "reversal_approved", req.txn_id, "transaction",
            f"Approved reversal request {req_id}. Partial={is_partial}. "
            f"Refunded={float(refunded_amount):,.2f}. Shortfall={float(shortfall):,.2f}.",
        )

        # Step 8.12 — Return full reversal summary
        return {
            "reversal_request_id": str(req_id),
            "txn_id":              str(req.txn_id),
            "decision":            "approved",
            "partial":             is_partial,
            "refunded_amount":     float(refunded_amount),
            "shortfall":           float(shortfall),
            "message": (
                f"Reversal approved. PKR {float(refunded_amount):,.2f} refunded to sender."
                + (f" WalletDebt of PKR {float(shortfall):,.2f} created for recipient." if is_partial else "")
            ),
        }

    # ── REJECTED path ─────────────────────────────────────────────────────────
    req.status      = "rejected"
    req.reviewed_by = admin.id
    req.reviewed_at = _utcnow()
    req.review_note = body.review_note

    await db.commit()

    # Notify the requesting admin about the rejection
    try:
        await send_notification(
            db, req.requested_by,
            "Reversal Request Rejected",
            f"Your reversal request for transaction {req.txn_id} was rejected. "
            f"Reason: {body.review_note or 'No reason provided.'}",
            "security",
            {"reversal_request_id": str(req_id), "txn_id": str(req.txn_id)},
        )
    except Exception as notify_err:
        print(f"[reversal] rejection notification failed (non-fatal): {notify_err}")

    await log_admin_action(
        db, admin.id, "reversal_rejected", req.txn_id, "transaction",
        f"Rejected reversal request {req_id}. Note: {body.review_note or ''}",
    )

    return {
        "reversal_request_id": str(req_id),
        "txn_id":              str(req.txn_id),
        "decision":            "rejected",
        "message":             "Reversal request rejected. Requesting admin has been notified.",
    }


@router.get("/reversal-requests/{req_id}")
async def get_reversal_request(
    req_id: UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Single-item detail for a reversal request, including the linked transaction summary."""
    req = (await db.execute(
        select(ReversalRequest).where(ReversalRequest.id == req_id)
    )).scalar_one_or_none()
    if not req:
        raise HTTPException(404, "Reversal request not found")

    txn = (await db.execute(
        select(Transaction).where(Transaction.id == req.txn_id)
    )).scalar_one_or_none()

    return {
        "id":                str(req.id),
        "txn_id":            str(req.txn_id),
        "txn_amount":        float(txn.amount) if txn else None,
        "txn_status":        txn.status if txn else None,
        "txn_created_at":    txn.created_at.isoformat() if txn and txn.created_at else None,
        "requested_by":      str(req.requested_by),
        "reason_code":       req.reason_code,
        "reason_detail":     req.reason_detail,
        "status":            req.status,
        "created_at":        req.created_at.isoformat(),
        "reviewed_by":       str(req.reviewed_by) if req.reviewed_by else None,
        "reviewed_at":       req.reviewed_at.isoformat() if req.reviewed_at else None,
        "review_note":       req.review_note,
    }


@router.get("/reversal-requests")
async def list_reversal_requests(
    status: Optional[str] = "pending",
    page: int = 1, per_page: int = 25,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    q = select(ReversalRequest).order_by(desc(ReversalRequest.created_at))
    if status:
        q = q.where(ReversalRequest.status == status)
    rows = (await db.execute(q.offset((page - 1) * per_page).limit(per_page))).scalars().all()
    return {
        "requests": [
            {
                "id":            str(r.id),
                "txn_id":        str(r.txn_id),
                "requested_by":  str(r.requested_by),
                "reason_code":   r.reason_code,
                "reason_detail": r.reason_detail,
                "status":        r.status,
                "reviewed_by":   str(r.reviewed_by) if r.reviewed_by else None,
                "reviewed_at":   r.reviewed_at.isoformat() if r.reviewed_at else None,
                "review_note":   r.review_note,
                "created_at":    r.created_at.isoformat(),
            }
            for r in rows
        ],
        "page": page, "per_page": per_page,
    }


# ══════════════════════════════════════════════════════════════════════════════
# DISPUTE MANAGEMENT — list, dismiss, evidence pre-check (Steps 10 & 11)
# ══════════════════════════════════════════════════════════════════════════════
MIN_EVIDENCE_CHARS = 30
MAX_DISMISSALS_BEFORE_FLAG = 3


class DisputeDecisionBody(BaseModel):
    decision:   str  = Field(..., pattern="^(accept|dismiss)$")
    admin_note: Optional[str] = None


@router.get("/disputes")
async def list_disputes(
    status: Optional[str] = "open",
    page: int = 1, per_page: int = 25,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    q = select(TransactionDispute).order_by(desc(TransactionDispute.created_at))
    if status:
        q = q.where(TransactionDispute.status == status)
    rows = (await db.execute(q.offset((page - 1) * per_page).limit(per_page))).scalars().all()
    return {
        "disputes": [
            {
                "id":             str(d.id),
                "transaction_id": str(d.transaction_id),
                "user_id":        str(d.user_id),
                "reason":         d.reason,
                "evidence_note":  d.evidence_note,
                "status":         d.status,
                "created_at":     d.created_at.isoformat(),
                "resolved_at":    d.resolved_at.isoformat() if d.resolved_at else None,
                "resolved_by":    str(d.resolved_by) if d.resolved_by else None,
            }
            for d in rows
        ],
        "page": page, "per_page": per_page,
    }


@router.post("/disputes/{dispute_id}/review")
async def review_dispute(
    dispute_id: UUID,
    body: DisputeDecisionBody,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Accept or dismiss a dispute.
    Evidence pre-check: dispute must have >= 30 chars of evidence_note to be accepted.
    Dismiss tracking: user dismissed_disputes_count incremented; flagged at 3+.
    """
    dispute = (await db.execute(
        select(TransactionDispute).where(TransactionDispute.id == dispute_id)
    )).scalar_one_or_none()
    if not dispute:
        raise HTTPException(404, "Dispute not found")
    if dispute.status not in ("open", "under_review"):
        raise HTTPException(400, f"Dispute already {dispute.status}")

    # ── Evidence pre-check (Step 11) ─────────────────────────────────────────
    if body.decision == "accept":
        evidence = dispute.evidence_note or ""
        if len(evidence.strip()) < MIN_EVIDENCE_CHARS:
            raise HTTPException(
                422,
                f"Dispute evidence too thin ({len(evidence.strip())} chars). "
                f"Minimum {MIN_EVIDENCE_CHARS} characters required before accepting."
            )

    dispute.status          = "resolved" if body.decision == "accept" else "dismissed"
    dispute.resolved_at     = _utcnow()
    dispute.resolved_by     = admin.id
    dispute.resolution_note = body.admin_note

    user = (await db.execute(select(User).where(User.id == dispute.user_id))).scalar_one_or_none()

    if body.decision == "dismiss" and user:
        user.dismissed_disputes_count = (user.dismissed_disputes_count or 0) + 1
        if user.dismissed_disputes_count >= MAX_DISMISSALS_BEFORE_FLAG:
            user.is_flagged = True
            from models.other import FraudFlag as FF
            db.add(FF(
                user_id=user.id,
                reason=f"Dispute abuse: {user.dismissed_disputes_count} dismissed disputes",
                severity="medium",
            ))

    await db.commit()
    await log_admin_action(db, admin.id, f"dispute_{body.decision}", dispute_id, "dispute",
                           body.admin_note or "")

    return {
        "dispute_id": str(dispute_id),
        "decision":   body.decision,
        "new_status": dispute.status,
        "dismissed_count": user.dismissed_disputes_count if user else None,
        "user_flagged": user.is_flagged if user else False,
    }


# ══════════════════════════════════════════════════════════════════════════════
# INVESTMENT SOLVENCY CHECK  (Step 13)
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/finance/solvency")
async def investment_solvency_check(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Returns pool-level solvency report:
    - investment_pool balance vs total active principal + expected returns
    - insurance_pool balance vs active policy premiums
    - gold_platform balance vs total user gold investments
    - savings_pool balance vs total saved amounts across all goals
    """
    from models.gold import GoldHolding
    from models.savings import SavingGoal

    # Pool balances
    pool_rows = (await db.execute(
        select(PlatformAccount.type, PlatformAccount.balance)
    )).all()
    pools = {row[0]: float(row[1] or 0) for row in pool_rows}

    # Investment pool obligation
    inv_principal = (await db.execute(
        select(func.coalesce(func.sum(Investment.amount), 0))
        .where(Investment.status == "active")
    )).scalar() or 0
    inv_expected_returns = (await db.execute(
        select(func.coalesce(func.sum(Investment.expected_return), 0))
        .where(Investment.status == "active")
    )).scalar() or 0
    hyd_principal = (await db.execute(
        select(func.coalesce(func.sum(HighYieldDeposit.amount), 0))
        .where(HighYieldDeposit.status == "active")
    )).scalar() or 0
    hyd_expected_interest = (await db.execute(
        select(func.coalesce(func.sum(HighYieldDeposit.expected_interest), 0))
        .where(HighYieldDeposit.status == "active")
    )).scalar() or 0

    total_inv_obligation  = float(inv_principal) + float(inv_expected_returns) + float(hyd_principal) + float(hyd_expected_interest)
    inv_pool_bal          = pools.get("investment_pool", 0)
    inv_shortfall         = max(0, total_inv_obligation - inv_pool_bal)

    # Insurance pool obligation
    ins_premiums = (await db.execute(
        select(func.coalesce(func.sum(InsurancePolicy.premium_paid), 0))
        .where(InsurancePolicy.status == "active")
    )).scalar() or 0
    ins_pool_bal  = pools.get("insurance_pool", 0)
    ins_shortfall = max(0, float(ins_premiums) - ins_pool_bal)

    # Gold pool obligation
    gold_invested = (await db.execute(
        select(func.coalesce(func.sum(GoldHolding.total_invested_pkr), 0))
    )).scalar() or 0
    gold_pool_bal  = pools.get("gold_platform", 0)
    gold_shortfall = max(0, float(gold_invested) - gold_pool_bal)

    # Savings pool obligation
    savings_total = (await db.execute(
        select(func.coalesce(func.sum(SavingGoal.saved_amount), 0))
        .where(SavingGoal.is_completed == False)
    )).scalar() or 0
    savings_pool_bal  = pools.get("savings_pool", 0)
    savings_shortfall = max(0, float(savings_total) - savings_pool_bal)

    overall_solvent = all(s == 0 for s in [inv_shortfall, ins_shortfall, gold_shortfall, savings_shortfall])

    return {
        "overall_solvent": overall_solvent,
        "pools": {
            "investment_pool": {
                "balance":     inv_pool_bal,
                "obligation":  total_inv_obligation,
                "shortfall":   inv_shortfall,
                "solvent":     inv_shortfall == 0,
            },
            "insurance_pool": {
                "balance":    ins_pool_bal,
                "obligation": float(ins_premiums),
                "shortfall":  ins_shortfall,
                "solvent":    ins_shortfall == 0,
            },
            "gold_platform": {
                "balance":    gold_pool_bal,
                "obligation": float(gold_invested),
                "shortfall":  gold_shortfall,
                "solvent":    gold_shortfall == 0,
            },
            "savings_pool": {
                "balance":    savings_pool_bal,
                "obligation": float(savings_total),
                "shortfall":  savings_shortfall,
                "solvent":    savings_shortfall == 0,
            },
        },
        "platform_revenue": pools.get("platform_revenue", 0),
        "main_float":        pools.get("main_float", 0),
        "checked_at":        _utcnow().isoformat(),
    }


# ══════════════════════════════════════════════════════════════════════════════
# ON-DEMAND RECONCILIATION TRIGGER  (Step 12)
# ══════════════════════════════════════════════════════════════════════════════
@router.post("/reconciliation/run")
async def trigger_reconciliation(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Manually trigger the reconciliation job — does not wait for 01:00 UTC."""
    import asyncio
    from scheduler.reconciliation_scheduler import _run_reconciliation
    asyncio.create_task(_run_reconciliation())
    return {
        "message": "Reconciliation job triggered in background. "
                   "Check admin notifications for any discrepancies.",
        "triggered_by": str(admin.id),
        "triggered_at": _utcnow().isoformat(),
    }


@router.get("/platform/ledger")
async def platform_ledger(
    account_type: Optional[str] = None,
    page: int = 1, per_page: int = 50,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Browse platform ledger entries — optionally filtered by account type."""
    q = (
        select(PlatformLedgerEntry, PlatformAccount.type)
        .join(PlatformAccount, PlatformLedgerEntry.account_id == PlatformAccount.id)
        .order_by(desc(PlatformLedgerEntry.created_at))
    )
    if account_type:
        q = q.where(PlatformAccount.type == account_type)

    rows = (await db.execute(q.offset((page - 1) * per_page).limit(per_page))).all()
    return {
        "entries": [
            {
                "id":               str(e.id),
                "account_type":     atype,
                "direction":        e.direction,
                "amount":           str(e.amount),
                "idempotency_key":  e.idempotency_key,
                "reference":        e.reference,
                "note":             e.note,
                "transaction_id":   str(e.transaction_id) if e.transaction_id else None,
                "user_id":          str(e.user_id) if e.user_id else None,
                "created_at":       e.created_at.isoformat(),
            }
            for e, atype in rows
        ],
        "page": page, "per_page": per_page,
    }
