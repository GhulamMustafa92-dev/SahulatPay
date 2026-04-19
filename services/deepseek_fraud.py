"""DeepSeek AI fraud analysis service.

All calls are wrapped in try/except — if DeepSeek is unreachable or times out,
the function returns None and callers fall back to rule-based scoring only.
Never blocks a transaction solely because DeepSeek failed.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Optional

import httpx

from config import settings

_DEEPSEEK_URL  = "https://api.deepseek.com/v1/chat/completions"
_MODEL         = "deepseek-chat"
_SYNC_TIMEOUT  = 3.0   # seconds for high-value synchronous calls
_ASYNC_TIMEOUT = 15.0  # seconds for background async calls
_GRAPH_TIMEOUT = 30.0  # seconds for daily graph analysis


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── Raw HTTP helpers ──────────────────────────────────────────────────────────

async def _call_deepseek_json(prompt: str, timeout: float) -> Optional[dict]:
    """Returns parsed JSON dict or None on any failure."""
    if not settings.DEEPSEEK_API_KEY:
        return None
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                _DEEPSEEK_URL,
                headers={"Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}"},
                json={
                    "model": _MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "response_format": {"type": "json_object"},
                },
            )
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"]
            return json.loads(raw)
    except Exception as exc:
        print(f"[deepseek_fraud] JSON call failed: {exc}")
        return None


async def _call_deepseek_text(prompt: str, timeout: float) -> Optional[str]:
    """Returns plain text string or None on any failure."""
    if not settings.DEEPSEEK_API_KEY:
        return None
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                _DEEPSEEK_URL,
                headers={"Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}"},
                json={
                    "model": _MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
    except Exception as exc:
        print(f"[deepseek_fraud] text call failed: {exc}")
        return None


# ── Per-transaction anomaly scoring ──────────────────────────────────────────

async def _build_transaction_prompt(user, transaction, profile) -> str:
    from services.fraud_scoring import is_known_recipient
    from database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        known = await is_known_recipient(user.id, transaction.recipient_id, db)

    avg       = float(profile.avg_transaction_pkr) if profile and profile.avg_transaction_pkr else 0
    max_sent  = float(profile.max_transaction_pkr)  if profile and profile.max_transaction_pkr  else 0
    h_start   = profile.typical_hour_start           if profile else "unknown"
    h_end     = profile.typical_hour_end             if profile else "unknown"
    known_cnt = profile.known_recipients_count       if profile else 0

    return f"""
User historical profile:
- Average transaction: PKR {avg}
- Maximum ever sent: PKR {max_sent}
- Typical active hours: {h_start} to {h_end}
- Known recipients count: {known_cnt}

Current transaction:
- Amount: PKR {float(transaction.amount)}
- Time: {transaction.created_at or _utcnow()}
- Recipient known: {known}

Is this transaction anomalous?
Reply with JSON only, no other text:
{{
    "anomaly_score": 0,
    "reasons": [],
    "recommendation": "allow"
}}
"""


async def analyse_transaction(user, transaction, profile) -> Optional[dict]:
    """
    Returns {{"anomaly_score": 0-100, "reasons": [...], "recommendation": "allow|hold|block"}}
    or None if DeepSeek is unavailable.
    """
    prompt = await _build_transaction_prompt(user, transaction, profile)
    return await _call_deepseek_json(prompt, timeout=_ASYNC_TIMEOUT)


async def analyse_transaction_sync(user, transaction, profile) -> Optional[dict]:
    """
    Synchronous wrapper with _SYNC_TIMEOUT for high-value (>= PKR 100,000) transactions.
    Returns None on timeout — caller must let transaction complete.
    """
    try:
        return await asyncio.wait_for(
            analyse_transaction(user, transaction, profile),
            timeout=_SYNC_TIMEOUT,
        )
    except asyncio.TimeoutError:
        print(f"[deepseek_fraud] sync timeout for txn {getattr(transaction, 'id', '?')}")
        return None


async def _store_deepseek_result(
    transaction_id, result: Optional[dict]
) -> None:
    """Persist deepseek_score + deepseek_recommendation back to the transaction row."""
    if not result:
        return
    try:
        from database import AsyncSessionLocal
        from sqlalchemy import update
        from models.transaction import Transaction
        from uuid import UUID
        async with AsyncSessionLocal() as db:
            await db.execute(
                update(Transaction)
                .where(Transaction.id == transaction_id)
                .values(
                    deepseek_score=result.get("anomaly_score"),
                    deepseek_recommendation=result.get("recommendation"),
                )
            )
            await db.commit()
    except Exception as exc:
        print(f"[deepseek_fraud] store result error: {exc}")


def fire_deepseek_async(user, transaction, profile) -> None:
    """Fire-and-forget background DeepSeek scoring for low-value transactions."""
    async def _run():
        result = await analyse_transaction(user, transaction, profile)
        await _store_deepseek_result(transaction.id, result)
    try:
        asyncio.create_task(_run())
    except RuntimeError:
        pass


# ── Daily cross-user graph analysis ──────────────────────────────────────────

async def analyse_transaction_graph(chains_json: str) -> Optional[dict]:
    """
    Returns {{
        "suspicious_chains": [...],
        "involved_users": [...],
        "pattern_type": "...",
        "confidence": 0-100
    }} or None.
    """
    prompt = f"""
Analyse these transaction chains for fraud patterns:
{chains_json}

Look for:
- Circular flow (A→B→C→A)
- Fan-out (1 sender, 10+ recipients)
- Fan-in (10+ senders, 1 recipient)
- Rapid forwarding (received then immediately sent within 60 seconds)

Return JSON only, no other text:
{{
    "suspicious_chains": [],
    "involved_users": [],
    "pattern_type": "",
    "confidence": 0
}}
"""
    return await _call_deepseek_json(prompt, timeout=_GRAPH_TIMEOUT)


# ── STR narrative generation ──────────────────────────────────────────────────

async def generate_str_narrative(user, transaction, flags: list) -> str:
    """
    Returns a formal STR narrative string for SBP submission.
    Falls back to a template if DeepSeek is unavailable.
    """
    flag_reasons = [f.reason for f in flags] if flags else ["automated_flag"]

    prompt = f"""
Generate a formal Suspicious Transaction Report narrative for SBP Pakistan submission.

User details:
- Name: {user.full_name}
- Phone: {user.phone_number}
- Account type: {user.account_type}
- KYC tier: {user.verification_tier}
- Risk score: {user.risk_score}

Transaction details:
- Amount: PKR {float(transaction.amount)}
- Type: {transaction.type}
- Reference: {transaction.reference_number}
- Date: {transaction.created_at}
- Status: {transaction.status}

Fraud flags: {", ".join(flag_reasons)}

Write in formal regulatory language suitable for submission to the Financial Intelligence Unit of the State Bank of Pakistan.
Return plain text narrative only, no JSON.
"""
    result = await _call_deepseek_text(prompt, timeout=_GRAPH_TIMEOUT)
    if result:
        return result

    return (
        f"SUSPICIOUS TRANSACTION REPORT\n\n"
        f"Subject: {user.full_name} | {user.phone_number}\n"
        f"Account Type: {user.account_type} | KYC Tier: {user.verification_tier}\n"
        f"Risk Score: {user.risk_score}\n\n"
        f"Transaction Reference: {transaction.reference_number}\n"
        f"Amount: PKR {float(transaction.amount):,.2f}\n"
        f"Type: {transaction.type} | Status: {transaction.status}\n"
        f"Date: {transaction.created_at}\n\n"
        f"Fraud Indicators: {', '.join(flag_reasons)}\n\n"
        f"[AI narrative unavailable — manual review required]"
    )
