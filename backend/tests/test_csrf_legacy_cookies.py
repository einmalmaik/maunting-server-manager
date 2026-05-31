"""Regression-Tests fuer das CSRF-Verhalten nach der Pfad-Migration des Cookies.

Hintergrund: in einem frueheren Release lag das CSRF-Cookie unter Path=/api.
Browser, die damals eingeloggt waren, haben es noch und schicken es zusammen
mit dem aktuellen Cookie unter Path=/ mit. Beim Parsen auf Server-Seite kann
dadurch der jeweils andere Wert "gewinnen" und die Double-Submit-Pruefung
schlaegt fehl ("CSRF-Token ungueltig").

verify_csrf akzeptiert deshalb den Header-Wert, wenn er zu IRGENDEINEM der vom
Browser gesendeten CSRF-Cookies passt. Zusaetzlich loescht das Backend bei
jedem Login/Logout das Legacy-Cookie unter /api defensiv mit.
"""
from fastapi.testclient import TestClient

from models import User


class TestVerifyCsrfWithStaleLegacyCookie:
    """Wenn der Browser zwei CSRF-Cookies (legacy + neu) schickt, muss der
    Server akzeptieren, solange der Header-Wert zu einem davon passt.
    """

    def test_accepts_header_when_only_legacy_cookie_present(
        self, client: TestClient, owner_user: User, owner_cookies: dict
    ):
        csrf = owner_cookies["__Secure-csrf_token"]
        # Simuliere Browser, der nur das alte (legacy) CSRF-Cookie hat — der
        # Cookie-Header enthaelt es zweimal mit dem GLEICHEN Wert, der zum
        # Header passt. Realistisch: Browser sendet beide Cookies, beide mit
        # gleichem Wert (Token wurde mit dem letzten Login rotiert).
        response = client.post(
            "/api/servers",
            json={"name": "x", "game_type": "dayz"},
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf, "Cookie": f"__Secure-csrf_token={csrf}; __Secure-csrf_token={csrf}; __Secure-access_token={owner_cookies['__Secure-access_token']}"},
        )
        # Wir wollen NICHT 403 wegen CSRF — alles andere (201/500) ist hier ok.
        assert response.status_code != 403 or "CSRF" not in response.json().get("detail", "")

    def test_accepts_header_matching_any_of_multiple_csrf_cookies(
        self, client: TestClient, owner_user: User, owner_cookies: dict
    ):
        """Header passt zum NEUEN Cookie, ein veralteter Wert ist parallel da."""
        new_csrf = owner_cookies["__Secure-csrf_token"]
        old_csrf = "stale_legacy_value_that_no_longer_matches_anything"
        access = owner_cookies["__Secure-access_token"]
        cookie_header = f"__Secure-csrf_token={old_csrf}; __Secure-csrf_token={new_csrf}; __Secure-access_token={access}"
        response = client.post(
            "/api/servers",
            json={"name": "x", "game_type": "dayz"},
            cookies=owner_cookies,
            headers={"X-CSRF-Token": new_csrf, "Cookie": cookie_header},
        )
        assert response.status_code != 403 or "CSRF" not in response.json().get("detail", "")

    def test_accepts_header_matching_first_of_multiple_csrf_cookies(
        self, client: TestClient, owner_user: User, owner_cookies: dict
    ):
        """Auch wenn der Header zum ERSTEN (longest-path) Cookie passt, darf
        die Pruefung nicht am 'last wins'-Parsing von Starlette scheitern.
        """
        legacy_csrf = "legacy_path_csrf_value_abc123"
        access = owner_cookies["__Secure-access_token"]
        cookie_header = f"__Secure-csrf_token={legacy_csrf}; __Secure-csrf_token={owner_cookies['__Secure-csrf_token']}; __Secure-access_token={access}"
        response = client.post(
            "/api/servers",
            json={"name": "x", "game_type": "dayz"},
            cookies=owner_cookies,
            headers={"X-CSRF-Token": legacy_csrf, "Cookie": cookie_header},
        )
        # Header passt zum LEGACY-Cookie, das im Cookie-Header zuerst kommt.
        # Starlette wuerde naiv den letzten Wert lesen und 403 liefern.
        assert response.status_code != 403 or "CSRF" not in response.json().get("detail", "")

    def test_rejects_when_header_matches_no_cookie(
        self, client: TestClient, owner_user: User, owner_cookies: dict
    ):
        access = owner_cookies["__Secure-access_token"]
        csrf = owner_cookies["__Secure-csrf_token"]
        cookie_header = f"__Secure-csrf_token={csrf}; __Secure-access_token={access}"
        response = client.post(
            "/api/servers",
            json={"name": "x", "game_type": "dayz"},
            cookies=owner_cookies,
            headers={"X-CSRF-Token": "totally_different_value", "Cookie": cookie_header},
        )
        assert response.status_code == 403
        # Detail unterscheidet den Mismatch-Fall vom Missing-Fall.
        assert response.json()["detail"] == "CSRF-Token ungültig"

    def test_rejects_when_no_csrf_header(
        self, client: TestClient, owner_user: User, owner_cookies: dict
    ):
        response = client.post(
            "/api/servers",
            json={"name": "x", "game_type": "dayz"},
            cookies=owner_cookies,
            # kein X-CSRF-Token Header
        )
        assert response.status_code == 403
        # Eigener Fehlercode fuer Header-fehlt — hilft beim Frontend-Debugging.
        assert response.json()["detail"] == "CSRF-Header fehlt"

    def test_rejects_when_no_csrf_cookie_but_header_present(
        self, client: TestClient, owner_user: User, owner_cookies: dict
    ):
        # Header da, aber Cookie-Header enthaelt KEIN __Secure-csrf_token.
        access = owner_cookies["__Secure-access_token"]
        response = client.post(
            "/api/servers",
            json={"name": "x", "game_type": "dayz"},
            cookies={"__Secure-access_token": access},
            headers={"X-CSRF-Token": "some_value", "Cookie": f"__Secure-access_token={access}"},
        )
        assert response.status_code == 403
        assert response.json()["detail"] == "CSRF-Cookie fehlt"


class TestLoginClearsLegacyCsrfCookie:
    """Login schickt zusaetzlich Set-Cookie mit Max-Age=0 fuer den alten
    /api-Pfad, damit der Browser das Legacy-Cookie verwirft.
    """

    def test_login_emits_delete_for_legacy_path(self, client: TestClient, owner_user: User):
        response = client.post("/api/auth/login", json={
            "username": "owner",
            "password": "OwnerPass123!",
            "otp_code": None,
        })
        assert response.status_code == 200
        set_cookies = response.headers.get_list("set-cookie") if hasattr(response.headers, "get_list") else [
            v for k, v in response.headers.raw if k.decode().lower() == "set-cookie"
        ]
        # Mindestens ein Set-Cookie raeumt das Legacy-CSRF-Cookie unter Path=/api auf.
        legacy_clears = [c for c in set_cookies if "__Secure-csrf_token=" in c and "Path=/api" in c]
        assert legacy_clears, f"Expected a Set-Cookie clearing legacy /api csrf cookie, got: {set_cookies}"
