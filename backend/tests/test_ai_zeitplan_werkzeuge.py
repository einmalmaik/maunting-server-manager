"""Der Durchgriff auf die eingebauten Zeitpläne — und wer am Ende gewinnt.

`propose_restart_schedule_set` und `propose_backup_schedule_set` stellen genau
die Felder, die der Benutzer im Panel sieht (Auto-Neustart, Auto-Backup). Vier
Zusagen, jede mit eigenem Test:

* Das Werkzeug nimmt dieselben Grenzen wie der Panel-Endpunkt — nachsichtig
  gelesen, streng gespeichert.
* Die Ausführung geht denselben Weg wie das Panel: Normalisierung,
  Scheduler-Sync, und der Server trägt danach „Von der KI verwaltet".
* Kommt der Vorschlag aus einem stehenden Auftrag, wird der Server mit ihm
  verknüpft — und eine **manuelle** Änderung deaktiviert genau diesen Auftrag.
* Das Löschen einer Aufgabe räumt ihre Verweise an den Servern auf.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import AiConversation, AiTask, Server, User
from services import ai_proposal_service, ai_task_service, scheduler_service
from services.ai_action_errors import AiActionValidationError
from services.ai_proposal_service import AufgabenKontext
from services.dis_client import DisClient


def _server(db: Session, tmp_path: Path, name: str = "zeitplan") -> Server:
    install_dir = tmp_path / name
    install_dir.mkdir(exist_ok=True)
    row = Server(
        name=f"Zeitplan {name}",
        game_type="dayz",
        install_dir=str(install_dir),
        container_name=f"msm-zp-{uuid4().hex[:8]}",
        status="stopped",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _conversation(db: Session, user: User, server: Server) -> AiConversation:
    row = AiConversation(
        id=str(uuid4()), user_id=user.id, server_id=server.id, title="Zeitplan"
    )
    db.add(row)
    db.flush()
    return row


def _vorschlagen(db, user, conversation, server, tool, argumente, *, aufgabe=None):
    return ai_proposal_service.create_proposal(
        db,
        user=user,
        conversation=conversation,
        tool_name=tool,
        arguments={
            "server_id": server.id,
            "reason": "Testbegruendung",
            "expected_effect": "Testwirkung",
            **argumente,
        },
        correlation_id=str(uuid4()),
        aufgabe=aufgabe,
    )


def _ausfuehren(db, user, proposal) -> dict:
    db.commit()
    _, token = ai_proposal_service.confirm_proposal(
        db, proposal_id=proposal.id, user=user
    )
    return ai_proposal_service.execute_proposal(
        db, proposal_id=proposal.id, user=user, confirmation_token=token
    )


def _payload(proposal) -> dict:
    return json.loads(DisClient.decrypt(
        proposal.payload_encrypted, aad=f"msm:ai:action-proposal:v1:{proposal.id}"
    ))


def _job_ids() -> set[str]:
    return {job.id for job in scheduler_service.get_scheduler().get_jobs()}


# ── Auto-Neustart ─────────────────────────────────────────────────────────


def test_der_neustart_zeitplan_geht_denselben_weg_wie_das_panel(
    db: Session, owner_user: User, tmp_path: Path
) -> None:
    server = _server(db, tmp_path, "intervall")
    conversation = _conversation(db, owner_user, server)

    proposal = _vorschlagen(
        db, owner_user, conversation, server,
        "propose_restart_schedule_set", {"enabled": True, "interval_hours": 8},
    )
    _ausfuehren(db, owner_user, proposal)
    db.refresh(server)

    assert server.auto_restart is True
    assert server.restart_interval_hours == 8
    # Normalisierung wie am Panel: Intervall und feste Zeiten schliessen sich aus.
    assert server.restart_times_utc is None and server.restart_time_utc is None
    assert server.restart_ai_managed is True
    assert server.restart_ai_task_id is None
    # Der Scheduler ist sofort synchron — nicht erst nach einem Neustart.
    assert f"restart_server_{server.id}" in _job_ids()


def test_feste_zeiten_werden_nachsichtig_gelesen_und_streng_gespeichert(
    db: Session, owner_user: User, tmp_path: Path
) -> None:
    server = _server(db, tmp_path, "zeiten")
    conversation = _conversation(db, owner_user, server)

    proposal = _vorschlagen(
        db, owner_user, conversation, server,
        "propose_restart_schedule_set",
        {"enabled": True, "times": ["8:00", "20:30", "08:00"]},
    )
    _ausfuehren(db, owner_user, proposal)
    db.refresh(server)

    # "8:00" wird zu "08:00", das Duplikat fällt weg, der Legacy-Spiegel
    # bekommt die erste Zeit — exakt wie beim Speichern über das Panel.
    assert server.restart_times_utc == "08:00,20:30"
    assert server.restart_time_utc == "08:00"
    assert server.restart_interval_hours is None
    assert f"restart_cron_server_{server.id}_0800" in _job_ids()


def test_der_neustart_zeitplan_weist_widersprueche_ab(
    db: Session, owner_user: User, tmp_path: Path
) -> None:
    server = _server(db, tmp_path, "widerspruch")
    conversation = _conversation(db, owner_user, server)

    with pytest.raises(AiActionValidationError, match="genau eines"):
        _vorschlagen(
            db, owner_user, conversation, server,
            "propose_restart_schedule_set",
            {"enabled": True, "interval_hours": 8, "times": ["08:00"]},
        )
    db.rollback()
    with pytest.raises(AiActionValidationError, match="ohne Planangaben"):
        _vorschlagen(
            db, owner_user, conversation, server,
            "propose_restart_schedule_set",
            {"enabled": False, "interval_hours": 8},
        )
    db.rollback()
    with pytest.raises(AiActionValidationError, match="12"):
        _vorschlagen(
            db, owner_user, conversation, server,
            "propose_restart_schedule_set",
            {"enabled": True, "times": [f"{stunde:02d}:00" for stunde in range(13)]},
        )
    db.rollback()


def test_ausschalten_laesst_den_plan_stehen(
    db: Session, owner_user: User, tmp_path: Path
) -> None:
    """Wiedereinschalten soll den Plan erinnern — wie der Panel-Schalter."""
    server = _server(db, tmp_path, "aus")
    server.auto_restart = True
    server.restart_interval_hours = 8
    db.commit()
    conversation = _conversation(db, owner_user, server)

    proposal = _vorschlagen(
        db, owner_user, conversation, server,
        "propose_restart_schedule_set", {"enabled": False},
    )
    _ausfuehren(db, owner_user, proposal)
    db.refresh(server)

    assert server.auto_restart is False
    assert server.restart_interval_hours == 8
    assert f"restart_server_{server.id}" not in _job_ids()


# ── Auto-Backup ───────────────────────────────────────────────────────────


def test_der_backup_zeitplan_ist_ein_nachtrag_und_synct_den_scheduler(
    db: Session, owner_user: User, tmp_path: Path
) -> None:
    server = _server(db, tmp_path, "backup")
    server.backup_on_start = True
    db.commit()
    conversation = _conversation(db, owner_user, server)

    proposal = _vorschlagen(
        db, owner_user, conversation, server,
        "propose_backup_schedule_set", {"interval_hours": 168, "retention_count": 10},
    )
    _ausfuehren(db, owner_user, proposal)
    db.refresh(server)

    assert server.backup_interval_hours == 168
    assert server.backup_retention_count == 10
    # Nicht genannt, nicht angefasst: der Vor-Start-Schalter bleibt an.
    assert server.backup_on_start is True
    assert server.backup_ai_managed is True
    assert f"backup_server_{server.id}" in _job_ids()

    # Intervall 0 schaltet ab und räumt den Job weg.
    zweiter = _vorschlagen(
        db, owner_user, conversation, server,
        "propose_backup_schedule_set", {"interval_hours": 0},
    )
    _ausfuehren(db, owner_user, zweiter)
    db.refresh(server)
    assert server.backup_interval_hours is None
    assert f"backup_server_{server.id}" not in _job_ids()


def test_der_backup_zeitplan_kennt_die_panel_grenzen(
    db: Session, owner_user: User, tmp_path: Path
) -> None:
    server = _server(db, tmp_path, "grenzen")
    conversation = _conversation(db, owner_user, server)

    with pytest.raises(AiActionValidationError, match="720"):
        _vorschlagen(
            db, owner_user, conversation, server,
            "propose_backup_schedule_set", {"interval_hours": 721},
        )
    db.rollback()
    with pytest.raises(AiActionValidationError, match="100"):
        _vorschlagen(
            db, owner_user, conversation, server,
            "propose_backup_schedule_set", {"retention_count": 101},
        )
    db.rollback()
    with pytest.raises(AiActionValidationError, match="backup_on_start"):
        _vorschlagen(
            db, owner_user, conversation, server,
            "propose_backup_schedule_set", {},
        )
    db.rollback()


# ── Die Verknüpfung mit dem stehenden Auftrag ─────────────────────────────


def _aufgabe(db: Session, user: User, titel: str = "Nachtplan") -> AiTask:
    aufgabe = ai_task_service.anlegen(db, user=user, felder={
        "title": titel,
        "instruction": "Halte die Zeitpläne aktuell.",
        "kind": "report",
        "plan_kind": "daily",
        "time_of_day": "03:00",
        "timezone": "Europe/Berlin",
    })
    db.commit()
    db.refresh(aufgabe)
    return aufgabe


def test_ein_vorschlag_aus_einem_auftrag_verknuepft_den_server(
    db: Session, owner_user: User, tmp_path: Path
) -> None:
    server = _server(db, tmp_path, "verknuepft")
    conversation = _conversation(db, owner_user, server)
    aufgabe = _aufgabe(db, owner_user)
    kontext = AufgabenKontext(
        task_id=aufgabe.id, kind="act", channel="chat", title=aufgabe.title
    )

    proposal = _vorschlagen(
        db, owner_user, conversation, server,
        "propose_restart_schedule_set", {"enabled": True, "interval_hours": 12},
        aufgabe=kontext,
    )
    assert _payload(proposal)["ai_task_id"] == aufgabe.id
    _ausfuehren(db, owner_user, proposal)
    db.refresh(server)

    assert server.restart_ai_managed is True
    assert server.restart_ai_task_id == aufgabe.id


def test_die_manuelle_aenderung_gewinnt_und_deaktiviert_den_auftrag(
    db: Session, owner_user: User, tmp_path: Path
) -> None:
    server = _server(db, tmp_path, "manuell")
    aufgabe = _aufgabe(db, owner_user)
    server.restart_ai_managed = True
    server.restart_ai_task_id = aufgabe.id
    db.commit()

    ai_task_service.ki_zeitplan_verwaltung_aufheben(db, server, bereich="restart")
    db.commit()
    db.refresh(server)
    db.refresh(aufgabe)

    assert server.restart_ai_managed is False
    assert server.restart_ai_task_id is None
    # Deaktiviert, nicht gelöscht: in der Aufgabenliste sichtbar und wieder
    # einschaltbar.
    assert aufgabe.enabled is False
    assert aufgabe.next_run_at is None


def test_das_loeschen_einer_aufgabe_raeumt_die_serververweise_auf(
    db: Session, owner_user: User, tmp_path: Path
) -> None:
    server = _server(db, tmp_path, "aufraeumen")
    aufgabe = _aufgabe(db, owner_user)
    server.backup_ai_managed = True
    server.backup_ai_task_id = aufgabe.id
    db.commit()

    ai_task_service.loeschen(db, user=owner_user, task_id=aufgabe.id)
    db.commit()
    db.refresh(server)

    assert server.backup_ai_task_id is None
    # Das Abzeichen bleibt: der Zeitplan kam weiterhin von der KI.
    assert server.backup_ai_managed is True


# ── Die Panel-Endpunkte ───────────────────────────────────────────────────


def _csrf(cookies: dict) -> dict[str, str]:
    return {"X-CSRF-Token": cookies.get("__Secure-csrf_token", "")}


def test_der_backup_patch_synct_den_scheduler_und_nimmt_die_verwaltung_zurueck(
    client: TestClient, db: Session, owner_user: User, owner_cookies: dict,
    tmp_path: Path,
) -> None:
    """Der frühere Fehler: eine Intervall-Änderung wirkte erst nach einem
    Backend-Neustart, weil der PATCH den Scheduler nie anfasste."""
    server = _server(db, tmp_path, "patch")
    aufgabe = _aufgabe(db, owner_user)
    server.backup_ai_managed = True
    server.backup_ai_task_id = aufgabe.id
    db.commit()

    antwort = client.patch(
        f"/api/backups/{server.id}/settings",
        json={"backup_interval_hours": 336},
        cookies=owner_cookies, headers=_csrf(owner_cookies),
    )
    assert antwort.status_code == 200, antwort.text
    db.refresh(server)
    db.refresh(aufgabe)

    assert server.backup_interval_hours == 336
    assert f"backup_server_{server.id}" in _job_ids()
    assert server.backup_ai_managed is False
    assert server.backup_ai_task_id is None
    assert aufgabe.enabled is False

    # Die neuen Grenzen gelten auch hier: mehr als 30 Tage gibt es nicht.
    abgelehnt = client.patch(
        f"/api/backups/{server.id}/settings",
        json={"backup_interval_hours": 9999},
        cookies=owner_cookies, headers=_csrf(owner_cookies),
    )
    assert abgelehnt.status_code == 422

    einstellungen = client.get(
        f"/api/backups/{server.id}/settings", cookies=owner_cookies
    )
    assert einstellungen.status_code == 200
    daten = einstellungen.json()
    assert daten["backup_ai_managed"] is False
    assert daten["next_auto_backup_at"] is not None


def test_der_server_patch_nimmt_die_neustart_verwaltung_zurueck(
    client: TestClient, db: Session, owner_user: User, owner_cookies: dict,
    tmp_path: Path,
) -> None:
    server = _server(db, tmp_path, "serverpatch")
    aufgabe = _aufgabe(db, owner_user)
    server.restart_ai_managed = True
    server.restart_ai_task_id = aufgabe.id
    db.commit()

    antwort = client.patch(
        f"/api/servers/{server.id}",
        json={"auto_restart": True, "restart_interval_hours": 6},
        cookies=owner_cookies, headers=_csrf(owner_cookies),
    )
    assert antwort.status_code == 200, antwort.text
    db.refresh(server)
    db.refresh(aufgabe)

    assert server.restart_ai_managed is False
    assert server.restart_ai_task_id is None
    assert aufgabe.enabled is False

    # Ein Umbenennen ist keine Zeitplan-Entscheidung und fasst nichts an.
    server.restart_ai_managed = True
    db.commit()
    umbenannt = client.patch(
        f"/api/servers/{server.id}",
        json={"name": "Nur ein neuer Name"},
        cookies=owner_cookies, headers=_csrf(owner_cookies),
    )
    assert umbenannt.status_code == 200, umbenannt.text
    db.refresh(server)
    assert server.restart_ai_managed is True
