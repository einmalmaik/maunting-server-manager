"""Globaler Steam-Account fuer SteamCMD-Logins (DayZ etc.).

Sicherheits-Invarianten:
- Passwort wird AT REST nur Fernet-encrypted gespeichert (Key abgeleitet
  aus settings.secret_key, identisches Pattern wie 2FA-Secrets).
- ``get_decrypted_password`` ist die einzige Methode, die Klartext
  zurueckgibt. Wird ausschliesslich im SteamCMD-Install-Pfad aufgerufen
  und niemals geloggt.
- ``get_status`` fuer GET-Endpoints liefert nur ``configured: bool`` +
  Username — niemals das Passwort, auch nicht maskiert.
"""

from cryptography.fernet import InvalidToken

from services.auth_service import AuthService
from services.panel_settings_service import PanelSettingsService

_USERNAME_KEY = "steam_account_username"
_PASSWORD_KEY = "steam_account_password_enc"


class SteamAccountService:
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
            raise ValueError("Username und Passwort muessen gesetzt sein.")
        if len(u) > 256 or len(password) > 1024:
            raise ValueError("Username/Passwort zu lang.")
        enc = AuthService.encrypt_2fa_secret(password)
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
            return AuthService.decrypt_2fa_secret(enc)
        except InvalidToken as e:
            raise RuntimeError(
                "Steam-Account-Passwort kann nicht entschluesselt werden — "
                "vermutlich wurde der SECRET_KEY rotiert. Bitte Account neu hinterlegen."
            ) from e
