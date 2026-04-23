"""Mock utility bills + government challan servers."""
import secrets
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

from mock_servers.db import get_db
from mock_servers.models import MockBill, MockChallan

router = APIRouter()

UTILITY_COMPANIES = {
    "ssgc":       "Sui Southern Gas Company",
    "sngpl":      "Sui Northern Gas Pipelines",
    "kelectric":  "K-Electric",
    "lesco":      "Lahore Electric Supply Company",
    "iesco":      "Islamabad Electric Supply Company",
    "fesco":      "Faisalabad Electric Supply Company",
    "mepco":      "Multan Electric Power Company",
    "pesco":      "Peshawar Electric Supply Company",
    "hesco":      "Hyderabad Electric Supply Company",
    "wapda":      "WAPDA",
    "ptcl":       "PTCL",
    "stormfiber": "StormFiber Internet",
    "nayatel":    "Nayatel",
    "telenor_bb": "Telenor Broadband",
    "jazz_bb":    "Jazz Home Internet",
    "sui_gas":    "Sui Gas (General)",
    "water":      "Water & Sanitation Agency",
}

CHALLAN_DEPTS = {
    "FBR":       "Federal Board of Revenue",
    "Traffic":   "Traffic Police",
    "PSID":      "Punjab Government PSID",
    "Passport":  "Directorate General of Immigration",
    "NADRA":     "NADRA",
    "Municipal": "Municipal Corporation",
    "BISP":      "Benazir Income Support Programme",
    "SRB":       "Sindh Revenue Board",
    "PRA":       "Punjab Revenue Authority",
    "Motor":     "Motor Vehicle Registration",
}


class BillFetchRequest(BaseModel):
    company: str
    consumer_id: str


class BillPayRequest(BaseModel):
    company: str
    consumer_id: str
    amount: float


class ChallanFetchRequest(BaseModel):
    psid: str


class ChallanPayRequest(BaseModel):
    psid: str
    amount: float


# ── GET /mock/bills/companies ─────────────────────────────────────────────────
@router.get("/companies")
def list_companies():
    return {
        "utility": [{"code": k, "name": v} for k, v in UTILITY_COMPANIES.items()],
        "government": [{"code": k, "name": v} for k, v in CHALLAN_DEPTS.items()],
    }


# ── POST /mock/bills/fetch ────────────────────────────────────────────────────
@router.post("/fetch")
def fetch_bill(body: BillFetchRequest, db: Session = Depends(get_db)):
    if body.company not in UTILITY_COMPANIES:
        raise HTTPException(400, f"Unknown company: {body.company}")
    bill = db.query(MockBill).filter_by(company=body.company, consumer_id=body.consumer_id).first()
    if not bill:
        raise HTTPException(404, f"No bill found for consumer ID {body.consumer_id} at {UTILITY_COMPANIES[body.company]}")
    if bill.is_paid:
        raise HTTPException(400, f"This bill for {UTILITY_COMPANIES[body.company]} has already been paid.")
    return {
        "found":         True,
        "company":       UTILITY_COMPANIES[body.company],
        "consumer_id":   body.consumer_id,
        "customer_name": bill.customer_name,
        "amount_due":    bill.amount_due,
        "due_date":      bill.due_date,
        "bill_month":    bill.bill_month,
        "is_paid":       bill.is_paid,
    }


# ── POST /mock/bills/pay ──────────────────────────────────────────────────────
@router.post("/pay")
def pay_bill(body: BillPayRequest, db: Session = Depends(get_db)):
    if body.company not in UTILITY_COMPANIES:
        raise HTTPException(400, f"Unknown company: {body.company}")
    bill = db.query(MockBill).filter_by(company=body.company, consumer_id=body.consumer_id, is_paid=False).first()
    if bill:
        bill.is_paid = True
        db.flush()
    return {
        "success":     True,
        "reference":   "BILL" + secrets.token_hex(5).upper(),
        "company":     UTILITY_COMPANIES[body.company],
        "consumer_id": body.consumer_id,
        "amount":      body.amount,
        "status":      "paid",
        "message":     f"Bill paid successfully for {UTILITY_COMPANIES[body.company]}",
    }


# ── POST /mock/challan/fetch ──────────────────────────────────────────────────
@router.post("/challan/fetch")
def fetch_challan(body: ChallanFetchRequest, db: Session = Depends(get_db)):
    challan = db.query(MockChallan).filter_by(psid=body.psid).first()
    if not challan:
        raise HTTPException(404, f"No challan found for PSID {body.psid}")
    if challan.is_paid:
        raise HTTPException(400, "This Government challan has already been paid.")
    return {
        "found":       True,
        "psid":        body.psid,
        "department":  challan.department,
        "reference":   challan.reference,
        "description": challan.description,
        "amount":      challan.amount,
        "due_date":    challan.due_date,
        "is_paid":     challan.is_paid,
    }


# ── POST /mock/challan/pay ────────────────────────────────────────────────────
@router.post("/challan/pay")
def pay_challan(body: ChallanPayRequest, db: Session = Depends(get_db)):
    challan = db.query(MockChallan).filter_by(psid=body.psid).first()
    if challan:
        challan.is_paid = True
        db.flush()
    return {
        "success":   True,
        "reference": "CHAL" + secrets.token_hex(5).upper(),
        "psid":      body.psid,
        "amount":    body.amount,
        "status":    "paid",
        "message":   "Government challan paid successfully",
    }
