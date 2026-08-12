"""Die Backup-Schranke des Heilungslaufs: erst der Nachweis, dann der Eingriff.

Das ist die eigentliche Sicherheitszusage der Guardian-Kopplung. Ein
Heilungslauf beginnt nicht mit der Bitte eines Menschen, sondern mit einem
Ereignis auf einem Server, auf dem Fremde spielen — und was das Modell dort
liest, kann jemand geschrieben haben, der genau darauf hofft. Niemand sitzt
davor, niemand klickt etwas weg. Der Weg zurueck muss deshalb **vor** dem
Eingriff hergestellt sein und nicht danach gehofft werden.

Die Zusage lautet in drei Teilen, und alle drei stehen hier als eigene Tests:

* Es genuegt nicht, dass eine Backup-Zeile existiert. Der Remote-Agent-Pfad legt
  die Zeile an, **bevor** der Agent gearbeitet hat; sie beweist damit nur, dass
  jemand ein Backup angefangen hat. `verified_at` traegt die Nachmessung, und
  `size_mb` taugt als Ersatz nicht: es ist ``bytes // (1024*1024)`` und damit
  **0** fuer jedes Archiv unter einem Megabyte — ein frischer Server hat genau
  so eines.
* Ein Backup, das aelter ist als der Vorfall, zaehlt nicht. Es liegt vor der
  Stoerung und sagt nichts ueber den Zustand, den die KI gleich anfasst.
* Die Schranke gilt **nur** im Heilungslauf. Im gewoehnlichen Chat entscheidet
  weiterhin der Mensch mit seinem Klick; ihn zum Backup zu zwingen waere eine
  Verschaerfung, um die niemand gebeten hat.

Geprueft wird durchgehend `AiActionStateError.code`, nie der Meldungstext: der
Code ist Teil der Schnittstelle zum Modell und zur Oberflaeche, der Text ist es
nicht.

Der zweite Teil der Datei (ab "Die Schranke vor der Ausfuehrung") prueft den
Weg, den der erste Teil offen liess. Bis dahin belegte hier alles nur
`create_proposal` — und genau das war die Luecke: zwischen dem Anlegen eines
Vorschlags und seiner Ausfuehrung liegt ein Commit und ein Zeitfenster ohne
Obergrenze. Ein Vorschlag im Status 'proposed' altert nicht, `cleanup_old_backups`
raeumt nach `backup_retention_count` aber auch die verifizierte Zeile ab, auf die
sich die erste Pruefung gestuetzt hat, und der Betreiber kann das Archiv von Hand
loeschen. Die Registry sagt zu, der Nachweis werde "beim Anlegen **und** vor der
Ausfuehrung" geprueft; `_verlangt_gesichertes_backup` hatte genau einen Aufrufer.
Die Zusage war damit eine Behauptung, und ein Klick auf "Bestaetigen" loeschte
die Datei ohne den Rueckweg, mit dem der Vorschlag ueberhaupt entstehen durfte.

Dazu der Zeitanker. Der Nachweis hing an `Incident.created_at` — dem **ersten**
Auftreten, das die Gruppierung nie auffrischt und das ungeprueft vom Agenten auf
der Node stammt. Ein Vorfall, der seit Tagen offen steht, liess damit ein
tagealtes Nachtbackup als Nachweis gelten: die Schranke formal erfuellt, der
Rollback auf einem Stand von vorgestern. Der ehrliche Anker ist der Beginn des
Heilungslaufs (`zustand["guardian"]["backup_anker"]`) — was juenger ist als er,
kann nur waehrend dieser Heilung entstanden sein.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from models import (
    AiActionProposal,
    AiConversation,
    AiRun,
    Backup,
    Incident,
    Role,
    RolePermission,
    Server,
    ServerPermission,
    User,
)
from services import ai_guardian_service, ai_proposal_service, ai_run_service
from services.ai_action_errors import AiActionStateError, AiActionValidationError
from services.ai_proposal_service import GuardianKontext
from services.ai_stream_service import guardian_aus_zustand
from services.ai_tool_registry import (
    GUARDIAN_BACKUP_PFLICHT_TOOLS,
    GUARDIAN_HEILUNG_TOOLS,
)
from services.file_edit_service import content_revision
from services.role_service import set_user_roles


#: Alle Serverrechte, die die Werkzeuge der Pflichtmenge zusammen verlangen.
#:
#: Bewusst vollstaendig: geprueft werden soll hier die Backup-Schranke und nicht
#: eine fehlende Berechtigung. Ein Aufruf, der schon an `_require_tool_permission`
#: scheitert, belegt ueber den Nachweis gar nichts — er kommt nie bis dorthin.
SERVERRECHTE = (
    "server.view",
    "server.files.read",
    "server.files.write",
    "server.config.write",
    "server.mods.write",
    "server.network.manage",
    "server.backups.create",
)

#: Die Adresse, die der Host in diesen Tests zu haben vorgibt. `propose_bind_ip_update`
#: prueft gegen die echten Schnittstellen; ohne diesen festen Wert entschiede die
#: Netzkonfiguration des Testrechners darueber, ob die Schranke geprueft wird.
HOST_ADRESSE = "192.168.1.50"


@pytest.fixture
def host_schnittstellen(monkeypatch: pytest.MonkeyPatch) -> str:
    class _Schnittstelle:
        ip = HOST_ADRESSE

    monkeypatch.setattr(
        "services.network_interfaces_service.list_host_interfaces",
        lambda: [_Schnittstelle()],
    )
    return HOST_ADRESSE


def _aufbau(
    db: Session,
    user: User,
    tmp_path: Path,
    *,
    serverrechte: tuple[str, ...] = SERVERRECHTE,
) -> tuple[Server, AiConversation, Incident]:
    """Server, Unterhaltung und ein offener Vorfall — der Rahmen einer Heilung.

    Der Vorfall ist eine echte Zeile und keine erfundene Nummer: seit die
    Testsuite Fremdschluessel scharf stellt, faellt eine Zuordnung ins Leere
    sofort auf, und `guardian.incident_id` landet im Audit.
    """
    rolle = Role(name=f"guardian-{user.id}", description=None, is_system=False)
    db.add(rolle)
    db.flush()
    db.add(RolePermission(role_id=rolle.id, permission_key="ai.chat.use"))
    db.commit()
    set_user_roles(db, user, [rolle.id])

    verzeichnis = tmp_path / "guardian-server"
    verzeichnis.mkdir()
    (verzeichnis / "server.cfg").write_text("port=2302\nmaxPlayers=40\n", encoding="utf-8")

    server = Server(
        name="Guardian Server",
        game_type="dayz",
        install_dir=str(verzeichnis),
        container_name=f"msm-guardian-{uuid4().hex[:8]}",
        status="stopped",
        # Eine andere Adresse als die des Hosts: `_bind_ip_payload` weist einen
        # Vorschlag ab, der die bereits eingestellte Adresse noch einmal setzt.
        public_bind_ip="172.17.0.9",
    )
    db.add(server)
    db.commit()
    db.refresh(server)

    for key in serverrechte:
        db.add(ServerPermission(user_id=user.id, server_id=server.id, permission_key=key))
    unterhaltung = AiConversation(
        id=str(uuid4()), user_id=user.id, server_id=server.id, title="Heilung"
    )
    db.add(unterhaltung)

    vorfall = Incident(
        server_id=server.id,
        title="Autopilot: process_not_running",
        description="GameThread haengt",
        type="process_not_running",
        status="open",
        fingerprint=f"guardian:{server.id}:process_not_running",
        occurrences=3,
        created_at=datetime.now(timezone.utc) - timedelta(minutes=10),
    )
    db.add(vorfall)
    db.commit()
    db.refresh(vorfall)
    return server, unterhaltung, vorfall


def _guardian(server: Server, vorfall: Incident) -> GuardianKontext:
    return GuardianKontext(
        server_id=server.id,
        incident_id=vorfall.id,
        incident_created_at=vorfall.created_at,
    )


def _backup(
    db: Session, server: Server, *, alter: timedelta, nachgewiesen: bool
) -> Backup:
    """Eine Backup-Zeile mit ausdruecklichem Alter und ausdruecklichem Nachweis.

    ``size_mb=0`` ist Absicht und kein vergessener Wert: es ist genau der Fall,
    an dem eine Erfolgspruefung ueber die Groesse das einzige vorhandene Backup
    verwerfen wuerde. Der Nachweis haengt allein an `verified_at`.
    """
    zeitpunkt = datetime.now(timezone.utc) - alter
    zeile = Backup(
        server_id=server.id,
        name="Vor der Heilung",
        filename=f"{uuid4().hex}.tar.gz",
        size_mb=0,
        created_at=zeitpunkt,
        sha256="a" * 64 if nachgewiesen else None,
        verified_at=zeitpunkt if nachgewiesen else None,
    )
    db.add(zeile)
    db.commit()
    db.refresh(zeile)
    return zeile


def _argumente(server: Server, werkzeug: str) -> dict:
    """Gueltige Argumente je Werkzeug der Pflichtmenge.

    Sie muessen gueltig sein, sonst belegt der Test nichts: die Schranke steht
    am **Ende** von `create_proposal`, hinter Rechtepruefung und Nutzlastbau. Ein
    Aufruf mit unbrauchbaren Argumenten scheitert vorher — mit einem anderen
    Fehler, an einer anderen Stelle, und ueber das Backup ist damit nichts
    gesagt.
    """
    datei = Path(server.install_dir) / "server.cfg"
    besonders: dict[str, dict] = {
        "propose_config_update": {
            "path": "server.cfg",
            "content": "port=2402\nmaxPlayers=40\n",
            "expected_revision": content_revision(datei.read_bytes()),
        },
        "propose_config_patch": {
            "path": "server.cfg",
            "expected_revision": content_revision(datei.read_bytes()),
            "edits": [{"find": "port=2302", "replace": "port=2402"}],
        },
        "propose_file_delete": {"path": "server.cfg"},
        "propose_server_repair": {"action": "repair_permissions"},
        "propose_mod_install": {"workshop_id": "1559212036", "action": "install"},
        "propose_bind_ip_update": {"bind_ip": HOST_ADRESSE},
    }
    if werkzeug not in besonders:
        pytest.fail(
            f"{werkzeug} steht in GUARDIAN_BACKUP_PFLICHT_TOOLS, aber hier fehlen "
            "gueltige Argumente dafuer. Ohne sie scheitert der Aufruf vor der "
            "Schranke und der Test wuerde Gruen melden, ohne sie geprueft zu haben."
        )
    return {
        "server_id": server.id,
        "reason": "Der Vorfall zeigt eine unbrauchbare Konfiguration.",
        "expected_effect": "Der Server laeuft danach wieder an.",
        **besonders[werkzeug],
    }


def _vorschlagen(
    db: Session,
    user: User,
    unterhaltung: AiConversation,
    server: Server,
    werkzeug: str,
    *,
    guardian: GuardianKontext | None,
) -> AiActionProposal:
    return ai_proposal_service.create_proposal(
        db,
        user=user,
        conversation=unterhaltung,
        tool_name=werkzeug,
        arguments=_argumente(server, werkzeug),
        correlation_id=str(uuid4()),
        guardian=guardian,
    )


# ── Die Schranke selbst ───────────────────────────────────────────────────


def test_ohne_jedes_verifizierte_backup_scheitert_der_eingriff(
    db: Session, regular_user: User, tmp_path: Path
) -> None:
    """Der Grundfall: keine Sicherung, kein Eingriff — und zwar als Zustandsfehler.

    Ein `AiActionStateError` und keine Validierungsmeldung, weil es kein
    Formfehler des Modells ist, sondern eine Bedingung der Anlage. Nur ueber den
    Code erfaehrt das Modell, dass es zuerst `propose_backup` aufrufen soll;
    aus einer Formmeldung koennte es das nicht ableiten.
    """
    server, unterhaltung, vorfall = _aufbau(db, regular_user, tmp_path)

    with pytest.raises(AiActionStateError) as fehler:
        _vorschlagen(
            db, regular_user, unterhaltung, server, "propose_file_delete",
            guardian=_guardian(server, vorfall),
        )

    assert fehler.value.code == "AI_BACKUP_UNVERIFIED"
    db.rollback()
    assert db.query(AiActionProposal).count() == 0
    # Die Datei ist noch da. Ein Vorschlag fuehrt zwar nichts aus, aber ohne
    # diese Zeile bliebe offen, ob der abgebrochene Weg unterwegs etwas anfasst.
    assert (Path(server.install_dir) / "server.cfg").exists()


def test_ein_nachgewiesenes_backup_nach_dem_vorfall_laesst_den_eingriff_zu(
    db: Session, regular_user: User, tmp_path: Path
) -> None:
    """Die Gegenprobe — ohne sie waere die Schranke auch durch Totalsperre erfuellt.

    Ein Test, der nur "ohne Backup geht es nicht" festhaelt, bliebe gruen, wenn
    ueberhaupt nichts mehr durchkaeme. Deshalb hier derselbe Aufruf unter der
    einen Bedingung, die die Schranke verlangt: `verified_at` gesetzt und
    juenger als der Vorfall.
    """
    server, unterhaltung, vorfall = _aufbau(db, regular_user, tmp_path)
    _backup(db, server, alter=timedelta(minutes=1), nachgewiesen=True)

    vorschlag = _vorschlagen(
        db, regular_user, unterhaltung, server, "propose_file_delete",
        guardian=_guardian(server, vorfall),
    )
    db.commit()

    assert vorschlag.tool_name == "propose_file_delete"
    assert vorschlag.server_id == server.id


def test_ein_backup_von_vor_dem_vorfall_zaehlt_nicht(
    db: Session, regular_user: User, tmp_path: Path
) -> None:
    """Nachgemessen ja — aber am falschen Zustand.

    Das Backup liegt vor der Stoerung. Es beweist, wie der Server aussah, bevor
    etwas schiefging, und genau darum geht es nicht: zurueckgeholt werden muss
    der Stand, den die KI gleich anfasst. Was seit dem Vorfall passiert ist —
    die Weltdatei einer laufenden Nacht etwa — holt es nicht zurueck.

    Zaehlte es mit, waere die Schranke bei jedem Server mit naechtlichem
    Automatikbackup dauerhaft offen: dort gibt es immer ein verifiziertes
    Backup, nur eben ein altes.
    """
    server, unterhaltung, vorfall = _aufbau(db, regular_user, tmp_path)
    _backup(db, server, alter=timedelta(hours=1), nachgewiesen=True)

    with pytest.raises(AiActionStateError) as fehler:
        _vorschlagen(
            db, regular_user, unterhaltung, server, "propose_file_delete",
            guardian=_guardian(server, vorfall),
        )

    assert fehler.value.code == "AI_BACKUP_UNVERIFIED"


def test_eine_zeile_ohne_verified_at_ist_kein_nachweis(
    db: Session, regular_user: User, tmp_path: Path
) -> None:
    """Der Unterschied zwischen "es gibt eine Zeile" und "es gibt einen Nachweis".

    Der Remote-Agent-Pfad legt die Backup-Zeile an, **bevor** der Agent
    ueberhaupt gearbeitet hat. Wer das blosse Vorhandensein als Beleg nimmt,
    laesst die KI genau in dem Moment loeschen, in dem das Backup noch gar nicht
    geschrieben ist — und das ist der schlechteste denkbare Zeitpunkt.

    `size_mb` taugt als zweiter Anhaltspunkt nicht und steht hier deshalb auf 0:
    das ist der echte Wert jedes Archivs unter einem Megabyte.
    """
    server, unterhaltung, vorfall = _aufbau(db, regular_user, tmp_path)
    _backup(db, server, alter=timedelta(minutes=1), nachgewiesen=False)

    with pytest.raises(AiActionStateError) as fehler:
        _vorschlagen(
            db, regular_user, unterhaltung, server, "propose_file_delete",
            guardian=_guardian(server, vorfall),
        )

    assert fehler.value.code == "AI_BACKUP_UNVERIFIED"
    db.rollback()
    # Die Zeile ist da — abgewiesen wurde sie trotzdem. Ohne diese Zusicherung
    # koennte der Test auch dann gruen sein, wenn das Anlegen fehlgeschlagen ist.
    assert db.query(Backup).count() == 1


def test_ein_backup_eines_anderen_servers_zaehlt_nicht(
    db: Session, regular_user: User, tmp_path: Path
) -> None:
    """Der Nachweis gehoert dem Server, nicht der Anlage.

    Ohne die `server_id`-Bedingung in der Abfrage wuerde das naechtliche Backup
    eines beliebigen anderen Servers die Schranke oeffnen — und dann waere sie
    auf einer belebten Anlage praktisch immer offen, ohne dass es jemandem
    auffiele.
    """
    server, unterhaltung, vorfall = _aufbau(db, regular_user, tmp_path)
    fremder = Server(
        name="Fremder Server",
        game_type="dayz",
        install_dir=str(tmp_path / "fremd"),
        container_name=f"msm-fremd-{uuid4().hex[:8]}",
        status="stopped",
    )
    db.add(fremder)
    db.commit()
    db.refresh(fremder)
    _backup(db, fremder, alter=timedelta(minutes=1), nachgewiesen=True)

    with pytest.raises(AiActionStateError) as fehler:
        _vorschlagen(
            db, regular_user, unterhaltung, server, "propose_file_delete",
            guardian=_guardian(server, vorfall),
        )

    assert fehler.value.code == "AI_BACKUP_UNVERIFIED"


def test_ohne_guardian_kontext_gilt_die_schranke_nicht(
    db: Session, regular_user: User, tmp_path: Path
) -> None:
    """Im gewoehnlichen Chat entscheidet der Mensch mit seinem Klick.

    Dieselben Argumente, dasselbe Werkzeug, kein einziges Backup — und der
    Vorschlag entsteht. Das ist keine Luecke, sondern die Grenze der Zusage: die
    Schranke ersetzt den fehlenden Menschen und nicht sein Urteil. Wuerde sie
    auch hier gelten, waere jede kleine Korrektur an einer Konfigurationsdatei
    ein Minutenvorgang mit vorgeschaltetem Archivlauf — eine Verschaerfung, um
    die niemand gebeten hat.
    """
    server, unterhaltung, _ = _aufbau(db, regular_user, tmp_path)
    assert db.query(Backup).count() == 0

    vorschlag = _vorschlagen(
        db, regular_user, unterhaltung, server, "propose_file_delete",
        guardian=None,
    )
    db.commit()

    assert vorschlag.tool_name == "propose_file_delete"
    assert vorschlag.requires_confirmation is True


@pytest.mark.parametrize("werkzeug", sorted(GUARDIAN_BACKUP_PFLICHT_TOOLS))
def test_jedes_pflichtwerkzeug_steht_hinter_der_schranke(
    db: Session,
    regular_user: User,
    tmp_path: Path,
    host_schnittstellen: str,
    werkzeug: str,
) -> None:
    """Parametrisiert ueber die Menge selbst, nicht ueber eine abgeschriebene Liste.

    Eine Liste im Test waere eine zweite Quelle. Ein spaeter aufgenommenes
    Werkzeug stuende dann in der Registry hinter der Schranke, aber niemand
    pruefte das — und ein wieder herausgenommenes fiele gar nicht auf. So zieht
    jede Aenderung an `GUARDIAN_BACKUP_PFLICHT_TOOLS` diesen Test mit: fehlen
    fuer ein neues Werkzeug die Argumente, meldet sich `_argumente` mit einem
    Satz, der sagt, was zu tun ist.

    Geprueft werden beide Richtungen im selben Durchlauf. Die zweite Haelfte ist
    dabei die wichtigere Zusicherung: sie belegt, dass die Argumente wirklich
    gueltig sind und der Aufruf tatsaechlich bis zur Schranke gelaufen ist.
    """
    server, unterhaltung, vorfall = _aufbau(db, regular_user, tmp_path)
    guardian = _guardian(server, vorfall)

    with pytest.raises(AiActionStateError) as fehler:
        _vorschlagen(db, regular_user, unterhaltung, server, werkzeug, guardian=guardian)
    assert fehler.value.code == "AI_BACKUP_UNVERIFIED", werkzeug
    db.rollback()

    _backup(db, server, alter=timedelta(minutes=1), nachgewiesen=True)
    vorschlag = _vorschlagen(
        db, regular_user, unterhaltung, server, werkzeug, guardian=guardian
    )
    db.commit()

    assert vorschlag.tool_name == werkzeug


def test_propose_backup_steht_nicht_hinter_der_schranke(
    db: Session, regular_user: User, tmp_path: Path
) -> None:
    """Sonst gaebe es keinen Weg, den Nachweis ueberhaupt herzustellen.

    Henne und Ei: das einzige Werkzeug, das ein verifiziertes Backup erzeugen
    kann, darf keines voraussetzen. Stuende `propose_backup` in der
    Pflichtmenge, waere ein Heilungslauf auf einem Server ohne frisches Backup
    vollstaendig handlungsunfaehig — und zwar dauerhaft, weil er den Zustand
    nicht mehr verlassen koennte, der ihn blockiert.

    Beide Haelften gehoeren dazu: das Werkzeug muss ausserhalb der Pflichtmenge
    stehen **und** innerhalb der Werkzeugmenge des Heilungslaufs. Fehlte das
    Zweite, waere die Sackgasse dieselbe, nur eine Pruefung frueher.
    """
    assert "propose_backup" not in GUARDIAN_BACKUP_PFLICHT_TOOLS
    assert "propose_backup" in GUARDIAN_HEILUNG_TOOLS

    server, unterhaltung, vorfall = _aufbau(db, regular_user, tmp_path)
    assert db.query(Backup).count() == 0

    vorschlag = ai_proposal_service.create_proposal(
        db,
        user=regular_user,
        conversation=unterhaltung,
        tool_name="propose_backup",
        arguments={
            "server_id": server.id,
            "name": "Vor der Heilung",
            "reason": "Vor dem Eingriff absichern.",
            "expected_effect": "Ein wiederherstellbarer Stand liegt vor.",
        },
        correlation_id=str(uuid4()),
        guardian=_guardian(server, vorfall),
    )
    db.commit()

    assert vorschlag.tool_name == "propose_backup"


def test_das_fehlende_recht_schlaegt_vor_dem_fehlenden_nachweis_zu(
    db: Session, regular_user: User, tmp_path: Path
) -> None:
    """Wer den Server nicht anfassen darf, erfaehrt nichts ueber seine Backups.

    Die Reihenfolge im Vorschlagspfad ist eine Zusage und kein Zufall: erst
    `_require_tool_permission`, dann der Nachweis. Andersherum waere die
    Ablehnung selbst eine Auskunft — "AI_BACKUP_UNVERIFIED" verraet einem
    Benutzer ohne Schreibrecht, dass es auf diesem Server seit dem Vorfall kein
    geprueftes Backup gibt.
    """
    server, unterhaltung, vorfall = _aufbau(
        db, regular_user, tmp_path, serverrechte=("server.view", "server.files.read")
    )

    with pytest.raises(AiActionValidationError):
        _vorschlagen(
            db, regular_user, unterhaltung, server, "propose_file_delete",
            guardian=_guardian(server, vorfall),
        )
