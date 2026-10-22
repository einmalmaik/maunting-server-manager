from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from dependencies import (
    get_current_user,
    session_familie,
    verify_csrf,
)
from models import User
from schemas.social import (
    E2eeDeviceItem,
    E2eeDeviceUpdate,
    E2eeDeviceApproveRequest,
    E2eeDeviceRemoveRequest,
    E2eeDeviceResetRequest,
    E2eeDeviceSelfApproveRequest,
)
from middleware.rate_limit import auth_rate_limit
from services import e2ee_device_service
from services.auth_service import AuthService
from routers.social.basis import _check_social_enabled

logger = logging.getLogger(__name__)

router = APIRouter()



@router.put("/e2ee/devices/self", response_model=E2eeDeviceItem, dependencies=[Depends(_check_social_enabled), Depends(verify_csrf)])
def put_own_e2ee_device(
    req: E2eeDeviceUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    familie: str | None = Depends(session_familie),
) -> dict:
    """Veröffentlicht den Schlüssel *dieses* Geräts.

    `user.id` kommt aus der Sitzung, nie aus dem Rumpf: ein Gerät schreibt
    ausschließlich seinen eigenen Eintrag.
    """
    try:
        eintrag = e2ee_device_service.veroeffentlichen(
            db,
            user,
            device_id=req.device_id,
            public_key_jwk=req.public_key,
            label=req.label or "",
            signing_public_key_jwk=req.signing_public_key or "",
            familie=familie,
            self_signature=req.approval_signature or "",
        )
    except (e2ee_device_service.GeraetedeckelErreichtError, e2ee_device_service.FremdeSitzungError) as e:
        # 409, nicht 400: die Anfrage ist in Ordnung, der Zustand des Kontos
        # steht ihr entgegen. Der Client zeigt den Text unverändert an.
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return e2ee_device_service.eintrag_als_dict(eintrag)


@router.get("/e2ee/devices/{target_user_id}", response_model=list[E2eeDeviceItem], dependencies=[Depends(_check_social_enabled)])
def get_e2ee_devices(
    target_user_id: int,
    include_unapproved: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[dict]:
    """Die Zustelladressen eines Kontos.

    Angemeldet zu sein genügt — wie zuvor beim Kontoschlüssel. Was hier
    herauskommt, sind öffentliche Schlüssel und bedeutungsfreie Zufallskennungen.

    Schutz vor unbefugtem Mitlesen: Dritte erhalten AUSSCHLIESSLICH bestätigte
    Geräte (nur_bestaetigt=True). Nur der Kontoinhaber selbst darf include_unapproved=True
    abfragen (z. B. um ausstehende Geräte zu prüfen und freizugeben).
    """
    target = db.query(User).filter_by(id=target_user_id).first()
    if not target or not target.is_active:
        raise HTTPException(status_code=404, detail="Benutzer nicht gefunden")
    nur_bestaetigt = True
    if include_unapproved and current_user.id == target_user_id:
        nur_bestaetigt = False
    return e2ee_device_service.geraete(db, target_user_id, nur_bestaetigt=nur_bestaetigt)


def _entfernen_nachweis(
    db: Session,
    user: User,
    *,
    password: str | None,
    otp_code: str | None,
    passkey: object | None,
) -> None:
    """Die Sicherheitskaskade vor dem Entfernen eines Geraets.

    In dieser Reihenfolge, und nur das, was das Konto wirklich hat:

    1. Der eingerichtete zweite Faktor — Passkey oder Authenticator-Code. Was
       gilt, entscheidet `passkey_service.zweiter_faktor_bestaetigt` am
       gespeicherten Material, nie ein Feld des Clients. Die Oberflaeche bietet
       zuerst den Passkey an und laesst auf den Code umschalten, wenn das
       Geraet keinen Passkey bedienen kann.
    2. Das Passwort, auch bei einem Social-Login-Konto, das sich nachtraeglich
       eines gesetzt hat (`has_password`).
    3. Die Sitzung selbst — aber nur bei einem Konto ohne Passwort und ohne
       zweiten Faktor. Das ist der Social-Login-Fall aus dem Fehlerbericht; ein
       Konto mit Passwort kommt hier nie vorbei.

    Ein Konto ohne Passwort und mit 2FA landet in Stufe 1, nicht in Stufe 3:
    die Faktoren stehen vor dem Passwort.
    """
    if user.two_factor_enabled:
        from services import passkey_service

        if not passkey_service.zweiter_faktor_bestaetigt(
            db,
            user,
            otp_code=otp_code,
            passkey=passkey.model_dump() if passkey is not None else None,
            zweck="e2ee_remove",
        ):
            raise HTTPException(status_code=403, detail="2FA-Bestätigung erforderlich oder ungültig")
        return
    if not user.has_password:
        return
    if (
        not password
        or not user.password_hash
        or not AuthService.verify_password(password, user.password_hash)
    ):
        raise HTTPException(status_code=403, detail="Passwort falsch")


def _geraete_nachweis(
    db: Session,
    user: User,
    *,
    zweck: str,
    password: str | None,
    confirmation: str | None,
    otp_code: str | None,
    passkey: object | None,
) -> None:
    """Frischer Nachweis vor jeder Aenderung am Geraeteverzeichnis.

    Drei Wege, und keiner davon ist das blosse Token: der eingerichtete zweite
    Faktor (Passkey oder Code), sonst das Passwort, sonst bei Konten ohne
    Passwort das Bestaetigungswort RESET. Was gilt, entscheiden die
    eingerichteten Faktoren — nie ein Feld, das der Client behauptet.

    Ein Ort fuer beide Wege (`self/reset` und `self/reapprove`): bis 10/2026
    stand die Pruefung nur im Reset, und die Bereinigung eines wartenden
    Geraets lief ganz ohne sie.
    """
    if user.two_factor_enabled:
        from services import passkey_service

        if not passkey_service.zweiter_faktor_bestaetigt(
            db,
            user,
            otp_code=otp_code,
            passkey=passkey.model_dump() if passkey is not None else None,
            zweck=zweck,
        ):
            raise HTTPException(status_code=403, detail="2FA-Bestätigung erforderlich oder ungültig")
        return
    if not user.has_password:
        if (confirmation or "").strip().upper() != "RESET":
            raise HTTPException(status_code=400, detail="Bestätigung RESET erforderlich")
        return
    if (
        not password
        or not user.password_hash
        or not AuthService.verify_password(password, user.password_hash)
    ):
        raise HTTPException(status_code=403, detail="Passwort falsch")


@router.post("/e2ee/devices/self/approve", dependencies=[Depends(_check_social_enabled), Depends(verify_csrf)])
def approve_own_e2ee_device(
    req: E2eeDeviceApproveRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Gibt ein wartendes Gerät frei — mit der Unterschrift eines freigegebenen.

    Die Unterschrift geht mit der Geräteliste hinaus. Gegenüber prüfen sie
    selbst; was hier steht, ist nur die Zustellsperre für wartende Geräte.
    """
    if not e2ee_device_service.bestaetigen(
        db, user, req.device_id, approver_device_id=req.approver_device_id, signature=req.signature
    ):
        raise HTTPException(status_code=403, detail="Freigabe nicht gültig unterschrieben")
    return {"ok": True}


@router.post(
    "/e2ee/devices/self/reapprove",
    dependencies=[Depends(_check_social_enabled), Depends(verify_csrf), Depends(auth_rate_limit)],
)
def reapprove_own_e2ee_device(
    req: E2eeDeviceSelfApproveRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Holt das eigene Geraet zurueck.

    Der Fall dahinter: das Geraet hat seinen Schluessel neu erzeugt (verlorene
    Ablage, neuer Browser) und ist damit wartend. Ohne diesen Weg bliebe nur
    der Neustart des ganzen Verzeichnisses, der alle Sitzungen sperrt — oder
    ein anderes freigegebenes Geraet, das der Mensch vielleicht nicht mehr hat.

    Der Nachweis ist Pflicht (Regel: was einen Zugang erzeugt, verlangt einen
    frischen Nachweis) und liegt hinter `auth_rate_limit`. Mit ihm darf sich
    das Geraet auch dann freigeben, wenn noch ein anderes freigegebenes Geraet
    existiert: der Nachweis ist derselbe wie beim Anmelden, und das andere
    Geraet sieht die neue Freigabe in seiner Liste. Ohne Nachweis bleibt es
    bei der Unterschrift des freigegebenen Geraets.

    **Erst pruefen, dann den Nachweis verbrauchen.** Ein Passkey oder
    Einmalcode gilt genau einmal; stand er vor der Pruefung „Geraet
    unbekannt" war er fuer eine 404 weg. Die Regel selbst faellt trotzdem erst
    in `wieder_freigeben` unter der Kontosperre — hier wird nur der
    offensichtliche Fall abgefangen.
    """
    hindernis = e2ee_device_service.freigabe_hindernis(db, user, req.device_id)
    if hindernis == "nicht_gefunden":
        raise HTTPException(status_code=404, detail="Gerät nicht gefunden")

    _geraete_nachweis(
        db,
        user,
        zweck="e2ee_reapprove",
        password=req.password,
        confirmation=req.confirmation,
        otp_code=req.otp_code,
        passkey=req.passkey,
    )
    try:
        freigegeben = e2ee_device_service.wieder_freigeben(
            db, user, req.device_id, signatur=req.signature or None, mit_nachweis=True
        )
    except e2ee_device_service.GeraeteaenderungAbgelehntError as e:
        raise HTTPException(status_code=403, detail=str(e))
    if not freigegeben:
        raise HTTPException(status_code=404, detail="Gerät nicht gefunden")
    return {"ok": True, "is_approved": True}


@router.get("/e2ee/devices/self/pending", response_model=list[E2eeDeviceItem], dependencies=[Depends(_check_social_enabled)])
def get_pending_e2ee_devices(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[dict]:
    """Liefert alle noch unbestätigten Geräte des angemeldeten Benutzers."""
    return e2ee_device_service.ausstehende_geraete(db, user)


@router.post(
    "/e2ee/devices/self/remove",
    dependencies=[Depends(_check_social_enabled), Depends(verify_csrf), Depends(auth_rate_limit)],
)
def remove_own_e2ee_device(
    req: E2eeDeviceRemoveRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    familie: str | None = Depends(session_familie),
) -> dict:
    """Entfernt ein Gerät und sperrt seine Sitzung sofort.

    Ein freigegebenes Gerät entfernt nur ein freigegebenes Gerät mit
    Unterschrift — sonst räumte, wer nur das Passwort hat, die echten Geräte
    ab und stünde danach allein als erstes da.

    **Dazu kommt der Nachweis.** Bis 10/2026 genügte ein Klick auf „Entfernen“:
    wer eine offene Sitzung vorfand, warf damit jedes echte Gerät hinaus. Jetzt
    gilt die Kaskade aus dem Fehlerbericht — eingerichteter zweiter Faktor
    (Passkey oder Authenticator-Code), sonst Passwort, und nur bei einem Konto
    ohne beides die Sitzung selbst.

    Der Nachweis gilt für **freigegebene** Geräte. Ein wartendes hat nie etwas
    bekommen; es zu entfernen nimmt niemandem etwas weg, und die Kopplung mit
    falscher Sicherheitsnummer muss sofort abbrechbar bleiben.

    **Das eigene Gerät ist kein Ziel.** Sonst wäre der Weg ein Abmelden mit
    Nebenwirkung: das Verzeichnis verlöre einen Eintrag, den es behalten muss.

    **`auth_rate_limit`.** Hier wird ein Passwort oder ein Einmalcode geprüft.
    Ohne die strenge Grenze bot die Route einen zweiten Ort, an dem sich diese
    Geheimnisse raten ließen — die Geräteunterschrift davor ist wiederverwendbar
    und hält niemanden auf, der ein freigegebenes Gerät kontrolliert.
    """
    hindernis = e2ee_device_service.entfernen_hindernis(
        db, user, req.device_id, anfrage_familie=familie
    )
    if hindernis == "nicht_gefunden":
        raise HTTPException(status_code=404, detail="Gerät nicht gefunden")
    if hindernis == "selbst":
        raise HTTPException(
            status_code=403,
            detail="Dieses Gerät kann sich nicht selbst entfernen.",
        )

    # Erst der Beleg, dann der Nachweis: ein Einmalcode oder Passkey gilt genau
    # einmal und darf nicht fuer eine 403 verbrannt werden.
    if not e2ee_device_service.entfernen_beleg_gilt(
        db, user, req.device_id, req.approver_device_id, req.signature
    ):
        raise HTTPException(
            status_code=403,
            detail="Ein freigegebenes Gerät entfernt nur ein freigegebenes Gerät.",
        )

    if e2ee_device_service.eintrag_ist_freigegeben(db, user, req.device_id):
        _entfernen_nachweis(
            db,
            user,
            password=req.password,
            otp_code=req.otp_code,
            passkey=req.passkey,
        )
    try:
        entfernt = e2ee_device_service.vergessen(
            db,
            user,
            req.device_id,
            approver_device_id=req.approver_device_id,
            signature=req.signature,
            anfrage_familie=familie,
        )
    except e2ee_device_service.GeraeteaenderungAbgelehntError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return {"ok": entfernt}


@router.post("/e2ee/devices/self/reset", dependencies=[Depends(_check_social_enabled), Depends(verify_csrf), Depends(auth_rate_limit)])
def reset_own_e2ee_devices(
    req: E2eeDeviceResetRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    familie: str | None = Depends(session_familie),
) -> dict:
    """Alle Geräte verloren: Verzeichnis leeren, alle anderen Sitzungen sperren.

    Verifikation:
    - Bei aktivem 2FA: der eingerichtete Faktor — otp_code (TOTP) oder passkey.
    - Ohne 2FA:
      - Konten ohne Passwort (nur Social Login): Bestätigungswort RESET.
      - Konten mit Passwort: Passwort-Prüfung, auch mit Social-Verknüpfung.
    """
    _geraete_nachweis(
        db,
        user,
        zweck="e2ee_reset",
        password=req.password,
        confirmation=req.confirmation,
        otp_code=req.otp_code,
        passkey=req.passkey,
    )

    return {"ok": True, "removed": e2ee_device_service.zuruecksetzen(db, user, familie)}
