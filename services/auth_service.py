"""Auth service — phone normalization, password/OTP hashing, JWT, Infobip SMS, CNIC age."""
from __future__ import annotations

import hashlib
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

import bcrypt
import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_db
from models.user import User


# ══════════════════════════════════════════════════════════════════════════════
# Dev-mode in-memory OTP store (plain OTP per phone) — read by /auth/dev/otp/*
# ══════════════════════════════════════════════════════════════════════════════
DEV_OTP_STORE: dict[str, str] = {}


# ══════════════════════════════════════════════════════════════════════════════
# Phone normalization
# ══════════════════════════════════════════════════════════════════════════════
PHONE_RE_PK_LOCAL  = re.compile(r"^03\d{9}$")
PHONE_RE_PK_INTL   = re.compile(r"^\+92\d{10}$")

def normalize_phone(phone: str) -> str:
    """Accept '+92XXXXXXXXXX' or '03XXXXXXXXX' → always return '+92XXXXXXXXXX'."""
    phone = phone.strip().replace(" ", "").replace("-", "")
    if PHONE_RE_PK_INTL.match(phone):
        return phone
    if PHONE_RE_PK_LOCAL.match(phone):
        return "+92" + phone[1:]
    raise ValueError("Invalid phone format. Use +92XXXXXXXXXX or 03XXXXXXXXX")


# ══════════════════════════════════════════════════════════════════════════════
# CNIC age extraction (Pakistani CNIC: XXXXX-XXXXXXX-X)
# ══════════════════════════════════════════════════════════════════════════════
CNIC_RE = re.compile(r"^\d{5}-\d{7}-\d$")

def extract_age_from_cnic(cnic: str) -> tuple[date, int]:
    """Extract approximate DOB + age from CNIC digits 6-7 (birth year suffix)."""
    if not CNIC_RE.match(cnic):
        raise ValueError("Invalid CNIC format. Expected XXXXX-XXXXXXX-X")
    digits = cnic.replace("-", "")
    year_suffix = int(digits[5:7])
    birth_year  = 1900 + year_suffix if year_suffix >= 50 else 2000 + year_suffix
    today       = date.today()
    age         = today.year - birth_year
    return date(birth_year, 1, 1), age


def mask_cnic(cnic: str) -> str:
    if not CNIC_RE.match(cnic):
        return cnic
    return cnic[:6] + "XXXXXXX-X"


def mask_phone(phone: str) -> str:
    # +923001234567 → +9230012****67
    if len(phone) < 6:
        return phone
    return phone[:6] + "****" + phone[-2:]


# ══════════════════════════════════════════════════════════════════════════════
# Password + PIN + OTP hashing (bcrypt cost=12)
# ══════════════════════════════════════════════════════════════════════════════
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")

def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False

hash_pin   = hash_password
verify_pin = verify_password
hash_otp   = hash_password
verify_otp = verify_password


DEV_FIXED_OTP = "123456"


def generate_otp() -> str:
    """6-digit numeric OTP. In DEV_MODE always returns 123456 for cross-worker consistency."""
    if settings.DEV_MODE:
        return DEV_FIXED_OTP
    return f"{secrets.randbelow(1_000_000):06d}"


# ══════════════════════════════════════════════════════════════════════════════
# Firebase Phone Auth — verify ID token from Android app
# ══════════════════════════════════════════════════════════════════════════════
# Dev bypass: in DEV_MODE, send this token to skip Firebase verification
DEV_FIREBASE_BYPASS = "dev-bypass-token"


async def verify_firebase_phone_token(id_token: str, expected_phone: str) -> bool:
    """Verify a Firebase ID token. Returns True iff:
    - Token is valid (signed + not expired)
    - sign_in_provider == 'phone'
    - Decoded phone_number == expected_phone (already normalized to +92...)
    """
    # Dev bypass
    if settings.DEV_MODE and id_token == DEV_FIREBASE_BYPASS:
        print(f"[FIREBASE-DEV-BYPASS] accepting phone {expected_phone}")
        return True

    try:
        from firebase_admin import auth as fb_auth
        decoded = fb_auth.verify_id_token(id_token)
    except Exception as e:
        print(f"[FIREBASE] verify_id_token failed: {e}")
        return False

    if decoded.get("firebase", {}).get("sign_in_provider") != "phone":
        return False

    token_phone = decoded.get("phone_number")
    return token_phone == expected_phone


# ══════════════════════════════════════════════════════════════════════════════
# Infobip OTP SMS (legacy — kept for password reset / bank link fallback)
# ══════════════════════════════════════════════════════════════════════════════
async def send_otp_sms(phone: str, otp: str) -> bool:
    """Send OTP via Infobip REST API. In DEV_MODE (no API key), store in-memory instead."""
    # Dev fallback — no Infobip credit required
    if settings.DEV_MODE and not settings.INFOBIP_API_KEY:
        DEV_OTP_STORE[phone] = otp
        print(f"[DEV-SMS] {phone}: {otp}")
        return True

    url = f"{settings.INFOBIP_BASE_URL}/sms/2/text/advanced"
    headers = {
        "Authorization": f"App {settings.INFOBIP_API_KEY}",
        "Content-Type":  "application/json",
        "Accept":        "application/json",
    }
    payload = {
        "messages": [{
            "from": settings.INFOBIP_SENDER_ID,
            "destinations": [{"to": phone}],
            "text": f"Your FinTech OTP is: {otp}. Valid for 5 minutes. Do not share."
        }]
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(url, headers=headers, json=payload)
            ok = r.status_code == 200
            if settings.DEV_MODE:
                DEV_OTP_STORE[phone] = otp   # also keep in-memory for testing
                print(f"[INFOBIP] {phone}: {otp} (status={r.status_code})")
            return ok
    except Exception as e:
        print(f"[INFOBIP] error: {e}")
        # Fallback to dev store so registration can still proceed in dev
        if settings.DEV_MODE:
            DEV_OTP_STORE[phone] = otp
            return True
        return False


# ══════════════════════════════════════════════════════════════════════════════
# JWT — access + refresh + session
# ══════════════════════════════════════════════════════════════════════════════
def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def create_access_token(user_id: UUID, is_superuser: bool = False) -> str:
    expire = _now_utc() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub":          str(user_id),
        "type":         "access",
        "is_superuser": is_superuser,
        "iat":          int(_now_utc().timestamp()),
        "exp":          int(expire.timestamp()),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_refresh_token(user_id: UUID) -> tuple[str, str, datetime]:
    """Return (raw_token, sha256_hash, expires_at)."""
    raw = secrets.token_urlsafe(48)
    h   = hash_refresh_token(raw)
    exp = _now_utc() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    return raw, h, exp


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session_token(user_id: UUID, device_fingerprint: str, purpose: str = "new_device") -> str:
    """Short-lived JWT (10 min) used between /auth/login and /auth/login/new-device/verify."""
    expire = _now_utc() + timedelta(minutes=10)
    payload = {
        "sub":  str(user_id),
        "type": "session",
        "dfp":  device_fingerprint,
        "prp":  purpose,
        "exp":  int(expire.timestamp()),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Invalid token: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# FastAPI dependencies
# ══════════════════════════════════════════════════════════════════════════════
bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    if not credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = decode_token(credentials.credentials)
    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Not an access token")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token subject")

    result = await db.execute(select(User).where(User.id == UUID(user_id)))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account deactivated")
    if user.is_locked:
        raise HTTPException(status_code=423, detail="Account locked — reset password to unlock")
    return user


async def get_current_verified_user(user: User = Depends(get_current_user)) -> User:
    if not user.is_verified:
        raise HTTPException(status_code=403, detail="Phone not verified")
    return user



# ══════════════════════════════════════════════════════════════════════════════
# KYC tier → daily_limit mapping
# ══════════════════════════════════════════════════════════════════════════════
TIER_LIMITS: dict[int, int] = {
    0: 0,
    1: 25_000,
    2: 100_000,
    3: 500_000,
    4: 2_000_000,
}
