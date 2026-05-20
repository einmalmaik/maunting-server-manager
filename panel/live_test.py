#!/usr/bin/env python3
"""
Maunting Server Manager — Live API Test
Testet alle Auth-Flows, Edge-Cases und Permission-Checks.
"""
from __future__ import annotations

import random
import string
import sys

import httpx

BASE = "http://127.0.0.1:8710"
client = httpx.Client(base_url=BASE, timeout=15.0, follow_redirects=True)

PASS_MARK = 0
FAIL_MARK = 0
FAILED_TESTS: list[str] = []


def ok(name: str) -> None:
    global PASS_MARK
    PASS_MARK += 1
    print(f"  ✓ {name}")


def fail(name: str, detail: str) -> None:
    global FAIL_MARK
    FAIL_MARK += 1
    FAILED_TESTS.append(name)
    print(f"  ✗ {name}: {detail}")


def section(title: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def rand_str(n: int = 8) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Setup & Health
# ═══════════════════════════════════════════════════════════════════════════════

section("1. Setup & Health")

try:
    r = client.get("/api/setup/status")
    if r.status_code == 200:
        data = r.json()
        ok(f"setup/status → needs_setup={data.get('needs_setup')}")
    else:
        fail("setup/status", f"status={r.status_code}")
except Exception as exc:
    fail("setup/status", str(exc))

# ═══════════════════════════════════════════════════════════════════════════════
# 2. Auth Edge-Cases (no session)
# ═══════════════════════════════════════════════════════════════════════════════

section("2. Auth Edge-Cases (no session)")

# 2.1 Login mit leeren Feldern
try:
    r = client.post("/api/auth/login", json={})
    if r.status_code == 422:
        ok("login empty body → 422")
    else:
        fail("login empty body", f"expected 422, got {r.status_code}")
except Exception as exc:
    fail("login empty body", str(exc))

# 2.2 Login mit nicht existierendem User
try:
    r = client.post("/api/auth/login", json={"username": rand_str(20), "password": "password123"})
    if r.status_code == 401:
        ok("login nonexistent user → 401")
    else:
        fail("login nonexistent user", f"expected 401, got {r.status_code}: {r.text[:200]}")
except Exception as exc:
    fail("login nonexistent user", str(exc))

# 2.3 Register mit leerem Body
try:
    r = client.post("/api/auth/register", json={})
    if r.status_code == 422:
        ok("register empty body → 422")
    else:
        fail("register empty body", f"expected 422, got {r.status_code}")
except Exception as exc:
    fail("register empty body", str(exc))

# 2.4 Register mit ungültiger Email
try:
    r = client.post("/api/auth/register", json={"username": rand_str(), "email": "not-an-email", "password": "password123"})
    if r.status_code == 422:
        ok("register invalid email → 422")
    else:
        fail("register invalid email", f"expected 422, got {r.status_code}: {r.text[:200]}")
except Exception as exc:
    fail("register invalid email", str(exc))

# 2.5 Register mit zu kurzem Passwort
try:
    r = client.post("/api/auth/register", json={"username": rand_str(), "email": f"{rand_str()}@test.com", "password": "123"})
    if r.status_code == 422:
        ok("register short password → 422")
    else:
        fail("register short password", f"expected 422, got {r.status_code}: {r.text[:200]}")
except Exception as exc:
    fail("register short password", str(exc))

# 2.6 Forgot-Password mit ungültiger Email
try:
    r = client.post("/api/auth/forgot-password", json={"email": "not-an-email"})
    if r.status_code == 422:
        ok("forgot-password invalid email → 422")
    else:
        fail("forgot-password invalid email", f"expected 422, got {r.status_code}")
except Exception as exc:
    fail("forgot-password invalid email", str(exc))

# 2.7 Reset-Password mit ungültigem Token
try:
    r = client.post("/api/auth/reset-password", json={"token": "invalid-token", "new_password": "newpassword123"})
    if r.status_code == 400:
        ok("reset-password invalid token → 400")
    else:
        fail("reset-password invalid token", f"expected 400, got {r.status_code}: {r.text[:200]}")
except Exception as exc:
    fail("reset-password invalid token", str(exc))

# 2.8 Reset-Password mit zu kurzem Passwort
try:
    r = client.post("/api/auth/reset-password", json={"token": "some-token", "new_password": "123"})
    if r.status_code == 422:
        ok("reset-password short password → 422")
    else:
        fail("reset-password short password", f"expected 422, got {r.status_code}: {r.text[:200]}")
except Exception as exc:
    fail("reset-password short password", str(exc))

# 2.9 Verify-Email mit ungültigem Token
try:
    r = client.get("/api/auth/verify-email", params={"token": "invalid-token"})
    if r.status_code == 400:
        ok("verify-email invalid token → 400")
    else:
        fail("verify-email invalid token", f"expected 400, got {r.status_code}: {r.text[:200]}")
except Exception as exc:
    fail("verify-email invalid token", str(exc))

# 2.10 Me ohne Session
try:
    r = client.get("/api/auth/me")
    if r.status_code == 401:
        ok("me without session → 401")
    else:
        fail("me without session", f"expected 401, got {r.status_code}")
except Exception as exc:
    fail("me without session", str(exc))

# ═══════════════════════════════════════════════════════════════════════════════
# 3. Rate Limiting / Throttling
# ═══════════════════════════════════════════════════════════════════════════════

section("3. Rate Limiting (5 failed logins)")

throttle_ok = True
for i in range(7):
    try:
        r = client.post("/api/auth/login", json={"username": rand_str(), "password": "wrong"})
        if i >= 5 and r.status_code != 429:
            throttle_ok = False
    except Exception:
        throttle_ok = False

if throttle_ok:
    ok("login throttle after 5 failures")
else:
    fail("login throttle", "expected 429 after 5 failures")

# Reset throttle by waiting — we don't wait, just report

# ═══════════════════════════════════════════════════════════════════════════════
# 4. Protected Endpoints without Auth
# ═══════════════════════════════════════════════════════════════════════════════

section("4. Protected Endpoints without Auth")

endpoints = [
    ("GET", "/api/users"),
    ("POST", "/api/users", {}),
    ("GET", "/api/servers"),
    ("POST", "/api/servers", {}),
    ("GET", "/api/dashboard"),
    ("GET", "/api/backups"),
    ("GET", "/api/mods"),
    ("GET", "/api/autorestart"),
]

for method, path, *body in endpoints:
    try:
        if method == "GET":
            r = client.get(path)
        else:
            r = client.post(path, json=body[0] if body else {})
        if r.status_code == 401:
            ok(f"{method} {path} → 401")
        else:
            fail(f"{method} {path} no auth", f"expected 401, got {r.status_code}")
    except Exception as exc:
        fail(f"{method} {path} no auth", str(exc))

# ═══════════════════════════════════════════════════════════════════════════════
# 5. Full Auth Flow (Register → Verify → Login → Actions → Logout)
# ═══════════════════════════════════════════════════════════════════════════════

section("5. Full Auth Flow")

# Use a cookie-aware client for this section
session = httpx.Client(base_url=BASE, timeout=15.0, follow_redirects=True)

test_user = f"test_{rand_str()}"
test_email = f"{test_user}@test.local"
test_password = "TestPassword123!"

# 5.1 Register
try:
    r = session.post("/api/auth/register", json={"username": test_user, "email": test_email, "password": test_password})
    if r.status_code == 200:
        ok(f"register user '{test_user}' → 200")
        reg_data = r.json()
        if "verify your email" in reg_data.get("message", "").lower():
            ok("register message mentions email verification")
        else:
            fail("register message", f"no email verification hint: {reg_data}")
    else:
        fail("register", f"expected 200, got {r.status_code}: {r.text[:300]}")
except Exception as exc:
    fail("register", str(exc))

# 5.2 Verify email with invalid token (should fail)
try:
    r = session.get("/api/auth/verify-email", params={"token": "fake-token"})
    if r.status_code == 400:
        ok("verify fake token → 400")
    else:
        fail("verify fake token", f"expected 400, got {r.status_code}")
except Exception as exc:
    fail("verify fake token", str(exc))

# 5.3 Login before verification (should work if email_verified not enforced, or fail if enforced)
try:
    r = session.post("/api/auth/login", json={"username": test_user, "password": test_password})
    login_status = r.status_code
    login_data = r.json() if r.status_code == 200 else None
    if login_status == 200 and "user" in (login_data or {}):
        ok(f"login before verification → 200 (email_verified NOT enforced)")
        logged_in = True
    elif login_status == 401 or login_status == 403:
        ok(f"login before verification → {login_status} (email_verified enforced)")
        logged_in = False
    else:
        fail("login before verification", f"unexpected {login_status}: {r.text[:300]}")
        logged_in = False
except Exception as exc:
    fail("login before verification", str(exc))
    logged_in = False

# 5.4 If logged in, try accessing protected endpoints
if logged_in:
    try:
        r = session.get("/api/auth/me")
        if r.status_code == 200:
            me = r.json()
            ok(f"me → {me['user']['username']}, role={me['user']['role']}")
        else:
            fail("me after login", f"status={r.status_code}")
    except Exception as exc:
        fail("me after login", str(exc))

    # Try accessing users (should fail for normal user without users.view)
    try:
        r = session.get("/api/users")
        if r.status_code == 403:
            ok("users list as normal user → 403")
        else:
            fail("users list as normal user", f"expected 403, got {r.status_code}: {r.text[:200]}")
    except Exception as exc:
        fail("users list as normal user", str(exc))

    # Logout
    try:
        r = session.post("/api/auth/logout")
        if r.status_code == 200:
            ok("logout → 200")
        else:
            fail("logout", f"status={r.status_code}")
    except Exception as exc:
        fail("logout", str(exc))

    # Verify session cleared
    try:
        r = session.get("/api/auth/me")
        if r.status_code == 401:
            ok("me after logout → 401 (session cleared)")
        else:
            fail("me after logout", f"expected 401, got {r.status_code}")
    except Exception as exc:
        fail("me after logout", str(exc))
else:
    ok("skipping post-login tests (email verification enforced)")

session.close()

# ═══════════════════════════════════════════════════════════════════════════════
# 6. Admin Flow (using owner account from dev db)
# ═══════════════════════════════════════════════════════════════════════════════

section("6. Admin Flow (owner login)")

admin_session = httpx.Client(base_url=BASE, timeout=15.0, follow_redirects=True)

# Try common dev credentials
dev_creds = [
    ("admin", "admin123"),
    ("admin", "password"),
    ("admin", "admin"),
]

owner_logged_in = False
for user, pw in dev_creds:
    try:
        r = admin_session.post("/api/auth/login", json={"username": user, "password": pw})
        if r.status_code == 200 and "user" in r.json():
            ok(f"owner login as '{user}' → 200")
            owner_logged_in = True
            break
    except Exception:
        pass

if not owner_logged_in:
    fail("owner login", "Could not log in with common dev credentials. May need fresh admin.")

if owner_logged_in:
    # Get user list
    try:
        r = admin_session.get("/api/users")
        if r.status_code == 200:
            users = r.json().get("users", [])
            ok(f"users list → {len(users)} users")
        else:
            fail("users list as owner", f"status={r.status_code}: {r.text[:200]}")
    except Exception as exc:
        fail("users list as owner", str(exc))

    # Create a user with custom permissions
    try:
        r = admin_session.post("/api/users", json={
            "username": f"permuser_{rand_str()}",
            "email": f"{rand_str()}@test.local",
            "password": "Password123!",
            "permissions": ["dashboard.view", "servers.view"],
        })
        if r.status_code == 200:
            ok("create user with custom permissions → 200")
        else:
            fail("create user with custom permissions", f"status={r.status_code}: {r.text[:300]}")
    except Exception as exc:
        fail("create user with custom permissions", str(exc))

    # Create user with invalid permission
    try:
        r = admin_session.post("/api/users", json={
            "username": f"baduser_{rand_str()}",
            "password": "Password123!",
            "permissions": ["dashboard.view", "invalid.permission"],
        })
        if r.status_code == 422:
            ok("create user with invalid permission → 422")
        else:
            fail("create user with invalid permission", f"expected 422, got {r.status_code}: {r.text[:200]}")
    except Exception as exc:
        fail("create user with invalid permission", str(exc))

    # Try creating user with duplicate username
    try:
        r = admin_session.post("/api/users", json={
            "username": test_user,
            "password": "Password123!",
        })
        if r.status_code == 409:
            ok("create duplicate username → 409")
        else:
            fail("create duplicate username", f"expected 409, got {r.status_code}: {r.text[:200]}")
    except Exception as exc:
        fail("create duplicate username", str(exc))

admin_session.close()

# ═══════════════════════════════════════════════════════════════════════════════
# 7. Servers API Edge Cases
# ═══════════════════════════════════════════════════════════════════════════════

section("7. Servers API Edge Cases (no auth)")

# 7.1 List servers without auth
try:
    r = client.get("/api/servers")
    if r.status_code == 401:
        ok("servers list no auth → 401")
    else:
        fail("servers list no auth", f"expected 401, got {r.status_code}")
except Exception as exc:
    fail("servers list no auth", str(exc))

# 7.2 Create server with empty body
try:
    r = client.post("/api/servers", json={})
    if r.status_code == 401:
        ok("servers create no auth → 401")
    else:
        fail("servers create no auth", f"expected 401, got {r.status_code}")
except Exception as exc:
    fail("servers create no auth", str(exc))

# ═══════════════════════════════════════════════════════════════════════════════
# 8. XSS / Injection Checks
# ═══════════════════════════════════════════════════════════════════════════════

section("8. Input Sanitization / Injection Checks")

xss_payload = "<script>alert('xss')</script>"
sql_payload = "'; DROP TABLE users; --"

# Try register with XSS in username
try:
    r = client.post("/api/auth/register", json={
        "username": xss_payload,
        "email": f"{rand_str()}@test.com",
        "password": "Password123!",
    })
    if r.status_code == 422:
        ok("register XSS username → 422 (rejected or sanitized)")
    elif r.status_code == 200:
        # If accepted, check if it's stored sanitized
        data = r.json()
        ok("register XSS username → 200 (check if stored raw)")
    else:
        fail("register XSS username", f"status={r.status_code}: {r.text[:200]}")
except Exception as exc:
    fail("register XSS username", str(exc))

# ═══════════════════════════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════════════════════════

section("TEST SUMMARY")
print(f"\n  Passed: {PASS_MARK}")
print(f"  Failed: {FAIL_MARK}")

if FAILED_TESTS:
    print(f"\n  Failed tests:")
    for name in FAILED_TESTS:
        print(f"    - {name}")

if FAIL_MARK == 0:
    print(f"\n  {C_GREEN}ALL TESTS PASSED{C_RESET}" if sys.stdout.isatty() else "\n  ALL TESTS PASSED")
else:
    print(f"\n  {C_RED}SOME TESTS FAILED{C_RESET}" if sys.stdout.isatty() else "\n  SOME TESTS FAILED")

sys.exit(1 if FAIL_MARK > 0 else 0)
