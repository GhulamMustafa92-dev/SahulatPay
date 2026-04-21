"""Rewards router — cashback, offers, claims. PROMPT 11."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from limiter import limiter
from models.rewards import Reward, RewardOffer, RewardTransaction, OfferTemplate
from models.user import User
from services.auth_service import get_current_user
from services.reward_service import claim_cashback, credit_offer_reward

router = APIRouter()


# ── GET /rewards/my ───────────────────────────────────────────────────────────
@router.get("/my")
async def my_rewards(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    reward = (await db.execute(
        select(Reward).where(Reward.user_id == current_user.id)
    )).scalar_one_or_none()

    active_offers = (await db.execute(
        select(RewardOffer).where(
            RewardOffer.user_id == current_user.id,
            RewardOffer.status  == "active",
        )
    )).scalars().all()

    completed_offers = (await db.execute(
        select(RewardOffer).where(
            RewardOffer.user_id == current_user.id,
            RewardOffer.status  == "completed",
        )
    )).scalars().all()

    return {
        "total_earned":       str(reward.total_earned) if reward else "0.00",
        "cashback_pending":   str(reward.pending)      if reward else "0.00",
        "cashback_claimed":   str(reward.claimed)      if reward else "0.00",
        "active_offers":      len(active_offers),
        "completed_offers":   len(completed_offers),
        "can_claim":          reward is not None and reward.pending > 0,
    }


# ── GET /rewards/history ──────────────────────────────────────────────────────
@router.get("/history")
async def reward_history(
    page:     int = 1,
    per_page: int = 20,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if per_page > 50:
        per_page = 50
    offset = (page - 1) * per_page
    rows = (await db.execute(
        select(RewardTransaction)
        .where(RewardTransaction.user_id == current_user.id)
        .order_by(RewardTransaction.created_at.desc())
        .offset(offset).limit(per_page)
    )).scalars().all()
    return {
        "page":    page,
        "results": [
            {
                "id":             str(r.id),
                "type":           r.type,
                "amount":         str(r.amount),
                "transaction_id": str(r.transaction_id) if r.transaction_id else None,
                "offer_id":       str(r.offer_id)       if r.offer_id       else None,
                "created_at":     r.created_at.isoformat(),
            }
            for r in rows
        ],
    }


# ── POST /rewards/claim ───────────────────────────────────────────────────────
@router.post("/claim")
@limiter.limit("5/hour")
async def claim_reward(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    reward = (await db.execute(
        select(Reward).where(Reward.user_id == current_user.id)
    )).scalar_one_or_none()
    if not reward or reward.pending <= 0:
        raise HTTPException(400, "No pending cashback to claim")

    amount = await claim_cashback(db, current_user.id)
    await db.commit()
    return {
        "claimed":   str(amount),
        "message":   f"PKR {amount:,.2f} cashback moved to your wallet.",
    }


# ── GET /rewards/offers ───────────────────────────────────────────────────────
@router.get("/offers")
async def my_offers(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    offers = (await db.execute(
        select(RewardOffer).where(
            RewardOffer.user_id    == current_user.id,
            RewardOffer.status.in_(["active", "completed"]),
            RewardOffer.expires_at > now,
        ).order_by(RewardOffer.created_at.desc())
    )).scalars().all()
    return {
        "offers": [
            {
                "id":             str(o.id),
                "title":          o.title,
                "category":       o.category,
                "target_amount":  str(o.target_amount),
                "current_spent":  str(o.current_spent),
                "reward_amount":  str(o.reward_amount),
                "progress":       round(float(o.current_spent / o.target_amount * 100), 1) if o.target_amount > 0 else 0,
                "status":         o.status,
                "expires_at":     o.expires_at.isoformat(),
                "completed_at":   o.completed_at.isoformat() if o.completed_at else None,
            }
            for o in offers
        ],
        "total": len(offers),
    }


# ── GET /rewards/available ───────────────────────────────────────────────────
@router.get("/available")
async def available_offers(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)

    templates = (await db.execute(
        select(OfferTemplate).where(
            OfferTemplate.is_active == True,
        ).order_by(OfferTemplate.created_at.desc())
    )).scalars().all()

    enrolled_template_ids = set()
    if templates:
        enrolled = (await db.execute(
            select(RewardOffer.template_id).where(
                RewardOffer.user_id == current_user.id,
                RewardOffer.status.in_(["active", "completed", "claimed"]),
            )
        )).scalars().all()
        enrolled_template_ids = {str(tid) for tid in enrolled if tid}

    result = []
    for t in templates:
        expiry = t.created_at + timedelta(days=t.duration_days) if t.created_at else now
        result.append({
            "id":             str(t.id),
            "title":          t.title,
            "description":    t.description or "",
            "category":       t.category,
            "target_amount":  str(t.target_amount),
            "reward_amount":  str(t.reward_amount),
            "duration_days":  t.duration_days,
            "expires_at":     expiry.isoformat(),
            "is_enrolled":    str(t.id) in enrolled_template_ids,
        })
    return {"offers": result, "total": len(result)}


# ── POST /rewards/offers/enroll/{template_id} ─────────────────────────────────
@router.post("/offers/enroll/{template_id}", status_code=201)
async def enroll_offer(
    template_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)

    tmpl = (await db.execute(
        select(OfferTemplate).where(
            OfferTemplate.id == template_id,
            OfferTemplate.is_active == True,
        )
    )).scalar_one_or_none()
    if not tmpl:
        raise HTTPException(404, "Offer not found or inactive.")

    existing = (await db.execute(
        select(RewardOffer).where(
            RewardOffer.user_id == current_user.id,
            RewardOffer.template_id == template_id,
            RewardOffer.status.in_(["active", "completed"]),
        )
    )).scalar_one_or_none()
    if existing:
        raise HTTPException(400, "Already enrolled in this offer.")

    offer = RewardOffer(
        user_id=current_user.id,
        template_id=tmpl.id,
        title=tmpl.title,
        category=tmpl.category,
        target_amount=tmpl.target_amount,
        reward_amount=tmpl.reward_amount,
        status="active",
        expires_at=now + timedelta(days=tmpl.duration_days),
    )
    db.add(offer)
    await db.commit()
    await db.refresh(offer)
    return {
        "offer_id":      str(offer.id),
        "title":         offer.title,
        "category":      offer.category,
        "target_amount": str(offer.target_amount),
        "reward_amount": str(offer.reward_amount),
        "expires_at":    offer.expires_at.isoformat(),
        "message":       f"Enrolled in '{offer.title}'. Start spending to track progress!",
    }


# ── POST /rewards/offers/{offer_id}/accept ────────────────────────────────────
@router.post("/offers/{offer_id}/accept")
async def accept_offer(
    offer_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    offer = (await db.execute(select(RewardOffer).where(
        RewardOffer.id      == offer_id,
        RewardOffer.user_id == current_user.id,
    ))).scalar_one_or_none()
    if not offer:
        raise HTTPException(404, "Offer not found")
    if offer.status != "pending":
        raise HTTPException(400, f"Offer is already {offer.status}")
    offer.status = "active"
    await db.commit()
    return {"status": "active", "offer_id": str(offer_id), "message": f"Offer '{offer.title}' is now active."}


# ── POST /rewards/offers/{offer_id}/claim ─────────────────────────────────────
@router.post("/offers/{offer_id}/claim")
@limiter.limit("10/hour")
async def claim_offer(
    request: Request,
    offer_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        amount = await credit_offer_reward(db, current_user.id, offer_id)
        await db.commit()
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "claimed":   str(amount),
        "offer_id":  str(offer_id),
        "message":   f"PKR {amount:,.2f} reward credited to your wallet.",
    }
