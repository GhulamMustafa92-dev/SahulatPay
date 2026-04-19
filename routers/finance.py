"""Finance router — Investments, Insurance, High-Yield Deposits. PROMPT 09."""
import bcrypt
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from limiter import limiter
from models.finance import Investment, InsurancePolicy, HighYieldDeposit
from models.user import User
from models.wallet import Wallet
from models.transaction import Transaction
from services.auth_service import get_current_user
from services.wallet_service import generate_reference
from services.platform_ledger import ledger_credit, ledger_debit, make_idem_key

router = APIRouter()


def _utcnow():
    return datetime.now(timezone.utc)


async def _verify_pin(user: User, pin: str):
    if not user.pin_hash:
        raise HTTPException(400, "PIN not set")
    if not bcrypt.checkpw(pin.encode(), user.pin_hash.encode()):
        raise HTTPException(401, "Incorrect PIN")


async def _deduct(db, user_id, amount: Decimal, ref, txn_type, purpose, desc, meta,
                  account_type: str = "main_float"):
    wallet = (await db.execute(select(Wallet).where(Wallet.user_id == user_id))).scalar_one_or_none()
    if not wallet:
        raise HTTPException(404, "Wallet not found")
    if wallet.is_frozen:
        raise HTTPException(403, "Wallet is frozen")
    if wallet.balance < amount:
        raise HTTPException(400, f"Insufficient balance. Available: PKR {wallet.balance:,.2f}")
    wallet.balance -= amount
    txn = Transaction(
        reference_number=ref, type=txn_type, amount=amount,
        fee=Decimal("0"), status="completed", sender_id=user_id,
        purpose=purpose, description=desc, tx_metadata=meta,
        completed_at=_utcnow(),
    )
    db.add(txn)
    idem_key = make_idem_key("deduct", account_type, str(user_id), ref)
    await ledger_credit(db, account_type, amount, idem_key,
                        user_id=user_id, reference=ref, note=desc)
    await db.commit()
    await db.refresh(wallet)
    return wallet


async def _credit(db, user_id, amount: Decimal, ref, txn_type, purpose, desc, meta,
                  account_type: str = "main_float"):
    wallet = (await db.execute(select(Wallet).where(Wallet.user_id == user_id))).scalar_one_or_none()
    if wallet:
        wallet.balance += amount
        txn = Transaction(
            reference_number=ref, type=txn_type, amount=amount,
            fee=Decimal("0"), status="completed", recipient_id=user_id,
            purpose=purpose, description=desc, tx_metadata=meta,
            completed_at=_utcnow(),
        )
        db.add(txn)
        idem_key = make_idem_key("credit", account_type, str(user_id), ref)
        await ledger_debit(db, account_type, amount, idem_key,
                           user_id=user_id, reference=ref, note=desc)
        await db.commit()
        await db.refresh(wallet)
    return wallet


# ════════════════════════════════════════════════════════════════════════════
# INVESTMENTS
# ════════════════════════════════════════════════════════════════════════════
class InvestmentCreate(BaseModel):
    plan_name:     str     = Field(..., min_length=2, max_length=255)
    amount:        Decimal = Field(..., gt=0)
    return_rate:   Decimal = Field(..., gt=0, le=100)
    maturity_date: date
    pin:           str


class WithdrawRequest(BaseModel):
    pin: str


@router.get("/investments/my")
async def my_investments(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Investment)
        .where(Investment.user_id == current_user.id)
        .order_by(Investment.created_at.desc())
    )
    investments = result.scalars().all()
    today = date.today()
    out = []
    for inv in investments:
        days_held = (today - inv.created_at.date()).days
        days_total = (inv.maturity_date - inv.created_at.date()).days if inv.maturity_date else 365
        projected = float(inv.amount) * float(inv.return_rate) / 100 * (days_total / 365) if days_total > 0 else 0
        out.append({
            "id":              str(inv.id),
            "plan_name":       inv.plan_name,
            "amount":          str(inv.amount),
            "return_rate":     str(inv.return_rate),
            "status":          inv.status,
            "maturity_date":   str(inv.maturity_date) if inv.maturity_date else None,
            "expected_return": str(round(projected, 2)),
            "days_remaining":  max(0, (inv.maturity_date - today).days) if inv.maturity_date else None,
            "is_matured":      bool(inv.maturity_date and today >= inv.maturity_date),
            "created_at":      inv.created_at.isoformat(),
        })
    return {"investments": out, "total": len(out)}


@router.post("/investments", status_code=201)
@limiter.limit("10/hour")
async def create_investment(
    request: Request,
    body: InvestmentCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _verify_pin(current_user, body.pin)
    if body.maturity_date <= date.today():
        raise HTTPException(400, "Maturity date must be in the future")

    days = (body.maturity_date - date.today()).days
    expected = body.amount * body.return_rate / 100 * Decimal(str(days / 365))

    ref = generate_reference()
    wallet = await _deduct(
        db, current_user.id, body.amount, ref,
        "investment", "Investment",
        f"Investment: {body.plan_name} @ {body.return_rate}% p.a.",
        {"plan_name": body.plan_name, "return_rate": str(body.return_rate)},
        account_type="investment_pool",
    )
    inv = Investment(
        user_id=current_user.id,
        plan_name=body.plan_name,
        amount=body.amount,
        return_rate=body.return_rate,
        maturity_date=body.maturity_date,
        expected_return=round(expected, 2),
        status="active",
    )
    db.add(inv)
    await db.commit()
    await db.refresh(inv)
    return {
        "id":             str(inv.id),
        "plan_name":      inv.plan_name,
        "amount":         str(inv.amount),
        "return_rate":    str(inv.return_rate),
        "maturity_date":  str(inv.maturity_date),
        "expected_return": str(inv.expected_return),
        "new_balance":    str(wallet.balance),
        "message":        f"Investment of PKR {body.amount:,.2f} in '{body.plan_name}' created.",
    }


@router.post("/investments/{inv_id}/withdraw")
async def withdraw_investment(
    inv_id: UUID,
    body: WithdrawRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _verify_pin(current_user, body.pin)
    inv = (await db.execute(select(Investment).where(Investment.id == inv_id))).scalar_one_or_none()
    if not inv or inv.user_id != current_user.id:
        raise HTTPException(404, "Investment not found")
    if inv.status == "withdrawn":
        raise HTTPException(400, "Investment already withdrawn")

    today = date.today()
    is_matured = inv.maturity_date and today >= inv.maturity_date
    actual_return = inv.expected_return if is_matured else Decimal("0")
    total_payout  = inv.amount + (actual_return or Decimal("0"))

    inv.status       = "withdrawn"
    inv.actual_return = actual_return
    inv.withdrawn_at  = _utcnow()
    await db.commit()

    ref = generate_reference()
    wallet = await _credit(
        db, current_user.id, total_payout, ref,
        "investment", "Investment",
        f"Investment withdrawal: {inv.plan_name}" + (" (matured)" if is_matured else " (early)"),
        {"plan_name": inv.plan_name, "matured": is_matured},
        account_type="investment_pool",
    )
    return {
        "status":       "withdrawn",
        "principal":    str(inv.amount),
        "return":       str(actual_return),
        "total_payout": str(total_payout),
        "matured":      is_matured,
        "new_balance":  str(wallet.balance) if wallet else "N/A",
        "message":      f"PKR {total_payout:,.2f} returned to wallet.",
    }


# ════════════════════════════════════════════════════════════════════════════
# INSURANCE
# ════════════════════════════════════════════════════════════════════════════
class InsuranceCreate(BaseModel):
    policy_type: str = Field(..., pattern="^(life|health|vehicle|travel|home)$")
    plan_name:   str = Field(..., min_length=2, max_length=255)
    premium:     Decimal = Field(..., gt=0)
    coverage:    Decimal = Field(..., gt=0)
    expires_at:  Optional[datetime] = None
    pin:         str


@router.get("/insurance/my")
async def my_insurance(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(InsurancePolicy)
        .where(InsurancePolicy.user_id == current_user.id)
        .order_by(InsurancePolicy.activated_at.desc())
    )
    policies = result.scalars().all()
    return {
        "policies": [
            {
                "id":           str(p.id),
                "policy_type":  p.policy_type,
                "plan_name":    p.plan_name,
                "premium":      str(p.premium),
                "coverage":     str(p.coverage),
                "status":       p.status,
                "expires_at":   p.expires_at.isoformat() if p.expires_at else None,
                "activated_at": p.activated_at.isoformat(),
            }
            for p in policies
        ],
        "total": len(policies),
        "active": sum(1 for p in policies if p.status == "active"),
    }


@router.post("/insurance", status_code=201)
@limiter.limit("10/hour")
async def create_insurance(
    request: Request,
    body: InsuranceCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _verify_pin(current_user, body.pin)
    ref = generate_reference()
    wallet = await _deduct(
        db, current_user.id, body.premium, ref,
        "bill", "Insurance",
        f"Insurance premium: {body.plan_name} ({body.policy_type})",
        {"policy_type": body.policy_type, "plan_name": body.plan_name},
        account_type="insurance_pool",
    )
    policy = InsurancePolicy(
        user_id=current_user.id,
        policy_type=body.policy_type,
        plan_name=body.plan_name,
        premium=body.premium,
        premium_paid=body.premium,
        coverage=body.coverage,
        expires_at=body.expires_at,
        policy_start=_utcnow(),
        policy_end=body.expires_at,
        status="active",
    )
    db.add(policy)
    await db.commit()
    await db.refresh(policy)
    return {
        "id":          str(policy.id),
        "policy_type": policy.policy_type,
        "plan_name":   policy.plan_name,
        "premium":     str(policy.premium),
        "coverage":    str(policy.coverage),
        "status":      policy.status,
        "new_balance": str(wallet.balance),
        "message":     f"Insurance policy '{body.plan_name}' activated.",
    }


@router.post("/insurance/{policy_id}/deactivate")
async def deactivate_insurance(
    policy_id: UUID,
    body: WithdrawRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _verify_pin(current_user, body.pin)
    policy = (await db.execute(select(InsurancePolicy).where(InsurancePolicy.id == policy_id))).scalar_one_or_none()
    if not policy or policy.user_id != current_user.id:
        raise HTTPException(404, "Policy not found")
    if policy.status != "active":
        raise HTTPException(400, f"Policy is already {policy.status}")

    now           = _utcnow()
    premium_paid  = policy.premium_paid or policy.premium or Decimal("0")
    policy_start  = policy.policy_start or policy.activated_at or now
    policy_end    = policy.policy_end or policy.expires_at

    # ── Pro-rata refund calculation ───────────────────────────────────────────
    days_since_start = max(0, (now - policy_start).days)
    if days_since_start <= 15:
        # Cooling-off period — 100% refund
        refund_amount = premium_paid
        refund_note   = "Insurance cancel — cooling-off period (100% refund)"
    elif policy_end:
        total_days    = max(1, (policy_end - policy_start).days)
        days_remaining= max(0, (policy_end - now).days)
        refund_amount = (Decimal(str(days_remaining)) / Decimal(str(total_days))) * premium_paid
        refund_amount = refund_amount.quantize(Decimal("0.01"))
        refund_note   = (
            f"Insurance cancel — pro-rata refund ({days_remaining}/{total_days} days remaining)"
        )
    else:
        refund_amount = Decimal("0")
        refund_note   = "Insurance cancel — no refund (no expiry date set)"

    policy.status       = "cancelled"
    policy.cancelled_at = now
    policy.refund_paid  = refund_amount

    if refund_amount > 0:
        wallet = (await db.execute(
            select(Wallet).where(Wallet.user_id == current_user.id)
        )).scalar_one_or_none()
        if wallet:
            wallet.balance += refund_amount
        ref = generate_reference()
        db.add(Transaction(
            reference_number=ref, type="bill", amount=refund_amount,
            fee=Decimal("0"), status="completed", recipient_id=current_user.id,
            purpose="Insurance", description=refund_note,
            tx_metadata={"policy_id": str(policy_id)},
            completed_at=now,
        ))
        await ledger_debit(db, "insurance_pool", refund_amount,
                           make_idem_key("insurance_refund", str(current_user.id), str(policy_id)),
                           user_id=current_user.id, reference=ref, note=refund_note)

        earned = premium_paid - refund_amount
        if earned > 0:
            await ledger_debit(db, "insurance_pool", earned,
                               make_idem_key("insurance_earned", str(current_user.id), str(policy_id)),
                               user_id=current_user.id, reference=ref,
                               note="Insurance earned premium moved to revenue")
            await ledger_credit(db, "platform_revenue", earned,
                                make_idem_key("insurance_earned_rev", str(current_user.id), str(policy_id)),
                                user_id=current_user.id, reference=ref,
                                note="Insurance earned premium")

    await db.commit()
    return {
        "status":         "cancelled",
        "policy_id":      str(policy_id),
        "refund_amount":  str(refund_amount),
        "message":        f"Policy '{policy.plan_name}' cancelled. PKR {refund_amount:,.2f} refunded.",
    }


# ════════════════════════════════════════════════════════════════════════════
# HIGH-YIELD DEPOSITS
# ════════════════════════════════════════════════════════════════════════════
class HighYieldCreate(BaseModel):
    amount:        Decimal = Field(..., gt=0)
    interest_rate: Decimal = Field(..., gt=0, le=100)
    period_days:   int     = Field(..., ge=30, le=3650)
    pin:           str


@router.get("/high-yield/my")
async def my_high_yield(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(HighYieldDeposit)
        .where(HighYieldDeposit.user_id == current_user.id)
        .order_by(HighYieldDeposit.created_at.desc())
    )
    deposits = result.scalars().all()
    today = date.today()
    out = []
    for d in deposits:
        projected = float(d.amount) * float(d.interest_rate) / 100 * (d.period_days / 365)
        days_remaining = max(0, (d.maturity_date - today).days) if d.maturity_date else 0
        out.append({
            "id":                 str(d.id),
            "amount":             str(d.amount),
            "interest_rate":      str(d.interest_rate),
            "period_days":        d.period_days,
            "status":             d.status,
            "maturity_date":      str(d.maturity_date),
            "days_remaining":     days_remaining,
            "is_matured":         today >= d.maturity_date,
            "projected_interest": str(round(projected, 2)),
            "expected_interest":  str(d.expected_interest),
            "early_withdrawal":   d.early_withdrawal,
            "created_at":         d.created_at.isoformat(),
        })
    return {"deposits": out, "total": len(out)}


@router.post("/high-yield", status_code=201)
@limiter.limit("10/hour")
async def create_high_yield(
    request: Request,
    body: HighYieldCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _verify_pin(current_user, body.pin)
    maturity = date.today() + timedelta(days=body.period_days)
    expected_interest = body.amount * body.interest_rate / 100 * Decimal(str(body.period_days / 365))

    ref = generate_reference()
    wallet = await _deduct(
        db, current_user.id, body.amount, ref,
        "investment", "Investment",
        f"High-yield deposit @ {body.interest_rate}% for {body.period_days} days",
        {"interest_rate": str(body.interest_rate), "period_days": body.period_days},
        account_type="investment_pool",
    )
    deposit = HighYieldDeposit(
        user_id=current_user.id,
        amount=body.amount,
        interest_rate=body.interest_rate,
        period_days=body.period_days,
        maturity_date=maturity,
        expected_interest=round(expected_interest, 2),
        status="active",
    )
    db.add(deposit)
    await db.commit()
    await db.refresh(deposit)
    return {
        "id":                str(deposit.id),
        "amount":            str(deposit.amount),
        "interest_rate":     str(deposit.interest_rate),
        "period_days":       deposit.period_days,
        "maturity_date":     str(deposit.maturity_date),
        "expected_interest": str(deposit.expected_interest),
        "new_balance":       str(wallet.balance),
        "message":           f"PKR {body.amount:,.2f} locked for {body.period_days} days at {body.interest_rate}% p.a.",
    }


@router.post("/high-yield/{deposit_id}/withdraw")
async def withdraw_high_yield(
    deposit_id: UUID,
    body: WithdrawRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _verify_pin(current_user, body.pin)
    dep = (await db.execute(select(HighYieldDeposit).where(HighYieldDeposit.id == deposit_id))).scalar_one_or_none()
    if not dep or dep.user_id != current_user.id:
        raise HTTPException(404, "Deposit not found")
    if dep.status == "withdrawn":
        raise HTTPException(400, "Already withdrawn")

    today = date.today()
    is_matured     = today >= dep.maturity_date
    early          = not is_matured
    interest_earned = dep.expected_interest if is_matured else Decimal("0")
    payout          = dep.amount + interest_earned

    dep.status           = "withdrawn"
    dep.early_withdrawal = early
    dep.matured_at       = _utcnow()
    await db.commit()

    ref = generate_reference()
    wallet = await _credit(
        db, current_user.id, payout, ref,
        "investment", "Investment",
        f"High-yield withdrawal" + (" (early — interest forfeited)" if early else " (matured)"),
        {"deposit_id": str(deposit_id), "early": early},
        account_type="investment_pool",
    )
    return {
        "status":          "withdrawn",
        "principal":       str(dep.amount),
        "interest_earned": str(interest_earned),
        "total_payout":    str(payout),
        "early_withdrawal": early,
        "interest_forfeited": early,
        "new_balance":     str(wallet.balance) if wallet else "N/A",
        "message": (
            f"PKR {payout:,.2f} returned. Interest forfeited (early withdrawal)." if early
            else f"PKR {payout:,.2f} returned including PKR {interest_earned:,.2f} interest."
        ),
    }
