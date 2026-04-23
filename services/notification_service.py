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
import logging as _logging
_log = _logging.getLogger(__name__)


async def _push_fcm(
    fcm_token: str,
    title: str,
    body: str,
    data: dict,
    user_id: Optional[UUID] = None,
    data_only: bool = False,
) -> None:
    """
    Fire-and-forget FCM push via firebase-admin.
    Runs the blocking messaging.send() in a thread-pool executor so it does
    not block the async event loop.
    If the token is invalid/unregistered, nulls user.fcm_token in DB so we
    don't retry until the app sends a fresh token via PUT /users/fcm-token.
    """
    if not fcm_token:
        _log.debug("[FCM] skipped — no token for user %s", user_id)
        return
    try:
        import firebase_admin
        if not firebase_admin._apps:
            _log.warning("[FCM] firebase not initialized — push skipped for user %s", user_id)
            return
        from firebase_admin import messaging
        import asyncio, functools
        full_data = {str(k): str(v) for k, v in (data or {}).items()}
        if not data_only:
            full_data.update({"title": title, "body": body})

        msg = messaging.Message(
            notification=None if data_only else messaging.Notification(title=title, body=body),
            data=full_data,
            android=messaging.AndroidConfig(
                priority="high",
                notification=None if data_only else messaging.AndroidNotification(
                    channel_id="sahulatpay_main",
                    priority="high",
                    default_vibrate_timings=True,
                    default_sound=True,
                ),
            ),
            apns=messaging.APNSConfig(
                payload=messaging.APNSPayload(
                    aps=messaging.Aps(sound="default", badge=1),
                ),
            ),
            token=fcm_token,
        )
        loop = asyncio.get_event_loop()
        msg_id = await loop.run_in_executor(None, functools.partial(messaging.send, msg))
        _log.info("[FCM] sent to user %s — message_id=%s title=%r", user_id, msg_id, title)
    except Exception as e:
        err_str = str(e).lower()
        _log.warning("[FCM] send failed for user %s: %s", user_id, e)
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
                _log.info("[FCM] cleared stale token for user %s", user_id)
            except Exception:
                pass


# ── Main public function ───────────────────────────────────────────────────────
async def send_notification(
    db: AsyncSession,       # kept for call-site compatibility; NOT used internally
    user_id: UUID,
    title: str,
    body: str,
    type: str,
    data: Optional[dict[str, Any]] = None,
) -> None:
    """
    Insert a Notification row in Postgres, then fire FCM push.

    Always opens its OWN AsyncSessionLocal session so it is safe to run as
    asyncio.create_task() after the request session has been returned to the
    pool (avoids IllegalStateChangeError on session.close()).

    type options: transaction | security | system | ai_insight | admin |
                  split | savings | investment | insurance | rewards | zakat
    """
    try:
        from database import AsyncSessionLocal
        async with AsyncSessionLocal() as _db:
            notif = Notification(
                user_id = user_id,
                title   = title,
                body    = body,
                type    = type,
                data    = data or {},
            )
            _db.add(notif)
            await _db.commit()
            await _db.refresh(notif)

            user = (await _db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
            if user and user.fcm_token:
                await _push_fcm(
                    user.fcm_token, title, body, data or {},
                    user_id=user_id,
                    data_only=(type == "split"),
                )
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("[send_notification] failed for user %s: %s", user_id, exc)
