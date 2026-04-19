"""WalletDebt lifecycle scheduler — runs every 15 minutes.

Stage progression:
  soft      (Day 1-7)   — push notification every 2 days
  intercept (Day 8-30)  — incoming credits to debtor wallet are intercepted
  hard      (Day 30+)   — wallet frozen, user is_flagged, admin notified

Interception of incoming transfers is handled inside _credit_recipient()
which is called by wallet_service._execute_transfer on every completed P2P txn.
"""
from datetime import datetime, timezone, timedelta
from decimal import Decimal

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select, and_

from database import AsyncSessionLocal
from models.fraud import WalletDebt
from models.user import User
from models.wallet import Wallet
from services.notification_service import send_notification

scheduler = AsyncIOScheduler(timezone="UTC")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _process_debt_lifecycle():
    """Advance WalletDebt stages and enforce collection actions."""
    print(f"[debt_scheduler] running lifecycle check @ {_utcnow().isoformat()}")
    async with AsyncSessionLocal() as db:
        debts = (await db.execute(
            select(WalletDebt)
            .where(WalletDebt.is_settled == False)
            .order_by(WalletDebt.created_at)
        )).scalars().all()

        if not debts:
            return

        for debt in debts:
            try:
                days_old = ((_utcnow()) - debt.created_at).days
                past_due = _utcnow() > debt.due_at

                # ── Determine correct stage ───────────────────────────────────
                if days_old >= 30 or past_due:
                    target_stage = "hard"
                elif days_old >= 7:
                    target_stage = "intercept"
                else:
                    target_stage = "soft"

                # ── Advance stage if needed ───────────────────────────────────
                stage_order = {"soft": 0, "intercept": 1, "hard": 2}
                if stage_order.get(target_stage, 0) > stage_order.get(debt.debt_stage, 0):
                    debt.debt_stage = target_stage

                user = (await db.execute(
                    select(User).where(User.id == debt.user_id)
                )).scalar_one_or_none()
                if not user:
                    continue

                # ── SOFT: notify every 2 days ─────────────────────────────────
                if debt.debt_stage == "soft":
                    last_notif = debt.last_notified_at
                    should_notify = (
                        last_notif is None or
                        (_utcnow() - last_notif).days >= 2
                    )
                    if should_notify:
                        await send_notification(
                            db, user.id,
                            "Outstanding Debt — Action Required",
                            f"You have an outstanding debt of PKR {debt.amount_pkr:,.2f} "
                            f"due on {debt.due_at.strftime('%d %b %Y')}. "
                            "Please top up your wallet to settle it.",
                            "payment",
                            {"debt_id": str(debt.id), "amount": str(debt.amount_pkr)},
                        )
                        debt.last_notified_at = _utcnow()

                # ── INTERCEPT: notify on stage transition ─────────────────────
                elif debt.debt_stage == "intercept":
                    last_notif = debt.last_notified_at
                    if last_notif is None or (_utcnow() - last_notif).days >= 3:
                        await send_notification(
                            db, user.id,
                            "⚠️ Debt Interception Active",
                            f"Your debt of PKR {debt.amount_pkr:,.2f} is overdue. "
                            "Any incoming funds will automatically be applied to your debt first.",
                            "security",
                            {"debt_id": str(debt.id), "amount": str(debt.amount_pkr)},
                        )
                        debt.last_notified_at = _utcnow()

                # ── HARD: freeze wallet + flag user ───────────────────────────
                elif debt.debt_stage == "hard":
                    wallet = (await db.execute(
                        select(Wallet).where(Wallet.user_id == user.id)
                    )).scalar_one_or_none()

                    if wallet and not wallet.is_frozen:
                        wallet.is_frozen   = True
                        user.is_flagged    = True

                        from models.other import FraudFlag
                        db.add(FraudFlag(
                            user_id=user.id,
                            reason=f"Wallet frozen: unsettled debt of PKR {debt.amount_pkr:,.2f} "
                                   f"past due since {debt.due_at.strftime('%d %b %Y')}",
                            severity="high",
                        ))

                        await send_notification(
                            db, user.id,
                            "🔴 Wallet Frozen — Unpaid Debt",
                            f"Your wallet has been frozen due to an unpaid debt of "
                            f"PKR {debt.amount_pkr:,.2f}. Contact support immediately.",
                            "security",
                            {"debt_id": str(debt.id), "amount": str(debt.amount_pkr)},
                        )
                        debt.last_notified_at = _utcnow()

                        # Notify all admins
                        from models.user import User as UserModel
                        admins = (await db.execute(
                            select(UserModel).where(UserModel.is_superuser == True)
                        )).scalars().all()
                        for admin in admins:
                            await send_notification(
                                db, admin.id,
                                "Admin Alert: Wallet Frozen — Bad Debt",
                                f"User {user.full_name} ({user.phone_number}) wallet frozen. "
                                f"Debt: PKR {debt.amount_pkr:,.2f}. "
                                f"Source txn: {debt.source_transaction_id}",
                                "security",
                                {"debt_id": str(debt.id), "user_id": str(user.id)},
                            )

                await db.commit()

            except Exception as e:
                await db.rollback()
                print(f"[debt_scheduler] error on debt {debt.id}: {e}")


async def intercept_incoming_credit(
    db,
    user_id,
    incoming_amount: Decimal,
) -> Decimal:
    """Called by wallet_service._execute_transfer before crediting recipient.

    If the recipient has active intercept-stage debts, deduct them first.
    Returns the net amount to actually credit to the wallet.

    This function does NOT commit — the caller commits.
    """
    debts = (await db.execute(
        select(WalletDebt)
        .where(
            and_(
                WalletDebt.user_id == user_id,
                WalletDebt.is_settled == False,
                WalletDebt.debt_stage == "intercept",
            )
        )
        .order_by(WalletDebt.created_at)
    )).scalars().all()

    if not debts:
        return incoming_amount

    remaining_credit = incoming_amount
    for debt in debts:
        if remaining_credit <= Decimal("0"):
            break
        owed = debt.amount_pkr or Decimal("0")
        payment = min(owed, remaining_credit)
        debt.amount_pkr   -= payment
        remaining_credit  -= payment
        if debt.amount_pkr <= Decimal("0"):
            debt.is_settled = True
            debt.settled_at = _utcnow()

    return max(Decimal("0"), remaining_credit)


def start_debt_scheduler():
    scheduler.add_job(
        _process_debt_lifecycle,
        trigger=IntervalTrigger(minutes=15),
        id="debt_lifecycle",
        replace_existing=True,
    )
    scheduler.start()
    print("[debt_scheduler] started — 15-min lifecycle job registered")


def stop_debt_scheduler():
    scheduler.shutdown(wait=False)
    print("[debt_scheduler] stopped")
