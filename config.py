from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator
from typing import Optional


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── DATABASE ──────────────────────────────────────────────────────────────
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/unified_fintech"

    # ── JWT ───────────────────────────────────────────────────────────────────
    SECRET_KEY: str = "change-me-in-production-minimum-32-chars"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    # ── SECURITY ──────────────────────────────────────────────────────────────
    ENCRYPTION_KEY: str = ""        # Generate: from cryptography.fernet import Fernet; Fernet.generate_key()
    ADMIN_SECRET_KEY: str = ""

    # ── AI ────────────────────────────────────────────────────────────────────
    DEEPSEEK_API_KEY: str = ""

    # ── SMS — Infobip ─────────────────────────────────────────────────────────
    INFOBIP_API_KEY: str = ""
    INFOBIP_BASE_URL: str = ""      # e.g. https://XXXXX.api.infobip.com
    INFOBIP_SENDER_ID: str = "FinTechApp"

    # ── KYC — Cloudinary ──────────────────────────────────────────────────────
    CLOUDINARY_CLOUD_NAME: str = ""
    CLOUDINARY_API_KEY: str = ""
    CLOUDINARY_API_SECRET: str = ""

    # ── KYC — OCR + Face++ ───────────────────────────────────────────────────
    OCR_API_KEY: str = ""
    FACE_API_KEY: str = ""
    FACE_API_SECRET: str = ""

    # ── METAL RATES — goldpricez.com ─────────────────────────────────────────
    GOLDPRICEZ_API_KEY: str = "af44776048953adc613205d9c8a5ff3aaf447760"

    # ── STRIPE ────────────────────────────────────────────────────────────────────
    STRIPE_SECRET_KEY: str = ""
    STRIPE_PUBLISHABLE_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""

    # ── FIREBASE FCM ──────────────────────────────────────────────────────────────
    FIREBASE_CREDENTIALS_JSON: Optional[str] = None
    FIREBASE_CREDENTIALS_BASE64: Optional[str] = None

    # ── APP CONFIG ────────────────────────────────────────────────────────────
    ENVIRONMENT: str = "production"
    ALLOWED_ORIGINS: str = "http://localhost:3000,http://localhost:3001,http://localhost:5173,http://localhost:5174"
    DEV_MODE: bool = False
    PORT: int = 8000                      # Railway sets this automatically

    # ── VALIDATORS ────────────────────────────────────────────────────────────
    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def _normalize_db_url(cls, v: str) -> str:
        """Railway & Heroku inject DATABASE_URL as `postgres://` —
        SQLAlchemy 2.x + asyncpg requires `postgresql+asyncpg://`."""
        if not v:
            return v
        if v.startswith("postgres://"):
            v = v.replace("postgres://", "postgresql+asyncpg://", 1)
        elif v.startswith("postgresql://") and "+asyncpg" not in v:
            v = v.replace("postgresql://", "postgresql+asyncpg://", 1)
        return v


settings = Settings()
