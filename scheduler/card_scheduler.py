"""APScheduler — 1st of every month: reset virtual card monthly_spent to 0."""
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import update

from database import AsyncSessionLocal
from models.card import VirtualCard

scheduler = AsyncIOScheduler(timezone="UTC")


def _first_of_next_month() -> datetime:
    now = datetime.now(timezone.utc)
    if now.month == 12:
        return now.replace(year=now.year + 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    return now.replace(month=now.month + 1, day=1, hour=0, minute=0, second=0, microsecond=0)


async def _reset_monthly_spent():
    """
    Runs at 00:00 UTC on the 1st of every month.
    Resets monthly_spent=0 on all active virtual cards.
    """
    print(f"[card_scheduler] monthly reset triggered @ {datetime.now(timezone.utc).isoformat()}")
    async with AsyncSessionLocal() as db:
        try:
            next_reset = _first_of_next_month()
            await db.execute(
                update(VirtualCard)
                .where(VirtualCard.status == "active")
                .values(monthly_spent=0, monthly_reset_at=next_reset)
            )
            await db.commit()
            print(f"[card_scheduler] monthly_spent reset complete. Next reset: {next_reset.date()}")
        except Exception as e:
            await db.rollback()
            print(f"[card_scheduler] monthly reset error: {e}")


def start_card_scheduler():
    scheduler.add_job(
        _reset_monthly_spent,
        trigger=CronTrigger(day=1, hour=0, minute=0, timezone="UTC"),
        id="card_monthly_reset",
        replace_existing=True,
        max_instances=1,
    )
    if not scheduler.running:
        scheduler.start()
    print("[card_scheduler] started — monthly reset on 1st of each month @ 00:00 UTC")


def stop_card_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)
    print("[card_scheduler] stopped")
