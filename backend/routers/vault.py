"""REST-Router für den blinden, verschlüsselten Zero-Knowledge Passwort-Manager."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from database import get_db
from dependencies import get_current_user, verify_csrf
from middleware.rate_limit import auth_rate_limit, limiter
from models.user import User
from schemas.vault import (
    VaultBlobAnlegen,
    VaultBlobLoeschen,
    VaultBlobsKlein,
    VaultBlobStatus,
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
from services import audit_service, passkey_service, vault_blob_service, vault_service
from services.auth_service import AuthService
from services.panel_settings_service import PanelSettingsService

router = APIRouter(prefix="/api/vault", tags=["vault"])
logger = logging.getLogger(__name__)


def _check_vault_enabled() -> None:
    if PanelSettingsService.get("vault_enabled", "true") == "false":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Der Passwort-Manager ist in den Panel-Einstellungen deaktiviert.",
        )


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
    _check_vault_enabled()
    try:
        return vault_service.sync_vault_blind(db, payload)
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
    _check_vault_enabled()
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
    current_user: User = Depends(get_current_user),
    __=Depends(verify_csrf),
) -> dict[str, str]:
    """Hinterlegt den blinden Besitznachweis für den eigenen Tresor-Bucket.

    Der authentifizierte Übergang vom Cookie-Pfad auf den blinden Pfad. Er ersetzt
    die frühere unauthentifizierte „sanfte Migration" in `/blind-sync`, über die
    sich jeder bestehende Tresor übernehmen ließ.
    """
    _check_vault_enabled()
    try:
        vault_service.register_blind_bucket(
            db, current_user.id, payload.bucket_id, payload.auth_token
        )
    except vault_service.VaultBucketAccessDenied as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except vault_service.VaultBucketAlreadyBound as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {"status": "ok", "message": "Blinder Besitznachweis hinterlegt."}


@router.post("/sync", response_model=VaultSyncResponse)
def sync_vault_entries(
    payload: VaultSyncRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    __=Depends(verify_csrf),
) -> VaultSyncResponse:
    """Synchronisiert verschlüsselte Tresor-Einträge mit dem Server.

    CRITICAL SECURITY INVARIANTS:
    - Der Server verarbeitet ausschließlich Ciphertext (`sv-vault-v1:`).
    - Es werden keine Klardaten, URLs, Passwörter oder Tags übertragen.
    - Die `bucket_id` ist an das autorisierte Benutzerkonto gebunden (SEC-02).
    - Double-Submit CSRF-Schutz via `verify_csrf` (SEC-09).
    """
    _check_vault_enabled()
    try:
        return vault_service.sync_vault(db, current_user, payload)
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
    current_user: User = Depends(get_current_user),
) -> VaultSaltResponse:
    """Ruft den am Benutzerkonto hinterlegten KDF-Salt für Multi-Device-Sync ab (SEC-04)."""
    _check_vault_enabled()
    return vault_service.get_vault_salt(db, current_user.id)


@router.post("/salt", response_model=VaultSaltResponse)
def set_vault_salt(
    payload: VaultSaltSetRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    __=Depends(verify_csrf),
) -> VaultSaltResponse:
    """Hinterlegt den KDF-Salt des Benutzers beim initialen Setup (SEC-04, SEC-09)."""
    _check_vault_enabled()
    try:
        return vault_service.set_vault_salt(
            db,
            current_user.id,
            payload.kdf_salt,
            payload.bucket_id,
            payload.auth_token,
        )
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
    _check_vault_enabled()
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

    vault_service.tresor_zuruecksetzen(db, current_user.id)
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
    current_user: User = Depends(get_current_user),
    __=Depends(verify_csrf),
) -> dict[str, str]:
    """Hinterlegt einen Passwort-Hinweis für den Passwort-Manager."""
    _check_vault_enabled()
    vault_service.set_vault_hint(db, current_user.id, payload.hint)
    return {"status": "ok", "message": "Passwort-Hinweis erfolgreich hinterlegt."}


@router.get("/hint-status", response_model=VaultHintStatusResponse)
def get_vault_hint_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> VaultHintStatusResponse:
    """Gibt den Status des Passwort-Hinweises und Cooldowns zurück."""
    _check_vault_enabled()
    return vault_service.get_vault_hint_status(db, current_user.id)


@router.post("/request-hint")
@limiter.limit("5/minute")
async def send_vault_hint(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    __=Depends(verify_csrf),
) -> dict[str, str]:
    """Sendet den hinterlegten Passwort-Hinweis an die E-Mail des Benutzers (max. 1x alle 10 Minuten).

    Zwei Bremsen, weil eine zu wenig ist: der Zähler hier begrenzt den Andrang
    pro Herkunft, die Sperrfrist im Service den pro Konto. Ohne die erste kosten
    schon die abgewiesenen Anfragen jeweils einen Datenbank-Roundtrip.
    """
    _check_vault_enabled()
    success, msg = await vault_service.request_vault_hint_email(db, current_user)
    if not success:
        # Falls Cooldown aktiv ist: 429 Too Many Requests
        if "10 Minuten" in msg:
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=msg)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg)
    return {"status": "ok", "message": msg}


# ─── Tresor-Cloud: verschluesselte Dateien ───────────────────────────────────
#
# Alle Wege sind angemeldet und gelten nur fuer die Blobs des eigenen Kontos.
# Ein fremder Blob ist wie ein fehlender (404). Die Grenzen sind grosszuegig und
# gehoeren nur diesen Routen: ein Upload von tausend Fotos sind dreitausend
# Blobs, und nichts davon darf die Anmeldegrenze aufbrauchen.


def _blob_fehler(exc: vault_blob_service.BlobFehler) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get("/speicher", response_model=VaultSpeicher)
def tresor_speicher(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> VaultSpeicher:
    _check_vault_enabled()
    return VaultSpeicher(**vault_blob_service.speicher(db, current_user))


@router.post("/blobs", status_code=status.HTTP_201_CREATED)
@limiter.limit("1200/minute")
def blob_anlegen(
    payload: VaultBlobAnlegen,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    __=Depends(verify_csrf),
) -> dict[str, str]:
    _check_vault_enabled()
    try:
        vault_blob_service.anlegen(
            db, current_user, payload.id, payload.chunk_count, payload.bytes_total, payload.delete_verifier
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
    current_user: User = Depends(get_current_user),
    __=Depends(verify_csrf),
) -> Response:
    """Nimmt einen Chunk als rohe Bytes an. Laenge exakt wie angemeldet, sonst 413/422.

    Datenbank und Platte laufen im Threadpool, nacheinander mit derselben Sitzung.
    """
    await run_in_threadpool(_check_vault_enabled)
    try:
        erwartet = await run_in_threadpool(vault_blob_service.laenge_fuer_upload, db, current_user.id, blob_id, index)
    except vault_blob_service.BlobFehler as exc:
        raise _blob_fehler(exc) from exc

    daten = bytearray()
    async for teil in request.stream():
        daten += teil
        if len(daten) > erwartet:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Chunk zu gross.")
    if len(daten) != erwartet:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Chunk hat die falsche Laenge.")

    await run_in_threadpool(vault_blob_service.chunk_schreiben, blob_id, index, bytes(daten))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/blobs/{blob_id}/status", response_model=VaultBlobStatus)
def blob_status(
    blob_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> VaultBlobStatus:
    _check_vault_enabled()
    try:
        blob = vault_blob_service.eigener_blob(db, current_user.id, blob_id)
    except vault_blob_service.BlobFehler as exc:
        raise _blob_fehler(exc) from exc
    return VaultBlobStatus(
        state=blob.state,
        chunk_count=blob.chunk_count,
        vorhanden=vault_blob_service.vorhandene_chunks(blob),
    )


@router.post("/blobs/{blob_id}/fertig")
def blob_fertig(
    blob_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    __=Depends(verify_csrf),
) -> dict[str, str]:
    _check_vault_enabled()
    try:
        vault_blob_service.fertigstellen(db, current_user.id, blob_id)
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
    current_user: User = Depends(get_current_user),
) -> FileResponse:
    _check_vault_enabled()
    try:
        pfad = vault_blob_service.chunk_pfad(db, current_user.id, blob_id, index)
    except vault_blob_service.BlobFehler as exc:
        raise _blob_fehler(exc) from exc
    return FileResponse(pfad, media_type="application/octet-stream")


@router.post("/blobs/klein")
@limiter.limit("300/minute")
def blobs_klein(
    payload: VaultBlobsKlein,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    __=Depends(verify_csrf),
) -> Response:
    """Bis zu 100 Miniaturen in einer Antwort, damit die Galerie nicht tausend Anfragen stellt."""
    _check_vault_enabled()
    inhalt = vault_blob_service.kleine_lesen(db, current_user.id, payload.ids)
    return Response(content=inhalt, media_type="application/octet-stream")


@router.delete("/blobs/{blob_id}")
def blob_loeschen(
    blob_id: str,
    payload: VaultBlobLoeschen,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    __=Depends(verify_csrf),
) -> dict[str, str]:
    _check_vault_enabled()
    try:
        vault_blob_service.loeschen(db, current_user.id, blob_id, payload.schluessel)
    except vault_blob_service.BlobFehler as exc:
        raise _blob_fehler(exc) from exc
    return {"state": "geloescht"}
