"""Subscription scheduler — daily auto-charge for active card subscriptions."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from decimal import Decimal

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from database import AsyncSessionLocal
from models.card import CardSubscription, VirtualCard
from models.transaction import Transaction
from models.wallet import Wallet
from services.wallet_service import generate_reference, _send_fcm

scheduler = AsyncIOScheduler(timezone="UTC")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _charge_subscriptions() -> None:
    """Run daily at 00:05 UTC — charge all active subscriptions due today."""
    today = date.today()
    print(f"[subscription_scheduler] running for {today}")

    async with AsyncSessionLocal() as db:
        subs = (await db.execute(
            select(CardSubscription)
            .where(CardSubscription.renewal_date == today, CardSubscription.is_active == True)
        )).scalars().all()

        print(f"[subscription_scheduler] {len(subs)} subscriptions due today")

        for sub in subs:
            try:
                wallet = (await db.execute(
                    select(Wallet).where(Wallet.user_id == sub.user_id)
                )).scalar_one_or_none()

                card = (await db.execute(
                    select(VirtualCard).where(VirtualCard.id == sub.card_id)
                )).scalar_one_or_none()

                if not wallet or not card:
                    continue

                # ── Insufficient balance — pause subscription ──────────────
                if wallet.balance < sub.amount:
                    sub.is_active = False
                    await db.commit()
                    if card.user and card.user.fcm_token:
                        asyncio.create_task(_send_fcm(
                            card.user.fcm_token,
                            title="⚠️ Subscription Paused",
                            body=f"Insufficient balance for {sub.service_name}. Subscription paused.",
                        ))
                    print(f"[subscription_scheduler] paused {sub.service_name} — insufficient balance")
                    continue

                # ── Charge wallet ──────────────────────────────────────────
                wallet.balance       -= sub.amount
                wallet.daily_spent    = (wallet.daily_spent or Decimal("0")) + sub.amount

                card.monthly_spent    = (card.monthly_spent or Decimal("0")) + sub.amount

                ref = generate_reference()
                txn = Transaction(
                    reference_number = ref,
                    type             = "subscription_charge",
                    amount           = sub.amount,
                    fee              = Decimal("0"),
                    cashback_earned  = Decimal("0"),
                    status           = "completed",
                    sender_id        = sub.user_id,
                    purpose          = "Bill",
                    description      = f"Auto-charge: {sub.service_name}",
                    completed_at     = _utcnow(),
                    tx_metadata      = {
                        "card_id":      str(sub.card_id),
                        "last_four":    card.last_four,
                        "service_name": sub.service_name,
                        "service_code": sub.service_code,
                    },
                )
                db.add(txn)

                # ── Advance renewal date ───────────────────────────────────
                from dateutil.relativedelta import relativedelta
                delta = relativedelta(months=1) if sub.billing_cycle == "monthly" else relativedelta(years=1)
                sub.renewal_date = sub.renewal_date + delta

                await db.commit()

                # ── FCM notification ───────────────────────────────────────
                from models.user import User
                user = (await db.execute(
                    select(User).where(User.id == sub.user_id)
                )).scalar_one_or_none()

                if user and user.fcm_token:
                    asyncio.create_task(_send_fcm(
                        user.fcm_token,
                        title=f"💳 {sub.service_name} Charged",
                        body=f"PKR {sub.amount:,.0f} charged on card ****{card.last_four}. Ref: {ref}",
                    ))

                print(f"[subscription_scheduler] charged {sub.service_name} PKR {sub.amount} — next: {sub.renewal_date}")

            except Exception as e:
                await db.rollback()
                print(f"[subscription_scheduler] ERROR for sub {sub.id}: {e}")


# ── DEV_MODE mock data seeder ─────────────────────────────────────────────────
MOCK_SUBSCRIPTIONS = [
    {"service_name": "Netflix Premium", "service_code": "netflix",  "amount": Decimal("1500"), "billing_cycle": "monthly"},
    {"service_name": "Spotify Family",  "service_code": "spotify",  "amount": Decimal("750"),  "billing_cycle": "monthly"},
    {"service_name": "YouTube Premium", "service_code": "youtube",  "amount": Decimal("449"),  "billing_cycle": "monthly"},
    {"service_name": "iCloud 50GB",     "service_code": "icloud",   "amount": Decimal("130"),  "billing_cycle": "monthly"},
]


async def seed_mock_subscriptions(user_id, card_id) -> None:
    """Seed 4 mock subscriptions for DEV_MODE — called after card issue if DEV_MODE=true."""
    from dateutil.relativedelta import relativedelta
    today = date.today()
    async with AsyncSessionLocal() as db:
        existing = (await db.execute(
            select(CardSubscription).where(CardSubscription.card_id == card_id)
        )).scalars().all()
        if existing:
            return
        for m in MOCK_SUBSCRIPTIONS:
            sub = CardSubscription(
                card_id       = card_id,
                user_id       = user_id,
                service_name  = m["service_name"],
                service_code  = m["service_code"],
                amount        = m["amount"],
                billing_cycle = m["billing_cycle"],
                renewal_date  = today + relativedelta(months=1),
            )
            db.add(sub)
        await db.commit()
        print(f"[subscription_scheduler] seeded 4 mock subscriptions for card {card_id}")


def start_subscription_scheduler() -> AsyncIOScheduler:
    scheduler.add_job(
        _charge_subscriptions,
        trigger=CronTrigger(hour=0, minute=5, timezone="UTC"),
        id="subscription_daily_charge",
        replace_existing=True,
    )
    scheduler.start()
    print("[subscription_scheduler] started — daily 00:05 UTC")
    return scheduler


def stop_subscription_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
        print("[subscription_scheduler] stopped")
