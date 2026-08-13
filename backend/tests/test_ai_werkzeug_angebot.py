"""Angeboten wird nur, was der Benutzer auch ausfuehren darf.

Bis hierher bekam **jeder** Benutzer alle 51 Werkzeugbeschreibungen mit jeder
Anfrage — 94 Prozent des Prompts, und das einmal je Werkzeugrunde. Das war
zuerst ein Fehler und erst danach eine Verschwendung: die KI erbt die Rechte
des Benutzers, wer kein Hoster-Recht hat, dessen KI kann die Hoster-Werkzeuge
gar nicht ausfuehren. Wir haben dem Modell also Faehigkeiten angeboten, die es
in seinem Namen nie hatte, und es daran scheitern lassen.

**Die wichtigste Zusage dieser Datei ist die letzte Gruppe.** Der Filter
entscheidet, was *angeboten* wird — nicht, was erlaubt ist. Ein Modell, das
sich ein Werkzeug ausdenkt oder aus dem Gespraechsverlauf abschreibt, muss
weiterhin abprallen. Ohne diesen Nachweis waere der Umbau eine Schwaechung.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from models import Role, RolePermission, Server, ServerPermission, User
from services import (
    ai_action_errors,
    ai_action_service,
    ai_proposal_service,
    ai_tool_registry,
    permission_service,
)
from services.role_service import set_user_roles


def _rolle_mit(db: Session, user: User, *keys: str) -> None:
    """Gibt dem Benutzer eine Rolle mit genau diesen Rechten."""
    role = Role(name=f"rolle-{user.id}-{len(keys)}", description=None, is_system=False)
    db.add(role)
    db.flush()
    for key in keys:
        db.add(RolePermission(role_id=role.id, permission_key=key))
    db.commit()
    set_user_roles(db, user, [role.id])


def _angebot(db: Session, user: User) -> frozenset[str]:
    return ai_action_service.angebotene_werkzeuge(db, user)


# ── Der Filter greift ─────────────────────────────────────────────────────

def test_a_customer_without_extra_rights_is_not_offered_the_hoster_tools(
    db: Session, regular_user: User
) -> None:
    """Ohne `panel.hoster.*` sind die Shop-Werkzeuge unbenutzbar — also weg.

    Sie gehoerten zu den teuersten Beschreibungen im Katalog und waren fuer
    einen gewoehnlichen Kunden ausnahmslos totes Gewicht.
    """
    angeboten = _angebot(db, regular_user)

    assert "read_hoster_setup" not in angeboten
    assert "read_hoster_integration_guide" not in angeboten
    assert "propose_hoster_integration" not in angeboten
    assert "propose_hoster_product" not in angeboten
    assert "propose_ai_tarif_role" not in angeboten


def test_a_customer_without_extra_rights_is_not_offered_planning_tools(
    db: Session, regular_user: User
) -> None:
    """Blueprintliste und Hostkapazitaet sind die Vorbereitung einer Anlage."""
    angeboten = _angebot(db, regular_user)

    assert "list_blueprints" not in angeboten
    assert "read_node_capacity" not in angeboten
    assert "read_node_health" not in angeboten
    assert "propose_server_create" not in angeboten
    assert "propose_server_delete" not in angeboten


def test_tools_without_any_right_stay_for_everyone(
    db: Session, regular_user: User
) -> None:
    """Doku, Serverliste und Rueckfrage haengen an keinem Recht.

    Dieselben Seiten stehen jedem angemeldeten Benutzer im Panel offen; ein
    Gate hier waere eine Schranke, die es nebenan nicht gibt.
    """
    angeboten = _angebot(db, regular_user)

    assert {"search_docs", "read_docs", "list_my_servers", "list_tasks", "ask_user"} <= angeboten


def test_the_hoster_gets_back_what_the_customer_does_not(
    db: Session, regular_user: User
) -> None:
    """Das Recht ist der einzige Unterschied — nicht die Rolle, nicht der Code."""
    ohne = _angebot(db, regular_user)
    _rolle_mit(db, regular_user, "panel.hoster.read", "panel.hoster.write")
    mit = _angebot(db, regular_user)

    assert "read_hoster_setup" in mit
    assert "propose_hoster_product" in mit
    assert mit > ohne


def test_the_owner_is_offered_everything(db: Session, owner_user: User) -> None:
    """Der Bootstrap-Bypass darf durch den Filter nicht verlorengehen."""
    assert _angebot(db, owner_user) == frozenset(ai_tool_registry.WERKZEUGE)


def test_a_delegated_server_right_is_enough_to_be_offered(
    db: Session, regular_user: User, test_server: Server
) -> None:
    """Ein per-Server delegiertes Recht zaehlt wie ein pauschales.

    Beim Zusammenstellen des Katalogs gibt es noch keinen Server — den waehlt
    das Modell erst im Argument. Die Frage lautet deshalb "kann er es
    ueberhaupt", nicht "darf er es hier".
    """
    assert "read_config" not in _angebot(db, regular_user)

    db.add(ServerPermission(
        user_id=regular_user.id,
        server_id=test_server.id,
        permission_key="server.files.read",
        granted_by=None,
    ))
    db.commit()

    assert "read_config" in _angebot(db, regular_user)


def test_lifecycle_is_offered_when_one_of_the_three_rights_is_held(
    db: Session, regular_user: User
) -> None:
    """Wer nur starten darf, soll das Werkzeug trotzdem sehen.

    Der Lebenszyklus haengt am Vorgang, nicht am Werkzeug — drei Rechte, ein
    Katalogeintrag. Welches der Aufruf braucht, entscheidet `_permission_for`.
    """
    assert "propose_server_lifecycle" not in _angebot(db, regular_user)

    _rolle_mit(db, regular_user, "server.start")

    assert "propose_server_lifecycle" in _angebot(db, regular_user)


def test_blueprint_reading_accepts_either_of_two_rights(
    db: Session, regular_user: User
) -> None:
    """Ohne den zweiten Fall koennte jemand mit `blueprints.manage` seine
    eigene Vorlage nicht ansehen."""
    _rolle_mit(db, regular_user, "blueprints.manage")

    assert "read_blueprint" in _angebot(db, regular_user)
    # Und die Serverliste bleibt trotzdem draussen: sie haengt an `servers.create`.
    assert "list_blueprints" not in _angebot(db, regular_user)


# ── Der Schnitt mit den ausgeschriebenen Laufmengen ───────────────────────

def test_the_unattended_sets_are_cut_not_replaced(db: Session, owner_user: User) -> None:
    """Ein Recht holt nichts in einen Lauf, in dem es nicht aufgezaehlt ist.

    `GUARDIAN_HEILUNG_TOOLS` und `AUFGABEN_LESEN` sind bewusst ausgeschriebene
    Aufzaehlungen: ein kuenftiges Werkzeug soll sich nicht stillschweigend in
    einen unbeaufsichtigten Lauf schleichen. Der Rechtefilter muss sie
    schneiden, nicht ersetzen — und der Owner, dem alles angeboten wird, ist
    dafuer der schaerfste Fall.
    """
    angeboten = _angebot(db, owner_user)

    guardian = angeboten & ai_tool_registry.GUARDIAN_HEILUNG_TOOLS
    aufgabe = angeboten & ai_tool_registry.aufgaben_tools("report")

    assert guardian == ai_tool_registry.GUARDIAN_HEILUNG_TOOLS
    assert aufgabe == ai_tool_registry.aufgaben_tools("report")
    # Was in keiner der beiden Aufzaehlungen steht, kommt auch beim Owner nicht
    # hinein — obwohl er jedes Recht haelt.
    assert "propose_hoster_integration" not in guardian
    assert "ask_user" not in aufgabe


def test_a_missing_right_still_shrinks_an_unattended_run(
    db: Session, regular_user: User
) -> None:
    """Und umgekehrt: die Aufzaehlung ersetzt kein fehlendes Recht.

    Ein stehender Auftrag darf `web_search` aufrufen — aber nur, wenn sein
    Besitzer die Websuche ueberhaupt benutzen darf.
    """
    geschnitten = _angebot(db, regular_user) & ai_tool_registry.aufgaben_tools("report")

    assert "web_search" in ai_tool_registry.aufgaben_tools("report")
    assert "web_search" not in geschnitten


# ── Die Sicherheitsgrenze verschiebt sich nicht ───────────────────────────
#
# Der Filter ist Fuehrung, keine Schranke. Die Wahrheit steht weiterhin in der
# Ausfuehrung. Diese drei Zusagen sind der Grund, warum der Umbau ueberhaupt
# vertretbar ist.

def test_a_tool_not_in_the_catalog_is_still_rejected_when_called(
    db: Session, regular_user: User
) -> None:
    """Nicht angeboten **und** trotzdem aufgerufen — muss abprallen.

    Genau der Fall, den ein Katalogfilter nicht abdeckt: ein Modell, das sich
    ein Werkzeug ausdenkt oder es aus dem Gespraechsverlauf abschreibt. Der
    Katalog hat es diesem Benutzer nie gezeigt; die Ausfuehrung weist es
    unabhaengig davon ab, weil ihm das Recht fehlt.
    """
    assert "read_node_health" not in _angebot(db, regular_user)

    with pytest.raises(ai_action_errors.AiActionValidationError):
        ai_action_service.execute_read_tool(
            db, user=regular_user, tool_name="read_node_health", arguments={}
        )

    assert "read_node_capacity" not in _angebot(db, regular_user)
    with pytest.raises(ai_action_errors.AiActionValidationError):
        ai_action_service.execute_read_tool(
            db, user=regular_user, tool_name="read_node_capacity", arguments={}
        )


def test_a_write_tool_not_in_the_catalog_is_still_rejected_when_proposed(
    db: Session, regular_user: User, test_server: Server
) -> None:
    """Dasselbe fuer den Vorschlagspfad.

    `propose_backup` steht diesem Benutzer nicht im Katalog, weil ihm
    `server.backups.create` fehlt. Ruft das Modell es trotzdem auf, entsteht
    kein Vorschlag — `_require_tool_permission` prueft unveraendert weiter.
    """
    db.add(ServerPermission(
        user_id=regular_user.id,
        server_id=test_server.id,
        permission_key="server.view",
        granted_by=None,
    ))
    db.commit()
    assert "propose_backup" not in _angebot(db, regular_user)

    from models import AiConversation

    conversation = AiConversation(
        id="konv-angebot-1", user_id=regular_user.id, server_id=None, title="t",
    )
    db.add(conversation)
    db.commit()

    with pytest.raises(ai_action_errors.AiActionValidationError):
        ai_proposal_service.create_proposal(
            db,
            user=regular_user,
            conversation=conversation,
            tool_name="propose_backup",
            arguments={
                "server_id": test_server.id,
                "reason": "weil",
                "expected_effect": "ein Backup",
            },
            correlation_id="korr-1",
        )


def test_the_catalog_never_widens_what_execution_allows(
    db: Session, regular_user: User
) -> None:
    """Ein angebotenes Werkzeug ist keine Erlaubnis.

    `read_config` steht im Katalog, sobald der Benutzer irgendwo Dateien lesen
    darf. Auf einem Server, den er nicht sehen darf, bleibt es trotzdem
    verschlossen — `_resolve_server` prueft am **konkreten** Server.
    """
    fremd = Server(
        name="Fremder Server",
        game_type="dayz",
        install_dir="/tmp/fremd",
        container_name="msm-srv-fremd",
        status="stopped",
    )
    db.add(fremd)
    db.commit()
    db.refresh(fremd)

    _rolle_mit(db, regular_user, "server.files.read")
    assert "read_config" in _angebot(db, regular_user)
    # Das Recht steckt in einer Rolle und gilt damit pauschal; sichtbar ist der
    # Server trotzdem nicht, denn `server.view` fehlt.
    assert not permission_service.has_server_permission(
        db, regular_user, fremd.id, "server.view"
    )

    with pytest.raises(ai_action_errors.AiActionValidationError):
        ai_action_service.execute_read_tool(
            db, user=regular_user, tool_name="read_config",
            arguments={"server_id": fremd.id, "path": "server.cfg"},
        )
