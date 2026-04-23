"""Stripe real payment integration — wallet top-up via Stripe PaymentIntent."""
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Request, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from typing import Optional

from config import settings
from database import get_db
from models.user import User
from models.wallet import Wallet
from models.transaction import Transaction
from services.auth_service import get_current_user
from services.wallet_service import generate_reference

router = APIRouter()


def _get_stripe():
    try:
        import stripe
        if not settings.STRIPE_SECRET_KEY:
            raise HTTPException(503, "Stripe is not configured. Add STRIPE_SECRET_KEY to env vars.")
        stripe.api_key = settings.STRIPE_SECRET_KEY
        return stripe
    except ImportError:
        raise HTTPException(503, "Stripe package not installed")


class CreatePaymentIntentRequest(BaseModel):
    amount_pkr: float
    description: Optional[str] = "SahulatPay wallet top-up"


class ConfirmStripeDepositRequest(BaseModel):
    payment_intent_id: str


class SandboxTopUpRequest(BaseModel):
    amount_pkr:  float
    description: Optional[str] = "SahulatPay sandbox top-up"


# ── GET /stripe/config ───────────────────────────────────────────────────────
@router.get("/config")
async def stripe_config(current_user: User = Depends(get_current_user)):
    """Return the Stripe publishable key so the Android SDK can initialize."""
    if not settings.STRIPE_PUBLISHABLE_KEY:
        raise HTTPException(503, "Stripe publishable key not configured on server")
    return {"publishable_key": settings.STRIPE_PUBLISHABLE_KEY}


# ── POST /stripe/create-intent ────────────────────────────────────────────────
@router.post("/create-intent")
async def create_payment_intent(
    body: CreatePaymentIntentRequest,
    current_user: User = Depends(get_current_user),
):
    """Create a Stripe PaymentIntent. Returns client_secret for frontend SDK."""
    stripe = _get_stripe()
    try:
        # Use USD for test-mode compatibility; store PKR amount in metadata
        amount_usd_cents = max(1, int(body.amount_pkr))  # 1 USD cent per PKR (test only)
        intent = stripe.PaymentIntent.create(
            amount=amount_usd_cents,
            currency="usd",
            description=body.description,
            metadata={
                "user_id":    str(current_user.id),
                "phone":      current_user.phone_number,
                "amount_pkr": str(body.amount_pkr),
            },
        )
        return {
            "client_secret":     intent.client_secret,
            "payment_intent_id": intent.id,
            "amount_pkr":        body.amount_pkr,
            "status":            intent.status,
        }
    except Exception as e:
        raise HTTPException(400, f"Stripe error: {str(e)}")


# ── POST /stripe/confirm-deposit ──────────────────────────────────────────────
@router.post("/confirm-deposit")
async def confirm_stripe_deposit(
    body: ConfirmStripeDepositRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Call after Stripe payment succeeds on frontend — credits wallet."""
    stripe = _get_stripe()
    try:
        intent = stripe.PaymentIntent.retrieve(body.payment_intent_id)
    except Exception as e:
        raise HTTPException(400, f"Stripe error: {str(e)}")
    if intent.status != "succeeded":
        raise HTTPException(400, f"Payment not completed. Status: {intent.status}")
    if intent.metadata.get("user_id") != str(current_user.id):
        raise HTTPException(403, "Payment intent does not belong to current user")

    # Read PKR amount from metadata (set during create-intent)
    meta_pkr = intent.metadata.get("amount_pkr")
    amount_pkr = Decimal(meta_pkr) if meta_pkr else Decimal(str(intent.amount))
    wallet = (await db.execute(select(Wallet).where(Wallet.user_id == current_user.id))).scalar_one_or_none()
    if not wallet:
        raise HTTPException(404, "Wallet not found")

    wallet.balance = (wallet.balance or Decimal("0")) + amount_pkr
    ref = generate_reference()
    txn = Transaction(
        reference_number=ref,
        type="deposit",
        amount=amount_pkr,
        fee=Decimal("0"),
        status="completed",
        recipient_id=current_user.id,
        purpose="TopUp",
        description=f"Stripe deposit — {intent.id}",
        tx_metadata={"stripe_payment_intent": intent.id, "method": "stripe"},
    )
    db.add(txn)
    await db.commit()
    await db.refresh(wallet)
    return {
        "success":           True,
        "message":           f"PKR {amount_pkr:,.2f} deposited via Stripe",
        "new_balance":       str(wallet.balance),
        "reference_number":  ref,
        "stripe_intent_id":  intent.id,
    }


# ── POST /stripe/webhook ──────────────────────────────────────────────────────
@router.post("/webhook")
async def stripe_webhook(
    request: Request,
    stripe_signature: Optional[str] = Header(None, alias="stripe-signature"),
    db: AsyncSession = Depends(get_db),
):
    """Stripe webhook — handles payment_intent.succeeded events."""
    stripe = _get_stripe()
    body = await request.body()
    try:
        event = stripe.Webhook.construct_event(
            body, stripe_signature, settings.STRIPE_WEBHOOK_SECRET or ""
        )
    except Exception as e:
        raise HTTPException(400, f"Webhook signature verification failed: {e}")

    if event["type"] == "payment_intent.succeeded":
        intent = event["data"]["object"]
        user_id = intent.get("metadata", {}).get("user_id")
        if user_id:
            from uuid import UUID
            amount_pkr = Decimal(str(intent["amount"] / 100))
            wallet = (await db.execute(
                select(Wallet).where(Wallet.user_id == UUID(user_id))
            )).scalar_one_or_none()
            if wallet:
                wallet.balance = (wallet.balance or Decimal("0")) + amount_pkr
                ref = generate_reference()
                txn = Transaction(
                    reference_number=ref,
                    type="deposit",
                    amount=amount_pkr,
                    fee=Decimal("0"),
                    status="completed",
                    recipient_id=UUID(user_id),
                    purpose="TopUp",
                    description=f"Stripe webhook deposit — {intent['id']}",
                    tx_metadata={"stripe_payment_intent": intent["id"], "method": "stripe_webhook"},
                )
                db.add(txn)
                await db.commit()
    return {"received": True}


# ── POST /stripe/sandbox-topup ────────────────────────────────────────────────
@router.post("/sandbox-topup")
async def sandbox_topup(
    body: SandboxTopUpRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Sandbox/test top-up — creates & auto-confirms a PaymentIntent using Stripe's
    built-in test payment method (pm_card_visa).  No client-side Stripe SDK needed.
    If STRIPE_SECRET_KEY is not set, simulates success directly (dev fallback).
    """
    amount_pkr = Decimal(str(body.amount_pkr))
    if amount_pkr <= 0:
        raise HTTPException(400, "Amount must be positive")

    wallet = (await db.execute(
        select(Wallet).where(Wallet.user_id == current_user.id)
    )).scalar_one_or_none()
    if not wallet:
        raise HTTPException(404, "Wallet not found")

    intent_id = None

    if settings.STRIPE_SECRET_KEY:
        stripe = _get_stripe()
        try:
            intent = stripe.PaymentIntent.create(
                amount               = max(50, int(body.amount_pkr * 100)),
                currency             = "usd",
                payment_method       = "pm_card_visa",
                payment_method_types = ["card"],
                confirm              = True,
                return_url           = "https://sahulatpay.app/stripe/return",
                description          = body.description,
                metadata             = {
                    "user_id":    str(current_user.id),
                    "phone":      current_user.phone_number,
                    "sandbox":    "true",
                },
            )
            if intent.status not in ("succeeded", "requires_capture"):
                raise HTTPException(400, f"Stripe sandbox payment failed. Status: {intent.status}")
            intent_id = intent.id
        except Exception as e:
            raise HTTPException(400, f"Stripe error: {str(e)}")

    wallet.balance += amount_pkr
    ref = generate_reference()
    txn = Transaction(
        reference_number = ref,
        type             = "deposit",
        amount           = amount_pkr,
        fee              = Decimal("0"),
        status           = "completed",
        recipient_id     = current_user.id,
        purpose          = "TopUp",
        description      = body.description or "Stripe sandbox top-up",
        tx_metadata      = {
            "method":               "stripe_sandbox",
            "stripe_payment_intent": intent_id or "sandbox_simulated",
        },
    )
    db.add(txn)
    await db.commit()
    await db.refresh(wallet)

    return {
        "success":          True,
        "message":          f"PKR {amount_pkr:,.2f} added to your wallet via Stripe (sandbox)",
        "new_balance":      str(wallet.balance),
        "reference_number": ref,
        "stripe_intent_id": intent_id or "simulated",
        "sandbox":          True,
    }
