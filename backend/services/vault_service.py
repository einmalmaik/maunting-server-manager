from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import html
import secrets
from typing import Sequence
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models.user import User
from models.vault_entry import VaultEntry
from models.vault_hint import VaultHint
from models.vault_user_setting import VaultUserSetting
from models.vault_blind_bucket import VaultBlindBucket
from models.vault_bucket_format import VaultBucketFormat
from schemas.vault import (
    VaultBlindSyncRequest,
    VaultEntryOut,
    VaultHintStatusResponse,
    VaultMutation,
    VaultSaltResponse,
    VaultSyncRequest,
    VaultSyncResponse,
)
from services import vault_blob_service
from services.auth_service import AuthService
from services.dis_client import DisClient


def _now() -> datetime:
    return datetime.now(timezone.utc)


class VaultBucketAccessDenied(Exception):
    """Raised when a user attempts to access a bucket not belonging to them."""


class VaultBucketUnauthorized(Exception):
    """Raised when an invalid auth_token is supplied for a blind vault bucket."""


class VaultBucketAlreadyBound(Exception):
    """Raised when a blind verifier already exists and must not be overwritten."""


class VaultClientZuAlt(Exception):
    """Die App ist aelter als das Format, das dieser Bucket verlangt."""


class VaultOhneBucket(Exception):
    """Das Konto hat keinen Bucket; die Dateien wissen nicht, wohin."""


# ─── Das Konto in den Tresortabellen ─────────────────────────────────────────
#
# Die Tresortabellen nennen kein Konto im Klartext. Bis 01.10.2026 stand die
# `user_id` in `vault_user_settings`, `vault_hints` und `vault_blobs`: wer die
# Datenbank las, sah, wem welcher Tresor gehoert und wie viele und wie grosse
# Dateien darin liegen. Heute steht dort ein HMAC der Kontonummer, den nur der
# DIS-Sidecar rechnen kann. Der Server erfaehrt die Zuordnung, solange eine
# angemeldete Anfrage laeuft.

_KONTO_INDEX: dict[int, str] = {}


@dataclass(frozen=True)
class TresorKonto:
    """Ein Konto, wie die Tresortabellen es kennen.

    ``index`` steht in der Datenbank. ``user_id`` dient nur dem Altbestand
    und der AAD des Hinweises; geschrieben wird sie nie.
    """

    user_id: int
    index: str


def tresor_konto(user_id: int) -> TresorKonto:
    """Rechnet den Index ueber den Sidecar und merkt ihn sich im Prozess.

    Der Wert haengt nur an Kontonummer und Sidecar-Schluessel, er aendert sich
    also nie. Scheitert der Sidecar, fliegt ``DisSidecarError``, und nichts
    wird gemerkt.
    """
    index = _KONTO_INDEX.get(user_id)
    if index is None:
        index = DisClient.blind_index([f"vault:konto\n{user_id}"])[0]
        _KONTO_INDEX[user_id] = index
    return TresorKonto(user_id=user_id, index=index)


def _eigene_zeile(db: Session, modell, konto: TresorKonto):
    """Die Zeile des Kontos in ``vault_user_settings`` bzw. ``vault_hints``.

    Findet sie den Index nicht, sucht sie nach der alten ``user_id`` und
    stellt die Zeile gleich um. Ohne diesen Rueckfall waere ein Tresor
    zwischen Update und Nachzug beim Start unsichtbar.
    """
    zeile = db.scalar(select(modell).where(modell.konto_index == konto.index))
    if zeile is None:
        zeile = db.scalar(select(modell).where(modell.user_id == konto.user_id))
        if zeile is not None:
            zeile.konto_index = konto.index
            zeile.user_id = None
            db.flush()
    return zeile


def kontoindex_nachziehen(db: Session) -> set[str]:
    """Setzt den Index im Altbestand und leert die ``user_id``. Laeuft beim Start.

    Gibt die Tabellen zurueck, in denen sich etwas geaendert hat: dort liegt
    die alte Zeilenversion mit ``user_id`` noch in der Datei, bis VACUUM FULL
    sie entfernt.
    """
    geaendert: set[str] = set()
    for modell in (VaultUserSetting, VaultHint):
        zeilen = db.scalars(select(modell).where(modell.user_id.isnot(None))).all()
        for anfang in range(0, len(zeilen), 1000):
            teil = zeilen[anfang:anfang + 1000]
            indizes = DisClient.blind_index([f"vault:konto\n{z.user_id}" for z in teil])
            for zeile, index in zip(teil, indizes):
                _KONTO_INDEX[zeile.user_id] = index
                zeile.konto_index = index
                zeile.user_id = None
            db.commit()
        if zeilen:
            geaendert.add(modell.__tablename__)
    return geaendert


def einstellung(db: Session, konto: TresorKonto) -> VaultUserSetting | None:
    return _eigene_zeile(db, VaultUserSetting, konto)


def konto_umstellen(db: Session, konto: TresorKonto) -> None:
    """Stellt den Altbestand eines Kontos auf den Index um. Committet nicht."""
    _eigene_zeile(db, VaultUserSetting, konto)
    _eigene_zeile(db, VaultHint, konto)


def eigener_bucket(db: Session, konto: TresorKonto) -> str:
    """Der Bucket des Kontos, an dem seine Dateien haengen."""
    zeile = einstellung(db, konto)
    if zeile is None or not zeile.bucket_id:
        raise VaultOhneBucket("Zu diesem Konto ist kein Tresor hinterlegt.")
    return zeile.bucket_id


def _pruefe_format(db: Session, bucket_id: str, client_format: int | None, min_client_format: int | None) -> None:
    """Laesst nur Apps an den Bucket, die seine Eintraege beim Speichern nicht verstuemmeln.

    Eine App vor 09/2026 schreibt jeden Eintrag aus einer festen Feldliste neu
    und verliert dabei Papierkorb, Archiv und Dateiverweise. Hat eine neuere App
    den Bucket einmal hochgestuft, weist der Server aeltere ab, lesend wie
    schreibend: sie wuerde die fremden Eintraege sonst als ihre eigenen zeigen.
    Herabstufen geht nicht.
    """
    eigenes = client_format or 0
    zeile = db.get(VaultBucketFormat, bucket_id)
    verlangt = zeile.min_client_format if zeile is not None else 0
    if eigenes < verlangt:
        raise VaultClientZuAlt("Diese App ist zu alt fuer diesen Tresor. Bitte aktualisieren.")
    if min_client_format and min_client_format > verlangt and min_client_format <= eigenes:
        if zeile is None:
            db.add(VaultBucketFormat(bucket_id=bucket_id, min_client_format=min_client_format, updated_at=_now()))
        else:
            zeile.min_client_format = min_client_format
            zeile.updated_at = _now()
        db.commit()


# ─── Gemeinsamer Kern beider Sync-Pfade ──────────────────────────────────────
#
# Die Leitregel des Tresors, aus der die Autorisierung beider Endpunkte folgt:
#
#     Ein Bucket, der bereits Daten traegt, darf niemals von einem neuen
#     Prinzipal beansprucht werden — weder blind noch angemeldet.
#
# Bis zum Audit vom 22.09.2026 galt das an keiner der beiden Stellen: der blinde
# Pfad registrierte jeden unbekannten Bucket per Trust-On-First-Use und loeste
# dabei die Kontokopplung, der Cookie-Pfad nahm jeden ungekoppelten Bucket in
# Besitz. Zusammen ergab das eine unauthentifizierte Uebernahme fremder Tresore.


def _bucket_hat_eintraege(db: Session, bucket_id: str) -> bool:
    """Traegt dieser Bucket bereits Ciphertext — also etwas zu verlieren?"""
    return db.scalar(
        select(VaultEntry.id).where(VaultEntry.bucket_id == bucket_id).limit(1)
    ) is not None


def _bucket_besitzer(db: Session, bucket_id: str) -> str | None:
    """Der Kontoindex, an den dieser Bucket gekoppelt ist — oder None.

    Eine Altzeile ohne Index zaehlt als gekoppelt: sie bekommt ``alt:`` und
    passt damit auf kein Konto. Wer sie besitzt, hat sie vorher beim Lesen
    ueber ``einstellung`` schon umgestellt.
    """
    zeile = db.execute(
        select(VaultUserSetting.konto_index, VaultUserSetting.user_id).where(VaultUserSetting.bucket_id == bucket_id)
    ).first()
    if zeile is None:
        return None
    return zeile.konto_index or f"alt:{zeile.user_id}"


def _sperre_bucket(db: Session, bucket_id: str) -> None:
    """Serialisiert die Revisionsvergabe eines Buckets gegen parallele Syncs.

    Ohne diese Sperre lesen zwei gleichzeitige Syncs dasselbe ``max(revision)``
    und vergeben beide dieselbe naechste Nummer. Zwei Eintraege mit gleicher
    Revision sind eine Luecke im Wasserzeichen des Clients: wer den einen sieht
    und ``since_revision`` fortschreibt, bekommt den anderen nie wieder
    angeboten — stiller Datenverlust in einem Tresor.

    PostgreSQL haelt die Sperre bis zum Ende der Transaktion, also bis zum
    ``commit`` des Aufrufers.
    """
    schluessel = int.from_bytes(
        hashlib.sha256(bucket_id.encode("utf-8")).digest()[:8], "big", signed=True
    )
    db.execute(text("SELECT pg_advisory_xact_lock(:schluessel)"), {"schluessel": schluessel})


def _wende_mutationen_an(
    db: Session, bucket_id: str, mutations: Sequence[VaultMutation]
) -> list[str]:
    """Schreibt die Mutationen des Clients und vergibt monoton steigende Revisionen.

    Die naechste Revision kommt ausschliesslich aus dem Serverstand. Das
    ``since_revision`` des Clients geht bewusst **nicht** mehr ein: es ist ein
    frei waehlbares Feld, und mit dem Schema-Maximum (2^53-1) liess sich der
    Zaehler eines Buckets in einem einzigen Request ueber die sichere
    Ganzzahlgrenze von JavaScript heben — danach rechnet jeder Client falsch.

    Liefert die IDs der Mutationen, die an ``expected_revision`` gescheitert sind.
    """
    if not mutations:
        return []

    _sperre_bucket(db, bucket_id)

    max_rev_db = db.scalar(
        select(func.max(VaultEntry.revision)).where(VaultEntry.bucket_id == bucket_id)
    ) or 0
    current_rev = int(max_rev_db)

    mutation_ids = [m.id for m in mutations]
    existing_stmt = select(VaultEntry).where(
        VaultEntry.bucket_id == bucket_id,
        VaultEntry.id.in_(mutation_ids),
    )
    existing_map = {row.id: row for row in db.scalars(existing_stmt).all()}

    konflikte: list[str] = []
    for m in mutations:
        existing = existing_map.get(m.id)
        if (
            m.expected_revision is not None
            and existing is not None
            and existing.revision != m.expected_revision
        ):
            konflikte.append(m.id)
            continue
        current_rev += 1
        if existing:
            existing.ciphertext = m.ciphertext
            existing.revision = current_rev
            existing.is_deleted = m.is_deleted
            existing.updated_at = _now()
        else:
            new_entry = VaultEntry(
                id=m.id,
                bucket_id=bucket_id,
                ciphertext=m.ciphertext,
                revision=current_rev,
                is_deleted=m.is_deleted,
                created_at=_now(),
                updated_at=_now(),
            )
            db.add(new_entry)
            existing_map[m.id] = new_entry

    db.commit()
    return konflikte


# Hoechstens so viele Eintraege je Antwort. Mit der Tresor-Cloud hat ein Tresor
# leicht zehntausende; alles in einer Antwort waeren dutzende Megabyte. Den Rest
# holt der Client in weiteren Runden (``has_more``). Aeltere Clients kennen das
# Feld nicht und holen ihn beim naechsten Sync: ihr Wasserzeichen ist die
# hoechste gelieferte Revision.
SYNC_SEITE = 2000


def _lies_bucket(db: Session, bucket_id: str, since_revision: int) -> VaultSyncResponse:
    """Liefert alles ab ``since_revision`` — und ein Wasserzeichen, das nie zu weit zeigt.

    ``server_revision`` ist die hoechste **tatsaechlich ausgelieferte** Revision.
    Frueher kam sie aus einer zweiten Abfrage ueber ``max(revision)``: committete
    ein anderes Geraet zwischen den beiden Abfragen, meldete der Server einen
    Stand, zu dem er den passenden Eintrag nie geschickt hatte. Der Client
    schreibt so ein Wasserzeichen fort und fragt kuenftig nur noch ``revision >``
    — der fremde Eintrag war damit fuer dieses Geraet dauerhaft unsichtbar.
    """
    entries_db = db.scalars(
        select(VaultEntry)
        .where(
            VaultEntry.bucket_id == bucket_id,
            VaultEntry.revision > since_revision,
        )
        .order_by(VaultEntry.revision.asc())
        .limit(SYNC_SEITE + 1)
    ).all()
    has_more = len(entries_db) > SYNC_SEITE
    entries_db = entries_db[:SYNC_SEITE]

    entries_out = [
        VaultEntryOut(
            id=e.id,
            ciphertext=e.ciphertext,
            revision=e.revision,
            is_deleted=e.is_deleted,
            updated_at=e.updated_at,
        )
        for e in entries_db
    ]

    hoechste_geliefert = max((e.revision for e in entries_out), default=int(since_revision))
    return VaultSyncResponse(
        server_revision=int(hoechste_geliefert),
        entries=entries_out,
        has_more=has_more,
    )


def sync_vault(db: Session, konto: TresorKonto, request: VaultSyncRequest) -> VaultSyncResponse:
    """Führt einen deterministischen Revisions-Sync für einen blinden Tresor-Bucket durch.

    Sicherheits-Invariante:
    - Der Server kennt keine Benutzernamen, Klartexte oder Passwörter.
    - Bucket-Autorisierung: Jeder Benutzer darf ausschließlich seinen eigenen Bucket syncen.
    - Monotone Revision: Jede serverseitige Mutation erhält eine aufsteigende Revisionsnummer.
    """
    bucket_id = request.bucket_id.lower()

    # 1. Bucket-Autorisierung (SEC-02: IDOR-Schutz)
    # Prüfe, ob dieser Bucket bereits einem ANDEREN Benutzer gehört. Erst die
    # eigene Zeile lesen: sie stellt einen Altbestand auf den Index um.
    user_setting = einstellung(db, konto)
    besitzer = _bucket_besitzer(db, bucket_id)
    if besitzer is not None and besitzer != konto.index:
        raise VaultBucketAccessDenied("Zugriff auf fremden Tresor-Bucket verweigert.")

    # Ein blind registrierter Bucket gehoert seinem Besitznachweis, nicht einem
    # Konto: ueber den Cookie-Pfad darf ihn nur beruehren, wer auch als Besitzer
    # eingetragen ist. Ohne diese Pruefung war `/sync` der Bypass um
    # `/blind-sync` herum — mit Cookie, aber ganz ohne `auth_token`.
    if besitzer is None and db.get(VaultBlindBucket, bucket_id) is not None:
        raise VaultBucketAccessDenied("Dieser Tresor-Bucket ist an einen blinden Besitznachweis gebunden.")

    if user_setting and user_setting.bucket_id and user_setting.bucket_id != bucket_id:
        raise VaultBucketAccessDenied("Nicht autorisierter Tresor-Bucket für dieses Benutzerkonto.")

    if besitzer is None:
        # Erstanspruch: nur auf einen Bucket, in dem nichts liegt. Ein Bucket mit
        # Ciphertext hatte schon einmal einen Besitzer — ihn dem naechsten
        # angemeldeten Konto zuzuschlagen, gaebe dessen Inhalt heraus.
        if _bucket_hat_eintraege(db, bucket_id):
            raise VaultBucketAccessDenied("Zugriff auf fremden Tresor-Bucket verweigert.")
        if user_setting:
            user_setting.bucket_id = bucket_id
            user_setting.updated_at = _now()
        else:
            db.add(
                VaultUserSetting(
                    konto_index=konto.index,
                    bucket_id=bucket_id,
                    created_at=_now(),
                    updated_at=_now(),
                )
            )
        db.commit()

    _pruefe_format(db, bucket_id, request.client_format, request.min_client_format)

    # 2. Monotone Mutation & Revisions-Zuweisung (SEC-03)
    konflikte = _wende_mutationen_an(db, bucket_id, request.mutations)
    antwort = _lies_bucket(db, bucket_id, request.since_revision)
    antwort.conflicts = konflikte
    return antwort


def sync_vault_blind(db: Session, request: VaultBlindSyncRequest) -> VaultSyncResponse:
    """Führt einen blinden, Cookie- und User-unabhängigen Revisions-Sync durch.

    Sicherheits- und Privacy-Invarianten:
    - Der Endpunkt erfordert und kennt kein Benutzerkonto, keine Session-Cookies und keine CSRF-Tokens.
    - Die Autorisierung erfolgt ausschließlich über den blinden Besitznachweis (auth_token).
    - Der Server speichert nur sha256(auth_token) als auth_verifier in konstanter Zeit geprüft.
    - Trust-On-First-Use gilt **nur** für einen jungfräulichen Bucket: ohne Eintrag und ohne
      Kontokopplung. Wer einen bestehenden Tresor auf den blinden Pfad heben will, tut das
      angemeldet über `register_blind_bucket`.

    Warum die enge Grenze (Audit 22.09.2026): vorher registrierte dieser Endpunkt jeden
    unbekannten Bucket und trennte dabei dessen Kontokopplung. Wer eine `bucket_id` kannte —
    und die steht fuer jeden mit DB- oder Log-Lesezugriff im Klartext — bekam mit einem
    einzigen unauthentifizierten Request alle Ciphertexte des Opfers, sperrte es dauerhaft
    aus seinem eigenen Tresor aus und oeffnete nebenbei den Cookie-Pfad fuer jedes beliebige
    angemeldete Konto.
    """
    bucket_id = request.bucket_id.lower()
    auth_token = request.auth_token.lower()
    computed_verifier = hashlib.sha256(auth_token.encode("utf-8")).hexdigest()

    # 1. Blind Bucket lookup
    blind_bucket = db.get(VaultBlindBucket, bucket_id)
    if not blind_bucket:
        # Erster blinder Sync. Erlaubt ist er nur dort, wo es nichts zu erben gibt.
        # Die Meldung ist bewusst dieselbe wie beim falschen Token: ein eigener
        # Fehlertext waere ein Orakel dafuer, welche Buckets belegt sind.
        if _bucket_hat_eintraege(db, bucket_id) or _bucket_besitzer(db, bucket_id) is not None:
            raise VaultBucketUnauthorized("Ungültiges Authentifizierungs-Token für diesen Tresor-Bucket.")

        blind_bucket = VaultBlindBucket(
            bucket_id=bucket_id,
            auth_verifier=computed_verifier,
            created_at=_now(),
            updated_at=_now(),
        )
        db.add(blind_bucket)

        try:
            db.commit()
        except IntegrityError:
            # Wettlauf zweier Erstregistrierungen: der andere war schneller.
            db.rollback()
            blind_bucket = db.get(VaultBlindBucket, bucket_id)
            if not blind_bucket or not secrets.compare_digest(blind_bucket.auth_verifier, computed_verifier):
                raise VaultBucketUnauthorized("Ungültiges Authentifizierungs-Token für diesen Tresor-Bucket.")
    else:
        # Bestehender blinder Bucket -> auth_verifier prüfen (Timing-sicher)
        if not secrets.compare_digest(blind_bucket.auth_verifier, computed_verifier):
            raise VaultBucketUnauthorized("Ungültiges Authentifizierungs-Token für diesen Tresor-Bucket.")

    _pruefe_format(db, bucket_id, request.client_format, request.min_client_format)

    # 2. Monotone Mutation & Revisions-Zuweisung (SEC-03)
    konflikte = _wende_mutationen_an(db, bucket_id, request.mutations)
    antwort = _lies_bucket(db, bucket_id, request.since_revision)
    antwort.conflicts = konflikte
    return antwort


def pruefe_blind_bucket(db: Session, bucket_id: str, auth_token: str) -> None:
    """Bestaetigt einen blinden Besitznachweis, ohne irgendetwas anzulegen.

    Das neue Geraet eines blinden Tresors hat keinen Canary und vom Server keine
    `bucket_id` — die Kontokopplung ist bei blinden Tresoren leer. Ohne diese
    Probe blieb dem Entsperren nur „falsches Master-Passwort", auch beim
    richtigen. `/blind-sync` taugt dafuer nicht: ein falsches Passwort ergibt
    einen jungfraeulichen Bucket, und den wuerde es anlegen.

    Ein Bucket ohne Eintrag zaehlt nicht: jeder echte Tresor traegt mindestens
    seinen Canary. Leere blinde Buckets sind Geister aus der Zeit vor dem
    11.09.2026, als ein falsches Passwort einen leeren Tresor oeffnete und
    registrierte. Sie zu bestaetigen hiesse, eine alte Tippvariante als das
    richtige Passwort auszugeben — und den Besitzer in einen leeren Tresor zu
    schicken, waehrend seine Eintraege nebenan liegen.

    Unbekannter Bucket, leerer Bucket und falscher Nachweis antworten gleich.
    """
    bucket_id = bucket_id.strip().lower()
    blind_bucket = db.get(VaultBlindBucket, bucket_id)
    verifier = hashlib.sha256(auth_token.lower().encode("utf-8")).hexdigest()
    gespeichert = blind_bucket.auth_verifier if blind_bucket is not None else "0" * 64
    passt = secrets.compare_digest(gespeichert, verifier) and blind_bucket is not None
    if not passt or not _bucket_hat_eintraege(db, bucket_id):
        raise VaultBucketUnauthorized("Ungültiges Authentifizierungs-Token für diesen Tresor-Bucket.")


def register_blind_bucket(db: Session, konto: TresorKonto, bucket_id: str, auth_token: str) -> None:
    """Hinterlegt den blinden Besitznachweis fuer den **eigenen** Bucket.

    Der authentifizierte Weg vom Cookie-Pfad auf den blinden Pfad. Er ersetzt die
    alte „sanfte Migration", die dasselbe unauthentifiziert tat und damit jeden
    bestehenden Tresor zur Uebernahme freigab.

    Drei Regeln:
    - Nur der eingetragene Besitzer darf registrieren; ein fremder Bucket ist 403.
    - Ein jungfraeulicher, herrenloser Bucket darf dabei zugleich beansprucht werden
      (der Normalfall bei der Ersteinrichtung auf einem zweiten Geraet).
    - Ein bereits hinterlegter Verifier wird **nie** ueberschrieben. Sonst waere ein
      uebernommenes Panel-Konto ein Generalschluessel fuer den Tresor — und genau
      davor soll der blinde Pfad schuetzen.
    """
    bucket_id = bucket_id.strip().lower()
    setting = einstellung(db, konto)
    besitzer = _bucket_besitzer(db, bucket_id)
    if besitzer is not None and besitzer != konto.index:
        raise VaultBucketAccessDenied("Zugriff auf fremden Tresor-Bucket verweigert.")

    vorhanden = db.get(VaultBlindBucket, bucket_id)
    if vorhanden is not None:
        if secrets.compare_digest(
            vorhanden.auth_verifier, hashlib.sha256(auth_token.lower().encode("utf-8")).hexdigest()
        ):
            return  # Idempotent: derselbe Nachweis, nichts zu tun.
        raise VaultBucketAlreadyBound("Für diesen Tresor-Bucket ist bereits ein Besitznachweis hinterlegt.")

    if besitzer is None:
        if _bucket_hat_eintraege(db, bucket_id):
            raise VaultBucketAccessDenied("Zugriff auf fremden Tresor-Bucket verweigert.")
        if setting and setting.bucket_id and setting.bucket_id != bucket_id:
            raise VaultBucketAccessDenied("Nicht autorisierter Tresor-Bucket für dieses Benutzerkonto.")
        if setting:
            setting.bucket_id = bucket_id
            setting.updated_at = _now()
        else:
            db.add(
                VaultUserSetting(
                    konto_index=konto.index,
                    bucket_id=bucket_id,
                    created_at=_now(),
                    updated_at=_now(),
                )
            )

    db.add(
        VaultBlindBucket(
            bucket_id=bucket_id,
            auth_verifier=hashlib.sha256(auth_token.lower().encode("utf-8")).hexdigest(),
            created_at=_now(),
            updated_at=_now(),
        )
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise VaultBucketAlreadyBound("Für diesen Tresor-Bucket ist bereits ein Besitznachweis hinterlegt.")


def get_vault_salt(db: Session, konto: TresorKonto) -> VaultSaltResponse:
    """Liest den hinterlegten KDF-Salt und Bucket-Status des Benutzers."""
    setting = einstellung(db, konto)
    db.commit()
    if not setting:
        return VaultSaltResponse(kdf_salt=None, bucket_id=None, has_vault=False)
    return VaultSaltResponse(
        kdf_salt=setting.kdf_salt,
        bucket_id=setting.bucket_id,
        has_vault=bool(setting.bucket_id or setting.kdf_salt),
    )


def set_vault_salt(
    db: Session,
    konto: TresorKonto,
    kdf_salt: str,
    bucket_id: str,
    auth_token: str | None = None,
) -> VaultSaltResponse:
    """Hinterlegt den initialen KDF-Salt und Bucket-ID für Multi-Device Synchronisation.

    Ein Bucket ohne Kontobesitzer, der schon blind registriert ist oder schon
    Eintraege traegt, geht nur an den, der den blinden Besitznachweis
    mitbringt. Bis 26.09.2026 genuegte die Kennung: wer sie kannte, wurde hier
    Besitzer und las danach ueber `/sync` jeden Eintrag. Solche Buckets
    entstehen, wenn der erste Abgleich ohne `/salt` lief oder das Konto des
    Besitzers geloescht wurde.
    """
    clean_bucket = bucket_id.strip().lower()
    clean_salt = kdf_salt.strip()

    # Prüfe ob Bucket bereits fremd vergeben ist
    setting = einstellung(db, konto)
    besitzer = _bucket_besitzer(db, clean_bucket)
    if besitzer is not None and besitzer != konto.index:
        raise VaultBucketAccessDenied("Der angegebene Tresor-Bucket ist bereits vergeben.")

    eigener = setting is not None and setting.bucket_id == clean_bucket
    if not eigener:
        blind = db.get(VaultBlindBucket, clean_bucket)
        belegt = blind is not None or _bucket_hat_eintraege(db, clean_bucket)
        nachweis = bool(auth_token) and blind is not None and secrets.compare_digest(
            blind.auth_verifier,
            hashlib.sha256(auth_token.lower().encode("utf-8")).hexdigest(),
        )
        if belegt and not nachweis:
            # Derselbe Wortlaut wie oben: die Antwort verraet nicht, ob es den
            # Bucket gibt.
            raise VaultBucketAccessDenied("Der angegebene Tresor-Bucket ist bereits vergeben.")
    if not setting:
        setting = VaultUserSetting(
            konto_index=konto.index,
            bucket_id=clean_bucket,
            kdf_salt=clean_salt,
            created_at=_now(),
            updated_at=_now(),
        )
        db.add(setting)
    else:
        if setting.bucket_id and setting.bucket_id != clean_bucket:
            raise VaultBucketAccessDenied("Der Tresor-Bucket kann nicht nachträglich geändert werden.")
        setting.bucket_id = clean_bucket
        setting.kdf_salt = clean_salt
        setting.updated_at = _now()

    db.commit()
    return VaultSaltResponse(
        kdf_salt=setting.kdf_salt,
        bucket_id=setting.bucket_id,
        has_vault=True,
    )


def tresor_zuruecksetzen(db: Session, konto: TresorKonto) -> None:
    """Loest den Tresor vom Konto: Salz, Kontokopplung und Hinweis. Committet nicht.

    Der Ausweg bei vergessenem Master-Passwort. Danach meldet `/salt` „kein
    Tresor", und die App bietet das Einrichten an. Bis 09/2026 ging das nur
    ueber die Datenbank.

    Die Ciphertexte bleiben liegen. Ein blinder Bucket gehoert keinem Konto,
    der Server kann ihn also nicht sicher zuordnen; und ein gekoppelter Bucket
    lebt womoeglich noch auf einem anderen Geraet, das ihn weiter abgleicht.
    Lesen kann sie ohne das alte Passwort niemand.

    Die Dateien der Tresor-Cloud (`vault_blobs`) werden dagegen entfernt
    (beim naechsten Aufraeumen, stuendlich). Ohne das alte Master-Passwort
    liesse sich keine davon je wieder loeschen: der Loeschnachweis haengt am
    Schluessel der Datei. Eine Loeschhaltung gibt es hier nicht mehr, seit die
    Blobs am Bucket haengen (01.10.2026): sie zaehlten gegen keinen Speicher,
    und Hochladen und Zuruecksetzen fuellte die Platte. Ein anderes Geraet mit
    dem alten Tresor verliert sie damit auch; der Dialog nennt vorher Anzahl
    und Groesse.
    """
    setting = einstellung(db, konto)
    hinweis = _eigene_zeile(db, VaultHint, konto)
    if setting is not None:
        if setting.bucket_id:
            vault_blob_service.alle_zur_loeschung(db, setting.bucket_id)
        db.delete(setting)
    if hinweis is not None:
        db.delete(hinweis)
    db.flush()


def konto_entfernen(db: Session, konto: TresorKonto) -> None:
    """Raeumt beim Loeschen eines Kontos alles ab, was im Tresor an ihm haengt. Committet nicht.

    Bis 01.10.2026 tat das die Kaskade auf ``users.id``. Die Tresortabellen
    tragen seither keinen Fremdschluessel aufs Konto mehr, also geschieht es
    hier. Anders als beim Zuruecksetzen gehen auch Eintraege, Besitznachweis
    und Formatsperre des Buckets mit: wer sein Konto loescht, will seine Daten
    weg haben.
    """
    setting = einstellung(db, konto)
    bucket = setting.bucket_id if setting is not None else None
    tresor_zuruecksetzen(db, konto)
    if bucket:
        db.query(VaultEntry).filter(VaultEntry.bucket_id == bucket).delete(synchronize_session=False)
        db.query(VaultBlindBucket).filter(VaultBlindBucket.bucket_id == bucket).delete(synchronize_session=False)
        db.query(VaultBucketFormat).filter(VaultBucketFormat.bucket_id == bucket).delete(synchronize_session=False)


HINT_RATE_LIMIT_SECONDS = 600  # 10 Minuten Cooldown


def set_vault_hint(db: Session, konto: TresorKonto, hint_text: str) -> None:
    """Hinterlegt oder aktualisiert den Passwort-Hinweis (verschlüsselt at rest mit Server-Key und AAD)."""
    encrypted_hint = AuthService.encrypt_secret(
        hint_text.strip(), aad=f"msm:vault:hint:{konto.user_id}"
    )
    hint_obj = _eigene_zeile(db, VaultHint, konto)
    if not hint_obj:
        hint_obj = VaultHint(konto_index=konto.index, hint=encrypted_hint)
        db.add(hint_obj)
    else:
        hint_obj.hint = encrypted_hint
        hint_obj.updated_at = _now()
    db.commit()


def _to_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def get_vault_hint_status(db: Session, konto: TresorKonto) -> VaultHintStatusResponse:
    """Prüft, ob ein Hinweis hinterlegt ist und ob die 10-Minuten-Sperrfrist aktiv ist."""
    hint_obj = _eigene_zeile(db, VaultHint, konto)
    db.commit()
    if not hint_obj or not hint_obj.hint:
        return VaultHintStatusResponse(
            has_hint=False,
            last_requested_at=None,
            can_request=False,
            cooldown_seconds_remaining=0,
        )

    cooldown = 0
    can_request = True
    last_req = _to_utc(hint_obj.last_requested_at)
    if last_req:
        diff = (_now() - last_req).total_seconds()
        if diff < HINT_RATE_LIMIT_SECONDS:
            can_request = False
            cooldown = int(HINT_RATE_LIMIT_SECONDS - diff)

    return VaultHintStatusResponse(
        has_hint=True,
        last_requested_at=hint_obj.last_requested_at,
        can_request=can_request,
        cooldown_seconds_remaining=cooldown,
    )


async def request_vault_hint_email(db: Session, user: User, konto: TresorKonto) -> tuple[bool, str]:
    """Sendet den hinterlegten Hinweis an die registrierte E-Mail-Adresse des Benutzers.
    
    Verbindliche Invariante: Nur 1 Anfrage alle 10 Minuten erlaubt.
    """
    hint_obj = _eigene_zeile(db, VaultHint, konto)
    db.commit()
    if not hint_obj or not hint_obj.hint:
        return False, "Für dein Konto ist kein Passwort-Hinweis hinterlegt."

    now = _now()

    # Die Sperrfrist wird **vor** dem Versand gesetzt, und zwar als bedingtes
    # UPDATE: nur wer die Zeile tatsaechlich aendert, darf senden.
    #
    # Vorher lag zwischen Pruefung und Fortschreibung der komplette
    # SMTP-Dialog — hunderte Millisekunden, in denen jeder weitere Request
    # dieselbe alte `last_requested_at` las und ebenfalls durchging. Ein Dutzend
    # paralleler Aufrufe ergab ein Dutzend E-Mails: ein Mailbombing-Werkzeug im
    # eigenen Panel, das nebenbei die Zustellbarkeit der Absenderdomain
    # verbrennt. Die 10-Minuten-Zusage aus den Patchnotes war damit reine Zierde.
    grenze = now - timedelta(seconds=HINT_RATE_LIMIT_SECONDS)
    getroffen = (
        db.query(VaultHint)
        .filter(
            VaultHint.id == hint_obj.id,
            (VaultHint.last_requested_at.is_(None)) | (VaultHint.last_requested_at <= grenze),
        )
        .update({"last_requested_at": now}, synchronize_session=False)
    )
    db.commit()

    if not getroffen:
        db.refresh(hint_obj)
        last_req = _to_utc(hint_obj.last_requested_at)
        diff = (now - last_req).total_seconds() if last_req else 0
        wait_minutes = max(1, int((HINT_RATE_LIMIT_SECONDS - diff + 59) // 60))
        return (
            False,
            f"Der Hinweis kann nur alle 10 Minuten angefordert werden. Bitte warte noch {wait_minutes} Minute(n).",
        )

    # Entschlüsseln mit AAD. Scheitert das, wird **nicht** der Rohwert verschickt:
    # der waere der Ciphertext aus der Datenbank, und den per E-Mail aus dem
    # Vertrauensbereich zu tragen ist schlimmer als gar keine Antwort.
    try:
        raw_hint = AuthService.decrypt_secret(
            hint_obj.hint, aad=f"msm:vault:hint:{user.id}"
        )
    except Exception:
        return False, "Der hinterlegte Hinweis konnte nicht gelesen werden. Bitte hinterlege ihn erneut."

    from services.email_service import EmailService

    subject = "Tresor: Dein Passwort-Hinweis"
    body = f"""Hallo {user.username},

du hast den Passwort-Hinweis für deinen Tresor im Maunting Service Manager angefordert.

Dein hinterlegter Hinweis lautet:
{raw_hint}

Falls du diese Anforderung nicht ausgelöst hast, überprüfe bitte die Sicherheit deines Kontos.

Maunting Service Manager
"""
    # `raw_hint` ist Freitext des Benutzers und landet in einem HTML-Dokument:
    # ohne Maskierung traegt jedes `<a href=…>` darin ungeprueft in eine Mail,
    # die aus der eigenen, per SPF/DKIM beglaubigten Domain kommt.
    html_content = EmailService._notification_email_html(
        user.username,
        "Passwort-Hinweis",
        "Hier ist deine persönliche Gedankenstütze für das Master-Passwort deines Tresors:",
        f"<strong>{html.escape(raw_hint)}</strong>",
        kategorie="Tresor",
    )

    success = await EmailService.send_email(user.email, subject, body, html_content)
    if not success:
        # Fehlschlag gibt die Frist wieder frei — sonst kostet ein kaputter
        # SMTP-Server den Benutzer zehn Minuten.
        hint_obj.last_requested_at = None
        db.commit()
        return False, "E-Mail konnte nicht versendet werden. Bitte prüfe die E-Mail-Konfiguration."

    return True, "Dein Passwort-Hinweis wurde erfolgreich an deine E-Mail-Adresse gesendet."
