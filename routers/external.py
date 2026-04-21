"""External services router — wallets, banks, bills, topup, international, insurance, investments, QR."""
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from limiter import limiter
from models.user import User
from models.wallet import Wallet
from models.transaction import Transaction
from services.auth_service import get_current_user
from services.wallet_service import generate_reference
from services.platform_ledger import ledger_credit, make_idem_key

router = APIRouter()


def _utcnow():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


async def _deduct_wallet(db, user_id, amount: Decimal, ref, txn_type, purpose, description, metadata,
                         account_type: str = "main_float"):
    wallet = (await db.execute(select(Wallet).where(Wallet.user_id == user_id))).scalar_one_or_none()
    if not wallet:
        raise HTTPException(404, "Wallet not found")
    if wallet.is_frozen:
        raise HTTPException(403, "Wallet is frozen")
    if wallet.balance < amount:
        raise HTTPException(400, f"Insufficient balance. Available: PKR {wallet.balance:,.2f}")
    wallet.balance -= amount
    txn = Transaction(
        reference_number=ref,
        type=txn_type,
        amount=amount,
        fee=Decimal("0"),
        status="completed",
        sender_id=user_id,
        purpose=purpose,
        description=description,
        tx_metadata=metadata,
        completed_at=_utcnow(),
    )
    db.add(txn)
    idem_key = make_idem_key("ext_deduct", account_type, str(user_id), ref)
    await ledger_credit(db, account_type, amount, idem_key,
                        user_id=user_id, reference=ref, note=description)
    await db.commit()
    await db.refresh(wallet)
    return wallet


async def _verify_pin(user, pin: str):
    import bcrypt
    if not user.pin_hash:
        raise HTTPException(400, "PIN not set")
    if not bcrypt.checkpw(pin.encode(), user.pin_hash.encode()):
        raise HTTPException(401, "Incorrect PIN")


# ════════════════════════════════════════════════════════════════════════════
# EXTERNAL WALLETS
# ════════════════════════════════════════════════════════════════════════════
from pydantic import BaseModel

class ExtWalletLookupReq(BaseModel):
    provider: str
    phone: str

class ExtWalletSendReq(BaseModel):
    provider: str
    phone: str
    amount: Decimal
    description: Optional[str] = None
    pin: str


@router.post("/wallet/lookup")
async def ext_wallet_lookup(body: ExtWalletLookupReq):
    from mock_servers.wallets import lookup_wallet
    from mock_servers.db import SessionLocal
    db = SessionLocal()
    try:
        return lookup_wallet(provider=body.provider, phone=body.phone, db=db)
    finally:
        db.close()


@router.post("/wallet/send", status_code=201)
@limiter.limit("10/minute")
async def ext_wallet_send(
    request: Request,
    body: ExtWalletSendReq,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _verify_pin(current_user, body.pin)
    ref = generate_reference()
    wallet = await _deduct_wallet(
        db, current_user.id, body.amount, ref,
        "external_wallet", "Other",
        f"Send to {body.provider} {body.phone}",
        {"provider": body.provider, "phone": body.phone},
    )
    from mock_servers.wallets import send_to_wallet, WalletSendRequest
    from mock_servers.db import SessionLocal
    mdb = SessionLocal()
    try:
        send_to_wallet(
            WalletSendRequest(
                provider=body.provider,
                phone=body.phone,
                amount=float(body.amount),
                description=body.description,
            ),
            db=mdb,
        )
        mdb.commit()
    except Exception as e:
        mdb.rollback()
        # Balance already deducted — log and continue; do NOT raise so user gets success
        import logging
        logging.getLogger(__name__).warning(
            f"[ext_wallet_send] mock send failed (balance already deducted): {e}"
        )
    finally:
        mdb.close()
    return {
        "status":        "completed",
        "reference":     ref,
        "provider":      body.provider,
        "phone":         body.phone,
        "amount":        body.amount,
        "new_balance":   wallet.balance,
        "message":       f"PKR {body.amount:,.2f} sent to {body.provider} {body.phone}",
    }


# ════════════════════════════════════════════════════════════════════════════
# BANKS / IBFT / RAAST
# ════════════════════════════════════════════════════════════════════════════
class IBFTLookupReq(BaseModel):
    bank_code: str
    account_number: str

class IBFTSendReq(BaseModel):
    bank_code: str
    account_number: str
    account_title: str
    amount: Decimal
    description: Optional[str] = None
    pin: str

class RaastSendReq(BaseModel):
    raast_id: str
    amount: Decimal
    description: Optional[str] = None
    pin: str


@router.post("/bank/lookup")
async def bank_lookup(body: IBFTLookupReq):
    from mock_servers.banks import lookup_bank_account, BankLookupRequest
    from mock_servers.db import SessionLocal
    db = SessionLocal()
    try:
        return lookup_bank_account(BankLookupRequest(bank_code=body.bank_code, account_number=body.account_number), db=db)
    finally:
        db.close()


@router.post("/bank/ibft", status_code=201)
@limiter.limit("5/minute")
async def ibft_send(
    request: Request,
    body: IBFTSendReq,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _verify_pin(current_user, body.pin)
    ref = generate_reference()
    from mock_servers.banks import ibft_send as mock_ibft, IBFTSendRequest
    from mock_servers.db import SessionLocal
    mdb = SessionLocal()
    try:
        mock_result = mock_ibft(IBFTSendRequest(
            bank_code=body.bank_code, account_number=body.account_number,
            account_title=body.account_title, amount=float(body.amount)), db=mdb)
        mdb.commit()
    except Exception as e:
        mdb.rollback()
        raise HTTPException(502, f"Bank transfer failed: {e}")
    finally:
        mdb.close()
    if not mock_result.get("success"):
        raise HTTPException(400, mock_result.get("message", "IBFT failed"))
    wallet = await _deduct_wallet(
        db, current_user.id, body.amount, ref,
        "bank_transfer", "Other",
        body.description or f"IBFT to {body.account_title} at {body.bank_code.upper()}",
        {"bank_code": body.bank_code, "account_number": body.account_number, "account_title": body.account_title},
    )
    return {**mock_result, "reference": ref, "new_balance": str(wallet.balance)}


@router.post("/bank/raast", status_code=201)
@limiter.limit("10/minute")
async def raast_send(
    request: Request,
    body: RaastSendReq,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _verify_pin(current_user, body.pin)
    ref = generate_reference()
    wallet = await _deduct_wallet(
        db, current_user.id, body.amount, ref,
        "bank_transfer", "Other",
        body.description or f"Raast to {body.raast_id}",
        {"raast_id": body.raast_id, "method": "raast"},
    )
    return {
        "status":     "completed",
        "reference":  ref,
        "raast_id":   body.raast_id,
        "amount":     body.amount,
        "new_balance": str(wallet.balance),
        "message":    f"PKR {body.amount:,.2f} sent via Raast to {body.raast_id}",
        "settled_in": "Instant",
    }


# ════════════════════════════════════════════════════════════════════════════
# UTILITY BILLS
# ════════════════════════════════════════════════════════════════════════════
class BillFetchReq(BaseModel):
    company: str
    consumer_id: str

class BillPayReq(BaseModel):
    company: str
    consumer_id: str
    amount: Decimal
    pin: str

class ChallanFetchReq(BaseModel):
    psid: str

class ChallanPayReq(BaseModel):
    psid: str
    amount: Decimal
    pin: str


@router.get("/bills/companies")
async def bill_companies():
    from mock_servers.bills import list_companies
    return list_companies()


@router.post("/bills/fetch")
async def fetch_bill(body: BillFetchReq):
    from mock_servers.bills import fetch_bill as mock_fetch, BillFetchRequest
    from mock_servers.db import SessionLocal
    db = SessionLocal()
    try:
        return mock_fetch(BillFetchRequest(company=body.company, consumer_id=body.consumer_id), db=db)
    finally:
        db.close()


@router.post("/bills/pay", status_code=201)
@limiter.limit("10/hour")
async def pay_bill(
    request: Request,
    body: BillPayReq,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _verify_pin(current_user, body.pin)
    ref = generate_reference()
    wallet = await _deduct_wallet(
        db, current_user.id, body.amount, ref,
        "bill", "Bill",
        f"Utility bill — {body.company.upper()} {body.consumer_id}",
        {"company": body.company, "consumer_id": body.consumer_id},
    )
    from mock_servers.bills import pay_bill as mock_pay, BillPayRequest
    from mock_servers.db import SessionLocal
    mdb = SessionLocal()
    try:
        mock_pay(BillPayRequest(company=body.company, consumer_id=body.consumer_id, amount=float(body.amount)), db=mdb)
        mdb.commit()
    except Exception as e:
        mdb.rollback()
        raise HTTPException(502, f"Bill payment failed: {e}")
    finally:
        mdb.close()
    return {"status": "completed", "reference": ref, "new_balance": str(wallet.balance), "message": f"Bill paid for {body.company.upper()} {body.consumer_id}"}


@router.post("/challan/fetch")
async def fetch_challan(body: ChallanFetchReq):
    from mock_servers.bills import fetch_challan as mock_fetch, ChallanFetchRequest
    from mock_servers.db import SessionLocal
    db = SessionLocal()
    try:
        return mock_fetch(ChallanFetchRequest(psid=body.psid), db=db)
    finally:
        db.close()


@router.post("/challan/pay", status_code=201)
@limiter.limit("10/hour")
async def pay_challan(
    request: Request,
    body: ChallanPayReq,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _verify_pin(current_user, body.pin)
    ref = generate_reference()
    wallet = await _deduct_wallet(
        db, current_user.id, body.amount, ref,
        "bill", "Other",
        f"Government challan PSID {body.psid}",
        {"psid": body.psid},
    )
    from mock_servers.bills import pay_challan as mock_pay, ChallanPayRequest
    from mock_servers.db import SessionLocal
    mdb = SessionLocal()
    try:
        mock_pay(ChallanPayRequest(psid=body.psid, amount=float(body.amount)), db=mdb)
        mdb.commit()
    except Exception as e:
        mdb.rollback()
        raise HTTPException(502, f"Challan payment failed: {e}")
    finally:
        mdb.close()
    return {"status": "completed", "reference": ref, "new_balance": str(wallet.balance)}


# ════════════════════════════════════════════════════════════════════════════
# INTERNATIONAL TRANSFER
# ════════════════════════════════════════════════════════════════════════════
class IntlRateReq(BaseModel):
    provider: str
    amount_pkr: Decimal
    currency: str
    country: str

class IntlSendReq(BaseModel):
    provider: str
    amount_pkr: Decimal
    currency: str
    country: str
    receiver_name: str
    receiver_phone: Optional[str] = None
    receiver_account: Optional[str] = None
    purpose: str = "Family Support"
    pin: str


@router.post("/international/rate")
async def intl_rate(body: IntlRateReq):
    from mock_servers.international import get_rate, RemittanceRateRequest
    return get_rate(RemittanceRateRequest(
        provider=body.provider, amount_pkr=float(body.amount_pkr),
        currency=body.currency, country=body.country))


@router.post("/international/send", status_code=201)
@limiter.limit("3/hour")
async def intl_send(
    request: Request,
    body: IntlSendReq,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _verify_pin(current_user, body.pin)
    ref = generate_reference()
    wallet = await _deduct_wallet(
        db, current_user.id, body.amount_pkr, ref,
        "bank_transfer", "Other",
        f"International transfer via {body.provider} to {body.receiver_name} ({body.country})",
        {"provider": body.provider, "currency": body.currency, "country": body.country},
    )
    from mock_servers.international import send_international, RemittanceSendRequest
    from mock_servers.db import SessionLocal
    mdb = SessionLocal()
    try:
        result = send_international(RemittanceSendRequest(
            provider=body.provider, amount_pkr=float(body.amount_pkr),
            currency=body.currency, country=body.country,
            receiver_name=body.receiver_name, receiver_phone=body.receiver_phone,
            receiver_account=body.receiver_account,
            sender_phone=current_user.phone_number, purpose=body.purpose,
        ), db=mdb)
    finally:
        mdb.close()
    return {**result, "new_balance": str(wallet.balance)}


# ════════════════════════════════════════════════════════════════════════════
# INSURANCE
# ════════════════════════════════════════════════════════════════════════════
class InsuranceLookupReq(BaseModel):
    policy_number: str

class InsurancePayReq(BaseModel):
    policy_number: str
    amount: Decimal
    pin: str


@router.get("/insurance/types")
async def insurance_types():
    from mock_servers.insurance import list_types
    return list_types()


@router.post("/insurance/lookup")
async def insurance_lookup(body: InsuranceLookupReq):
    from mock_servers.insurance import lookup_policy, PolicyLookupRequest
    from mock_servers.db import SessionLocal
    db = SessionLocal()
    try:
        return lookup_policy(PolicyLookupRequest(policy_number=body.policy_number), db=db)
    finally:
        db.close()


@router.post("/insurance/pay-premium", status_code=201)
@limiter.limit("5/hour")
async def insurance_pay(
    request: Request,
    body: InsurancePayReq,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _verify_pin(current_user, body.pin)
    ref = generate_reference()
    wallet = await _deduct_wallet(
        db, current_user.id, body.amount, ref,
        "bill", "Insurance",
        f"Insurance premium — policy {body.policy_number}",
        {"policy_number": body.policy_number},
    )
    return {"status": "completed", "reference": ref, "policy_number": body.policy_number,
            "amount": body.amount, "new_balance": str(wallet.balance)}


# ════════════════════════════════════════════════════════════════════════════
# INVESTMENTS (PSX + Mutual Funds)
# ════════════════════════════════════════════════════════════════════════════
class StockOrderReq(BaseModel):
    symbol: str
    units: float
    order_type: str = "buy"
    pin: str

class FundOrderReq(BaseModel):
    fund_code: str
    amount_pkr: Decimal
    order_type: str = "buy"
    pin: str


@router.get("/investments/stocks")
async def stocks(sector: Optional[str] = None):
    from mock_servers.investments import list_stocks
    from mock_servers.db import SessionLocal
    db = SessionLocal()
    try:
        return list_stocks(sector=sector, db=db)
    finally:
        db.close()


@router.get("/investments/stocks/{symbol}")
async def stock_detail(symbol: str):
    from mock_servers.investments import get_stock
    from mock_servers.db import SessionLocal
    db = SessionLocal()
    try:
        return get_stock(symbol=symbol, db=db)
    finally:
        db.close()


@router.post("/investments/stocks/order", status_code=201)
@limiter.limit("20/minute")
async def stock_order(
    request: Request,
    body: StockOrderReq,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _verify_pin(current_user, body.pin)
    from mock_servers.investments import get_stock
    from mock_servers.db import SessionLocal
    mdb = SessionLocal()
    try:
        stock = mdb.query(__import__("mock_servers.models", fromlist=["MockStock"]).MockStock).filter_by(symbol=body.symbol.upper()).first()
        if not stock:
            raise HTTPException(404, f"Stock {body.symbol} not found")
        total = Decimal(str(body.units * stock.price))
        if body.order_type == "buy":
            ref = generate_reference()
            wallet = await _deduct_wallet(
                db, current_user.id, total, ref,
                "investment", "Investment",
                f"PSX buy {body.units} {body.symbol} @ {stock.price}",
                {"type": "stock", "symbol": body.symbol, "units": body.units, "price": stock.price},
            )
        from mock_servers.investments import stock_order as mock_order, StockOrderRequest
        result = mock_order(StockOrderRequest(
            user_phone=current_user.phone_number,
            symbol=body.symbol, units=body.units, order_type=body.order_type,
        ), db=mdb)
        if body.order_type == "sell":
            ref = generate_reference()
            wallet_obj = (await db.execute(select(Wallet).where(Wallet.user_id == current_user.id))).scalar_one_or_none()
            wallet_obj.balance += total
            txn = Transaction(
                reference_number=ref, type="investment", amount=total,
                fee=Decimal("0"), status="completed", recipient_id=current_user.id,
                purpose="Investment", description=f"PSX sell {body.units} {body.symbol}",
                tx_metadata={"type": "stock", "symbol": body.symbol},
            )
            db.add(txn)
            await db.commit()
            await db.refresh(wallet_obj)
            result["new_balance"] = str(wallet_obj.balance)
        else:
            result["new_balance"] = str(wallet.balance)
        return result
    finally:
        mdb.close()


@router.get("/investments/funds")
async def funds(category: Optional[str] = None):
    from mock_servers.investments import list_funds
    from mock_servers.db import SessionLocal
    db = SessionLocal()
    try:
        return list_funds(category=category, db=db)
    finally:
        db.close()


@router.post("/investments/funds/order", status_code=201)
@limiter.limit("20/minute")
async def fund_order(
    request: Request,
    body: FundOrderReq,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _verify_pin(current_user, body.pin)
    from mock_servers.investments import fund_order as mock_order, MutualFundOrderRequest
    from mock_servers.db import SessionLocal
    mdb = SessionLocal()
    try:
        if body.order_type == "buy":
            ref = generate_reference()
            wallet = await _deduct_wallet(
                db, current_user.id, body.amount_pkr, ref,
                "investment", "Investment",
                f"Mutual fund buy {body.fund_code} PKR {body.amount_pkr}",
                {"type": "mutual_fund", "fund_code": body.fund_code},
            )
        result = mock_order(MutualFundOrderRequest(
            user_phone=current_user.phone_number,
            fund_code=body.fund_code,
            amount_pkr=float(body.amount_pkr),
            order_type=body.order_type,
        ), db=mdb)
        if body.order_type == "sell":
            ref = generate_reference()
            wallet_obj = (await db.execute(select(Wallet).where(Wallet.user_id == current_user.id))).scalar_one_or_none()
            wallet_obj.balance += body.amount_pkr
            txn = Transaction(
                reference_number=ref, type="investment", amount=body.amount_pkr,
                fee=Decimal("0"), status="completed", recipient_id=current_user.id,
                purpose="Investment", description=f"MF redeem {body.fund_code}",
                tx_metadata={"type": "mutual_fund", "fund_code": body.fund_code},
            )
            db.add(txn)
            await db.commit()
            await db.refresh(wallet_obj)
            result["new_balance"] = str(wallet_obj.balance)
        else:
            result["new_balance"] = str(wallet.balance)
        return result
    finally:
        mdb.close()


@router.get("/investments/portfolio")
async def portfolio(current_user: User = Depends(get_current_user)):
    from mock_servers.investments import get_portfolio
    from mock_servers.db import SessionLocal
    db = SessionLocal()
    try:
        return get_portfolio(user_phone=current_user.phone_number, db=db)
    finally:
        db.close()


# ════════════════════════════════════════════════════════════════════════════
# QR PAYMENTS
# ════════════════════════════════════════════════════════════════════════════
class QRGenerateReq(BaseModel):
    amount: Optional[float] = None
    description: Optional[str] = None
    expires_minutes: int = 30

class QRPayReq(BaseModel):
    qr_id: str
    amount: Optional[Decimal] = None
    pin: str


@router.post("/qr/generate", status_code=201)
async def qr_generate(
    body: QRGenerateReq,
    current_user: User = Depends(get_current_user),
):
    from mock_servers.qr import generate_qr, QRGenerateRequest
    from mock_servers.db import SessionLocal
    db = SessionLocal()
    try:
        return generate_qr(QRGenerateRequest(
            phone=current_user.phone_number,
            amount=body.amount,
            description=body.description,
            expires_minutes=body.expires_minutes,
        ), db=db)
    finally:
        db.close()


@router.get("/qr/decode")
async def qr_decode(qr_id: str):
    from mock_servers.qr import decode_qr, QRDecodeRequest
    from mock_servers.db import SessionLocal
    db = SessionLocal()
    try:
        return decode_qr(QRDecodeRequest(qr_id=qr_id), db=db)
    finally:
        db.close()


# ════════════════════════════════════════════════════════════════════════════
# NADRA KYC
# ════════════════════════════════════════════════════════════════════════════
class NADRAVerifyReq(BaseModel):
    cnic: str
    full_name: Optional[str] = None

class BiometricReq(BaseModel):
    cnic: str
    biometric_data: str = "mock_fingerprint_hash"


@router.post("/nadra/verify")
async def nadra_verify(body: NADRAVerifyReq):
    from mock_servers.nadra import verify_cnic, CNICVerifyRequest
    from mock_servers.db import SessionLocal
    db = SessionLocal()
    try:
        return verify_cnic(CNICVerifyRequest(cnic=body.cnic, full_name=body.full_name), db=db)
    finally:
        db.close()


@router.post("/nadra/biometric")
async def nadra_biometric(body: BiometricReq):
    from mock_servers.nadra import verify_biometric, BiometricRequest
    from mock_servers.db import SessionLocal
    db = SessionLocal()
    try:
        return verify_biometric(BiometricRequest(cnic=body.cnic, biometric_data=body.biometric_data), db=db)
    finally:
        db.close()


# ════════════════════════════════════════════════════════════════════════════
# MERCHANTS
# ════════════════════════════════════════════════════════════════════════════
class MerchantSubReq(BaseModel):
    merchant_code: str
    card_number: str
    last_four: str
    plan: str
    billing_cycle: str = "monthly"


@router.get("/merchants/list")
async def merchants_list():
    from mock_servers.merchants import list_merchants
    return list_merchants()


@router.post("/merchants/subscribe", status_code=201)
async def merchant_subscribe(
    body: MerchantSubReq,
    current_user: User = Depends(get_current_user),
):
    from mock_servers.merchants import subscribe, MerchantSubscribeRequest
    from mock_servers.db import SessionLocal
    db = SessionLocal()
    try:
        return subscribe(MerchantSubscribeRequest(
            merchant_code=body.merchant_code,
            card_number=body.card_number,
            last_four=body.last_four,
            user_phone=current_user.phone_number,
            plan=body.plan,
            billing_cycle=body.billing_cycle,
        ), db=db)
    finally:
        db.close()
