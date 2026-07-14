"""Globaler Steam-Account fuer SteamCMD-Logins (DayZ etc.).

Sicherheits-Invarianten:
- Passwort wird AT REST nur DIS-verschluesselt gespeichert (AES-256-GCM
  ueber den DIS Sidecar, AAD gebunden an 'msm:steam:password').
- ``get_decrypted_password`` ist die einzige Methode, die Klartext
  zurueckgibt. Wird ausschliesslich im SteamCMD-Install-Pfad aufgerufen
  und niemals geloggt.
- ``get_status`` fuer GET-Endpoints liefert nur ``configured: bool`` +
  Username — niemals das Passwort, auch nicht maskiert.
"""

from services.auth_service import AuthService
from services.dis_client import DisDecryptionError
from services.panel_settings_service import PanelSettingsService

_USERNAME_KEY = "steam_account_username"
_PASSWORD_KEY = "steam_account_password_enc"
_LEGACY_USER_KEY = "steam_user"
_LEGACY_PASS_KEY = "steam_password"
_AAD = "msm:steam:password"


class SteamAccountService:
    @staticmethod
    def migrate_legacy_if_needed() -> bool:
        """Übernimmt alte plain-text Keys ``steam_user``/``steam_password`` einmalig."""
        if SteamAccountService.is_configured():
            return False
        u = PanelSettingsService.get(_LEGACY_USER_KEY, "").strip()
        p = PanelSettingsService.get(_LEGACY_PASS_KEY, "").strip()
        if not u or not p:
            return False
        SteamAccountService.set(u, p)
        PanelSettingsService.set(_LEGACY_USER_KEY, "")
        PanelSettingsService.set(_LEGACY_PASS_KEY, "")
        return True

    @staticmethod
    def is_configured() -> bool:
        return bool(PanelSettingsService.get(_USERNAME_KEY)) and bool(
            PanelSettingsService.get(_PASSWORD_KEY)
        )

    @staticmethod
    def get_username() -> str:
        return PanelSettingsService.get(_USERNAME_KEY, "")

    @staticmethod
    def set(username: str, password: str) -> None:
        u = username.strip()
        if not u or not password:
            raise ValueError("Username und Passwort müssen gesetzt sein.")
        if len(u) > 256 or len(password) > 1024:
            raise ValueError("Username/Passwort zu lang.")
        enc = AuthService.encrypt_secret(password, aad=_AAD)
        PanelSettingsService.set(_USERNAME_KEY, u)
        PanelSettingsService.set(_PASSWORD_KEY, enc)

    @staticmethod
    def clear() -> None:
        PanelSettingsService.set(_USERNAME_KEY, "")
        PanelSettingsService.set(_PASSWORD_KEY, "")

    @staticmethod
    def get_decrypted_password() -> str:
        """NUR im SteamCMD-Install-Pfad aufrufen. Nie loggen.

        Wirft RuntimeError, wenn kein Account konfiguriert ist oder
        die Entschluesselung fehlschlaegt (z. B. nach SECRET_KEY-Rotation).
        """
        enc = PanelSettingsService.get(_PASSWORD_KEY, "")
        if not enc:
            raise RuntimeError("Steam-Account nicht konfiguriert.")
        try:
            return AuthService.decrypt_secret(enc, aad=_AAD)
        except DisDecryptionError as e:
            raise RuntimeError(
                "Steam-Account-Passwort kann nicht entschlüsselt werden — "
                "vermutlich wurde der SECRET_KEY rotiert. Bitte Account neu hinterlegen."
            ) from e
