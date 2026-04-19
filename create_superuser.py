"""
Create or update a superuser in the PostgreSQL database.

Usage:
    python create_superuser.py                          # uses DATABASE_URL from .env / env vars
    python create_superuser.py <DATABASE_URL>           # explicit URL

The script prompts for phone, full name and password interactively.
"""
from __future__ import annotations

import asyncio
import getpass
import sys

import bcrypt
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker


# ── helpers ─────────────────────────────────────────────────────────────────

def _make_engine(url: str):
    url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if "+asyncpg" not in url:
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return create_async_engine(url, echo=False)


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def _normalize_phone(phone: str) -> str:
    phone = phone.strip().replace(" ", "").replace("-", "")
    if phone.startswith("+92") and len(phone) == 13:
        return phone
    if phone.startswith("03") and len(phone) == 11:
        return "+92" + phone[1:]
    raise ValueError("Invalid phone. Use +92XXXXXXXXXX or 03XXXXXXXXX")


# ── main ────────────────────────────────────────────────────────────────────

async def create_superuser(db_url: str):
    engine = _make_engine(db_url)
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    print("\n=== SahulatPay — Create Superuser ===\n")

    # Collect credentials
    phone_raw = input("Phone number (e.g. 03001234567): ").strip()
    try:
        phone = _normalize_phone(phone_raw)
    except ValueError as e:
        print(f"Error: {e}")
        await engine.dispose()
        return

    full_name = input("Full name: ").strip()
    if not full_name:
        print("Error: Full name is required.")
        await engine.dispose()
        return

    password = getpass.getpass("Password: ")
    if len(password) < 8:
        print("Error: Password must be at least 8 characters.")
        await engine.dispose()
        return

    password_confirm = getpass.getpass("Confirm password: ")
    if password != password_confirm:
        print("Error: Passwords do not match.")
        await engine.dispose()
        return

    password_hash = _hash_password(password)

    async with Session() as session:
        # Check if user already exists
        result = await session.execute(
            text("SELECT id, is_superuser FROM users WHERE phone_number = :phone"),
            {"phone": phone}
        )
        existing = result.fetchone()

        if existing:
            if existing[1]:
                print(f"\nUser {phone} is already a superuser. No changes made.")
            else:
                confirm = input(f"\nUser {phone} exists but is NOT a superuser. Promote to superuser? [yes/no]: ").strip().lower()
                if confirm == "yes":
                    await session.execute(
                        text("UPDATE users SET is_superuser = true, is_active = true, is_verified = true, password_hash = :ph WHERE phone_number = :phone"),
                        {"ph": password_hash, "phone": phone}
                    )
                    await session.commit()
                    print(f"\n✅  {phone} has been promoted to superuser.")
                else:
                    print("Aborted.")
        else:
            await session.execute(
                text("""
                    INSERT INTO users (
                        phone_number, full_name, password_hash,
                        is_superuser, is_active, is_verified,
                        verification_tier, account_type
                    ) VALUES (
                        :phone, :name, :ph,
                        true, true, true,
                        3, 'individual'
                    )
                """),
                {"phone": phone, "name": full_name, "ph": password_hash}
            )
            await session.commit()
            print(f"\n✅  Superuser '{full_name}' ({phone}) created successfully.")

    await engine.dispose()


if __name__ == "__main__":
    # Resolve database URL
    if len(sys.argv) >= 2:
        db_url = sys.argv[1]
    else:
        try:
            from config import settings
            db_url = settings.DATABASE_URL
        except Exception:
            print("Error: Could not load DATABASE_URL from config. Pass it as an argument.")
            print("Usage: python create_superuser.py <DATABASE_URL>")
            sys.exit(1)

    asyncio.run(create_superuser(db_url))
