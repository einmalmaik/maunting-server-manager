"""Dual-Auth für native Clients (Smart System): Bearer neben Cookie.

Die Invarianten dieses Moduls:

1. Der Browser-Flow bleibt unangetastet: Login ohne `native_client` liefert
   leere Tokens im Body und funktioniert ausschliesslich über Cookies + CSRF.
2. Ein nativer Client bekommt seine Tokens im Body (`native_client=True`),
   authentifiziert per `Authorization: Bearer` und ist vom Cookie-CSRF befreit
   — aber nur mit einem gültig signierten Access-Token.
3. Bearer-Tokens laufen durch dieselbe Validierung wie Cookies: jti-Pflicht
   und Blacklist gelten, ein Logout widerruft sie wirklich.
4. Refresh und Logout funktionieren für native Clients über Body/Header und
   nutzen dieselbe Familienrotation wie der Cookie-Weg.
"""

from fastapi.testclient import TestClient

from models import User


def _native_login(client: TestClient, username: str = "user1", password: str = "UserPass123!") -> dict:
    """Login als nativer Client: Tokens aus dem Body, Cookies verworfen."""
    resp = client.post("/api/auth/login", json={
        "username": username,
        "password": password,
        "otp_code": None,
        "native_client": True,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"], "nativer Login muss ein Access-Token liefern"
    assert body["refresh_token"], "nativer Login muss ein Refresh-Token liefern"
    assert body["expires_in"] > 0
    # Cookies verwerfen: ab hier beweist nur noch der Header die Identität —
    # genau die Lage der Desktop-App, die keine httponly-Cookies verwalten kann.
    client.cookies.clear()
    return body


def _bearer(body: dict) -> dict:
    return {"Authorization": f"Bearer {body['access_token']}"}


class TestNativeLogin:
    def test_browser_login_bekommt_keine_tokens_im_body(self, client: TestClient, regular_user: User):
        resp = client.post("/api/auth/login", json={
            "username": "user1", "password": "UserPass123!", "otp_code": None,
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["access_token"] == ""
        assert body.get("refresh_token", "") == ""

    def test_native_login_authentifiziert_per_bearer(self, client: TestClient, regular_user: User):
        body = _native_login(client)
        me = client.get("/api/auth/me", headers=_bearer(body))
        assert me.status_code == 200
        assert me.json()["username"] == "user1"

    def test_ohne_cookie_und_ohne_bearer_401(self, client: TestClient, regular_user: User):
        assert client.get("/api/auth/me").status_code == 401

    def test_muell_als_bearer_401(self, client: TestClient, regular_user: User):
        resp = client.get("/api/auth/me", headers={"Authorization": "Bearer kaputt"})
        assert resp.status_code == 401


class TestBearerCsrf:
    def test_bearer_ist_vom_cookie_csrf_befreit(self, client: TestClient, regular_user: User):
        body = _native_login(client)
        resp = client.patch(
            "/api/auth/me/timezone",
            json={"time_zone": "Europe/Berlin"},
            headers=_bearer(body),
        )
        assert resp.status_code == 200
        assert resp.json()["time_zone"] == "Europe/Berlin"

    def test_cookie_ohne_csrf_header_bleibt_403(self, client: TestClient, regular_user: User, user_cookies: dict):
        # Der Browser-Flow verliert durch den Bearer-Pfad nichts von seinem
        # CSRF-Schutz: Cookie-Session ohne X-CSRF-Token wird abgelehnt.
        resp = client.patch(
            "/api/auth/me/timezone",
            json={"time_zone": "Europe/Berlin"},
            cookies=user_cookies,
        )
        assert resp.status_code == 403

    def test_kaputter_bearer_befreit_nicht(self, client: TestClient, regular_user: User, user_cookies: dict):
        # Cookie-Session vorhanden, dazu ein Header mit Müll: der darf weder
        # vom CSRF befreien noch authentifizieren. Je nach Dependency-Reihenfolge
        # antwortet der Bearer-Pfad mit 401 oder der CSRF-Schutz mit 403 —
        # niemals 2xx.
        resp = client.patch(
            "/api/auth/me/timezone",
            json={"time_zone": "Europe/Berlin"},
            headers={"Authorization": "Bearer kaputt"},
            cookies=user_cookies,
        )
        assert resp.status_code in (401, 403)


class TestNativeRefresh:
    def test_body_refresh_liefert_neue_tokens(self, client: TestClient, regular_user: User):
        body = _native_login(client)
        resp = client.post("/api/auth/refresh", json={"refresh_token": body["refresh_token"]})
        client.cookies.clear()
        assert resp.status_code == 200
        neu = resp.json()
        assert neu["access_token"] and neu["access_token"] != body["access_token"]
        assert neu["refresh_token"] and neu["refresh_token"] != body["refresh_token"]
        me = client.get("/api/auth/me", headers=_bearer(neu))
        assert me.status_code == 200

    def test_altes_refresh_token_ist_nach_rotation_verbrannt(self, client: TestClient, regular_user: User):
        body = _native_login(client)
        first = client.post("/api/auth/refresh", json={"refresh_token": body["refresh_token"]})
        client.cookies.clear()
        assert first.status_code == 200
        again = client.post("/api/auth/refresh", json={"refresh_token": body["refresh_token"]})
        assert again.status_code == 401

    def test_cookie_refresh_bekommt_keine_tokens_im_body(self, client: TestClient, regular_user: User, user_cookies: dict):
        resp = client.post("/api/auth/refresh", cookies=user_cookies)
        assert resp.status_code == 200
        assert "access_token" not in resp.json()


class TestNativeLogout:
    def test_logout_widerruft_access_und_refresh(self, client: TestClient, regular_user: User):
        body = _native_login(client)
        resp = client.post(
            "/api/auth/logout",
            json={"refresh_token": body["refresh_token"]},
            headers=_bearer(body),
        )
        assert resp.status_code == 200
        client.cookies.clear()
        # Access-Token steht auf der Blacklist (jti-Widerruf) ...
        me = client.get("/api/auth/me", headers=_bearer(body))
        assert me.status_code == 401
        # ... und das Refresh-Token ist revoziert.
        again = client.post("/api/auth/refresh", json={"refresh_token": body["refresh_token"]})
        assert again.status_code == 401
