"""Auth router — register, OTP (Infobip), login, PIN, tokens."""
from datetime import datetime, timezone, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from config   import settings
from database import get_db
from limiter  import limiter
from models.user   import User, DeviceRegistry, RefreshToken, LoginAudit
from models.wallet import Wallet
from models.other  import OtpCode, PendingRegistration
from schemas.auth  import (
    RegisterRequest, RegisterResponse, PreRegisterResponse,
    OtpVerifyRequest, OtpResendRequest,
    LoginRequest, LoginResponse, TokenPair,
    NewDeviceVerifyRequest, NewDeviceFirebaseRequest,
    PinLoginRequest, BiometricLoginRequest,
    RefreshRequest, PasswordResetInitiate, PasswordResetComplete,
    PinSetRequest, PinVerifyRequest, MessageResponse,
    PinResetInitiate, PinResetComplete,
)
from services.encryption_service import encrypt, mask_cnic
from services.auth_service import (
    DEV_OTP_STORE, TIER_LIMITS,
    normalize_phone, extract_age_from_cnic, mask_phone,
    hash_password, verify_password,
    hash_pin, verify_pin,
    hash_otp, verify_otp, generate_otp,
    send_otp_sms,
    verify_firebase_phone_token,
    create_access_token, create_refresh_token, create_session_token,
    decode_token, hash_refresh_token,
    get_current_user,
)

router = APIRouter()


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════
def _utcnow():
    return datetime.now(timezone.utc)


async def _generate_and_send_otp(db: AsyncSession, phone: str, purpose: str) -> str:
    """Invalidate old OTPs for same phone+purpose, create new one, send SMS."""
    # Invalidate existing unused codes
    await db.execute(
        update(OtpCode)
        .where(
            OtpCode.phone_number == phone,
            OtpCode.purpose == purpose,
            OtpCode.is_used == False,
        )
        .values(is_used=True)
    )

    otp = generate_otp()
    otp_row = OtpCode(
        phone_number = phone,
        code_hash    = hash_otp(otp),
        purpose      = purpose,
        expires_at   = _utcnow() + timedelta(minutes=5),
    )
    db.add(otp_row)
    await db.commit()

    sent = await send_otp_sms(phone, otp)
    if not sent and not settings.DEV_MODE:
        raise HTTPException(status_code=502, detail="Failed to send OTP SMS. Try again.")
    return otp


async def _verify_and_consume_otp(db: AsyncSession, phone: str, otp: str, purpose: str) -> bool:
    """Return True if OTP verifies and is consumed. Increments attempts on failure."""
    result = await db.execute(
        select(OtpCode)
        .where(
            OtpCode.phone_number == phone,
            OtpCode.purpose == purpose,
            OtpCode.is_used == False,
        )
        .order_by(OtpCode.created_at.desc())
    )
    row = result.scalars().first()
    if not row:
        return False
    if row.expires_at < _utcnow():
        return False
    if row.attempts >= 3:
        row.is_used = True
        await db.commit()
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


async def _issue_tokens(db: AsyncSession, user: User, device_fingerprint: str | None) -> TokenPair:
    access = create_access_token(user.id, is_superuser=user.is_superuser)
    raw, token_hash, exp = create_refresh_token(user.id)
    db.add(RefreshToken(
        user_id            = user.id,
        token_hash         = token_hash,
        device_fingerprint = device_fingerprint,
        expires_at         = exp,
    ))
    user.last_login_at  = _utcnow()
    user.login_attempts = 0
    await db.commit()
    return TokenPair(
        access_token  = access,
        refresh_token = raw,
        expires_in    = settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


async def _log_login(db: AsyncSession, request: Request, user_id: UUID | None, phone: str,
                     device_fp: str | None, success: bool, reason: str | None = None):
    db.add(LoginAudit(
        user_id            = user_id,
        phone_number       = phone[:15] if phone else None,
        ip_address         = request.client.host if request.client else None,
        user_agent         = request.headers.get("user-agent", "")[:500],
        device_fingerprint = device_fp,
        success            = success,
        failure_reason     = reason,
    ))
    await db.commit()


# ══════════════════════════════════════════════════════════════════════════════
# REGISTER
# ══════════════════════════════════════════════════════════════════════════════
@router.post("/register", response_model=PreRegisterResponse, status_code=200)
@limiter.limit("5/hour")
async def register(
    request: Request,
    body: RegisterRequest,
    db: AsyncSession = Depends(get_db),
):
    """Step 1 of 2: Validate registration data, save to pending_registrations,
    and send a 6-digit OTP to the phone number.
    Step 2: POST /auth/otp/verify (purpose=registration) to complete account creation.
    In DEV_MODE, GET /auth/dev/otp?phone=... returns the auto-filled OTP.
    """
    try:
        phone = normalize_phone(body.phone)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Firebase bypass / validation (keeps compatibility with existing Android build)
    verified = await verify_firebase_phone_token(body.firebase_id_token, phone)
    if not verified:
        raise HTTPException(status_code=401,
            detail="Firebase phone verification failed.")

    # Duplicate checks against confirmed users
    if (await db.execute(select(User).where(User.phone_number == phone))).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Phone already registered")
    if body.email:
        if (await db.execute(select(User).where(User.email == body.email))).scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Email already registered")

    # CNIC validation
    try:
        dob, age = extract_age_from_cnic(body.cnic_number)
        cnic_masked = body.cnic_number[:6] + "XXXXXXX-X"
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Upsert pending_registration (replace if phone already exists from a previous attempt)
    existing_pending = (await db.execute(
        select(PendingRegistration).where(PendingRegistration.phone_number == phone)
    )).scalar_one_or_none()
    if existing_pending:
        await db.delete(existing_pending)
        await db.flush()

    db.add(PendingRegistration(
        phone_number       = phone,
        email              = body.email,
        full_name          = body.full_name,
        password_hash      = hash_password(body.password),
        country            = body.country,
        cnic_number        = body.cnic_number,
        cnic_masked        = cnic_masked,
        date_of_birth      = dob,
        age                = age,
        account_type       = body.account_type,
        device_fingerprint = body.device_fingerprint,
        device_name        = body.device_name,
        device_os          = body.device_os,
        expires_at         = _utcnow() + timedelta(minutes=30),
    ))
    await db.commit()

    await _generate_and_send_otp(db, phone, "registration")

    return PreRegisterResponse(phone_masked=mask_phone(phone))


# ══════════════════════════════════════════════════════════════════════════════
# OTP — verify + resend
# ══════════════════════════════════════════════════════════════════════════════
@router.post("/otp/verify")
async def otp_verify(request: Request, body: OtpVerifyRequest, db: AsyncSession = Depends(get_db)):
    try:
        phone = normalize_phone(body.phone)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    ok = await _verify_and_consume_otp(db, phone, body.otp, body.purpose)
    if not ok:
        raise HTTPException(status_code=400, detail="Invalid or expired OTP")

    DEV_OTP_STORE.pop(phone, None)

    # ── Registration: create the real user from pending data ──────────────────
    if body.purpose == "registration":
        pending = (await db.execute(
            select(PendingRegistration).where(PendingRegistration.phone_number == phone)
        )).scalar_one_or_none()
        if not pending:
            raise HTTPException(status_code=400,
                detail="Registration session expired. Please start registration again.")

        user = User(
            phone_number       = phone,
            email              = pending.email,
            full_name          = pending.full_name,
            country            = pending.country,
            date_of_birth      = pending.date_of_birth,
            age                = pending.age,
            password_hash      = pending.password_hash,
            cnic_encrypted     = encrypt(pending.cnic_number) if pending.cnic_number else None,
            cnic_number_masked = pending.cnic_masked,
            account_type       = pending.account_type,
            verification_tier  = 1,
            is_verified        = True,
        )
        db.add(user)
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            raise HTTPException(status_code=409, detail="Phone or email already registered")

        db.add(Wallet(user_id=user.id, daily_limit=TIER_LIMITS[1]))
        db.add(DeviceRegistry(
            user_id            = user.id,
            device_fingerprint = pending.device_fingerprint,
            device_name        = pending.device_name,
            device_os          = pending.device_os,
            is_trusted         = True,
            trusted_at         = _utcnow(),
        ))

        await db.delete(pending)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            raise HTTPException(status_code=409, detail="Phone or email already registered")
        await db.refresh(user)

        tokens = await _issue_tokens(db, user, pending.device_fingerprint)
        await _log_login(db, request, user.id, phone, pending.device_fingerprint, True, "register_otp")

        return RegisterResponse(
            user_id      = user.id,
            phone_masked = mask_phone(phone),
            tokens       = tokens,
        )

    return MessageResponse(message="OTP verified")


@router.post("/otp/resend", response_model=MessageResponse)
@limiter.limit("3/hour")
async def otp_resend(request: Request, body: OtpResendRequest, db: AsyncSession = Depends(get_db)):
    try:
        phone = normalize_phone(body.phone)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await _generate_and_send_otp(db, phone, body.purpose)
    return MessageResponse(message="OTP resent")


# ══════════════════════════════════════════════════════════════════════════════
# LOGIN — password + device check
# ══════════════════════════════════════════════════════════════════════════════
@router.post("/login", response_model=LoginResponse)
@limiter.limit("10/hour")
async def login(request: Request, body: LoginRequest, db: AsyncSession = Depends(get_db)):
    try:
        phone = normalize_phone(body.phone)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    user = (await db.execute(select(User).where(User.phone_number == phone))).scalar_one_or_none()
    if not user:
        await _log_login(db, request, None, phone, body.device_fingerprint, False, "user_not_found")
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not user.is_active:
        await _log_login(db, request, user.id, phone, body.device_fingerprint, False, "inactive")
        raise HTTPException(status_code=403, detail="Account deactivated")
    if user.is_locked:
        await _log_login(db, request, user.id, phone, body.device_fingerprint, False, "locked")
        raise HTTPException(status_code=423, detail="Account locked")
    if not verify_password(body.password, user.password_hash):
        await _log_login(db, request, user.id, phone, body.device_fingerprint, False, "bad_password")
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not user.is_verified:
        await _log_login(db, request, user.id, phone, body.device_fingerprint, False, "not_verified")
        raise HTTPException(status_code=403, detail="Phone not verified. Complete OTP verification first.")

    # ── Superuser bypass — admin panel skips device trust / OTP flow ──────────
    if user.is_superuser:
        tokens = await _issue_tokens(db, user, body.device_fingerprint)
        await _log_login(db, request, user.id, phone, body.device_fingerprint, True, "superuser_direct")
        return LoginResponse(
            status="authenticated",
            tokens=tokens,
            message="Admin login successful.",
            is_superuser=True,
        )

    # Device check
    dev_res = await db.execute(
        select(DeviceRegistry).where(
            DeviceRegistry.user_id == user.id,
            DeviceRegistry.device_fingerprint == body.device_fingerprint,
        )
    )
    device = dev_res.scalar_one_or_none()

    if device and device.is_trusted:
        device.last_seen_at = _utcnow()
        tokens = await _issue_tokens(db, user, body.device_fingerprint)
        await _log_login(db, request, user.id, phone, body.device_fingerprint, True)
        return LoginResponse(status="authenticated", tokens=tokens,
                             message="Login successful (trusted device)")

    # New device — issue OTP + session token
    await _generate_and_send_otp(db, phone, "new_device")
    session_tok = create_session_token(user.id, body.device_fingerprint, "new_device")
    # Pre-create device record (not trusted yet)
    if not device:
        db.add(DeviceRegistry(
            user_id            = user.id,
            device_fingerprint = body.device_fingerprint,
            device_name        = body.device_name,
            device_os          = body.device_os,
            is_trusted         = False,
        ))
        await db.commit()
    return LoginResponse(
        status="otp_required", session_token=session_tok,
        message="New device detected. Verify OTP sent to registered phone.",
    )


@router.post("/login/new-device/verify", response_model=LoginResponse)
async def login_new_device_verify(
    request: Request, body: NewDeviceVerifyRequest, db: AsyncSession = Depends(get_db)
):
    payload = decode_token(body.session_token)
    if payload.get("type") != "session" or payload.get("prp") != "new_device":
        raise HTTPException(status_code=400, detail="Invalid session token")

    user_id = UUID(payload["sub"])
    dfp     = payload["dfp"]

    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    ok = await _verify_and_consume_otp(db, user.phone_number, body.otp, "new_device")
    if not ok:
        await _log_login(db, request, user.id, user.phone_number, dfp, False, "bad_otp")
        raise HTTPException(status_code=400, detail="Invalid or expired OTP")

    # Trust the device
    dev_res = await db.execute(
        select(DeviceRegistry).where(
            DeviceRegistry.user_id == user.id,
            DeviceRegistry.device_fingerprint == dfp,
        )
    )
    device = dev_res.scalar_one_or_none()
    if device:
        device.is_trusted = True
        device.trusted_at = _utcnow()
        device.last_seen_at = _utcnow()
    else:
        db.add(DeviceRegistry(
            user_id=user.id, device_fingerprint=dfp,
            is_trusted=True, trusted_at=_utcnow(),
        ))
    await db.commit()
    DEV_OTP_STORE.pop(user.phone_number, None)

    tokens = await _issue_tokens(db, user, dfp)
    await _log_login(db, request, user.id, user.phone_number, dfp, True, "new_device_verified")
    return LoginResponse(status="authenticated", tokens=tokens,
                         message="Device trusted. Login successful.")


@router.post("/login/new-device/firebase", response_model=LoginResponse)
async def login_new_device_firebase(
    request: Request, body: NewDeviceFirebaseRequest, db: AsyncSession = Depends(get_db),
):
    """Alternative to /login/new-device/verify that uses Firebase Phone Auth
    instead of a backend-sent OTP. The Android app completes Firebase Phone Auth
    for the user's registered phone, then sends the ID token here."""
    payload = decode_token(body.session_token)
    if payload.get("type") != "session" or payload.get("prp") != "new_device":
        raise HTTPException(status_code=400, detail="Invalid session token")

    user_id = UUID(payload["sub"])
    dfp     = payload["dfp"]

    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    verified = await verify_firebase_phone_token(body.firebase_id_token, user.phone_number)
    if not verified:
        raise HTTPException(status_code=401, detail="Firebase verification failed")

    # Trust the device
    dev_res = await db.execute(
        select(DeviceRegistry).where(
            DeviceRegistry.user_id == user.id,
            DeviceRegistry.device_fingerprint == dfp,
        )
    )
    device = dev_res.scalar_one_or_none()
    if device:
        device.is_trusted   = True
        device.trusted_at   = _utcnow()
        device.last_seen_at = _utcnow()
    else:
        db.add(DeviceRegistry(
            user_id=user.id, device_fingerprint=dfp,
            is_trusted=True, trusted_at=_utcnow(),
        ))
    await db.commit()

    tokens = await _issue_tokens(db, user, dfp)
    await _log_login(db, request, user.id, user.phone_number, dfp, True, "new_device_firebase")
    return LoginResponse(status="authenticated", tokens=tokens,
                         message="Device trusted via Firebase. Login successful.")


@router.post("/login/pin", response_model=TokenPair)
@limiter.limit("10/hour")
async def login_pin(request: Request, body: PinLoginRequest, db: AsyncSession = Depends(get_db)):
    try:
        phone = normalize_phone(body.phone)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    user = (await db.execute(select(User).where(User.phone_number == phone))).scalar_one_or_none()
    if not user or not user.pin_hash:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if user.is_locked:
        raise HTTPException(status_code=423, detail="Account locked")

    # Device must be trusted
    dev = (await db.execute(
        select(DeviceRegistry).where(
            DeviceRegistry.user_id == user.id,
            DeviceRegistry.device_fingerprint == body.device_fingerprint,
            DeviceRegistry.is_trusted == True,
        )
    )).scalar_one_or_none()
    if not dev:
        raise HTTPException(status_code=403, detail="Untrusted device — use password login")

    if not verify_pin(body.pin, user.pin_hash):
        user.login_attempts = (user.login_attempts or 0) + 1
        if user.login_attempts >= 3:
            user.is_locked = True
        await db.commit()
        await _log_login(db, request, user.id, phone, body.device_fingerprint, False, "bad_pin")
        raise HTTPException(status_code=401, detail="Invalid PIN")

    dev.last_seen_at = _utcnow()
    tokens = await _issue_tokens(db, user, body.device_fingerprint)
    await _log_login(db, request, user.id, phone, body.device_fingerprint, True, "pin")
    return tokens


@router.post("/login/biometric", response_model=TokenPair)
async def login_biometric(request: Request, body: BiometricLoginRequest, db: AsyncSession = Depends(get_db)):
    """Biometric token is a short-lived JWT issued by the device after local biometric auth."""
    payload = decode_token(body.biometric_token)
    if payload.get("type") != "biometric" or payload.get("dfp") != body.device_fingerprint:
        raise HTTPException(status_code=400, detail="Invalid biometric token")

    try:
        phone = normalize_phone(body.phone)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    user = (await db.execute(select(User).where(User.phone_number == phone))).scalar_one_or_none()
    if not user or str(user.id) != payload.get("sub"):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not user.biometric_enabled:
        raise HTTPException(status_code=403, detail="Biometric not enabled for user")
    if user.is_locked or not user.is_active:
        raise HTTPException(status_code=403, detail="Account unavailable")

    dev = (await db.execute(
        select(DeviceRegistry).where(
            DeviceRegistry.user_id == user.id,
            DeviceRegistry.device_fingerprint == body.device_fingerprint,
            DeviceRegistry.is_trusted == True,
        )
    )).scalar_one_or_none()
    if not dev:
        raise HTTPException(status_code=403, detail="Untrusted device")

    dev.last_seen_at = _utcnow()
    tokens = await _issue_tokens(db, user, body.device_fingerprint)
    await _log_login(db, request, user.id, phone, body.device_fingerprint, True, "biometric")
    return tokens


# ══════════════════════════════════════════════════════════════════════════════
# TOKEN — refresh + logout
# ══════════════════════════════════════════════════════════════════════════════
@router.post("/token/refresh", response_model=TokenPair)
async def token_refresh(body: RefreshRequest, db: AsyncSession = Depends(get_db)):
    h = hash_refresh_token(body.refresh_token)
    row = (await db.execute(select(RefreshToken).where(RefreshToken.token_hash == h))).scalar_one_or_none()
    if not row or row.is_revoked:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    if row.expires_at < _utcnow():
        raise HTTPException(status_code=401, detail="Refresh token expired")

    user = (await db.execute(select(User).where(User.id == row.user_id))).scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")

    # Rotate
    row.is_revoked = True
    row.revoked_at = _utcnow()
    tokens = await _issue_tokens(db, user, row.device_fingerprint)
    return tokens


@router.post("/logout", response_model=MessageResponse)
async def logout(body: RefreshRequest, db: AsyncSession = Depends(get_db),
                 user: User = Depends(get_current_user)):
    h = hash_refresh_token(body.refresh_token)
    row = (await db.execute(
        select(RefreshToken).where(
            RefreshToken.token_hash == h,
            RefreshToken.user_id == user.id,
        )
    )).scalar_one_or_none()
    if row and not row.is_revoked:
        row.is_revoked = True
        row.revoked_at = _utcnow()
        await db.commit()
    return MessageResponse(message="Logged out")


@router.post("/logout-all", response_model=MessageResponse)
async def logout_all(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.is_revoked == False)
        .values(is_revoked=True, revoked_at=_utcnow())
    )
    await db.commit()
    return MessageResponse(message="All devices logged out")


# ══════════════════════════════════════════════════════════════════════════════
# PASSWORD RESET
# ══════════════════════════════════════════════════════════════════════════════
@router.post("/password/reset/initiate", response_model=MessageResponse)
@limiter.limit("5/hour")
async def password_reset_initiate(
    request: Request, body: PasswordResetInitiate, db: AsyncSession = Depends(get_db),
):
    # No enumeration — always return 200
    try:
        phone = normalize_phone(body.phone)
        user = (await db.execute(select(User).where(User.phone_number == phone))).scalar_one_or_none()
        if user:
            await _generate_and_send_otp(db, phone, "password_reset")
    except Exception:
        pass
    return MessageResponse(message="If the phone is registered, an OTP has been sent.")


@router.post("/password/reset/complete", response_model=MessageResponse)
async def password_reset_complete(body: PasswordResetComplete, db: AsyncSession = Depends(get_db)):
    try:
        phone = normalize_phone(body.phone)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    ok = await _verify_and_consume_otp(db, phone, body.otp, "password_reset")
    if not ok:
        raise HTTPException(status_code=400, detail="Invalid or expired OTP")

    user = (await db.execute(select(User).where(User.phone_number == phone))).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.password_hash  = hash_password(body.new_password)
    user.is_locked      = False
    user.login_attempts = 0
    # Revoke all refresh tokens — force re-login on all devices
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.is_revoked == False)
        .values(is_revoked=True, revoked_at=_utcnow())
    )
    await db.commit()
    DEV_OTP_STORE.pop(phone, None)
    return MessageResponse(message="Password updated. Please log in again.")


# ══════════════════════════════════════════════════════════════════════════════
# PIN — set + verify
# ══════════════════════════════════════════════════════════════════════════════
@router.post("/pin/set", response_model=MessageResponse)
async def pin_set(body: PinSetRequest, db: AsyncSession = Depends(get_db),
                  user: User = Depends(get_current_user)):
    user.pin_hash = hash_pin(body.pin)
    await db.commit()
    return MessageResponse(message="PIN set successfully")


@router.post("/pin/verify", response_model=MessageResponse)
async def pin_verify(body: PinVerifyRequest, db: AsyncSession = Depends(get_db),
                     user: User = Depends(get_current_user)):
    if not user.pin_hash:
        raise HTTPException(status_code=400, detail="PIN not set")
    if user.is_locked:
        raise HTTPException(status_code=423, detail="Account locked")

    if not verify_pin(body.pin, user.pin_hash):
        user.login_attempts = (user.login_attempts or 0) + 1
        if user.login_attempts >= 3:
            user.is_locked = True
        await db.commit()
        raise HTTPException(status_code=401, detail="Invalid PIN")

    user.login_attempts = 0
    await db.commit()
    return MessageResponse(message="PIN verified")


# ══════════════════════════════════════════════════════════════════════════════
# PIN RESET — forgot MPIN
# ══════════════════════════════════════════════════════════════════════════════
@router.post("/pin/reset/initiate", response_model=MessageResponse)
@limiter.limit("5/hour")
async def pin_reset_initiate(
    request: Request, body: PinResetInitiate, db: AsyncSession = Depends(get_db),
):
    try:
        phone = normalize_phone(body.phone)
        user = (await db.execute(select(User).where(User.phone_number == phone))).scalar_one_or_none()
        if user and not user.is_locked:
            await _generate_and_send_otp(db, phone, "security_change")
    except Exception:
        pass
    return MessageResponse(message="If the phone is registered, an OTP has been sent.")


@router.post("/pin/reset/complete", response_model=MessageResponse)
async def pin_reset_complete(body: PinResetComplete, db: AsyncSession = Depends(get_db)):
    try:
        phone = normalize_phone(body.phone)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    ok = await _verify_and_consume_otp(db, phone, body.otp, "security_change")
    if not ok:
        raise HTTPException(status_code=400, detail="Invalid or expired OTP")

    user = (await db.execute(select(User).where(User.phone_number == phone))).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.pin_hash       = hash_pin(body.new_pin)
    user.login_attempts = 0
    user.is_locked      = False
    await db.commit()
    DEV_OTP_STORE.pop(phone, None)
    return MessageResponse(message="MPIN reset successfully. Please log in again.")


# ══════════════════════════════════════════════════════════════════════════════
# PATCH /auth/fcm-token   (Android calls this on login + token refresh)
# ══════════════════════════════════════════════════════════════════════════════
from pydantic import BaseModel as _BaseModel

class _FcmTokenRequest(_BaseModel):
    fcm_token: str

@router.patch("/fcm-token", response_model=MessageResponse)
async def update_fcm_token(
    body: _FcmTokenRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Android app calls this after login or whenever FirebaseMessaging gives a new token."""
    current_user.fcm_token = body.fcm_token
    await db.commit()
    return MessageResponse(message="FCM token updated. Push notifications are active.")


# ══════════════════════════════════════════════════════════════════════════════
# GET /auth/me  — validate token + return live account state from the database
# Android calls this on every app startup to verify the token is still valid
# and to sync local state (pin_set, biometric_enabled, tier) from the backend.
# Returns 401 if token expired/revoked → app clears local storage → login screen.
# ══════════════════════════════════════════════════════════════════════════════
from pydantic import BaseModel as _BM2

class MeResponse(_BM2):
    user_id:           str
    full_name:         str
    phone_masked:      str
    phone:             str
    pin_set:           bool
    biometric_enabled: bool
    verification_tier: int
    is_verified:       bool
    is_locked:         bool
    account_type:      str

@router.get("/me", response_model=MeResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    return MeResponse(
        user_id           = str(current_user.id),
        full_name         = current_user.full_name,
        phone_masked      = mask_phone(current_user.phone_number),
        phone             = current_user.phone_number,
        pin_set           = bool(current_user.pin_hash),
        biometric_enabled = bool(current_user.biometric_enabled),
        verification_tier = current_user.verification_tier or 0,
        is_verified       = current_user.is_verified or False,
        is_locked         = current_user.is_locked or False,
        account_type      = current_user.account_type or "individual",
    )


# ══════════════════════════════════════════════════════════════════════════════
# GET /auth/dev/otp  — DEV_MODE only: returns the last OTP sent to a phone
# Android dev builds call this to auto-fill the OTP input field.
# This endpoint returns 404 in production (DEV_MODE=False).
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/dev/otp", include_in_schema=False)
async def dev_get_otp(phone: str):
    if not settings.DEV_MODE:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        phone_normalized = normalize_phone(phone)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid phone number format")
    otp = DEV_OTP_STORE.get(phone_normalized)
    if not otp:
        raise HTTPException(
            status_code=404,
            detail="No OTP found for this phone. Make sure you registered/logged in first.",
        )
    return {"otp": otp, "phone": phone_normalized}


