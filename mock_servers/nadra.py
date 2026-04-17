"""Mock NADRA verification server — CNIC lookup, biometric simulation."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from mock_servers.db import get_db
from mock_servers.models import MockCNIC

router = APIRouter()


class CNICVerifyRequest(BaseModel):
    cnic: str
    full_name: Optional[str] = None


class BiometricRequest(BaseModel):
    cnic: str
    biometric_data: str = "mock_fingerprint_hash"


from typing import Optional


# ── POST /mock/nadra/verify ───────────────────────────────────────────────────
@router.post("/verify")
def verify_cnic(body: CNICVerifyRequest, db: Session = Depends(get_db)):
    clean = body.cnic.replace("-", "").replace(" ", "")
    record = db.query(MockCNIC).filter(
        MockCNIC.cnic.in_([body.cnic, clean])
    ).first()
    if not record:
        return {
            "verified":  False,
            "cnic":      body.cnic,
            "message":   "CNIC not found in NADRA database",
        }
    if record.status == "blocked":
        return {"verified": False, "cnic": body.cnic, "message": "CNIC is blocked"}
    if record.status == "expired":
        return {"verified": False, "cnic": body.cnic, "message": "CNIC is expired. Please renew."}
    if body.full_name:
        name_match = body.full_name.strip().upper() in record.full_name.upper()
        if not name_match:
            return {"verified": False, "cnic": body.cnic, "message": "Name does not match NADRA records"}
    return {
        "verified":    True,
        "cnic":        record.cnic,
        "full_name":   record.full_name,
        "father_name": record.father_name,
        "dob":         record.dob,
        "address":     record.address,
        "status":      record.status,
        "tier_upgrade": "tier2",
    }


# ── POST /mock/nadra/biometric ────────────────────────────────────────────────
@router.post("/biometric")
def verify_biometric(body: BiometricRequest, db: Session = Depends(get_db)):
    """Simulate NADRA biometric (fingerprint) verification."""
    record = db.query(MockCNIC).filter_by(cnic=body.cnic).first()
    if not record:
        return {"verified": False, "message": "CNIC not found"}
    return {
        "verified":     True,
        "cnic":         record.cnic,
        "full_name":    record.full_name,
        "tier_upgrade": "tier3",
        "message":      "Biometric verification successful — Tier 3 KYC approved",
    }


# ── GET /mock/nadra/status ────────────────────────────────────────────────────
@router.get("/status")
def cnic_status(cnic: str, db: Session = Depends(get_db)):
    record = db.query(MockCNIC).filter_by(cnic=cnic).first()
    if not record:
        return {"found": False, "cnic": cnic}
    return {"found": True, "cnic": record.cnic, "status": record.status}
