"""Social router — SmartSplit + Trusted Circle. PROMPT 10."""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from limiter import limiter
from models.social import BillSplit, SplitParticipant, TrustedCircle
from models.user import User
from services.auth_service import get_current_user, normalize_phone
from services.wallet_service import doTransfer, _send_fcm
from services.notification_service import send_notification

router = APIRouter()


def _utcnow():
    return datetime.now(timezone.utc)


def _mask_phone(phone: str) -> str:
    if len(phone) >= 7:
        return phone[:3] + "****" + phone[-4:]
    return phone


# ════════════════════════════════════════════════════════════════════════════
# BILL SPLITS
# ════════════════════════════════════════════════════════════════════════════
class ParticipantInput(BaseModel):
    phone:  str
    amount: Optional[Decimal] = None   # required if split_type=custom


class SplitCreate(BaseModel):
    title:        str  = Field(..., min_length=1, max_length=255)
    total_amount: Decimal = Field(..., gt=0)
    split_type:   Literal["equal", "custom"] = "equal"
    participants: list[ParticipantInput] = Field(..., min_length=1)
    pin:          str


class SplitRespondRequest(BaseModel):
    action: Literal["accept", "decline"]
    pin:    Optional[str] = None   # required on accept


# ── POST /splits ──────────────────────────────────────────────────────────────
@router.post("/splits", status_code=201)
@limiter.limit("20/minute")
async def create_split(
    request: Request,
    body: SplitCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    import bcrypt
    if not current_user.pin_hash or not bcrypt.checkpw(body.pin.encode(), current_user.pin_hash.encode()):
        raise HTTPException(401, "Incorrect PIN")

    # Resolve participant phones → users
    resolved = []
    total_custom = Decimal("0")
    for p in body.participants:
        try:
            phone = normalize_phone(p.phone)
        except ValueError:
            raise HTTPException(400, f"Invalid phone: {p.phone}")
        if phone == current_user.phone_number:
            raise HTTPException(400, "Cannot add yourself as a split participant")
        user = (await db.execute(select(User).where(User.phone_number == phone))).scalar_one_or_none()
        if not user:
            raise HTTPException(404, f"No user found for {_mask_phone(phone)}")
        if body.split_type == "custom":
            if not p.amount or p.amount <= 0:
                raise HTTPException(400, f"Custom amount required for {_mask_phone(phone)}")
            total_custom += p.amount
        resolved.append((user, p.amount))

    if body.split_type == "custom" and abs(total_custom - body.total_amount) > Decimal("0.01"):
        raise HTTPException(400, f"Custom amounts sum ({total_custom}) must equal total_amount ({body.total_amount})")

    equal_share = body.total_amount / len(resolved) if body.split_type == "equal" else None

    # Create split
    split = BillSplit(
        creator_id=current_user.id,
        title=body.title,
        total_amount=body.total_amount,
        split_type=body.split_type,
        status="active",
    )
    db.add(split)
    await db.flush()   # get split.id

    # Create participants
    import asyncio
    notification_tasks = []
    participants_out = []
    for user, custom_amount in resolved:
        amount = custom_amount if body.split_type == "custom" else equal_share
        part = SplitParticipant(
            split_id=split.id,
            user_id=user.id,
            amount=amount,
            status="pending",
        )
        db.add(part)
        participants_out.append({
            "user_id":   str(user.id),
            "full_name": user.full_name,
            "phone":     _mask_phone(user.phone_number),
            "amount":    str(amount),
            "status":    "pending",
        })
        notification_tasks.append(
            send_notification(
                db, user.id,
                title=f"💸 Split Request from {current_user.full_name}",
                body=f'"{body.title}" — Your share: PKR {amount:,.2f}. Tap to pay.',
                type="transaction",
            )
        )

    await db.commit()
    for task in notification_tasks:
        asyncio.create_task(task)

    return {
        "split_id":    str(split.id),
        "title":       split.title,
        "total_amount": str(split.total_amount),
        "split_type":  split.split_type,
        "participants": participants_out,
        "message":     f"Split created. Notified {len(resolved)} participant(s).",
    }


# ── GET /splits/my-splits ─────────────────────────────────────────────────────
@router.get("/splits/my-splits")
async def my_splits(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    splits_created = (await db.execute(
        select(BillSplit).where(BillSplit.creator_id == current_user.id)
        .order_by(BillSplit.created_at.desc())
    )).scalars().all()

    parts_rows = (await db.execute(
        select(SplitParticipant).where(SplitParticipant.user_id == current_user.id)
    )).scalars().all()
    participating_split_ids = {p.split_id for p in parts_rows}

    splits_participating = []
    if participating_split_ids:
        splits_participating = (await db.execute(
            select(BillSplit).where(BillSplit.id.in_(participating_split_ids))
        )).scalars().all()

    def _fmt(s: BillSplit, role: str):
        return {
            "split_id":     str(s.id),
            "title":        s.title,
            "total_amount": str(s.total_amount),
            "split_type":   s.split_type,
            "status":       s.status,
            "role":         role,
            "created_at":   s.created_at.isoformat(),
        }

    return {
        "as_creator":     [_fmt(s, "creator") for s in splits_created],
        "as_participant": [_fmt(s, "participant") for s in splits_participating],
    }


# ── GET /splits/{split_id} ────────────────────────────────────────────────────
@router.get("/splits/{split_id}")
async def get_split(
    split_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    split = (await db.execute(select(BillSplit).where(BillSplit.id == split_id))).scalar_one_or_none()
    if not split:
        raise HTTPException(404, "Split not found")

    participants = (await db.execute(
        select(SplitParticipant).where(SplitParticipant.split_id == split_id)
    )).scalars().all()

    is_creator = split.creator_id == current_user.id
    is_participant = any(p.user_id == current_user.id for p in participants)
    if not is_creator and not is_participant:
        raise HTTPException(403, "Access denied")

    parts_out = []
    for p in participants:
        user = (await db.execute(select(User).where(User.id == p.user_id))).scalar_one_or_none()
        parts_out.append({
            "participant_id": str(p.id),
            "user_id":        str(p.user_id),
            "full_name":      user.full_name if user else "Unknown",
            "phone":          _mask_phone(user.phone_number) if user else "",
            "amount":         str(p.amount),
            "status":         p.status,
            "paid_at":        p.paid_at.isoformat() if p.paid_at else None,
        })

    paid_total   = sum(p.amount for p in participants if p.status == "paid")
    pending_total = sum(p.amount for p in participants if p.status == "pending")

    return {
        "split_id":      str(split.id),
        "title":         split.title,
        "total_amount":  str(split.total_amount),
        "paid_total":    str(paid_total),
        "pending_total": str(pending_total),
        "split_type":    split.split_type,
        "status":        split.status,
        "created_at":    split.created_at.isoformat(),
        "participants":  parts_out,
    }


# ── POST /splits/{split_id}/respond ───────────────────────────────────────────
@router.post("/splits/{split_id}/respond")
@limiter.limit("20/minute")
async def respond_to_split(
    request: Request,
    split_id: UUID,
    body: SplitRespondRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    split = (await db.execute(select(BillSplit).where(BillSplit.id == split_id))).scalar_one_or_none()
    if not split or split.status != "active":
        raise HTTPException(404, "Active split not found")

    part = (await db.execute(
        select(SplitParticipant).where(
            SplitParticipant.split_id == split_id,
            SplitParticipant.user_id  == current_user.id,
        )
    )).scalar_one_or_none()
    if not part:
        raise HTTPException(403, "You are not a participant in this split")
    if part.status != "pending":
        raise HTTPException(400, f"Already {part.status}")

    if body.action == "decline":
        part.status      = "declined"
        part.declined_at = _utcnow()
        await db.commit()
        import asyncio
        asyncio.create_task(send_notification(
            db, split.creator_id,
            title="❌ Split Declined",
            body=f"{current_user.full_name} declined their share in '{split.title}'.",
            type="alert",
        ))
        return {"status": "declined", "message": "You have declined this split request."}

    # Accept → transfer money from participant to creator
    if not body.pin:
        raise HTTPException(400, "PIN required to accept and pay")

    result = await doTransfer(
        db=db,
        sender_id=current_user.id,
        recipient_id=split.creator_id,
        amount=part.amount,
        purpose="Other",
        description=f"Split payment: {split.title}",
        pin=body.pin,
        biometric_confirmed=False,
    )

    if result["status"] == "pending_biometric":
        return {
            "status":          "pending_biometric",
            "message":         f"Amount PKR {part.amount:,.2f} ≥ PKR 1,000. Confirm with biometrics.",
            "pending_tx_token": result["pending_tx_token"],
        }

    part.status  = "paid"
    part.paid_at = _utcnow()
    await db.commit()

    import asyncio
    asyncio.create_task(send_notification(
        db, split.creator_id,
        title=f"✅ Split Payment Received",
        body=f"{current_user.full_name} paid PKR {part.amount:,.2f} for '{split.title}'.",
        type="transaction",
    ))

    # Check if all paid → mark split completed
    all_parts = (await db.execute(
        select(SplitParticipant).where(SplitParticipant.split_id == split_id)
    )).scalars().all()
    if all(p.status in ("paid", "declined") for p in all_parts):
        split.status = "completed"
        await db.commit()

    return {
        "status":      "paid",
        "amount":      str(part.amount),
        "reference":   result.get("reference_number"),
        "new_balance": str(result.get("new_balance")),
        "message":     f"PKR {part.amount:,.2f} sent to {split.title} creator.",
    }


# ── POST /splits/{split_id}/remind ───────────────────────────────────────────
@router.post("/splits/{split_id}/remind")
@limiter.limit("5/hour")
async def remind_split(
    request: Request,
    split_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    split = (await db.execute(select(BillSplit).where(BillSplit.id == split_id))).scalar_one_or_none()
    if not split:
        raise HTTPException(404, "Split not found")
    if split.creator_id != current_user.id:
        raise HTTPException(403, "Only the creator can send reminders")

    participants = (await db.execute(
        select(SplitParticipant).where(
            SplitParticipant.split_id == split_id,
            SplitParticipant.status == "pending",
        )
    )).scalars().all()

    import asyncio
    count = 0
    for part in participants:
        user = (await db.execute(select(User).where(User.id == part.user_id))).scalar_one_or_none()
        if user:
            asyncio.create_task(_send_fcm(
                user.fcm_token or "",
                title=f"⏰ Reminder: Split Payment Due",
                body=f'Your PKR {part.amount:,.2f} share for "{split.title}" is pending.',
            ))
            part.reminder_sent_at = _utcnow()
            count += 1

    await db.commit()
    return {"reminded": count, "message": f"Reminder sent to {count} pending participant(s)."}


# ════════════════════════════════════════════════════════════════════════════
# TRUSTED CIRCLE
# ════════════════════════════════════════════════════════════════════════════
class AddContactRequest(BaseModel):
    phone:    str
    nickname: Optional[str] = Field(default=None, max_length=100)


@router.get("/trusted-circle/lookup")
async def lookup_trusted_contact(
    phone: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Look up a user by phone number before adding them to the trusted circle."""
    try:
        normalized = normalize_phone(phone)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if normalized == current_user.phone_number:
        raise HTTPException(400, "That is your own number.")
    contact = (await db.execute(select(User).where(User.phone_number == normalized))).scalar_one_or_none()
    if not contact:
        raise HTTPException(404, "No SahulatPay user found with this number.")
    return {
        "found":      True,
        "full_name":  contact.full_name,
        "phone_masked": _mask_phone(contact.phone_number),
    }


@router.get("/trusted-circle")
async def get_trusted_circle(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = (await db.execute(
        select(TrustedCircle).where(TrustedCircle.user_id == current_user.id)
        .order_by(TrustedCircle.added_at.desc())
    )).scalars().all()

    out = []
    for row in rows:
        contact = (await db.execute(select(User).where(User.id == row.contact_id))).scalar_one_or_none()
        if contact:
            out.append({
                "id":            str(row.id),
                "contact_id":    str(contact.id),
                "full_name":     contact.full_name,
                "phone_masked":  _mask_phone(contact.phone_number),
                "profile_photo": contact.profile_photo,
                "nickname":      row.nickname,
                "added_at":      row.added_at.isoformat(),
            })
    return {"contacts": out, "total": len(out)}


@router.post("/trusted-circle", status_code=201)
@limiter.limit("30/hour")
async def add_contact(
    request: Request,
    body: AddContactRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        phone = normalize_phone(body.phone)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if phone == current_user.phone_number:
        raise HTTPException(400, "You cannot add yourself to trusted circle")

    contact = (await db.execute(select(User).where(User.phone_number == phone))).scalar_one_or_none()
    if not contact:
        raise HTTPException(404, "No SahulatPay user found with this phone number")

    existing = (await db.execute(
        select(TrustedCircle).where(
            TrustedCircle.user_id    == current_user.id,
            TrustedCircle.contact_id == contact.id,
        )
    )).scalar_one_or_none()
    if existing:
        raise HTTPException(409, "Contact already in your trusted circle")

    entry = TrustedCircle(
        user_id=current_user.id,
        contact_id=contact.id,
        nickname=body.nickname,
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    return {
        "id":           str(entry.id),
        "contact_id":   str(contact.id),
        "full_name":    contact.full_name,
        "phone_masked": _mask_phone(contact.phone_number),
        "nickname":     entry.nickname,
        "message":      f"{contact.full_name} added to your trusted circle.",
    }


@router.delete("/trusted-circle/{entry_id}", status_code=200)
async def remove_contact(
    entry_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    entry = (await db.execute(select(TrustedCircle).where(TrustedCircle.id == entry_id))).scalar_one_or_none()
    if not entry or entry.user_id != current_user.id:
        raise HTTPException(404, "Contact not found in your trusted circle")
    await db.delete(entry)
    await db.commit()
    return {"status": "removed", "message": "Contact removed from trusted circle."}
