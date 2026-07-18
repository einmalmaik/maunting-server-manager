"""Backup Crypto Service — Fassade fuer DIS-Streaming-Verschluesselung.

Wrappt die DIS-Sidecar-Streaming-Endpunkte (init-key, encrypt-stream,
decrypt-stream, invalidate-key) via httpx. Alle Krypto laeuft im DIS Sidecar
(auditierte @msdis/shield Library) — das Panel enthaelt keine eigene Krypto.

Key-Lifecycle:
1. init_key(password, salt) -> key_id  (vor Verschluesselung/Entschluesselung)
2. encrypt_file_stream / decrypt_to_file  (nutzt key_id)
3. invalidate_key(key_id)  (nach Operation, immer via try/finally)

Sicherheits-Invarianten:
- Keys werden nur im DIS-Speicher gehalten, nie persistiert.
- Nach Gebrauch (oder Fehler) wird der Key invalidiert.
- Keine Plaintext/Key-Material in Logs.
"""
from __future__ import annotations

import logging
import os
from typing import Iterator

import httpx

from config import settings

logger = logging.getLogger(__name__)

# Grosszuegiges Timeout fuer Streaming ( grosse Dateien ).
_STREAM_TIMEOUT = 600.0
_JSON_TIMEOUT = 30.0


class BackupCryptoError(Exception):
    """DIS-Streaming-Verschluesselung fehlgeschlagen."""


class BackupDecryptionError(BackupCryptoError):
    """Entschluesselung fehlgeschlagen (falsches Passwort / manipulierter Stream).

    DIS meldet HTTP 400 DecryptionFailed — der Key stimmt nicht mit dem
    verschluesselten Objekt ueberein (Passwort geaendert, falscher Salt,
    oder Daten manipuliert). Der Caller kann darauf eine klare User-Meldung
    zurueckgeben (statt eines generischen 500).
    """


class BackupCryptoService:
    """Statische Fassade fuer DIS-Streaming-Verschluesselung."""

    @staticmethod
    def _dis_url(path: str) -> str:
        return settings.dis_sidecar_url.rstrip("/") + path

    @staticmethod
    def _auth_headers() -> dict[str, str]:
        if settings.dis_sidecar_token:
            return {"Authorization": f"Bearer {settings.dis_sidecar_token}"}
        return {}

    @staticmethod
    def derive_raw_key_b64(password: str, salt: str) -> str:
        """Derive AES-256 key (base64) via DIS for one-shot agent handoff (Phase 6).

        Same Argon2id params as init_key. Key is not stored in DIS — caller must
        pass it only in-memory to the agent and never log it.
        """
        url = BackupCryptoService._dis_url("/backup/derive-raw-key")
        headers = BackupCryptoService._auth_headers()
        headers["Content-Type"] = "application/json"
        try:
            resp = httpx.post(
                url,
                json={"password": password, "salt": salt},
                headers=headers,
                timeout=_JSON_TIMEOUT,
            )
        except httpx.HTTPError as e:
            raise BackupCryptoError(f"DIS nicht erreichbar: {e}") from e
        if resp.status_code != 200:
            raise BackupCryptoError(f"DIS derive-raw-key fehlgeschlagen: HTTP {resp.status_code}")
        key_b64 = resp.json().get("key_b64")
        if not key_b64 or not isinstance(key_b64, str):
            raise BackupCryptoError("DIS derive-raw-key: missing key_b64")
        return key_b64

    @staticmethod
    def init_key(password: str, salt: str) -> str:
        """Initialisiert einen Backup-Verschluesselungskey via DIS.

        Args:
            password: Backup-Passwort (Klartext, wird nicht geloggt).
            salt: Base64-codiertes Salt.

        Returns: key_id (UUID) fuer encrypt/decrypt-Streams.
        """
        url = BackupCryptoService._dis_url("/backup/init-key")
        headers = BackupCryptoService._auth_headers()
        headers["Content-Type"] = "application/json"
        try:
            resp = httpx.post(
                url,
                json={"password": password, "salt": salt},
                headers=headers,
                timeout=_JSON_TIMEOUT,
            )
        except httpx.HTTPError as e:
            raise BackupCryptoError(f"DIS nicht erreichbar: {e}") from e
        if resp.status_code != 200:
            raise BackupCryptoError(f"DIS init-key fehlgeschlagen: HTTP {resp.status_code}")
        return resp.json()["key_id"]

    @staticmethod
    def invalidate_key(key_id: str) -> None:
        """Entfernt den Backup-Key aus dem DIS-Speicher (idempotent)."""
        url = BackupCryptoService._dis_url("/backup/invalidate-key")
        headers = BackupCryptoService._auth_headers()
        headers["Content-Type"] = "application/json"
        try:
            resp = httpx.post(
                url,
                json={"key_id": key_id},
                headers=headers,
                timeout=_JSON_TIMEOUT,
            )
        except httpx.HTTPError as e:
            raise BackupCryptoError(f"DIS nicht erreichbar: {e}") from e
        if resp.status_code != 200:
            raise BackupCryptoError(f"DIS invalidate-key fehlgeschlagen: HTTP {resp.status_code}")

    @staticmethod
    def encrypt_file_stream(file_path: str, key_id: str) -> Iterator[bytes]:
        """Stream-verschluesselt eine Datei via DIS, yielded verschluesselte Frames.

        Oeffnet die Datei, streamt sie an DIS /backup/encrypt-stream und
        yielded die verschluesselten Response-Bytes. Der Caller konsumiert
        den Generator (z.B. S3Service.upload_stream).
        """
        url = BackupCryptoService._dis_url("/backup/encrypt-stream")
        headers = BackupCryptoService._auth_headers()
        headers["X-Backup-Key-Id"] = key_id
        with open(file_path, "rb") as f:
            with httpx.stream(
                "POST",
                url,
                content=f,
                headers=headers,
                timeout=_STREAM_TIMEOUT,
            ) as resp:
                if resp.status_code != 200:
                    raise BackupCryptoError(
                        f"DIS encrypt-stream fehlgeschlagen: HTTP {resp.status_code}"
                    )
                yield from resp.iter_bytes()

    @staticmethod
    def decrypt_to_file(encrypted_stream, key_id: str, output_path: str) -> None:
        """Stream-entschluesst verschluesselte Frames und schreibt in Datei.

        Schreibt zunaechst in eine Temporaerdatei (<output_path>.tmp) und
        benennt sie erst nach erfolgreicher Entschluesselung atomar um.
        So bleibt die Zieldatei bei einem Fehler (falsches Passwort,
        abgebrochener Stream) unversehrt bzw. wird nicht als korrupt
        hinterlassen.

        Args:
            encrypted_stream: Iterator[bytes] oder file-like mit verschluesselten Frames.
            key_id: Gueltige DIS key_id.
            output_path: Pfad fuer die entschlueselte Ausgabedatei.
        """
        url = BackupCryptoService._dis_url("/backup/decrypt-stream")
        headers = BackupCryptoService._auth_headers()
        headers["X-Backup-Key-Id"] = key_id
        tmp_path = output_path + ".tmp"
        try:
            with httpx.stream(
                "POST",
                url,
                content=encrypted_stream,
                headers=headers,
                timeout=_STREAM_TIMEOUT,
            ) as resp:
                if resp.status_code == 400:
                    # DecryptionFailed: falsches Passwort, falscher Salt, oder manipulierter Stream.
                    # Klare Ausnahme fuer aufrufenden Code, damit eine verstaendliche
                    # Fehlermeldung an den User gegeben werden kann (kein generisches 500).
                    raise BackupDecryptionError("Entschluesselung fehlgeschlagen")
                if resp.status_code != 200:
                    raise BackupCryptoError(
                        f"DIS decrypt-stream fehlgeschlagen: HTTP {resp.status_code}"
                    )
                with open(tmp_path, "wb") as out:
                    for chunk in resp.iter_bytes():
                        out.write(chunk)
            # Atomares Rename: Zieldatei wird erst nach vollstaendiger,
            # erfolgreicher Entschluesselung sichtbar.
            os.replace(tmp_path, output_path)
        except httpx.HTTPError as e:
            # Stream abgebrochen (z.B. DIS zerstoert Socket bei Tamper-Erkennung
            # in einem spaeteren Frame). Temp-Datei wird unten aufgeraeumt.
            raise BackupDecryptionError(
                "Entschluesselung fehlgeschlagen (Stream abgebrochen)"
            ) from e
        finally:
            # Temp-Datei bei Fehler oder Erfolg aufraeumen.
            # Nach erfolgreichem os.replace existiert tmp_path nicht mehr.
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
