from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
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

    # Schedulers started in Prompt 06 — placeholder
    # from scheduler.savings_scheduler import start_savings_scheduler
    # from scheduler.card_scheduler import start_card_scheduler
    # start_savings_scheduler()
    # start_card_scheduler()

    yield

    # ── Shutdown ──
    # scheduler.shutdown() added in Prompt 06
    print("[shutdown] server stopping")


def _init_firebase():
    import base64, tempfile, os
    try:
        import firebase_admin
        from firebase_admin import credentials
        if not firebase_admin._apps:
            if settings.FIREBASE_CREDENTIALS_BASE64:
                decoded = base64.b64decode(settings.FIREBASE_CREDENTIALS_BASE64)
                tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
                tmp.write(decoded)
                tmp.flush()
                cred = credentials.Certificate(tmp.name)
            elif settings.FIREBASE_CREDENTIALS_JSON:
                cred = credentials.Certificate(settings.FIREBASE_CREDENTIALS_JSON)
            else:
                print("[firebase] no credentials — FCM disabled")
                return
            firebase_admin.initialize_app(cred)
            print("[firebase] initialized")
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

# from routers import user
# app.include_router(user.router, prefix="/api/v1/users", tags=["Users & KYC"])

from routers import wallet
app.include_router(wallet.router, prefix="/api/v1/wallets", tags=["Wallet"])

# from routers import transaction
# app.include_router(transaction.router, prefix="/api/v1/transactions", tags=["Transactions"])

from routers import card
app.include_router(card.router, prefix="/api/v1/cards", tags=["Cards"])

# from routers import savings
# app.include_router(savings.router, prefix="/api/v1/savings", tags=["Savings"])

# from routers import finance
# app.include_router(finance.router, prefix="/api/v1", tags=["Finance"])

# from routers import rewards
# app.include_router(rewards.router, prefix="/api/v1/rewards", tags=["Rewards"])

# from routers import social
# app.include_router(social.router, prefix="/api/v1", tags=["Social"])

# from routers import ai
# app.include_router(ai.router, prefix="/api/v1/ai", tags=["AI"])

# from routers import zakat
# app.include_router(zakat.router, prefix="/api/v1/zakat", tags=["Zakat"])

# from routers import notification
# app.include_router(notification.router, prefix="/api/v1/notifications", tags=["Notifications"])

# from routers import banking
# app.include_router(banking.router, prefix="/api/v1/banking", tags=["Banking"])

# from routers import admin
# app.include_router(admin.router, prefix="/api/v1/admin", tags=["Admin"])
