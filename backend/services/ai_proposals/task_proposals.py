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

def _popup_create_payload(db: Session, user: User, rest: dict) -> tuple[dict, dict]:
    title = str(rest.get("title", "")).strip()
    content_markdown = str(rest.get("content_markdown", "")).strip()
    if not title or not content_markdown:
        raise AiActionValidationError("Pop-up erfordert title und content_markdown")

    is_active = bool(rest.get("is_active", True))
    start_at = rest.get("start_at")
    end_at = rest.get("end_at")
    button_text = rest.get("button_text")
    button_url = rest.get("button_url")

    payload = {
        "title": redact_sensitive_text(title),
        "content_markdown": redact_sensitive_text(content_markdown),
        "is_active": is_active,
        "start_at": str(start_at).strip() if start_at else None,
        "end_at": str(end_at).strip() if end_at else None,
        "button_text": redact_sensitive_text(str(button_text).strip()) if button_text else None,
        "button_url": str(button_url).strip() if button_url else None,
    }
    preview = {
        "operation": "popup_create",
        "title": redact_sensitive_text(title),
        "content_preview": redact_sensitive_text(content_markdown)[:300],
        "is_active": is_active,
        "start_at": str(start_at).strip() if start_at else None,
        "end_at": str(end_at).strip() if end_at else None,
        "button_text": redact_sensitive_text(str(button_text).strip()) if button_text else None,
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

def _ausfuehren_popup_create(db: Session, rahmen: _AusfuehrungsRahmen) -> _Ausgefuehrt:
    from models import PanelPopup
    from datetime import datetime

    p = rahmen.payload
    start_dt = (
        datetime.fromisoformat(p["start_at"].replace("Z", "+00:00"))
        if p.get("start_at")
        else None
    )
    end_dt = (
        datetime.fromisoformat(p["end_at"].replace("Z", "+00:00"))
        if p.get("end_at")
        else None
    )

    popup = PanelPopup(
        title=str(p["title"]),
        content_markdown=str(p["content_markdown"]),
        is_active=bool(p.get("is_active", True)),
        start_at=start_dt,
        end_at=end_dt,
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
    from services.ai_action_service import execute_read_tool

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
