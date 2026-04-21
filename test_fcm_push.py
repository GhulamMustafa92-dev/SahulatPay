"""
FCM Push Notification Test Script
===================================
Run this to verify Firebase push notifications are working.

Usage:
  # Option A — sends to the token stored in .env or DB for a phone number
  python test_fcm_push.py

  # Option B — send to a specific FCM token directly
  python test_fcm_push.py --token <FCM_TOKEN>

  # Option C — look up token from DB by phone number, then push
  python test_fcm_push.py --phone 03001234567
"""

import asyncio
import os
import sys
import argparse
import functools
from pathlib import Path

# ── resolve backend root ───────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))


# ── 1. initialise Firebase ─────────────────────────────────────────────────────
def init_firebase() -> bool:
    try:
        import firebase_admin
        from firebase_admin import credentials

        if firebase_admin._apps:
            print("[firebase] already initialized")
            return True

        # Try env vars first
        cred = None
        fb_b64 = os.getenv("FIREBASE_CREDENTIALS_BASE64", "")
        fb_json = os.getenv("FIREBASE_CREDENTIALS_JSON", "")

        if fb_b64:
            import base64, tempfile
            decoded = base64.b64decode(fb_b64)
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
            tmp.write(decoded)
            tmp.flush()
            cred = credentials.Certificate(tmp.name)
            print(f"[firebase] loaded from FIREBASE_CREDENTIALS_BASE64")
        elif fb_json and Path(fb_json).exists():
            cred = credentials.Certificate(fb_json)
            print(f"[firebase] loaded from FIREBASE_CREDENTIALS_JSON={fb_json}")
        else:
            # Auto-detect local files
            for fname in ("firebase-credentials.json", "firebase-adminsdk.json"):
                fpath = BASE_DIR / fname
                if fpath.exists():
                    cred = credentials.Certificate(str(fpath))
                    print(f"[firebase] auto-detected: {fname}")
                    break

        if cred is None:
            print("[firebase] ❌ No credential file found!")
            print("  Place firebase-credentials.json in the backend folder, or set")
            print("  FIREBASE_CREDENTIALS_JSON / FIREBASE_CREDENTIALS_BASE64 in .env")
            return False

        firebase_admin.initialize_app(cred)
        print("[firebase] ✅ Initialized successfully")
        return True

    except Exception as e:
        print(f"[firebase] ❌ Init failed: {e}")
        return False


# ── 2. send a test push ────────────────────────────────────────────────────────
async def send_test_push(token: str, title: str, body: str) -> None:
    from firebase_admin import messaging

    msg = messaging.Message(
        notification=messaging.Notification(title=title, body=body),
        data={"test": "true", "source": "test_fcm_push.py"},
        android=messaging.AndroidConfig(
            priority="high",
            notification=messaging.AndroidNotification(
                channel_id="sahulatpay_main",
                priority="high",
                default_vibrate_timings=True,
                default_sound=True,
            ),
        ),
        apns=messaging.APNSConfig(
            payload=messaging.APNSPayload(
                aps=messaging.Aps(sound="default", badge=1),
            ),
        ),
        token=token,
    )

    loop = asyncio.get_event_loop()
    try:
        msg_id = await loop.run_in_executor(None, functools.partial(messaging.send, msg))
        print(f"\n✅ Push SENT successfully!")
        print(f"   message_id : {msg_id}")
        print(f"   title      : {title}")
        print(f"   body       : {body}")
        print(f"   token      : {token[:20]}...{token[-10:]}")
    except Exception as e:
        print(f"\n❌ Push FAILED: {e}")


# ── 3. look up FCM token from DB by phone ──────────────────────────────────────
async def get_token_from_db(phone: str) -> str | None:
    try:
        from database import AsyncSessionLocal
        from models.user import User
        from sqlalchemy import select

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(User.fcm_token, User.full_name)
                .where(User.phone_number == phone)
            )
            row = result.first()
            if row is None:
                print(f"[db] ❌ No user found with phone {phone}")
                return None
            name, token = row.full_name, row.fcm_token
            if not token:
                print(f"[db] ❌ User '{name}' has no FCM token stored — app hasn't registered yet")
                return None
            print(f"[db] ✅ Found token for '{name}' ({phone})")
            return token
    except Exception as e:
        print(f"[db] ❌ DB lookup failed: {e}")
        return None


# ── 4. look up the first user that has an FCM token ───────────────────────────
async def get_any_token_from_db() -> tuple[str, str] | None:
    try:
        from database import AsyncSessionLocal
        from models.user import User
        from sqlalchemy import select

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(User.fcm_token, User.full_name, User.phone_number)
                .where(User.fcm_token.isnot(None))
                .limit(1)
            )
            row = result.first()
            if row is None:
                print("[db] ❌ No user with an FCM token found in DB")
                return None
            token, name, phone = row.fcm_token, row.full_name, row.phone_number
            print(f"[db] ✅ Using token for '{name}' ({phone})")
            return token, name
    except Exception as e:
        print(f"[db] ❌ DB lookup failed: {e}")
        return None


# ── 5. main ────────────────────────────────────────────────────────────────────
async def main():
    parser = argparse.ArgumentParser(description="SahulatPay FCM push test")
    parser.add_argument("--token", help="FCM device token to push to directly")
    parser.add_argument("--phone", help="Look up token from DB by phone number")
    parser.add_argument("--title", default="🔔 Test Notification", help="Notification title")
    parser.add_argument("--body",  default="This is a test push from the SahulatPay backend!", help="Notification body")
    args = parser.parse_args()

    print("=" * 60)
    print("  SahulatPay FCM Push Notification Test")
    print("=" * 60)

    # Load .env
    try:
        from dotenv import load_dotenv
        load_dotenv(BASE_DIR / ".env")
        print("[env] .env loaded")
    except ImportError:
        pass

    # Init Firebase
    if not init_firebase():
        sys.exit(1)

    # Resolve token
    token = args.token
    if not token and args.phone:
        token = await get_token_from_db(args.phone)
    if not token:
        result = await get_any_token_from_db()
        if result:
            token, _ = result

    if not token:
        print("\n❌ No FCM token available. Options:")
        print("   --token <token>   pass token directly")
        print("   --phone 03xxxxxxx look up from DB")
        print("   Or ensure a user has logged in and registered their FCM token")
        sys.exit(1)

    # Send push
    print(f"\n[push] Sending test notification...")
    await send_test_push(token, args.title, args.body)
    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
