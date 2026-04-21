"""Users router — profile, QR, photo, devices, FCM token. PROMPT 15."""
import base64
import io
from datetime import datetime, timezone, timedelta
from typing import Optional
from uuid import UUID

import bcrypt
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select, or_, update
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_db
from limiter import limiter
from models.other import OtpCode
from models.user import DeviceRegistry, RefreshToken, User
from models.wallet import Wallet
from services.auth_service import (
    get_current_user, normalize_phone,
    generate_otp, hash_otp, verify_otp, send_otp_sms,
    verify_password,
)
from services.wallet_service import TIER_LIMITS

router = APIRouter()

MAX_PHOTO_MB    = 5
MAX_PHOTO_BYTES = MAX_PHOTO_MB * 1024 * 1024


def _utcnow():
    return datetime.now(timezone.utc)


# ── OTP helpers (inline — avoids circular import with auth.py) ─────────────────
async def _send_otp(db: AsyncSession, phone: str, purpose: str) -> None:
    await db.execute(
        update(OtpCode)
        .where(OtpCode.phone_number == phone, OtpCode.purpose == purpose, OtpCode.is_used == False)
        .values(is_used=True)
    )
    otp = generate_otp()
    db.add(OtpCode(
        phone_number = phone,
        code_hash    = hash_otp(otp),
        purpose      = purpose,
        expires_at   = _utcnow() + timedelta(minutes=5),
    ))
    await db.commit()
    await send_otp_sms(phone, otp)


async def _check_otp(db: AsyncSession, phone: str, otp: str, purpose: str) -> bool:
    result = await db.execute(
        select(OtpCode).where(
            OtpCode.phone_number == phone,
            OtpCode.purpose == purpose,
            OtpCode.is_used == False,
        ).order_by(OtpCode.created_at.desc())
    )
    row = result.scalars().first()
    if not row or row.expires_at < _utcnow() or row.attempts >= 3:
        return False
    if not verify_otp(otp, row.code_hash):
        row.attempts += 1
        if row.attempts >= 3:
            row.is_used = True
        await db.commit()
        return False
    row.is_used = True
    await db.commit()
    return True


# ══════════════════════════════════════════════════════════════════════════════
# GET /users/me
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/me")
async def get_profile(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Full profile. NEVER returns password_hash, pin_hash, cnic_encrypted."""
    wallet = (await db.execute(
        select(Wallet).where(Wallet.user_id == current_user.id)
    )).scalar_one_or_none()

    return {
        "id":                   current_user.id,
        "phone_number":         current_user.phone_number,
        "email":                current_user.email,
        "full_name":            current_user.full_name,
        "country":              current_user.country,
        "date_of_birth":        current_user.date_of_birth.isoformat() if current_user.date_of_birth else None,
        "age":                  current_user.age,
        "account_type":         current_user.account_type,
        "profile_photo_url":    current_user.profile_photo_url,
        "cnic_number_masked":   current_user.cnic_number_masked,
        "verification_tier":    current_user.verification_tier,
        "is_verified":          current_user.is_verified,
        "is_active":            current_user.is_active,
        "biometric_enabled":    current_user.biometric_enabled,
        "cnic_verified":        current_user.cnic_verified,
        "biometric_verified":   current_user.biometric_verified,
        "fingerprint_verified": current_user.fingerprint_verified,
        "nadra_verified":       current_user.nadra_verified,
        "wallet_balance":       float(wallet.balance) if wallet else 0.0,
        "member_since":         current_user.member_since.isoformat() if current_user.member_since else None,
        "last_login_at":        current_user.last_login_at.isoformat() if current_user.last_login_at else None,
    }


# ══════════════════════════════════════════════════════════════════════════════
# PATCH /users/me
# ══════════════════════════════════════════════════════════════════════════════
class ProfileUpdateRequest(BaseModel):
    full_name:     Optional[str]      = Field(None, min_length=2, max_length=255)
    email:         Optional[EmailStr] = None
    date_of_birth: Optional[str]      = None   # YYYY-MM-DD


@router.patch("/me")
async def update_profile(
    body: ProfileUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if body.full_name:
        current_user.full_name = body.full_name
    if body.email:
        existing = (await db.execute(
            select(User).where(User.email == body.email, User.id != current_user.id)
        )).scalar_one_or_none()
        if existing:
            raise HTTPException(409, "Email already in use by another account.")
        current_user.email = body.email
    if body.date_of_birth:
        from datetime import date
        try:
            current_user.date_of_birth = date.fromisoformat(body.date_of_birth)
        except ValueError:
            raise HTTPException(400, "date_of_birth must be YYYY-MM-DD format.")
    await db.commit()
    return {"message": "Profile updated.", "full_name": current_user.full_name, "email": current_user.email}


# ══════════════════════════════════════════════════════════════════════════════
# POST /users/me/photo
# ══════════════════════════════════════════════════════════════════════════════
@router.post("/me/photo")
@limiter.limit("10/day")
async def upload_photo(
    request: Request,
    photo: UploadFile = File(..., description="Profile photo (JPEG/PNG, max 5MB)"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload profile photo to Cloudinary (public CDN). Returns URL."""
    data = await photo.read()
    if len(data) > MAX_PHOTO_BYTES:
        raise HTTPException(400, f"Photo exceeds {MAX_PHOTO_MB}MB limit.")

    try:
        import cloudinary
        import cloudinary.uploader
        cloudinary.config(
            cloud_name=settings.CLOUDINARY_CLOUD_NAME,
            api_key=settings.CLOUDINARY_API_KEY,
            api_secret=settings.CLOUDINARY_API_SECRET,
            secure=True,
        )
        import asyncio
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: cloudinary.uploader.upload(
                io.BytesIO(data),
                folder="profiles/",
                public_id=str(current_user.id),
                overwrite=True,
                resource_type="image",
                transformation=[{"width": 400, "height": 400, "crop": "fill", "gravity": "face"}],
            )
        )
        url = result.get("secure_url", "")
    except Exception as e:
        raise HTTPException(503, f"Photo upload failed: {str(e)}")

    current_user.profile_photo_url = url
    await db.commit()
    return {"message": "Photo uploaded.", "profile_photo_url": url}


# ══════════════════════════════════════════════════════════════════════════════
# GET /users/me/qr
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/me/qr")
async def get_qr_code(current_user: User = Depends(get_current_user)):
    """Generate QR PNG (base64) encoding the user's phone number."""
    try:
        import qrcode
        import json as _json
        payload = _json.dumps({"phone": current_user.phone_number, "name": current_user.full_name})
        qr = qrcode.QRCode(version=1, box_size=10, border=4)
        qr.add_data(payload)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()
    except Exception as e:
        raise HTTPException(500, f"QR generation failed: {str(e)}")

    return {
        "qr_base64":  b64,
        "qr_payload": {"phone": current_user.phone_number, "name": current_user.full_name},
        "format":     "image/png",
    }


# ══════════════════════════════════════════════════════════════════════════════
# POST /users/me/biometric
# ══════════════════════════════════════════════════════════════════════════════
class BiometricToggleRequest(BaseModel):
    enable: bool
    otp:    str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")


@router.post("/me/biometric")
async def toggle_biometric(
    body: BiometricToggleRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Enable or disable biometric login. Requires security_change OTP."""
    ok = await _check_otp(db, current_user.phone_number, body.otp, "security_change")
    if not ok:
        raise HTTPException(400, "Invalid or expired OTP.")
    current_user.biometric_enabled = body.enable
    await db.commit()
    state = "enabled" if body.enable else "disabled"
    return {"message": f"Biometric login {state}.", "biometric_enabled": body.enable}


@router.post("/me/biometric/otp")
@limiter.limit("5/hour")
async def request_biometric_otp(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Request OTP for biometric toggle (purpose=security_change)."""
    await _send_otp(db, current_user.phone_number, "security_change")
    return {"message": "OTP sent for biometric toggle."}


# ══════════════════════════════════════════════════════════════════════════════
# GET /users/verification-status
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/verification-status")
async def verification_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from decimal import Decimal
    wallet = (await db.execute(select(Wallet).where(Wallet.user_id == current_user.id))).scalar_one_or_none()
    tier   = current_user.verification_tier or 0
    limit  = TIER_LIMITS.get(tier, Decimal("0"))
    spent  = wallet.daily_spent if wallet else Decimal("0")
    return {
        "tier":                   tier,
        "daily_limit":            float(limit),
        "daily_remaining":        float(max(limit - (spent or Decimal("0")), Decimal("0"))),
        "cnic_verified":          current_user.cnic_verified,
        "cnic_masked":            current_user.cnic_number_masked,
        "biometric_verified":     current_user.biometric_verified,
        "fingerprint_verified":   current_user.fingerprint_verified,
        "nadra_verified":         current_user.nadra_verified,
        "biometric_enabled":      current_user.biometric_enabled,
        "next_step": (
            "Upload CNIC to reach Tier 2"         if tier < 2 else
            "Complete liveness check for Tier 3"  if tier < 3 else
            "Register fingerprint for Tier 4"     if tier < 4 else
            "Fully verified"
        ),
    }


# ══════════════════════════════════════════════════════════════════════════════
# GET /users/daily-limit-status
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/daily-limit-status")
async def daily_limit_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from decimal import Decimal
    wallet = (await db.execute(select(Wallet).where(Wallet.user_id == current_user.id))).scalar_one_or_none()
    tier   = current_user.verification_tier or 0
    limit  = TIER_LIMITS.get(tier, Decimal("0"))
    spent  = (wallet.daily_spent if wallet else Decimal("0")) or Decimal("0")
    # reset_at = next midnight UTC
    now       = _utcnow()
    reset_at  = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return {
        "spent_today":   float(spent),
        "tier_limit":    float(limit),
        "remaining":     float(max(limit - spent, Decimal("0"))),
        "reset_at":      reset_at.isoformat(),
        "tier":          tier,
    }


# ══════════════════════════════════════════════════════════════════════════════
# GET /users/search?q=
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/search")
@limiter.limit("30/minute")
async def search_users(
    request: Request,
    q: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if len(q.strip()) < 3:
        raise HTTPException(400, "Search query must be at least 3 characters.")

    results = (await db.execute(
        select(User).where(
            or_(
                User.full_name.ilike(f"%{q}%"),
                User.phone_number.ilike(f"%{q}%"),
            ),
            User.id != current_user.id,
            User.is_active == True,
        ).limit(20)
    )).scalars().all()

    def _mask_phone(p: str) -> str:
        return p[:3] + "****" + p[-4:] if len(p) >= 7 else p

    return {
        "results": [
            {
                "id":                str(u.id),
                "full_name":         u.full_name,
                "phone_masked":      _mask_phone(u.phone_number),
                "profile_photo_url": u.profile_photo_url,
                "verification_tier": u.verification_tier,
            }
            for u in results
        ],
        "count": len(results),
    }


# ══════════════════════════════════════════════════════════════════════════════
# PUT /users/fcm-token  AND  /users/me/fcm-token  (Android app uses /me/)
# ══════════════════════════════════════════════════════════════════════════════
class FcmTokenRequest(BaseModel):
    fcm_token: str = Field(..., min_length=10)


async def _do_update_fcm_token(
    body: FcmTokenRequest,
    current_user: User,
    db: AsyncSession,
):
    current_user.fcm_token            = body.fcm_token
    current_user.fcm_token_updated_at = datetime.now(timezone.utc)
    await db.commit()
    return {"message": "FCM token updated. Push notifications active."}


@router.put("/fcm-token")
async def update_fcm_token(
    body: FcmTokenRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await _do_update_fcm_token(body, current_user, db)


@router.put("/me/fcm-token")
async def update_fcm_token_me(
    body: FcmTokenRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await _do_update_fcm_token(body, current_user, db)


# ══════════════════════════════════════════════════════════════════════════════
# GET /users/devices
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/devices")
async def list_devices(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    devices = (await db.execute(
        select(DeviceRegistry).where(DeviceRegistry.user_id == current_user.id)
        .order_by(DeviceRegistry.last_seen_at.desc())
    )).scalars().all()

    return {
        "devices": [
            {
                "id":                 d.id,
                "device_name":        d.device_name,
                "device_os":          d.device_os,
                "is_trusted":         d.is_trusted,
                "trusted_at":         d.trusted_at.isoformat()    if d.trusted_at    else None,
                "first_seen_at":      d.first_seen_at.isoformat() if d.first_seen_at else None,
                "last_seen_at":       d.last_seen_at.isoformat()  if d.last_seen_at  else None,
            }
            for d in devices
        ],
        "count": len(devices),
    }


# ══════════════════════════════════════════════════════════════════════════════
# DELETE /users/devices/{id}
# ══════════════════════════════════════════════════════════════════════════════
@router.delete("/devices/{device_id}")
async def remove_device(
    device_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove a trusted device. Forces OTP challenge on next login from that device."""
    device = (await db.execute(
        select(DeviceRegistry).where(
            DeviceRegistry.id      == device_id,
            DeviceRegistry.user_id == current_user.id,
        )
    )).scalar_one_or_none()

    if not device:
        raise HTTPException(404, "Device not found.")

    await db.delete(device)
    await db.commit()
    return {"message": "Device removed. OTP will be required on next login from this device.", "device_id": device_id}


# ══════════════════════════════════════════════════════════════════════════════
# POST /users/me/deactivate
# ══════════════════════════════════════════════════════════════════════════════
class DeactivateRequest(BaseModel):
    password: str
    otp:      str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")


@router.post("/me/deactivate/otp")
@limiter.limit("3/hour")
async def request_deactivate_otp(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _send_otp(db, current_user.phone_number, "security_change")
    return {"message": "OTP sent for account deactivation."}


@router.post("/me/deactivate")
async def deactivate_account(
    body: DeactivateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Soft deactivate account. Requires password + OTP."""
    if not verify_password(body.password, current_user.password_hash):
        raise HTTPException(401, "Incorrect password.")

    ok = await _check_otp(db, current_user.phone_number, body.otp, "security_change")
    if not ok:
        raise HTTPException(400, "Invalid or expired OTP.")

    current_user.is_active = False
    # Revoke all refresh tokens
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == current_user.id, RefreshToken.is_revoked == False)
        .values(is_revoked=True, revoked_at=_utcnow())
    )
    await db.commit()
    return {"message": "Account deactivated. Contact support to reactivate."}
