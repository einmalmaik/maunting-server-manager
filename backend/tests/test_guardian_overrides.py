"""Die Guardian-Uebersteuerung je Server.

Der Fall dahinter ist keine Bequemlichkeit, sondern eine Luecke im Aufbau: die
Blueprint gilt fuer **jeden** Server ihres Spiels. Sie kann nicht wissen, dass
auf dieser einen Node zwoelf Instanzen um acht Gigabyte streiten und deshalb
keine davon in dreissig Sekunden hochkommt. Guardian sieht dort einen Server,
der die Startfrist reisst, startet ihn neu, sieht es wieder — und nach drei
Anlaeufen steht er in Quarantaene, obwohl nichts kaputt ist ausser der
Erwartung.

Genau diesen Fall darf die KI im Reparaturlauf ohne Klick beheben. Was diese
Datei prueft, ist deshalb weniger die Funktion als die Schranken darum:

* die Werte kommen aus einer **geschlossenen Menge** und werden zweimal
  geklemmt — im Werkzeug (als Rueckmeldung an das Modell) und im Compiler
  (gegen alles, was nicht durch das Werkzeug kam),
* eine Uebersteuerung, die der Agent nicht annimmt, wird **zurueckgerollt**,
* sie bewegt die Generation, sonst kaeme sie nie an,
* und sie ist im Panel **sichtbar** samt Herkunft und Rueckweg.
"""

from __future__ import annotations

import json
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from blueprints.schema import load_blueprint_dict
from models import ChangeEvent, Node, Server, User
from models.server_port import ServerPort
from services.ai_action_service import AiActionValidationError
from services.guardian_runtime_compiler import (
    GUARDIAN_STELLSCHRAUBEN,
    compile_guardian_config,
    gelesene_uebersteuerung,
    guardian_config_hash,
)


def _blueprint():
    return load_blueprint_dict(
        {
            "version": 1,
            "meta": {
                "id": "guardian_override_test",
                "name": "Guardian Override Test",
                "category": "bot",
                "description": "",
            },
            "runtime": {
                "image": "synthetic.invalid/runtime:1",
                "startup": "./start",
                "env": {},
            },
            "ports": [{"name": "game", "protocol": "tcp"}],
            "source": {"type": "dockerOnly", "updateStrategy": "none"},
            "health": {
                "process": {"required": True, "id": "process"},
                "port": {
                    "id": "game-port",
                    "protocol": "tcp",
                    "port": "{{SERVER_PORT}}",
                    "timeout": "2s",
                    "interval": "15s",
                },
                "startup": {"grace_period_seconds": 10, "timeout_seconds": 120},
            },
            "recovery": {
                "policies": [{"match": "process_not_running", "action": "restart"}],
                "max_attempts": 3,
                "cooldown_seconds": 60,
            },
        }
    )


def _server(uebersteuerung: dict | str | None = None) -> Server:
    server = Server(
        id=4711,
        name="Synthetic",
        game_type="guardian_override_test",
        install_dir="/synthetic/not-real",
        status="stopped",
        desired_power_state="running",
        desired_state_generation=3,
        guardian_observed_state="unknown",
        public_bind_ip="127.0.0.1",
    )
    server.ports = [ServerPort(role="game", port=25565, protocol="tcp")]
    if isinstance(uebersteuerung, str):
        server.guardian_overrides_json = uebersteuerung
    elif uebersteuerung is not None:
        server.guardian_overrides_json = json.dumps(uebersteuerung)
    return server


class TestKlemmung:
    """Was aus der Spalte kommt, ist nie ungeprueft.

    `gelesene_uebersteuerung` laeuft in jedem Reconcile-Takt ueber jeden Server.
    Sie ist damit die letzte Stelle, an der eine von Hand verbogene oder aus
    einer aelteren Fassung stammende Zeile noch abgefangen werden kann — und
    zugleich eine, die unter keinen Umstaenden werfen darf.
    """

    def test_werte_ausserhalb_des_bereichs_werden_geklemmt(self) -> None:
        server = _server(
            {
                "startup_grace_period_seconds": 999_999,
                "probe_interval_seconds": 0,
            }
        )
        werte = gelesene_uebersteuerung(server)
        assert werte["startup_grace_period_seconds"] == 3_600
        assert werte["probe_interval_seconds"] == 1

    def test_unbekannte_schluessel_fallen_weg(self) -> None:
        """Die Menge ist geschlossen — nicht "alles ausser".

        Ein durchgelassener Fremdschluessel landete sonst irgendwann in der
        Nutzlast an den Agenten, und der prueft seine Felder.
        """
        server = _server(
            {"probe_interval_seconds": 30, "quarantine_disabled": True, "foo": 1}
        )
        assert gelesene_uebersteuerung(server) == {"probe_interval_seconds": 30}

    def test_wahrheitswerte_zaehlen_nicht_als_zahl(self) -> None:
        """`True` ist in Python eine Eins — als Startfenster waere das eine Sekunde.

        Niemand hat das gemeint, und `isinstance(True, int)` ist wahr. Ohne die
        ausdrueckliche Abweisung haette ein Modell, das statt einer Zahl einen
        Schalter schickt, ein Startfenster von einer Sekunde bekommen.
        """
        server = _server({"startup_grace_period_seconds": True})
        assert gelesene_uebersteuerung(server) == {}

    def test_unlesbares_json_gilt_als_keine_uebersteuerung(self) -> None:
        """Eine kaputte Zeile darf nicht die Node anhalten.

        Diese Funktion laeuft im Reconcile-Takt ueber jeden Server. Wuerfe sie,
        bliebe die Guardian-Synchronisation **aller** Server dieser Node
        stehen — wegen einer einzigen Zeile.
        """
        assert gelesene_uebersteuerung(_server("{kein json")) == {}
        assert gelesene_uebersteuerung(_server("[1,2,3]")) == {}
        assert gelesene_uebersteuerung(_server(None)) == {}

    def test_jede_stellschraube_hat_eine_untere_und_obere_grenze(self) -> None:
        """Kein Deckel offen — auch nicht bei einem spaeteren Zusatz.

        Der Test faellt, sobald jemand eine Stellschraube ohne Bereich
        hinzufuegt. Ohne Deckel waere ein Startfenster von zehn Tagen erlaubt,
        also ein Guardian, der nie wieder etwas meldet.
        """
        for name, grenzen in GUARDIAN_STELLSCHRAUBEN.items():
            unten, oben = grenzen
            assert isinstance(unten, int) and isinstance(oben, int), name
            assert unten < oben, name


class TestWirkung:
    """Was die Uebersteuerung in der fertigen Konfiguration bewegt — und was nicht."""

    def test_nicht_genanntes_bleibt_wie_die_blueprint_es_sagt(self) -> None:
        """Ein Nachtrag, keine zweite Konfiguration.

        Sonst muesste eine Uebersteuerung vollstaendig sein — und dann waere sie
        eine Blueprint.
        """
        ohne = compile_guardian_config(_server(), _blueprint())
        mit = compile_guardian_config(
            _server({"probe_interval_seconds": 42}), _blueprint()
        )
        assert mit["startup"] == ohne["startup"]
        assert mit["recovery"]["max_attempts"] == ohne["recovery"]["max_attempts"]
        assert all(p["interval_seconds"] == 42 for p in mit["health_checks"])

    def test_die_prozessprobe_bekommt_keine_netzgeduld(self) -> None:
        """Sie sieht nach, ob ein Prozess laeuft — es gibt keine Gegenstelle.

        Eine Zeitueberschreitung waere dort eine Zahl ohne Bedeutung, und der
        Agent prueft seine Felder.
        """
        config = compile_guardian_config(
            _server({"probe_timeout_seconds": 77}), _blueprint()
        )
        proben = {p["type"]: p for p in config["health_checks"]}
        assert "process" in proben
        assert "timeout_seconds" not in proben["process"] or (
            proben["process"].get("timeout_seconds") != 77
        )
        assert proben["tcp"]["timeout_seconds"] == 77

    def test_startfenster_und_leiter_kommen_an(self) -> None:
        config = compile_guardian_config(
            _server(
                {
                    "startup_grace_period_seconds": 600,
                    "startup_timeout_seconds": 1_800,
                    "recovery_max_attempts": 0,
                    "recovery_cooldown_seconds": 900,
                }
            ),
            _blueprint(),
        )
        assert config["startup"]["grace_period_seconds"] == 600
        assert config["startup"]["timeout_seconds"] == 1_800
        # ``0`` ist erlaubt und heisst "haende weg, melde nur noch". Ein
        # Rueckfall auf den Blueprint-Wert waere hier die schlimmste Auslegung:
        # der Betreiber haette die Selbstheilung abgeschaltet und bekaeme sie
        # unveraendert weiter.
        assert config["recovery"]["max_attempts"] == 0
        assert config["recovery"]["cooldown_seconds"] == 900


class TestGeneration:
    """Ohne bewegten Hash kaeme die Uebersteuerung nie an.

    `compile_and_sync_desired_state` schickt nur, was sich geaendert hat. Waere
    die Uebersteuerung nicht Teil des Hashes, stuende sie in der Datenbank und
    wirkte nie — der schlimmste Ausgang, weil das Panel sie anzeigte.
    """

    def test_die_uebersteuerung_bewegt_den_konfigurationshash(self) -> None:
        blueprint = _blueprint()
        ohne = guardian_config_hash(_server(), blueprint)
        mit = guardian_config_hash(_server({"probe_interval_seconds": 42}), blueprint)
        assert ohne != mit

    def test_gleiche_uebersteuerung_gleicher_hash(self) -> None:
        """Sonst liefe bei jedem Takt eine Synchronisation ohne Anlass."""
        blueprint = _blueprint()
        a = guardian_config_hash(_server({"probe_interval_seconds": 42}), blueprint)
        b = guardian_config_hash(_server({"probe_interval_seconds": 42}), blueprint)
        assert a == b


class TestNutzlast:
    """`propose_guardian_tuning` — abgewiesen statt stillschweigend geklemmt."""

    def test_unbekannte_stellschraube_wird_abgewiesen(self) -> None:
        from services.ai_proposal_service import _guardian_tuning_payload

        with pytest.raises(AiActionValidationError, match="Unbekannte"):
            _guardian_tuning_payload(_server(), {"quarantine_disabled": 1})

    def test_ausserhalb_des_bereichs_wird_abgewiesen_nicht_geklemmt(self) -> None:
        """Das Modell soll erfahren, dass es danebenlag.

        Geklemmt wird trotzdem noch einmal im Compiler — aber wer dort klemmt,
        bekaeme etwas anderes, als er vorgeschlagen hat, und schriebe es dem
        Menschen als seine Aenderung in die Karte.
        """
        from services.ai_proposal_service import _guardian_tuning_payload

        with pytest.raises(AiActionValidationError, match="ausserhalb"):
            _guardian_tuning_payload(
                _server(), {"startup_grace_period_seconds": 99_999}
            )

    def test_zuruecksetzen_und_setzen_schliessen_sich_aus(self) -> None:
        from services.ai_proposal_service import _guardian_tuning_payload

        with pytest.raises(AiActionValidationError, match="schliessen sich aus"):
            _guardian_tuning_payload(
                _server(), {"reset": True, "probe_interval_seconds": 30}
            )

    def test_eine_nutzlast_ohne_stellschraube_ist_keine(self) -> None:
        from services.ai_proposal_service import _guardian_tuning_payload

        with pytest.raises(AiActionValidationError, match="Keine Guardian"):
            _guardian_tuning_payload(_server(), {})

    def test_der_nachtrag_behaelt_was_er_nicht_nennt(self) -> None:
        """Sonst hiesse jede einzelne Zahl, alle anderen zu verlieren."""
        from services.ai_proposal_service import _guardian_tuning_payload

        server = _server({"probe_interval_seconds": 30, "recovery_max_attempts": 5})
        payload, preview = _guardian_tuning_payload(
            server, {"recovery_max_attempts": 1}
        )
        assert payload["overrides"] == {
            "probe_interval_seconds": 30,
            "recovery_max_attempts": 1,
        }
        assert preview["before"] == {
            "probe_interval_seconds": 30,
            "recovery_max_attempts": 5,
        }
        assert preview["changed"] == ["recovery_max_attempts"]

    def test_zuruecksetzen_leert_alles(self) -> None:
        from services.ai_proposal_service import _guardian_tuning_payload

        server = _server({"probe_interval_seconds": 30})
        payload, preview = _guardian_tuning_payload(server, {"reset": True})
        assert payload == {"overrides": {}, "reset": True}
        assert preview["after"] == {}
        assert preview["changed"] == ["probe_interval_seconds"]


class TestAusfuehrung:
    """Schreiben, synchronisieren — und zurueckrollen, wenn es nicht ankommt."""

    def _echter_server(self, db: Session, owner_user: User) -> Server:
        node = Node(
            name="node-1",
            host="https://node-1.invalid:8443",
            auth_token_enc="test-enc-v1::7b7d",
            status="online",
        )
        db.add(node)
        db.commit()
        db.refresh(node)
        server = Server(
            name="Uebersteuert",
            game_type="dayz",
            install_dir="/tmp/uebersteuert",
            status="running",
            node_id=node.id,
            desired_power_state="running",
            desired_state_generation=1,
        )
        db.add(server)
        db.commit()
        db.refresh(server)
        return server

    def test_die_uebersteuerung_wird_geschrieben_und_die_generation_bewegt(
        self, db: Session, owner_user: User
    ) -> None:
        from services.ai_proposal_service import _execute_guardian_tuning

        server = self._echter_server(db, owner_user)
        vorher = server.desired_state_generation
        with patch(
            "services.server_lifecycle_service.sync_desired_state_to_agent",
            return_value=True,
        ):
            ergebnis = _execute_guardian_tuning(
                db,
                server_id=server.id,
                payload={"overrides": {"probe_interval_seconds": 42}},
                user=owner_user,
                correlation_id=str(uuid4()),
                incident_id=99,
            )
        db.refresh(server)
        assert ergebnis["overrides"] == {"probe_interval_seconds": 42}
        assert json.loads(server.guardian_overrides_json) == {
            "probe_interval_seconds": 42
        }
        assert server.desired_state_generation > vorher
        # Ohne genullten Hash haelt der Compiler die Konfiguration fuer
        # unveraendert und schickt gar nichts.
        assert server.guardian_config_hash is None

    def test_eine_abgelehnte_quittung_rollt_zurueck(
        self, db: Session, owner_user: User
    ) -> None:
        """Der eigentliche Inhalt der Funktion.

        Ohne den Rueckweg haengt die Guardian-Synchronisation dieses Servers
        dauerhaft in einem gespeicherten Fehler: die Generation ist erhoeht, der
        Agent lehnt die Nutzlast ab, und jeder folgende Takt versucht dieselbe
        abgelehnte Konfiguration erneut. Der Server bekaeme von da an gar keine
        Guardian-Aktualisierung mehr — auch keine richtige.
        """
        from services.ai_action_errors import AiActionStateError
        from services.ai_proposal_service import _execute_guardian_tuning

        server = self._echter_server(db, owner_user)
        server.guardian_overrides_json = json.dumps({"probe_interval_seconds": 30})
        db.commit()

        with patch(
            "services.server_lifecycle_service.sync_desired_state_to_agent",
            return_value=False,
        ):
            with pytest.raises(AiActionStateError) as fehler:
                _execute_guardian_tuning(
                    db,
                    server_id=server.id,
                    payload={"overrides": {"probe_interval_seconds": 300}},
                    user=owner_user,
                    correlation_id=str(uuid4()),
                )
        assert str(fehler.value) == "AI_ACTION_GUARDIAN_SYNC_FAILED"
        db.refresh(server)
        assert json.loads(server.guardian_overrides_json) == {
            "probe_interval_seconds": 30
        }

    def test_die_chronikzeile_traegt_herkunft_und_vorfall(
        self, db: Session, owner_user: User
    ) -> None:
        """Sie ist zugleich die Herkunftsangabe im Guardian-Reiter.

        Ohne den Vorfall koennte der Reiter zwar sagen "von der KI gesetzt",
        aber nicht, woraufhin — und genau das ist die Frage, die jemand stellt,
        der eine unerwartete Zahl sieht.
        """
        from services.ai_proposal_service import _execute_guardian_tuning

        server = self._echter_server(db, owner_user)
        with patch(
            "services.server_lifecycle_service.sync_desired_state_to_agent",
            return_value=True,
        ):
            _execute_guardian_tuning(
                db,
                server_id=server.id,
                payload={"overrides": {"probe_interval_seconds": 42}},
                user=owner_user,
                correlation_id=str(uuid4()),
                incident_id=17,
            )
        zeile = (
            db.query(ChangeEvent)
            .filter(
                ChangeEvent.server_id == server.id,
                ChangeEvent.event_type == "guardian_overrides",
            )
            .one()
        )
        details = json.loads(zeile.details)
        assert details["source"] == "ai"
        assert details["incident_id"] == 17


class TestSichtbarkeit:
    """Der Reiter zeigt, was wirkt — und bietet den Rueckweg an.

    Eine unsichtbare Verhaltensaenderung waere schlimmer als das Problem, das
    sie behebt: der Betreiber sucht die Ursache dann in der Blueprint, wo sie
    nicht steht.
    """

    def test_der_reiter_zeigt_geltende_werte_und_herkunft(
        self, client, db: Session, owner_cookies: dict, test_server: Server
    ) -> None:
        test_server.guardian_overrides_json = json.dumps(
            {"probe_interval_seconds": 42, "unbekannt": 1}
        )
        db.add(
            ChangeEvent(
                server_id=test_server.id,
                event_type="guardian_overrides",
                description="x",
                details=json.dumps({"source": "ai", "incident_id": 5}),
            )
        )
        db.commit()

        antwort = client.get(
            f"/api/servers/{test_server.id}/guardian/overrides", cookies=owner_cookies
        )
        assert antwort.status_code == 200
        daten = antwort.json()
        # Gelesen wird durch dieselbe Saeuberung wie im Compiler: der Reiter
        # zeigt, was **wirkt**, nicht, was in der Spalte steht.
        assert daten["overrides"] == {"probe_interval_seconds": 42}
        assert daten["origin"]["source"] == "ai"
        assert daten["origin"]["incident_id"] == 5
        assert daten["bounds"]["probe_interval_seconds"] == {"min": 1, "max": 600}

    def test_ohne_uebersteuerung_keine_herkunft(
        self, client, db: Session, owner_cookies: dict, test_server: Server
    ) -> None:
        antwort = client.get(
            f"/api/servers/{test_server.id}/guardian/overrides", cookies=owner_cookies
        )
        assert antwort.status_code == 200
        assert antwort.json() == {
            "overrides": {},
            "bounds": antwort.json()["bounds"],
            "origin": None,
        }

    def test_zuruecksetzen_leert_die_spalte_und_bewegt_die_generation(
        self,
        client,
        db: Session,
        owner_cookies: dict,
        csrf_token: str,
        test_server: Server,
    ) -> None:
        test_server.guardian_overrides_json = json.dumps({"probe_interval_seconds": 42})
        db.commit()
        vorher = test_server.desired_state_generation

        antwort = client.request(
            "DELETE",
            f"/api/servers/{test_server.id}/guardian/overrides",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
        )
        assert antwort.status_code == 202
        db.refresh(test_server)
        assert test_server.guardian_overrides_json is None
        assert test_server.desired_state_generation > vorher
        assert test_server.guardian_config_hash is None

    def test_zuruecksetzen_braucht_das_recht_am_panelknopf(
        self,
        client,
        db: Session,
        user_cookies: dict,
        user_csrf_token: str,
        test_server: Server,
        regular_user: User,
    ) -> None:
        """`server.config.write` — nie ein eigenes Recht fuer die KI-Spalte.

        Wer die Guardian-Einstellungen eines Servers zuruecksetzen darf, ist
        dieselbe Frage wie: wer darf die Server-Einstellungen aendern.
        """
        test_server.guardian_overrides_json = json.dumps({"probe_interval_seconds": 42})
        db.commit()

        antwort = client.request(
            "DELETE",
            f"/api/servers/{test_server.id}/guardian/overrides",
            cookies=user_cookies,
            headers={"X-CSRF-Token": user_csrf_token},
        )
        assert antwort.status_code == 403
        db.refresh(test_server)
        assert test_server.guardian_overrides_json is not None


class TestAbleitungImReparaturlauf:
    """Eine Ableitung darf den Wachmann nicht mitnehmen.

    `AENDERBARE_PFADE` kennt fuenf Pfade unter `meta` und `runtime`; alles
    uebrige wird aus der Vorlage tief kopiert. Ein abgeleiteter Blueprint traegt
    die Guardian-Bloecke seiner Vorlage also immer — verlieren kann man sie nur,
    indem man von einer **guardianlosen Vorlage** ableitet und den Server
    anschliessend darauf umstellt. Dann meldet der Agent nie wieder etwas ueber
    diesen Server, und die Kampagne wartet auf einen Nachweis, den es nicht mehr
    geben kann.
    """

    def _ohne_guardian(self, monkeypatch) -> None:
        from services import blueprint_service

        leer = load_blueprint_dict(
            {
                "version": 1,
                "meta": {
                    "id": "ohne_guardian",
                    "name": "Ohne Guardian",
                    "category": "bot",
                    "description": "",
                },
                "runtime": {
                    "image": "synthetic.invalid/leer:1",
                    "startup": "./start",
                    "env": {},
                },
                "ports": [{"name": "game", "protocol": "tcp"}],
                "source": {"type": "dockerOnly", "updateStrategy": "none"},
            }
        )

        def _abgeleitet(source_id, *, new_id, changes, db):
            nutzlast = leer.model_dump(mode="json", by_alias=True)
            nutzlast["meta"]["id"] = new_id
            return nutzlast

        monkeypatch.setattr(blueprint_service, "derived_payload", _abgeleitet)
        monkeypatch.setattr(
            blueprint_service,
            "blueprint_view",
            lambda bid: {"blueprint": leer.model_dump(mode="json", by_alias=True)},
        )

    def test_im_reparaturlauf_abgewiesen(self, db: Session, monkeypatch) -> None:
        from services.ai_proposal_service import _blueprint_change_payload

        self._ohne_guardian(monkeypatch)
        with pytest.raises(AiActionValidationError, match="Guardian"):
            _blueprint_change_payload(
                db,
                {
                    "source_id": "ohne_guardian",
                    "new_id": "abgeleitet_x",
                    "changes": {"meta.name": "Neu"},
                },
                reparatur=True,
            )

    def test_im_chat_erlaubt_aber_auf_der_karte_vermerkt(
        self, db: Session, monkeypatch
    ) -> None:
        """Im Chat liest ein Mensch mit — er soll es sehen, nicht verboten bekommen.

        Ein Blueprint ohne Guardian ist ein zulaessiger Blueprint; wer im Chat
        einen ableitet, tut das absichtlich. Die Karte sagt ihm trotzdem, dass
        er dabei keinen Wachmann bekommt.
        """
        from services.ai_proposal_service import _blueprint_change_payload

        self._ohne_guardian(monkeypatch)
        _, preview = _blueprint_change_payload(
            db,
            {
                "source_id": "ohne_guardian",
                "new_id": "abgeleitet_x",
                "changes": {"meta.name": "Neu"},
            },
        )
        assert preview["guardian_enabled"] is False
