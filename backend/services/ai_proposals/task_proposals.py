from __future__ import annotations

import logging
import json
from uuid import uuid4
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from fastapi import HTTPException

from models import User
from services import audit_service, permission_service, ai_task_service
from services.ai_action_errors import AiActionValidationError
from services.ai_redaction import redact_sensitive_text
from services.ai_proposals.base import _AusfuehrungsRahmen, _Ausgefuehrt

logger = logging.getLogger(__name__)

_AUFGABEN_FELDER = frozenset({
    "task_id", "title", "instruction", "kind", "enabled", "plan_kind",
    "time_of_day", "weekdays", "interval_hours", "once_at", "timezone",
    "channel",
})

_POPUP_FELDER = frozenset({
    "popup_id", "title", "content_markdown", "is_active",
    "start_at", "end_at", "button_text", "button_url",
})

# Was beim Aendern unberuehrt bleibt, wenn das Modell es nicht nennt. Genannt
# werden muss ein Feld auch dann, wenn es auf `null` soll — deshalb entscheidet
# die **Anwesenheit** des Schluessels und nicht sein Wert.
_POPUP_OPTIONAL = ("start_at", "end_at", "button_text", "button_url")


def _popup_zeitpunkt(wert: object) -> datetime | None:
    if not wert:
        return None
    try:
        return datetime.fromisoformat(str(wert).replace("Z", "+00:00"))
    except ValueError:
        raise AiActionValidationError(
            f"'{wert}' ist kein Zeitpunkt im Format ISO-8601"
        ) from None


def _popup_set_payload(db: Session, user: User, rest: dict) -> tuple[dict, dict]:
    """Nutzlast fuer `propose_popup_set` — anlegen oder aendern.

    Welcher der beiden Faelle gemeint ist, entscheidet `popup_id`: ohne sie
    entsteht ein neues Pop-up, mit ihr wird das bestehende geaendert. Dasselbe
    Muster wie `propose_task_set` — die Felder sind in beiden Faellen dieselben,
    und zwei Werkzeuge dafuer waeren zweimal dasselbe Schema im Katalog.

    Das Pop-up wird **jetzt** aufgeschlagen und nicht erst beim Klick. Auf der
    Karte soll der Titel stehen, der geaendert wird, und nicht eine Kennung;
    und ein Vorschlag auf ein geloeschtes Pop-up soll gar nicht erst entstehen.
    """
    from models import PanelPopup

    if set(rest) - _POPUP_FELDER:
        raise AiActionValidationError("Pop-up-Tool hat ungueltige Argumente")

    # Eine leere Kennung heisst dasselbe wie keine: anlegen. Dieselbe Nachsicht
    # wie bei `propose_task_set` — ein Modell schickt lieber `""` als ein Feld
    # wegzulassen, das es gerade gelesen hat.
    roh = rest.get("popup_id")
    if isinstance(roh, str) and not roh.strip():
        roh = None
    popup_id: int | None = None
    bestehend: PanelPopup | None = None
    if roh is not None:
        try:
            popup_id = int(roh)
        except (TypeError, ValueError):
            raise AiActionValidationError(
                "popup_id muss eine Kennung aus popups_read sein"
            ) from None
        bestehend = db.query(PanelPopup).filter(PanelPopup.id == popup_id).first()
        if bestehend is None:
            raise AiActionValidationError(f"Pop-up {popup_id} gibt es nicht")

    title = str(rest["title"]).strip() if rest.get("title") is not None else None
    content_markdown = (
        str(rest["content_markdown"]).strip()
        if rest.get("content_markdown") is not None
        else None
    )

    if bestehend is None:
        # Ein neues Pop-up ohne Titel oder Text waere eine leere Karte.
        if not title or not content_markdown:
            raise AiActionValidationError(
                "Ein neues Pop-up erfordert title und content_markdown"
            )
    elif not any(feld in rest for feld in _POPUP_FELDER - {"popup_id"}):
        raise AiActionValidationError(
            "Es wurde nichts genannt, das geaendert werden soll"
        )
    # Ein leer genanntes Pflichtfeld ist beim Aendern **nicht** dasselbe wie ein
    # weggelassenes: es wuerde das Pop-up ohne Titel oder ohne Inhalt
    # zuruecklassen. Beide sind in der Datenbank `nullable=False` und im
    # Panel-Schema `min_length=1`; die KI darf sie nicht unterlaufen.
    if title == "":
        raise AiActionValidationError("Der Titel darf nicht leer sein")
    if content_markdown == "":
        raise AiActionValidationError("Der Inhalt darf nicht leer sein")
    # Die Zeitpunkte werden **jetzt** geprueft und nicht erst beim Klick. Eine
    # Karte, die im Bestaetigungsmoment an einem Datumsformat scheitert, hat dem
    # Benutzer eine Zusage hingelegt, die nicht haelt.
    for feld in ("start_at", "end_at"):
        if rest.get(feld):
            _popup_zeitpunkt(rest[feld])

    payload: dict = {"popup_id": popup_id}
    if title is not None:
        payload["title"] = redact_sensitive_text(title)
    if content_markdown is not None:
        payload["content_markdown"] = redact_sensitive_text(content_markdown)
    if "is_active" in rest:
        payload["is_active"] = bool(rest["is_active"])
    for feld in _POPUP_OPTIONAL:
        if feld not in rest:
            continue
        wert = rest[feld]
        text = str(wert).strip() if wert is not None and str(wert).strip() else None
        payload[feld] = (
            redact_sensitive_text(text)
            if text is not None and feld == "button_text"
            else text
        )

    preview = {
        "operation": "popup_update" if bestehend is not None else "popup_create",
        "popup_id": popup_id,
        # Beim Aendern steht der bisherige Titel da, wenn das Modell keinen
        # neuen nennt — sonst traegt die Karte "Pop-up aendern" und sonst nichts.
        "title": payload.get(
            "title",
            redact_sensitive_text(str(bestehend.title)) if bestehend else "",
        ),
        "content_preview": payload.get(
            "content_markdown",
            redact_sensitive_text(str(bestehend.content_markdown)) if bestehend else "",
        )[:300],
        "is_active": payload.get(
            "is_active", bool(bestehend.is_active) if bestehend else True
        ),
        "start_at": payload.get("start_at"),
        "end_at": payload.get("end_at"),
        "button_text": payload.get("button_text"),
    }
    return payload, preview

def _task_set_payload(db: Session, user: User, arguments: dict) -> tuple[dict, dict]:
    """Nutzlast fuer `propose_task_set` â€” anlegen oder aendern.

    Der Payload-Bau prueft **vollstaendig**: Zeitzone, Plan, Art, Zustellweg,
    Rechte und die autonome Freigabe. Das ist nicht nur fuer die Vorschau da.
    Ein Modell, dessen Vorschlag erst beim Klick scheitert, hat dem Benutzer
    eine Karte hingelegt, die nicht haelt â€” und im Chat steht dann eine
    Fehlermeldung an der Stelle, an der eine Zusage stand.

    Gespeichert wird hier nichts. `vorschau` arbeitet auf einer losen Aufgabe;
    die eigentliche Aenderung passiert erst in `_execute_task_set`, und dort
    laufen dieselben Pruefungen erneut.
    """
    if set(arguments) - _AUFGABEN_FELDER:
        raise AiActionValidationError("Aufgaben-Tool hat ungueltige Argumente")
    roh = arguments.get("task_id")
    # **Eine leere Kennung heisst dasselbe wie keine: anlegen.** Das Schema sagt
    # "weglassen legt neu an", aber ein Modell kann ein Feld schlecht weglassen,
    # das es gerade gelesen hat â€” es schickt stattdessen `""`. Die Unterscheidung
    # zwischen "nicht genannt" und "leer genannt" traegt hier nichts und kostete
    # im Betrieb die haeufigste aller Aufgaben: das Anlegen der ersten.
    if isinstance(roh, str) and not roh.strip():
        roh = None
    if roh is not None and not isinstance(roh, str):
        raise AiActionValidationError("task_id muss eine Kennung aus list_tasks sein")
    task_id = roh.strip() if isinstance(roh, str) else None

    felder = {name: wert for name, wert in arguments.items() if name != "task_id"}
    if task_id is not None and not felder:
        raise AiActionValidationError(
            "Es wurde nichts genannt, das geaendert werden soll"
        )

    preview = ai_task_service.vorschau(db, user=user, felder=felder, task_id=task_id)
    return {"task_id": task_id, "felder": felder}, preview

def _task_delete_payload(db: Session, user: User, arguments: dict) -> tuple[dict, dict]:
    """Nutzlast fuer `propose_task_delete`.

    Die Aufgabe wird **jetzt** aufgeschlagen, damit auf der Karte ihr Name und
    ihr Zeitplan stehen und nicht nur eine Kennung. "Aufgabe
    a3f2c1â€¦-â€¦ loeschen?" ist keine Frage, die jemand beantworten kann.
    """
    if set(arguments) != {"task_id"}:
        raise AiActionValidationError("Aufgaben-Tool hat ungueltige Argumente")
    aufgabe = ai_task_service.eigene_aufgabe(
        db, user=user, task_id=arguments["task_id"]
    )
    return (
        {"task_id": aufgabe.id},
        {
            "operation": "task_delete",
            "task_id": aufgabe.id,
            "title": aufgabe.title,
            "plan": ai_task_service.plan_text(aufgabe),
            "kind": aufgabe.kind,
            "enabled": bool(aufgabe.enabled),
        },
    )

def _ausfuehren_popup_set(db: Session, rahmen: _AusfuehrungsRahmen) -> _Ausgefuehrt:
    """Legt ein Pop-up an oder aendert das genannte.

    **Zwischen Vorschlag und Bestaetigung liegt ein Zeitfenster ohne
    Obergrenze**, und in ihm kann jemand das Pop-up im Panel geloescht haben.
    Deshalb wird hier erneut nachgesehen, statt sich auf die Pruefung im
    Payload-Bau zu verlassen; ein `None` an dieser Stelle wuerde sonst still
    ein zweites Pop-up anlegen.
    """
    from models import PanelPopup

    p = dict(rahmen.payload)
    popup_id = p.pop("popup_id", None)

    if popup_id is not None:
        popup = db.query(PanelPopup).filter(PanelPopup.id == int(popup_id)).first()
        if popup is None:
            raise AiActionValidationError(
                f"Pop-up {popup_id} gibt es nicht mehr — es wurde inzwischen geloescht"
            )
        if "title" in p:
            popup.title = str(p["title"])
        if "content_markdown" in p:
            popup.content_markdown = str(p["content_markdown"])
        if "is_active" in p:
            popup.is_active = bool(p["is_active"])
        if "start_at" in p:
            popup.start_at = _popup_zeitpunkt(p["start_at"])
        if "end_at" in p:
            popup.end_at = _popup_zeitpunkt(p["end_at"])
        if "button_text" in p:
            popup.button_text = str(p["button_text"]) if p["button_text"] else None
        if "button_url" in p:
            popup.button_url = str(p["button_url"]) if p["button_url"] else None
        popup.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(popup)
        return _Ausgefuehrt(
            result={"updated": True, "popup_id": popup.id, "title": popup.title}
        )

    popup = PanelPopup(
        title=str(p["title"]),
        content_markdown=str(p["content_markdown"]),
        is_active=bool(p.get("is_active", True)),
        start_at=_popup_zeitpunkt(p.get("start_at")),
        end_at=_popup_zeitpunkt(p.get("end_at")),
        button_text=str(p["button_text"]) if p.get("button_text") else None,
        button_url=str(p["button_url"]) if p.get("button_url") else None,
        created_by_user_id=rahmen.active_user.id,
    )
    db.add(popup)
    db.commit()
    db.refresh(popup)
    return _Ausgefuehrt(result={"created": True, "popup_id": popup.id, "title": popup.title})

def _ausfuehren_task_set(db: Session, rahmen: _AusfuehrungsRahmen) -> _Ausgefuehrt:
    # **Die Felder werden hier erneut geprueft**, nicht nur
    # angewandt. Zwischen Vorschlag und Bestaetigung liegt ein
    # Zeitfenster ohne Obergrenze, und in ihm kann der Betreiber die
    # autonome Freigabe zurueckgenommen haben. Ohne die zweite
    # Pruefung entstuende hier eine handelnde Aufgabe auf Grundlage
    # einer Freigabe, die es nicht mehr gibt â€” und sie liefe von da
    # an jede Nacht.
    #
    # `ai_task_service` prueft beides in `_anwenden`; deshalb steht
    # hier nur der Aufruf und keine eigene Kette.
    gemerkt = rahmen.payload.get("task_id")
    felder = dict(rahmen.payload.get("felder") or {})
    if gemerkt:
        aufgabe = ai_task_service.aendern(
            db, user=rahmen.active_user, task_id=str(gemerkt), felder=felder
        )
    else:
        aufgabe = ai_task_service.anlegen(
            db, user=rahmen.active_user, felder=felder
        )
    # `task_id` im Ergebnis ist die ID der **KI-Aufgabe**. Das gleichnamige
    # Feld von `_Ausgefuehrt` bleibt bewusst leer: es meint die
    # Operation-Task eines Lifecycles, und eine KI-Aufgabe ist keine.
    return _Ausgefuehrt(result={
        "task_id": aufgabe.id,
        "title": aufgabe.title,
        "plan": ai_task_service.plan_text(aufgabe),
        "enabled": bool(aufgabe.enabled),
        "next_run": (
            ai_task_service.utc(aufgabe.next_run_at).isoformat()
            if aufgabe.next_run_at is not None else None
        ),
    })

def _ausfuehren_task_delete(db: Session, rahmen: _AusfuehrungsRahmen) -> _Ausgefuehrt:
    geloescht = ai_task_service.loeschen(
        db, user=rahmen.active_user, task_id=str(rahmen.payload["task_id"])
    )
    return _Ausgefuehrt(result={"deleted": True, "title": geloescht})

def _ausfuehren_read_tool(db: Session, rahmen: _AusfuehrungsRahmen) -> _Ausgefuehrt:
    """Ein bestaetigter Lesevorschlag, geschwaerzt wie ein direkter Aufruf.

    Ohne autonomen Modus laeuft **jedes** Lesewerkzeug hier entlang, im Chat
    wie in der Stimme. Der direkte Weg (`ai_stream.read_tools`) schwaerzt jedes
    Ergebnis, bevor es zum Modell geht; dieser Weg tat es bis zum 23.09.2026
    nicht. Das Ergebnis landete roh in `ai_tool_results` und von dort im
    Kontext der naechsten Runde, etwa ein Passwort aus der Umgebung eines
    Blueprints (`read_blueprint` liefert sie ungefiltert).
    """
    from services.ai_action_service import execute_read_tool
    from services.ai_stream.read_tools import _ergebnis_schwaerzen
    from services.ai_stream.types import _FREITEXT_WERKZEUGE

    args = dict(rahmen.payload)
    if rahmen.server_id is not None and "server_id" not in args:
        args["server_id"] = rahmen.server_id
    res = execute_read_tool(
        db,
        user=rahmen.active_user,
        tool_name=rahmen.tool_name,
        arguments=args,
        herkunft="panel",
    )
    res = _ergebnis_schwaerzen(
        res, freitext=rahmen.tool_name in _FREITEXT_WERKZEUGE
    )
    return _Ausgefuehrt(result=res if isinstance(res, dict) else {"result": res})

def _ausfuehren_worker_start(db: Session, rahmen: _AusfuehrungsRahmen) -> _Ausgefuehrt:
    from services import ai_worker_service

    res = ai_worker_service.worker_start(
        db,
        user=rahmen.active_user,
        arguments=rahmen.payload,
        herkunft="panel",
    )
    return _Ausgefuehrt(result=res if isinstance(res, dict) else {"result": res})

def _ausfuehren_worker_cancel(db: Session, rahmen: _AusfuehrungsRahmen) -> _Ausgefuehrt:
    from services import ai_worker_service

    res = ai_worker_service.worker_cancel(
        db,
        user=rahmen.active_user,
        arguments=rahmen.payload,
    )
    return _Ausgefuehrt(result=res if isinstance(res, dict) else {"result": res})
