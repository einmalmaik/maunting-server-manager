"""Backup Config Service — verwaltet S3-Konfiguration und Backup-Passwort.

Speichert S3-Credentials verschluesselt via DIS in panel_settings
(AAD="msm:backup:s3") und das Backup-Passwort verschluesselt via DIS
(AAD="msm:backup:pw"). Das Salt fuer die Argon2id-Key-Derivation wird
plain-text gespeichert (nicht sensitiv — es verhindert nur Rainbow-Tables).

Sicherheits-Invarianten:
- S3-Credentials (access_key, secret_key) werden verschluesselt gespeichert.
- Backup-Passwort wird verschluesselt gespeichert und in KEINER API-Antwort
  zurueckgegeben (write-only).
- AAD-Domain-Separation verhindert Cross-Context-Decryption
  (S3-Cred mit backup:pw AAD entschluesseln schlaegt fehl und umgekehrt).
- Keine Credentials/Passwoerter in Logs.
- Overwrite-Semantik: PanelSettingsService.set upsertet (keine Duplikate).
- Salt wird beim ersten Setzen generiert und bei Passwort-Aenderung reused.
"""
from __future__ import annotations

import base64
import logging
import secrets

from services.dis_client import DisClient
from services.panel_settings_service import PanelSettingsService

logger = logging.getLogger(__name__)

# AAD-Domain-Separation: unterschiedliche Contexte fuer S3-Credentials und
# Backup-Passwort. Verhindert, dass ein Ciphertext im falschen Context
# entschluesselt werden kann (Swap-Angriffe).
_S3_AAD = "msm:backup:s3"
_PW_AAD = "msm:backup:pw"

# panel_settings Keys
_KEY_ENDPOINT = "backup.s3_endpoint"
_KEY_ACCESS_ENC = "backup.s3_access_key_encrypted"
_KEY_SECRET_ENC = "backup.s3_secret_key_encrypted"
_KEY_BUCKET = "backup.s3_bucket"
_KEY_REGION = "backup.s3_region"
_KEY_PW_ENC = "backup.password_encrypted"
_KEY_SALT = "backup.salt"


class BackupConfigService:
    """Statische Fassade fuer Backup-Konfiguration (S3 + Passwort)."""

    # ── S3 Config ────────────────────────────────────────────────────────

    @staticmethod
    def set_s3_config(
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        region: str | None = None,
    ) -> None:
        """Speichert S3-Config; Credentials verschluesselt via DIS (AAD=s3).

        endpoint ist optional (fuer AWS S3 nicht noetig). region ist optional.
        Verwendet PanelSettingsService.set (Upsert — keine Duplikate).
        """
        access_enc = DisClient.encrypt(access_key, aad=_S3_AAD)
        secret_enc = DisClient.encrypt(secret_key, aad=_S3_AAD)
        PanelSettingsService.set(_KEY_ENDPOINT, endpoint or "")
        PanelSettingsService.set(_KEY_ACCESS_ENC, access_enc)
        PanelSettingsService.set(_KEY_SECRET_ENC, secret_enc)
        PanelSettingsService.set(_KEY_BUCKET, bucket)
        PanelSettingsService.set(_KEY_REGION, region or "")

    @staticmethod
    def get_s3_config() -> dict:
        """Gibt S3-Config zurueck (Credentials maskiert, last 4 chars).

        Returns: dict mit endpoint, access_key (maskiert), secret_key (maskiert),
                 bucket, region. Leere Felder wenn nicht konfiguriert.
        """
        endpoint = PanelSettingsService.get(_KEY_ENDPOINT)
        access_enc = PanelSettingsService.get(_KEY_ACCESS_ENC)
        secret_enc = PanelSettingsService.get(_KEY_SECRET_ENC)
        bucket = PanelSettingsService.get(_KEY_BUCKET)
        region = PanelSettingsService.get(_KEY_REGION)

        access_key = ""
        secret_key = ""
        if access_enc:
            try:
                access_key = DisClient.decrypt(access_enc, aad=_S3_AAD)
            except Exception:
                # Bei Decrypt-Fehler maskiert leer zurueckgeben (kein Leak).
                access_key = ""
        if secret_enc:
            try:
                secret_key = DisClient.decrypt(secret_enc, aad=_S3_AAD)
            except Exception:
                secret_key = ""

        return {
            "endpoint": endpoint,
            "access_key": _mask(access_key),
            "secret_key": _mask(secret_key),
            "bucket": bucket,
            "region": region,
        }

    @staticmethod
    def is_s3_configured() -> bool:
        """Prueft ob S3 konfiguriert ist (Credentials + Bucket vorhanden)."""
        access_enc = PanelSettingsService.get(_KEY_ACCESS_ENC)
        secret_enc = PanelSettingsService.get(_KEY_SECRET_ENC)
        bucket = PanelSettingsService.get(_KEY_BUCKET)
        return bool(access_enc and secret_enc and bucket)

    # ── Backup Password ──────────────────────────────────────────────────

    @staticmethod
    def set_backup_password(password: str) -> None:
        """Speichert Backup-Passwort verschluesselt via DIS (AAD=pw).

        Salt wird beim ersten Setzen generiert und bei folgenden Aenderungen
        reused (wichtig: Aenderung des Passworts darf den Salt nicht aendern,
        sonst koennen alte Backups nicht mehr entschluesselt werden).
        """
        salt = BackupConfigService.ensure_backup_salt()
        pw_enc = DisClient.encrypt(password, aad=_PW_AAD)
        PanelSettingsService.set(_KEY_PW_ENC, pw_enc)
        # Salt ist bereits gesetzt (ensure_backup_salt), aber zur Sicherheit
        # nochmal schreiben (idempotent via PanelSettingsService.set).
        PanelSettingsService.set(_KEY_SALT, salt)

    @staticmethod
    def get_backup_password() -> str:
        """Entschluesst das Backup-Passwort via DIS (AAD=pw).

        Wird vom Backup-Orchestrator/Crypto-Service verwendet, NICHT von
        API-Endpunkten (Passwort ist write-only aus API-Sicht).
        Raises: DisDecryptionError wenn Passwort nicht gesetzt oder Decrypt fehlschlaegt.
        """
        pw_enc = PanelSettingsService.get(_KEY_PW_ENC)
        if not pw_enc:
            raise ValueError("Backup-Passwort nicht gesetzt")
        return DisClient.decrypt(pw_enc, aad=_PW_AAD)

    @staticmethod
    def get_backup_salt() -> str:
        """Gibt das Salt zurueck (base64, plain-text — nicht sensitiv)."""
        return PanelSettingsService.get(_KEY_SALT)

    @staticmethod
    def ensure_backup_salt() -> str:
        """Generiert Salt wenn nicht vorhanden, speichert in panel_settings.

        Wenn bereits ein Salt existiert, wird dieses reused (keine Regeneration).
        Returns: base64-codiertes 16-Byte Salt.
        """
        existing = PanelSettingsService.get(_KEY_SALT)
        if existing:
            return existing
        salt = base64.b64encode(secrets.token_bytes(16)).decode()
        PanelSettingsService.set(_KEY_SALT, salt)
        return salt

    @staticmethod
    def is_backup_password_set() -> bool:
        """Prueft ob ein Backup-Passwort gesetzt ist."""
        return bool(PanelSettingsService.get(_KEY_PW_ENC))

    # ── Status ───────────────────────────────────────────────────────────

    @staticmethod
    def get_status() -> dict:
        """Gibt Backup-System-Status zurueck (s3_configured, password_set).

        last_panel_backup wird vom Panel-Backup-Feature gesetzt (M3) und ist
        hier null bis dieses implementiert ist.
        """
        return {
            "s3_configured": BackupConfigService.is_s3_configured(),
            "backup_password_set": BackupConfigService.is_backup_password_set(),
            "last_panel_backup": None,
        }


def _mask(value: str) -> str:
    """Maskiert einen Credential-Wert: nur letzte 4 Zeichen sichtbar.

    Kurze Werte (<=4) werden vollstaendig maskiert. Leere Werte bleiben leer.
    """
    if not value:
        return ""
    if len(value) <= 4:
        return "*" * len(value)
    return "*" * (len(value) - 4) + value[-4:]
