"""Tests fuer SteamAccountService.

Sicherheits-Invarianten:
- Passwort ist AT REST DIS-encrypted (nicht Klartext).
- ``get_decrypted_password`` liefert nach Set→Get das Klartext-Passwort.
- GET-Response enthaelt niemals das Passwort.
- Passwort taucht in keinem Log-Record auf.
- Falsche Entschluesselung (z. B. nach SECRET_KEY-Rotation) -> klare Fehlermeldung.
"""

from __future__ import annotations

import logging

import pytest

from services.auth_service import AuthService
from services.steam_account_service import SteamAccountService


class TestSteamAccountService:
    def test_round_trip(self) -> None:
        SteamAccountService.set("testuser", "secret_password_123")
        assert SteamAccountService.get_username() == "testuser"
        assert SteamAccountService.is_configured() is True
        assert SteamAccountService.get_decrypted_password() == "secret_password_123"
        SteamAccountService.clear()
        assert SteamAccountService.is_configured() is False

    def test_password_is_encrypted_at_rest(self) -> None:
        SteamAccountService.set("user", "mypass")
        from services.panel_settings_service import PanelSettingsService
        raw = PanelSettingsService.get("steam_account_password_enc", "")
        assert raw != "mypass"
        assert raw.startswith("test-enc-")  # DIS-mock prefix
        SteamAccountService.clear()

    def test_clear_removes_both_keys(self) -> None:
        SteamAccountService.set("u", "p")
        SteamAccountService.clear()
        assert SteamAccountService.get_username() == ""
        with pytest.raises(RuntimeError, match="nicht konfiguriert"):
            SteamAccountService.get_decrypted_password()

    def test_decrypt_fails_gracefully_after_key_rotation(self, monkeypatch) -> None:
        SteamAccountService.set("u", "p")
        # Simuliere DIS-Entschluesselungsfehler (z. B. nach Key-Rotation)
        from services.dis_client import DisDecryptionError

        def _raise_dis_error(*a, **kw):
            raise DisDecryptionError("mock key rotation")

        monkeypatch.setattr(
            AuthService,
            "decrypt_secret",
            staticmethod(_raise_dis_error),
        )
        with pytest.raises(RuntimeError, match="SECRET_KEY rotiert"):
            SteamAccountService.get_decrypted_password()
        monkeypatch.undo()
        SteamAccountService.clear()

    def test_password_never_in_logs(self, caplog) -> None:
        caplog.set_level(logging.DEBUG)
        secret = "my_super_secret_steam_password_42"
        SteamAccountService.set("loguser", secret)
        SteamAccountService.is_configured()
        SteamAccountService.get_username()
        SteamAccountService.clear()
        for record in caplog.records:
            assert secret not in record.message
            assert secret not in (record.args or [])

    def test_validation_rejects_empty(self) -> None:
        with pytest.raises(ValueError, match="gesetzt sein"):
            SteamAccountService.set("", "pass")
        with pytest.raises(ValueError, match="gesetzt sein"):
            SteamAccountService.set("user", "")

    def test_validation_rejects_too_long(self) -> None:
        with pytest.raises(ValueError, match="zu lang"):
            SteamAccountService.set("u" * 300, "pass")
        with pytest.raises(ValueError, match="zu lang"):
            SteamAccountService.set("user", "p" * 2000)
