"""AI router — DeepSeek chat, insights, health score, roast. PROMPT 12."""
from datetime import datetime, timezone, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from limiter import limiter
from models.ai import AiInsight, ChatSession
from models.user import User
from services.auth_service import get_current_user
from services.ai_service import (
    build_transaction_summary,
    generate_insights,
    generate_chat_response,
)

router = APIRouter()


def _utcnow():
    return datetime.now(timezone.utc)


# ── Helpers ────────────────────────────────────────────────────────────────────
async def _get_or_create_chat(db: AsyncSession, user_id: UUID) -> ChatSession:
    row = (await db.execute(
        select(ChatSession).where(ChatSession.user_id == user_id)
    )).scalar_one_or_none()
    if not row:
        row = ChatSession(user_id=user_id, messages=[])
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return row


async def _get_or_create_insight(db: AsyncSession, user_id: UUID) -> AiInsight | None:
    return (await db.execute(
        select(AiInsight).where(AiInsight.user_id == user_id)
    )).scalar_one_or_none()


async def _build_and_store_insights(
    db: AsyncSession, user_id: UUID, existing: AiInsight | None
) -> AiInsight:
    summary = await build_transaction_summary(user_id, db)
    data    = await generate_insights(summary)

    if existing:
        existing.health_score       = data.get("health_score")
        existing.health_label       = data.get("health_label")
        existing.top_categories     = data.get("top_categories", [])
        existing.monthly_comparison = data.get("monthly_comparison", {})
        existing.savings_tips       = data.get("savings_tips", [])
        existing.unusual_spending   = data.get("unusual_spending", [])
        existing.roast_content      = data.get("roast_content")
        existing.expires_at         = _utcnow() + timedelta(days=7)
        existing.updated_at         = _utcnow()
        await db.commit()
        await db.refresh(existing)
        return existing

    row = AiInsight(
        user_id            = user_id,
        health_score       = data.get("health_score"),
        health_label       = data.get("health_label"),
        top_categories     = data.get("top_categories", []),
        monthly_comparison = data.get("monthly_comparison", {}),
        savings_tips       = data.get("savings_tips", []),
        unusual_spending   = data.get("unusual_spending", []),
        roast_content      = data.get("roast_content"),
        expires_at         = _utcnow() + timedelta(days=7),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


# ══════════════════════════════════════════════════════════════════════════════
# POST /ai/chat
# ══════════════════════════════════════════════════════════════════════════════
class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)


@router.post("/chat")
@limiter.limit("30/minute")
async def chat(
    request: Request,
    body: ChatRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Send a message to the AI assistant. Detects payment intent automatically."""
    session  = await _get_or_create_chat(db, current_user.id)
    history  = session.messages or []

    try:
        summary = await build_transaction_summary(current_user.id, db)
    except Exception:
        summary = {
            "balance": 0, "total_income": 0, "total_spending": 0,
            "transaction_count": 0, "top_categories": [],
            "monthly_comparison": {"this_month": 0, "last_month": 0, "change_pct": 0},
            "period_days": 90,
        }

    user_msg = {
        "role":      "user",
        "content":   body.message,
        "type":      "message",
        "amount":    None,
        "recipient": None,
        "timestamp": _utcnow().isoformat(),
    }

    try:
        ai_reply = await generate_chat_response(body.message, history, summary)
    except Exception:
        ai_reply = {
            "role": "assistant",
            "content": f"Your wallet balance is PKR {summary.get('balance', 0):,.0f}. How can I help?",
            "type": "message", "amount": None, "recipient": None,
            "timestamp": _utcnow().isoformat(),
        }

    new_messages = history + [user_msg, ai_reply]

    # Persist (replace list — JSONB update)
    session.messages = new_messages
    await db.commit()

    return {
        "reply":    ai_reply,
        "session_id": session.id,
    }


# ══════════════════════════════════════════════════════════════════════════════
# GET /ai/chat/history
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/chat/history")
async def chat_history(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    session = await _get_or_create_chat(db, current_user.id)
    return {
        "session_id": session.id,
        "messages":   session.messages or [],
        "count":      len(session.messages or []),
    }


# ══════════════════════════════════════════════════════════════════════════════
# DELETE /ai/chat/history
# ══════════════════════════════════════════════════════════════════════════════
@router.delete("/chat/history")
async def clear_chat_history(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    session = await _get_or_create_chat(db, current_user.id)
    session.messages = []
    await db.commit()
    return {"message": "Chat history cleared."}


# ══════════════════════════════════════════════════════════════════════════════
# GET /ai/insights
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/insights")
async def get_insights(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return cached insights if still valid (7-day TTL). Otherwise generate fresh."""
    existing = await _get_or_create_insight(db, current_user.id)

    if existing and existing.expires_at and existing.expires_at > _utcnow():
        # Cache hit
        return _insight_response(existing, cached=True)

    # Cache miss — generate
    row = await _build_and_store_insights(db, current_user.id, existing)
    return _insight_response(row, cached=False)


# ══════════════════════════════════════════════════════════════════════════════
# POST /ai/insights/refresh
# ══════════════════════════════════════════════════════════════════════════════
@router.post("/insights/refresh")
@limiter.limit("5/hour")
async def refresh_insights(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Force regenerate insights ignoring cache."""
    existing = await _get_or_create_insight(db, current_user.id)
    row      = await _build_and_store_insights(db, current_user.id, existing)
    return _insight_response(row, cached=False)


def _insight_response(row: AiInsight, cached: bool) -> dict:
    return {
        "health_score":       row.health_score,
        "health_label":       row.health_label,
        "top_categories":     row.top_categories,
        "monthly_comparison": row.monthly_comparison,
        "savings_tips":       row.savings_tips,
        "unusual_spending":   row.unusual_spending,
        "roast_content":      row.roast_content,
        "expires_at":         row.expires_at.isoformat() if row.expires_at else None,
        "cached":             cached,
        "generated_at":       row.updated_at.isoformat() if row.updated_at else None,
    }


# ══════════════════════════════════════════════════════════════════════════════
# GET /ai/health-score
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/health-score")
async def health_score(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return health score + label. Respects 7-day cache — regenerates when expired."""
    existing = await _get_or_create_insight(db, current_user.id)

    if not existing or not existing.expires_at or existing.expires_at <= _utcnow():
        existing = await _build_and_store_insights(db, current_user.id, existing)

    return {
        "health_score": existing.health_score,
        "health_label": existing.health_label,
        "expires_at":   existing.expires_at.isoformat() if existing.expires_at else None,
        "cached":       True,
    }


# ══════════════════════════════════════════════════════════════════════════════
# GET /ai/roast
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/roast")
async def roast(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return AI spending roast. Generates insights if none exist yet."""
    existing = await _get_or_create_insight(db, current_user.id)
    if not existing or not existing.roast_content:
        existing = await _build_and_store_insights(db, current_user.id, existing)

    if not existing.roast_content:
        raise HTTPException(status_code=404, detail="No roast available yet. Check back after more transactions.")

    return {
        "roast": existing.roast_content,
        "health_score": existing.health_score,
        "health_label": existing.health_label,
    }
