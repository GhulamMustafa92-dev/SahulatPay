"""Mock merchant subscription servers (Netflix, Spotify, etc.) + card authorization gateway."""
import hashlib
import secrets
from datetime import date, datetime
from dateutil.relativedelta import relativedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

from mock_servers.db import get_db
from mock_servers.models import MockMerchantSubscription

router = APIRouter()

MERCHANTS = {
    "netflix":  {"name": "Netflix",          "plans": {"basic": 1200, "standard": 1500, "premium": 2200}},
    "spotify":  {"name": "Spotify",          "plans": {"individual": 499, "duo": 650, "family": 750}},
    "youtube":  {"name": "YouTube Premium",  "plans": {"individual": 449, "family": 699}},
    "icloud":   {"name": "Apple iCloud",     "plans": {"50gb": 130, "200gb": 400, "2tb": 1200}},
    "amazon":   {"name": "Amazon Prime",     "plans": {"monthly": 800}},
    "canva":    {"name": "Canva Pro",        "plans": {"monthly": 1800, "yearly": 15000}},
    "chatgpt":  {"name": "ChatGPT Plus",     "plans": {"monthly": 5600}},
    "adobe":    {"name": "Adobe Creative",   "plans": {"photography": 2800, "all_apps": 8500}},
    "microsoft":{"name": "Microsoft 365",   "plans": {"personal": 1800, "family": 3000}},
    "duolingo": {"name": "Duolingo Plus",    "plans": {"monthly": 900}},
}


class MerchantSubscribeRequest(BaseModel):
    merchant_code: str
    card_number: str
    last_four: str
    user_phone: str
    plan: str
    billing_cycle: str = "monthly"


class CardAuthorizeRequest(BaseModel):
    merchant_code: str
    card_number_hash: str
    amount: float
    currency: str = "PKR"
    description: Optional[str] = None


class CardAuthorizeResponse(BaseModel):
    approved: bool
    auth_code: Optional[str] = None
    decline_reason: Optional[str] = None
    amount: float
    merchant: str


# ── GET /mock/merchants/list ──────────────────────────────────────────────────
@router.get("/list")
def list_merchants():
    return {
        "merchants": [
            {"code": k, "name": v["name"], "plans": v["plans"]}
            for k, v in MERCHANTS.items()
        ]
    }


# ── POST /mock/merchants/subscribe ───────────────────────────────────────────
@router.post("/subscribe")
def subscribe(body: MerchantSubscribeRequest, db: Session = Depends(get_db)):
    if body.merchant_code not in MERCHANTS:
        raise HTTPException(400, f"Unknown merchant: {body.merchant_code}")
    merchant = MERCHANTS[body.merchant_code]
    if body.plan not in merchant["plans"]:
        raise HTTPException(400, f"Invalid plan. Available: {list(merchant['plans'])}")
    amount = merchant["plans"][body.plan]
    card_hash = hashlib.sha256(body.card_number.encode()).hexdigest()
    existing = db.query(MockMerchantSubscription).filter_by(
        merchant_code=body.merchant_code, card_hash=card_hash
    ).first()
    if existing and existing.is_active:
        raise HTTPException(409, f"Card already subscribed to {merchant['name']}")
    delta = relativedelta(months=1) if body.billing_cycle == "monthly" else relativedelta(years=1)
    sub = MockMerchantSubscription(
        merchant_code  = body.merchant_code,
        card_hash      = card_hash,
        last_four      = body.last_four,
        user_phone     = body.user_phone,
        amount         = amount,
        billing_cycle  = body.billing_cycle,
        next_charge_at = date.today() + delta,
    )
    db.add(sub)
    db.commit()
    return {
        "success":       True,
        "merchant":      merchant["name"],
        "plan":          body.plan,
        "amount":        amount,
        "billing_cycle": body.billing_cycle,
        "next_charge":   str(date.today() + delta),
        "message":       f"Subscribed to {merchant['name']} {body.plan} plan. First charge on {date.today() + delta}.",
    }


# ── POST /mock/merchants/authorize (card gateway) ────────────────────────────
@router.post("/authorize", response_model=CardAuthorizeResponse)
def authorize_card(body: CardAuthorizeRequest):
    """
    Card authorization gateway — called by mock merchants to charge a card.
    In production this is replaced by real Visa/Mastercard authorization.
    The actual wallet deduction is handled by the main SahulatPay /cards/authorize endpoint.
    This mock just simulates the merchant side of the handshake.
    """
    if body.merchant_code not in MERCHANTS:
        return CardAuthorizeResponse(
            approved=False,
            decline_reason="Unknown merchant",
            amount=body.amount,
            merchant=body.merchant_code,
        )
    return CardAuthorizeResponse(
        approved=True,
        auth_code="AUTH" + secrets.token_hex(4).upper(),
        amount=body.amount,
        merchant=MERCHANTS[body.merchant_code]["name"],
    )


# ── GET /mock/merchants/subscriptions ────────────────────────────────────────
@router.get("/subscriptions")
def get_subscriptions(card_hash: str, db: Session = Depends(get_db)):
    subs = db.query(MockMerchantSubscription).filter_by(card_hash=card_hash, is_active=True).all()
    return {
        "subscriptions": [
            {
                "merchant":      MERCHANTS.get(s.merchant_code, {}).get("name", s.merchant_code),
                "amount":        s.amount,
                "billing_cycle": s.billing_cycle,
                "next_charge":   str(s.next_charge_at),
                "last_four":     s.last_four,
            }
            for s in subs
        ]
    }


# ── DELETE /mock/merchants/unsubscribe ────────────────────────────────────────
@router.delete("/unsubscribe")
def unsubscribe(merchant_code: str, card_hash: str, db: Session = Depends(get_db)):
    sub = db.query(MockMerchantSubscription).filter_by(
        merchant_code=merchant_code, card_hash=card_hash, is_active=True
    ).first()
    if not sub:
        raise HTTPException(404, "Subscription not found")
    sub.is_active = False
    db.commit()
    return {"success": True, "message": f"Unsubscribed from {MERCHANTS.get(merchant_code, {}).get('name', merchant_code)}"}
