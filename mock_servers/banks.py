"""Mock bank IBFT server + Raast instant payment."""
import secrets
import random
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

from mock_servers.db import get_db
from mock_servers.models import MockBankAccount

router = APIRouter()

BANKS = {
    "hbl":        "Habib Bank Limited",
    "mcb":        "MCB Bank",
    "ubl":        "United Bank Limited",
    "meezan":     "Meezan Bank",
    "allied":     "Allied Bank",
    "alfalah":    "Bank Alfalah",
    "faysal":     "Faysal Bank",
    "habibmetro": "Habib Metropolitan Bank",
    "js":         "JS Bank",
    "scb":        "Standard Chartered",
    "silk":       "Silk Bank",
    "askari":     "Askari Bank",
    "soneri":     "Soneri Bank",
    "bahl":       "Bank Al-Habib",
}


def _ref() -> str:
    return "IBFT" + secrets.token_hex(5).upper()


class BankLookupRequest(BaseModel):
    bank_code: str
    account_number: str


class IBFTSendRequest(BaseModel):
    bank_code: str
    account_number: str
    account_title: str
    amount: float
    description: Optional[str] = None


class RaastSendRequest(BaseModel):
    raast_id: str        # phone number or CNIC used as Raast ID
    amount: float
    description: Optional[str] = None


# ── GET /mock/banks/list ──────────────────────────────────────────────────────
@router.get("/list")
def list_banks():
    return {
        "banks": [{"code": k, "name": v} for k, v in BANKS.items()]
    }


# ── POST /mock/banks/lookup ───────────────────────────────────────────────────
@router.post("/lookup")
def lookup_bank_account(body: BankLookupRequest, db: Session = Depends(get_db)):
    if body.bank_code not in BANKS:
        raise HTTPException(400, f"Unknown bank code: {body.bank_code}")
    account = db.query(MockBankAccount).filter_by(
        bank_code=body.bank_code, account_number=body.account_number
    ).first()
    if not account:
        return {"found": False, "bank": BANKS[body.bank_code], "account_number": body.account_number}
    return {
        "found":         True,
        "bank":          BANKS[body.bank_code],
        "account_number": body.account_number,
        "account_title": account.account_title,
        "iban":          account.iban,
    }


# ── POST /mock/banks/ibft ─────────────────────────────────────────────────────
@router.post("/ibft")
def ibft_send(body: IBFTSendRequest, db: Session = Depends(get_db)):
    if body.bank_code not in BANKS:
        raise HTTPException(400, f"Unknown bank code: {body.bank_code}")
    account = db.query(MockBankAccount).filter_by(
        bank_code=body.bank_code, account_number=body.account_number
    ).first()
    if account:
        account.balance += body.amount
        db.commit()
    # Simulate ~10% failure
    if random.random() < 0.10:
        return {
            "success":   False,
            "reference": _ref(),
            "status":    "failed",
            "message":   "Transaction failed — recipient bank temporarily unavailable. Amount will be refunded within 2 hours.",
        }
    return {
        "success":        True,
        "reference":      _ref(),
        "status":         "completed",
        "bank":           BANKS[body.bank_code],
        "account_number": body.account_number,
        "account_title":  body.account_title,
        "amount":         body.amount,
        "message":        f"PKR {body.amount:,.2f} sent to {body.account_title} at {BANKS[body.bank_code]}",
    }


# ── POST /mock/banks/raast ────────────────────────────────────────────────────
@router.post("/raast")
def raast_send(body: RaastSendRequest, db: Session = Depends(get_db)):
    """Raast instant payment by Raast ID (phone or CNIC)."""
    return {
        "success":   True,
        "reference": "RAAST" + secrets.token_hex(5).upper(),
        "status":    "completed",
        "raast_id":  body.raast_id,
        "amount":    body.amount,
        "message":   f"PKR {body.amount:,.2f} sent via Raast to {body.raast_id}",
        "settled_in": "Instant",
    }
