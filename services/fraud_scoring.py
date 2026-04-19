"""Rule-based fraud scoring engine — velocity checks + score calculation."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Optional, Tuple
from uuid import UUID

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from models.user import User
from models.transaction import Transaction
from services.wallet_service import TIER_LIMITS


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── Behaviour profile helpers ─────────────────────────────────────────────────

async def get_behaviour_profile(user_id: UUID, db: AsyncSession):
    from models.fraud import UserBehaviourProfile
    return (await db.execute(
        select(UserBehaviourProfile).where(UserBehaviourProfile.user_id == user_id)
    )).scalar_one_or_none()


async def is_known_recipient(
    user_id: UUID, recipient_id: Optional[UUID], db: AsyncSession
) -> bool:
    """True if sender has successfully paid this recipient in the last 90 days."""
    if not recipient_id:
        return False
    cutoff = _utcnow() - timedelta(days=90)
    count = (await db.execute(
        select(func.count(Transaction.id)).where(
            and_(
                Transaction.sender_id == user_id,
                Transaction.recipient_id == recipient_id,
                Transaction.status == "completed",
                Transaction.created_at >= cutoff,
            )
        )
    )).scalar() or 0
    return count > 0


async def get_recent_transaction_count(
    user_id: UUID, minutes: int, db: AsyncSession
) -> int:
    cutoff = _utcnow() - timedelta(minutes=minutes)
    return (await db.execute(
        select(func.count(Transaction.id)).where(
            and_(
                Transaction.sender_id == user_id,
                Transaction.created_at >= cutoff,
            )
        )
    )).scalar() or 0


async def get_unique_recipients_count(
    user_id: UUID, minutes: int, db: AsyncSession
) -> int:
    cutoff = _utcnow() - timedelta(minutes=minutes)
    return (await db.execute(
        select(func.count(func.distinct(Transaction.recipient_id))).where(
            and_(
                Transaction.sender_id == user_id,
                Transaction.recipient_id.isnot(None),
                Transaction.created_at >= cutoff,
            )
        )
    )).scalar() or 0


# ── Velocity check — runs BEFORE scoring ─────────────────────────────────────

async def check_velocity(
    user_id: UUID, db: AsyncSession
) -> Tuple[str, Optional[str]]:
    """
    Returns ("pass", None) | ("hold", reason) | ("blocked", reason).
    Short-circuits scoring if velocity thresholds crossed.
    """
    count_5min   = await get_recent_transaction_count(user_id, minutes=5,  db=db)
    count_1hr    = await get_recent_transaction_count(user_id, minutes=60, db=db)
    unique_10min = await get_unique_recipients_count(user_id,  minutes=10, db=db)

    if count_1hr > 15:
        return "blocked", "velocity_critical"
    if count_5min > 5:
        return "hold", "velocity_high"
    if unique_10min > 3:
        return "hold", "velocity_recipients"
    return "pass", None


# ── Rule-based scoring engine ─────────────────────────────────────────────────

async def calculate_fraud_score(
    user: User,
    amount: Decimal,
    recipient_id: Optional[UUID],
    db: AsyncSession,
) -> Tuple[int, list]:
    """
    Returns (score, reasons[]).
    All rules are additive.  Caller is responsible for applying threshold actions.
    """
    score: int = 0
    reasons: list = []

    # +10 — amount above user 30-day average
    profile = await get_behaviour_profile(user.id, db)
    if profile and profile.avg_transaction_pkr and amount > Decimal(str(profile.avg_transaction_pkr)):
        score += 10
        reasons.append("above_average_amount")

    # +20 — amount > 80% of daily KYC limit
    tier = user.verification_tier or 0
    daily_limit = TIER_LIMITS.get(tier, Decimal("0"))
    if daily_limit > 0 and amount > daily_limit * Decimal("0.8"):
        score += 20
        reasons.append("near_daily_limit")

    # +15 — unusual hour: 1 AM – 5 AM PKT (UTC 20:00-00:00)
    hour_utc = _utcnow().hour
    if hour_utc >= 20 or hour_utc == 0:
        score += 15
        reasons.append("unusual_hour")

    # +25 — unknown recipient (never paid in last 90 days)
    if recipient_id and not await is_known_recipient(user.id, recipient_id, db):
        score += 25
        reasons.append("unknown_recipient")

    # +30 — high velocity: 5+ transactions in last 10 minutes
    recent_count = await get_recent_transaction_count(user.id, minutes=10, db=db)
    if recent_count >= 5:
        score += 30
        reasons.append("high_velocity")

    # +40 — structuring: amount exactly equals daily_limit − PKR 1,000
    if daily_limit > 0 and amount == daily_limit - Decimal("1000"):
        score += 40
        reasons.append("structuring_pattern")

    # +50 — multiple failed PIN attempts recorded on the user
    if (user.login_attempts or 0) > 2:
        score += 50
        reasons.append("failed_pin_attempts")

    return score, reasons


# ── Admin notification (background) ──────────────────────────────────────────

async def notify_admins_background(title: str, body: str, data: dict) -> None:
    """Fire-and-forget: opens its own DB session and notifies all superusers."""
    try:
        from database import AsyncSessionLocal
        from services.notification_service import send_notification
        async with AsyncSessionLocal() as db:
            admins = (await db.execute(
                select(User).where(User.is_superuser == True, User.is_active == True)
            )).scalars().all()
            for admin in admins:
                await send_notification(db, admin.id, title, body, "admin", data)
    except Exception as exc:
        print(f"[fraud_scoring] admin notification error: {exc}")


def schedule_admin_notify(title: str, body: str, data: dict) -> None:
    """Safe wrapper — creates asyncio task only if an event loop is running."""
    try:
        asyncio.create_task(notify_admins_background(title, body, data))
    except RuntimeError:
        pass


# ── Severity helper ───────────────────────────────────────────────────────────

def score_to_severity(score: int) -> str:
    if score >= 81:
        return "critical"
    if score >= 51:
        return "high"
    if score >= 31:
        return "medium"
    return "low"
