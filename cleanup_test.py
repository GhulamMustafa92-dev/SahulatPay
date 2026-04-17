import asyncio
from sqlalchemy import text
from database import engine

async def main():
    async with engine.begin() as c:
        await c.execute(text("DELETE FROM login_audit WHERE phone_number='+923001234567'"))
        await c.execute(text("DELETE FROM users WHERE phone_number='+923001234567'"))
        await c.execute(text("DELETE FROM pending_registrations WHERE phone_number='+923001234567'"))
        await c.execute(text("DELETE FROM otp_codes WHERE phone_number='+923001234567'"))
    print("cleaned")

asyncio.run(main())
