"""Zakat router — upgraded with wealth profiles, madhab support,
Hawl tracking, cache-based rates, and liability deductions. PROMPT 13 v2."""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Optional
from uuid import UUID

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from limiter import limiter
from models.other import ZakatCalculation
from models.user import User
from models.wallet import Wallet
from models.transaction import Transaction
from models.zakat import UserZakatSettings, WealthProfile, HawlTracking, MetalRateCache
from services.auth_service import get_current_user
from services.wallet_service import generate_reference
from services.notification_service import send_notification

router = APIRouter()

NISAB_GOLD_GRAMS   = Decimal("87.48")
NISAB_SILVER_GRAMS = Decimal("612.36")
ZAKAT_RATE         = Decimal("0.025")
HAWL_DAYS          = 354   # one lunar year


def _utcnow():
    return datetime.now(timezone.utc)


# ── Shared helpers ─────────────────────────────────────────────────────────────
async def _get_or_create_settings(db: AsyncSession, user_id: UUID) -> UserZakatSettings:
    row = (await db.execute(
        select(UserZakatSettings).where(UserZakatSettings.user_id == user_id)
    )).scalar_one_or_none()
    if not row:
        row = UserZakatSettings(user_id=user_id)
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return row


async def _get_or_create_wealth_profile(db: AsyncSession, user_id: UUID) -> WealthProfile:
    row = (await db.execute(
        select(WealthProfile).where(WealthProfile.user_id == user_id)
    )).scalar_one_or_none()
    if not row:
        row = WealthProfile(user_id=user_id)
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return row


async def _get_or_create_hawl(db: AsyncSession, user_id: UUID) -> HawlTracking:
    row = (await db.execute(
        select(HawlTracking).where(HawlTracking.user_id == user_id)
    )).scalar_one_or_none()
    if not row:
        row = HawlTracking(user_id=user_id)
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return row


async def _latest_rate(db: AsyncSession) -> Optional[MetalRateCache]:
    return (await db.execute(
        select(MetalRateCache).order_by(MetalRateCache.fetched_at.desc()).limit(1)
    )).scalar_one_or_none()


def _resolve_nisab(pref: str, nisab_gold: Decimal, nisab_silver: Decimal) -> Decimal:
    if pref == "gold":
        return nisab_gold
    if pref == "silver":
        return nisab_silver
    return min(nisab_gold, nisab_silver)


# ══════════════════════════════════════════════════════════════════════════════
# GET /zakat/settings
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/settings")
async def get_zakat_settings(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return current user's madhab and nisab_preference. Creates defaults if missing."""
    row = await _get_or_create_settings(db, current_user.id)
    return {
        "madhab":           row.madhab,
        "nisab_preference": row.nisab_preference,
        "updated_at":       row.updated_at.isoformat() if row.updated_at else None,
    }


# ══════════════════════════════════════════════════════════════════════════════
# PUT /zakat/settings
# ══════════════════════════════════════════════════════════════════════════════
_VALID_MADHABS = {"hanafi", "shafi", "maliki", "hanbali"}
_VALID_NISAB   = {"gold", "silver", "lower_of_two"}


class ZakatSettingsUpdate(BaseModel):
    madhab:           Optional[str] = Field(default=None)
    nisab_preference: Optional[str] = Field(default=None)


@router.put("/settings")
async def update_zakat_settings(
    body: ZakatSettingsUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if body.madhab and body.madhab not in _VALID_MADHABS:
        raise HTTPException(400, f"madhab must be one of: {', '.join(_VALID_MADHABS)}")
    if body.nisab_preference and body.nisab_preference not in _VALID_NISAB:
        raise HTTPException(400, f"nisab_preference must be one of: {', '.join(_VALID_NISAB)}")

    row = await _get_or_create_settings(db, current_user.id)
    if body.madhab:
        row.madhab = body.madhab
    if body.nisab_preference:
        row.nisab_preference = body.nisab_preference
    await db.commit()
    await db.refresh(row)
    return {
        "madhab":           row.madhab,
        "nisab_preference": row.nisab_preference,
        "updated_at":       row.updated_at.isoformat() if row.updated_at else None,
        "message":          "Zakat settings updated.",
    }


# ══════════════════════════════════════════════════════════════════════════════
# GET /zakat/wealth-profile
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/wealth-profile")
async def get_wealth_profile(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return saved wealth profile. Creates empty record if missing.
    Returns profile_freshness_days showing days since last_verified_at."""
    wp = await _get_or_create_wealth_profile(db, current_user.id)
    freshness_days = None
    if wp.last_verified_at:
        freshness_days = (_utcnow() - wp.last_verified_at).days

    return {
        "external_banks_pkr":        float(wp.external_banks_pkr or 0),
        "other_wallets_pkr":         float(wp.other_wallets_pkr or 0),
        "physical_gold_grams":       float(wp.physical_gold_grams or 0),
        "physical_silver_grams":     float(wp.physical_silver_grams or 0),
        "receivables_pkr":           float(wp.receivables_pkr or 0),
        "bad_debts_pkr":             float(wp.bad_debts_pkr or 0),
        "business_tradeable_pkr":    float(wp.business_tradeable_pkr or 0),
        "business_cash_pkr":         float(wp.business_cash_pkr or 0),
        "business_fixed_assets_pkr": float(wp.business_fixed_assets_pkr or 0),
        "personal_loans_pkr":        float(wp.personal_loans_pkr or 0),
        "credit_card_pkr":           float(wp.credit_card_pkr or 0),
        "car_loan_installments_pkr": float(wp.car_loan_installments_pkr or 0),
        "home_loan_pkr":             float(wp.home_loan_pkr or 0),
        "home_loan_include":         wp.home_loan_include,
        "other_liabilities_pkr":     float(wp.other_liabilities_pkr or 0),
        "last_verified_at":          wp.last_verified_at.isoformat() if wp.last_verified_at else None,
        "profile_freshness_days":    freshness_days,
        "updated_at":                wp.updated_at.isoformat() if wp.updated_at else None,
    }


# ══════════════════════════════════════════════════════════════════════════════
# PUT /zakat/wealth-profile
# ══════════════════════════════════════════════════════════════════════════════
class WealthProfileUpdate(BaseModel):
    external_banks_pkr:        Optional[Decimal] = Field(default=None, ge=0)
    other_wallets_pkr:         Optional[Decimal] = Field(default=None, ge=0)
    physical_gold_grams:       Optional[Decimal] = Field(default=None, ge=0)
    physical_silver_grams:     Optional[Decimal] = Field(default=None, ge=0)
    receivables_pkr:           Optional[Decimal] = Field(default=None, ge=0)
    bad_debts_pkr:             Optional[Decimal] = Field(default=None, ge=0)
    business_tradeable_pkr:    Optional[Decimal] = Field(default=None, ge=0)
    business_cash_pkr:         Optional[Decimal] = Field(default=None, ge=0)
    business_fixed_assets_pkr: Optional[Decimal] = Field(default=None, ge=0)
    personal_loans_pkr:        Optional[Decimal] = Field(default=None, ge=0)
    credit_card_pkr:           Optional[Decimal] = Field(default=None, ge=0)
    car_loan_installments_pkr: Optional[Decimal] = Field(default=None, ge=0)
    home_loan_pkr:             Optional[Decimal] = Field(default=None, ge=0)
    home_loan_include:         Optional[bool]    = None
    other_liabilities_pkr:    Optional[Decimal] = Field(default=None, ge=0)


@router.put("/wealth-profile")
async def update_wealth_profile(
    body: WealthProfileUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update wealth profile fields. Sets last_verified_at = now() on every save."""
    wp = await _get_or_create_wealth_profile(db, current_user.id)

    fields = body.model_dump(exclude_none=True)
    for field, value in fields.items():
        setattr(wp, field, value)

    wp.last_verified_at = _utcnow()
    await db.commit()
    await db.refresh(wp)

    freshness_days = (_utcnow() - wp.last_verified_at).days

    return {
        "message":               "Wealth profile updated and verified.",
        "last_verified_at":      wp.last_verified_at.isoformat(),
        "profile_freshness_days": freshness_days,
    }


# ══════════════════════════════════════════════════════════════════════════════
# GET /zakat/hawl-status
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/hawl-status")
async def hawl_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return Hawl tracking record for current user."""
    hawl = await _get_or_create_hawl(db, current_user.id)

    days_remaining = None
    if hawl.hawl_active and hawl.zakat_due_date:
        days_remaining = max(0, (hawl.zakat_due_date - _utcnow()).days)

    return {
        "hawl_active":          hawl.hawl_active,
        "nisab_crossed_at":     hawl.nisab_crossed_at.isoformat() if hawl.nisab_crossed_at else None,
        "zakat_due_date":       hawl.zakat_due_date.isoformat()   if hawl.zakat_due_date   else None,
        "days_remaining":       days_remaining,
        "hawl_reset_count":     hawl.hawl_reset_count or 0,
        "hawl_reset_at":        hawl.hawl_reset_at.isoformat()    if hawl.hawl_reset_at    else None,
        "last_reminder_sent_at": hawl.last_reminder_sent_at.isoformat() if hawl.last_reminder_sent_at else None,
    }


# ══════════════════════════════════════════════════════════════════════════════
# GET /zakat/live-rates   (reads from cache — no direct API call)
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/live-rates")
async def live_rates(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns the latest cached metal rates from metal_rate_cache.
    cache_age_hours: how old the cached data is.
    rates_warning: true if cache is older than 24 hours.
    """
    cache = await _latest_rate(db)
    if not cache:
        raise HTTPException(503, "Metal rate cache is empty. The background job has not run yet. Please try again in a few minutes.")

    age_hours = (_utcnow() - cache.fetched_at).total_seconds() / 3600

    return {
        "gold_usd_per_oz":     float(cache.gold_usd_oz),
        "silver_usd_per_oz":   float(cache.silver_usd_oz),
        "usd_to_pkr":          float(cache.usd_to_pkr),
        "gold_pkr_per_gram":   float(cache.gold_pkr_gram),
        "silver_pkr_per_gram": float(cache.silver_pkr_gram),
        "nisab_gold_pkr":      float(cache.nisab_gold_pkr),
        "nisab_silver_pkr":    float(cache.nisab_silver_pkr),
        "nisab_threshold_pkr": float(min(cache.nisab_gold_pkr, cache.nisab_silver_pkr)),
        "fetched_at":          cache.fetched_at.isoformat(),
        "cache_age_hours":     round(age_hours, 2),
        "rates_warning":       age_hours > 24,
        "source":              cache.source,
        "note":                "Nisab is the lower of gold (87.48g) or silver (612.36g) nisab.",
    }


# ══════════════════════════════════════════════════════════════════════════════
# POST /zakat/calculate   (upgraded — reads from DB, no manual input)
# ══════════════════════════════════════════════════════════════════════════════
@router.post("/calculate", status_code=201)
@limiter.limit("20/hour")
async def calculate_zakat(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Calculate Zakat from saved wealth profile + wallet balance.
    Wealth profile must have been verified (saved) within the last 30 days.
    """
    # Step 1 — Profile freshness check
    wp = await _get_or_create_wealth_profile(db, current_user.id)
    if wp.last_verified_at is None:
        raise HTTPException(400, detail={
            "code":    "PROFILE_STALE",
            "message": "Wealth profile must be reviewed before calculation. Please go to PUT /zakat/wealth-profile to save your current wealth data.",
        })
    days_since = (_utcnow() - wp.last_verified_at).days
    if days_since > 30:
        raise HTTPException(400, detail={
            "code":    "PROFILE_STALE",
            "message": f"Wealth profile was last verified {days_since} days ago. Please update it via PUT /zakat/wealth-profile before calculating.",
        })

    # Step 2 — Load wallet, settings, rates
    wallet = (await db.execute(select(Wallet).where(Wallet.user_id == current_user.id))).scalar_one_or_none()
    if not wallet:
        raise HTTPException(404, "Wallet not found.")
    if wallet.is_frozen:
        raise HTTPException(403, "Wallet is frozen.")

    settings = await _get_or_create_settings(db, current_user.id)

    cache = await _latest_rate(db)
    if not cache:
        raise HTTPException(503, "Metal rate cache is empty. The background job has not run yet. Please try again shortly.")

    gold_pkr_gram   = Decimal(str(cache.gold_pkr_gram))
    silver_pkr_gram = Decimal(str(cache.silver_pkr_gram))
    nisab_gold_pkr  = Decimal(str(cache.nisab_gold_pkr))
    nisab_silver_pkr= Decimal(str(cache.nisab_silver_pkr))

    wallet_balance  = wallet.balance or Decimal("0")

    # Step 3 — Resolve nisab threshold based on preference
    nisab_threshold = _resolve_nisab(settings.nisab_preference, nisab_gold_pkr, nisab_silver_pkr)

    # Step 4 — Total zakatable assets (bad_debts and business_fixed_assets excluded per fiqh)
    gold_value_pkr   = (wp.physical_gold_grams   or Decimal("0")) * gold_pkr_gram
    silver_value_pkr = (wp.physical_silver_grams or Decimal("0")) * silver_pkr_gram

    zakatable_assets = (
        wallet_balance
        + (wp.external_banks_pkr     or Decimal("0"))
        + (wp.other_wallets_pkr      or Decimal("0"))
        + gold_value_pkr
        + silver_value_pkr
        + (wp.business_tradeable_pkr or Decimal("0"))
        + (wp.business_cash_pkr      or Decimal("0"))
        + (wp.receivables_pkr        or Decimal("0"))
    )

    # Step 5 — Deductible liabilities
    total_liabilities = (
        (wp.personal_loans_pkr        or Decimal("0"))
        + (wp.credit_card_pkr         or Decimal("0"))
        + (wp.car_loan_installments_pkr or Decimal("0"))
        + (wp.other_liabilities_pkr   or Decimal("0"))
    )
    if wp.home_loan_include:
        total_liabilities += (wp.home_loan_pkr or Decimal("0"))

    # Step 6 — Net zakatable wealth
    net_zakatable = max(zakatable_assets - total_liabilities, Decimal("0"))

    # Step 7 — Zakat due
    zakat_obligatory = net_zakatable >= nisab_threshold
    zakat_due = (net_zakatable * ZAKAT_RATE).quantize(Decimal("0.01")) if zakat_obligatory else Decimal("0")

    # Step 8 — Hawl tracking
    hawl = await _get_or_create_hawl(db, current_user.id)
    import asyncio as _aio

    if zakat_obligatory and not hawl.hawl_active:
        hawl.nisab_crossed_at = _utcnow()
        hawl.zakat_due_date   = _utcnow() + timedelta(days=HAWL_DAYS)
        hawl.hawl_active      = True
        await db.commit()
        _aio.create_task(send_notification(
            db, current_user.id,
            title="🕌 Hawl Started",
            body="Your wealth has crossed the Nisab threshold. Your Hawl (lunar year) has begun.",
            type="zakat",
            data={"event": "hawl_started", "due_date": hawl.zakat_due_date.isoformat()},
        ))

    elif not zakat_obligatory and hawl.hawl_active:
        hawl.hawl_reset_count = (hawl.hawl_reset_count or 0) + 1
        hawl.hawl_reset_at    = _utcnow()
        hawl.hawl_active      = False
        hawl.nisab_crossed_at = None
        hawl.zakat_due_date   = None
        await db.commit()
        _aio.create_task(send_notification(
            db, current_user.id,
            title="⚠️ Hawl Reset",
            body="Your wealth has dropped below the Nisab threshold. Your Hawl has been reset.",
            type="zakat",
            data={"event": "hawl_reset"},
        ))

    # Step 9 — Save frozen snapshot
    record = ZakatCalculation(
        user_id                  = current_user.id,
        cash_pkr                 = wallet_balance,
        gold_grams               = wp.physical_gold_grams   or Decimal("0"),
        silver_grams             = wp.physical_silver_grams or Decimal("0"),
        business_inventory_pkr   = (wp.business_tradeable_pkr or Decimal("0")) + (wp.business_cash_pkr or Decimal("0")),
        receivables_pkr          = wp.receivables_pkr        or Decimal("0"),
        gold_rate_per_gram       = gold_pkr_gram.quantize(Decimal("0.01")),
        silver_rate_per_gram     = silver_pkr_gram.quantize(Decimal("0.0001")),
        usd_to_pkr_rate          = Decimal(str(cache.usd_to_pkr)).quantize(Decimal("0.0001")),
        total_assets_pkr         = zakatable_assets.quantize(Decimal("0.01")),
        nisab_threshold_pkr      = nisab_threshold.quantize(Decimal("0.01")),
        zakat_due_pkr            = zakat_due,
        madhab_used              = settings.madhab,
        nisab_preference_used    = settings.nisab_preference,
        business_tradeable_pkr   = wp.business_tradeable_pkr or Decimal("0"),
        business_cash_pkr        = wp.business_cash_pkr      or Decimal("0"),
        bad_debts_pkr            = wp.bad_debts_pkr           or Decimal("0"),
        personal_loans_pkr       = wp.personal_loans_pkr      or Decimal("0"),
        credit_card_pkr          = wp.credit_card_pkr         or Decimal("0"),
        car_loan_installments    = wp.car_loan_installments_pkr or Decimal("0"),
        home_loan_pkr            = wp.home_loan_pkr           or Decimal("0"),
        home_loan_included       = wp.home_loan_include,
        other_liabilities_pkr    = wp.other_liabilities_pkr   or Decimal("0"),
        total_liabilities_pkr    = total_liabilities.quantize(Decimal("0.01")),
        net_zakatable_pkr        = net_zakatable.quantize(Decimal("0.01")),
        wallet_balance_snapshot  = wallet_balance.quantize(Decimal("0.01")),
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)

    return {
        "calculation_id":       str(record.id),
        "madhab":               settings.madhab,
        "nisab_preference":     settings.nisab_preference,
        "wallet_balance":       float(wallet_balance),
        "zakatable_assets_pkr": float(zakatable_assets.quantize(Decimal("0.01"))),
        "total_liabilities_pkr": float(total_liabilities.quantize(Decimal("0.01"))),
        "net_zakatable_pkr":    float(net_zakatable.quantize(Decimal("0.01"))),
        "nisab_threshold_pkr":  float(nisab_threshold.quantize(Decimal("0.01"))),
        "zakat_due_pkr":        float(zakat_due),
        "zakat_obligatory":     zakat_obligatory,
        "gold_rate_per_gram":   float(gold_pkr_gram.quantize(Decimal("0.01"))),
        "silver_rate_per_gram": float(silver_pkr_gram.quantize(Decimal("0.0001"))),
        "usd_to_pkr":           float(cache.usd_to_pkr),
        "hawl_active":          hawl.hawl_active,
        "hawl_due_date":        hawl.zakat_due_date.isoformat() if hawl.zakat_due_date else None,
        "assets_breakdown": {
            "wallet_balance_pkr":    float(wallet_balance),
            "external_banks_pkr":    float(wp.external_banks_pkr    or 0),
            "other_wallets_pkr":     float(wp.other_wallets_pkr     or 0),
            "gold_value_pkr":        float(gold_value_pkr.quantize(Decimal("0.01"))),
            "silver_value_pkr":      float(silver_value_pkr.quantize(Decimal("0.01"))),
            "business_tradeable_pkr": float(wp.business_tradeable_pkr or 0),
            "business_cash_pkr":     float(wp.business_cash_pkr      or 0),
            "receivables_pkr":       float(wp.receivables_pkr        or 0),
        },
        "liabilities_breakdown": {
            "personal_loans_pkr":        float(wp.personal_loans_pkr        or 0),
            "credit_card_pkr":           float(wp.credit_card_pkr           or 0),
            "car_loan_installments_pkr": float(wp.car_loan_installments_pkr or 0),
            "home_loan_pkr":             float(wp.home_loan_pkr             or 0) if wp.home_loan_include else 0,
            "other_liabilities_pkr":     float(wp.other_liabilities_pkr     or 0),
        },
        "message": (
            f"Zakat obligatory: PKR {zakat_due:,.2f} due on net wealth of PKR {net_zakatable:,.2f}."
            if zakat_obligatory
            else "Your net zakatable wealth is below the Nisab threshold. Zakat is not obligatory."
        ),
    }


# ══════════════════════════════════════════════════════════════════════════════
# POST /zakat/pay   (unchanged logic, rate limit preserved)
# ══════════════════════════════════════════════════════════════════════════════
class ZakatPayRequest(BaseModel):
    calculation_id: UUID
    pin:            str = Field(..., min_length=4, max_length=6)


@router.post("/pay", status_code=201)
@limiter.limit("5/hour")
async def pay_zakat(
    request: Request,
    body: ZakatPayRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Deduct zakat_due from wallet, mark calculation as paid."""
    calc = (await db.execute(
        select(ZakatCalculation).where(
            ZakatCalculation.id      == body.calculation_id,
            ZakatCalculation.user_id == current_user.id,
        )
    )).scalar_one_or_none()

    if not calc:
        raise HTTPException(404, "Zakat calculation not found.")
    if calc.is_paid:
        raise HTTPException(400, "This zakat has already been paid.")
    if not calc.zakat_due_pkr or calc.zakat_due_pkr <= 0:
        raise HTTPException(400, "No zakat due on this calculation.")

    if not current_user.pin_hash:
        raise HTTPException(400, "PIN not set. Please set a PIN first.")
    if not bcrypt.checkpw(body.pin.encode(), current_user.pin_hash.encode()):
        raise HTTPException(401, "Incorrect PIN.")

    wallet = (await db.execute(
        select(Wallet).where(Wallet.user_id == current_user.id)
    )).scalar_one_or_none()
    if not wallet:
        raise HTTPException(404, "Wallet not found.")
    if wallet.is_frozen:
        raise HTTPException(403, "Wallet is frozen. Contact support.")
    if wallet.balance < calc.zakat_due_pkr:
        raise HTTPException(400, f"Insufficient balance. Need PKR {calc.zakat_due_pkr:,.2f}, have PKR {wallet.balance:,.2f}.")

    wallet.balance -= calc.zakat_due_pkr
    ref = generate_reference()
    txn = Transaction(
        reference_number = ref,
        type             = "zakat",
        amount           = calc.zakat_due_pkr,
        fee              = Decimal("0"),
        status           = "completed",
        sender_id        = current_user.id,
        purpose          = "Zakat",
        description      = f"Zakat payment — calculation {calc.id}",
        completed_at     = _utcnow(),
        tx_metadata      = {"calculation_id": str(calc.id)},
    )
    db.add(txn)

    calc.is_paid = True
    calc.paid_at = _utcnow()
    await db.commit()
    await db.refresh(wallet)

    await send_notification(
        db, current_user.id,
        title = "Zakat Paid ✅",
        body  = f"PKR {calc.zakat_due_pkr:,.2f} zakat paid successfully. JazakAllah Khair.",
        type  = "zakat",
        data  = {"calculation_id": str(calc.id), "reference": ref},
    )

    return {
        "status":           "paid",
        "zakat_paid_pkr":   float(calc.zakat_due_pkr),
        "reference_number": ref,
        "new_balance":      float(wallet.balance),
        "message":          f"PKR {calc.zakat_due_pkr:,.2f} zakat paid. JazakAllah Khair.",
    }


# ══════════════════════════════════════════════════════════════════════════════
# GET /zakat/history   (extended with new fields)
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/history")
async def zakat_history(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return all zakat calculations for the current user, newest first."""
    result = await db.execute(
        select(ZakatCalculation)
        .where(ZakatCalculation.user_id == current_user.id)
        .order_by(ZakatCalculation.created_at.desc())
    )
    records = result.scalars().all()

    return {
        "count": len(records),
        "calculations": [
            {
                "id":                    str(r.id),
                "total_assets_pkr":      float(r.total_assets_pkr or 0),
                "net_zakatable_pkr":     float(r.net_zakatable_pkr or 0),
                "total_liabilities_pkr": float(r.total_liabilities_pkr or 0),
                "nisab_threshold_pkr":   float(r.nisab_threshold_pkr or 0),
                "zakat_due_pkr":         float(r.zakat_due_pkr or 0),
                "is_paid":               r.is_paid,
                "paid_at":               r.paid_at.isoformat() if r.paid_at else None,
                "madhab_used":           r.madhab_used,
                "nisab_preference_used": r.nisab_preference_used,
                "wallet_balance_snapshot": float(r.wallet_balance_snapshot or 0),
                "gold_rate_per_gram":    float(r.gold_rate_per_gram or 0),
                "silver_rate_per_gram":  float(r.silver_rate_per_gram or 0),
                "usd_to_pkr_rate":       float(r.usd_to_pkr_rate or 0),
                "breakdown": {
                    "cash_pkr":               float(r.cash_pkr or 0),
                    "gold_grams":             float(r.gold_grams or 0),
                    "silver_grams":           float(r.silver_grams or 0),
                    "business_inventory_pkr": float(r.business_inventory_pkr or 0),
                    "receivables_pkr":        float(r.receivables_pkr or 0),
                    "business_tradeable_pkr": float(r.business_tradeable_pkr or 0),
                    "business_cash_pkr":      float(r.business_cash_pkr or 0),
                    "personal_loans_pkr":     float(r.personal_loans_pkr or 0),
                    "credit_card_pkr":        float(r.credit_card_pkr or 0),
                    "car_loan_installments":  float(r.car_loan_installments or 0),
                    "home_loan_pkr":          float(r.home_loan_pkr or 0),
                    "home_loan_included":     r.home_loan_included,
                    "other_liabilities_pkr":  float(r.other_liabilities_pkr or 0),
                },
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in records
        ],
    }
