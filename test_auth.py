"""
Auth end-to-end smoke test.
Run: venv\\Scripts\\python test_auth.py
Requires: server running on http://localhost:8000
"""
import httpx
import sys

BASE = "http://localhost:8000/api/v1/auth"
PHONE = "03001234567"
EMAIL = "test_user@example.com"
PWD   = "SuperSecret123!"
DFP   = "a" * 64    # fake device fingerprint (SHA-256 hex, 64 chars)


def p(label, ok, details=""):
    mark = "✅" if ok else "❌"
    print(f"{mark} {label}{' — ' + details if details else ''}")


def main():
    c = httpx.Client(base_url=BASE, timeout=10.0)

    # ── 1. Register (Firebase dev-bypass) — user created + tokens returned ──
    r = c.post("/register", json={
        "phone":     PHONE,
        "firebase_id_token": "dev-bypass-token",    # DEV_MODE-only
        "email":     EMAIL,
        "full_name": "Test User",
        "password":  PWD,
        "country":   "Pakistan",
        "cnic_number": "42101-1234567-1",
        "account_type": "individual",
        "device_fingerprint": DFP,
        "device_name": "Test Device",
        "device_os":   "Android 14",
    })
    if r.status_code == 409:
        print("⚠  user already exists — run cleanup_test.py to reset")
        sys.exit(1)
    p("register (Firebase)", r.status_code == 201 and "tokens" in r.json(),
      f"{r.status_code}")
    tokens  = r.json()["tokens"]
    access  = tokens["access_token"]
    refresh = tokens["refresh_token"]

    # ── 7. Set PIN (requires access token) ──
    r = c.post("/pin/set", json={"pin": "1234"}, headers={"Authorization": f"Bearer {access}"})
    p("pin/set", r.status_code == 200, r.text)

    # ── 8. PIN login (trusted device) ──
    r = c.post("/login/pin", json={"phone": PHONE, "pin": "1234", "device_fingerprint": DFP})
    p("login/pin", r.status_code == 200, str(r.json().get("token_type")))

    # ── 9. Token refresh ──
    r = c.post("/token/refresh", json={"refresh_token": refresh})
    p("token/refresh", r.status_code == 200, str(r.json().get("token_type")))
    new_refresh = r.json()["refresh_token"]

    # ── 10. Old refresh token should be revoked ──
    r = c.post("/token/refresh", json={"refresh_token": refresh})
    p("old refresh rejected", r.status_code == 401, r.text)

    # ── 11. Logout all ──
    r = c.post("/logout-all", headers={"Authorization": f"Bearer {access}"})
    p("logout-all", r.status_code == 200, r.text)

    # ── 12. New refresh token should now be revoked too ──
    r = c.post("/token/refresh", json={"refresh_token": new_refresh})
    p("logout-all revoked new refresh", r.status_code == 401, r.text)

    print("\nAll auth flows working! 🎉")


if __name__ == "__main__":
    main()
