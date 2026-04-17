"""Mock international transfer servers: Western Union, Wise, Remitly, MoneyGram."""
import secrets
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

from mock_servers.db import get_db
from mock_servers.models import MockInternationalTransfer

router = APIRouter()

PROVIDERS = {
    "western_union": "Western Union",
    "wise":          "Wise (TransferWise)",
    "remitly":       "Remitly",
    "moneygram":     "MoneyGram",
}

# Exchange rates (PKR → foreign currency, approximate)
RATES = {
    "USD": 277.50,
    "GBP": 350.20,
    "EUR": 302.80,
    "AED": 75.55,
    "SAR": 74.00,
    "CAD": 205.40,
    "AUD": 183.60,
    "EUR": 302.80,
}

PROVIDER_FEES = {
    "western_union": {"flat": 500, "percent": 1.0},
    "wise":          {"flat": 0,   "percent": 0.6},
    "remitly":       {"flat": 0,   "percent": 1.2},
    "moneygram":     {"flat": 400, "percent": 0.9},
}


class RemittanceRateRequest(BaseModel):
    provider: str
    amount_pkr: float
    currency: str
    country: str


class RemittanceSendRequest(BaseModel):
    provider: str
    amount_pkr: float
    currency: str
    country: str
    receiver_name: str
    receiver_phone: Optional[str] = None
    receiver_account: Optional[str] = None
    sender_phone: str
    purpose: str = "Family Support"


# ── GET /mock/international/providers ────────────────────────────────────────
@router.get("/providers")
def list_providers():
    return {
        "providers": [{"code": k, "name": v} for k, v in PROVIDERS.items()],
        "currencies": list(RATES.keys()),
    }


# ── POST /mock/international/rate ─────────────────────────────────────────────
@router.post("/rate")
def get_rate(body: RemittanceRateRequest):
    if body.provider not in PROVIDERS:
        raise HTTPException(400, f"Unknown provider: {body.provider}")
    if body.currency not in RATES:
        raise HTTPException(400, f"Unsupported currency: {body.currency}")
    fee_config = PROVIDER_FEES[body.provider]
    fee = fee_config["flat"] + (body.amount_pkr * fee_config["percent"] / 100)
    net_pkr = body.amount_pkr - fee
    fx_rate = RATES[body.currency]
    fx_amount = round(net_pkr / fx_rate, 2)
    return {
        "provider":      PROVIDERS[body.provider],
        "amount_pkr":    body.amount_pkr,
        "fee_pkr":       round(fee, 2),
        "net_pkr":       round(net_pkr, 2),
        "currency":      body.currency,
        "exchange_rate": fx_rate,
        "fx_amount":     fx_amount,
        "delivery_time": "Minutes" if body.provider == "wise" else "1-2 Business Days",
    }


# ── POST /mock/international/send ─────────────────────────────────────────────
@router.post("/send")
def send_international(body: RemittanceSendRequest, db: Session = Depends(get_db)):
    if body.provider not in PROVIDERS:
        raise HTTPException(400, f"Unknown provider: {body.provider}")
    if body.currency not in RATES:
        raise HTTPException(400, f"Unsupported currency: {body.currency}")
    fee_config = PROVIDER_FEES[body.provider]
    fee = fee_config["flat"] + (body.amount_pkr * fee_config["percent"] / 100)
    net_pkr = body.amount_pkr - fee
    fx_amount = round(net_pkr / RATES[body.currency], 2)
    ref = body.provider[:2].upper() + secrets.token_hex(5).upper()
    record = MockInternationalTransfer(
        provider=body.provider,
        reference=ref,
        sender_phone=body.sender_phone,
        receiver_name=body.receiver_name,
        country=body.country,
        amount_pkr=body.amount_pkr,
        amount_fx=fx_amount,
        currency=body.currency,
        status="processing",
    )
    db.add(record)
    db.commit()
    return {
        "success":       True,
        "reference":     ref,
        "provider":      PROVIDERS[body.provider],
        "receiver":      body.receiver_name,
        "country":       body.country,
        "amount_pkr":    body.amount_pkr,
        "fee_pkr":       round(fee, 2),
        "currency":      body.currency,
        "fx_amount":     fx_amount,
        "exchange_rate": RATES[body.currency],
        "status":        "processing",
        "eta":           "Minutes" if body.provider == "wise" else "1-2 business days",
        "message":       f"{body.currency} {fx_amount:,.2f} sent to {body.receiver_name} via {PROVIDERS[body.provider]}",
    }
