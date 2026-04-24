"""KYC router — CNIC upload, liveness check, fingerprint verification."""

import asyncio
import re
from datetime import datetime, timezone

from database import get_db
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from limiter import limiter
from models.kyc import Document, FingerprintScan, KycReviewRequest
from models.user import User
from models.wallet import Wallet
from pydantic import BaseModel
from services.auth_service import get_current_user
from services.kyc_service import (
    deepseek_extract_cnic,
    encrypt_value,
    facepp_compare,
    get_signed_url,
    ocr_extract_text,
    upload_kyc_document,
)
from services.notification_service import send_notification
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()

_TIER_LIMITS = {2: 100_000, 3: 500_000, 4: 2_000_000}


# â”€â”€ Credential-match helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def _normalize_name(name: str) -> str:
    """Lowercase, collapse whitespace, strip punctuation for comparison."""
    name = re.sub(r"[^\w\s]", "", name.lower())
    return re.sub(r"\s+", " ", name).strip()


def _names_match(registered: str, extracted: str) -> bool:
    """True if every word in the registered name appears in the extracted name."""
    reg = _normalize_name(registered)
    ext = _normalize_name(extracted)
    if not reg or not ext:
        return True  # nothing to compare â€” give benefit of doubt
    if reg == ext:
        return True
    reg_words = set(reg.split())
    ext_words = set(ext.split())
    # All registered words must appear in extracted text
    return reg_words.issubset(ext_words)


def _cnic_digits(cnic: str) -> str:
    """Strip dashes and whitespace â€” 13 digit string."""
    return re.sub(r"[\s\-]", "", cnic.strip())


MAX_FILE_MB = 10
MAX_FILE_BYTES = MAX_FILE_MB * 1024 * 1024


def _utcnow():
    return datetime.now(timezone.utc)


def _check_file_size(file_bytes: bytes, label: str = "File"):
    if len(file_bytes) > MAX_FILE_BYTES:
        raise HTTPException(400, f"{label} exceeds {MAX_FILE_MB}MB limit")


async def _bump_tier(
    user: User, wallet: Wallet | None, new_tier: int, db: AsyncSession
):
    """Upgrade user tier and wallet daily limit if new_tier is higher."""
    if user.verification_tier < new_tier:
        user.verification_tier = new_tier
        if wallet:
            wallet.daily_limit = _TIER_LIMITS.get(new_tier, wallet.daily_limit)
    await db.commit()


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# POST /users/upload-cnic
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
@router.post("/upload-cnic")
@limiter.limit("50/day")
async def upload_cnic(
    request: Request,
    front: UploadFile = File(..., description="CNIC front image"),
    back: UploadFile = File(..., description="CNIC back image"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Upload CNIC front + back â†’ Cloudinary (private) â†’ OCR â†’ DeepSeek extract
    â†’ credential match â†’ create admin review request (pending approval).
    """
    if current_user.verification_tier >= 2:
        return {
            "status": "already_verified",
            "tier": current_user.verification_tier,
            "daily_limit": _TIER_LIMITS.get(current_user.verification_tier, 0),
            "cnic_masked": current_user.cnic_number_masked,
            "extracted": None,
            "message": "CNIC already verified. Tier 2+ active.",
        }

    # Check if there's already a pending review
    existing = (
        (
            await db.execute(
                select(KycReviewRequest)
                .where(
                    KycReviewRequest.user_id == current_user.id,
                    KycReviewRequest.status == "pending",
                )
                .order_by(KycReviewRequest.submitted_at.desc())
            )
        )
        .scalars()
        .first()
    )
    if existing:
        return {
            "status": "pending_review",
            "tier": current_user.verification_tier,
            "daily_limit": _TIER_LIMITS.get(current_user.verification_tier, 0),
            "cnic_masked": existing.cnic_masked,
            "extracted": {
                "full_name": existing.extracted_name,
                "dob": existing.extracted_dob,
                "address": existing.extracted_address,
                "father_name": existing.extracted_father,
            },
            "message": "Your CNIC is already under review. You will be notified once approved.",
        }

    front_bytes = await front.read()
    back_bytes = await back.read()
    _check_file_size(front_bytes, "Front image")
    _check_file_size(back_bytes, "Back image")

    # 1 â€” Upload to Cloudinary AND run OCR simultaneously
    (front_pub_id, back_pub_id), raw_ocr = await asyncio.gather(
        asyncio.gather(
            upload_kyc_document(front_bytes, current_user.id, "cnic_front"),
            upload_kyc_document(back_bytes, current_user.id, "cnic_back"),
        ),
        ocr_extract_text(front_bytes),
    )

    # 2 â€” Guard: OCR must return some text
    if not raw_ocr or not raw_ocr.strip():
        raise HTTPException(
            422,
            "OCR could not read text from your CNIC image. "
            "Please retake the photo with better lighting and no glare.",
        )

    # 3 â€” DeepSeek: format + extract structured fields from raw OCR text
    cnic_data = await deepseek_extract_cnic(raw_ocr)

    # 4 â€” Guard: DeepSeek must return a CNIC number at minimum
    if not cnic_data or not cnic_data.get("cnic_number"):
        raise HTTPException(
            422,
            "AI could not extract CNIC details from the document. "
            "Please ensure your CNIC is clearly visible and retake the photo.",
        )

    # 5 â€” Pull extracted values
    extracted_cnic = cnic_data.get("cnic_number") or ""
    extracted_name = cnic_data.get("full_name") or ""

    # 6a â€” Validate CNIC number matches account (if user registered with one)
    if current_user.cnic_number:
        reg_digits = _cnic_digits(current_user.cnic_number)
        ext_digits = _cnic_digits(extracted_cnic)
        if reg_digits and ext_digits and reg_digits != ext_digits:
            raise HTTPException(
                422,
                "CNIC number on the uploaded document does not match your account. "
                "Please upload your own CNIC.",
            )

    # 6b â€” Validate name matches account
    if extracted_name and not _names_match(current_user.full_name, extracted_name):
        raise HTTPException(
            422,
            f"Name on CNIC ('{extracted_name}') does not match your account name "
            f"('{current_user.full_name}'). Please upload a CNIC that matches your registered name.",
        )

    final_cnic = extracted_cnic or current_user.cnic_number
    if not final_cnic:
        raise HTTPException(
            422,
            "Could not determine CNIC number. Please upload a clearer image.",
        )

    # 7 â€” Fernet encrypt CNIC
    encrypted_cnic = encrypt_value(final_cnic)
    cnic_masked = final_cnic[:6] + "*******-*" if len(final_cnic) >= 6 else final_cnic

    # 8 — Save Document records and get their IDs
    front_doc = Document(
        user_id=current_user.id,
        document_type="cnic_front",
        cloudinary_public_id=front_pub_id,
    )
    back_doc = Document(
        user_id=current_user.id,
        document_type="cnic_back",
        cloudinary_public_id=back_pub_id,
    )
    db.add(front_doc)
    db.add(back_doc)
    await db.flush()  # assigns UUIDs

    # 9 — Store CNIC on user record
    current_user.cnic_number = final_cnic
    current_user.cnic_number_masked = cnic_masked
    current_user.cnic_encrypted = encrypted_cnic
    current_user.cnic_verified = True

    # 10 — Create auto-approved review record (audit trail only — no admin needed)
    review = KycReviewRequest(
        user_id=current_user.id,
        front_doc_id=front_doc.id,
        back_doc_id=back_doc.id,
        extracted_cnic=final_cnic,
        extracted_name=extracted_name,
        extracted_dob=cnic_data.get("dob"),
        extracted_father=cnic_data.get("father_name"),
        extracted_address=cnic_data.get("address"),
        cnic_masked=cnic_masked,
        cnic_encrypted=encrypted_cnic,
        status="auto_approved",
        reviewed_at=datetime.now(timezone.utc),
    )
    db.add(review)

    # 11 — Immediately upgrade to Tier 2
    wallet = (
        await db.execute(select(Wallet).where(Wallet.user_id == current_user.id))
    ).scalar_one_or_none()
    await _bump_tier(current_user, wallet, 2, db)

    asyncio.create_task(
        send_notification(
            db,
            current_user.id,
            title="✅ CNIC Verified!",
            body="Your CNIC has been verified by AI. Tier 2 unlocked — daily limit upgraded to PKR 1,00,000.",
            type="system",
        )
    )

    return {
        "status": "approved",
        "tier": current_user.verification_tier,
        "daily_limit": _TIER_LIMITS.get(current_user.verification_tier, 0),
        "cnic_masked": cnic_masked,
        "extracted": {
            "full_name": cnic_data.get("full_name"),
            "dob": cnic_data.get("dob"),
            "address": cnic_data.get("address"),
            "father_name": cnic_data.get("father_name"),
        },
        "message": "CNIC verified by AI. Tier 2 unlocked successfully.",
    }


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# POST /users/verify-liveness
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
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
    Confidence ≥ 75% → auto-approve immediately → Tier 3 upgrade (PKR 5,00,000/day).
    No admin approval required.
    """
    if current_user.verification_tier < 2:
        raise HTTPException(
            400, "Complete CNIC verification (Tier 2) before liveness check."
        )
    if current_user.verification_tier >= 3:
        return {
            "message": "Liveness already verified.",
            "tier": current_user.verification_tier,
        }

    # Block duplicate pending liveness review
    existing_approved = (
        (
            await db.execute(
                select(KycReviewRequest)
                .where(
                    KycReviewRequest.user_id == current_user.id,
                    KycReviewRequest.review_type == "liveness",
                    KycReviewRequest.status == "auto_approved",
                )
                .order_by(KycReviewRequest.submitted_at.desc())
            )
        )
        .scalars()
        .first()
    )
    if existing_approved:
        return {
            "status": "approved",
            "tier": current_user.verification_tier,
            "message": "Liveness already verified.",
        }

    selfie_bytes = await selfie.read()
    _check_file_size(selfie_bytes, "Selfie")

    # Retrieve CNIC front from DB to get the public_id (pick most recent if multiple exist)
    cnic_front_doc = (
        (
            await db.execute(
                select(Document)
                .where(
                    Document.user_id == current_user.id,
                    Document.document_type == "cnic_front",
                )
                .order_by(Document.uploaded_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )

    if not cnic_front_doc:
        raise HTTPException(
            400, "CNIC front image not found. Please re-upload your CNIC."
        )

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

    THRESHOLD = 75.0
    if confidence < THRESHOLD:
        raise HTTPException(
            400,
            f"Liveness check failed. Face match confidence {confidence:.1f}% "
            f"(minimum {THRESHOLD}% required). Please try again in good lighting.",
        )

    # ✅ AI passed — upload selfie to Cloudinary and auto-approve immediately
    selfie_pub_id = await upload_kyc_document(
        selfie_bytes, current_user.id, "liveness_selfie"
    )
    selfie_doc = Document(
        user_id=current_user.id,
        document_type="liveness_selfie",
        cloudinary_public_id=selfie_pub_id,
    )
    db.add(selfie_doc)
    await db.flush()  # get selfie_doc.id

    # Create auto-approved record for audit trail
    review = KycReviewRequest(
        user_id=current_user.id,
        review_type="liveness",
        selfie_doc_id=selfie_doc.id,
        face_confidence=round(confidence, 2),
        status="auto_approved",
        reviewed_at=datetime.now(timezone.utc),
    )
    db.add(review)

    # Immediately upgrade to Tier 3
    wallet = (
        await db.execute(select(Wallet).where(Wallet.user_id == current_user.id))
    ).scalar_one_or_none()
    current_user.biometric_verified = True
    await _bump_tier(current_user, wallet, 3, db)

    asyncio.create_task(
        send_notification(
            db,
            current_user.id,
            title="✅ Liveness Verified!",
            body=f"Face match {confidence:.1f}% confirmed. Tier 3 unlocked — daily limit upgraded to PKR 5,00,000.",
            type="system",
        )
    )

    return {
        "status": "approved",
        "confidence": round(confidence, 2),
        "tier": current_user.verification_tier,
        "message": (
            f"Face match confirmed ({confidence:.1f}%). Tier 3 unlocked successfully!"
        ),
    }


# ══════════════════════════════════════════════════════════════════════════════
# POST /users/simulate-liveness   (demo bypass — no Face++ call)
# ══════════════════════════════════════════════════════════════════════════════
@router.post("/simulate-liveness")
@limiter.limit("10/day")
async def simulate_liveness(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    PROTOTYPE SIMULATION ONLY — bypasses Face++ comparison.

    Pakistani CNICs are valid for 10 years. Over that period a person's face
    changes significantly, making automated face-match APIs unreliable for
    legitimate users whose CNIC photo is old.

    In production this endpoint would be disabled. During demos / judging it
    lets evaluators approve KYC when the real Face++ call fails due to this
    known limitation, without pretending the real system passed.
    """
    if current_user.verification_tier < 2:
        raise HTTPException(
            400, "Complete CNIC verification (Tier 2) before liveness check."
        )
    if current_user.verification_tier >= 3:
        return {
            "status": "approved",
            "confidence": 100.0,
            "tier": current_user.verification_tier,
            "daily_limit": _TIER_LIMITS.get(current_user.verification_tier, 0),
            "message": "Liveness already verified.",
        }

    wallet = (
        await db.execute(select(Wallet).where(Wallet.user_id == current_user.id))
    ).scalar_one_or_none()

    review = KycReviewRequest(
        user_id=current_user.id,
        review_type="liveness",
        face_confidence=100.0,
        status="auto_approved",
        reviewed_at=datetime.now(timezone.utc),
    )
    db.add(review)

    current_user.biometric_verified = True
    await _bump_tier(current_user, wallet, 3, db)

    asyncio.create_task(
        send_notification(
            db,
            current_user.id,
            title=" Liveness Approved (Simulation)",
            body="KYC simulation approved. Tier 3 unlocked — PKR 5,00,000/day limit.",
            type="system",
        )
    )

    return {
        "status": "approved",
        "confidence": 100.0,
        "tier": current_user.verification_tier,
        "daily_limit": _TIER_LIMITS.get(current_user.verification_tier, 0),
        "message": "Simulation: face verification bypassed for demo. Tier 3 unlocked.",
    }


# ══════════════════════════════════════════════════════════════════════════════
# POST /users/fingerprint   (JSON body — no multipart)
# ══════════════════════════════════════════════════════════════════════════════
class FingerprintPayload(BaseModel):
    fingers: list[dict]  # [{finger_index: 1, hash: "sha256..."}, ...]


@router.post("/fingerprint")
@limiter.limit("30/day")
async def submit_fingerprint(
    request: Request,
    body: FingerprintPayload,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Accept 8-finger SHA-256 hashes (JSON body).
    Requires Tier 3 (liveness approved) first.
    Creates a pending admin review request â€” admin approval â†’ Tier 4 upgrade.
    """
    if current_user.verification_tier < 3:
        raise HTTPException(
            400,
            "Complete liveness verification (Tier 3) before fingerprint registration.",
        )
    if current_user.verification_tier >= 4:
        return {
            "message": "Fingerprint already verified.",
            "tier": current_user.verification_tier,
        }

    # Block duplicate pending fingerprint review
    existing_pending = (
        (
            await db.execute(
                select(KycReviewRequest)
                .where(
                    KycReviewRequest.user_id == current_user.id,
                    KycReviewRequest.review_type == "fingerprint",
                    KycReviewRequest.status == "pending",
                )
                .order_by(KycReviewRequest.submitted_at.desc())
            )
        )
        .scalars()
        .first()
    )
    if existing_pending:
        return {
            "status": "pending_review",
            "tier": current_user.verification_tier,
            "message": "Your fingerprint review is already pending. You will be notified once approved.",
        }

    fingers = body.fingers
    if len(fingers) < 8:
        raise HTTPException(400, f"8 finger hashes required. Received {len(fingers)}.")

    # Validate all required indices present
    provided_indices = {int(f["finger_index"]) for f in fingers}
    expected_indices = set(range(1, 9))
    if not expected_indices.issubset(provided_indices):
        missing = expected_indices - provided_indices
        raise HTTPException(400, f"Missing finger indices: {sorted(missing)}")

    # Simulate a 2.5 s NADRA processing delay (realism)
    await asyncio.sleep(2.5)

    # Validate all hashes are present (non-empty strings)
    for f in fingers[:8]:
        idx = int(f["finger_index"])
        fhash = str(f.get("hash", ""))
        if not fhash:
            raise HTTPException(400, f"Missing hash for finger_index {idx}")

    # Store SHA-256 hashes (raw images are NEVER stored)
    for f in fingers[:8]:
        scan = FingerprintScan(
            user_id=current_user.id,
            finger_index=int(f["finger_index"]),
            feature_hash=str(f["hash"]),
        )
        db.add(scan)

    # Create pending admin review (no auto-upgrade)
    review = KycReviewRequest(
        user_id=current_user.id,
        review_type="fingerprint",
        status="pending",
    )
    db.add(review)
    await db.commit()

    asyncio.create_task(
        send_notification(
            db,
            current_user.id,
            title="ðŸ– Fingerprint Submitted for Review",
            body="Your 8-finger biometric data has been sent to admin for approval. You will be notified once Tier 4 is unlocked.",
            type="system",
        )
    )

    return {
        "status": "pending_review",
        "tier": current_user.verification_tier,
        "fingers_registered": 8,
        "message": "Fingerprint data submitted. Pending admin approval for Tier 4 upgrade.",
    }


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# GET /users/kyc-status
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
@router.get("/kyc-status")
async def kyc_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Quick summary of user's KYC tier and verification flags + pending reviews."""
    wallet = (
        await db.execute(select(Wallet).where(Wallet.user_id == current_user.id))
    ).scalar_one_or_none()

    # Latest CNIC review
    latest_cnic_review = (
        (
            await db.execute(
                select(KycReviewRequest)
                .where(
                    KycReviewRequest.user_id == current_user.id,
                    KycReviewRequest.review_type == "cnic",
                )
                .order_by(KycReviewRequest.submitted_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )

    # Latest liveness review
    latest_liveness_review = (
        (
            await db.execute(
                select(KycReviewRequest)
                .where(
                    KycReviewRequest.user_id == current_user.id,
                    KycReviewRequest.review_type == "liveness",
                )
                .order_by(KycReviewRequest.submitted_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )

    # Latest fingerprint review
    latest_fingerprint_review = (
        (
            await db.execute(
                select(KycReviewRequest)
                .where(
                    KycReviewRequest.user_id == current_user.id,
                    KycReviewRequest.review_type == "fingerprint",
                )
                .order_by(KycReviewRequest.submitted_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )

    def _review_dict(r):
        if not r:
            return None
        return {
            "id": str(r.id),
            "status": r.status,
            "extracted_name": r.extracted_name,
            "extracted_cnic": r.cnic_masked,
            "extracted_dob": r.extracted_dob,
            "face_confidence": r.face_confidence,
            "rejection_reason": r.rejection_reason,
            "submitted_at": r.submitted_at.isoformat() if r.submitted_at else None,
            "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
        }

    return {
        "tier": current_user.verification_tier,
        "daily_limit": str(wallet.daily_limit) if wallet else "0",
        "cnic_verified": current_user.cnic_verified,
        "cnic_masked": current_user.cnic_number_masked,
        "biometric_verified": current_user.biometric_verified,
        "fingerprint_verified": current_user.fingerprint_verified,
        "nadra_verified": current_user.nadra_verified,
        "cnic_review": _review_dict(latest_cnic_review),
        "liveness_review": _review_dict(latest_liveness_review),
        "fingerprint_review": _review_dict(latest_fingerprint_review),
        "next_step": (
            "Upload CNIC to reach Tier 2"
            if current_user.verification_tier < 2
            else "Complete liveness check for Tier 3"
            if current_user.verification_tier < 3
            else "Register fingerprint for Tier 4"
            if current_user.verification_tier < 4
            else "Fully verified"
        ),
    }
