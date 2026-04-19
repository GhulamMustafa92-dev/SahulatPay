"""Fraud detection scheduler — 15-minute scanner + 24-hour deep analysis."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone, timedelta
from decimal import Decimal

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select, func, and_, update, desc
from sqlalchemy.ext.asyncio import AsyncSession

from database import AsyncSessionLocal
from models.fraud import StrReport, UserBehaviourProfile
from models.other import FraudFlag
from models.transaction import Transaction
from models.user import User
from models.wallet import Wallet

scheduler = AsyncIOScheduler(timezone="UTC")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ══════════════════════════════════════════════════════════════════════════════
# JOB 1 — Fraud Scanner (every 15 minutes)
# ══════════════════════════════════════════════════════════════════════════════

async def _fraud_scanner_job() -> None:
    """
    Scans all transactions from last 15 minutes that have not been scored yet.
    Applies rule-based scoring, creates FraudFlags, auto-locks critical users.
    Also releases held transactions whose hold_expires_at has passed.
    """
    print(f"[fraud_scheduler] 15-min scanner running at {_utcnow().isoformat()}")
    async with AsyncSessionLocal() as db:
        try:
            await _scan_unscored_transactions(db)
            await _release_expired_holds(db)
            await _update_behaviour_profiles_active_today(db)
        except Exception as exc:
            await db.rollback()
            print(f"[fraud_scheduler] scanner error: {exc}")


async def _scan_unscored_transactions(db: AsyncSession) -> None:
    cutoff = _utcnow() - timedelta(minutes=15)
    txns = (await db.execute(
        select(Transaction).where(
            and_(
                Transaction.created_at >= cutoff,
                Transaction.status == "completed",
                Transaction.fraud_score == 0,
                Transaction.sender_id.isnot(None),
            )
        )
    )).scalars().all()

    print(f"[fraud_scheduler] found {len(txns)} unscored transactions")

    from services.fraud_scoring import calculate_fraud_score, score_to_severity, notify_admins_background

    for txn in txns:
        try:
            user = (await db.execute(select(User).where(User.id == txn.sender_id))).scalar_one_or_none()
            if not user:
                continue

            score, reasons = await calculate_fraud_score(user, txn.amount, txn.recipient_id, db)
            if score == 0:
                continue

            reason_text = ", ".join(reasons)

            await db.execute(
                update(Transaction)
                .where(Transaction.id == txn.id)
                .values(fraud_score=score)
            )

            if score >= 31:
                db.add(FraudFlag(
                    user_id=txn.sender_id,
                    transaction_id=txn.id,
                    reason=f"SCANNER: {reason_text}",
                    severity=score_to_severity(score),
                ))

            if score >= 81:
                await db.execute(
                    update(User)
                    .where(User.id == txn.sender_id)
                    .values(is_locked=True, is_active=False, is_flagged=True, risk_score=min(score, 32767))
                )
                asyncio.create_task(notify_admins_background(
                    "🚨 CRITICAL — Scanner Auto-Locked User",
                    f"Score {score} detected on completed txn PKR {float(txn.amount):,.0f}. User locked.",
                    {"user_id": str(txn.sender_id), "txn_id": str(txn.id), "score": str(score)},
                ))
            elif score >= 51:
                await db.execute(
                    update(User)
                    .where(User.id == txn.sender_id)
                    .values(risk_score=min(score, 32767))
                )
                asyncio.create_task(notify_admins_background(
                    "⚠️ HIGH RISK — Scanner Alert",
                    f"Score {score} on completed txn PKR {float(txn.amount):,.0f}. User: {user.phone_number}",
                    {"user_id": str(txn.sender_id), "txn_id": str(txn.id), "score": str(score)},
                ))

            await db.commit()

        except Exception as exc:
            await db.rollback()
            print(f"[fraud_scheduler] error scoring txn {txn.id}: {exc}")


async def _release_expired_holds(db: AsyncSession) -> None:
    """Auto-complete held transactions whose 2-hour hold window has expired."""
    expired = (await db.execute(
        select(Transaction).where(
            and_(
                Transaction.status == "under_review",
                Transaction.hold_expires_at <= _utcnow(),
            )
        )
    )).scalars().all()

    for txn in expired:
        try:
            txn.status       = "completed"
            txn.completed_at = _utcnow()
            if txn.recipient_id:
                recv_wallet = (await db.execute(
                    select(Wallet).where(Wallet.user_id == txn.recipient_id)
                )).scalar_one_or_none()
                if recv_wallet:
                    recv_wallet.balance = (recv_wallet.balance or Decimal("0")) + txn.amount
            await db.commit()
            print(f"[fraud_scheduler] auto-completed held txn {txn.id} (hold expired)")
        except Exception as exc:
            await db.rollback()
            print(f"[fraud_scheduler] error releasing hold {txn.id}: {exc}")


async def _update_behaviour_profiles_active_today(db: AsyncSession) -> None:
    """Recalculate behaviour profiles for users active in last 24 hours."""
    cutoff = _utcnow() - timedelta(hours=24)
    active_user_ids = (await db.execute(
        select(Transaction.sender_id.distinct()).where(
            and_(Transaction.created_at >= cutoff, Transaction.sender_id.isnot(None))
        )
    )).scalars().all()

    for user_id in active_user_ids:
        try:
            await _recalculate_profile(db, user_id)
        except Exception as exc:
            await db.rollback()
            print(f"[fraud_scheduler] profile update error for {user_id}: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# JOB 2 — Daily Deep Analysis (every 24 hours at 02:00 UTC)
# ══════════════════════════════════════════════════════════════════════════════

async def _daily_analysis_job() -> None:
    """
    Builds transaction graph for last 24 hours, sends to DeepSeek for pattern
    detection, checks STR thresholds, generates STR drafts, updates all profiles.
    """
    print(f"[fraud_scheduler] daily analysis running at {_utcnow().isoformat()}")
    async with AsyncSessionLocal() as db:
        try:
            await _run_graph_analysis(db)
            await _check_str_thresholds(db)
            await _update_all_behaviour_profiles(db)
        except Exception as exc:
            await db.rollback()
            print(f"[fraud_scheduler] daily analysis error: {exc}")


async def _run_graph_analysis(db: AsyncSession) -> None:
    """Build 24h transaction graph and send to DeepSeek for pattern detection."""
    cutoff = _utcnow() - timedelta(hours=24)
    txns = (await db.execute(
        select(Transaction).where(
            and_(
                Transaction.created_at >= cutoff,
                Transaction.status == "completed",
                Transaction.sender_id.isnot(None),
                Transaction.recipient_id.isnot(None),
            )
        ).order_by(Transaction.created_at)
    )).scalars().all()

    if not txns:
        return

    chains = [
        {
            "sender_id":    str(t.sender_id),
            "recipient_id": str(t.recipient_id),
            "amount":       float(t.amount),
            "time":         t.created_at.isoformat() if t.created_at else None,
            "reference":    t.reference_number,
        }
        for t in txns
    ]

    try:
        from services.deepseek_fraud import analyse_transaction_graph
        result = await analyse_transaction_graph(json.dumps(chains))
        if not result:
            return

        confidence = result.get("confidence", 0)
        if confidence > 75:
            involved_users = result.get("involved_users", [])
            pattern_type   = result.get("pattern_type", "unknown")
            for user_id_str in involved_users:
                try:
                    from uuid import UUID
                    uid = UUID(str(user_id_str))
                    db.add(FraudFlag(
                        user_id=uid,
                        reason=f"AI_GRAPH: {pattern_type} (confidence {confidence}%)",
                        severity="high",
                    ))
                except Exception:
                    pass
            await db.commit()
            print(f"[fraud_scheduler] graph analysis flagged {len(involved_users)} users — pattern: {pattern_type}")
    except Exception as exc:
        print(f"[fraud_scheduler] graph analysis error: {exc}")


async def _check_str_thresholds(db: AsyncSession) -> None:
    """Check STR thresholds and auto-generate draft STR reports."""
    cutoff = _utcnow() - timedelta(hours=24)

    high_value = (await db.execute(
        select(Transaction).where(
            and_(
                Transaction.created_at >= cutoff,
                Transaction.status == "completed",
                Transaction.amount >= Decimal("2500000"),
            )
        )
    )).scalars().all()

    for txn in high_value:
        try:
            existing = (await db.execute(
                select(StrReport).where(StrReport.transaction_id == txn.id)
            )).scalar_one_or_none()
            if existing:
                continue

            user = (await db.execute(select(User).where(User.id == txn.sender_id))).scalar_one_or_none()
            if not user:
                continue

            flags = (await db.execute(
                select(FraudFlag).where(FraudFlag.transaction_id == txn.id)
            )).scalars().all()

            from services.deepseek_fraud import generate_str_narrative
            narrative = await generate_str_narrative(user, txn, flags)

            db.add(StrReport(
                user_id=txn.sender_id,
                transaction_id=txn.id,
                report_type="STR",
                amount_pkr=txn.amount,
                ai_narrative=narrative,
                status="draft",
            ))
            await db.commit()
            print(f"[fraud_scheduler] STR draft created for txn {txn.reference_number} PKR {float(txn.amount):,.0f}")
        except Exception as exc:
            await db.rollback()
            print(f"[fraud_scheduler] STR generation error for txn {txn.id}: {exc}")

    users_result = await db.execute(
        select(
            Transaction.sender_id,
            func.sum(Transaction.amount).label("total"),
        ).where(
            and_(
                Transaction.created_at >= cutoff,
                Transaction.status == "completed",
                Transaction.sender_id.isnot(None),
            )
        ).group_by(Transaction.sender_id)
        .having(func.sum(Transaction.amount) >= Decimal("10000000"))
    )
    heavy_users = users_result.all()

    for row in heavy_users:
        user_id_val, total = row
        try:
            existing = (await db.execute(
                select(StrReport).where(
                    and_(
                        StrReport.user_id == user_id_val,
                        StrReport.transaction_id.is_(None),
                        StrReport.generated_at >= cutoff,
                    )
                )
            )).scalar_one_or_none()
            if existing:
                continue

            user = (await db.execute(select(User).where(User.id == user_id_val))).scalar_one_or_none()
            if not user:
                continue

            db.add(StrReport(
                user_id=user_id_val,
                report_type="CTR",
                amount_pkr=Decimal(str(total)),
                ai_narrative=f"User {user.full_name} ({user.phone_number}) transferred PKR {float(total):,.0f} in 24 hours, exceeding CTR threshold of PKR 10,000,000.",
                status="draft",
            ))
            await db.commit()
            print(f"[fraud_scheduler] CTR draft created for user {user.phone_number} total PKR {float(total):,.0f}")
        except Exception as exc:
            await db.rollback()
            print(f"[fraud_scheduler] CTR generation error for user {user_id_val}: {exc}")


async def _update_all_behaviour_profiles(db: AsyncSession) -> None:
    """Recalculate behaviour baseline for every user with transactions."""
    all_users = (await db.execute(
        select(Transaction.sender_id.distinct()).where(Transaction.sender_id.isnot(None))
    )).scalars().all()

    updated = 0
    for user_id in all_users:
        try:
            await _recalculate_profile(db, user_id)
            updated += 1
        except Exception as exc:
            await db.rollback()
            print(f"[fraud_scheduler] profile error for {user_id}: {exc}")

    print(f"[fraud_scheduler] updated {updated} behaviour profiles")


# ── Profile recalculation helper ─────────────────────────────────────────────

async def _recalculate_profile(db: AsyncSession, user_id) -> None:
    cutoff_90 = _utcnow() - timedelta(days=90)
    txns = (await db.execute(
        select(Transaction).where(
            and_(
                Transaction.sender_id == user_id,
                Transaction.status == "completed",
                Transaction.created_at >= cutoff_90,
            )
        )
    )).scalars().all()

    if not txns:
        return

    amounts     = [t.amount for t in txns]
    avg_amount  = sum(amounts) / len(amounts)
    max_amount  = max(amounts)
    total_count = len(txns)

    hours = [t.created_at.hour for t in txns if t.created_at]
    typical_start = min(hours) if hours else None
    typical_end   = max(hours) if hours else None

    unique_recipients = len({t.recipient_id for t in txns if t.recipient_id})

    existing = (await db.execute(
        select(UserBehaviourProfile).where(UserBehaviourProfile.user_id == user_id)
    )).scalar_one_or_none()

    if existing:
        existing.avg_transaction_pkr     = Decimal(str(avg_amount)).quantize(Decimal("0.01"))
        existing.max_transaction_pkr     = Decimal(str(max_amount)).quantize(Decimal("0.01"))
        existing.typical_hour_start      = typical_start
        existing.typical_hour_end        = typical_end
        existing.known_recipients_count  = unique_recipients
        existing.total_transaction_count = total_count
        existing.last_calculated_at      = _utcnow()
    else:
        db.add(UserBehaviourProfile(
            user_id=user_id,
            avg_transaction_pkr=Decimal(str(avg_amount)).quantize(Decimal("0.01")),
            max_transaction_pkr=Decimal(str(max_amount)).quantize(Decimal("0.01")),
            typical_hour_start=typical_start,
            typical_hour_end=typical_end,
            known_recipients_count=unique_recipients,
            total_transaction_count=total_count,
            last_calculated_at=_utcnow(),
        ))

    await db.commit()


# ══════════════════════════════════════════════════════════════════════════════
# Scheduler start / stop
# ══════════════════════════════════════════════════════════════════════════════

def start_fraud_scheduler() -> AsyncIOScheduler:
    scheduler.add_job(
        _fraud_scanner_job,
        trigger=IntervalTrigger(minutes=15),
        id="fraud_scanner_15min",
        replace_existing=True,
    )
    scheduler.add_job(
        _daily_analysis_job,
        trigger=CronTrigger(hour=2, minute=0, timezone="UTC"),
        id="fraud_daily_analysis",
        replace_existing=True,
    )
    scheduler.start()
    print("[fraud_scheduler] started — 15-min scanner + 02:00 UTC daily analysis")
    return scheduler


def stop_fraud_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
        print("[fraud_scheduler] stopped")
