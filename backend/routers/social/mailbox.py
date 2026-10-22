from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database import get_db
from dependencies import (
    get_current_user,
    session_familie,
    verify_csrf,
)
from models import User
from schemas.social import (
    E2eeBlindEnvelopeCreate,
    E2eeBlindEnvelopeResponse,
    E2eeMailboxRegister,
    E2eeMailboxSyncResponse,
    E2eeTypingSignalCreate,
    MailboxPushAbos,
    PushSubscriptionCreate,
    DirectChatResponse,
    CanMessageResponse,
)
from services.social_service import SocialService
from services.sync_event_service import MAX_MAILBOXES
from services import webpush_service
from routers.social.basis import _check_social_enabled, mailbox_token

router = APIRouter()



@router.get("/push/public-key", dependencies=[Depends(_check_social_enabled)])
def get_push_public_key(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> dict:
    """Der `applicationServerKey`, gegen den ein Browser abonniert.

    Ein leerer Wert heißt: dieses Panel kann nicht zustellen. Der Client
    abonniert dann gar nicht erst, statt ein Abonnement anzulegen, das nie
    bedient wird.
    """
    return {"key": webpush_service.oeffentlicher_schluessel(db)}


@router.post("/push/subscribe", dependencies=[Depends(_check_social_enabled), Depends(verify_csrf)])
def subscribe_push(
    req: PushSubscriptionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    familie: str | None = Depends(session_familie),
) -> dict:
    """Trägt die Zustelladresse *dieses* Browsers ein.

    `user.id` kommt aus der Sitzung, nie aus dem Rumpf — genau wie beim
    Geräteschlüssel eine Route weiter oben. Eine fremde Adresse einem anderen
    Konto unterzuschieben ist damit kein Weg.
    """
    webpush_service.eintragen(
        db, user, endpoint=req.endpoint, p256dh=req.p256dh, auth=req.auth, familie=familie
    )
    return {"ok": True}


@router.delete("/push/subscribe", dependencies=[Depends(_check_social_enabled), Depends(verify_csrf)])
def unsubscribe_push(
    endpoint: str = Query(..., min_length=16, max_length=2048),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Entfernt eine Zustelladresse. Nur die eigene — fremde findet die Abfrage nicht."""
    entfernt = webpush_service.austragen(db, user, endpoint)
    # Und die mailboxgebundenen Zeilen derselben Adresse. Sie kennen kein
    # Konto, also kann sie auch kein Abmelden eines Kontos treffen — sie
    # hängen an dieser Adresse, und die trägt sich hier gerade aus.
    webpush_service.austragen_mailboxen(db, endpoint)
    return {"ok": entfernt}


@router.post(
    "/e2ee/mailbox-push",
    dependencies=[Depends(_check_social_enabled), Depends(verify_csrf)],
)
def subscribe_mailbox_push(
    req: MailboxPushAbos,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Trägt diesen Browser als Zustelladresse für Mailboxen ein.

    Das Gegenstück zu `/push/subscribe` für den Fall, dass es kein Konto zu
    benachrichtigen gibt. Die Sitzung entscheidet hier, **ob** eingetragen
    werden darf — und nur das: in der Zeile bleibt von ihr nichts übrig, kein
    `user_id` und kein Fremdschlüssel.

    Die Erlaubnis kommt aus derselben Prüfung wie beim Echtzeitstrom. Wichtig,
    dass es dieselbe ist: wäre Push die nachsichtigere Tür, könnte man sich
    über eine Benachrichtigung sagen lassen, was der Strom einem verschweigt.

    Was durchfällt, fällt still durch. Die Antwort nennt nur die Anzahl —
    welche Kennung abgelehnt wurde, wäre eine Auskunft darüber, welche
    Mailboxen es gibt.
    """
    erlaubt = SocialService.erlaubte_mailboxen(
        db,
        user.id,
        ((e.mailbox_id, e.mailbox_token) for e in req.eintraege[:MAX_MAILBOXES]),
    )

    for mid in erlaubt:
        webpush_service.eintragen_mailbox(
            db, mailbox_id=mid, endpoint=req.endpoint, p256dh=req.p256dh, auth=req.auth
        )

    # Was dieser Browser vorher hatte und jetzt nicht mehr nennt, fällt weg:
    # sonst bliebe die verlassene Gruppe als Zustellziel stehen, und das Gerät
    # bekäme weiter Meldungen über Nachrichten, die es nicht mehr lesen kann.
    bestand = webpush_service.mailboxen_von(db, req.endpoint)
    ueberzaehlig = bestand - set(erlaubt)
    if ueberzaehlig:
        webpush_service.austragen_mailboxen(db, req.endpoint, nur=ueberzaehlig)

    return {"ok": True, "count": len(erlaubt)}


@router.delete(
    "/e2ee/mailbox-push",
    dependencies=[Depends(_check_social_enabled), Depends(verify_csrf)],
)
def unsubscribe_mailbox_push(
    endpoint: str = Query(..., min_length=16, max_length=2048),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> dict:
    """Trägt diesen Browser aus allen Mailboxen aus. Der Weg beim Abmelden.

    Es wird bewusst nicht geprüft, wem die Adresse gehört: das ist in dieser
    Tabelle nicht hinterlegt, und es nachzutragen hiesse, die Zeile doch wieder
    einem Konto zuzuordnen. Die Adresse ist das Geheimnis des Browsers, der sie
    bekommen hat — sie steht in keiner Antwort dieses Panels.
    """
    return {"ok": True, "count": webpush_service.austragen_mailboxen(db, endpoint)}


@router.post(
    "/e2ee/mailbox/register",
    dependencies=[Depends(_check_social_enabled), Depends(verify_csrf)],
)
def register_blind_mailbox(
    req: E2eeMailboxRegister,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Hinterlegt den blinden Besitznachweis einer Mailbox.

    Der authentifizierte Übergang vom kontogebundenen Weg auf den blinden —
    dieselbe Stelle, die beim Tresor `/blind-register` ist. Danach gilt für
    diese Mailbox der Nachweis **zusätzlich** zur bisherigen Prüfung.
    """
    SocialService.register_blind_mailbox(
        db,
        user_id=current_user.id,
        mailbox_id=req.mailbox_id,
        auth_token=req.auth_token,
    )
    return {"ok": True}


@router.post("/e2ee/relay", response_model=E2eeBlindEnvelopeResponse, dependencies=[Depends(_check_social_enabled), Depends(verify_csrf)])
def relay_e2ee_message(
    req: E2eeBlindEnvelopeCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    nachweis: str | None = Depends(mailbox_token),
) -> dict:
    SocialService.assert_mailbox_token(db, req.blind_mailbox_id, nachweis, current_user.id)
    envelope = SocialService.relay_blind_envelope(
        db,
        blind_mailbox_id=req.blind_mailbox_id,
        ciphertext_envelope=req.ciphertext_envelope,
        sender_user_id=current_user.id,
        client_uuid=req.client_uuid,
        is_control=req.is_control,
        control_type=req.control_type,
        mailbox_token=nachweis,
        push_ausnahme=req.push_ausnahme,
    )

    return {
        "id": envelope.id,
        "blind_mailbox_id": envelope.blind_mailbox_id,
        "ciphertext_envelope": envelope.ciphertext_envelope,
        "client_uuid": envelope.client_uuid,
        "created_at": envelope.created_at,
    }


@router.get("/chat/can-message/{target_user_id}", response_model=CanMessageResponse, dependencies=[Depends(_check_social_enabled)])
def check_can_message_user(
    target_user_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Prüft, ob der angemeldete Nutzer den Zielnutzer direkt anschreiben darf."""
    can_msg, reason = SocialService.can_message_user(db, user.id, target_user_id)
    mid = SocialService.derive_blind_mailbox_id(user.id, target_user_id) if can_msg else None
    return {
        "can_message": can_msg,
        "reason": reason,
        "blind_mailbox_id": mid,
    }


@router.post("/chat/start/{target_user_id}", response_model=DirectChatResponse, dependencies=[Depends(_check_social_enabled), Depends(verify_csrf)])
def start_or_get_direct_chat(
    target_user_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Initiiert oder holt einen direkten Chatkanal mit dem Zielnutzer."""
    chat = SocialService.ensure_direct_chat(db, user.id, target_user_id)
    other = db.query(User).filter_by(id=target_user_id).first()
    if not other:
        raise HTTPException(status_code=404, detail="Zielnutzer nicht gefunden")
    is_friend = SocialService.is_confirmed_friend(db, user.id, target_user_id)
    pres = SocialService.get_presence_for_viewer(db, user.id, other)
    return {
        "id": chat.id,
        "other_user_id": other.id,
        "other_username": other.username,
        "other_avatar_url": other.avatar_url,
        "blind_mailbox_id": chat.blind_mailbox_id,
        "is_friend": is_friend,
        "other_privacy": getattr(other, "social_privacy", "friends"),
        "presence": pres,
        "created_at": chat.created_at,
        "updated_at": chat.updated_at,
    }


@router.get(
    "/e2ee/sync",
    response_model=E2eeMailboxSyncResponse,
    dependencies=[Depends(_check_social_enabled)],
)
def sync_blind_mailboxes(
    since_id: int = Query(0, ge=0, description="Nur Mailboxen mit Umschlägen nach dieser Envelope-ID synchronisieren"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    mailboxes = SocialService.sync_mailboxes(
        db, current_user=current_user, since_id=since_id
    )
    return {"mailboxes": mailboxes}


@router.get("/e2ee/mailbox/{blind_mailbox_id}", response_model=list[E2eeBlindEnvelopeResponse], dependencies=[Depends(_check_social_enabled)])
@router.get("/e2ee/envelopes/{blind_mailbox_id}", response_model=list[E2eeBlindEnvelopeResponse], dependencies=[Depends(_check_social_enabled)])
def fetch_blind_mailbox_envelopes(
    blind_mailbox_id: str,
    since_id: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
    oldest_first: bool = Query(
        False,
        description=(
            "Ohne since_id die aeltesten statt der neuesten Umschlaege liefern — "
            "fuer einen vollstaendigen Abgleich Seite fuer Seite."
        ),
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    nachweis: str | None = Depends(mailbox_token),
) -> list[dict]:
    # Eine Mailbox öffnet sich nur ihren Besitzern: entweder gehört das Konto
    # dazu, oder es legt den Besitznachweis vor. Seit die Kennung aus einem
    # Gruppengeheimnis fallen kann, ist der zweite Weg kein Zusatz mehr,
    # sondern der einzige — der Server kann dort niemanden nachschlagen.
    SocialService.assert_mailbox_zugang(db, current_user.id, blind_mailbox_id, nachweis)

    envelopes = SocialService.get_blind_envelopes(
        db,
        blind_mailbox_id=blind_mailbox_id,
        since_id=since_id,
        limit=limit,
        oldest_first=oldest_first,
    )
    # Hier bewusst **keine** Log-Zeile: der offene Chat ruft diese Route alle
    # fünf Sekunden mit derselben Mailbox und bekommt jedes Mal dieselben
    # Umschläge — eine Zeile je Abruf wäre Rauschen statt Spur. Das Einliefern
    # steht in `relay_blind_envelope`, das Abholen im Zugriffslog
    # (`docker logs -t`: `GET /api/social/e2ee/mailbox/… 200`).
    return [
        {
            "id": env.id,
            "blind_mailbox_id": env.blind_mailbox_id,
            "ciphertext_envelope": env.ciphertext_envelope,
            "client_uuid": env.client_uuid,
            "created_at": env.created_at,
        }
        for env in envelopes
    ]


@router.delete(
    "/e2ee/envelopes/{blind_mailbox_id}",
    dependencies=[Depends(_check_social_enabled), Depends(verify_csrf)],
)
def delete_blind_mailbox_envelopes(
    blind_mailbox_id: str,
    client_uuid: str = Query(..., min_length=1, max_length=64),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    nachweis: str | None = Depends(mailbox_token),
) -> dict:
    """Nimmt die Umschlaege einer geloeschten Nachricht aus der Mailbox.

    Adressiert wird ueber die logische Nachrichtenkennung, nicht ueber die
    Umschlagkennung: eine Nachricht liegt als eine Kopie je Zielgeraet da.
    """
    entfernt = SocialService.delete_blind_envelopes(
        db,
        blind_mailbox_id=blind_mailbox_id,
        client_uuid=client_uuid,
        user_id=current_user.id,
        mailbox_token=nachweis,
    )
    return {"ok": True, "deleted": entfernt}


@router.post("/e2ee/typing", dependencies=[Depends(_check_social_enabled), Depends(verify_csrf)])
def send_e2ee_typing_signal(
    req: E2eeTypingSignalCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    nachweis: str | None = Depends(mailbox_token),
) -> dict:
    # Dieselbe Tür wie beim Lesen: Besitznachweis oder Teilnahme. Bis
    # 29.09.2026 stand hier nur `assert_mailbox_token`, und das tut nichts,
    # solange kein Nachweis hinterlegt ist, auf ableitbaren Kennungen also
    # nie. Jedes Konto konnte so „tippt gerade" samt Namen in fremde Gruppen,
    # an fremde Geräte und an Konten nur für Freunde schicken.
    SocialService.assert_mailbox_zugang(db, user.id, req.blind_mailbox_id, nachweis)
    SocialService.broadcast_typing_signal(
        blind_mailbox_id=req.blind_mailbox_id,
        status=req.status,
        sender_id=user.id,
        sender_username=user.username,
        db=db,
    )
    return {"ok": True}
