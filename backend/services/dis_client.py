"""DIS Client — zentrale Fassade fuer alle Krypto-Operationen.

Kommuniziert mit dem lokalen DIS Sidecar (Node.js, @msdis/shield) ueber HTTP.
Das Panel selbst enthaelt keine eigene Kryptographie — alle Ver-/Entschluesselung,
Passwort-Hashing (Argon2id) und TOTP laufen ueber diesen Client und damit ueber
DIS.

Sicherheits-Invarianten:
- Fail-closed: wenn der Sidecar nicht erreichbar ist, schlagen Krypto-Operationen
  fehl. Es gibt KEINEN Fallback auf eigene Krypto.
- Plaintext-Daten werden nie geloggt.
- Der Sidecar lauscht nur auf 127.0.0.1; Bearer-Token verhindert Aufrufe durch
  andere lokale Prozesse.
"""
from __future__ import annotations

import httpx
from config import settings


class DisSidecarError(Exception):
    """Sidecar nicht erreichbar oder Fehler bei der Krypto-Operation."""


class DisDecryptionError(DisSidecarError):
    """Entschluesselung fehlgeschlagen (wrong key / tamper / AAD mismatch).

    Der Sidecar unterscheidet nicht zwischen diesen Ursachen (kein Oracle).
    """


class DisClient:
    """Statische Fassade fuer DIS-Krypto-Operationen ueber den lokalen Sidecar."""

    _timeout = 15.0

    @staticmethod
    def _headers() -> dict[str, str]:
        if settings.dis_sidecar_token:
            return {"Authorization": f"Bearer {settings.dis_sidecar_token}"}
        return {}

    @staticmethod
    def _post(endpoint: str, payload: dict) -> dict:
        url = settings.dis_sidecar_url.rstrip("/") + endpoint
        try:
            resp = httpx.post(url, json=payload, headers=DisClient._headers(), timeout=DisClient._timeout)
        except httpx.HTTPError as e:
            raise DisSidecarError(f"DIS Sidecar nicht erreichbar: {e}") from e
        if resp.status_code == 401:
            raise DisSidecarError("DIS Sidecar Auth fehlgeschlagen (Token falsch?)")
        if resp.status_code == 400:
            body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            err = body.get("error", "")
            if err in ("DisDecryptionError", "DisIntegrityError"):
                raise DisDecryptionError("Entschluesselung fehlgeschlagen")
            raise DisSidecarError(f"DIS Sidecar Fehler: {err or resp.status_code}")
        if resp.status_code != 200:
            raise DisSidecarError(f"DIS Sidecar Fehler: HTTP {resp.status_code}")
        return resp.json()

    # ── Encryption (AES-256-GCM) ────────────────────────────────────────

    @staticmethod
    def encrypt(plaintext: str, aad: str | None = None) -> str:
        """Verschluesselt einen String mit DIS AES-256-GCM.

        Args:
            plaintext: Klartext.
            aad: Optionaler Context (Associated Authenticated Data) zum Binden
                 des Ciphertext an einen Context (verhindert Swap-Angriffe).

        Returns: Base64-codierter Ciphertext (IV + encrypted + tag).
        """
        payload: dict = {"plaintext": plaintext}
        if aad:
            payload["aad"] = aad
        return DisClient._post("/encrypt", payload)["ciphertext"]

    @staticmethod
    def decrypt(ciphertext: str, aad: str | None = None) -> str:
        """Entschluesselt einen DIS-AES-256-GCM-Ciphertext.

        Raises DisDecryptionError bei falschem Key, Tampering oder AAD-Mismatch.
        """
        payload: dict = {"ciphertext": ciphertext}
        if aad:
            payload["aad"] = aad
        return DisClient._post("/decrypt", payload)["plaintext"]

    # ── Password Hashing (Argon2id) ──────────────────────────────────────

    @staticmethod
    def hash_password(password: str) -> str:
        """Hasht ein Passwort mit DIS Argon2id.

        Returns: Hash im Format msm-pw-v1:b64(salt):b64(hash):v2
        """
        return DisClient._post("/hash-password", {"password": password})["hash"]

    @staticmethod
    def verify_password(password: str, stored_hash: str) -> bool:
        """Verifiziert ein Passwort gegen einen DIS-Hash (msm-pw-v1:...).

        Fuer legacy passlib-Hashes ($argon2...) siehe
        AuthService.verify_password (Migration-Pfad).
        """
        result = DisClient._post("/verify-password", {"password": password, "hash": stored_hash})
        return result.get("valid", False)

    @staticmethod
    def is_dis_hash(stored_hash: str) -> bool:
        """Prueft ob ein Hash im DIS-Format (msm-pw-v1:) ist."""
        return stored_hash.startswith("msm-pw-v1:")

    # ── TOTP (2FA) ───────────────────────────────────────────────────────

    @staticmethod
    def generate_totp_secret() -> str:
        """Generiert ein neues Base32-TOTP-Secret (160-bit)."""
        return DisClient._post("/totp/generate-secret", {})["secret"]

    @staticmethod
    def verify_totp(secret: str, code: str) -> bool:
        """Verifiziert einen TOTP-Code gegen ein Secret (±30s Fenster)."""
        return DisClient._post("/totp/verify", {"secret": secret, "code": code}).get("valid", False)

    @staticmethod
    def build_totp_uri(issuer: str, label: str, secret: str) -> str:
        """Baut die otpauth://-URI fuer QR-Code-Generierung."""
        return DisClient._post("/totp/build-uri", {"issuer": issuer, "label": label, "secret": secret})["uri"]

    # ── Health ───────────────────────────────────────────────────────────

    @staticmethod
    def health_check() -> bool:
        """Prueft ob der Sidecar erreichbar ist."""
        try:
            url = settings.dis_sidecar_url.rstrip("/") + "/health"
            resp = httpx.get(url, headers=DisClient._headers(), timeout=5.0)
            return resp.status_code == 200
        except httpx.HTTPError:
            return False
