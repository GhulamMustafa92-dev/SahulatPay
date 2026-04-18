"""Hawl notification scheduler — daily check for Hawl due dates and reminders."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from database import AsyncSessionLocal
from models.user import User
from models.zakat import HawlTracking
from services.notification_service import send_notification

scheduler = AsyncIOScheduler(timezone="UTC")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _process_hawl_notifications() -> None:
    """
    Runs daily at 01:00 UTC.
    For each user with hawl_active = True:
      - If today >= zakat_due_date → send "Zakat is now due" notification.
      - Elif last_reminder_sent_at is null or >= 30 days ago → send reminder.
    """
    today = _utcnow()
    print(f"[hawl_scheduler] running Hawl notification check @ {today.isoformat()}")

    async with AsyncSessionLocal() as db:
        hawl_rows = (await db.execute(
            select(HawlTracking).where(HawlTracking.hawl_active == True)
        )).scalars().all()

        print(f"[hawl_scheduler] {len(hawl_rows)} active Hawl(s) to check")

        for hawl in hawl_rows:
            try:
                user = (await db.execute(
                    select(User).where(User.id == hawl.user_id)
                )).scalar_one_or_none()

                if not user or not user.is_active:
                    continue

                # ── Hawl complete — zakat is now due ──────────────────────────
                if hawl.zakat_due_date and today >= hawl.zakat_due_date:
                    await send_notification(
                        db,
                        user_id = hawl.user_id,
                        title   = "🕌 Hawl Complete — Zakat Due",
                        body    = (
                            "Your Hawl (lunar year) is complete. "
                            "Zakat is now due on your wealth. "
                            "Please calculate and pay your Zakat."
                        ),
                        type    = "zakat",
                        data    = {
                            "event":         "hawl_complete",
                            "zakat_due_date": hawl.zakat_due_date.isoformat(),
                        },
                    )
                    print(f"[hawl_scheduler] sent Hawl-complete notification to user {hawl.user_id}")

                # ── Monthly reminder — wealth profile review ──────────────────
                elif (
                    hawl.last_reminder_sent_at is None
                    or (today - hawl.last_reminder_sent_at).days >= 30
                ):
                    await send_notification(
                        db,
                        user_id = hawl.user_id,
                        title   = "📊 Zakat Reminder — Review Wealth Profile",
                        body    = (
                            "Please review your wealth profile to keep your "
                            "Zakat calculation accurate."
                        ),
                        type    = "zakat",
                        data    = {
                            "event":         "hawl_reminder",
                            "zakat_due_date": hawl.zakat_due_date.isoformat() if hawl.zakat_due_date else "",
                        },
                    )
                    hawl.last_reminder_sent_at = today
                    await db.commit()
                    print(f"[hawl_scheduler] sent monthly reminder to user {hawl.user_id}")

            except Exception as e:
                await db.rollback()
                print(f"[hawl_scheduler] ERROR for hawl {hawl.id}: {e}")


def start_hawl_scheduler() -> AsyncIOScheduler:
    scheduler.add_job(
        _process_hawl_notifications,
        trigger=CronTrigger(hour=1, minute=0, timezone="UTC"),
        id="hawl_daily_check",
        replace_existing=True,
        max_instances=1,
    )
    if not scheduler.running:
        scheduler.start()
    print("[hawl_scheduler] started — Hawl notifications checked daily at 01:00 UTC")
    return scheduler


def stop_hawl_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
    print("[hawl_scheduler] stopped")
