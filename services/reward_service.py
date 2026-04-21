"""Reward service — cashback engine (1%), offer tracking, claim logic."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.rewards import Reward, RewardOffer, RewardTransaction

CASHBACK_RATE = Decimal("0.01")   # 1% of outgoing transaction


def _utcnow():
    return datetime.now(timezone.utc)


async def _get_or_create_reward(db: AsyncSession, user_id: UUID) -> Reward:
    reward = (await db.execute(select(Reward).where(Reward.user_id == user_id))).scalar_one_or_none()
    if not reward:
        reward = Reward(user_id=user_id)
        db.add(reward)
        await db.flush()
    return reward


async def add_cashback(
    db: AsyncSession,
    user_id: UUID,
    transaction_amount: Decimal,
    transaction_id: UUID,
    purpose: str = "Other",
) -> Decimal:
    """
    Called inside doTransfer after a successful outgoing transaction.
    Adds 1% of transaction_amount to rewards.pending (NOT wallet balance).
    Also updates matching active RewardOffers for this category.
    Returns cashback amount credited.
    """
    cashback = (transaction_amount * CASHBACK_RATE).quantize(Decimal("0.01"))
    if cashback <= 0:
        return Decimal("0")

    reward = await _get_or_create_reward(db, user_id)
    reward.pending      += cashback
    reward.total_earned += cashback

    rt = RewardTransaction(
        user_id=user_id,
        transaction_id=transaction_id,
        type="cashback",
        amount=cashback,
    )
    db.add(rt)

    # Update matching active offers (case-insensitive category match)
    active_offers = (await db.execute(
        select(RewardOffer).where(
            RewardOffer.user_id  == user_id,
            RewardOffer.status   == "active",
            RewardOffer.category == purpose.lower(),
            RewardOffer.expires_at > _utcnow(),
        )
    )).scalars().all()

    for offer in active_offers:
        offer.current_spent += transaction_amount
        if offer.current_spent >= offer.target_amount:
            offer.status       = "completed"
            offer.completed_at = _utcnow()

    return cashback


async def claim_cashback(db: AsyncSession, user_id: UUID) -> Decimal:
    """
    Move rewards.pending → rewards.claimed + wallet.balance.
    Called from /rewards/claim endpoint.
    Returns amount claimed.
    """
    from models.wallet import Wallet
    reward = (await db.execute(select(Reward).where(Reward.user_id == user_id))).scalar_one_or_none()
    if not reward or reward.pending <= 0:
        return Decimal("0")

    amount = reward.pending
    reward.claimed += amount
    reward.pending  = Decimal("0")

    wallet = (await db.execute(select(Wallet).where(Wallet.user_id == user_id))).scalar_one_or_none()
    if wallet:
        wallet.balance          += amount
        wallet.cashback_pending  = Decimal("0")
        wallet.cashback_claimed  = (wallet.cashback_claimed or Decimal("0")) + amount

    rt = RewardTransaction(
        user_id=user_id,
        type="claim",
        amount=amount,
    )
    db.add(rt)
    return amount


async def credit_offer_reward(
    db: AsyncSession,
    user_id: UUID,
    offer_id: UUID,
) -> Decimal:
    """
    Credit completed offer reward to wallet.
    Called from /rewards/offers/{id}/claim.
    Returns amount credited.
    """
    from models.wallet import Wallet
    offer = (await db.execute(select(RewardOffer).where(
        RewardOffer.id      == offer_id,
        RewardOffer.user_id == user_id,
    ))).scalar_one_or_none()
    if not offer:
        return Decimal("0")
    if offer.status != "completed":
        raise ValueError(f"Offer is not completed yet (status: {offer.status})")

    offer.status     = "claimed"
    offer.claimed_at = _utcnow()

    reward = await _get_or_create_reward(db, user_id)
    reward.total_earned += offer.reward_amount
    reward.claimed      += offer.reward_amount

    wallet = (await db.execute(select(Wallet).where(Wallet.user_id == user_id))).scalar_one_or_none()
    if wallet:
        wallet.balance += offer.reward_amount

    rt = RewardTransaction(
        user_id=user_id,
        offer_id=offer_id,
        type="offer_reward",
        amount=offer.reward_amount,
    )
    db.add(rt)
    return offer.reward_amount
