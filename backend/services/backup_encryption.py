"""Client-seitige Verschluesselung fuer Backup-Files (AES-256-GCM).

Vor jedem Upload wird der tar.gz hier verschluesselt, nach jedem Download
entschluesselt. So schuetzen wir den Inhalt selbst dann, wenn der
Provider (S3-Bucket, SFTP-Server, etc.) kompromittiert ist.

Algorithmus
-----------
AES-256-GCM (authenticated encryption) ueber ``cryptography.hazmat.primitives
.ciphers.aead.AESGCM``. Eine 32-Byte-Key-Datei, ein per-Backup-12-Byte-Nonce,
ein 16-Byte-Auth-Tag. Keine eigene Krypto (AGENTS.md §4).

File-Format
-----------
``[ 1 byte version ][ 12 byte nonce ][ ciphertext + 16 byte tag ]``

Version-Byte erlaubt spaetere Migration auf andere Algorithmen ohne
Kollision mit alten Files. Stand 2026-06 ist 0x01.

Schluessel-Management
---------------------
- Master-Key ist 32 Byte zufaellig (``secrets.token_bytes``), base64-kodiert
  in ``.env`` als ``MSM_BACKUP_ENCRYPTION_KEY``.
- ``.env`` hat bereits ``chmod 600`` (heutiges Muster) — kein neuer Schutz noetig.
- Key wird **nicht** von ``SECRET_KEY`` abgeleitet: SECRET_KEY-Rotation soll
  alte Backups nicht zerstoeren.
- Key-Material erscheint in **keinem** Log, keiner Exception, keinem Toast.
  Fehlermeldungen sind generisch (``"Entschluesselung fehlgeschlagen"``).
- Wer den ``.env`` verliert, verliert alle Cloud-Backups. Wird in
  install.sh und PATCHNOTES explizit kommuniziert.
"""
import base64
import binascii
import logging
import secrets
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger(__name__)


# ── Format-Konstanten ─────────────────────────────────────────────────────

FORMAT_VERSION = 0x01
NONCE_SIZE = 12   # GCM-Standard
KEY_SIZE = 32     # AES-256
TAG_SIZE = 16     # GCM-Auth-Tag (in AESGCM.encrypt output bereits appended)


class BackupEncryptionError(Exception):
    """Generischer Verschluesselungs-Fehler. Enthaelt NIEMALS Key-Material,
    Klartext oder Datei-Inhalte — nur kategorische Codes."""


# ── Public API ────────────────────────────────────────────────────────────


def generate_master_key() -> str:
    """Generiert einen frischen 32-Byte-Master-Key, gibt als URL-safe Base64 zurueck.

    Wird beim ersten Cloud-Enable vom Installer aufgerufen und in
    ``.env`` als ``MSM_BACKUP_ENCRYPTION_KEY`` geschrieben.
    """
    return secrets.token_urlsafe(KEY_SIZE)


def _load_key(master_key_b64: str) -> bytes:
    """Decodet und validiert den Master-Key.

    Wirft ``BackupEncryptionError`` mit generischem Text (kein Key-Material).
    """
    if not master_key_b64:
        raise BackupEncryptionError("Kein Master-Key konfiguriert")
    try:
        # urlsafe_b64decode verlangt korrektes Padding. ``token_urlsafe``
        # liefert Strings ohne Padding; wir ergaenzen es.
        padded = master_key_b64 + "=" * (-len(master_key_b64) % 4)
        key = base64.urlsafe_b64decode(padded)
    except (ValueError, TypeError, binascii.Error) as e:
        raise BackupEncryptionError("Ungueltiger Master-Key") from e
    if len(key) != KEY_SIZE:
        # Wir nennen die erwartete Laenge nicht (kein Hint fuer Brute-Force),
        # nur die Tatsache dass es nicht passt.
        raise BackupEncryptionError("Ungueltiger Master-Key")
    return key


def encrypt_file(src: Path, dst: Path, master_key_b64: str) -> int:
    """Verschluesselt ``src`` → ``dst``.

    Output-Format: ``[version:1][nonce:12][ciphertext+tag]``.
    Gibt die Anzahl geschriebener Bytes zurueck (fuer size_mb).

    Bei Fehler wird ``dst`` wieder entfernt (kein Partial-Write).
    """
    key = _load_key(master_key_b64)
    try:
        plaintext = src.read_bytes()
    except OSError as e:
        raise BackupEncryptionError("Quelldatei nicht lesbar") from e

    nonce = secrets.token_bytes(NONCE_SIZE)
    aesgcm = AESGCM(key)
    try:
        ciphertext = aesgcm.encrypt(nonce, plaintext, associated_data=None)
    except (ValueError, TypeError) as e:
        # AESGCM raises nur bei Programmierfehlern (z. B. falsche Key-Laenge).
        raise BackupEncryptionError("Verschluesselung fehlgeschlagen") from e

    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(dst, "wb") as f:
            f.write(bytes([FORMAT_VERSION]))
            f.write(nonce)
            f.write(ciphertext)
    except OSError:
        # Best-Effort Cleanup
        if dst.exists():
            try:
                dst.unlink()
            except OSError:
                pass
        raise BackupEncryptionError("Zieldatei nicht schreibbar")

    written = 1 + NONCE_SIZE + len(ciphertext)
    return written


def decrypt_file(src: Path, dst: Path, master_key_b64: str) -> int:
    """Entschluesselt ``src`` → ``dst``.

    Input-Format: ``[version:1][nonce:12][ciphertext+tag]``.
    Wirft ``BackupEncryptionError`` bei:
    - falschem / unbekanntem Format
    - falschem Key (GCM-Tag-Check failt)
    - Datei zu klein / beschaedigt
    """
    key = _load_key(master_key_b64)
    try:
        raw = src.read_bytes()
    except OSError as e:
        raise BackupEncryptionError("Quelldatei nicht lesbar") from e

    header_size = 1 + NONCE_SIZE
    min_size = header_size + TAG_SIZE
    if len(raw) < min_size:
        raise BackupEncryptionError("Datei zu kurz fuer verschluesseltes Format")

    version = raw[0]
    if version != FORMAT_VERSION:
        raise BackupEncryptionError("Unbekanntes Datei-Format")
    nonce = raw[1:header_size]
    ciphertext = raw[header_size:]

    aesgcm = AESGCM(key)
    try:
        plaintext = aesgcm.decrypt(nonce, ciphertext, associated_data=None)
    except Exception as e:  # cryptography raises InvalidTag (subclass of Exception)
        # Generischer Text: "falscher Key" UND "Datei beschaedigt" produzieren
        # denselben Output — kein Hint fuer Angreifer.
        raise BackupEncryptionError("Entschluesselung fehlgeschlagen") from e

    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        dst.write_bytes(plaintext)
    except OSError:
        if dst.exists():
            try:
                dst.unlink()
            except OSError:
                pass
        raise BackupEncryptionError("Zieldatei nicht schreibbar")
    return len(plaintext)
