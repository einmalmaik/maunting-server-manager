"""Regression-Tests fuer die Konsistenz der Cookie-Domain.

Hintergrund: das OAuth-State-Cookie wird seit dem Domain-Refactor mit der
Parent-Domain aus ``get_effective_cookie_domain()`` gesetzt (z. B. Domain=
.mauntingstudios.de fuer panel.mauntingstudios.de). Vorher waren die
Auth-Cookies (access/refresh/csrf) host-only. Diese Asymmetrie konnte
browserseitig zu subtilen Mismatch-Bugs fuehren (Cookie beim IdP-Callback
nicht mitgeschickt, oauth_state_mismatch). Beide Cookietypen muessen jetzt
konsistente Domain-Attribute tragen.
"""
from fastapi import Response
from fastapi.testclient import TestClient

from cookies import (
    _set_cookie,
    _clear_auth_cookies,
    _set_auth_cookies,
    cookie_name,
    dev_http_cookies,
    lies_zugangstoken,
)
import config


class TestAuthCookiesShareDomainWithStateCookie:
    """Auth-Cookies muessen die gleiche Domain bekommen wie das OAuth-State-Cookie."""

    def test_access_token_inherits_effective_cookie_domain(self, monkeypatch):
        # panel_url auf nicht-loopback Host zwingen
        monkeypatch.setattr(config.settings, "panel_url", "https://panel.example.com", raising=False)
        monkeypatch.setattr(config.settings, "cookie_domain", "", raising=False)
        resp = Response()
        _set_cookie(resp, "__Secure-access_token", "tok123", max_age=600)
        set_cookie_headers = [
            v for k, v in resp.headers.raw
            if k.lower() == b"set-cookie" and b"__Secure-access_token" in v
        ]
        assert set_cookie_headers, "Set-Cookie fehlt"
        joined = b" ".join(set_cookie_headers).decode()
        # Erwartet: Domain=.example.com (Parent-Domain), nicht host-only.
        assert "Domain=.example.com" in joined, (
            f"Auth-Cookie muss Domain=.example.com haben, header war: {joined!r}"
        )

    def test_refresh_token_inherits_effective_cookie_domain(self, monkeypatch):
        monkeypatch.setattr(config.settings, "panel_url", "https://panel.example.com", raising=False)
        monkeypatch.setattr(config.settings, "cookie_domain", "", raising=False)
        resp = Response()
        _set_cookie(resp, "__Secure-refresh_token", "rtok", max_age=600)
        set_cookie_headers = [
            v for k, v in resp.headers.raw
            if k.lower() == b"set-cookie" and b"__Secure-refresh_token" in v
        ]
        joined = b" ".join(set_cookie_headers).decode()
        assert "Domain=.example.com" in joined, (
            f"Refresh-Cookie muss Domain=.example.com haben, header war: {joined!r}"
        )

    def test_csrf_token_inherits_effective_cookie_domain(self, monkeypatch):
        monkeypatch.setattr(config.settings, "panel_url", "https://panel.example.com", raising=False)
        monkeypatch.setattr(config.settings, "cookie_domain", "", raising=False)
        resp = Response()
        _set_cookie(resp, "__Secure-csrf_token", "csrf", max_age=600)
        set_cookie_headers = [
            v for k, v in resp.headers.raw
            if k.lower() == b"set-cookie" and b"__Secure-csrf_token" in v
        ]
        joined = b" ".join(set_cookie_headers).decode()
        assert "Domain=.example.com" in joined, (
            f"CSRF-Cookie muss Domain=.example.com haben, header war: {joined!r}"
        )

    def test_localhost_remains_host_only(self, monkeypatch):
        """Loopback darf NIEMALS eine Domain bekommen — TestClient & lokale
        Browser-Instanzen wuerden das Set-Cookie sonst verwerfen."""
        monkeypatch.setattr(config.settings, "panel_url", "http://localhost:3000", raising=False)
        monkeypatch.setattr(config.settings, "cookie_domain", "", raising=False)
        resp = Response()
        _set_cookie(resp, "__Secure-access_token", "tok", max_age=600)
        set_cookie_headers = [
            v for k, v in resp.headers.raw
            if k.lower() == b"set-cookie" and b"__Secure-access_token" in v
        ]
        joined = b" ".join(set_cookie_headers).decode()
        assert "Domain=" not in joined, (
            f"Localhost-Cookies bleiben host-only, header war: {joined!r}"
        )

    def test_split_hosting_uses_api_domain_not_frontend_domain(self, monkeypatch):
        """A response from api.example.com cannot set cookies for vercel.app."""
        monkeypatch.setattr(
            config.settings,
            "panel_url",
            "https://my-panel.vercel.app",
            raising=False,
        )
        monkeypatch.setattr(
            config.settings,
            "api_url",
            "https://api.example.com",
            raising=False,
        )
        monkeypatch.setattr(config.settings, "cookie_domain", "", raising=False)
        resp = Response()
        _set_cookie(resp, "__Secure-access_token", "tok", max_age=600)
        joined = b" ".join(
            value
            for key, value in resp.headers.raw
            if key.lower() == b"set-cookie" and b"__Secure-access_token" in value
        ).decode()
        assert "Domain=.example.com" in joined
        assert "vercel.app" not in joined

    def test_clear_auth_cookies_uses_same_domain(self, monkeypatch):
        """Beim Logout / Cookie-Clear muss die Domain mit der Set-Variante
        uebereinstimmen, sonst verwirft der Browser das Delete und der
        'Phantom-Cookie'-Effekt tritt auf."""
        monkeypatch.setattr(config.settings, "panel_url", "https://panel.example.com", raising=False)
        monkeypatch.setattr(config.settings, "cookie_domain", "", raising=False)
        resp = Response()
        _clear_auth_cookies(resp)
        # Set-Cookie-Header mit Max-Age=0 (Delete) muessen ebenfalls Domain= haben
        delete_headers = [
            v for k, v in resp.headers.raw
            if k.lower() == b"set-cookie"
            and any(name in v for name in (b"__Secure-access_token", b"__Secure-refresh_token", b"__Secure-csrf_token"))
        ]
        assert delete_headers, "Loesch-Header fehlen"
        for header in delete_headers:
            text = header.decode()
            assert "Domain=.example.com" in text, (
                f"Delete-Cookie ohne Domain matcht das gesetzte Cookie nicht: {text!r}"
            )

    def test_explicit_cookie_domain_override_takes_precedence(self, monkeypatch):
        """Wenn der Admin MSM_COOKIE_DOMAIN explizit setzt, gewinnt dieser
        Wert — auch wenn er von panel_url abweicht (Reverse-Proxy-Setups)."""
        monkeypatch.setattr(config.settings, "panel_url", "https://panel.example.com", raising=False)
        monkeypatch.setattr(config.settings, "cookie_domain", ".foo.bar", raising=False)
        resp = Response()
        _set_cookie(resp, "__Secure-access_token", "tok", max_age=600)
        set_cookie_headers = [
            v for k, v in resp.headers.raw
            if k.lower() == b"set-cookie" and b"__Secure-access_token" in v
        ]
        joined = b" ".join(set_cookie_headers).decode()
        assert "Domain=.foo.bar" in joined

    def test_ip_addresses_remain_host_only(self, monkeypatch):
        """IPv4 und IPv6 Adressen duerfen NIE ein Domain-Attribut erhalten.
        
        RFC 6265 Section 5.1.3 & 5.2.3: Domain-Attribute auf IP-Adressen sind ungueltig
        und fuehren im Browser zur kompletten Verwerfung des Cookies.
        """
        for ip_url in (
            "http://192.168.1.100:8000",
            "http://10.0.0.5:3000",
            "https://45.88.109.123",
            "http://[2001:db8::1]:8000",
        ):
            monkeypatch.setattr(config.settings, "panel_url", ip_url, raising=False)
            monkeypatch.setattr(config.settings, "cookie_domain", "", raising=False)
            assert config.get_effective_cookie_domain() == "", f"IP {ip_url} muss host-only sein"
            resp = Response()
            _set_cookie(resp, "__Secure-access_token", "tok", max_age=600)
            set_cookie_headers = [
                v for k, v in resp.headers.raw
                if k.lower() == b"set-cookie" and b"__Secure-access_token" in v
            ]
            joined = b" ".join(set_cookie_headers).decode()
            assert "Domain=" not in joined, f"IP {ip_url} darf kein Domain-Attribut haben: {joined!r}"

    def test_single_label_host_remains_host_only(self, monkeypatch):
        """Hostnamen ohne Punkte (z. B. http://msm-box:8000) bleiben host-only."""
        monkeypatch.setattr(config.settings, "panel_url", "http://msm-box:8000", raising=False)
        monkeypatch.setattr(config.settings, "cookie_domain", "", raising=False)
        assert config.get_effective_cookie_domain() == ""

    def test_explicit_ip_cookie_domain_override_remains_host_only(self, monkeypatch):
        """Auch wenn ein Betreiber versehentlich eine IP-Adresse in MSM_COOKIE_DOMAIN eintraegt,
        darf kein Domain-Attribut gesetzt werden."""
        monkeypatch.setattr(config.settings, "panel_url", "http://192.168.1.100:8000", raising=False)
        monkeypatch.setattr(config.settings, "cookie_domain", "192.168.1.100", raising=False)
        assert config.get_effective_cookie_domain() == ""
        monkeypatch.setattr(config.settings, "cookie_domain", ".192.168.1.100", raising=False)
        assert config.get_effective_cookie_domain() == ""

    def test_explicit_localhost_or_single_label_override_remains_host_only(self, monkeypatch):
        """Explizite Overrides auf localhost oder Single-Label-Hostnamen duerfen
        ebenfalls NIE ein ungueltiges Domain-Attribut erzeugen."""
        for explicit in ("localhost", "127.0.0.1", "::1", "intranet", "none", "   "):
            monkeypatch.setattr(config.settings, "cookie_domain", explicit, raising=False)
            assert config.get_effective_cookie_domain() == "", f"Override {explicit!r} muss host-only ergeben"


class TestDevHttpCookies:
    """Anmeldung im lokalen Dev-Stack ueber einfaches HTTP auf Loopback.

    Ein `__Secure-`-Cookie verwirft Chromium ueber http://localhost — der Login
    antwortete 200, und `/api/auth/me` danach 401, weil kein Cookie ankam. Der
    Schalter `MSM_DEV_HTTP_COOKIES` nimmt Praefix und Secure-Attribut weg, aber
    nur bei `debug` und einer Loopback-Adresse.
    """

    @staticmethod
    def _schalte(monkeypatch, *, an: bool, debug: bool = True, panel: str = "http://localhost:3000"):
        monkeypatch.setattr(config.settings, "dev_http_cookies", an, raising=False)
        monkeypatch.setattr(config.settings, "debug", debug, raising=False)
        monkeypatch.setattr(config.settings, "panel_url", panel, raising=False)
        monkeypatch.setattr(config.settings, "api_url", "", raising=False)

    @staticmethod
    def _set_cookie_header(response: Response, name: str) -> str:
        for key, value in response.headers.raw:
            if key.lower() == b"set-cookie" and value.decode().startswith(name + "="):
                return value.decode()
        raise AssertionError(f"Set-Cookie fuer {name} fehlt")

    def test_setzt_ohne_praefix_und_ohne_secure(self, monkeypatch):
        self._schalte(monkeypatch, an=True)
        assert dev_http_cookies() is True
        response = Response()
        _set_auth_cookies(response, "zugang", "erneuerung", "csrf")

        access = self._set_cookie_header(response, "msm_dev_access_token")
        assert "Secure" not in access
        assert "HttpOnly" in access
        # Path bleibt, damit das Cookie nur an die API geht.
        assert "Path=/api" in access
        assert self._set_cookie_header(response, "msm_dev_csrf_token")
        assert self._set_cookie_header(response, "msm_dev_refresh_token")

    def test_ohne_schalter_bleiben_die_praefix_cookies(self, monkeypatch):
        self._schalte(monkeypatch, an=False)
        assert dev_http_cookies() is False
        response = Response()
        _set_auth_cookies(response, "zugang", "erneuerung", "csrf")

        access = self._set_cookie_header(response, "__Secure-access_token")
        assert "Secure" in access
        assert "msm_dev_access_token" not in access

    def test_gilt_nicht_ausserhalb_von_loopback(self, monkeypatch):
        self._schalte(monkeypatch, an=True, panel="https://panel.example.com")
        assert dev_http_cookies() is False
        assert cookie_name("__Secure-access_token") == "__Secure-access_token"

    def test_gilt_nicht_ohne_debug(self, monkeypatch):
        self._schalte(monkeypatch, an=True, debug=False)
        assert dev_http_cookies() is False
        assert cookie_name("__Secure-access_token") == "__Secure-access_token"

    def test_lesen_findet_das_cookie_des_modus(self, monkeypatch):
        self._schalte(monkeypatch, an=True)
        assert lies_zugangstoken({"msm_dev_access_token": "t"}) == "t"
        assert lies_zugangstoken({"__Secure-access_token": "t"}) is None

        self._schalte(monkeypatch, an=False)
        assert lies_zugangstoken({"__Secure-access_token": "t"}) == "t"
        assert lies_zugangstoken({"msm_dev_access_token": "t"}) is None


