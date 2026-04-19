"""Daily reconciliation scheduler — runs at 01:00 UTC every day.

Checks that:
  1. Sum of all user wallet balances
     == main_float.balance + savings_pool.balance + investment_pool.balance
        + insurance_pool.balance + gold_platform.balance
     (i.e. every PKR held in a user wallet is backed by a platform pool)

  2. Each individual pool's balance == sum of (credits - debits) in
     platform_ledger_entries for that pool.

  3. gold_platform.balance >= sum of all GoldHolding.total_invested_pkr

On any discrepancy:
  - Creates a Notification to all superusers with the full diff report
  - Logs severity to stdout for Railway log monitoring
"""
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select, func

from database import AsyncSessionLocal
from models.gold import GoldHolding
from models.platform import PlatformAccount, PlatformLedgerEntry
from models.user import User
from models.wallet import Wallet

scheduler = AsyncIOScheduler(timezone="UTC")

_POOLS = (
    "savings_pool", "investment_pool", "insurance_pool",
    "gold_platform", "main_float", "platform_revenue",
)

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _run_reconciliation():
    print(f"[reconciliation] starting @ {_utcnow().isoformat()}")
    issues = []

    async with AsyncSessionLocal() as db:
        try:
            # ── 1. Total user wallets ─────────────────────────────────────────
            total_wallets = (await db.execute(
                select(func.coalesce(func.sum(Wallet.balance), 0))
            )).scalar() or 0

            # ── 2. Pool balances from platform_accounts ───────────────────────
            pool_rows = (await db.execute(
                select(PlatformAccount.type, PlatformAccount.balance)
            )).all()
            pool_balances = {row[0]: (row[1] or 0) for row in pool_rows}

            # platform_revenue is retained earnings — excluded from user-backing check
            user_backing_pools = [k for k in pool_balances if k != "platform_revenue"]
            total_pools = sum(pool_balances.get(p, 0) for p in user_backing_pools)

            diff_wallets_vs_pools = float(total_wallets) - float(total_pools)
            if abs(diff_wallets_vs_pools) > 0.01:
                issues.append(
                    f"❌ WALLET vs POOLS MISMATCH: "
                    f"User wallets sum = PKR {float(total_wallets):,.2f} | "
                    f"Pool totals = PKR {float(total_pools):,.2f} | "
                    f"Diff = PKR {diff_wallets_vs_pools:,.2f}"
                )
            else:
                print(f"[reconciliation] ✅ Wallets vs pools balanced: PKR {float(total_wallets):,.2f}")

            # ── 3. Ledger consistency — each pool balance == credits - debits ──
            for pool_type in _POOLS:
                total_credits = (await db.execute(
                    select(func.coalesce(func.sum(PlatformLedgerEntry.amount), 0))
                    .join(PlatformAccount, PlatformLedgerEntry.account_id == PlatformAccount.id)
                    .where(
                        PlatformAccount.type == pool_type,
                        PlatformLedgerEntry.direction == "credit",
                    )
                )).scalar() or 0

                total_debits = (await db.execute(
                    select(func.coalesce(func.sum(PlatformLedgerEntry.amount), 0))
                    .join(PlatformAccount, PlatformLedgerEntry.account_id == PlatformAccount.id)
                    .where(
                        PlatformAccount.type == pool_type,
                        PlatformLedgerEntry.direction == "debit",
                    )
                )).scalar() or 0

                expected = float(total_credits) - float(total_debits)
                actual   = float(pool_balances.get(pool_type, 0))
                diff     = abs(expected - actual)

                if diff > 0.01:
                    issues.append(
                        f"❌ LEDGER MISMATCH [{pool_type}]: "
                        f"balance = PKR {actual:,.2f} | "
                        f"credits - debits = PKR {expected:,.2f} | "
                        f"diff = PKR {diff:,.2f}"
                    )
                else:
                    print(f"[reconciliation] ✅ {pool_type} ledger consistent: PKR {actual:,.2f}")

            # ── 4. Gold platform solvency ─────────────────────────────────────
            total_invested_gold = (await db.execute(
                select(func.coalesce(func.sum(GoldHolding.total_invested_pkr), 0))
            )).scalar() or 0

            gold_pool_bal = float(pool_balances.get("gold_platform", 0))
            if gold_pool_bal < float(total_invested_gold) - 0.01:
                issues.append(
                    f"❌ GOLD POOL UNDERFUNDED: "
                    f"gold_platform = PKR {gold_pool_bal:,.2f} | "
                    f"users invested = PKR {float(total_invested_gold):,.2f} | "
                    f"shortfall = PKR {float(total_invested_gold) - gold_pool_bal:,.2f}"
                )
            else:
                print(f"[reconciliation] ✅ Gold pool solvent: PKR {gold_pool_bal:,.2f} covers "
                      f"PKR {float(total_invested_gold):,.2f} user investments")

            # ── 5. Notify admins if issues found ──────────────────────────────
            if issues:
                report = "\n".join(issues)
                print(f"[reconciliation] ⚠️ ISSUES FOUND:\n{report}")

                admins = (await db.execute(
                    select(User).where(User.is_superuser == True)
                )).scalars().all()

                from models.other import Notification
                for admin in admins:
                    db.add(Notification(
                        user_id=admin.id,
                        title="⚠️ Daily Reconciliation — Discrepancies Found",
                        body=report[:1000],
                        category="security",
                        data={"issues_count": len(issues)},
                    ))
                await db.commit()
            else:
                print(f"[reconciliation] ✅ All checks passed — system balanced")

        except Exception as e:
            print(f"[reconciliation] ❌ Exception: {e}")
            await db.rollback()


def start_reconciliation_scheduler():
    scheduler.add_job(
        _run_reconciliation,
        trigger=CronTrigger(hour=1, minute=0, timezone="UTC"),
        id="daily_reconciliation",
        replace_existing=True,
    )
    scheduler.start()
    print("[reconciliation_scheduler] started — daily at 01:00 UTC")


def stop_reconciliation_scheduler():
    scheduler.shutdown(wait=False)
    print("[reconciliation_scheduler] stopped")
