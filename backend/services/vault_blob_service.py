"""Blob-Speicher der Tresor-Cloud.

Der Server bewahrt hier nur Chiffrat auf. Jeder Blob besteht aus Chunks von
4 MiB Klartext; verschluesselt sind es 28 Byte mehr (12 Byte IV, 16 Byte Tag,
das Format von DIS ``encryptChunk``, als Bytes statt Base64). Der Client
polstert vorher auf Groessenklassen, die echte Groesse steht nur im
Tresor-Eintrag. Name, Typ und Schluessel kennt der Server nicht.

Was er erzwingt:

- Jeder Blob gehoert einem Bucket; ein fremder ist wie ein fehlender (404).
  Welches Konto den Bucket hat, steht nur als HMAC in ``vault_user_settings``;
  die Routen bekommen den Bucket des angemeldeten Kontos von
  ``vault_service.eigener_bucket``.
- Die Quote wird beim Anlegen unter einer Sperre je Bucket reserviert. Ihre
  Grenze kommt aus den Rollen des angemeldeten Kontos (``quote_fuer``). Sie zaehlt
  offene, fertige und geloeschte Blobs: auch ein geloeschter belegt die Platte,
  bis die Loeschhaltung um ist. Sonst liesse sich die Platte mit
  Hochladen-Loeschen-Hochladen fuellen.
- Loeschen verlangt den Loeschschluessel aus dem Tresor-Eintrag. Ein
  abgegriffenes Zugangstoken allein loescht nichts.
- Geloescht wird erst nach ``LOESCHHALTUNG``; bis dahin kann ein Betreiber
  einen Fehler noch rueckgaengig machen.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import shutil
import struct
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from config import settings
from models import Role, RoleVaultQuota
from models.user import User
from models.vault_blob import VaultBlob
from services.role_service import effective_user_role_ids

CHUNK_KLARTEXT = 4 * 1024 * 1024
CHUNK_UEBERHANG = 12 + 16
CHUNK_CHIFFRAT = CHUNK_KLARTEXT + CHUNK_UEBERHANG

LOESCHHALTUNG = timedelta(days=7)
OFFEN_HOECHSTENS = timedelta(hours=24)
MAX_BLOBS_JE_BUCKET = 500_000
# Ein Blob, groesser als jede sinnvolle Quote. Haelt die Zahlen klein, bevor die
# Quote greift.
MAX_BLOB_BYTES = 1024 * 1024 * 1024 * 1024
# Obergrenze fuer jede eingestellte Quote (1 PiB); groesser ist ein Tippfehler.
MAX_QUOTE = 1024**5
# So viel muss auf der Platte frei bleiben, auch wenn die Quote mehr erlaubt.
PLATTENRESERVE = 1024 * 1024 * 1024

# Sammelabruf fuer Miniaturen: nur einteilige Blobs bis zu dieser Groesse.
KLEIN_GRENZE = 64 * 1024 + CHUNK_UEBERHANG
KLEIN_MAX_ANZAHL = 100

_HEX32 = re.compile(r"^[0-9a-f]{32}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class BlobFehler(Exception):
    status_code = 400
    code: str | None = None


class BlobNichtGefunden(BlobFehler):
    status_code = 404


class BlobKonflikt(BlobFehler):
    status_code = 409


class BlobUngueltig(BlobFehler):
    status_code = 422


class BlobZuGross(BlobFehler):
    status_code = 413


class SpeicherVoll(BlobFehler):
    status_code = 507


class LoeschnachweisFalsch(BlobFehler):
    status_code = 403
    # Eigener Code: 403 kommt auch von CSRF und vom abgeschalteten Tresor, und
    # danach muss der Client es spaeter noch einmal versuchen.
    code = "VAULT_LOESCHNACHWEIS_FALSCH"


def _jetzt() -> datetime:
    return datetime.now(timezone.utc)


def ist_blob_id(blob_id: str) -> bool:
    return bool(_HEX32.match(blob_id or ""))


def _basis() -> Path:
    return Path(settings.vault_blob_dir)


def blob_verzeichnis(blob_id: str) -> Path:
    """Wo die Chunks eines Blobs liegen. Die Kennung ist geprueft, bevor sie in einen Pfad geht."""
    if not ist_blob_id(blob_id):
        raise BlobNichtGefunden("Blob nicht gefunden.")
    return _basis() / blob_id[:2] / blob_id


def erwartete_laenge(blob: VaultBlob, index: int) -> int:
    if index < 0 or index >= blob.chunk_count:
        raise BlobUngueltig("Chunk ausserhalb des Blobs.")
    if index < blob.chunk_count - 1:
        return CHUNK_CHIFFRAT
    return blob.bytes_total - (blob.chunk_count - 1) * CHUNK_CHIFFRAT


def _groesse_passt(chunk_count: int, bytes_total: int) -> bool:
    """Alle Chunks bis auf den letzten sind voll; der letzte traegt mindestens ein Byte Klartext."""
    if chunk_count < 1 or bytes_total > MAX_BLOB_BYTES:
        return False
    voll = (chunk_count - 1) * CHUNK_CHIFFRAT
    return voll + CHUNK_UEBERHANG + 1 <= bytes_total <= voll + CHUNK_CHIFFRAT


def quote_fuer(db: Session, user: User) -> int:
    """Der Speicher eines Kontos: der hoechste Wert seiner Rollen.

    Ohne Rolle mit Speicher ist es 0, das Konto laedt nichts hoch; vorhandene
    Dateien bleiben lesbar und loeschbar. Der Owner ist wie bei den Rechten
    nicht begrenzt, er hat oft gar keine Rolle.
    """
    if user.is_owner:
        return MAX_QUOTE
    rollen = effective_user_role_ids(db, user)
    if not rollen:
        return 0
    hoechste = db.scalar(select(func.max(RoleVaultQuota.quota_bytes)).where(RoleVaultQuota.role_id.in_(rollen)))
    return max(0, int(hoechste or 0))


def rollen_speicher(db: Session) -> list[dict]:
    """Jede Rolle mit ihrem Speicher (``None``: keiner) fuer die Einstellungen."""
    gesetzt = dict(db.execute(select(RoleVaultQuota.role_id, RoleVaultQuota.quota_bytes)).all())
    rollen = db.execute(select(Role.id, Role.name).order_by(Role.is_system.desc(), Role.name.asc())).all()
    return [{"role_id": rid, "role_name": name, "quota_bytes": gesetzt.get(rid)} for rid, name in rollen]


def rolle_speicher(db: Session, role_id: int) -> int | None:
    return db.scalar(select(RoleVaultQuota.quota_bytes).where(RoleVaultQuota.role_id == role_id))


def rolle_speicher_setzen(db: Session, role_id: int, quota_bytes: int | None) -> None:
    """Setzt den Speicher einer Rolle in der offenen Transaktion; ``None`` nimmt ihn weg."""
    if db.get(Role, role_id) is None:
        raise ValueError("Rolle nicht gefunden")
    if quota_bytes is not None and (isinstance(quota_bytes, bool) or not 0 <= int(quota_bytes) <= MAX_QUOTE):
        raise ValueError("Ungueltiger Speicherwert")
    zeile = db.get(RoleVaultQuota, role_id)
    if quota_bytes is None:
        if zeile is not None:
            db.delete(zeile)
    elif zeile is None:
        db.add(RoleVaultQuota(role_id=role_id, quota_bytes=int(quota_bytes)))
    else:
        zeile.quota_bytes = int(quota_bytes)
        zeile.updated_at = datetime.now(timezone.utc)
    db.flush()


def belegt(db: Session, bucket_id: str) -> int:
    return int(
        db.scalar(select(func.coalesce(func.sum(VaultBlob.bytes_total), 0)).where(VaultBlob.bucket_id == bucket_id))
        or 0
    )


def _sperre_bucket(db: Session, bucket_id: str) -> None:
    """Serialisiert das Reservieren der Quote eines Buckets bis zum Ende der Transaktion."""
    schluessel = int.from_bytes(hashlib.sha256(f"vault-blob:{bucket_id}".encode()).digest()[:8], "big", signed=True)
    db.execute(text("SELECT pg_advisory_xact_lock(:schluessel)"), {"schluessel": schluessel})


def anlegen(
    db: Session, user: User, bucket_id: str, blob_id: str, chunk_count: int, bytes_total: int, delete_verifier: str
) -> VaultBlob:
    if not ist_blob_id(blob_id) or not _HEX64.match(delete_verifier or ""):
        raise BlobUngueltig("Ungueltige Kennung.")
    if not _groesse_passt(chunk_count, bytes_total):
        raise BlobUngueltig("Chunkzahl und Groesse passen nicht zusammen.")

    _sperre_bucket(db, bucket_id)
    if db.get(VaultBlob, blob_id) is not None:
        raise BlobKonflikt("Diese Kennung ist vergeben.")
    anzahl = int(db.scalar(select(func.count()).select_from(VaultBlob).where(VaultBlob.bucket_id == bucket_id)) or 0)
    if anzahl >= MAX_BLOBS_JE_BUCKET:
        raise SpeicherVoll("Zu viele Dateien in diesem Tresor.")
    if belegt(db, bucket_id) + bytes_total > quote_fuer(db, user):
        raise SpeicherVoll("Der Speicher dieses Kontos ist voll.")

    basis = _basis()
    basis.mkdir(parents=True, exist_ok=True, mode=0o700)
    if shutil.disk_usage(basis).free < bytes_total + PLATTENRESERVE:
        raise SpeicherVoll("Auf dem Server ist kein Platz mehr frei.")

    blob = VaultBlob(
        id=blob_id,
        bucket_id=bucket_id,
        chunk_count=chunk_count,
        bytes_total=bytes_total,
        delete_verifier=delete_verifier,
        state="offen",
        created_at=_jetzt(),
    )
    db.add(blob)
    db.commit()
    return blob


def eigener_blob(db: Session, bucket_id: str, blob_id: str) -> VaultBlob:
    if not ist_blob_id(blob_id):
        raise BlobNichtGefunden("Blob nicht gefunden.")
    blob = db.get(VaultBlob, blob_id)
    if blob is None or blob.bucket_id != bucket_id:
        raise BlobNichtGefunden("Blob nicht gefunden.")
    return blob


def laenge_fuer_upload(db: Session, bucket_id: str, blob_id: str, index: int) -> int:
    blob = eigener_blob(db, bucket_id, blob_id)
    if blob.state != "offen":
        raise BlobKonflikt("Dieser Blob nimmt keine Chunks mehr an.")
    return erwartete_laenge(blob, index)


def chunk_schreiben(blob_id: str, index: int, daten: bytes) -> None:
    """Schreibt einen Chunk. Erst unter eigenem Namen, dann atomar an seinen Platz:
    zwei Anfragen fuer denselben Chunk ueberschreiben sich, mischen sich aber nie."""
    verzeichnis = blob_verzeichnis(blob_id)
    verzeichnis.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Die Pruefung beim Reservieren bestehen beliebig viele Uploads zugleich;
    # erst hier, wo geschrieben wird, haelt sie die Reserve wirklich frei.
    if shutil.disk_usage(verzeichnis).free < len(daten) + PLATTENRESERVE:
        raise SpeicherVoll("Auf dem Server ist kein Platz mehr frei.")
    zwischen = verzeichnis / f"{index}.{secrets.token_hex(8)}.part"
    try:
        with open(zwischen, "wb") as datei:
            datei.write(daten)
            datei.flush()
            os.fsync(datei.fileno())
        os.replace(zwischen, verzeichnis / str(index))
    finally:
        zwischen.unlink(missing_ok=True)


def vorhandene_chunks(blob: VaultBlob) -> list[int]:
    verzeichnis = blob_verzeichnis(blob.id)
    vorhanden: list[int] = []
    for index in range(blob.chunk_count):
        pfad = verzeichnis / str(index)
        try:
            if pfad.stat().st_size == erwartete_laenge(blob, index):
                vorhanden.append(index)
        except FileNotFoundError:
            continue
    return vorhanden


def fertigstellen(db: Session, bucket_id: str, blob_id: str) -> None:
    blob = eigener_blob(db, bucket_id, blob_id)
    if blob.state == "fertig":
        return
    if blob.state != "offen":
        raise BlobKonflikt("Dieser Blob ist geloescht.")
    if len(vorhandene_chunks(blob)) != blob.chunk_count:
        raise BlobKonflikt("Es fehlen noch Chunks.")
    ergebnis = db.execute(
        update(VaultBlob)
        .where(VaultBlob.id == blob_id, VaultBlob.bucket_id == bucket_id, VaultBlob.state == "offen")
        .values(state="fertig", completed_at=_jetzt())
    )
    db.commit()
    if ergebnis.rowcount == 0:
        db.refresh(blob)
        if blob.state != "fertig":
            raise BlobKonflikt("Dieser Blob ist geloescht.")


def chunk_pfad(db: Session, bucket_id: str, blob_id: str, index: int) -> Path:
    blob = eigener_blob(db, bucket_id, blob_id)
    if blob.state != "fertig":
        raise BlobNichtGefunden("Blob nicht gefunden.")
    erwartete_laenge(blob, index)
    pfad = blob_verzeichnis(blob_id) / str(index)
    if not pfad.is_file():
        raise BlobNichtGefunden("Chunk fehlt.")
    return pfad


def kleine_lesen(db: Session, bucket_id: str, ids: Sequence[str]) -> bytes:
    """Viele Miniaturen in einer Antwort: je angefragter Kennung, in dieser
    Reihenfolge, 4 Byte Laenge (big endian) und dann der Chunk. Laenge 0 heisst:
    nicht da, nicht fertig, fremd oder zu gross fuer diesen Weg."""
    gueltig = [blob_id for blob_id in ids if ist_blob_id(blob_id)]
    zeilen = {
        blob.id: blob
        for blob in db.scalars(
            select(VaultBlob).where(
                VaultBlob.id.in_(gueltig),
                VaultBlob.bucket_id == bucket_id,
                VaultBlob.state == "fertig",
                VaultBlob.chunk_count == 1,
                VaultBlob.bytes_total <= KLEIN_GRENZE,
            )
        ).all()
    }
    teile: list[bytes] = []
    for blob_id in ids:
        daten = b""
        if blob_id in zeilen:
            try:
                daten = (blob_verzeichnis(blob_id) / "0").read_bytes()
            except FileNotFoundError:
                daten = b""
            if len(daten) != zeilen[blob_id].bytes_total:
                daten = b""
        teile.append(struct.pack(">I", len(daten)))
        teile.append(daten)
    return b"".join(teile)


def loeschen(db: Session, bucket_id: str, blob_id: str, schluessel_hex: str) -> None:
    blob = eigener_blob(db, bucket_id, blob_id)
    try:
        schluessel = bytes.fromhex(schluessel_hex)
    except ValueError as exc:
        raise LoeschnachweisFalsch("Loeschnachweis passt nicht.") from exc
    if len(schluessel) != 32 or not secrets.compare_digest(
        hashlib.sha256(schluessel).hexdigest(), blob.delete_verifier
    ):
        raise LoeschnachweisFalsch("Loeschnachweis passt nicht.")
    db.execute(
        update(VaultBlob)
        .where(VaultBlob.id == blob_id, VaultBlob.bucket_id == bucket_id, VaultBlob.state != "geloescht")
        .values(state="geloescht", deleted_at=_jetzt())
    )
    db.commit()


def speicher(db: Session, user: User, bucket_id: str | None) -> dict[str, int]:
    """Belegung des Buckets und Quote des Kontos. Ohne Bucket ist nichts belegt."""
    quote = quote_fuer(db, user)
    if bucket_id is None:
        return {"belegt": 0, "quote": quote, "in_loeschung": 0, "blobs": 0}
    in_loeschung = int(
        db.scalar(
            select(func.coalesce(func.sum(VaultBlob.bytes_total), 0)).where(
                VaultBlob.bucket_id == bucket_id, VaultBlob.state == "geloescht"
            )
        )
        or 0
    )
    blobs = int(
        db.scalar(
            select(func.count()).select_from(VaultBlob).where(
                VaultBlob.bucket_id == bucket_id, VaultBlob.state != "geloescht"
            )
        )
        or 0
    )
    return {"belegt": belegt(db, bucket_id), "quote": quote, "in_loeschung": in_loeschung, "blobs": blobs}


def alle_zur_loeschung(db: Session, bucket_id: str) -> None:
    """Gibt alle Blobs des Buckets zum Entfernen frei, ohne Loeschhaltung. Committet nicht.

    Fuer Zuruecksetzen und Kontoloeschung: danach gehoert der Bucket keinem
    Konto mehr, und seine Blobs zaehlen gegen keinen Speicher. Mit sieben
    Tagen Haltung liesse sich die Platte durch Hochladen und Zuruecksetzen
    beliebig oft fuellen. Das naechste Aufraeumen (stuendlich) entfernt sie.
    """
    db.execute(
        update(VaultBlob)
        .where(VaultBlob.bucket_id == bucket_id)
        .values(state="geloescht", deleted_at=_jetzt() - LOESCHHALTUNG)
    )


def _entfernen(blob_id: str) -> None:
    shutil.rmtree(blob_verzeichnis(blob_id), ignore_errors=True)


def aufraeumen(db: Session, jetzt: datetime | None = None) -> dict[str, int]:
    """Entfernt abgebrochene Uploads, Blobs nach der Loeschhaltung und
    Verzeichnisse ohne Zeile (etwa nach der Umstellung auf Buckets)."""
    jetzt = jetzt or _jetzt()
    faellig = db.scalars(
        select(VaultBlob.id).where(
            ((VaultBlob.state == "offen") & (VaultBlob.created_at < jetzt - OFFEN_HOECHSTENS))
            | ((VaultBlob.state == "geloescht") & (VaultBlob.deleted_at < jetzt - LOESCHHALTUNG))
        )
    ).all()
    for blob_id in faellig:
        _entfernen(blob_id)
        db.execute(VaultBlob.__table__.delete().where(VaultBlob.id == blob_id))
    db.commit()

    verwaist = 0
    basis = _basis()
    if basis.is_dir():
        for fach in basis.iterdir():
            if not fach.is_dir():
                continue
            namen = [eintrag.name for eintrag in fach.iterdir() if eintrag.is_dir() and ist_blob_id(eintrag.name)]
            if not namen:
                continue
            bekannt = set(db.scalars(select(VaultBlob.id).where(VaultBlob.id.in_(namen))).all())
            for name in namen:
                if name not in bekannt:
                    _entfernen(name)
                    verwaist += 1
    return {"entfernt": len(faellig), "verwaist": verwaist}
