"""Mock external wallet servers: JazzCash, EasyPaisa, SadaPay, NayaPay, UPaisa."""
import secrets
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

from mock_servers.db import get_db
from mock_servers.models import MockWalletAccount

router = APIRouter()

PROVIDERS = {
    "jazzcash":  "JazzCash",
    "easypaisa": "EasyPaisa",
    "sadapay":   "SadaPay",
    "nayapay":   "NayaPay",
    "upaisa":    "UPaisa",
}


def _mock_ref() -> str:
    return "MW" + secrets.token_hex(6).upper()


def _mask_name(name: str) -> str:
    parts = name.strip().split()
    if len(parts) == 1:
        return parts[0][:2] + "****"
    return parts[0] + " " + parts[1][0] + "****"


def _normalize_phone(phone: str) -> str:
    """Normalize Pakistani phone to +92XXXXXXXXXX format.
    Accepts: 03001234567  →  +923001234567
             923001234567 →  +923001234567
             +923001234567 → +923001234567 (unchanged)
    """
    p = phone.strip().replace(" ", "").replace("-", "")
    if p.startswith("+92"):
        return p
    if p.startswith("92") and len(p) >= 12:
        return "+" + p
    if p.startswith("0") and len(p) == 11:
        return "+92" + p[1:]
    return p  # return as-is if unrecognised


class WalletLookupRequest(BaseModel):
    provider: str
    phone: str


class WalletSendRequest(BaseModel):
    provider: str
    phone: str
    amount: float
    description: Optional[str] = None


# ── GET /mock/wallets/lookup ──────────────────────────────────────────────────
@router.get("/lookup")
def lookup_wallet(provider: str, phone: str, db: Session = Depends(get_db)):
    if provider not in PROVIDERS:
        raise HTTPException(400, f"Unknown provider: {provider}. Valid: {list(PROVIDERS)}")
    phone = _normalize_phone(phone)
    account = db.query(MockWalletAccount).filter_by(provider=provider, phone=phone).first()
    if not account or not account.is_active:
        return {"found": False, "provider": PROVIDERS[provider], "phone": phone}
    return {
        "found":       True,
        "provider":    PROVIDERS[provider],
        "phone":       phone,
        "name":        account.name,
        "masked_name": _mask_name(account.name),
        "is_active":   account.is_active,
    }


# ── POST /mock/wallets/send ───────────────────────────────────────────────────
@router.post("/send")
def send_to_wallet(body: WalletSendRequest, db: Session = Depends(get_db)):
    if body.provider not in PROVIDERS:
        raise HTTPException(400, f"Unknown provider: {body.provider}")
    body.phone = _normalize_phone(body.phone)
    account = db.query(MockWalletAccount).filter_by(provider=body.provider, phone=body.phone).first()
    if not account:
        account = MockWalletAccount(
            provider=body.provider, phone=body.phone,
            name="Unknown Account", balance=0,
        )
        db.add(account)
    account.balance += body.amount
    db.flush()   # stage the change; caller commits
    ref = _mock_ref()
    return {
        "success":      True,
        "provider":     PROVIDERS[body.provider],
        "reference":    ref,
        "phone":        body.phone,
        "amount":       body.amount,
        "message":      f"PKR {body.amount:,.2f} sent to {PROVIDERS[body.provider]} {body.phone}",
    }


# ── GET /mock/wallets/providers ───────────────────────────────────────────────
@router.get("/providers")
def list_providers():
    return {
        "providers": [
            {"code": code, "name": name, "logo": f"{code}.png"}
            for code, name in PROVIDERS.items()
        ]
    }


# ── GET /mock/wallets/balance ─────────────────────────────────────────────────
@router.get("/balance")
def get_wallet_balance(provider: str, phone: str, db: Session = Depends(get_db)):
    phone = _normalize_phone(phone)
    account = db.query(MockWalletAccount).filter_by(provider=provider, phone=phone).first()
    if not account:
        raise HTTPException(404, "Account not found in mock database")
    return {
        "provider": PROVIDERS.get(provider, provider),
        "phone":    phone,
        "balance":  account.balance,
    }
