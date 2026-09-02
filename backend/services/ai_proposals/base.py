from __future__ import annotations

import logging
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable
from sqlalchemy.orm import Session

from models import (
    AiActionProposal,
    Server,
    User,
)
from services import audit_service, permission_service
from services.ai_action_errors import (
    AiActionStateError,
    AiActionValidationError,
)
from services.ai_tool_registry import (
    GLOBAL_READ_TOOLS,
    GUARDIAN_HEILUNG_TOOLS,
    SERVER_READ_TOOLS,
    WERKZEUGE,
    WORKER_STEUERUNG,
)

logger = logging.getLogger(__name__)

_REPARATUR_RECHTE = {
    "repair_permissions": "server.files.write",
    "reallocate_port": "server.network.manage",
}

_LIFECYCLE_RECHTE = {
    "start": "server.start",
    "stop": "server.stop",
    "restart": "server.restart",
}

@dataclass(frozen=True)
class GuardianKontext:
    """Der Rahmen eines Laufs, den ein Vorfall ausgeloest hat â€” nicht ein Mensch.

    Er wird beim Start des Heilungslaufs gebildet, liegt im Arbeitsgedaechtnis
    des Laufs (`ai_runs.state_json`) und wird bei jeder Runde daraus wieder
    hergestellt. Drei Angaben, und jede traegt eine Schranke:

    * ``server_id`` â€” der **einzige** Server, an dem dieser Lauf arbeiten darf.
      Im gewoehnlichen Chat nennt das Modell die Server-ID selbst; das ist dort
      richtig, weil ein Mensch mitliest. Hier liest niemand mit, und die Eingabe
      des Modells stammt teilweise aus Serverlogs â€” also aus Text, den ein
      Spieler geschrieben haben kann. Der Bezug wird deshalb vorgegeben.
    * ``incident_id`` â€” welcher Vorfall gemeint ist. Fuer die Notiz-Zeile, den
      Bericht und das Audit.
    * ``incident_created_at`` â€” ab wann ein Backup als Nachweis taugt. Ein
      Backup von gestern liegt vor der Stoerung und beweist nichts ueber den
      Zustand, den die KI gleich anfasst.
    """

    server_id: int
    incident_id: int
    incident_created_at: datetime

@dataclass(frozen=True)
class AufgabenKontext:
    """Der Rahmen eines Laufs, den die Uhr ausgeloest hat â€” nicht ein Mensch.

    Das Gegenstueck zu `GuardianKontext` und bewusst **anders geschnitten**. Ein
    Heilungslauf gehoert einem Server; ein stehender Auftrag gehoert keinem. Der
    Benutzer hat "sieh nach **meinen Servern**" gesagt, und welche das sind,
    entscheidet seine Rechteliste, nicht der Auftrag. Es gibt hier deshalb keine
    Serverbindung und keine Backup-Schranke.

    Was bleibt, ist die Werkzeugmenge â€” und die haengt an ``kind``:

    * ``report`` liest, fasst zusammen und meldet.
    * ``act`` darf zusaetzlich handeln, und zwar nur, soweit `autonomy_allows`
      es im Einzelfall zulaesst. Der Rahmen erweitert nichts: er begrenzt.

    ``channel`` und ``title`` tragen nichts zur Schranke bei und stehen
    trotzdem hier. Sie werden am **Ende** des Laufs gebraucht, fuer den Bericht
    â€” und die Aufgabe kann bis dahin geloescht worden sein. Ein Bericht, der
    seinen eigenen Betreff aus einer Zeile holen muesste, die es nicht mehr
    gibt, waere ein Bericht, der ausgerechnet dann ausfaellt, wenn jemand
    aufgeraeumt hat.
    """

    task_id: str
    kind: str
    channel: str
    title: str

def _aad(proposal_id: str) -> str:
    return f"msm:ai:action-proposal:v1:{proposal_id}"

def _json_object(value: str) -> dict:
    try:
        decoded = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise AiActionStateError("AI_ACTION_PAYLOAD_INVALID") from exc
    if not isinstance(decoded, dict):
        raise AiActionStateError("AI_ACTION_PAYLOAD_INVALID")
    return decoded

def _permission_for(tool_name: str, payload: dict) -> tuple[str, ...]:
    """Die Permission-Keys, die dieses Werkzeug verlangt â€” alle zugleich."""
    if tool_name == "propose_server_lifecycle":
        recht = _LIFECYCLE_RECHTE.get(str(payload.get("operation")), "")
        return (recht,) if recht else ()
    if tool_name == "propose_server_repair":
        recht = _REPARATUR_RECHTE.get(str(payload.get("action")), "")
        if recht:
            return (recht,)
        return tuple(_REPARATUR_RECHTE.values())
    if tool_name in WORKER_STEUERUNG:
        return ("ai.background.use",)
    if tool_name in SERVER_READ_TOOLS:
        werkzeug = WERKZEUGE.get(tool_name)
        if werkzeug and werkzeug.angebot:
            return ("server.view", *werkzeug.angebot)
        return ("server.view",)
    werkzeug = WERKZEUGE.get(tool_name)
    if werkzeug and werkzeug.recht:
        return (werkzeug.recht,)
    if werkzeug and werkzeug.angebot:
        return werkzeug.angebot
    return ()

def _require_tool_permission(
    db: Session, user: User, server_id: int | None, tool_name: str, payload: dict
) -> None:
    if tool_name in {"propose_guardian_tuning", "read_guardian_incidents"}:
        from services.ai_guardian_settings import is_guardian_ai_enabled
        if not is_guardian_ai_enabled(db=db):
            raise AiActionValidationError("Guardian-KI-Integration ist deaktiviert")

    if tool_name in GLOBAL_READ_TOOLS:
        werkzeug = WERKZEUGE.get(tool_name)
        if werkzeug and werkzeug.angebot:
            has_any = any(
                permission_service.has_global_permission(db, user, p)
                for p in werkzeug.angebot
            )
            if not has_any:
                raise AiActionValidationError("AI-Aktion ist nicht erlaubt")
        return

    if tool_name in WORKER_STEUERUNG:
        if not permission_service.has_global_permission(db, user, "ai.background.use"):
            raise AiActionValidationError("Hintergrund-Worker sind nicht erlaubt")
        return

    if tool_name in SERVER_READ_TOOLS:
        if server_id is None:
            raise AiActionValidationError("Server-ID fehlt")
        if not permission_service.has_server_permission(db, user, server_id, "server.view"):
            raise AiActionValidationError("AI-Aktion ist nicht erlaubt")
        werkzeug = WERKZEUGE.get(tool_name)
        if werkzeug and werkzeug.angebot:
            has_any = any(
                permission_service.has_server_permission(db, user, server_id, p)
                for p in werkzeug.angebot
            )
            if not has_any:
                raise AiActionValidationError("AI-Aktion ist nicht erlaubt")
        return

    permissions = _permission_for(tool_name, payload)
    if not permissions:
        raise AiActionValidationError("AI-Aktion ist nicht erlaubt")

    werkzeug = WERKZEUGE.get(tool_name)
    if werkzeug is not None and werkzeug.recht_global:
        for permission in permissions:
            if not permission_service.has_global_permission(db, user, permission):
                raise AiActionValidationError("AI-Aktion ist nicht erlaubt")
        return

    if server_id is None:
        raise AiActionValidationError("AI-Aktion ist nicht erlaubt")
    for permission in permissions:
        if not permission_service.has_server_permission(db, user, server_id, permission):
            raise AiActionValidationError("AI-Aktion ist nicht erlaubt")
    if tool_name in {
        "propose_config_update",
        "propose_config_patch",
        "propose_config_set",
    } and not permission_service.has_server_permission(
        db, user, server_id, "server.files.read"
    ):
        raise AiActionValidationError("Config-Vorschlag benoetigt Lese- und Schreibrecht")

def _verlangt_gesichertes_backup(
    db: Session, server_id: int, tool_name: str, *, seit: datetime | None
) -> None:
    """Die Schranke: kein Eingriff ohne nachweislich geglecktes Backup.

    Gilt **nur** im von Guardian ausgeloesten Heilungslauf. Im gewoehnlichen
    Chat entscheidet weiterhin der Mensch mit seinem Klick; ihn zum Backup zu
    zwingen waere eine Aenderung, um die niemand gebeten hat, und sie wuerde
    jede kleine Korrektur zu einem Minutenvorgang machen.

    Der Nachweis ist `Backup.verified_at`, nicht das blosse Vorhandensein einer
    Zeile. Der Unterschied ist der ganze Punkt: eine Zeile entsteht auch dann,
    wenn der Remote-Agent-Pfad sie vor der Arbeit des Agenten anlegt, und
    `size_mb` ist fuer jedes Archiv unter einem Megabyte 0. `verified_at` wird
    ausschliesslich gesetzt, nachdem die Datei nachgemessen wurde.

    ``seit`` ist der Zeitpunkt des Vorfalls. Ein Backup von gestern beweist
    nichts ueber den Zustand, den die KI gleich anfasst â€” es liegt vor der
    Stoerung, und was seitdem passiert ist, holt es nicht zurueck.

    Der Fehler ist ein `AiActionStateError` und keine Validierungsmeldung: es
    ist kein Formfehler des Modells, sondern eine Bedingung der Anlage. Das
    Modell erfaehrt sie ueber den `error_code` und kann darauf antworten, indem
    es zuerst `propose_backup` aufruft.
    """
    from models import Backup
    from services.ai_tool_registry import GUARDIAN_BACKUP_PFLICHT_TOOLS

    if tool_name not in GUARDIAN_BACKUP_PFLICHT_TOOLS:
        return
    abfrage = db.query(Backup.id).filter(
        Backup.server_id == server_id,
        Backup.verified_at.isnot(None),
    )
    if seit is not None:
        abfrage = abfrage.filter(Backup.created_at >= seit)
    if abfrage.first() is None:
        raise AiActionStateError("AI_BACKUP_UNVERIFIED")

def guardian_aus_lauf(db: Session, run_id: str | None) -> "GuardianKontext | None":
    """Holt den Guardian-Rahmen eines Vorschlags aus seinem Lauf zurueck.

    `execute_proposal` bekommt keinen Rahmen uebergeben â€” es wird aus dem Router
    gerufen, wenn ein Mensch auf "Bestaetigen" klickt, und das kann Stunden nach
    dem Anlegen sein. Der Rahmen lebt im Arbeitsgedaechtnis des Laufs, und der
    Vorschlag traegt dessen Kennung; damit ist er wiederherstellbar, ohne dass
    eine Spalte an `ai_action_proposals` noetig waere.

    Genau diese Luecke machte die zugesagte doppelte Pruefung zur Behauptung: die
    Registry sagt zu `propose_file_delete` zu, der Backup-Nachweis werde "beim
    Anlegen **und** vor der Ausfuehrung" geprueft, `_verlangt_gesichertes_backup`
    hatte aber genau einen Aufrufer. Der Abstand zwischen beiden Punkten ist kein
    Detail: dazwischen liegt ein Commit und ein unbegrenztes Zeitfenster, in dem
    `cleanup_old_backups` das nachgewiesene Archiv abraeumen kann. Dieselbe
    Begruendung laesst die Rechtepruefung dreimal laufen.
    """
    if not run_id:
        return None
    from models import AiRun
    from services import ai_run_service
    # Verzoegert wegen des Importzyklus: der Stream-Service importiert dieses
    # Modul beim Laden.
    from services.ai_stream_service import GuardianRahmenUnlesbar, guardian_aus_zustand

    run = db.get(AiRun, run_id)
    if run is None:
        return None
    try:
        # **Derselbe** Parser wie in jeder Laufrunde, keine zweite Auslegung.
        # Hier stand eine Abschrift mit eigener Semantik, und sie war bereits
        # gedriftet: ein vorhandener, aber nicht-dict Rahmen galt hier als
        # â€žkein Guardian" und liess `execute_proposal` ohne Backup-Nachweis
        # und ohne Serverbindung weiterlaufen â€” waehrend dieselbe Lage im
        # Stream ausdruecklich wirft, weil der Verlust des Rahmens die
        # gefaehrliche Richtung ist.
        return guardian_aus_zustand(ai_run_service.zustand_lesen(run) or {})
    except GuardianRahmenUnlesbar as exc:
        # Ein unlesbarer Rahmen ist kein Freibrief. Er heisst: dieser Vorschlag
        # stammt aus einem Lauf, dessen Bedingungen nicht mehr feststellbar sind
        # â€” und dann wird nicht ausgefuehrt.
        raise AiActionStateError("AI_BACKUP_UNVERIFIED") from exc

def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)

@dataclass(frozen=True)
class _AusfuehrungsRahmen:
    """Die gemeinsamen Groessen einer bestaetigten Ausfuehrung â€” ein Rahmen.

    Jede Ausfuehrungsfunktion bekommt denselben Rahmen und laesst liegen, was
    sie nicht braucht. Die Felder sind die festen Kopien aus
    `execute_proposal` (dort angelegt, damit die Fehlerbehandlung nach einem
    Rollback nicht auf ein abgelaufenes ORM-Objekt greifen muss) plus der
    handelnde Benutzer und der Guardian-Rahmen des Laufs. Die Session geht
    daneben als eigener Parameter mit: sie ist kein Wert des Vorschlags,
    sondern der Ort, an dem dieser Request arbeitet.
    """

    payload: dict
    server_id: int | None
    active_user: User
    correlation_id: str | None
    expected_revision: str | None
    row_id: str
    guardian: GuardianKontext | None
    tool_name: str

@dataclass(frozen=True)
class _Ausgefuehrt:
    """Das Ergebnis einer Ausfuehrung â€” vollstaendig per Konstruktion.

    In der frueheren elif-Kette waren `result`, `task_id` und `queued`
    nirgends vorinitialisiert; jeder Zweig musste alle drei setzen, und ein
    vergessenes Feld fiel erst beim Bestaetigen als `NameError` auf. Hier
    erzwingt der Konstruktor `result`, und die uebrigen Felder tragen die
    Werte, die fast alle Zweige meinen: nur der Lifecycle reiht ein
    (`queued`, `task_id`), und nur die Servererstellung liefert mit
    `neuer_server_id` einen frisch vergebenen Server zurueck.
    """

    result: dict
    task_id: str | None = None
    queued: bool = False
    neuer_server_id: int | None = None
