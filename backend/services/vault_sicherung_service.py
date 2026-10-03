"""Zugang der Kamera-Sicherung, die auf dem Telefon ohne offene App laeuft.

Der Worker auf dem Telefon hat keine Sitzung: das Refresh-Token rotiert, und
zwei Halter derselben Familie (App und Worker) sperrten sie sich gegenseitig
(AGENTS.md Punkt 62). Er bekommt deshalb einen eigenen Zugang, der nicht
rotiert und nur das darf, was die Sicherung braucht: Blobs anlegen und
hochladen, Datensaetze in den Posteingang legen und eigene wieder loeschen.
Kein Abgleich, kein Salz, kein Lesen.

Grenzen:

- Er entsteht nur mit frischem Nachweis (Punkt 21) und gehoert zu einer
  Sitzungsfamilie. Er gilt nur, solange diese Familie lebt und das Konto
  aktiv ist. Damit faellt er mit jedem Widerruf (Geraet entfernen, Abmelden,
  Passwort, Wiederverwendung; Punkt 20), ohne dass jeder dieser Wege ihn
  kennen muss.
- Er gilt fuer einen Bucket. Ist der beerdigt oder nicht mehr der des Kontos,
  ist es 410, und Zuruecksetzen loescht ihn.
- In der Datenbank steht das Token nur als SHA-256, Konto und Familie nur als
  HMAC (Punkt 73). Konto und Familie stehen im Token selbst; der Hash bindet
  sie, ein Austausch ergaebe ein anderes Token.

Wer das Token ausliest, kann Speicher fuellen und Datensaetze ablegen, die
die Uebernahme ohne Unterschrift des Geraets verwirft (``tresorEingang.ts``).
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from models.refresh_token import RefreshToken
from models.user import User
from models.vault_sicherungszugang import VaultSicherungszugang
from services import vault_blob_service, vault_service
from services.dis_client import DisClient

PRAEFIX = "msz1"

_FAMILIE_INDEX: dict[str, str] = {}


class ZugangUngueltig(Exception):
    """401: unbekannt, abgelaufen oder widerrufen."""


class ZugangZurueckgesetzt(Exception):
    """410: der Bucket ist beerdigt oder nicht mehr der des Kontos."""


@dataclass(frozen=True)
class Sicherung:
    user: User
    bucket: str


def familie_index(familie: str) -> str:
    """HMAC der Sitzungsfamilie aus dem Sidecar, im Prozess gemerkt. Wirft ``DisSidecarError``."""
    index = _FAMILIE_INDEX.get(familie)
    if index is None:
        index = DisClient.blind_index([f"vault:familie\n{familie}"])[0]
        _FAMILIE_INDEX[familie] = index
    return index


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def anlegen(db: Session, konto: vault_service.TresorKonto, familie: str, fam_index: str, bucket: str) -> str:
    """Legt den Zugang an und ersetzt den bisherigen derselben Sitzung. Committet nicht.

    ``fam_index`` rechnet der Aufrufer vorher, damit ein fehlender Sidecar
    keinen schon verbrauchten Nachweis kostet (Punkt 82).
    """
    entfernen(db, konto, fam_index)
    token = f"{PRAEFIX}.{konto.user_id}.{familie}.{secrets.token_urlsafe(32)}"
    db.add(
        VaultSicherungszugang(
            id=str(uuid.uuid4()),
            token_hash=_hash(token),
            konto_index=konto.index,
            familie_index=fam_index,
            bucket_id=bucket,
        )
    )
    return token


def entfernen(db: Session, konto: vault_service.TresorKonto, fam_index: str) -> None:
    """Loescht den Zugang dieser Sitzung. Committet nicht."""
    db.query(VaultSicherungszugang).filter(
        VaultSicherungszugang.konto_index == konto.index,
        VaultSicherungszugang.familie_index == fam_index,
    ).delete(synchronize_session=False)


def _familie_lebt(db: Session, user_id: int, familie: str) -> bool:
    jetzt = datetime.now(timezone.utc)
    return (
        db.query(RefreshToken.id)
        .filter(
            RefreshToken.user_id == user_id,
            RefreshToken.family == familie,
            RefreshToken.revoked_at.is_(None),
            RefreshToken.expires_at > jetzt,
        )
        .first()
        is not None
    )


def pruefen(db: Session, token: str) -> Sicherung:
    """Wer mit diesem Token spricht und fuer welchen Bucket. Wirft ``ZugangUngueltig``,
    ``ZugangZurueckgesetzt`` oder ``DisSidecarError``."""
    teile = token.split(".")
    if len(teile) != 4 or teile[0] != PRAEFIX or not teile[1].isdigit() or not teile[2] or len(teile[2]) > 64:
        raise ZugangUngueltig()
    user_id, familie = int(teile[1]), teile[2]
    zeile = db.query(VaultSicherungszugang).filter(VaultSicherungszugang.token_hash == _hash(token)).first()
    if zeile is None:
        raise ZugangUngueltig()
    user = db.get(User, user_id)
    if user is not None and user.is_active and not _familie_lebt(db, user_id, familie):
        # Eine widerrufene oder abgelaufene Familie lebt nie wieder auf. Der
        # Zugang ist danach nur noch ein Rest, der Konto und Bucket nennt; der
        # Aufrufer committet das Löschen.
        db.delete(zeile)
        raise ZugangUngueltig()
    if user is None or not user.is_active:
        raise ZugangUngueltig()
    konto = vault_service.tresor_konto(user_id)
    if zeile.konto_index != konto.index or zeile.familie_index != familie_index(familie):
        raise ZugangUngueltig()
    if vault_blob_service.ist_beerdigt(db, zeile.bucket_id):
        raise ZugangZurueckgesetzt()
    try:
        bucket = vault_service.eigener_bucket(db, konto)
    except vault_service.VaultOhneBucket as exc:
        raise ZugangZurueckgesetzt() from exc
    if bucket != zeile.bucket_id:
        raise ZugangZurueckgesetzt()
    return Sicherung(user=user, bucket=bucket)
