"""Platform ledger service — double-entry accounting for all platform pool movements.

Every time money leaves a user's wallet it must credit a platform pool.
Every time money returns to a user's wallet it must debit a platform pool.
Both sides happen in the same database transaction — atomic, no partial state.

Usage:
    await ledger_credit(db, "savings_pool", amount, idempotency_key, user_id=..., txn_id=..., note=...)
    await ledger_debit(db,  "savings_pool", amount, idempotency_key, user_id=..., txn_id=..., note=...)

Idempotency:
    If the idempotency_key already exists in platform_ledger_entries, the call
    is a no-op and returns the existing entry — safe for network retries.
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import HTTPException
from models.platform import PlatformAccount, PlatformLedgerEntry


# ── Internal helpers ──────────────────────────────────────────────────────────

async def _get_account(db: AsyncSession, account_type: str) -> PlatformAccount:
    acct = (await db.execute(
        select(PlatformAccount)
        .where(PlatformAccount.type == account_type)
        .with_for_update()
    )).scalar_one_or_none()
    if not acct:
        try:
            acct = PlatformAccount(type=account_type, balance=Decimal("0.00"))
            db.add(acct)
            await db.flush()
        except Exception:
            raise HTTPException(
                status_code=503,
                detail=f"Platform account '{account_type}' not configured. Run: alembic upgrade head",
            )
    return acct


async def _check_idempotency(db: AsyncSession, key: str) -> Optional[PlatformLedgerEntry]:
    return (await db.execute(
        select(PlatformLedgerEntry).where(PlatformLedgerEntry.idempotency_key == key)
    )).scalar_one_or_none()


# ── Public API ────────────────────────────────────────────────────────────────

async def ledger_credit(
    db: AsyncSession,
    account_type: str,
    amount: Decimal,
    idempotency_key: str,
    *,
    transaction_id: Optional[UUID] = None,
    user_id: Optional[UUID] = None,
    reference: Optional[str] = None,
    note: Optional[str] = None,
) -> PlatformLedgerEntry:
    """Credit (money flows IN) to the named platform pool.
    Idempotent — safe to call multiple times with the same key.
    Must be called inside an existing db transaction; caller commits.
    """
    existing = await _check_idempotency(db, idempotency_key)
    if existing:
        return existing

    acct = await _get_account(db, account_type)
    acct.balance = (acct.balance or Decimal("0")) + amount

    entry = PlatformLedgerEntry(
        account_id=acct.id,
        direction="credit",
        amount=amount,
        idempotency_key=idempotency_key,
        transaction_id=transaction_id,
        user_id=user_id,
        reference=reference,
        note=note,
    )
    db.add(entry)
    return entry


async def ledger_debit(
    db: AsyncSession,
    account_type: str,
    amount: Decimal,
    idempotency_key: str,
    *,
    transaction_id: Optional[UUID] = None,
    user_id: Optional[UUID] = None,
    reference: Optional[str] = None,
    note: Optional[str] = None,
) -> PlatformLedgerEntry:
    """Debit (money flows OUT) from the named platform pool.
    Idempotent — safe to call multiple times with the same key.
    Raises ValueError if pool has insufficient balance.
    Must be called inside an existing db transaction; caller commits.
    """
    existing = await _check_idempotency(db, idempotency_key)
    if existing:
        return existing

    acct = await _get_account(db, account_type)
    if (acct.balance or Decimal("0")) < amount:
        raise ValueError(
            f"Platform pool '{account_type}' has insufficient balance "
            f"(available: PKR {acct.balance:,.2f}, requested: PKR {amount:,.2f}). "
            "Contact operations team."
        )
    acct.balance -= amount

    entry = PlatformLedgerEntry(
        account_id=acct.id,
        direction="debit",
        amount=amount,
        idempotency_key=idempotency_key,
        transaction_id=transaction_id,
        user_id=user_id,
        reference=reference,
        note=note,
    )
    db.add(entry)
    return entry


def make_idem_key(*parts: str) -> str:
    """Deterministic idempotency key from ordered string parts.
    e.g. make_idem_key("savings_deposit", str(user_id), str(goal_id), ref)
    Produces a UUID5 (namespaced) so same inputs always produce same key.
    """
    combined = "|".join(str(p) for p in parts)
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, combined))
