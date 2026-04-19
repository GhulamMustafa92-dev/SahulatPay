"""Gold & Silver trading router — buy, sell, holdings, live rates."""
import bcrypt
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from limiter import limiter
from models.gold import GoldHolding
from models.transaction import Transaction
from models.user import User
from models.wallet import Wallet
from models.zakat import MetalRateCache
from services.auth_service import get_current_user
from services.platform_ledger import ledger_credit, ledger_debit, make_idem_key
from services.wallet_service import generate_reference

router = APIRouter()

SPREAD_BUY  = Decimal("0.015")
SPREAD_SELL = Decimal("0.015")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _verify_pin(user: User, pin: str):
    if not user.pin_hash:
        raise HTTPException(400, "PIN not set")
    if not bcrypt.checkpw(pin.encode(), user.pin_hash.encode()):
        raise HTTPException(401, "Incorrect PIN")


async def _latest_rate(db: AsyncSession) -> MetalRateCache:
    rate = (await db.execute(
        select(MetalRateCache).order_by(MetalRateCache.fetched_at.desc()).limit(1)
    )).scalar_one_or_none()
    if not rate:
        raise HTTPException(503, "Metal rates unavailable. Please try again shortly.")
    return rate


async def _get_or_create_holding(db: AsyncSession, user_id: UUID) -> GoldHolding:
    holding = (await db.execute(
        select(GoldHolding).where(GoldHolding.user_id == user_id)
    )).scalar_one_or_none()
    if not holding:
        holding = GoldHolding(user_id=user_id)
        db.add(holding)
        await db.flush()
    return holding


# ── Schemas ───────────────────────────────────────────────────────────────────

class BuyRequest(BaseModel):
    metal:     str     = Field(..., pattern="^(gold|silver)$")
    amount_pkr: Decimal = Field(..., gt=0, description="PKR amount to spend")
    pin:       str


class SellRequest(BaseModel):
    metal:  str     = Field(..., pattern="^(gold|silver)$")
    grams:  Decimal = Field(..., gt=0, description="Grams to sell")
    pin:    str


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/rates")
async def get_rates(db: AsyncSession = Depends(get_db)):
    """Live gold and silver rates (PKR/gram). Buy = market + 1.5%. Sell = market − 1.5%."""
    rate = await _latest_rate(db)
    return {
        "gold": {
            "market_pkr_gram": str(rate.gold_pkr_gram),
            "buy_pkr_gram":    str(round(rate.gold_pkr_gram   * (1 + SPREAD_BUY), 4)),
            "sell_pkr_gram":   str(round(rate.gold_pkr_gram   * (1 - SPREAD_SELL), 4)),
        },
        "silver": {
            "market_pkr_gram": str(rate.silver_pkr_gram),
            "buy_pkr_gram":    str(round(rate.silver_pkr_gram * (1 + SPREAD_BUY), 4)),
            "sell_pkr_gram":   str(round(rate.silver_pkr_gram * (1 - SPREAD_SELL), 4)),
        },
        "usd_to_pkr":  str(rate.usd_to_pkr),
        "fetched_at":  rate.fetched_at.isoformat() if rate.fetched_at else None,
    }


@router.get("/holdings")
async def get_holdings(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return user's current gold and silver holdings with live valuation."""
    holding = (await db.execute(
        select(GoldHolding).where(GoldHolding.user_id == current_user.id)
    )).scalar_one_or_none()

    if not holding or (holding.gold_grams == 0 and holding.silver_grams == 0):
        return {"gold_grams": "0.0000", "silver_grams": "0.0000",
                "total_invested_pkr": "0.00", "current_value_pkr": "0.00",
                "pnl_pkr": "0.00", "pnl_pct": "0.00"}

    rate = await _latest_rate(db)
    sell_gold   = rate.gold_pkr_gram   * (1 - SPREAD_SELL)
    sell_silver = rate.silver_pkr_gram * (1 - SPREAD_SELL)
    current_val = (holding.gold_grams * sell_gold) + (holding.silver_grams * sell_silver)
    invested    = holding.total_invested_pkr or Decimal("0")
    pnl         = current_val - invested
    pnl_pct     = (pnl / invested * 100) if invested > 0 else Decimal("0")

    return {
        "gold_grams":         str(holding.gold_grams),
        "silver_grams":       str(holding.silver_grams),
        "avg_gold_rate_pkr":  str(holding.avg_gold_rate_pkr),
        "avg_silver_rate_pkr":str(holding.avg_silver_rate_pkr),
        "total_invested_pkr": str(invested),
        "current_value_pkr":  str(round(current_val, 2)),
        "pnl_pkr":            str(round(pnl, 2)),
        "pnl_pct":            str(round(pnl_pct, 2)),
    }


@router.post("/buy", status_code=201)
@limiter.limit("10/hour")
async def buy_metal(
    request: Request,
    body: BuyRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Buy gold or silver. Deducts PKR from wallet, credits gold_platform pool, updates holdings."""
    await _verify_pin(current_user, body.pin)

    rate    = await _latest_rate(db)
    wallet  = (await db.execute(
        select(Wallet).where(Wallet.user_id == current_user.id).with_for_update()
    )).scalar_one_or_none()
    if not wallet:
        raise HTTPException(404, "Wallet not found")
    if wallet.is_frozen:
        raise HTTPException(403, "Wallet is frozen")
    if wallet.balance < body.amount_pkr:
        raise HTTPException(400, f"Insufficient balance. Available: PKR {wallet.balance:,.2f}")

    if body.metal == "gold":
        buy_rate = rate.gold_pkr_gram * (1 + SPREAD_BUY)
        grams    = (body.amount_pkr / buy_rate).quantize(Decimal("0.0001"))
        spread_desc = f"Gold purchase: {grams}g @ PKR {buy_rate:,.4f}/g"
    else:
        buy_rate = rate.silver_pkr_gram * (1 + SPREAD_BUY)
        grams    = (body.amount_pkr / buy_rate).quantize(Decimal("0.0001"))
        spread_desc = f"Silver purchase: {grams}g @ PKR {buy_rate:,.4f}/g"

    if grams <= 0:
        raise HTTPException(400, "Amount too small — minimum 0.0001 grams")

    wallet.balance -= body.amount_pkr

    holding = await _get_or_create_holding(db, current_user.id)
    old_invested = holding.total_invested_pkr or Decimal("0")

    if body.metal == "gold":
        old_grams = holding.gold_grams or Decimal("0")
        new_grams = old_grams + grams
        old_avg   = holding.avg_gold_rate_pkr or Decimal("0")
        new_avg   = ((old_avg * old_grams) + body.amount_pkr) / new_grams if new_grams > 0 else buy_rate
        holding.gold_grams        = new_grams
        holding.avg_gold_rate_pkr = new_avg.quantize(Decimal("0.0001"))
    else:
        old_grams = holding.silver_grams or Decimal("0")
        new_grams = old_grams + grams
        old_avg   = holding.avg_silver_rate_pkr or Decimal("0")
        new_avg   = ((old_avg * old_grams) + body.amount_pkr) / new_grams if new_grams > 0 else buy_rate
        holding.silver_grams        = new_grams
        holding.avg_silver_rate_pkr = new_avg.quantize(Decimal("0.0001"))

    holding.total_invested_pkr = old_invested + body.amount_pkr
    holding.last_updated       = _utcnow()

    ref = generate_reference()
    txn = Transaction(
        reference_number=ref, type="investment", amount=body.amount_pkr,
        fee=Decimal("0"), status="completed", sender_id=current_user.id,
        purpose="Investment", description=spread_desc,
        tx_metadata={"metal": body.metal, "grams": str(grams), "rate_pkr": str(buy_rate)},
        completed_at=_utcnow(),
    )
    db.add(txn)

    idem_key = make_idem_key("gold_buy", str(current_user.id), ref)
    await ledger_credit(db, "gold_platform", body.amount_pkr, idem_key,
                        user_id=current_user.id, reference=ref, note=spread_desc)

    spread_amt = body.amount_pkr * SPREAD_BUY / (1 + SPREAD_BUY)
    rev_idem   = make_idem_key("gold_buy_spread", str(current_user.id), ref)
    await ledger_credit(db, "platform_revenue", spread_amt, rev_idem,
                        user_id=current_user.id, reference=ref,
                        note=f"Gold spread revenue: {body.metal} {grams}g")

    await db.commit()
    await db.refresh(wallet)

    return {
        "status":       "completed",
        "metal":        body.metal,
        "grams_bought": str(grams),
        "rate_pkr":     str(round(buy_rate, 4)),
        "amount_pkr":   str(body.amount_pkr),
        "reference":    ref,
        "new_balance":  str(wallet.balance),
        "message":      f"Purchased {grams}g of {body.metal} for PKR {body.amount_pkr:,.2f}",
    }


@router.post("/sell", status_code=201)
@limiter.limit("10/hour")
async def sell_metal(
    request: Request,
    body: SellRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Sell gold or silver. Debits gold_platform pool, credits user wallet, spread to platform_revenue."""
    await _verify_pin(current_user, body.pin)

    rate    = await _latest_rate(db)
    holding = (await db.execute(
        select(GoldHolding).where(GoldHolding.user_id == current_user.id).with_for_update()
    )).scalar_one_or_none()

    if not holding:
        raise HTTPException(400, "No holdings found")

    if body.metal == "gold":
        if (holding.gold_grams or Decimal("0")) < body.grams:
            raise HTTPException(400, f"Insufficient gold. You own {holding.gold_grams}g")
        sell_rate   = rate.gold_pkr_gram * (1 - SPREAD_SELL)
        proceeds    = (body.grams * sell_rate).quantize(Decimal("0.01"))
        holding.gold_grams = (holding.gold_grams or Decimal("0")) - body.grams
        spread_desc = f"Gold sell: {body.grams}g @ PKR {sell_rate:,.4f}/g"
    else:
        if (holding.silver_grams or Decimal("0")) < body.grams:
            raise HTTPException(400, f"Insufficient silver. You own {holding.silver_grams}g")
        sell_rate   = rate.silver_pkr_gram * (1 - SPREAD_SELL)
        proceeds    = (body.grams * sell_rate).quantize(Decimal("0.01"))
        holding.silver_grams = (holding.silver_grams or Decimal("0")) - body.grams
        spread_desc = f"Silver sell: {body.grams}g @ PKR {sell_rate:,.4f}/g"

    invested_reduction = (body.grams * (holding.avg_gold_rate_pkr if body.metal == "gold"
                                         else holding.avg_silver_rate_pkr or Decimal("0")))
    holding.total_invested_pkr = max(
        Decimal("0"),
        (holding.total_invested_pkr or Decimal("0")) - invested_reduction
    )
    holding.last_updated = _utcnow()

    wallet = (await db.execute(
        select(Wallet).where(Wallet.user_id == current_user.id).with_for_update()
    )).scalar_one_or_none()
    if wallet:
        wallet.balance += proceeds

    ref  = generate_reference()
    txn  = Transaction(
        reference_number=ref, type="investment", amount=proceeds,
        fee=Decimal("0"), status="completed", recipient_id=current_user.id,
        purpose="Investment", description=spread_desc,
        tx_metadata={"metal": body.metal, "grams": str(body.grams), "rate_pkr": str(sell_rate)},
        completed_at=_utcnow(),
    )
    db.add(txn)

    idem_key = make_idem_key("gold_sell", str(current_user.id), ref)
    await ledger_debit(db, "gold_platform", proceeds, idem_key,
                       user_id=current_user.id, reference=ref, note=spread_desc)

    spread_amt = body.grams * rate.gold_pkr_gram * SPREAD_SELL if body.metal == "gold" \
                 else body.grams * rate.silver_pkr_gram * SPREAD_SELL
    rev_idem   = make_idem_key("gold_sell_spread", str(current_user.id), ref)
    await ledger_credit(db, "platform_revenue", spread_amt, rev_idem,
                        user_id=current_user.id, reference=ref,
                        note=f"Gold spread revenue: sell {body.metal} {body.grams}g")

    await db.commit()
    await db.refresh(wallet)

    return {
        "status":      "completed",
        "metal":       body.metal,
        "grams_sold":  str(body.grams),
        "rate_pkr":    str(round(sell_rate, 4)),
        "proceeds_pkr":str(proceeds),
        "reference":   ref,
        "new_balance": str(wallet.balance) if wallet else "N/A",
        "message":     f"Sold {body.grams}g of {body.metal} for PKR {proceeds:,.2f}",
    }
