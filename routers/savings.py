"""Savings goals router — PROMPT 06."""
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
from uuid import UUID

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from limiter import limiter
from models.user import User
from models.wallet import Wallet
from models.transaction import Transaction
from models.savings import SavingGoal
from services.auth_service import get_current_user
from services.wallet_service import generate_reference, _send_fcm
from schemas.savings import (
    SavingGoalCreate, SavingGoalResponse, SavingGoalListResponse,
    DepositRequest, WithdrawRequest, AutoDeductUpdate,
)

router = APIRouter()

MAX_ACTIVE_GOALS = 5


def _utcnow():
    return datetime.now(timezone.utc)


def _progress(goal: SavingGoal) -> float:
    if goal.target_amount <= 0:
        return 0.0
    return round(float(goal.saved_amount / goal.target_amount * 100), 2)


def _daily_needed(goal: SavingGoal) -> float | None:
    if not goal.deadline or goal.is_completed:
        return None
    remaining = float(goal.target_amount - goal.saved_amount)
    days_left  = (goal.deadline - date.today()).days
    if days_left <= 0 or remaining <= 0:
        return 0.0
    return round(remaining / days_left, 2)


def _next_deduction(freq: str) -> datetime:
    now = _utcnow()
    if freq == "weekly":
        return now + timedelta(weeks=1)
    return now + timedelta(days=30)


def _to_response(goal: SavingGoal) -> SavingGoalResponse:
    return SavingGoalResponse(
        id=goal.id, goal_name=goal.goal_name, icon=goal.icon,
        target_amount=goal.target_amount, saved_amount=goal.saved_amount,
        deadline=goal.deadline, is_completed=goal.is_completed,
        goal_achieved=goal.goal_achieved, withdraw_count=goal.withdraw_count,
        auto_deduct_enabled=goal.auto_deduct_enabled,
        auto_deduct_amount=goal.auto_deduct_amount,
        auto_deduct_freq=goal.auto_deduct_freq,
        next_deduction_at=goal.next_deduction_at,
        last_deduction_at=goal.last_deduction_at,
        created_at=goal.created_at,
        progress_percent=_progress(goal),
        daily_saving_needed=_daily_needed(goal),
    )


async def _verify_pin(user: User, pin: str):
    if not user.pin_hash:
        raise HTTPException(400, "PIN not set. Set a PIN first.")
    if not bcrypt.checkpw(pin.encode(), user.pin_hash.encode()):
        raise HTTPException(401, "Incorrect PIN")


# ══════════════════════════════════════════════════════════════════════════════
# GET /savings/goals
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/goals", response_model=SavingGoalListResponse)
async def list_goals(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(SavingGoal)
        .where(SavingGoal.user_id == current_user.id)
        .order_by(SavingGoal.created_at.desc())
    )
    goals = result.scalars().all()
    active = [g for g in goals if not g.is_completed]
    return SavingGoalListResponse(
        goals=[_to_response(g) for g in goals],
        total=len(goals),
        active_count=len(active),
    )


# ══════════════════════════════════════════════════════════════════════════════
# POST /savings/goals
# ══════════════════════════════════════════════════════════════════════════════
@router.post("/goals", response_model=SavingGoalResponse, status_code=201)
@limiter.limit("10/minute")
async def create_goal(
    request: Request,
    body: SavingGoalCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    active_count = (await db.execute(
        select(SavingGoal)
        .where(SavingGoal.user_id == current_user.id, SavingGoal.is_completed == False)
    )).scalars().all()
    if len(active_count) >= MAX_ACTIVE_GOALS:
        raise HTTPException(400, f"Maximum {MAX_ACTIVE_GOALS} active saving goals allowed. Complete or delete an existing goal first.")
    goal = SavingGoal(
        user_id=current_user.id,
        goal_name=body.goal_name,
        target_amount=body.target_amount,
        icon=body.icon,
        deadline=body.deadline,
    )
    db.add(goal)
    await db.commit()
    await db.refresh(goal)
    return _to_response(goal)


# ══════════════════════════════════════════════════════════════════════════════
# GET /savings/goals/{goal_id}
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/goals/{goal_id}", response_model=SavingGoalResponse)
async def get_goal(
    goal_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    goal = (await db.execute(select(SavingGoal).where(SavingGoal.id == goal_id))).scalar_one_or_none()
    if not goal:
        raise HTTPException(404, "Saving goal not found")
    if goal.user_id != current_user.id:
        raise HTTPException(403, "Access denied")
    return _to_response(goal)


# ══════════════════════════════════════════════════════════════════════════════
# PUT /savings/goals/{goal_id}/deposit
# ══════════════════════════════════════════════════════════════════════════════
@router.put("/goals/{goal_id}/deposit", response_model=SavingGoalResponse)
@limiter.limit("20/minute")
async def deposit_to_goal(
    request: Request,
    goal_id: UUID,
    body: DepositRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _verify_pin(current_user, body.pin)
    goal = (await db.execute(select(SavingGoal).where(SavingGoal.id == goal_id))).scalar_one_or_none()
    if not goal or goal.user_id != current_user.id:
        raise HTTPException(404, "Goal not found")
    if goal.is_completed:
        raise HTTPException(400, "Goal is already completed")
    wallet = (await db.execute(select(Wallet).where(Wallet.user_id == current_user.id))).scalar_one_or_none()
    if not wallet or wallet.balance < body.amount:
        raise HTTPException(400, f"Insufficient balance. Available: PKR {wallet.balance:,.2f}")
    if wallet.is_frozen:
        raise HTTPException(403, "Wallet is frozen")

    wallet.balance    -= body.amount
    goal.saved_amount += body.amount
    ref = generate_reference()
    txn = Transaction(
        reference_number=ref, type="savings", amount=body.amount,
        fee=Decimal("0"), status="completed", sender_id=current_user.id,
        purpose="Savings", description=f"Deposit to goal: {goal.goal_name}",
        tx_metadata={"goal_id": str(goal_id), "action": "deposit"},
        completed_at=_utcnow(),
    )
    db.add(txn)

    # Check if goal reached 100%
    achieved = goal.saved_amount >= goal.target_amount
    if achieved and not goal.goal_achieved:
        goal.is_completed  = True
        goal.goal_achieved = True
        goal.auto_deduct_enabled = False
        # Refund overshoot
        overshoot = goal.saved_amount - goal.target_amount
        if overshoot > 0:
            wallet.balance    += overshoot
            goal.saved_amount  = goal.target_amount
        await db.commit()
        await db.refresh(goal)
        await db.refresh(wallet)
        import asyncio
        asyncio.create_task(_send_fcm(
            current_user.fcm_token or "",
            title="🎉 Goal Achieved!",
            body=f'Congratulations! Your "{goal.goal_name}" goal is complete!',
        ))
        return _to_response(goal)

    await db.commit()
    await db.refresh(goal)
    return _to_response(goal)


# ══════════════════════════════════════════════════════════════════════════════
# PUT /savings/goals/{goal_id}/withdraw
# ══════════════════════════════════════════════════════════════════════════════
@router.put("/goals/{goal_id}/withdraw", response_model=SavingGoalResponse)
@limiter.limit("10/minute")
async def withdraw_from_goal(
    request: Request,
    goal_id: UUID,
    body: WithdrawRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _verify_pin(current_user, body.pin)
    goal = (await db.execute(select(SavingGoal).where(SavingGoal.id == goal_id))).scalar_one_or_none()
    if not goal or goal.user_id != current_user.id:
        raise HTTPException(404, "Goal not found")
    if goal.saved_amount < body.amount:
        raise HTTPException(400, f"Cannot withdraw more than saved amount (PKR {goal.saved_amount:,.2f})")
    wallet = (await db.execute(select(Wallet).where(Wallet.user_id == current_user.id))).scalar_one_or_none()
    if not wallet:
        raise HTTPException(404, "Wallet not found")

    goal.saved_amount  -= body.amount
    goal.withdraw_count = (goal.withdraw_count or 0) + 1
    wallet.balance     += body.amount
    ref = generate_reference()
    txn = Transaction(
        reference_number=ref, type="savings", amount=body.amount,
        fee=Decimal("0"), status="completed", recipient_id=current_user.id,
        purpose="Savings", description=f"Withdrawal from goal: {goal.goal_name}",
        tx_metadata={"goal_id": str(goal_id), "action": "withdraw"},
        completed_at=_utcnow(),
    )
    db.add(txn)
    await db.commit()
    await db.refresh(goal)

    # AI roast trigger at withdraw_count >= 2
    if goal.withdraw_count >= 2:
        import asyncio
        asyncio.create_task(_trigger_ai_roast(current_user, goal))

    return _to_response(goal)


async def _trigger_ai_roast(user: User, goal: SavingGoal):
    """Fire-and-forget DeepSeek AI roast for repeat withdrawers."""
    try:
        from openai import AsyncOpenAI
        from config import settings
        client = AsyncOpenAI(
            api_key=settings.DEEPSEEK_API_KEY,
            base_url="https://api.deepseek.com",
        )
        resp = await client.chat.completions.create(
            model="deepseek-chat",
            messages=[{
                "role": "user",
                "content": (
                    f"You are a funny financial coach. The user {user.full_name} has withdrawn from their "
                    f"'{goal.goal_name}' savings goal {goal.withdraw_count} times. "
                    f"They've only saved PKR {goal.saved_amount} of PKR {goal.target_amount}. "
                    "Write a single witty, friendly roast (max 120 chars) to motivate them. "
                    "Be humorous, not mean. Use Urdu/English mix if appropriate."
                )
            }],
            max_tokens=80,
        )
        roast = resp.choices[0].message.content.strip()
        await _send_fcm(
            user.fcm_token or "",
            title="💸 Hey spender!",
            body=roast,
        )
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# PATCH /savings/goals/{goal_id}/auto-deduct
# ══════════════════════════════════════════════════════════════════════════════
@router.patch("/goals/{goal_id}/auto-deduct", response_model=SavingGoalResponse)
async def update_auto_deduct(
    goal_id: UUID,
    body: AutoDeductUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    goal = (await db.execute(select(SavingGoal).where(SavingGoal.id == goal_id))).scalar_one_or_none()
    if not goal or goal.user_id != current_user.id:
        raise HTTPException(404, "Goal not found")
    if goal.is_completed:
        raise HTTPException(400, "Cannot set auto-deduction on a completed goal")

    if body.enabled:
        wallet = (await db.execute(select(Wallet).where(Wallet.user_id == current_user.id))).scalar_one_or_none()
        if not wallet or wallet.balance < body.amount:
            raise HTTPException(400, f"Insufficient balance to enable auto-deduction. Available: PKR {wallet.balance:,.2f}")
        goal.auto_deduct_enabled = True
        goal.auto_deduct_amount  = body.amount
        goal.auto_deduct_freq    = body.frequency
        goal.next_deduction_at   = _next_deduction(body.frequency)
    else:
        goal.auto_deduct_enabled = False
        goal.auto_deduct_amount  = None
        goal.auto_deduct_freq    = None
        goal.next_deduction_at   = None

    await db.commit()
    await db.refresh(goal)
    return _to_response(goal)


# ══════════════════════════════════════════════════════════════════════════════
# DELETE /savings/goals/{goal_id}
# ══════════════════════════════════════════════════════════════════════════════
@router.delete("/goals/{goal_id}", status_code=200)
async def delete_goal(
    goal_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    goal = (await db.execute(select(SavingGoal).where(SavingGoal.id == goal_id))).scalar_one_or_none()
    if not goal or goal.user_id != current_user.id:
        raise HTTPException(404, "Goal not found")

    refund = goal.saved_amount
    if refund > 0:
        wallet = (await db.execute(select(Wallet).where(Wallet.user_id == current_user.id))).scalar_one_or_none()
        if wallet:
            wallet.balance += refund
            ref = generate_reference()
            txn = Transaction(
                reference_number=ref, type="savings", amount=refund,
                fee=Decimal("0"), status="completed", recipient_id=current_user.id,
                purpose="Savings", description=f"Goal deleted — refund: {goal.goal_name}",
                tx_metadata={"goal_id": str(goal_id), "action": "delete_refund"},
                completed_at=_utcnow(),
            )
            db.add(txn)

    await db.delete(goal)
    await db.commit()
    return {
        "message":       f'Goal "{goal.goal_name}" deleted',
        "refunded":      str(refund),
        "status":        "deleted",
    }
