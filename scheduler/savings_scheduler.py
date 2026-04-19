"""APScheduler — hourly auto-deduction for saving goals."""
from datetime import datetime, timezone
from decimal import Decimal

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select

from database import AsyncSessionLocal
from models.savings import SavingGoal
from models.wallet import Wallet
from models.transaction import Transaction
from services.wallet_service import generate_reference, _send_fcm
from services.platform_ledger import ledger_credit, make_idem_key

scheduler = AsyncIOScheduler(timezone="UTC")


def _utcnow():
    return datetime.now(timezone.utc)


def _next_deduction(freq: str) -> datetime:
    from datetime import timedelta
    now = _utcnow()
    return now + (timedelta(weeks=1) if freq == "weekly" else timedelta(days=30))


async def _process_auto_deductions():
    """
    Runs every hour.
    Finds all saving goals with auto_deduct_enabled=True and next_deduction_at <= now.
    Deducts wallet → credits goal. Disables on insufficient balance.
    """
    print(f"[savings_scheduler] running auto-deduction check @ {_utcnow().isoformat()}")
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(SavingGoal)
            .where(
                SavingGoal.auto_deduct_enabled == True,
                SavingGoal.is_completed == False,
                SavingGoal.next_deduction_at <= _utcnow(),
            )
        )
        goals = result.scalars().all()
        if not goals:
            return
        print(f"[savings_scheduler] {len(goals)} goal(s) due for auto-deduction")

        for goal in goals:
            try:
                from sqlalchemy import select as _select
                from models.user import User
                user = (await db.execute(
                    _select(User).where(User.id == goal.user_id)
                )).scalar_one_or_none()
                if not user:
                    continue

                wallet = (await db.execute(
                    _select(Wallet).where(Wallet.user_id == goal.user_id)
                )).scalar_one_or_none()
                if not wallet:
                    continue

                amount = goal.auto_deduct_amount or Decimal("0")
                if amount <= 0:
                    continue

                # Insufficient balance — disable auto-deduct
                if wallet.balance < amount:
                    goal.auto_deduct_enabled = False
                    goal.next_deduction_at   = None
                    await db.commit()
                    import asyncio
                    asyncio.create_task(_send_fcm(
                        user.fcm_token or "",
                        title="⚠️ Auto-Save Paused",
                        body=f'Insufficient balance for "{goal.goal_name}" auto-deduction of PKR {amount:,.0f}.',
                    ))
                    continue

                # Cap at remaining amount needed
                remaining = goal.target_amount - goal.saved_amount
                deduct    = min(amount, remaining)

                wallet.balance    -= deduct
                goal.saved_amount += deduct
                goal.last_deduction_at = _utcnow()
                goal.next_deduction_at = _next_deduction(goal.auto_deduct_freq or "monthly")

                ref = generate_reference()
                txn = Transaction(
                    reference_number=ref,
                    type="savings",
                    amount=deduct,
                    fee=Decimal("0"),
                    status="completed",
                    sender_id=goal.user_id,
                    purpose="Savings",
                    description=f"Auto-deduction: {goal.goal_name}",
                    tx_metadata={"goal_id": str(goal.id), "action": "auto_deduct"},
                    completed_at=_utcnow(),
                )
                db.add(txn)
                await ledger_credit(
                    db, "savings_pool", deduct,
                    make_idem_key("savings_auto_deduct", str(goal.user_id), str(goal.id), ref),
                    user_id=goal.user_id, reference=ref,
                    note=f"Auto-deduction: {goal.goal_name}",
                )

                # Check if goal now complete
                if goal.saved_amount >= goal.target_amount:
                    goal.is_completed        = True
                    goal.goal_achieved       = True
                    goal.auto_deduct_enabled = False
                    goal.next_deduction_at   = None
                    await db.commit()
                    import asyncio
                    asyncio.create_task(_send_fcm(
                        user.fcm_token or "",
                        title="🎉 Savings Goal Achieved!",
                        body=f'"{goal.goal_name}" is fully funded! PKR {goal.target_amount:,.0f} saved.',
                    ))
                    continue

                await db.commit()
                import asyncio
                asyncio.create_task(_send_fcm(
                    user.fcm_token or "",
                    title="💰 Auto-Save Done",
                    body=f'PKR {deduct:,.0f} saved to "{goal.goal_name}". Progress: {float(goal.saved_amount/goal.target_amount*100):.0f}%',
                ))

            except Exception as e:
                await db.rollback()
                print(f"[savings_scheduler] error on goal {goal.id}: {e}")


def start_savings_scheduler():
    scheduler.add_job(
        _process_auto_deductions,
        trigger=IntervalTrigger(hours=1),
        id="savings_auto_deduct",
        replace_existing=True,
        max_instances=1,
    )
    if not scheduler.running:
        scheduler.start()
    print("[savings_scheduler] started — auto-deduction every 1 hour")


def stop_savings_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)
    print("[savings_scheduler] stopped")
