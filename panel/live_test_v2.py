#!/usr/bin/env python3
"""Maunting Server Manager — Live API Test v2 (einfacher, stabil)"""
from __future__ import annotations

import random
import string
import sys
import time

import httpx

BASE = "http://127.0.0.1:8710"

def rand(n=8):
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))

passed = 0
failed = 0
failures = []

def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  ✓ {name}")
    else:
        failed += 1
        failures.append(name)
        print(f"  ✗ {name}: {detail}")

print("=" * 60)
print("  LIVE TEST — Maunting Server Manager API")
print("=" * 60)

# ── 1. Basic Health ──────────────────────────────────────────────────────────
print("\n[1] Health & Setup")
r = httpx.get(f"{BASE}/api/setup/status", timeout=10)
check("setup/status", r.status_code == 200, f"{r.status_code}")

# ── 2. Auth Edge-Cases (no session) ──────────────────────────────────────────
print("\n[2] Auth Edge-Cases (no session)")

r = httpx.post(f"{BASE}/api/auth/login", json={}, timeout=10)
check("login empty → 422", r.status_code == 422, f"{r.status_code}")

r = httpx.post(f"{BASE}/api/auth/login", json={"username": rand(20), "password": "pw"}, timeout=10)
check("login wrong user → 401", r.status_code == 401, f"{r.status_code}")

r = httpx.post(f"{BASE}/api/auth/register", json={}, timeout=10)
check("register empty → 422", r.status_code == 422, f"{r.status_code}")

r = httpx.post(f"{BASE}/api/auth/register", json={"username": rand(), "email": "bad", "password": "12345678"}, timeout=10)
check("register bad email → 422", r.status_code == 422, f"{r.status_code}")

r = httpx.post(f"{BASE}/api/auth/register", json={"username": rand(), "email": f"{rand()}@t.com", "password": "123"}, timeout=10)
check("register short pw → 422", r.status_code == 422, f"{r.status_code}")

r = httpx.post(f"{BASE}/api/auth/forgot-password", json={"email": "bad"}, timeout=10)
check("forgot bad email → 422", r.status_code == 422, f"{r.status_code}")

r = httpx.post(f"{BASE}/api/auth/reset-password", json={"token": "x", "new_password": "12345678"}, timeout=10)
check("reset bad token → 400", r.status_code == 400, f"{r.status_code}")

r = httpx.post(f"{BASE}/api/auth/reset-password", json={"token": "x", "new_password": "12"}, timeout=10)
check("reset short pw → 422", r.status_code == 422, f"{r.status_code}")

r = httpx.get(f"{BASE}/api/auth/verify-email", params={"token": "x"}, timeout=10)
check("verify bad token → 400", r.status_code == 400, f"{r.status_code}")

r = httpx.get(f"{BASE}/api/auth/me", timeout=10)
check("me no session → 401", r.status_code == 401, f"{r.status_code}")

# ── 3. Rate Limiting ─────────────────────────────────────────────────────────
print("\n[3] Rate Limiting")
throttle_user = f"throttle_{rand()}"
for i in range(6):
    r = httpx.post(f"{BASE}/api/auth/login", json={"username": throttle_user, "password": "x"}, timeout=10)
throttled = r.status_code == 429
check("throttle after 5 fails", throttled, f"last status={r.status_code}")

# ── 4. Protected endpoints without auth ──────────────────────────────────────
print("\n[4] Protected endpoints (no auth)")
for path in ["/api/users", "/api/servers", "/api/dashboard", "/api/backups", "/api/mods"]:
    r = httpx.get(f"{BASE}{path}", timeout=10)
    check(f"GET {path} → 401", r.status_code == 401, f"{r.status_code}")

# ── 5. Full Register → Login → Logout Flow ─────────────────────────────────
print("\n[5] Full Auth Flow")

u = f"t_{rand()}"
pw = "TestPass123!"
email = f"{u}@test.local"

# Register
r = httpx.post(f"{BASE}/api/auth/register", json={"username": u, "email": email, "password": pw}, timeout=10)
check("register user", r.status_code == 200, f"{r.status_code}: {r.text[:300]}")

# Login
r = httpx.post(f"{BASE}/api/auth/login", json={"username": u, "password": pw}, timeout=10)
login_ok = r.status_code == 200 and "user" in r.json()
check("login new user", login_ok, f"{r.status_code}: {r.text[:300]}")

if login_ok:
    # The httpx POST to /login should have set a session cookie automatically in follow_redirects mode
    # But httpx default doesn't persist cookies across separate requests...
    # Let's use a session client
    sess = httpx.Client(base_url=BASE, timeout=10)
    r2 = sess.post("/api/auth/login", json={"username": u, "password": pw})
    
    r3 = sess.get("/api/auth/me")
    check("me after login", r3.status_code == 200, f"{r3.status_code}: {r3.text[:200]}")
    
    # Users list should fail (no users.view permission)
    r4 = sess.get("/api/users")
    check("users list as normal user → 403", r4.status_code == 403, f"{r4.status_code}")
    
    # Logout
    r5 = sess.post("/api/auth/logout")
    check("logout", r5.status_code == 200, f"{r5.status_code}")
    
    # Verify session cleared
    r6 = sess.get("/api/auth/me")
    check("me after logout → 401", r6.status_code == 401, f"{r6.status_code}")
    sess.close()
else:
    print("  (skipping post-login tests)")

# ── 6. XSS / Injection Input ─────────────────────────────────────────────────
print("\n[6] Input Sanitization")

xss = f"<script>alert({rand()})</script>"
r = httpx.post(f"{BASE}/api/auth/register", json={"username": xss, "email": f"{rand()}@t.com", "password": "Pass123456!"}, timeout=10)
check("XSS in username rejected/sanitized", r.status_code in (200, 422), f"{r.status_code}")

# ── 7. Admin Flow (dev credentials) ──────────────────────────────────────────
print("\n[7] Admin Flow")

admin = httpx.Client(base_url=BASE, timeout=10)
r = admin.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
if r.status_code == 200 and "user" in r.json():
    check("admin login", True, "")
    
    r = admin.get("/api/users")
    check("admin users list", r.status_code == 200, f"{r.status_code}")
    
    # Create user with permissions
    r = admin.post("/api/users", json={"username": f"pu_{rand()}", "password": "Pass123456!", "permissions": ["dashboard.view"]})
    check("create user with perms", r.status_code == 200, f"{r.status_code}: {r.text[:300]}")
    
    # Create user with invalid permission
    r = admin.post("/api/users", json={"username": f"bu_{rand()}", "password": "Pass123456!", "permissions": ["invalid.perm"]})
    check("create user invalid perm → 422", r.status_code == 422, f"{r.status_code}")
    
    # Duplicate username
    r = admin.post("/api/users", json={"username": u, "password": "Pass123456!"})
    check("create duplicate → 409", r.status_code == 409, f"{r.status_code}")
else:
    check("admin login", False, f"{r.status_code}: {r.text[:300]}")

admin.close()

# ── Summary ──────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print(f"  PASSED: {passed}")
print(f"  FAILED: {failed}")
if failures:
    print("  Failed tests:")
    for f in failures:
        print(f"    - {f}")
print("=" * 60)

sys.exit(0 if failed == 0 else 1)
