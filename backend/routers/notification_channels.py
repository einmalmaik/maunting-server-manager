"""Der dauerhafte Ausweis eines Geräts für den Hintergrundkanal.

Anlegen, auflisten, widerrufen. Der Ausweis selbst erscheint genau einmal in
der Antwort auf `POST` — danach nur noch seine Kennung und sein Label.

**Warum ein frischer Nachweis.** Ein Ausweis, der länger gilt als eine Sitzung,
ist ein neuer Zugang (Punkt 21). Er entsteht deshalb nur gegen Passwort, einen
eingerichteten zweiten Faktor oder einen Passkey — nie gegen ein bloß
vorhandenes Access-Token. `auth_rate_limit` sitzt davor, weil hier ein Passwort
geprüft wird.

**Was er darf.** Nur zuhören: Ereignisstrom lesen, Präsenz melden, Mailboxen
abonnieren (`routers/sync_events.py`). Jede andere Route lehnt ihn ab, weil
`get_current_user` ihn nicht kennt.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db
from dependencies import get_current_user, session_familie, verify_csrf
from middleware.rate_limit import auth_rate_limit
from models.user import User
from schemas.passkey import FaktorNachweis
from services import notification_channel_service, passkey_service

router = APIRouter(prefix="/api/notifications/channels", tags=["notifications"])


class KanalAnlage(FaktorNachweis):
    """Ein neuer Kanal — samt Nachweis, dass der Mensch es ist."""

    label: str | None = Field(None, max_length=64)


class KanalAusgabe(BaseModel):
    id: int
    label: str | None
    created_at: str | None
    last_seen_at: str | None


class KanalAngelegt(KanalAusgabe):
    """Die Antwort auf die Anlage — der einzige Ort mit dem Ausweis."""

    token: str


def _ausgabe(kanal) -> KanalAusgabe:
    return KanalAusgabe(
        id=kanal.id,
        label=kanal.label,
        created_at=kanal.created_at.isoformat() if kanal.created_at else None,
        last_seen_at=kanal.last_seen_at.isoformat() if kanal.last_seen_at else None,
    )


@router.get("", response_model=list[KanalAusgabe])
def meine_kanaele(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[KanalAusgabe]:
    """Was gerade zuhören darf. Der Ausweis selbst ist hier nie enthalten."""
    return [_ausgabe(k) for k in notification_channel_service.liste(db, user.id)]


@router.post(
    "",
    response_model=KanalAngelegt,
    dependencies=[Depends(verify_csrf), Depends(auth_rate_limit)],
)
def kanal_anlegen(
    req: KanalAnlage,
    response: Response,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    familie: str | None = Depends(session_familie),
) -> KanalAngelegt:
    """Legt einen Kanal an und gibt den Ausweis **einmal** zurück.

    Der Ausweis steht in der Antwort und nirgends sonst. Wer ihn verliert,
    legt einen neuen an und widerruft den alten.
    """
    fehlt = passkey_service.frischer_nachweis_fehlt(
        db,
        user,
        password=req.password,
        otp_code=req.otp_code,
        passkey=req.passkey.model_dump() if req.passkey else None,
        zweck="notification_channel",
    )
    if fehlt:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=fehlt)

    kanal, ausweis = notification_channel_service.anlegen(
        db, user, familie=familie, label=req.label
    )
    response.headers["Cache-Control"] = "no-store"
    # Der einzige Moment, in dem dieser Wert das Backend verlässt.
    return KanalAngelegt(**_ausgabe(kanal).model_dump(), token=ausweis)


@router.delete("/{kanal_id}", status_code=status.HTTP_204_NO_CONTENT)
def kanal_widerrufen(
    kanal_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    __: None = Depends(verify_csrf),
) -> Response:
    """Widerruft einen Kanal — Zeile und offene Verbindung.

    Nur der eigene Kanal: eine fremde Kennung ist 404, nicht 403. Ob sie
    existiert, geht niemanden etwas an, der sie nicht angelegt hat.
    """
    kanal = next(
        (k for k in notification_channel_service.liste(db, user.id) if k.id == kanal_id),
        None,
    )
    if kanal is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Kanal nicht gefunden")
    notification_channel_service.widerrufen(db, kanal)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
