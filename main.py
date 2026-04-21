from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from config  import settings
from limiter import limiter


# ── Lifespan (startup / shutdown) ─────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──
    print(f"[startup] environment={settings.ENVIRONMENT} dev_mode={settings.DEV_MODE}")

    # Firebase init
    _init_firebase()

    # Mock SQLite DB — create tables + seed data
    from mock_servers.db import create_all_tables
    from mock_servers.seeds import seed_all
    from mock_servers.db import SessionLocal as MockSession
    create_all_tables()
    mock_db = MockSession()
    try:
        seed_all(mock_db)
    finally:
        mock_db.close()

    # Subscription scheduler (PROMPT 05b)
    from scheduler.subscription_scheduler import start_subscription_scheduler, stop_subscription_scheduler
    start_subscription_scheduler()

    # Savings auto-deduction scheduler (PROMPT 06)
    from scheduler.savings_scheduler import start_savings_scheduler, stop_savings_scheduler
    start_savings_scheduler()

    # Card monthly-reset scheduler (PROMPT 06)
    from scheduler.card_scheduler import start_card_scheduler, stop_card_scheduler
    start_card_scheduler()

    # Metal rate cache — fetches gold/silver prices every hour (Zakat)
    from scheduler.metal_rate_scheduler import start_metal_rate_scheduler, stop_metal_rate_scheduler
    start_metal_rate_scheduler()

    # Hawl notification — daily Zakat due-date + reminder notifications
    from scheduler.hawl_scheduler import start_hawl_scheduler, stop_hawl_scheduler
    start_hawl_scheduler()

    # Fraud detection — 15-min scanner + 02:00 UTC daily deep analysis
    from scheduler.fraud_scheduler import start_fraud_scheduler, stop_fraud_scheduler
    start_fraud_scheduler()

    # Debt lifecycle — 15-min soft/intercept/hard escalation
    from scheduler.debt_scheduler import start_debt_scheduler, stop_debt_scheduler
    start_debt_scheduler()

    # Daily reconciliation — 01:00 UTC balance integrity check
    from scheduler.reconciliation_scheduler import start_reconciliation_scheduler, stop_reconciliation_scheduler
    start_reconciliation_scheduler()

    yield

    # ── Shutdown ──
    stop_subscription_scheduler()
    stop_savings_scheduler()
    stop_card_scheduler()
    stop_metal_rate_scheduler()
    stop_hawl_scheduler()
    stop_fraud_scheduler()
    stop_debt_scheduler()
    stop_reconciliation_scheduler()
    print("[shutdown] server stopping")


def _init_firebase():
    import base64, os, tempfile
    try:
        import firebase_admin
        from firebase_admin import credentials
        if not firebase_admin._apps:
            cred = None
            if settings.FIREBASE_CREDENTIALS_BASE64:
                decoded = base64.b64decode(settings.FIREBASE_CREDENTIALS_BASE64)
                tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
                tmp.write(decoded)
                tmp.flush()
                cred = credentials.Certificate(tmp.name)
                print("[firebase] using base64 credentials")
            elif settings.FIREBASE_CREDENTIALS_JSON:
                cred = credentials.Certificate(settings.FIREBASE_CREDENTIALS_JSON)
                print(f"[firebase] using credentials from {settings.FIREBASE_CREDENTIALS_JSON}")
            else:
                _base = os.path.dirname(os.path.abspath(__file__))
                for _fname in ("firebase-credentials.json", "firebase-adminsdk.json"):
                    _path = os.path.join(_base, _fname)
                    if os.path.exists(_path):
                        cred = credentials.Certificate(_path)
                        print(f"[firebase] auto-detected credentials: {_fname}")
                        break
            if cred is None:
                print("[firebase] no credentials found — FCM push disabled")
                return
            firebase_admin.initialize_app(cred)
            print("[firebase] initialized OK")
    except Exception as e:
        print(f"[firebase] init failed: {e}")


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Unified FinTech API",
    description="SmartPay + EasyPay unified backend",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.DEV_MODE else None,
    redoc_url="/redoc" if settings.DEV_MODE else None,
)

# Rate limiter
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.ALLOWED_ORIGINS.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# HSTS (production only — Railway handles TLS termination)
if settings.ENVIRONMENT == "production":
    @app.middleware("http")
    async def add_hsts(request: Request, call_next):
        response = await call_next(request)
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
        return response


# ── Health check ──────────────────────────────────────────────────────────────
@app.get("/health", tags=["Health"])
async def health():
    return {"status": "ok", "environment": settings.ENVIRONMENT}


# ── Routers — uncomment as each prompt is completed ──────────────────────────
from routers import auth
app.include_router(auth.router, prefix="/api/v1/auth", tags=["Auth"])

from routers import kyc
app.include_router(kyc.router, prefix="/api/v1/users", tags=["KYC"])

from routers import user
app.include_router(user.router, prefix="/api/v1/users", tags=["Users"])

from routers import wallet
app.include_router(wallet.router, prefix="/api/v1/wallets", tags=["Wallet"])

from routers import transaction
app.include_router(transaction.router, prefix="/api/v1/transactions", tags=["Transactions"])

from routers import external
app.include_router(external.router, prefix="/api/v1/external", tags=["External Services"])

from routers import stripe_router
app.include_router(stripe_router.router, prefix="/api/v1/stripe", tags=["Stripe"])

from mock_servers import wallets as mock_wallets, banks as mock_banks, bills as mock_bills
from mock_servers import topup as mock_topup, merchants as mock_merchants
from mock_servers import nadra as mock_nadra, international as mock_intl
from mock_servers import insurance as mock_insurance, investments as mock_investments, qr as mock_qr
app.include_router(mock_wallets.router,    prefix="/mock/wallets",       tags=["Mock: Wallets"])
app.include_router(mock_banks.router,      prefix="/mock/banks",         tags=["Mock: Banks"])
app.include_router(mock_bills.router,      prefix="/mock/bills",         tags=["Mock: Bills"])
app.include_router(mock_topup.router,      prefix="/mock/topup",         tags=["Mock: Top-up"])
app.include_router(mock_merchants.router,  prefix="/mock/merchants",     tags=["Mock: Merchants"])
app.include_router(mock_nadra.router,      prefix="/mock/nadra",         tags=["Mock: NADRA"])
app.include_router(mock_intl.router,       prefix="/mock/international", tags=["Mock: International"])
app.include_router(mock_insurance.router,  prefix="/mock/insurance",     tags=["Mock: Insurance"])
app.include_router(mock_investments.router,prefix="/mock/investments",   tags=["Mock: Investments"])
app.include_router(mock_qr.router,         prefix="/mock/qr",            tags=["Mock: QR"])

from mock_servers import dashboard as mock_dashboard
app.include_router(mock_dashboard.router,  prefix="/mock/dashboard",      tags=["Mock: Dashboard"])

from routers import card
app.include_router(card.router, prefix="/api/v1/cards", tags=["Cards"])

from routers import savings
app.include_router(savings.router, prefix="/api/v1/savings", tags=["Savings"])

from routers import finance
app.include_router(finance.router, prefix="/api/v1", tags=["Finance"])

from routers import rewards
app.include_router(rewards.router, prefix="/api/v1/rewards", tags=["Rewards"])

from routers import social
app.include_router(social.router, prefix="/api/v1", tags=["Social"])

from routers import ai
app.include_router(ai.router, prefix="/api/v1/ai", tags=["AI"])

from routers import zakat
app.include_router(zakat.router, prefix="/api/v1/zakat", tags=["Zakat"])

from routers import notification
app.include_router(notification.router, prefix="/api/v1/notifications", tags=["Notifications"])

from routers import banking
app.include_router(banking.router, prefix="/api/v1/banking", tags=["Banking"])

from routers import admin
app.include_router(admin.router, prefix="/api/v1/admin", tags=["Admin"])

from routers import gold
app.include_router(gold.router, prefix="/api/v1/gold", tags=["Gold & Silver"])

from routers import topup
app.include_router(topup.router, prefix="/api/v1/topup", tags=["TopUp"])
