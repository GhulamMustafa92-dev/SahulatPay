"""Notification service — DB insert + FCM push. PROMPT 13."""
from __future__ import annotations

import asyncio
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.other import Notification
from models.user  import User


# ── FCM push helper ────────────────────────────────────────────────────────────
async def _push_fcm(
    fcm_token: str,
    title: str,
    body: str,
    data: dict,
    user_id: Optional[UUID] = None,
) -> None:
    """
    Fire-and-forget FCM push via firebase-admin.
    If the token is invalid/unregistered, nulls user.fcm_token in DB so we
    don't retry until the app sends a fresh token via PUT /users/fcm-token.
    """
    if not fcm_token:
        return
    try:
        from firebase_admin import messaging
        msg = messaging.Message(
            notification=messaging.Notification(title=title, body=body),
            data={str(k): str(v) for k, v in (data or {}).items()},
            token=fcm_token,
        )
        messaging.send(msg)
    except Exception as e:
        err_str = str(e).lower()
        _invalid = (
            "registration-token-not-registered" in err_str
            or "invalid registration" in err_str
            or "requested entity was not found" in err_str
            or "invalid argument" in err_str
        )
        if _invalid and user_id:
            try:
                from database import AsyncSessionLocal
                async with AsyncSessionLocal() as _db:
                    await _db.execute(
                        update(User)
                        .where(User.id == user_id, User.fcm_token == fcm_token)
                        .values(fcm_token=None)
                    )
                    await _db.commit()
            except Exception:
                pass


# ── Main public function ───────────────────────────────────────────────────────
async def send_notification(
    db: AsyncSession,
    user_id: UUID,
    title: str,
    body: str,
    type: str,
    data: Optional[dict[str, Any]] = None,
) -> Notification:
    """
    Insert a Notification row in Postgres, then fire FCM push non-blocking.

    type options: transaction | security | system | ai_insight | admin |
                  split | savings | investment | insurance | rewards | zakat
    """
    notif = Notification(
        user_id = user_id,
        title   = title,
        body    = body,
        type    = type,
        data    = data or {},
    )
    db.add(notif)
    await db.commit()
    await db.refresh(notif)

    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user and user.fcm_token:
        asyncio.create_task(_push_fcm(user.fcm_token, title, body, data or {}, user_id=user_id))

    return notif
