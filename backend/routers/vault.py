"""REST-Router für den blinden, verschlüsselten Zero-Knowledge-Tresor."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from database import get_db
from dependencies import get_current_user, session_familie, verify_csrf
from middleware.rate_limit import auth_rate_limit, limiter
from models.user import User
from schemas.vault import (
    VaultBlobAnlegen,
    VaultBlobLoeschen,
    VaultBlobsKlein,
    VaultBlobStatus,
    VaultEingangAnlegen,
    VaultEingangDatensatz,
    VaultEingangListe,
    VaultSicherungszugangAnlegen,
    VaultSpeicher,
    VaultBlindCheckRequest,
    VaultBlindRegisterRequest,
    VaultBlindSyncRequest,
    VaultHintSetRequest,
    VaultHintStatusResponse,
    VaultResetRequest,
    VaultSaltResponse,
    VaultSaltSetRequest,
    VaultSyncRequest,
    VaultSyncResponse,
)
from services import audit_service, passkey_service, vault_blob_service, vault_service, vault_sicherung_service
from services.dis_client import DisSidecarError
from services.auth_service import AuthService
from services.panel_settings_service import PanelSettingsService

logger = logging.getLogger(__name__)


def _check_vault_enabled() -> None:
    """Laeuft vor allen anderen Abhaengigkeiten jeder Tresor-Route (Router-Abhaengigkeit).

    Bis 02.10.2026 stand der Aufruf 19-mal von Hand im Rumpf, also nach Sitzung,
    Sidecar und Datenbank: ohne Sidecar hiess ein abgeschalteter Tresor 503.
    """
    if PanelSettingsService.get("vault_enabled", "true") == "false":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Der Tresor ist in den Panel-Einstellungen deaktiviert.",
        )


router = APIRouter(prefix="/api/vault", tags=["vault"], dependencies=[Depends(_check_vault_enabled)])


def _tresor_konto(current_user: User = Depends(get_current_user)) -> vault_service.TresorKonto:
    """Das angemeldete Konto, wie die Tresortabellen es kennen (HMAC aus dem Sidecar).

    Ohne Sidecar gibt es keinen Index und damit keinen Tresor: 503 statt 500.
    """
    return vault_service.tresor_konto_oder_503(current_user.id)


BUCKET_KOPF = "X-MSM-Vault-Bucket"


def _bucket_pruefen(request: Request, db: Session, konto: vault_service.TresorKonto, pflicht: bool) -> str:
    """Der Bucket des Kontos, und nur, wenn er der ist, den der Client offen hat.

    Der Client nennt seinen Bucket in ``X-MSM-Vault-Bucket``. Ist der beerdigt
    oder nicht (mehr) der des Kontos, ist es 410 ``VAULT_ZURUECKGESETZT``. Bis
    02.10.2026 nahmen die Routen den Bucket des Kontos: nach einem
    Zuruecksetzen auf einem Geraet lud ein anderes mit dem alten Tresor in den
    neuen hoch, und die Dateien ohne Eintrag belegten dessen Speicher.

    Fehlt dem Konto der Bucket (die Meldung des Salzes ist beim Einrichten
    gescheitert), meldet der Client ihn auf ``VAULT_BUCKET_UNBEKANNT`` neu und
    versucht es noch einmal.
    """
    genannt = (request.headers.get(BUCKET_KOPF) or "").strip().lower()
    if not genannt and pflicht:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Der Tresor-Bucket fehlt.")
    if genannt and vault_blob_service.ist_beerdigt(db, genannt):
        raise _mit_code(status.HTTP_410_GONE, "VAULT_ZURUECKGESETZT")
    try:
        bucket = vault_service.eigener_bucket(db, konto)
    except vault_service.VaultOhneBucket as exc:
        raise _mit_code(status.HTTP_409_CONFLICT, "VAULT_BUCKET_UNBEKANNT") from exc
    db.commit()
    if genannt and bucket != genannt:
        raise _mit_code(status.HTTP_410_GONE, "VAULT_ZURUECKGESETZT")
    return bucket


def _eigener_bucket(
    request: Request,
    db: Session = Depends(get_db),
    konto: vault_service.TresorKonto = Depends(_tresor_konto),
) -> str:
    """Fuer lesende Datei-Routen: der Kopf ist freiwillig.

    Wer einen fremden Bucket liest, findet die Kennungen seines alten Tresors
    dort nicht (404) und kennt die des neuen nicht. Lesende Wege wissen im
    Client nicht immer, welcher Bucket offen ist.
    """
    return _bucket_pruefen(request, db, konto, pflicht=False)


def _genannter_bucket(
    request: Request,
    db: Session = Depends(get_db),
    konto: vault_service.TresorKonto = Depends(_tresor_konto),
) -> str:
    """Fuer schreibende Datei-Routen: ohne genannten Bucket kein Schreiben."""
    return _bucket_pruefen(request, db, konto, pflicht=True)


def _mit_code(status_code: int, code: str) -> HTTPException:
    """Fehler mit Code; die App uebersetzt ``errors.<code>``."""
    return HTTPException(status_code=status_code, detail={"code": code, "message": f"errors.{code.lower()}"})


@router.post("/blind-sync", response_model=VaultSyncResponse)
@limiter.limit("60/minute")
def sync_vault_blind(
    payload: VaultBlindSyncRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> VaultSyncResponse:
    """Synchronisiert verschlüsselte Tresor-Einträge ohne Session-Cookies oder User-Metadaten.

    CRITICAL PRIVACY & SECURITY INVARIANTS:
    - Keine User-Cookies, keine Authorization-Header, keine CSRF-Tokens erforderlich (credentials: 'omit').
    - Authentifizierung erfolgt ausschließlich über den blinden Besitznachweis (auth_token).
    - Rate-limitiert gegen Brute-Force.
    """
    try:
        return vault_service.sync_vault_blind(db, payload)
    except vault_service.VaultZurueckgesetzt as exc:
        raise _mit_code(status.HTTP_410_GONE, "VAULT_ZURUECKGESETZT") from exc
    except vault_service.VaultClientZuAlt as exc:
        raise HTTPException(status_code=status.HTTP_426_UPGRADE_REQUIRED, detail=str(exc)) from exc
    except vault_service.VaultBucketUnauthorized as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        logger.error("Fehler bei der blinden Tresor-Synchronisation: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Interner Fehler bei der blinden Tresor-Synchronisation.",
        ) from exc


@router.post("/blind-check")
@limiter.limit("20/minute")
def check_vault_blind(
    payload: VaultBlindCheckRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    """Bestätigt das Master-Passwort eines blinden Tresors auf einem neuen Gerät.

    Wie `/blind-sync` ohne Cookies und ohne Konto, aber ohne Nebenwirkung: ein
    falsches Passwort legt hier keinen leeren Bucket an.
    """
    try:
        vault_service.pruefe_blind_bucket(db, payload.bucket_id, payload.auth_token)
    except vault_service.VaultBucketUnauthorized as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return {"status": "ok"}


@router.post("/blind-register")
def register_blind_vault_bucket(
    payload: VaultBlindRegisterRequest,
    request: Request,
    db: Session = Depends(get_db),
    konto: vault_service.TresorKonto = Depends(_tresor_konto),
    __=Depends(verify_csrf),
) -> dict[str, str]:
    """Hinterlegt den blinden Besitznachweis für den eigenen Tresor-Bucket.

    Der authentifizierte Übergang vom Cookie-Pfad auf den blinden Pfad. Er ersetzt
    die frühere unauthentifizierte „sanfte Migration" in `/blind-sync`, über die
    sich jeder bestehende Tresor übernehmen ließ.
    """
    try:
        vault_service.register_blind_bucket(db, konto, payload.bucket_id, payload.auth_token)
    except vault_service.VaultZurueckgesetzt as exc:
        raise _mit_code(status.HTTP_410_GONE, "VAULT_ZURUECKGESETZT") from exc
    except vault_service.VaultBucketAccessDenied as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except vault_service.VaultBucketAlreadyBound as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {"status": "ok", "message": "Blinder Besitznachweis hinterlegt."}


@router.post("/sync", response_model=VaultSyncResponse)
def sync_vault_entries(
    payload: VaultSyncRequest,
    db: Session = Depends(get_db),
    konto: vault_service.TresorKonto = Depends(_tresor_konto),
    __=Depends(verify_csrf),
) -> VaultSyncResponse:
    """Synchronisiert verschlüsselte Tresor-Einträge mit dem Server.

    CRITICAL SECURITY INVARIANTS:
    - Der Server verarbeitet ausschließlich Ciphertext (`sv-vault-v1:`).
    - Es werden keine Klardaten, URLs, Passwörter oder Tags übertragen.
    - Die `bucket_id` ist an das autorisierte Benutzerkonto gebunden (SEC-02).
    - Double-Submit CSRF-Schutz via `verify_csrf` (SEC-09).
    """
    try:
        return vault_service.sync_vault(db, konto, payload)
    except vault_service.VaultZurueckgesetzt as exc:
        raise _mit_code(status.HTTP_410_GONE, "VAULT_ZURUECKGESETZT") from exc
    except vault_service.VaultClientZuAlt as exc:
        raise HTTPException(status_code=status.HTTP_426_UPGRADE_REQUIRED, detail=str(exc)) from exc
    except vault_service.VaultBucketAccessDenied as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        logger.error("Fehler bei der Tresor-Synchronisation: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Interner Fehler bei der Tresor-Synchronisation.",
        ) from exc


@router.get("/salt", response_model=VaultSaltResponse)
def get_vault_salt(
    db: Session = Depends(get_db),
    konto: vault_service.TresorKonto = Depends(_tresor_konto),
) -> VaultSaltResponse:
    """Ruft den am Benutzerkonto hinterlegten KDF-Salt für Multi-Device-Sync ab (SEC-04)."""
    return vault_service.get_vault_salt(db, konto)


@router.post("/salt", response_model=VaultSaltResponse)
def set_vault_salt(
    payload: VaultSaltSetRequest,
    db: Session = Depends(get_db),
    konto: vault_service.TresorKonto = Depends(_tresor_konto),
    __=Depends(verify_csrf),
) -> VaultSaltResponse:
    """Hinterlegt den KDF-Salt des Benutzers beim initialen Setup (SEC-04, SEC-09).

    Ein gesetztes Salz bleibt; ein anderes ist 409, ein neues gibt es nur ueber `/reset`.
    """
    try:
        return vault_service.set_vault_salt(
            db,
            konto,
            payload.kdf_salt,
            payload.bucket_id,
            payload.auth_token,
        )
    except vault_service.VaultZurueckgesetzt as exc:
        raise _mit_code(status.HTTP_410_GONE, "VAULT_ZURUECKGESETZT") from exc
    except vault_service.VaultSalzGesetzt as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except vault_service.VaultBucketAccessDenied as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.post("/reset", dependencies=[Depends(auth_rate_limit)])
def reset_vault(
    payload: VaultResetRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    konto: vault_service.TresorKonto = Depends(_tresor_konto),
    __=Depends(verify_csrf),
) -> dict[str, str]:
    """Setzt den Tresor des Kontos zurueck, wenn das Master-Passwort vergessen ist.

    Dieselben Huerden wie beim Loeschen des Kontos, jede fuer sich:
    - Konto mit Passwort: das Passwort, auch wenn 2FA aktiv ist.
    - Aktive 2FA: ein eingerichteter Faktor, gleich welcher.
    - Immer: das Wort „delete".
    Ein reines Social-Konto ohne 2FA kommt mit dem Wort allein durch
    (Entscheidung des Betreibers vom 30.09.2026) — mehr hat es nicht, und
    beim Loeschen des Kontos gilt dieselbe Regel.
    `auth_rate_limit`, weil hier ein Passwort geprueft wird.
    """
    if (payload.confirmation or "").strip().lower() != "delete":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Bestätigung delete erforderlich")
    if current_user.has_password and (
        not payload.password or not AuthService.verify_password(payload.password, current_user.password_hash)
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Bitte dein Passwort bestätigen.")
    if current_user.two_factor_enabled and not passkey_service.zweiter_faktor_bestaetigt(
        db, current_user,
        otp_code=payload.otp_code,
        passkey=payload.passkey.model_dump() if payload.passkey else None,
        zweck="vault_reset",
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=passkey_service.nachweis_hinweis(current_user))

    vault_service.tresor_zuruecksetzen(db, konto)
    audit_service.record_privileged_action(
        db,
        user_id=current_user.id,
        action="vault.reset",
        target_type="user",
        target_id=current_user.id,
        commit=True,
    )
    return {"status": "ok"}


@router.post("/hint")
def save_vault_hint(
    payload: VaultHintSetRequest,
    db: Session = Depends(get_db),
    konto: vault_service.TresorKonto = Depends(_tresor_konto),
    __=Depends(verify_csrf),
) -> dict[str, str]:
    """Hinterlegt einen Passwort-Hinweis für den Tresor."""
    vault_service.set_vault_hint(db, konto, payload.hint)
    return {"status": "ok", "message": "Passwort-Hinweis erfolgreich hinterlegt."}


@router.get("/hint-status", response_model=VaultHintStatusResponse)
def get_vault_hint_status(
    db: Session = Depends(get_db),
    konto: vault_service.TresorKonto = Depends(_tresor_konto),
) -> VaultHintStatusResponse:
    """Gibt den Status des Passwort-Hinweises und Cooldowns zurück."""
    return vault_service.get_vault_hint_status(db, konto)


@router.post("/request-hint")
@limiter.limit("5/minute")
async def send_vault_hint(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    konto: vault_service.TresorKonto = Depends(_tresor_konto),
    __=Depends(verify_csrf),
) -> dict[str, str]:
    """Sendet den hinterlegten Passwort-Hinweis an die E-Mail des Benutzers (max. 1x alle 10 Minuten).

    Zwei Bremsen, weil eine zu wenig ist: der Zähler hier begrenzt den Andrang
    pro Herkunft, die Sperrfrist im Service den pro Konto. Ohne die erste kosten
    schon die abgewiesenen Anfragen jeweils einen Datenbank-Roundtrip.

    Async wegen des Versands; alles Synchrone laeuft im Threadpool (AGENTS 47).
    """
    success, msg = await vault_service.request_vault_hint_email(db, current_user, konto)
    if not success:
        # Falls Cooldown aktiv ist: 429 Too Many Requests
        if "10 Minuten" in msg:
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=msg)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg)
    return {"status": "ok", "message": msg}


# ─── Tresor-Cloud: verschluesselte Dateien ───────────────────────────────────
#
# Alle Wege sind angemeldet und gelten nur fuer die Blobs im Bucket des
# eigenen Kontos, den der Client auch selbst nennen muss (`_eigener_bucket`).
# Ein fremder Blob ist wie ein fehlender (404). Die Grenzen sind grosszuegig und
# gehoeren nur diesen Routen: ein Upload von tausend Fotos sind dreitausend
# Blobs, und nichts davon darf die Anmeldegrenze aufbrauchen.

# So viele Chunks nimmt ein Prozess zugleich entgegen. Jeder liegt bis zum
# Schreiben im Speicher (gut 4 MiB); ohne Grenze hielten tausend gleichzeitige
# Uploads Gigabytes. Darueber 503 mit Retry-After, der Client versucht es beim
# naechsten Anstoss wieder.
CHUNKS_ZUGLEICH = 16
_chunks_laufend = 0


def _blob_fehler(exc: vault_blob_service.BlobFehler) -> HTTPException:
    if exc.code:
        return _mit_code(exc.status_code, exc.code)
    return HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get("/speicher", response_model=VaultSpeicher)
@limiter.limit("120/minute")
def tresor_speicher(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    konto: vault_service.TresorKonto = Depends(_tresor_konto),
) -> VaultSpeicher:
    einstellung = vault_service.einstellung(db, konto)
    bucket = einstellung.bucket_id if einstellung is not None else None
    db.commit()
    return VaultSpeicher(**vault_blob_service.speicher(db, current_user, bucket))


@router.post("/blobs", status_code=status.HTTP_201_CREATED)
@limiter.limit("1200/minute")
def blob_anlegen(
    payload: VaultBlobAnlegen,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    bucket: str = Depends(_genannter_bucket),
    __=Depends(verify_csrf),
) -> dict[str, str]:
    try:
        vault_blob_service.anlegen(
            db, current_user, bucket, payload.id, payload.chunk_count, payload.bytes_total, payload.delete_verifier
        )
    except vault_blob_service.BlobFehler as exc:
        raise _blob_fehler(exc) from exc
    return {"id": payload.id}


@router.put("/blobs/{blob_id}/chunks/{index}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("1200/minute")
async def blob_chunk_hochladen(
    blob_id: str,
    index: int,
    request: Request,
    db: Session = Depends(get_db),
    bucket: str = Depends(_genannter_bucket),
    __=Depends(verify_csrf),
) -> Response:
    return await _chunk_annehmen(request, db, bucket, blob_id, index)


async def _chunk_annehmen(request: Request, db: Session, bucket: str, blob_id: str, index: int) -> Response:
    """Nimmt einen Chunk als rohe Bytes an. Laenge exakt wie angemeldet, sonst 413/422.

    Datenbank und Platte laufen im Threadpool. Die Sitzung ist frei, bevor der
    Body gelesen wird; danach prueft ``chunk_ablegen`` den Blob noch einmal.
    """
    global _chunks_laufend
    try:
        erwartet = await run_in_threadpool(vault_blob_service.laenge_fuer_upload, db, bucket, blob_id, index)
    except vault_blob_service.BlobFehler as exc:
        raise _blob_fehler(exc) from exc
    # Der Body kann langsam kommen. Solange er laeuft, haelt die Route keine
    # Verbindung aus dem Pool, sonst legen ein paar langsame Uploads das Panel lahm.
    await run_in_threadpool(db.rollback)

    # Pruefen und Hochzaehlen ohne await dazwischen: in der einen
    # Ereignisschleife kommt niemand dazwischen.
    if _chunks_laufend >= CHUNKS_ZUGLEICH:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Der Tresor nimmt gerade zu viele Dateien zugleich an.",
            headers={"Retry-After": "5"},
        )
    _chunks_laufend += 1
    try:
        daten = bytearray()
        async for teil in request.stream():
            daten += teil
            if len(daten) > erwartet:
                raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Chunk zu gross.")
        if len(daten) != erwartet:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Chunk hat die falsche Laenge.")

        try:
            await run_in_threadpool(vault_blob_service.chunk_ablegen, db, bucket, blob_id, index, daten)
        except vault_blob_service.BlobFehler as exc:
            raise _blob_fehler(exc) from exc
    finally:
        _chunks_laufend -= 1
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/blobs/{blob_id}/status", response_model=VaultBlobStatus)
@limiter.limit("1200/minute")
def blob_status(
    blob_id: str,
    request: Request,
    db: Session = Depends(get_db),
    bucket: str = Depends(_eigener_bucket),
) -> VaultBlobStatus:
    try:
        blob = vault_blob_service.eigener_blob(db, bucket, blob_id)
    except vault_blob_service.BlobFehler as exc:
        raise _blob_fehler(exc) from exc
    return VaultBlobStatus(
        state=blob.state,
        chunk_count=blob.chunk_count,
        vorhanden=vault_blob_service.vorhandene_chunks(blob),
    )


@router.post("/blobs/{blob_id}/fertig")
@limiter.limit("1200/minute")
def blob_fertig(
    blob_id: str,
    request: Request,
    db: Session = Depends(get_db),
    bucket: str = Depends(_genannter_bucket),
    __=Depends(verify_csrf),
) -> dict[str, str]:
    try:
        vault_blob_service.fertigstellen(db, bucket, blob_id)
    except vault_blob_service.BlobFehler as exc:
        raise _blob_fehler(exc) from exc
    return {"state": "fertig"}


@router.get("/blobs/{blob_id}/chunks/{index}")
@limiter.limit("1200/minute")
def blob_chunk_lesen(
    blob_id: str,
    index: int,
    request: Request,
    db: Session = Depends(get_db),
    bucket: str = Depends(_eigener_bucket),
) -> FileResponse:
    try:
        pfad = vault_blob_service.chunk_pfad(db, bucket, blob_id, index)
    except vault_blob_service.BlobFehler as exc:
        raise _blob_fehler(exc) from exc
    return FileResponse(pfad, media_type="application/octet-stream")


@router.post("/blobs/klein")
@limiter.limit("300/minute")
def blobs_klein(
    payload: VaultBlobsKlein,
    request: Request,
    db: Session = Depends(get_db),
    bucket: str = Depends(_eigener_bucket),
    __=Depends(verify_csrf),
) -> Response:
    """Bis zu 100 Miniaturen in einer Antwort, damit die Galerie nicht tausend Anfragen stellt."""
    inhalt = vault_blob_service.kleine_lesen(db, bucket, payload.ids)
    return Response(content=inhalt, media_type="application/octet-stream")


@router.delete("/blobs/{blob_id}")
@limiter.limit("1200/minute")
def blob_loeschen(
    blob_id: str,
    payload: VaultBlobLoeschen,
    request: Request,
    db: Session = Depends(get_db),
    bucket: str = Depends(_genannter_bucket),
    __=Depends(verify_csrf),
) -> dict[str, str]:
    try:
        vault_blob_service.loeschen(db, bucket, blob_id, payload.schluessel)
    except vault_blob_service.BlobFehler as exc:
        raise _blob_fehler(exc) from exc
    return {"state": "geloescht"}


# ── Posteingang (Kamera-Sicherung bei gesperrtem Tresor) ────────────────────
# Abgelegt wird nur vom Hintergrund-Job (`/sicherung/eingang`); die App holt ab.


@router.get("/eingang", response_model=VaultEingangListe)
@limiter.limit("120/minute")
def eingang_liste(
    request: Request,
    nach: str | None = None,
    db: Session = Depends(get_db),
    bucket: str = Depends(_genannter_bucket),
) -> VaultEingangListe:
    zeilen, weiter = vault_blob_service.eingang_liste(db, bucket, nach)
    return VaultEingangListe(
        eintraege=[VaultEingangDatensatz(id=z.id, ciphertext=z.ciphertext, created_at=z.created_at) for z in zeilen],
        weiter=weiter,
    )


@router.delete("/eingang/{eingang_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("1200/minute")
def eingang_loeschen(
    eingang_id: str,
    request: Request,
    db: Session = Depends(get_db),
    bucket: str = Depends(_genannter_bucket),
    __=Depends(verify_csrf),
) -> Response:
    vault_blob_service.eingang_loeschen(db, bucket, eingang_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── Kamera-Sicherung ohne offene App (eigener Zugang, vault_sicherung_service) ──

SICHERUNG_KOPF = "X-MSM-Sicherung"


@router.post("/sicherung/zugang", status_code=status.HTTP_201_CREATED, dependencies=[Depends(auth_rate_limit)])
def sicherungszugang_anlegen(
    payload: VaultSicherungszugangAnlegen,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    konto: vault_service.TresorKonto = Depends(_tresor_konto),
    bucket: str = Depends(_genannter_bucket),
    familie: str | None = Depends(session_familie),
    __=Depends(verify_csrf),
) -> dict[str, str]:
    """Zugang fuer die Sicherung auf diesem Geraet; ersetzt den bisherigen derselben Sitzung.

    Ein dauerhafter Zugang, deshalb nur mit frischem Nachweis (AGENTS.md
    Punkt 21). Ohne Sitzungsfamilie gibt es nichts, woran er fallen koennte.
    Index und Bucket stehen vor dem Nachweis fest (Punkt 82).
    """
    if not familie:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Nur aus einer angemeldeten App.")
    try:
        fam_index = vault_sicherung_service.familie_index(familie)
    except DisSidecarError as exc:
        raise HTTPException(status_code=503, detail="Der Tresor ist gerade nicht erreichbar.") from exc
    fehlt = passkey_service.frischer_nachweis_fehlt(
        db,
        current_user,
        password=payload.password,
        otp_code=payload.otp_code,
        passkey=payload.passkey.model_dump() if payload.passkey else None,
        zweck="kamera_sicherung",
    )
    if fehlt:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=fehlt)
    token = vault_sicherung_service.anlegen(db, konto, familie, fam_index, bucket)
    db.commit()
    return {"zugang": token}


@router.delete("/sicherung/zugang", status_code=status.HTTP_204_NO_CONTENT)
def sicherungszugang_entfernen(
    db: Session = Depends(get_db),
    konto: vault_service.TresorKonto = Depends(_tresor_konto),
    familie: str | None = Depends(session_familie),
    __=Depends(verify_csrf),
) -> Response:
    if familie:
        try:
            vault_sicherung_service.entfernen(db, konto, vault_sicherung_service.familie_index(familie))
        except DisSidecarError as exc:
            raise HTTPException(status_code=503, detail="Der Tresor ist gerade nicht erreichbar.") from exc
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _sicherung(request: Request, db: Session = Depends(get_db)) -> vault_sicherung_service.Sicherung:
    """Der Worker auf dem Telefon. Kein CSRF: der Zugang steht in einem eigenen Kopf, nie in einem Cookie."""
    token = (request.headers.get(SICHERUNG_KOPF) or "").strip()
    if not token:
        raise _mit_code(status.HTTP_401_UNAUTHORIZED, "VAULT_SICHERUNG_UNGUELTIG")
    try:
        sicherung = vault_sicherung_service.pruefen(db, token)
    except vault_sicherung_service.ZugangUngueltig as exc:
        db.commit()  # ein toter Zugang fällt (``pruefen``)
        raise _mit_code(status.HTTP_401_UNAUTHORIZED, "VAULT_SICHERUNG_UNGUELTIG") from exc
    except vault_sicherung_service.ZugangZurueckgesetzt as exc:
        raise _mit_code(status.HTTP_410_GONE, "VAULT_ZURUECKGESETZT") from exc
    except DisSidecarError as exc:
        raise HTTPException(status_code=503, detail="Der Tresor ist gerade nicht erreichbar.") from exc
    db.commit()
    return sicherung


@router.post("/sicherung/blobs", status_code=status.HTTP_201_CREATED)
@limiter.limit("1200/minute")
def sicherung_blob_anlegen(
    payload: VaultBlobAnlegen,
    request: Request,
    db: Session = Depends(get_db),
    sicherung: vault_sicherung_service.Sicherung = Depends(_sicherung),
) -> dict[str, str]:
    try:
        vault_blob_service.anlegen(
            db, sicherung.user, sicherung.bucket, payload.id, payload.chunk_count, payload.bytes_total, payload.delete_verifier
        )
    except vault_blob_service.BlobFehler as exc:
        raise _blob_fehler(exc) from exc
    return {"id": payload.id}


@router.put("/sicherung/blobs/{blob_id}/chunks/{index}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("1200/minute")
async def sicherung_chunk_hochladen(
    blob_id: str,
    index: int,
    request: Request,
    db: Session = Depends(get_db),
    sicherung: vault_sicherung_service.Sicherung = Depends(_sicherung),
) -> Response:
    return await _chunk_annehmen(request, db, sicherung.bucket, blob_id, index)


@router.get("/sicherung/blobs/{blob_id}/status", response_model=VaultBlobStatus)
@limiter.limit("1200/minute")
def sicherung_blob_status(
    blob_id: str,
    request: Request,
    db: Session = Depends(get_db),
    sicherung: vault_sicherung_service.Sicherung = Depends(_sicherung),
) -> VaultBlobStatus:
    try:
        blob = vault_blob_service.eigener_blob(db, sicherung.bucket, blob_id)
    except vault_blob_service.BlobFehler as exc:
        raise _blob_fehler(exc) from exc
    return VaultBlobStatus(state=blob.state, chunk_count=blob.chunk_count, vorhanden=vault_blob_service.vorhandene_chunks(blob))


@router.post("/sicherung/blobs/{blob_id}/fertig")
@limiter.limit("1200/minute")
def sicherung_blob_fertig(
    blob_id: str,
    request: Request,
    db: Session = Depends(get_db),
    sicherung: vault_sicherung_service.Sicherung = Depends(_sicherung),
) -> dict[str, str]:
    try:
        vault_blob_service.fertigstellen(db, sicherung.bucket, blob_id)
    except vault_blob_service.BlobFehler as exc:
        raise _blob_fehler(exc) from exc
    return {"state": "fertig"}


@router.delete("/sicherung/blobs/{blob_id}")
@limiter.limit("1200/minute")
def sicherung_blob_loeschen(
    blob_id: str,
    payload: VaultBlobLoeschen,
    request: Request,
    db: Session = Depends(get_db),
    sicherung: vault_sicherung_service.Sicherung = Depends(_sicherung),
) -> dict[str, str]:
    """Nur mit dem Loeschnachweis, den allein das Geraet kennt, das den Blob angelegt hat."""
    try:
        vault_blob_service.loeschen(db, sicherung.bucket, blob_id, payload.schluessel)
    except vault_blob_service.BlobFehler as exc:
        raise _blob_fehler(exc) from exc
    return {"state": "geloescht"}


@router.post("/sicherung/eingang", status_code=status.HTTP_201_CREATED)
@limiter.limit("600/minute")
def sicherung_eingang_ablegen(
    payload: VaultEingangAnlegen,
    request: Request,
    db: Session = Depends(get_db),
    sicherung: vault_sicherung_service.Sicherung = Depends(_sicherung),
) -> dict[str, str]:
    try:
        vault_blob_service.eingang_ablegen(db, sicherung.user, sicherung.bucket, payload.id, payload.ciphertext)
    except vault_blob_service.BlobFehler as exc:
        raise _blob_fehler(exc) from exc
    return {"id": payload.id}


@router.delete("/sicherung/eingang/{eingang_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("1200/minute")
def sicherung_eingang_loeschen(
    eingang_id: str,
    request: Request,
    db: Session = Depends(get_db),
    sicherung: vault_sicherung_service.Sicherung = Depends(_sicherung),
) -> Response:
    """Fuer einen Datensatz, dessen Datei sich beim Hochladen geaendert hat. Lesen kann der Zugang nichts."""
    vault_blob_service.eingang_loeschen(db, sicherung.bucket, eingang_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
