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

from cookies import _set_cookie, _clear_auth_cookies, _set_auth_cookies
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


