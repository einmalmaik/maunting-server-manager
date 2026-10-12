"""Vorfallmeldungen — die Abfrage, die für jeden Nicht-Betreiber abstürzte.

`incident_alerts` fragte die Rollen über `user.user_roles` ab. Diese Beziehung
gibt es am Model nicht; sie heisst `role_assignments`. Weil Python bei
`user.is_owner or any(...)` abkürzt, traf der `AttributeError` nur die
Nicht-Betreiber — also genau die Benutzer, die ihre Serverliste aus den
delegierten Rechten beziehen. Der Endpunkt antwortete ihnen mit HTTP 500,
und kein Test sah hin.

Die Tests hier halten drei Zusagen fest: die Abfrage trägt für jede Rolle, sie
zeigt niemandem einen fremden Server, und ein Admin sieht alles — auch wenn
seine Rolle noch an der Legacy-Spalte `users.role_id` hängt.
"""

from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

from models import Incident, Role, Server, ServerPermission, User, UserRole


def _vorfall(
    db: Session,
    server: Server,
    titel: str,
    *,
    incident_type: str = "container_down",
) -> Incident:
    incident = Incident(
        server_id=server.id,
        title=titel,
        description="Ausgedachter Vorfall für den Test.",
        type=incident_type,
        status="open",
        fingerprint=f"fp-{titel}",
    )
    db.add(incident)
    db.commit()
    db.refresh(incident)
    return incident


def _zweiter_server(db: Session) -> Server:
    server = Server(
        name="Fremder Server",
        game_type="dayz",
        install_dir="/tmp/fremd",
        container_name="msm-srv-fremd",
        status="stopped",
    )
    db.add(server)
    db.commit()
    db.refresh(server)
    return server


def test_nutzer_ohne_besondere_rolle_bekommt_eine_antwort_statt_500(
    db: Session,
    client: TestClient,
    regular_user: User,
    test_server: Server,
    user_cookies: dict,
):
    """Der eigentliche Regressionstest: kein AttributeError mehr."""
    db.add(
        ServerPermission(
            user_id=regular_user.id,
            server_id=test_server.id,
            permission_key="server.view",
        )
    )
    db.commit()
    _vorfall(db, test_server, "Container gestoppt")

    antwort = client.get("/api/system/incident-alerts", cookies=user_cookies)

    assert antwort.status_code == 200
    meldungen = antwort.json()
    assert [m["title"] for m in meldungen] == ["Container gestoppt"]
    assert meldungen[0]["server_name"] == test_server.name


def test_ohne_recht_auf_den_server_kommt_nichts_zurueck(
    db: Session,
    client: TestClient,
    regular_user: User,
    test_server: Server,
    user_cookies: dict,
):
    """Ein Vorfall auf einem fremden Server bleibt fremd."""
    fremd = _zweiter_server(db)
    db.add(
        ServerPermission(
            user_id=regular_user.id,
            server_id=test_server.id,
            permission_key="server.view",
        )
    )
    db.commit()
    _vorfall(db, fremd, "Geht dich nichts an")

    antwort = client.get("/api/system/incident-alerts", cookies=user_cookies)

    assert antwort.status_code == 200
    assert antwort.json() == []


def test_admin_sieht_auch_server_ohne_eigene_delegation(
    db: Session,
    client: TestClient,
    regular_user: User,
    test_server: Server,
    user_cookies: dict,
):
    """Die Admin-Rolle ersetzt die Einzeldelegation — über beide Wege."""
    admin = db.query(Role).filter(Role.name == "admin").first()
    if admin is None:
        admin = Role(name="admin", description=None, is_system=True)
        db.add(admin)
        db.flush()
    db.add(UserRole(user_id=regular_user.id, role_id=admin.id))
    db.commit()
    _vorfall(db, test_server, "Sichtbar für Admins")

    antwort = client.get("/api/system/incident-alerts", cookies=user_cookies)

    assert antwort.status_code == 200
    assert [m["title"] for m in antwort.json()] == ["Sichtbar für Admins"]


def test_admin_ueber_die_legacy_primaerrolle_zaehlt_auch(
    db: Session,
    client: TestClient,
    regular_user: User,
    test_server: Server,
    user_cookies: dict,
):
    """`users.role_id` ist der ältere Weg und trägt dieselbe Aussage.

    Die kaputte Fassung hätte ihn selbst nach dem Umbenennen der Beziehung
    übersehen: sie las ausschliesslich die Zuweisungstabelle.
    """
    admin = db.query(Role).filter(Role.name == "admin").first()
    if admin is None:
        admin = Role(name="admin", description=None, is_system=True)
        db.add(admin)
        db.flush()
    regular_user.role_id = admin.id
    db.commit()
    _vorfall(db, test_server, "Auch über die Primärrolle")

    antwort = client.get("/api/system/incident-alerts", cookies=user_cookies)

    assert antwort.status_code == 200
    assert [m["title"] for m in antwort.json()] == ["Auch über die Primärrolle"]


def test_abgeschaltete_geraetemeldungen_bleiben_still(
    db: Session,
    client: TestClient,
    regular_user: User,
    test_server: Server,
    user_cookies: dict,
):
    """Wer keine Gerätemeldungen will, bekommt auch keine Vorfälle."""
    db.add(
        ServerPermission(
            user_id=regular_user.id,
            server_id=test_server.id,
            permission_key="server.view",
        )
    )
    regular_user.device_notifications = False
    db.commit()
    _vorfall(db, test_server, "Bleibt ungesagt")

    antwort = client.get("/api/system/incident-alerts", cookies=user_cookies)

    assert antwort.status_code == 200
    assert antwort.json() == []


def test_process_not_running_auf_gestopptem_server_wird_unterdrueckt(
    db: Session,
    client: TestClient,
    regular_user: User,
    test_server: Server,
    user_cookies: dict,
):
    """process_not_running auf einem gestoppten Server ist kein Fehlalarm."""
    db.add(
        ServerPermission(
            user_id=regular_user.id,
            server_id=test_server.id,
            permission_key="server.view",
        )
    )
    test_server.status = "stopped"
    test_server.desired_power_state = "stopped"
    db.commit()
    _vorfall(db, test_server, "Autopilot: process_not_running", incident_type="process_not_running")

    antwort = client.get("/api/system/incident-alerts", cookies=user_cookies)

    assert antwort.status_code == 200
    assert antwort.json() == []


def test_veraltete_vorfaelle_aelter_als_24h_werden_nicht_gemeldet(
    db: Session,
    client: TestClient,
    regular_user: User,
    test_server: Server,
    user_cookies: dict,
):
    """Vorfälle älter als 24 Stunden poppen nicht mehr als Alert auf."""
    db.add(
        ServerPermission(
            user_id=regular_user.id,
            server_id=test_server.id,
            permission_key="server.view",
        )
    )
    test_server.status = "running"
    test_server.desired_power_state = "running"
    db.commit()
    vorfall = _vorfall(db, test_server, "Alter Vorfall", incident_type="custom_error")
    vorfall.created_at = datetime.now(timezone.utc) - timedelta(hours=26)
    db.commit()

    antwort = client.get("/api/system/incident-alerts", cookies=user_cookies)

    assert antwort.status_code == 200
    assert antwort.json() == []


def test_auto_resolve_stopped_server_incidents_schliesst_offene_vorfaelle(
    db: Session,
    test_server: Server,
):
    """Beim Stoppen werden process_not_running und ähnliche Vorfälle auf resolved gesetzt."""
    from services.server_lifecycle_service import _auto_resolve_stopped_server_incidents

    inc = _vorfall(db, test_server, "Autopilot: process_not_running", incident_type="process_not_running")
    test_server.guardian_active_incident_uuid = inc.uuid
    db.commit()

    _auto_resolve_stopped_server_incidents(db, test_server)
    db.commit()

    db.refresh(inc)
    assert inc.status == "resolved"
    assert inc.resolved_at is not None
    assert test_server.guardian_active_incident_uuid is None

