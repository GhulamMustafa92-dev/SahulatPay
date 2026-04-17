"""Mobile top-up mock — stateless, simulates Jazz/Telenor/Zong/Ufone/SCO APIs."""
import secrets
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

router = APIRouter()

TOPUP_PACKAGES = {
    "jazz":    [100, 200, 300, 500, 1000, 1500, 2000],
    "telenor": [100, 200, 300, 500, 1000, 1500, 2000],
    "zong":    [100, 200, 300, 500, 1000, 1500],
    "ufone":   [100, 200, 500, 1000, 1500],
    "sco":     [50,  100, 200, 500],
}

BONUS_DATA = {
    "jazz":    {"100": "300MB", "200": "1GB",  "300": "2GB",  "500": "5GB",  "1000": "12GB", "1500": "20GB"},
    "telenor": {"100": "300MB", "200": "1GB",  "300": "2GB",  "500": "5GB",  "1000": "12GB", "1500": "20GB"},
    "zong":    {"100": "250MB", "200": "1GB",  "300": "2GB",  "500": "4GB",  "1000": "10GB"},
    "ufone":   {"100": "200MB", "200": "750MB","500": "3GB",  "1000": "8GB",  "1500": "15GB"},
    "sco":     {"50":  "100MB", "100": "300MB","200": "750MB","500": "2GB"},
}


def detect_network(phone: str) -> str:
    """Auto-detect network from Pakistani phone prefix."""
    if phone.startswith("+92"):
        phone = "0" + phone[3:]
    try:
        prefix = int(phone[1:4])
    except (ValueError, IndexError):
        return "unknown"
    if 300 <= prefix <= 319:
        return "jazz"
    if 320 <= prefix <= 329:
        return "zong"
    if 330 <= prefix <= 339:
        return "ufone"
    if 340 <= prefix <= 349:
        return "telenor"
    if prefix == 855:
        return "sco"
    return "unknown"


class TopupRequest(BaseModel):
    phone: str
    amount: float
    network: Optional[str] = None


# ── GET /mock/topup/networks ──────────────────────────────────────────────────
@router.get("/networks")
def list_networks():
    return {
        "networks": [
            {"code": "jazz",    "name": "Jazz / Warid", "prefixes": "0300-0319"},
            {"code": "zong",    "name": "Zong",         "prefixes": "0320-0329"},
            {"code": "ufone",   "name": "Ufone",        "prefixes": "0330-0339"},
            {"code": "telenor", "name": "Telenor",      "prefixes": "0340-0349"},
            {"code": "sco",     "name": "SCO",          "prefixes": "0855"},
        ],
        "packages": TOPUP_PACKAGES,
    }


# ── POST /mock/topup/send ─────────────────────────────────────────────────────
@router.post("/send")
def send_topup(body: TopupRequest):
    network = body.network or detect_network(body.phone)
    if network == "unknown":
        raise HTTPException(400, f"Could not detect network for {body.phone}. Please specify network manually.")
    if network not in TOPUP_PACKAGES:
        raise HTTPException(400, f"Unsupported network: {network}")
    bonus = BONUS_DATA.get(network, {}).get(str(int(body.amount)), None)
    return {
        "success":   True,
        "reference": "TPUP" + secrets.token_hex(5).upper(),
        "phone":     body.phone,
        "network":   network.capitalize(),
        "amount":    body.amount,
        "bonus_data": bonus,
        "status":    "credited",
        "message":   f"PKR {body.amount:,.0f} top-up sent to {body.phone} ({network.capitalize()})"
                     + (f". Bonus: {bonus} data" if bonus else ""),
    }


# ── GET /mock/topup/detect ────────────────────────────────────────────────────
@router.get("/detect")
def detect(phone: str):
    network = detect_network(phone)
    return {
        "phone":   phone,
        "network": network if network != "unknown" else None,
        "name":    {"jazz": "Jazz/Warid", "zong": "Zong", "ufone": "Ufone", "telenor": "Telenor", "sco": "SCO"}.get(network),
    }
