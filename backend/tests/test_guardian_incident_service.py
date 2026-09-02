from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session

from models import Incident, Server
from services.guardian_incident_service import ingest_incidents_and_ack


@pytest.fixture(autouse=True)
def _isolate_background_notification_worker(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
):
    """Hält Incident-Transaktionstests frei von StaticPool-Thread-Races.

    Die Benachrichtigung besitzt unten einen eigenen Integrationstest. Alle
    anderen Tests prüfen Ingestion/Grouping/ACK und dürfen deshalb keinen
    zweiten DB-Thread auf derselben In-Memory-SQLite-Verbindung starten.
    """
    if request.node.name != "test_notify_guardian_incident_triggers_webhook_and_email":
        monkeypatch.setattr(
            "services.guardian_incident_service._notify_guardian_incident",
            lambda *_args, **_kwargs: None,
        )
    yield


def _server() -> Server:
    return Server(
        id=42,
        name="TestSrv",
        game_type="minecraft",
        install_dir="/tmp/test",
        status="stopped",
        desired_power_state="running",
        desired_state_generation=1,
        guardian_observed_state="unknown",
        public_bind_ip="127.0.0.1",
    )


def test_idempotent_ingestion_duplicate_uuids(db: Session) -> None:
    server = _server()
    server.id = None
    db.add(server)
    db.commit()
    db.refresh(server)

    client = MagicMock()
    inc_uuid = str(uuid.uuid4())
    incidents = [
        {
            "uuid": inc_uuid,
            "server_id": server.id,
            "type": "process_not_running",
            "status": "open",
            "fingerprint": "process-error",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "payload": {
                "schema_version": 1,
                "message": "Process went offline",
                "attempts": [{"attempt_number": 1, "timestamp": "2026-07-20T12:00:00Z"}],
            },
        }
    ]

    # First ingestion
    ack = ingest_incidents_and_ack(db, server, client, "srv-42", incidents)
    assert len(ack) == 1
    assert ack[0] == inc_uuid

    db_inc = db.query(Incident).filter(Incident.uuid == inc_uuid).first()
    assert db_inc is not None
    assert db_inc.occurrences == 1
    assert len(json.loads(db_inc.attempts)) == 1

    # Second ingestion of the exact same UUID (simulate retry)
    ack2 = ingest_incidents_and_ack(db, server, client, "srv-42", incidents)
    assert len(ack2) == 1
    assert ack2[0] == inc_uuid

    db.refresh(db_inc)
    assert db_inc.occurrences == 1  # exact UUID duplicate does not increment occurrences
    assert len(json.loads(db_inc.attempts)) == 1


def test_fingerprint_grouping_consolidates_active_incidents(db: Session) -> None:
    server = _server()
    server.id = None
    db.add(server)
    db.commit()
    db.refresh(server)

    client = MagicMock()
    inc_uuid1 = str(uuid.uuid4())
    inc_uuid2 = str(uuid.uuid4())
    
    # First incident
    inc1 = {
        "uuid": inc_uuid1,
        "server_id": server.id,
        "type": "process_not_running",
        "status": "open",
        "fingerprint": "process-error",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "payload": {
            "schema_version": 1,
            "message": "Process went offline 1",
            "attempts": [{"attempt_number": 1, "started_at": "2026-07-20T12:00:00Z"}],
        },
    }
    
    # Second incident with different UUID but same fingerprint
    inc2 = {
        "uuid": inc_uuid2,
        "server_id": server.id,
        "type": "process_not_running",
        "status": "recovering",
        "fingerprint": "process-error",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "payload": {
            "schema_version": 1,
            "message": "Process went offline 2",
            "attempts": [{"attempt_number": 2, "started_at": "2026-07-20T12:05:00Z"}],
        },
    }

    ingest_incidents_and_ack(db, server, client, "srv-42", [inc1])
    ingest_incidents_and_ack(db, server, client, "srv-42", [inc2])

    db.expire_all()
    # Should only have one incident in DB for this fingerprint, occurrences = 2
    incidents_in_db = db.query(Incident).filter(Incident.server_id == server.id).all()
    assert len(incidents_in_db) == 1
    
    parent = incidents_in_db[0]
    assert parent.uuid == inc_uuid1  # kept parent UUID
    assert parent.occurrences == 2
    assert parent.status == "recovering"
    
    attempts = json.loads(parent.attempts)
    assert len(attempts) == 2
    assert attempts[0]["attempt_number"] == 1
    assert attempts[1]["attempt_number"] == 2


def test_incident_attempt_count_does_not_force_panel_quarantine(db: Session) -> None:
    server = _server()
    server.id = None
    db.add(server)
    db.commit()
    db.refresh(server)

    client = MagicMock()
    inc_uuid = str(uuid.uuid4())
    
    # Incident with 3 attempts but status recovering
    inc = {
        "uuid": inc_uuid,
        "server_id": server.id,
        "type": "process_not_running",
        "status": "recovering",
        "fingerprint": "process-error-quarantine",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "payload": {
            "schema_version": 1,
            "message": "Too many failures",
            "attempts": [
                {"attempt_number": 1, "started_at": "2026-07-20T12:00:00Z"},
                {"attempt_number": 2, "started_at": "2026-07-20T12:05:00Z"},
                {"attempt_number": 3, "started_at": "2026-07-20T12:10:00Z"},
            ],
        },
    }

    ingest_incidents_and_ack(db, server, client, "srv-42", [inc])

    db_inc = db.query(Incident).filter(Incident.uuid == inc_uuid).first()
    assert db_inc is not None
    # Backend no longer sets quarantine on its own
    assert db_inc.status == "recovering"
    
    db.refresh(server)
    # Server quarantine state shouldn't be touched by the backend
    assert server.guardian_quarantine_status != "quarantined"


def test_agent_quarantine_state_is_mirrored(db: Session) -> None:
    server = _server()
    server.id = None
    db.add(server)
    db.commit()
    db.refresh(server)

    client = MagicMock()
    inc_uuid = str(uuid.uuid4())
    
    # Incident where the agent explicitly sent status quarantined
    inc = {
        "uuid": inc_uuid,
        "server_id": server.id,
        "type": "process_not_running",
        "status": "quarantined",
        "fingerprint": "process-error-quarantine",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "payload": {
            "schema_version": 1,
            "message": "Too many failures",
            "attempts": [{"attempt_number": 1, "started_at": "2026-07-20T12:00:00Z"}],
        },
    }

    ingest_incidents_and_ack(db, server, client, "srv-42", [inc])

    db_inc = db.query(Incident).filter(Incident.uuid == inc_uuid).first()
    assert db_inc is not None
    assert db_inc.status == "quarantined"


def test_grouped_incident_uuid_retry_does_not_increment_occurrence(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Notification delivery ist ein separater Post-Commit-Pfad. Ein echter
    # Hintergrundthread darf in diesem StaticPool-SQLite-Test nicht dieselbe
    # Verbindung parallel verwenden, sonst kann er die Incident-Transaktion
    # nondeterministisch beeinflussen.
    monkeypatch.setattr(
        "services.guardian_incident_service._notify_guardian_incident",
        lambda *_args, **_kwargs: None,
    )
    server = _server()
    server.id = None
    db.add(server)
    db.commit()
    db.refresh(server)

    client = MagicMock()
    inc_uuid1 = str(uuid.uuid4())
    inc_uuid2 = str(uuid.uuid4())
    
    # First incident
    inc1 = {
        "uuid": inc_uuid1,
        "server_id": server.id,
        "type": "process_not_running",
        "status": "open",
        "fingerprint": "process-error",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "payload": {
            "schema_version": 1,
            "message": "Process went offline 1",
            "attempts": [{"attempt_number": 1, "started_at": "2026-07-20T12:00:00Z"}],
        },
    }
    
    # Second incident with different UUID but same fingerprint (grouping happens here)
    inc2 = {
        "uuid": inc_uuid2,
        "server_id": server.id,
        "type": "process_not_running",
        "status": "recovering",
        "fingerprint": "process-error",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "payload": {
            "schema_version": 1,
            "message": "Process went offline 2",
            "attempts": [{"attempt_number": 2, "started_at": "2026-07-20T12:05:00Z"}],
        },
    }

    ingest_incidents_and_ack(db, server, client, "srv-42", [inc1])
    ingest_incidents_and_ack(db, server, client, "srv-42", [inc2])

    db.expire_all()
    parent = db.query(Incident).filter(Incident.server_id == server.id).first()
    assert parent.occurrences == 2

    # Agent retries the second incident exactly as it was
    ingest_incidents_and_ack(db, server, client, "srv-42", [inc2])
    
    db.refresh(parent)
    # The occurrence should still be 2 because the delivery UUID (inc_uuid2) was already seen
    assert parent.occurrences == 2


def test_duplicate_incident_uuid_is_idempotent(db: Session) -> None:
    server = _server()
    server.id = None
    db.add(server)
    db.commit()
    db.refresh(server)

    client = MagicMock()
    inc_uuid = str(uuid.uuid4())
    inc = {
        "uuid": inc_uuid,
        "server_id": server.id,
        "type": "process_not_running",
        "status": "open",
        "fingerprint": "process-error-idempotency",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "payload": {
            "schema_version": 1,
            "message": "Process went offline",
            "attempts": [{"attempt_number": 1, "timestamp": "2026-07-20T12:00:00Z"}],
        },
    }

    ack1 = ingest_incidents_and_ack(db, server, client, "srv-42", [inc])
    assert ack1 == [inc_uuid]

    # Re-ingest the exact same UUID (retry delivery)
    ack2 = ingest_incidents_and_ack(db, server, client, "srv-42", [inc])
    assert ack2 == [inc_uuid]

    db_inc = db.query(Incident).filter(Incident.uuid == inc_uuid).one()
    assert db_inc.occurrences == 1
    assert len(json.loads(db_inc.attempts)) == 1


def test_ack_failure_preserves_delivery_record(db: Session) -> None:
    from models import GuardianIncidentDelivery
    server = _server()
    server.id = None
    db.add(server)
    db.commit()
    db.refresh(server)

    client = MagicMock()
    # Mock ACK to raise exception
    client.acknowledge_incidents.side_effect = RuntimeError("Network partition")

    inc_uuid = str(uuid.uuid4())
    inc = {
        "uuid": inc_uuid,
        "server_id": server.id,
        "type": "process_not_running",
        "status": "open",
        "fingerprint": "process-error",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "payload": {
            "schema_version": 1,
            "message": "Process went offline",
            "attempts": [{"attempt_number": 1, "timestamp": "2026-07-20T12:00:00Z"}],
        },
    }

    # ACK failure re-raises exception after local delivery commit
    with pytest.raises(RuntimeError, match="Network partition"):
        ingest_incidents_and_ack(db, server, client, "srv-42", [inc])

    delivery = db.query(GuardianIncidentDelivery).filter(GuardianIncidentDelivery.incident_uuid == inc_uuid).first()
    assert delivery is not None
    # Delivery record is preserved, even if network partition prevented ACK callback
    assert delivery.incident_uuid == inc_uuid


def test_notify_guardian_incident_triggers_webhook_and_email(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    from models import User, ServerPermission
    from services.guardian_incident_service import _notify_guardian_incident

    class ImmediateThread:
        """Führt den Worker deterministisch ohne parallelen SQLite-Zugriff aus."""

        def __init__(self, *, target, daemon: bool = False):
            self._target = target

        def start(self) -> None:
            self._target()

    server = _server()
    server.id = None
    db.add(server)
    db.commit()
    db.refresh(server)

    user1 = User(username="admin1", email="admin1@example.com", password_hash="hash", email_notifications=True)
    user2 = User(username="admin2", email="admin2@example.com", password_hash="hash", email_notifications=False)
    db.add_all([user1, user2])
    db.commit()
    db.refresh(user1)
    db.refresh(user2)

    perm1 = ServerPermission(server_id=server.id, user_id=user1.id, permission_key="server.view")
    perm2 = ServerPermission(server_id=server.id, user_id=user2.id, permission_key="server.view")
    db.add_all([perm1, perm2])
    db.commit()

    dispatched_events = []
    sent_emails = []

    async def mock_dispatch(db, *, server, event_type, payload):
        dispatched_events.append((server.id, event_type, payload))
        return [1]

    async def mock_send_email(to, username, server_name, incident_type, status, details=""):
        sent_emails.append((to, username, server_name, incident_type, status, details))
        return True

    monkeypatch.setattr("services.outbound_webhook_service.dispatch_event", mock_dispatch)
    monkeypatch.setattr("services.email_service.EmailService.is_configured", lambda: True)
    monkeypatch.setattr("services.email_service.EmailService.send_guardian_incident_notification", mock_send_email)
    monkeypatch.setattr(threading, "Thread", ImmediateThread)

    _notify_guardian_incident(server.id, "CrashLoop", "quarantined", "Process crashed 3 times")

    assert len(dispatched_events) == 1
    srv_id, evt_type, payload = dispatched_events[0]
    assert srv_id == server.id
    assert evt_type == "guardian_incident"
    assert payload["incident_type"] == "CrashLoop"
    assert payload["status"] == "quarantined"

    assert len(sent_emails) == 1
    to_email, uname, sname, inc_t, st, det = sent_emails[0]
    assert to_email == "admin1@example.com"
    assert uname == "admin1"
    assert sname == server.name
    assert inc_t == "CrashLoop"
    assert st == "quarantined"
    assert "crashed 3 times" in det


# ── Entdopplung der Versuchsliste: _attempt_key / _merge_attempts ──────
#
# Anlass ist ein Betriebsfall: der Entdopplungsschluessel suchte nach
# attempt_number / started_at / timestamp — Namen, die der Agent nirgends
# schreibt. Er schreibt attempt, stage, action, at, result
# (msm-agent/services/guardian_service.py, _attempt_recovery). Der Schluessel
# war damit IMMER None, kein Eintrag galt je als bekannt, und weil der Agent bei
# jedem Sync seine vollstaendige Liste mitschickt (_incident_payload), wuchs die
# gespeicherte Historie bei jedem Sync um ihre eigene Laenge. Genau diese Liste
# liest die KI, wenn sie einen Vorfall untersucht: sie sah hundert Versuche, wo
# drei stattgefunden hatten.
#
# Zwei Eigenschaften des Agenten bestimmen, wie richtig gemergt wird, und beide
# sind hier festgehalten: er schickt immer alles, und er aendert bestehende
# Eintraege nach (result "running" wird zu "executed" oder "failed", waehrend at
# gleich bleibt).

from services.guardian_incident_service import _attempt_key, _merge_attempts


def _versuch(
    nummer: int,
    at: str,
    *,
    stage: int = 0,
    action: str = "restart_container",
    result: str = "running",
) -> dict:
    """Ein Versuch in genau der Form, die der Agent schreibt.

    Wird diese Form im Agenten geaendert, muessen die Tests hier fehlschlagen —
    sie sind der einzige Ort, an dem Panel und Agent sich auf dieselben
    Feldnamen festlegen.
    """
    return {
        "attempt": nummer,
        "stage": stage,
        "action": action,
        "at": at,
        "result": result,
    }


def test_merge_attempts_wiederholter_sync_verlaengert_die_liste_nicht() -> None:
    """Der eigentliche Fehler: zweimal dieselbe Liste ist nicht die doppelte Liste.

    Der Agent schickt bei jedem Sync seine **vollstaendige** Versuchsliste mit.
    Solange kein Eintrag beschluesselt war, haengte jeder Sync die ganze Liste
    noch einmal an — nach einem Tag Betrieb stand jeder Versuch hundertfach in
    der Historie. Zehn Syncs sind hier keine Uebertreibung, sondern etwa zehn
    Minuten Laufzeit.
    """
    liste = [
        _versuch(1, "2026-08-12T10:00:00+00:00"),
        _versuch(2, "2026-08-12T10:05:00+00:00"),
    ]

    einmal = _merge_attempts(json.dumps(liste), liste)
    assert len(einmal) == 2

    gespeichert = json.dumps(liste)
    for _ in range(10):
        gespeichert = json.dumps(_merge_attempts(gespeichert, liste))

    nach_zehn_syncs = json.loads(gespeichert)
    assert len(nach_zehn_syncs) == 2
    assert [a["attempt"] for a in nach_zehn_syncs] == [1, 2]


def test_merge_attempts_neuer_eintrag_gewinnt_bei_gleichem_schluessel() -> None:
    """"running" ist ein Zwischenstand, kein Endstand.

    Der Agent legt den Versuch mit result "running" an und meldet ihn sofort;
    erst danach schreibt er "executed" oder "failed" in **denselben** Eintrag,
    at bleibt dabei unveraendert. Behielte der Merge den alten Eintrag, stuende
    in der Historie fuer immer ein Versuch, der nie zu Ende ging — die KI
    schloesse daraus auf einen haengenden Guardian statt auf eine
    fehlgeschlagene Wiederherstellung.
    """
    laufend = [_versuch(1, "2026-08-12T10:00:00+00:00", result="running")]
    abgeschlossen = [
        _versuch(1, "2026-08-12T10:00:00+00:00", result="failed") | {"details": "exit 1"}
    ]

    gemergt = _merge_attempts(json.dumps(laufend), abgeschlossen)

    assert len(gemergt) == 1
    assert gemergt[0]["result"] == "failed"
    assert gemergt[0]["details"] == "exit 1"


def test_merge_attempts_agent_neustart_behaelt_beide_versuche_nummer_eins() -> None:
    """Nach einem Neustart faengt der Agent wieder bei attempt 1 an.

    Die laufende Nummer allein ist deshalb keine Identitaet: der Zeitpunkt
    gehoert mit in den Schluessel. Ohne ihn ueberschriebe der erste Versuch nach
    dem Neustart den ersten Versuch davor, und die Vorgeschichte des Vorfalls
    verschwaende genau in dem Moment, in dem sie interessant wird.
    """
    vor_neustart = [_versuch(1, "2026-08-12T10:00:00+00:00", result="failed")]
    nach_neustart = [_versuch(1, "2026-08-12T11:30:00+00:00", result="running")]

    gemergt = _merge_attempts(json.dumps(vor_neustart), nach_neustart)

    assert len(gemergt) == 2
    assert [a["at"] for a in gemergt] == [
        "2026-08-12T10:00:00+00:00",
        "2026-08-12T11:30:00+00:00",
    ]


def test_merge_attempts_eintrag_ohne_schluessel_geht_nicht_verloren() -> None:
    """Unbekannte Formen werden mitgeschleppt, nicht stillschweigend verworfen.

    Ein Eintrag, aus dem sich weder Nummer noch Zeitpunkt lesen laesst, ist
    nicht entdoppelbar — aber er ist Betriebsgeschichte. Ihn wegzuwerfen hiesse,
    der KI eine Luecke zu zeigen, von der sie nichts weiss.
    """
    alt = [
        _versuch(1, "2026-08-12T10:00:00+00:00"),
        {"note": "manuell durch den Betreiber eingetragen"},
    ]
    neu = [_versuch(1, "2026-08-12T10:00:00+00:00", result="executed")]

    gemergt = _merge_attempts(json.dumps(alt), neu)

    assert {"note": "manuell durch den Betreiber eingetragen"} in gemergt
    # Der beschluesselte Versuch bleibt trotzdem entdoppelt.
    assert len([a for a in gemergt if a.get("attempt") == 1]) == 1


def test_merge_attempts_eintrag_ohne_schluessel_vervielfacht_sich_nicht() -> None:
    """Behalten heisst einmal behalten, nicht bei jedem Sync noch einmal.

    Der Panel-Code prueft an payload["attempts"] nur, dass es eine Liste ist —
    was darin steht, bestimmt der Agent. Weicht er von seiner heutigen Form ab
    (ein Eintrag ohne attempt und ohne at), landet dieser Eintrag im
    Rueckfallzweig ohne jede Entdopplung und wird bei jedem Sync erneut
    angehaengt. Nach hundert Syncs steht er hundertmal in incidents.attempts,
    und routers/incidents.py gibt die Liste ungekuerzt an den Browser weiter.
    Das ist genau der behobene Fehler, nur enger.
    """
    ohne_schluessel = [{"stage": 0, "action": "restart_container", "result": "failed"}]

    gespeichert = json.dumps(ohne_schluessel)
    for _ in range(5):
        gespeichert = json.dumps(_merge_attempts(gespeichert, ohne_schluessel))

    assert len(json.loads(gespeichert)) == 1


def test_merge_attempts_reihenfolge_bleibt_die_des_ersten_auftretens() -> None:
    """Die Historie ist chronologisch, auch wenn ein Eintrag spaeter ersetzt wird.

    Ein nachtraeglich aktualisierter Versuch darf nicht ans Ende rutschen: sonst
    stuende der aelteste Versuch hinter dem juengsten, sobald sein Ergebnis
    eintrifft. Die neue Liste ist hier absichtlich anders sortiert als die
    gespeicherte, damit die Reihenfolge nachweislich aus dem ersten Auftreten
    kommt und nicht aus der Reihenfolge des letzten Syncs.
    """
    gespeichert = [
        _versuch(1, "2026-08-12T10:00:00+00:00", result="running"),
        _versuch(2, "2026-08-12T10:05:00+00:00", result="running"),
        _versuch(3, "2026-08-12T10:10:00+00:00", result="running"),
    ]
    neu = [
        _versuch(3, "2026-08-12T10:10:00+00:00", result="running"),
        _versuch(1, "2026-08-12T10:00:00+00:00", result="failed"),
        _versuch(2, "2026-08-12T10:05:00+00:00", result="executed"),
    ]

    gemergt = _merge_attempts(json.dumps(gespeichert), neu)

    assert [a["attempt"] for a in gemergt] == [1, 2, 3]
    assert [a["result"] for a in gemergt] == ["failed", "executed", "running"]


@pytest.mark.parametrize(
    "kaputt",
    [
        "{das ist kein json",
        '{"attempts": []}',  # gueltiges JSON, aber keine Liste
        '"nur ein string"',
        "",
        None,
    ],
)
def test_merge_attempts_unlesbare_spalte_stuerzt_nicht_ab(kaputt) -> None:
    """Eine kaputte Spalte kostet Historie, nicht den ganzen Sync.

    In attempts steht ein JSON-Text; abgeschnittene oder von Hand veraenderte
    Werte sind moeglich. Wuerde der Merge daran scheitern, brechen Ingestion und
    ACK fuer diesen Server ab, und der Agent liefert denselben Vorfall endlos
    erneut. Der aktuelle Sync ist wichtiger als die alte Zeile: es wird mit
    leerer Ausgangsliste weitergemacht.
    """
    neu = [_versuch(1, "2026-08-12T10:00:00+00:00")]

    gemergt = _merge_attempts(kaputt, neu)

    assert gemergt == neu


def test_attempt_key_kennt_die_alten_spaltennamen_weiterhin() -> None:
    """Bereits gespeicherte Zeilen aus der Zeit davor bleiben beschluesselt.

    In der Datenbank stehen Versuchslisten, die noch mit attempt_number /
    started_at / timestamp geschrieben wurden. Faellt der Rueckfall weg, gelten
    sie schlagartig als schluessellos und wachsen ab dem naechsten Sync genau so
    unbegrenzt weiter, wie es der behobene Fehler tat.
    """
    assert _attempt_key({"attempt_number": 1, "started_at": "2026-07-20T12:00:00Z"}) is not None
    assert _attempt_key({"attempt_number": 1, "timestamp": "2026-07-20T12:00:00Z"}) is not None
    assert _attempt_key(_versuch(1, "2026-08-12T10:00:00+00:00")) is not None
    # Ohne Nummer und ohne Zeitpunkt gibt es nichts zu entdoppeln.
    assert _attempt_key({"result": "failed", "action": "restart_container"}) is None

    alt_format = [
        {"attempt_number": 1, "started_at": "2026-07-20T12:00:00Z"},
        {"attempt_number": 2, "started_at": "2026-07-20T12:05:00Z"},
    ]
    assert len(_merge_attempts(json.dumps(alt_format), alt_format)) == 2


def test_ingest_haelt_die_versuchshistorie_ueber_viele_syncs_kurz(db: Session) -> None:
    """Derselbe Fehler noch einmal, aber auf dem Weg, den der Agent wirklich geht.

    Vier Syncs desselben Vorfalls, wie sie im Betrieb anfallen: Versuch 1 laeuft,
    Versuch 1 ist durch, Versuch 2 laeuft, Versuch 2 ist fehlgeschlagen. Danach
    stehen zwei Versuche in der Historie mit ihrem jeweils **letzten** Ergebnis.
    Vor der Behebung waren es sechs, darunter zweimal ein "running", das laengst
    vorbei war.
    """
    server = _server()
    server.id = None
    db.add(server)
    db.commit()
    db.refresh(server)

    client = MagicMock()
    inc_uuid = str(uuid.uuid4())

    def _sync(status: str, attempts: list[dict]) -> None:
        ingest_incidents_and_ack(
            db,
            server,
            client,
            "srv-42",
            [
                {
                    "uuid": inc_uuid,
                    "server_id": server.id,
                    "type": "process_not_running",
                    "status": status,
                    "fingerprint": f"guardian:{server.id}:process_not_running",
                    "created_at": "2026-08-12T09:59:00+00:00",
                    "payload": {
                        "schema_version": 1,
                        "message": "Guardian detected an unhealthy server state",
                        "attempts": attempts,
                    },
                }
            ],
        )

    versuch1_laeuft = _versuch(1, "2026-08-12T10:00:00+00:00", result="running")
    versuch1_fertig = _versuch(1, "2026-08-12T10:00:00+00:00", result="executed")
    versuch2_laeuft = _versuch(2, "2026-08-12T10:05:00+00:00", stage=1, result="running")
    versuch2_fehlt = _versuch(2, "2026-08-12T10:05:00+00:00", stage=1, result="failed")

    _sync("recovering", [versuch1_laeuft])
    _sync("verifying", [versuch1_fertig])
    _sync("recovering", [versuch1_fertig, versuch2_laeuft])
    _sync("open", [versuch1_fertig, versuch2_fehlt])

    db.expire_all()
    db_inc = db.query(Incident).filter(Incident.uuid == inc_uuid).one()
    historie = json.loads(db_inc.attempts)

    assert len(historie) == 2
    assert [a["attempt"] for a in historie] == [1, 2]
    assert [a["result"] for a in historie] == ["executed", "failed"]
