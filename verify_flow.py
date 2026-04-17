"""Verify the pending_registrations flow:
1. /register → pending row exists, users row does NOT
2. /otp/verify → users row created, pending row deleted
"""
import asyncio, httpx
from sqlalchemy import text
from database import engine

PHONE = "03007777777"
NORM  = "+923007777777"


async def fetch_row_count(table, where):
    async with engine.connect() as c:
        r = await c.execute(text(f"SELECT COUNT(*) FROM {table} WHERE {where}"))
        return r.scalar()


async def cleanup():
    async with engine.begin() as c:
        await c.execute(text(f"DELETE FROM login_audit WHERE phone_number='{NORM}'"))
        await c.execute(text(f"DELETE FROM users WHERE phone_number='{NORM}'"))
        await c.execute(text(f"DELETE FROM pending_registrations WHERE phone_number='{NORM}'"))
        await c.execute(text(f"DELETE FROM otp_codes WHERE phone_number='{NORM}'"))


async def main():
    await cleanup()
    c = httpx.AsyncClient(base_url="http://localhost:8000/api/v1/auth", timeout=10.0)

    # Before register — both empty
    pending = await fetch_row_count("pending_registrations", f"phone_number='{NORM}'")
    users   = await fetch_row_count("users",                 f"phone_number='{NORM}'")
    print(f"Before /register → pending={pending}, users={users}")
    assert pending == 0 and users == 0

    # Register
    await c.post("/register", json={
        "phone": PHONE, "email": "x@y.com", "full_name": "X Y",
        "password": "Pass1234!", "country": "Pakistan", "account_type": "individual",
    })
    pending = await fetch_row_count("pending_registrations", f"phone_number='{NORM}'")
    users   = await fetch_row_count("users",                 f"phone_number='{NORM}'")
    print(f"After  /register → pending={pending}, users={users}  ← users still 0!")
    assert pending == 1, "pending row should exist"
    assert users   == 0, "user should NOT be in users table yet"

    # Get OTP and verify
    otp = (await c.get(f"/dev/otp/{NORM}")).json()["otp"]
    await c.post("/otp/verify", json={"phone": PHONE, "otp": otp, "purpose": "registration"})

    pending = await fetch_row_count("pending_registrations", f"phone_number='{NORM}'")
    users   = await fetch_row_count("users",                 f"phone_number='{NORM}'")
    print(f"After  /otp/verify → pending={pending}, users={users}  ← user created, pending deleted")
    assert pending == 0, "pending row should be deleted"
    assert users   == 1, "user should now exist"

    await c.aclose()
    await cleanup()
    print("\n✅ Flow verified: user is ONLY saved to DB after OTP is verified.")


asyncio.run(main())
