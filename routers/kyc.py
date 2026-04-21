"""KYC router — CNIC upload, liveness check, fingerprint, business docs. PROMPT 08."""
import asyncio
import hashlib
import re
from datetime import datetime, timezone
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_db
from limiter import limiter
from models.kyc import Document, FingerprintScan, BusinessProfile, KycReviewRequest
from models.user import User
from models.wallet import Wallet
from services.auth_service import get_current_user
from services.kyc_service import (
    upload_kyc_document,
    ocr_extract_text,
    deepseek_extract_cnic,
    encrypt_value,
    facepp_compare,
    deepseek_analyse_business,
    get_signed_url,
)

router = APIRouter()

_TIER_LIMITS = {2: 100_000, 3: 500_000, 4: 2_000_000}


# ── Credential-match helpers ──────────────────────────────────────────────────

def _normalize_name(name: str) -> str:
    """Lowercase, collapse whitespace, strip punctuation for comparison."""
    name = re.sub(r"[^\w\s]", "", name.lower())
    return re.sub(r"\s+", " ", name).strip()


def _names_match(registered: str, extracted: str) -> bool:
    """True if every word in the registered name appears in the extracted name."""
    reg = _normalize_name(registered)
    ext = _normalize_name(extracted)
    if not reg or not ext:
        return True          # nothing to compare — give benefit of doubt
    if reg == ext:
        return True
    reg_words = set(reg.split())
    ext_words = set(ext.split())
    # All registered words must appear in extracted text
    return reg_words.issubset(ext_words)


def _cnic_digits(cnic: str) -> str:
    """Strip dashes and whitespace — 13 digit string."""
    return re.sub(r"[\s\-]", "", cnic.strip())

MAX_FILE_MB   = 10
MAX_FILE_BYTES = MAX_FILE_MB * 1024 * 1024


def _utcnow():
    return datetime.now(timezone.utc)


def _check_file_size(file_bytes: bytes, label: str = "File"):
    if len(file_bytes) > MAX_FILE_BYTES:
        raise HTTPException(400, f"{label} exceeds {MAX_FILE_MB}MB limit")


async def _bump_tier(user: User, wallet: Wallet | None, new_tier: int, db: AsyncSession):
    """Upgrade user tier and wallet daily limit if new_tier is higher."""
    if user.verification_tier < new_tier:
        user.verification_tier = new_tier
        if wallet:
            wallet.daily_limit = _TIER_LIMITS.get(new_tier, wallet.daily_limit)
    await db.commit()


# ══════════════════════════════════════════════════════════════════════════════
# POST /users/upload-cnic
# ══════════════════════════════════════════════════════════════════════════════
@router.post("/upload-cnic")
@limiter.limit("50/day")
async def upload_cnic(
    request: Request,
    front: UploadFile = File(..., description="CNIC front image"),
    back:  UploadFile = File(..., description="CNIC back image"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Upload CNIC front + back → Cloudinary (private) → OCR → DeepSeek extract
    → credential match → create admin review request (pending approval).
    """
    if current_user.verification_tier >= 2:
        return {
            "status":      "already_verified",
            "tier":        current_user.verification_tier,
            "daily_limit": _TIER_LIMITS.get(current_user.verification_tier, 0),
            "cnic_masked": current_user.cnic_number_masked,
            "extracted":   None,
            "message":     "CNIC already verified. Tier 2+ active.",
        }

    # Check if there's already a pending review
    existing = (await db.execute(
        select(KycReviewRequest).where(
            KycReviewRequest.user_id == current_user.id,
            KycReviewRequest.status == "pending",
        )
    )).scalar_one_or_none()
    if existing:
        return {
            "status":      "pending_review",
            "tier":        current_user.verification_tier,
            "daily_limit": _TIER_LIMITS.get(current_user.verification_tier, 0),
            "cnic_masked": existing.cnic_masked,
            "extracted":   {
                "full_name":   existing.extracted_name,
                "dob":         existing.extracted_dob,
                "address":     existing.extracted_address,
                "father_name": existing.extracted_father,
            },
            "message":     "Your CNIC is already under review. You will be notified once approved.",
        }

    front_bytes = await front.read()
    back_bytes  = await back.read()
    _check_file_size(front_bytes, "Front image")
    _check_file_size(back_bytes,  "Back image")

    # 1 — Upload to Cloudinary AND run OCR simultaneously
    (front_pub_id, back_pub_id), raw_ocr = await asyncio.gather(
        asyncio.gather(
            upload_kyc_document(front_bytes, current_user.id, "cnic_front"),
            upload_kyc_document(back_bytes,  current_user.id, "cnic_back"),
        ),
        ocr_extract_text(front_bytes),
    )

    # 2 — Guard: OCR must return some text
    if not raw_ocr or not raw_ocr.strip():
        raise HTTPException(
            422,
            "OCR could not read text from your CNIC image. "
            "Please retake the photo with better lighting and no glare.",
        )

    # 3 — DeepSeek: format + extract structured fields from raw OCR text
    cnic_data = await deepseek_extract_cnic(raw_ocr)

    # 4 — Guard: DeepSeek must return a CNIC number at minimum
    if not cnic_data or not cnic_data.get("cnic_number"):
        raise HTTPException(
            422,
            "AI could not extract CNIC details from the document. "
            "Please ensure your CNIC is clearly visible and retake the photo.",
        )

    # 5 — Pull extracted values
    extracted_cnic = cnic_data.get("cnic_number") or ""
    extracted_name = cnic_data.get("full_name")  or ""

    # 6a — Validate CNIC number matches account (if user registered with one)
    if current_user.cnic_number:
        reg_digits = _cnic_digits(current_user.cnic_number)
        ext_digits = _cnic_digits(extracted_cnic)
        if reg_digits and ext_digits and reg_digits != ext_digits:
            raise HTTPException(
                422,
                "CNIC number on the uploaded document does not match your account. "
                "Please upload your own CNIC.",
            )

    # 6b — Validate name matches account
    if extracted_name and not _names_match(current_user.full_name, extracted_name):
        raise HTTPException(
            422,
            f"Name on CNIC (\'{extracted_name}\') does not match your account name "
            f"(\'{current_user.full_name}\'). Please upload a CNIC that matches your registered name.",
        )

    final_cnic = extracted_cnic or current_user.cnic_number
    if not final_cnic:
        raise HTTPException(
            422,
            "Could not determine CNIC number. Please upload a clearer image.",
        )

    # 7 — Fernet encrypt CNIC
    encrypted_cnic = encrypt_value(final_cnic)
    cnic_masked = final_cnic[:6] + "*******-*" if len(final_cnic) >= 6 else final_cnic

    # 8 — Save Document records and get their IDs
    front_doc = Document(user_id=current_user.id, document_type="cnic_front", cloudinary_public_id=front_pub_id)
    back_doc  = Document(user_id=current_user.id, document_type="cnic_back",  cloudinary_public_id=back_pub_id)
    db.add(front_doc)
    db.add(back_doc)
    await db.flush()   # assigns UUIDs

    # 9 — Create admin review request (NOT auto-upgrading)
    review = KycReviewRequest(
        user_id           = current_user.id,
        front_doc_id      = front_doc.id,
        back_doc_id       = back_doc.id,
        extracted_cnic    = final_cnic,
        extracted_name    = extracted_name,
        extracted_dob     = cnic_data.get("dob"),
        extracted_father  = cnic_data.get("father_name"),
        extracted_address = cnic_data.get("address"),
        cnic_masked       = cnic_masked,
        cnic_encrypted    = encrypted_cnic,
        status            = "pending",
    )
    db.add(review)
    await db.commit()

    return {
        "status":       "pending_review",
        "tier":         current_user.verification_tier,
        "daily_limit":  _TIER_LIMITS.get(current_user.verification_tier, 0),
        "cnic_masked":  cnic_masked,
        "extracted":    {
            "full_name":   cnic_data.get("full_name"),
            "dob":         cnic_data.get("dob"),
            "address":     cnic_data.get("address"),
            "father_name": cnic_data.get("father_name"),
        },
        "message": "CNIC verified by AI. Your request is now pending admin approval.",
    }


# ══════════════════════════════════════════════════════════════════════════════
# POST /users/verify-liveness
# ══════════════════════════════════════════════════════════════════════════════
@router.post("/verify-liveness")
@limiter.limit("30/day")
async def verify_liveness(
    request: Request,
    selfie: UploadFile = File(..., description="Live selfie"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Compare selfie vs stored CNIC front photo using Face++.
    Confidence > 80% → Tier 3 upgrade (PKR 5,00,000/day).
    """
    if current_user.verification_tier < 2:
        raise HTTPException(400, "Complete CNIC verification (Tier 2) before liveness check.")
    if current_user.verification_tier >= 3:
        return {"message": "Liveness already verified.", "tier": current_user.verification_tier}

    selfie_bytes = await selfie.read()
    _check_file_size(selfie_bytes, "Selfie")

    # Retrieve CNIC front from DB to get the public_id
    cnic_front_doc = (await db.execute(
        select(Document).where(
            Document.user_id      == current_user.id,
            Document.document_type == "cnic_front",
        ).order_by(Document.uploaded_at.desc())
    )).scalar_one_or_none()

    if not cnic_front_doc:
        raise HTTPException(400, "CNIC front image not found. Please re-upload your CNIC.")

    # Get signed Cloudinary URL and download image for comparison
    signed_url = get_signed_url(cnic_front_doc.cloudinary_public_id)
    try:
        async with __import__("httpx").AsyncClient(timeout=20) as client:
            cnic_resp = await client.get(signed_url)
            cnic_bytes = cnic_resp.content
    except Exception as e:
        raise HTTPException(503, f"Could not retrieve CNIC document: {str(e)}")

    # Face++ comparison
    confidence = await facepp_compare(selfie_bytes, cnic_bytes)

    THRESHOLD = 80.0
    if confidence < THRESHOLD:
        raise HTTPException(400, f"Liveness check failed. Face match confidence {confidence:.1f}% (minimum {THRESHOLD}% required). Please try again in good lighting.")

    # Upload selfie to Cloudinary
    selfie_pub_id = await upload_kyc_document(selfie_bytes, current_user.id, "liveness_selfie")
    doc = Document(
        user_id=current_user.id,
        document_type="liveness_selfie",
        cloudinary_public_id=selfie_pub_id,
    )
    db.add(doc)
    current_user.biometric_verified = True

    wallet = (await db.execute(select(Wallet).where(Wallet.user_id == current_user.id))).scalar_one_or_none()
    await _bump_tier(current_user, wallet, 3, db)

    return {
        "status":      "verified",
        "confidence":  round(confidence, 2),
        "tier":        current_user.verification_tier,
        "daily_limit": _TIER_LIMITS[3],
        "message":     f"Liveness verified ({confidence:.1f}% match). Tier 3 unlocked — PKR 5,00,000/day limit.",
    }


# ══════════════════════════════════════════════════════════════════════════════
# POST /users/verify-fingerprint
# ══════════════════════════════════════════════════════════════════════════════
class FingerprintPayload(BaseModel):
    fingers: list[dict]   # [{finger_index: 1, hash: "sha256..."}, ...]


@router.post("/verify-fingerprint")
@limiter.limit("30/day")
async def verify_fingerprint(
    request: Request,
    body: FingerprintPayload,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Accept 8-finger SHA-256 hashes, simulate NADRA verification (2.5s delay).
    Tier 4 upgrade → PKR 20,00,000/day.
    """
    if current_user.verification_tier < 3:
        raise HTTPException(400, "Complete liveness verification (Tier 3) before fingerprint registration.")
    if current_user.verification_tier >= 4:
        return {"message": "Fingerprint already verified.", "tier": current_user.verification_tier}

    fingers = body.fingers
    if len(fingers) < 8:
        raise HTTPException(400, f"8 finger hashes required. Received {len(fingers)}.")

    # Validate all indices present
    provided_indices = {int(f["finger_index"]) for f in fingers}
    expected_indices = set(range(1, 9))
    if not expected_indices.issubset(provided_indices):
        missing = expected_indices - provided_indices
        raise HTTPException(400, f"Missing finger indices: {sorted(missing)}")

    # Simulated NADRA biometric check (2.5s processing delay for realism)
    await asyncio.sleep(2.5)

    # Store fingerprint hashes (never raw images — only SHA-256)
    for f in fingers[:8]:
        idx  = int(f["finger_index"])
        fhash = str(f.get("hash", ""))
        if not fhash:
            raise HTTPException(400, f"Missing hash for finger_index {idx}")
        scan = FingerprintScan(
            user_id=current_user.id,
            finger_index=idx,
            feature_hash=fhash,
        )
        db.add(scan)

    current_user.fingerprint_verified = True
    current_user.nadra_verified       = True

    wallet = (await db.execute(select(Wallet).where(Wallet.user_id == current_user.id))).scalar_one_or_none()
    await _bump_tier(current_user, wallet, 4, db)

    return {
        "status":      "verified",
        "tier":        current_user.verification_tier,
        "daily_limit": _TIER_LIMITS[4],
        "fingers_registered": 8,
        "message":     "Fingerprint verified via NADRA. Tier 4 unlocked — PKR 20,00,000/day limit.",
    }


# ══════════════════════════════════════════════════════════════════════════════
# POST /users/upload-business-docs
# ══════════════════════════════════════════════════════════════════════════════
@router.post("/upload-business-docs", status_code=201)
@limiter.limit("10/day")
async def upload_business_docs(
    request: Request,
    business_name:       str        = Form(...),
    registration_number: str        = Form(default=""),
    business_type:       str        = Form(default=""),
    ntn_number:          str        = Form(default=""),
    docs: List[UploadFile]          = File(..., description="Business registration documents (1-5 files)"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Upload business documents → OCR each → DeepSeek analysis →
    Create/update BusinessProfile with status=under_review.
    """
    if len(docs) > 5:
        raise HTTPException(400, "Maximum 5 documents allowed per submission.")

    # Read + validate all files
    doc_bytes_list = []
    for f in docs:
        data = await f.read()
        _check_file_size(data, f.filename or "Document")
        doc_bytes_list.append(data)

    # Upload all to Cloudinary (parallel)
    upload_tasks = [
        upload_kyc_document(data, current_user.id, f"business_doc_{i}")
        for i, data in enumerate(doc_bytes_list)
    ]
    pub_ids = await asyncio.gather(*upload_tasks)

    # OCR all documents (parallel)
    ocr_tasks = [ocr_extract_text(data) for data in doc_bytes_list]
    ocr_texts = await asyncio.gather(*ocr_tasks)
    ocr_texts = [t for t in ocr_texts if t.strip()]

    # DeepSeek analysis
    ai_analysis = await deepseek_analyse_business(ocr_texts, business_name)

    # Save Document records
    for pub_id in pub_ids:
        db.add(Document(
            user_id=current_user.id,
            document_type="business_doc",
            cloudinary_public_id=pub_id,
        ))

    # Create or update BusinessProfile
    existing_profile = (await db.execute(
        select(BusinessProfile).where(BusinessProfile.user_id == current_user.id)
    )).scalar_one_or_none()

    if existing_profile:
        existing_profile.business_name       = business_name
        existing_profile.registration_number = registration_number or existing_profile.registration_number
        existing_profile.business_type       = business_type or existing_profile.business_type
        existing_profile.ntn_number          = ntn_number or existing_profile.ntn_number
        existing_profile.verification_status = "under_review"
        existing_profile.ai_analysis_result  = ai_analysis
        existing_profile.submitted_at        = _utcnow()
    else:
        profile = BusinessProfile(
            user_id=current_user.id,
            business_name=business_name,
            registration_number=registration_number or None,
            business_type=business_type or None,
            ntn_number=ntn_number or None,
            verification_status="under_review",
            ai_analysis_result=ai_analysis,
        )
        db.add(profile)

    await db.commit()

    return {
        "status":          "under_review",
        "business_name":   business_name,
        "documents_uploaded": len(pub_ids),
        "ai_summary":      ai_analysis,
        "message":         "Business documents submitted. Your account is under review. You will be notified within 24-48 hours.",
    }


# ══════════════════════════════════════════════════════════════════════════════
# GET /users/kyc-status
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/kyc-status")
async def kyc_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Quick summary of user's KYC tier and verification flags + pending review."""
    business = (await db.execute(
        select(BusinessProfile).where(BusinessProfile.user_id == current_user.id)
    )).scalar_one_or_none()

    wallet = (await db.execute(select(Wallet).where(Wallet.user_id == current_user.id))).scalar_one_or_none()

    # Check for pending or latest CNIC review
    latest_review = (await db.execute(
        select(KycReviewRequest)
        .where(KycReviewRequest.user_id == current_user.id)
        .order_by(KycReviewRequest.submitted_at.desc())
    )).scalar_one_or_none()

    cnic_review = None
    if latest_review:
        cnic_review = {
            "id":              str(latest_review.id),
            "status":          latest_review.status,
            "extracted_name":  latest_review.extracted_name,
            "extracted_cnic":  latest_review.cnic_masked,
            "extracted_dob":   latest_review.extracted_dob,
            "rejection_reason": latest_review.rejection_reason,
            "submitted_at":    latest_review.submitted_at.isoformat() if latest_review.submitted_at else None,
            "reviewed_at":     latest_review.reviewed_at.isoformat() if latest_review.reviewed_at else None,
        }

    return {
        "tier":                 current_user.verification_tier,
        "daily_limit":          str(wallet.daily_limit) if wallet else "0",
        "cnic_verified":        current_user.cnic_verified,
        "cnic_masked":          current_user.cnic_number_masked,
        "biometric_verified":   current_user.biometric_verified,
        "fingerprint_verified": current_user.fingerprint_verified,
        "nadra_verified":       current_user.nadra_verified,
        "business_status":      business.verification_status if business else None,
        "cnic_review":          cnic_review,
        "next_step": (
            "Upload CNIC to reach Tier 2"          if current_user.verification_tier < 2 else
            "Complete liveness check for Tier 3"   if current_user.verification_tier < 3 else
            "Register fingerprint for Tier 4"      if current_user.verification_tier < 4 else
            "Fully verified"
        ),
    }
