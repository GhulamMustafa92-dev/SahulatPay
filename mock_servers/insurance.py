"""Mock insurance server: Jubilee, State Life, EFU, Adamjee, TPL."""
import secrets
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

from mock_servers.db import get_db
from mock_servers.models import MockInsurancePolicy

router = APIRouter()

PROVIDERS = ["Jubilee Life", "State Life", "EFU Health", "Adamjee", "TPL Insurance", "Jubilee General"]
POLICY_TYPES = {
    "life":    {"name": "Life Insurance",    "min_premium": 1000,  "min_coverage": 500000},
    "health":  {"name": "Health Insurance",  "min_premium": 2000,  "min_coverage": 500000},
    "vehicle": {"name": "Vehicle Insurance", "min_premium": 3000,  "min_coverage": 200000},
    "travel":  {"name": "Travel Insurance",  "min_premium": 500,   "min_coverage": 100000},
    "home":    {"name": "Home Insurance",    "min_premium": 1000,  "min_coverage": 500000},
}


class PolicyLookupRequest(BaseModel):
    policy_number: str


class PremiumPayRequest(BaseModel):
    policy_number: str
    amount: float


class NewPolicyRequest(BaseModel):
    policy_type: str
    provider: str
    coverage_amount: float
    customer_name: str


# ── GET /mock/insurance/types ─────────────────────────────────────────────────
@router.get("/types")
def list_types():
    return {
        "types":     [{"code": k, "name": v["name"]} for k, v in POLICY_TYPES.items()],
        "providers": PROVIDERS,
    }


# ── POST /mock/insurance/lookup ───────────────────────────────────────────────
@router.post("/lookup")
def lookup_policy(body: PolicyLookupRequest, db: Session = Depends(get_db)):
    policy = db.query(MockInsurancePolicy).filter_by(policy_number=body.policy_number).first()
    if not policy:
        return {"found": False, "policy_number": body.policy_number}
    return {
        "found":           True,
        "policy_number":   policy.policy_number,
        "policy_type":     policy.policy_type,
        "provider":        policy.provider,
        "customer_name":   policy.customer_name,
        "premium_amount":  policy.premium_amount,
        "coverage_amount": policy.coverage_amount,
        "next_due_date":   policy.next_due_date,
        "is_active":       policy.is_active,
    }


# ── POST /mock/insurance/pay-premium ─────────────────────────────────────────
@router.post("/pay-premium")
def pay_premium(body: PremiumPayRequest, db: Session = Depends(get_db)):
    policy = db.query(MockInsurancePolicy).filter_by(policy_number=body.policy_number).first()
    if not policy:
        raise HTTPException(404, f"Policy {body.policy_number} not found")
    if not policy.is_active:
        raise HTTPException(400, "Policy is no longer active")
    return {
        "success":        True,
        "reference":      "INS" + secrets.token_hex(5).upper(),
        "policy_number":  body.policy_number,
        "provider":       policy.provider,
        "amount":         body.amount,
        "status":         "paid",
        "message":        f"Premium of PKR {body.amount:,.2f} paid for policy {body.policy_number}",
    }


# ── POST /mock/insurance/new-policy ──────────────────────────────────────────
@router.post("/new-policy")
def new_policy(body: NewPolicyRequest, db: Session = Depends(get_db)):
    if body.policy_type not in POLICY_TYPES:
        raise HTTPException(400, f"Invalid policy type. Choose: {list(POLICY_TYPES)}")
    if body.provider not in PROVIDERS:
        raise HTTPException(400, f"Unknown provider. Choose: {PROVIDERS}")
    ptype = POLICY_TYPES[body.policy_type]
    premium = max(ptype["min_premium"], body.coverage_amount * 0.002)
    policy_number = body.provider[:3].upper().replace(" ", "") + "-" + secrets.token_hex(4).upper()
    return {
        "success":        True,
        "policy_number":  policy_number,
        "policy_type":    ptype["name"],
        "provider":       body.provider,
        "customer_name":  body.customer_name,
        "coverage_amount": body.coverage_amount,
        "premium_amount": round(premium, 2),
        "message":        f"{ptype['name']} policy created with {body.provider}. Premium: PKR {premium:,.2f}/month",
    }
