"""
Test the REAL Firebase Phone Auth path end-to-end, without a frontend.

Prerequisites (one-time):
1. Firebase Console → Authentication → Sign-in method → Phone → ENABLE
2. Same page → scroll to "Phone numbers for testing" → add:
      Phone:  +923001234567
      Code:   123456
3. Firebase Console → Project Settings → General → copy Web API Key
4. Set env var FIREBASE_WEB_API_KEY=AIzaSy... (either in .env or exported)

How it works:
- Calls Firebase Auth REST API to "sign in" with the test phone
- Firebase returns a real ID token (with sign_in_provider=phone)
- We send that ID token to YOUR backend /auth/register
- Backend calls firebase_admin.verify_id_token() → accepts → creates user
"""
import os, sys, httpx

FIREBASE_API_KEY = os.getenv("FIREBASE_WEB_API_KEY")
if not FIREBASE_API_KEY:
    print("❌ Set FIREBASE_WEB_API_KEY env var first")
    sys.exit(1)

TEST_PHONE = "+923001234567"
TEST_CODE  = "123456"
BACKEND    = "http://localhost:8000/api/v1/auth"

# Step 1 — Request OTP (Firebase returns sessionInfo; no SMS sent for test numbers)
print("→ Requesting Firebase OTP for test phone...")
r = httpx.post(
    f"https://identitytoolkit.googleapis.com/v1/accounts:sendVerificationCode?key={FIREBASE_API_KEY}",
    json={"phoneNumber": TEST_PHONE, "recaptchaToken": "ignored-for-test-numbers"},
)
if r.status_code != 200:
    print(f"❌ sendVerificationCode failed: {r.status_code} {r.text}")
    sys.exit(1)
session_info = r.json()["sessionInfo"]
print(f"✅ got sessionInfo: {session_info[:20]}...")

# Step 2 — Submit the fixed test code → Firebase returns an ID token
print("→ Submitting test OTP...")
r = httpx.post(
    f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPhoneNumber?key={FIREBASE_API_KEY}",
    json={"sessionInfo": session_info, "code": TEST_CODE},
)
if r.status_code != 200:
    print(f"❌ signInWithPhoneNumber failed: {r.status_code} {r.text}")
    sys.exit(1)
id_token = r.json()["idToken"]
print(f"✅ got Firebase ID token: {id_token[:40]}...")

# Step 3 — Send it to YOUR backend /auth/register
print("→ Calling backend /auth/register with real Firebase token...")
r = httpx.post(f"{BACKEND}/register", json={
    "phone": "03001234567",
    "firebase_id_token": id_token,
    "email": "firebase_real@test.com",
    "full_name": "Firebase Real Test",
    "password": "RealTest1234!",
    "country": "Pakistan",
    "account_type": "individual",
    "device_fingerprint": "f" * 64,
    "device_name": "Firebase Test",
    "device_os": "Python REST",
})
print(f"Backend responded: {r.status_code}")
print(r.json())
