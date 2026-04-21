"""Banking router — link/unlink bank accounts (Fernet encrypted). PROMPT 16."""
from datetime import datetime, timezone, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from limiter import limiter
from models.bank import BankAccount
from models.other import OtpCode
from models.user import User
from services.auth_service import (
    get_current_user, normalize_phone,
    generate_otp, hash_otp, verify_otp, send_otp_sms,
)
from services.encryption_service import encrypt, mask_account
from services.notification_service import send_notification

router = APIRouter()


def _utcnow():
    return datetime.now(timezone.utc)


# ── OTP helpers (same pattern as user.py) ─────────────────────────────────────
async def _send_bank_link_otp(db: AsyncSession, phone: str) -> None:
    await db.execute(
        update(OtpCode)
        .where(OtpCode.phone_number == phone, OtpCode.purpose == "bank_link", OtpCode.is_used == False)
        .values(is_used=True)
    )
    otp = generate_otp()
    db.add(OtpCode(
        phone_number = phone,
        code_hash    = hash_otp(otp),
        purpose      = "bank_link",
        expires_at   = _utcnow() + timedelta(minutes=5),
    ))
    await db.commit()
    await send_otp_sms(phone, otp)


async def _verify_bank_link_otp(db: AsyncSession, phone: str, otp: str) -> bool:
    result = await db.execute(
        select(OtpCode).where(
            OtpCode.phone_number == phone,
            OtpCode.purpose      == "bank_link",
            OtpCode.is_used      == False,
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
# POST /banking/otp/request
# ══════════════════════════════════════════════════════════════════════════════
@router.post("/otp/request")
@limiter.limit("5/hour")
async def request_bank_link_otp(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Send a bank_link OTP to the user's registered phone before linking an account."""
    await _send_bank_link_otp(db, current_user.phone_number)
    return {"message": "OTP sent. Enter it when linking your bank account."}


# ══════════════════════════════════════════════════════════════════════════════
# GET /banking/accounts
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/accounts")
async def list_bank_accounts(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all linked bank accounts. Account numbers always returned as ****1234."""
    accounts = (await db.execute(
        select(BankAccount)
        .where(BankAccount.user_id == current_user.id)
        .order_by(BankAccount.created_at)
    )).scalars().all()

    return {
        "accounts": [
            {
                "id":                   a.id,
                "bank_name":            a.bank_name,
                "account_number_masked": a.account_number_masked,
                "account_title":        a.account_title,
                "is_primary":           a.is_primary,
                "is_verified":          a.is_verified,
                "created_at":           a.created_at.isoformat() if a.created_at else None,
            }
            for a in accounts
        ],
        "count": len(accounts),
    }


# ══════════════════════════════════════════════════════════════════════════════
# POST /banking/accounts
# ══════════════════════════════════════════════════════════════════════════════
class LinkAccountRequest(BaseModel):
    bank_name:      str = Field(..., min_length=2, max_length=100)
    account_number: str = Field(..., min_length=8, max_length=30, description="Plain account number — will be encrypted")
    account_title:  str = Field(..., min_length=2, max_length=255)
    otp:            str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")
    set_as_primary: bool = False


@router.post("/accounts", status_code=201)
@limiter.limit("5/hour")
async def link_bank_account(
    request: Request,
    body: LinkAccountRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Link a bank account.
    1. Verify bank_link OTP.
    2. Fernet AES-256 encrypt account_number before storing.
    3. Store masked version (****1234) for display.
    """
    # 1. Verify OTP
    ok = await _verify_bank_link_otp(db, current_user.phone_number, body.otp)
    if not ok:
        raise HTTPException(400, "Invalid or expired OTP. Request a new one via /banking/otp/request.")

    # 2. Check for duplicate (same masked number at same bank)
    masked = mask_account(body.account_number)
    existing = (await db.execute(
        select(BankAccount).where(
            BankAccount.user_id              == current_user.id,
            BankAccount.bank_name            == body.bank_name,
            BankAccount.account_number_masked == masked,
        )
    )).scalar_one_or_none()
    if existing:
        raise HTTPException(409, "This account is already linked.")

    # 3. Encrypt account number
    encrypted = encrypt(body.account_number)

    # 4. If set_as_primary, demote all existing primaries
    if body.set_as_primary:
        await db.execute(
            update(BankAccount)
            .where(BankAccount.user_id == current_user.id)
            .values(is_primary=False)
        )

    account = BankAccount(
        user_id                  = current_user.id,
        bank_name                = body.bank_name,
        account_number_encrypted = encrypted,
        account_number_masked    = masked,
        account_title            = body.account_title,
        is_primary               = body.set_as_primary,
        is_verified              = True,    # OTP verified = account confirmed by user
    )
    db.add(account)
    await db.commit()
    await db.refresh(account)

    import asyncio
    asyncio.create_task(send_notification(
        db, current_user.id,
        title="🏦 Bank Account Linked",
        body=f"{body.bank_name} account {masked} linked successfully.",
        type="security",
    ))
    return {
        "message":               "Bank account linked successfully.",
        "id":                    account.id,
        "bank_name":             account.bank_name,
        "account_number_masked": account.account_number_masked,
        "account_title":         account.account_title,
        "is_primary":            account.is_primary,
    }


# ══════════════════════════════════════════════════════════════════════════════
# DELETE /banking/accounts/{id}
# ══════════════════════════════════════════════════════════════════════════════
@router.delete("/accounts/{account_id}")
async def unlink_bank_account(
    account_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Unlink a bank account."""
    account = (await db.execute(
        select(BankAccount).where(
            BankAccount.id      == account_id,
            BankAccount.user_id == current_user.id,
        )
    )).scalar_one_or_none()

    if not account:
        raise HTTPException(404, "Bank account not found.")

    await db.delete(account)
    await db.commit()
    return {
        "message":    "Bank account unlinked.",
        "account_id": account_id,
    }
