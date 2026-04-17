"""All SQLite ORM models for mock servers."""
from sqlalchemy import Column, Integer, String, Float, Boolean, Date, DateTime, Text
from sqlalchemy.sql import func
from mock_servers.db import Base


# ── External Wallet Accounts ──────────────────────────────────────────────────
class MockWalletAccount(Base):
    __tablename__ = "mock_wallet_accounts"
    id         = Column(Integer, primary_key=True, autoincrement=True)
    provider   = Column(String(30), nullable=False)   # jazzcash | easypaisa | sadapay | nayapay | upaisa
    phone      = Column(String(20), nullable=False, unique=True)
    name       = Column(String(100), nullable=False)
    balance    = Column(Float, default=5000.0)
    is_active  = Column(Boolean, default=True)


# ── Bank Accounts (IBFT) ──────────────────────────────────────────────────────
class MockBankAccount(Base):
    __tablename__ = "mock_bank_accounts"
    id              = Column(Integer, primary_key=True, autoincrement=True)
    bank_code       = Column(String(30), nullable=False)   # hbl | mcb | ubl | meezan | allied | alfalah | faysal | habibmetro | js | scb
    account_number  = Column(String(30), nullable=False)
    iban            = Column(String(30))
    account_title   = Column(String(150), nullable=False)
    balance         = Column(Float, default=50000.0)
    is_active       = Column(Boolean, default=True)


# ── Utility Bills ─────────────────────────────────────────────────────────────
class MockBill(Base):
    __tablename__ = "mock_bills"
    id            = Column(Integer, primary_key=True, autoincrement=True)
    company       = Column(String(50), nullable=False)    # ssgc | sngpl | kelectric | lesco | ptcl | stormfiber | nayatel | iesco | fesco | mepco
    consumer_id   = Column(String(30), nullable=False)
    customer_name = Column(String(150), nullable=False)
    amount_due    = Column(Float, nullable=False)
    due_date      = Column(String(20))
    bill_month    = Column(String(20))
    is_paid       = Column(Boolean, default=False)


# ── Government Challans ───────────────────────────────────────────────────────
class MockChallan(Base):
    __tablename__ = "mock_challans"
    id          = Column(Integer, primary_key=True, autoincrement=True)
    department  = Column(String(80))      # FBR | Traffic | BISP | PSID | Municipal | Passport | NADRA
    psid        = Column(String(30), nullable=False, unique=True)
    reference   = Column(String(50))
    description = Column(String(200))
    amount      = Column(Float, nullable=False)
    is_paid     = Column(Boolean, default=False)
    due_date    = Column(String(20))


# ── NADRA CNIC Database ───────────────────────────────────────────────────────
class MockCNIC(Base):
    __tablename__ = "mock_cnics"
    id          = Column(Integer, primary_key=True, autoincrement=True)
    cnic        = Column(String(20), nullable=False, unique=True)
    full_name   = Column(String(150), nullable=False)
    father_name = Column(String(150))
    dob         = Column(String(20))
    address     = Column(Text)
    status      = Column(String(20), default="valid")   # valid | expired | blocked


# ── Merchant Subscribed Cards ─────────────────────────────────────────────────
class MockMerchantSubscription(Base):
    __tablename__ = "mock_merchant_subscriptions"
    id             = Column(Integer, primary_key=True, autoincrement=True)
    merchant_code  = Column(String(30), nullable=False)   # netflix | spotify | youtube | icloud | amazon | canva | chatgpt | adobe
    card_hash      = Column(String(255), nullable=False)  # SHA-256 of card number
    last_four      = Column(String(4))
    user_phone     = Column(String(20))
    amount         = Column(Float, nullable=False)
    billing_cycle  = Column(String(20), default="monthly")
    next_charge_at = Column(Date, nullable=False)
    is_active      = Column(Boolean, default=True)
    subscribed_at  = Column(DateTime, server_default=func.now())


# ── International Transfer Log ────────────────────────────────────────────────
class MockInternationalTransfer(Base):
    __tablename__ = "mock_international_transfers"
    id           = Column(Integer, primary_key=True, autoincrement=True)
    provider     = Column(String(30))   # western_union | wise | remitly | moneygram
    reference    = Column(String(50))
    sender_phone = Column(String(20))
    receiver_name= Column(String(150))
    country      = Column(String(80))
    amount_pkr   = Column(Float)
    amount_fx    = Column(Float)
    currency     = Column(String(10))
    status       = Column(String(20), default="processing")
    created_at   = Column(DateTime, server_default=func.now())


# ── Insurance Policies ────────────────────────────────────────────────────────
class MockInsurancePolicy(Base):
    __tablename__ = "mock_insurance_policies"
    id              = Column(Integer, primary_key=True, autoincrement=True)
    policy_number   = Column(String(30), nullable=False, unique=True)
    policy_type     = Column(String(30))   # life | health | vehicle | travel | home
    provider        = Column(String(80))   # Jubilee | State Life | EFU | Adamjee | TPL
    customer_name   = Column(String(150))
    premium_amount  = Column(Float)
    coverage_amount = Column(Float)
    next_due_date   = Column(String(20))
    is_active       = Column(Boolean, default=True)


# ── PSX Stocks ────────────────────────────────────────────────────────────────
class MockStock(Base):
    __tablename__ = "mock_stocks"
    id             = Column(Integer, primary_key=True, autoincrement=True)
    symbol         = Column(String(20), nullable=False, unique=True)
    company_name   = Column(String(150))
    sector         = Column(String(80))
    price          = Column(Float, nullable=False)
    change         = Column(Float, default=0.0)
    change_percent = Column(Float, default=0.0)
    volume         = Column(Integer, default=0)
    market_cap     = Column(Float, default=0.0)


# ── Mutual Funds ──────────────────────────────────────────────────────────────
class MockMutualFund(Base):
    __tablename__ = "mock_mutual_funds"
    id          = Column(Integer, primary_key=True, autoincrement=True)
    fund_code   = Column(String(20), nullable=False, unique=True)
    fund_name   = Column(String(150))
    provider    = Column(String(80))   # NBP | UBL | HBL | Meezan | MCB
    category    = Column(String(50))   # equity | income | money_market | islamic | balanced
    nav         = Column(Float)        # Net Asset Value per unit
    ytd_return  = Column(Float, default=0.0)
    risk_level  = Column(String(20), default="medium")


# ── User Investment Portfolio ─────────────────────────────────────────────────
class MockPortfolio(Base):
    __tablename__ = "mock_portfolios"
    id          = Column(Integer, primary_key=True, autoincrement=True)
    user_phone  = Column(String(20), nullable=False)
    asset_type  = Column(String(20))   # stock | mutual_fund
    symbol      = Column(String(20))   # stock symbol or fund_code
    units       = Column(Float, default=0.0)
    avg_price   = Column(Float, default=0.0)


# ── QR Payment Codes ──────────────────────────────────────────────────────────
class MockQRCode(Base):
    __tablename__ = "mock_qr_codes"
    id          = Column(Integer, primary_key=True, autoincrement=True)
    qr_id       = Column(String(50), nullable=False, unique=True)
    phone       = Column(String(20), nullable=False)
    amount      = Column(Float, nullable=True)    # None = open amount QR
    description = Column(String(200))
    is_used     = Column(Boolean, default=False)
    expires_at  = Column(DateTime)
    created_at  = Column(DateTime, server_default=func.now())
