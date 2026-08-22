"""Mod-Werkzeuge der KI: lesen, Updates erkennen, Installation vorschlagen.

Zielpunkt 3.1 nennt Modverwaltung ausdruecklich als KI-Aufgabe. Zielpunkt 16
zieht die Grenze: externe Inhalte duerfen nicht ungeprueft in Serververzeichnisse
geschrieben werden. Beides zusammen bedeutet, dass die KI den vorhandenen
Installationspfad benutzt statt einen eigenen zu bekommen.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from models import (
    AiActionProposal,
    AiConversation,
    Mod,
    Role,
    RolePermission,
    Server,
    ServerPermission,
    User,
)
from services import ai_action_errors, ai_action_service, ai_proposal_service
from services.role_service import set_user_roles


def _setup(db: Session, user: User, *, server_keys: tuple[str, ...]) -> tuple[Server, AiConversation]:
    role = Role(name=f"mods-{user.id}", description=None, is_system=False)
    db.add(role)
    db.flush()
    db.add(RolePermission(role_id=role.id, permission_key="ai.chat.use"))
    db.commit()
    set_user_roles(db, user, [role.id])

    server = Server(
        name="Mod Server",
        game_type="dayz",
        install_dir="/tmp/mod-server",
        container_name="msm-mod-server",
        status="stopped",
    )
    db.add(server)
    db.commit()
    db.refresh(server)
    for key in server_keys:
        db.add(ServerPermission(user_id=user.id, server_id=server.id, permission_key=key))
    conversation = AiConversation(
        id=str(uuid4()), user_id=user.id, server_id=server.id, title="Mods"
    )
    db.add(conversation)
    db.commit()
    return server, conversation


def _install_arguments(server_id: int = 0, **overrides) -> dict:
    """Argumente eines Mod-Vorschlags samt `server_id`.

    Der Serverbezug haengt seit dem Einzelchat am Werkzeugaufruf und nicht mehr
    an der Unterhaltung.
    """
    values = {
        "server_id": server_id,
        "workshop_id": "1559212036",
        "action": "install",
        "reason": "Der Server startet ohne diese Abhaengigkeit nicht.",
        "expected_effect": "Nach dem Neustart laedt der Server die Mod.",
    }
    values.update(overrides)
    return values


def test_reading_mods_requires_the_mod_read_permission(
    db: Session, regular_user: User
) -> None:
    server, conversation = _setup(db, regular_user, server_keys=("server.view",))

    with pytest.raises(ai_action_errors.AiActionValidationError):
        ai_action_service.execute_read_tool(
            db,
            user=regular_user,
            tool_name="read_server_mods",
            arguments={"server_id": server.id},
        )


def test_reading_mods_returns_status_without_secrets(
    db: Session, regular_user: User
) -> None:
    server, conversation = _setup(
        db, regular_user, server_keys=("server.view", "server.mods.read")
    )
    db.add(Mod(
        server_id=server.id,
        workshop_id="1559212036",
        name="CF",
        enabled=True,
        install_status="installed",
        update_status="outdated",
        update_reason="remote_newer",
        load_order=1,
    ))
    db.commit()

    result = ai_action_service.execute_read_tool(
        db,
        user=regular_user,
        tool_name="read_server_mods",
        arguments={"server_id": server.id},
    )

    assert result["mods"][0]["workshop_id"] == "1559212036"
    assert result["mods"][0]["update_status"] == "outdated"
    assert result["mods"][0]["update_reason"] == "remote_newer"


def test_mod_install_without_write_permission_is_rejected(
    db: Session, regular_user: User
) -> None:
    server, conversation = _setup(
        db, regular_user, server_keys=("server.view", "server.mods.read")
    )

    with pytest.raises(ai_action_errors.AiActionValidationError):
        ai_proposal_service.create_proposal(
            db,
            user=regular_user,
            conversation=conversation,
            tool_name="propose_mod_install",
            arguments=_install_arguments(server.id),
            correlation_id=str(uuid4()),
        )
    assert db.query(AiActionProposal).count() == 0


def test_a_non_numeric_workshop_id_is_rejected(
    db: Session, regular_user: User
) -> None:
    """Die Kennung geht in einen Downloadpfad — sie muss rein numerisch sein."""
    server, conversation = _setup(
        db, regular_user, server_keys=("server.view", "server.mods.read", "server.mods.write")
    )

    for bad in ("../../etc/passwd", "12a", "", "1" * 21):
        with pytest.raises(ai_action_errors.AiActionValidationError):
            ai_proposal_service.create_proposal(
                db,
                user=regular_user,
                conversation=conversation,
                tool_name="propose_mod_install",
                arguments=_install_arguments(server.id, workshop_id=bad),
                correlation_id=str(uuid4()),
            )


def test_mod_install_proposal_needs_confirmation_and_shows_a_preview(
    db: Session, regular_user: User
) -> None:
    import json

    server, conversation = _setup(
        db, regular_user, server_keys=("server.view", "server.mods.read", "server.mods.write")
    )

    proposal = ai_proposal_service.create_proposal(
        db,
        user=regular_user,
        conversation=conversation,
        tool_name="propose_mod_install",
        arguments=_install_arguments(server.id),
        correlation_id=str(uuid4()),
    )
    db.commit()

    assert proposal.requires_confirmation is True
    preview = json.loads(proposal.preview_json)
    assert preview["operation"] == "mod_install"
    assert preview["workshop_id"] == "1559212036"
    assert preview["already_installed"] is False
    # Eine Mod wirkt erst nach einem Neustart — das gehoert in die Vorschau.
    assert preview["restart_required"] is True


def test_execution_uses_the_existing_install_path(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zielpunkt 16: kein eigener Downloadbereich fuer die KI.

    Ausgefuehrt wird `install_mod_bg` — derselbe Code mit demselben
    Install-Lock, den auch der Mod-Tab des Panels ausloest.
    """
    started: list[tuple] = []

    class _Thread:
        def __init__(self, *, target, args, daemon, name):
            started.append((target, args))

        def start(self) -> None:
            return None

    server, conversation = _setup(
        db, regular_user, server_keys=("server.view", "server.mods.read", "server.mods.write")
    )
    proposal = ai_proposal_service.create_proposal(
        db,
        user=regular_user,
        conversation=conversation,
        tool_name="propose_mod_install",
        arguments=_install_arguments(server.id),
        correlation_id=str(uuid4()),
    )
    db.commit()
    monkeypatch.setattr("threading.Thread", _Thread)

    _, token = ai_proposal_service.confirm_proposal(
        db, proposal_id=proposal.id, user=regular_user
    )
    executed, result = ai_proposal_service.execute_proposal(
        db, proposal_id=proposal.id, user=regular_user, confirmation_token=token
    )

    assert len(started) == 1
    target, args = started[0]
    from routers.mods import install_mod_bg

    assert target is install_mod_bg
    assert args == (server.id, "1559212036", "install")
    assert result["installation"] == "running"
    assert executed.status == "succeeded"
    # Die Mod-Zeile existiert und traegt den laufenden Vorgang.
    assert db.query(Mod).filter(Mod.server_id == server.id).count() == 1


def test_a_running_installation_blocks_a_second_one(
    db: Session, regular_user: User
) -> None:
    from services.mod_install_status_service import INSTALL_RUNNING

    server, conversation = _setup(
        db, regular_user, server_keys=("server.view", "server.mods.read", "server.mods.write")
    )
    db.add(Mod(
        server_id=server.id,
        workshop_id="1559212036",
        install_status=INSTALL_RUNNING,
    ))
    db.commit()
    proposal = ai_proposal_service.create_proposal(
        db,
        user=regular_user,
        conversation=conversation,
        tool_name="propose_mod_install",
        arguments=_install_arguments(server.id, action="update"),
        correlation_id=str(uuid4()),
    )
    db.commit()

    _, token = ai_proposal_service.confirm_proposal(
        db, proposal_id=proposal.id, user=regular_user
    )
    with pytest.raises(ai_action_errors.AiActionStateError) as excinfo:
        ai_proposal_service.execute_proposal(
            db, proposal_id=proposal.id, user=regular_user, confirmation_token=token
        )

    assert excinfo.value.code == "AI_ACTION_SERVER_BUSY"


def test_workshop_search_reports_a_missing_api_key_honestly(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Eine leere Trefferliste waere hier eine falsche Aussage ueber den Workshop."""
    server, conversation = _setup(
        db, regular_user, server_keys=("server.view", "server.mods.read")
    )
    monkeypatch.setattr("services.steam_api_key_service.resolve_key", lambda: None)

    result = ai_action_service.execute_read_tool(
        db,
        user=regular_user,
        tool_name="search_workshop_mods",
        arguments={"server_id": server.id, "query": "cf"},
    )

    assert result["available"] is False
    assert result["reason"] in {"steam_api_key_missing", "workshop_id_missing", "mods_not_supported"}
    assert "results" not in result


def test_mod_install_proposal_with_name_stores_mod_name(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    started: list[tuple] = []

    class _Thread:
        def __init__(self, *, target, args, daemon, name):
            started.append((target, args))

        def start(self) -> None:
            return None

    monkeypatch.setattr("threading.Thread", _Thread)

    server, conversation = _setup(
        db, regular_user, server_keys=("server.view", "server.mods.read", "server.mods.write")
    )
    proposal = ai_proposal_service.create_proposal(
        db,
        user=regular_user,
        conversation=conversation,
        tool_name="propose_mod_install",
        arguments=_install_arguments(
            server.id,
            workshop_id="927142",
            name="Moros Indomitable Duo",
        ),
        correlation_id=str(uuid4()),
    )
    db.commit()

    preview = json.loads(proposal.preview_json)
    assert preview["workshop_id"] == "927142"
    assert preview["known_name"] == "Moros Indomitable Duo"

    _, token = ai_proposal_service.confirm_proposal(
        db, proposal_id=proposal.id, user=regular_user
    )
    executed, result = ai_proposal_service.execute_proposal(
        db, proposal_id=proposal.id, user=regular_user, confirmation_token=token
    )
    assert executed.status == "succeeded"

    mod = db.query(Mod).filter(Mod.server_id == server.id, Mod.workshop_id == "927142").first()
    assert mod is not None
    assert mod.name == "Moros Indomitable Duo"


def test_read_server_mods_reports_install_error(
    db: Session, regular_user: User
) -> None:
    server, conversation = _setup(
        db, regular_user, server_keys=("server.view", "server.mods.read")
    )
    db.add(Mod(
        server_id=server.id,
        workshop_id="927142",
        name="Moros Indomitable Duo",
        enabled=True,
        install_status="failed",
        install_error="Installation fehlgeschlagen. — Download timeout",
        load_order=0,
    ))
    db.commit()

    result = ai_action_service.execute_read_tool(
        db,
        user=regular_user,
        tool_name="read_server_mods",
        arguments={"server_id": server.id},
    )

    assert len(result["mods"]) == 1
    assert result["mods"][0]["workshop_id"] == "927142"
    assert result["mods"][0]["install_status"] == "failed"
    assert result["mods"][0]["install_error"] == "Installation fehlgeschlagen. — Download timeout"



# ── Der Schalter ──────────────────────────────────────────────────────────
#
# Anlass ist ein Betriebsvorfall vom 22.08.2026: der Betreiber bat, eine Mod
# zu aktivieren und den Server neu zu starten. Passiert ist nichts. Der
# Auftrag suchte die Einstellung in der `GameUserSettings.ini` — dort steht
# sie nicht und stand sie nie — und meldete, die Aenderung sei "derzeit nicht
# pruefbar". `read_server_mods` meldete `enabled` seit jeher; setzen konnte
# es niemand.


def _toggle_arguments(server_id: int, enabled: bool, **overrides) -> dict:
    values = {
        "server_id": server_id,
        "workshop_id": "1559212036",
        "enabled": enabled,
        "reason": "Der Benutzer hat darum gebeten.",
        "expected_effect": "Nach dem Neustart laedt der Server die Mod.",
    }
    values.update(overrides)
    return values


def _installierte_mod(db: Session, server_id: int, *, enabled: bool) -> Mod:
    mod = Mod(
        server_id=server_id,
        workshop_id="1559212036",
        name="Moros Indominus",
        enabled=enabled,
        install_status="installed",
        load_order=0,
    )
    db.add(mod)
    db.commit()
    return mod


def test_mod_toggle_braucht_das_schaltrecht(db: Session, regular_user: User) -> None:
    """`server.mods.toggle` und nicht `server.mods.write`.

    Das Recht steht seit jeher im Katalog und war fuer die KI unerreichbar.
    Wer Mods nur schalten darf, soll nichts herunterladen koennen — und
    umgekehrt.
    """
    server, conversation = _setup(
        db, regular_user, server_keys=("server.view", "server.mods.write")
    )
    _installierte_mod(db, server.id, enabled=False)

    with pytest.raises(ai_action_errors.AiActionValidationError):
        ai_proposal_service.create_proposal(
            db,
            user=regular_user,
            conversation=conversation,
            tool_name="propose_mod_toggle",
            arguments=_toggle_arguments(server.id, True),
            correlation_id=str(uuid4()),
        )
    assert db.query(AiActionProposal).count() == 0

    # Mit dem richtigen Recht geht derselbe Aufruf durch.
    db.add(ServerPermission(
        user_id=regular_user.id, server_id=server.id,
        permission_key="server.mods.toggle",
    ))
    db.commit()
    proposal = ai_proposal_service.create_proposal(
        db,
        user=regular_user,
        conversation=conversation,
        tool_name="propose_mod_toggle",
        arguments=_toggle_arguments(server.id, True),
        correlation_id=str(uuid4()),
    )
    assert proposal.tool_name == "propose_mod_toggle"


def test_ein_schalter_an_einer_nicht_installierten_mod_ist_ein_formfehler(
    db: Session, regular_user: User
) -> None:
    """Erst installieren, dann schalten — als Auskunft, nicht als Absturz.

    Ein Formfehler kostet eine Runde und sagt dem Modell den Weg; ein
    Vorschlag, der beim Klick scheitert, kostet den Menschen einen Klick und
    sagt niemandem etwas.
    """
    server, conversation = _setup(
        db, regular_user, server_keys=("server.view", "server.mods.toggle")
    )

    with pytest.raises(ai_action_service.AiActionValidationError, match="nicht installiert"):
        ai_proposal_service.create_proposal(
            db,
            user=regular_user,
            conversation=conversation,
            tool_name="propose_mod_toggle",
            arguments=_toggle_arguments(server.id, True),
            correlation_id=str(uuid4()),
        )


def test_der_schalter_zeigt_den_bisherigen_stand_und_den_neustart(
    db: Session, regular_user: User
) -> None:
    import json

    server, conversation = _setup(
        db, regular_user, server_keys=("server.view", "server.mods.toggle")
    )
    _installierte_mod(db, server.id, enabled=False)

    proposal = ai_proposal_service.create_proposal(
        db,
        user=regular_user,
        conversation=conversation,
        tool_name="propose_mod_toggle",
        arguments=_toggle_arguments(server.id, True),
        correlation_id=str(uuid4()),
    )
    db.commit()

    preview = json.loads(proposal.preview_json)
    assert preview["operation"] == "mod_enable"
    assert preview["was_enabled"] is False
    # Die Startzeile entsteht beim Bau des Containers — vorher wirkt nichts.
    assert preview["restart_required"] is True
    # Die Rueckfrage der Karte setzt `path` ein („Den Schalter der Mod
    # „{{path}}" umlegen?"). Ohne den Schluessel stand dort ein leeres Paar
    # Anfuehrungszeichen, und der Bestaetigende sollte zustimmen, ohne zu
    # lesen, wozu.
    assert preview["path"] == "Moros Indominus"


def test_der_name_der_mod_steht_in_der_rueckfrage(
    db: Session, regular_user: User
) -> None:
    """Auch beim Einspielen — und ohne bekannten Namen die Kennung.

    Eine frisch in der Werkstatt gefundene Mod hat im Panel noch keinen
    Namen; dann ist die Workshop-Kennung die ehrlichere Auskunft als eine
    leere Zeile.
    """
    import json

    server, conversation = _setup(
        db, regular_user,
        server_keys=("server.view", "server.mods.read", "server.mods.write"),
    )

    proposal = ai_proposal_service.create_proposal(
        db,
        user=regular_user,
        conversation=conversation,
        tool_name="propose_mod_install",
        arguments=_install_arguments(server.id),
        correlation_id=str(uuid4()),
    )
    db.commit()

    preview = json.loads(proposal.preview_json)
    assert preview["path"]
    assert preview["path"] == (preview["known_name"] or preview["workshop_id"])


def test_der_schalter_legt_die_spalte_um(
    db: Session, regular_user: User
) -> None:
    """Die Wahrheit ueber „welche Mod ist aktiv" steht in `mods.enabled`.

    Von dort baut `games/base.active_mod_ids` die Startzeile — nicht aus einer
    Spielkonfiguration. Genau diese Spalte muss der Schalter treffen; alles
    andere (Modlisten-Dateien, `.disabled`-Marken) zieht `update_modlist`
    hinterher und ist je nach Spiel wirkungslos.
    """
    server, _conversation = _setup(
        db, regular_user, server_keys=("server.view", "server.mods.toggle")
    )
    mod = _installierte_mod(db, server.id, enabled=True)

    ergebnis = ai_proposal_service._execute_mod_toggle(
        db, server_id=server.id,
        payload={"workshop_id": "1559212036", "enabled": False},
    )

    db.refresh(mod)
    assert mod.enabled is False
    assert ergebnis["enabled"] is False
    assert ergebnis["restart_required"] is True


def test_eine_installation_meldet_den_schalterstand_mit(
    db: Session, regular_user: User
) -> None:
    """Installiert heisst nicht aktiv.

    Eine vorhandene, aber ausgeschaltete Mod laedt der Installationspfad
    zwar herunter — in die Startzeile kommt sie trotzdem nicht. Ohne diesen
    Wert in der Vorschau meldet die KI Erfolg und der Server startet ohne die
    Mod.
    """
    import json

    server, conversation = _setup(
        db, regular_user,
        server_keys=("server.view", "server.mods.read", "server.mods.write"),
    )
    _installierte_mod(db, server.id, enabled=False)

    proposal = ai_proposal_service.create_proposal(
        db,
        user=regular_user,
        conversation=conversation,
        tool_name="propose_mod_install",
        arguments=_install_arguments(server.id, action="reinstall"),
        correlation_id=str(uuid4()),
    )
    db.commit()

    preview = json.loads(proposal.preview_json)
    assert preview["already_installed"] is True
    assert preview["currently_enabled"] is False
