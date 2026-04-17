"""Mock QR payment server — generate, decode, and pay via QR."""
import io
import json
import secrets
import base64
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session
from pydantic import BaseModel

from mock_servers.db import get_db
from mock_servers.models import MockQRCode

router = APIRouter()


def _utcnow():
    return datetime.now(timezone.utc)


class QRGenerateRequest(BaseModel):
    phone: str
    amount: Optional[float] = None
    description: Optional[str] = None
    expires_minutes: int = 30


class QRDecodeRequest(BaseModel):
    qr_id: str


class QRPayRequest(BaseModel):
    qr_id: str
    amount: Optional[float] = None


# ── POST /mock/qr/generate ────────────────────────────────────────────────────
@router.post("/generate")
def generate_qr(body: QRGenerateRequest, db: Session = Depends(get_db)):
    """Generate a QR code containing payment info. Returns base64 PNG + QR ID."""
    qr_id = "QR" + secrets.token_hex(8).upper()
    expires = _utcnow() + timedelta(minutes=body.expires_minutes)

    payload = {
        "qr_id":       qr_id,
        "phone":       body.phone,
        "amount":      body.amount,
        "description": body.description,
        "app":         "SahulatPay",
    }

    record = MockQRCode(
        qr_id=qr_id,
        phone=body.phone,
        amount=body.amount,
        description=body.description,
        expires_at=expires,
    )
    db.add(record)
    db.commit()

    try:
        import qrcode
        qr = qrcode.QRCode(version=1, box_size=10, border=4)
        qr.add_data(json.dumps(payload))
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        img_b64 = base64.b64encode(buf.getvalue()).decode()
    except Exception:
        img_b64 = None

    return {
        "qr_id":      qr_id,
        "phone":      body.phone,
        "amount":     body.amount,
        "payload":    json.dumps(payload),
        "qr_image":   img_b64,
        "expires_at": expires.isoformat(),
        "message":    "QR code generated. Valid for " + str(body.expires_minutes) + " minutes.",
    }


# ── POST /mock/qr/decode ──────────────────────────────────────────────────────
@router.post("/decode")
def decode_qr(body: QRDecodeRequest, db: Session = Depends(get_db)):
    """Decode a QR ID and return payment info."""
    record = db.query(MockQRCode).filter_by(qr_id=body.qr_id).first()
    if not record:
        raise HTTPException(404, "QR code not found")
    if record.is_used:
        raise HTTPException(400, "QR code has already been used")
    if record.expires_at and record.expires_at.replace(tzinfo=timezone.utc) < _utcnow():
        raise HTTPException(400, "QR code has expired")
    return {
        "valid":       True,
        "qr_id":       record.qr_id,
        "phone":       record.phone,
        "amount":      record.amount,
        "description": record.description,
        "expires_at":  record.expires_at.isoformat() if record.expires_at else None,
    }


# ── POST /mock/qr/mark-used ───────────────────────────────────────────────────
@router.post("/mark-used")
def mark_qr_used(qr_id: str, db: Session = Depends(get_db)):
    """Mark QR as used after successful payment."""
    record = db.query(MockQRCode).filter_by(qr_id=qr_id).first()
    if record:
        record.is_used = True
        db.commit()
    return {"success": True, "qr_id": qr_id, "message": "QR code marked as used"}
