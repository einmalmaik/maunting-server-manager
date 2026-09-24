"""Die KI verwaltet Rechte anderer Benutzer — mit den Grenzen des Panels.

Anlass ist der Betreiberplan vom 24.09.2026: unterwegs, per Stimme, "gib dem
Kollegen, der sich vorhin registriert hat, die normalen Rechte auf dem
Minecraft-Server". Dafuer bekam die KI drei Lesewerkzeuge und vier
Schreibwerkzeuge. Diese Datei haelt fest, was daran nicht still brechen darf:

* **Dieselben Grenzen wie im Panel.** Niemand vergibt ueber die KI mehr, als
  er selbst dauerhaft haelt; der Owner und die Systemrollen bleiben
  unangetastet; `admin` vergibt nur der Owner. Geprueft am echten Pfad
  (`create_proposal`, `execute_proposal`), nicht an einer Attrappe.
* **Autonom laeuft nur Unkritisches.** Wer die Freigabe erteilt hat, bekommt
  eine Vergabe von `UNCRITICAL_SERVER_PERMISSIONS` ohne Rueckfrage — jedes
  Entziehen, jedes kritische und jedes globale Recht fragt trotzdem, und die
  Stimme will dafuer den Klick.
* **Nur ein Worker schreibt.** Gehirn und Stimme lesen selbst, aendern aber
  ueber `worker_start` — auch der Umweg ueber `execute_server_action` fuehrt
  nicht an den Schreibwerkzeugen vorbei.
* **Keine Geheimnisse.** Die Benutzersuche gibt keine E-Mail, keinen Hash und
  kein Token heraus.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from models import (
    AiActionProposal,
    AiConversation,
    RefreshToken,
    Role,
    RolePermission,
    Server,
    ServerPermission,
    User,
)
from services import (
    ai_action_service,
    ai_autonomy_service,
    ai_proposal_service,
    ai_tool_registry,
    rechtevergabe_service,
)
from services.ai_action_errors import AiActionStateError, AiActionValidationError
from services.ai_tools import user_tools
from services.ai_voice import interactions as voice_interactions
from services.auth_service import AuthService
from services.permission_catalog import (
    CRITICAL_SERVER_PERMISSIONS,
    SERVER_KEYS,
    UNCRITICAL_SERVER_PERMISSIONS,
)
from services.permission_service import list_user_server_permission_keys
from services.role_service import get_role_by_name, set_user_roles

VERWALTUNG = (
    "users.read",
    "users.permissions.manage",
    "roles.manage",
    "ai.chat.use",
)


def _konto(db: Session, name: str, rechte: tuple[str, ...] = ()) -> User:
    user = AuthService.create_user(db, name, f"{name}@test.de", "RechtePass123!")
    if rechte:
        rolle = Role(name=f"rolle-{name}", is_system=False)
        db.add(rolle)
        db.flush()
        for recht in rechte:
            db.add(RolePermission(role_id=rolle.id, permission_key=recht))
        db.commit()
        set_user_roles(db, user, [rolle.id])
    db.refresh(user)
    return user


def _verwalter(db: Session, *, autonom: bool = False, server_rechte=UNCRITICAL_SERVER_PERMISSIONS) -> User:
    """Ein Nicht-Owner, der Rechte verwaltet und die unkritischen Serverrechte
    pauschal selbst haelt — mehr darf er also auch nicht weitergeben."""
    rechte = (*VERWALTUNG, *sorted(server_rechte))
    if autonom:
        rechte = (*rechte, "ai.autonomous.use")
    user = _konto(db, f"verwalter-{uuid4().hex[:6]}", rechte)
    if autonom:
        ai_autonomy_service.set_grant(
            db, user=user, server_id=None, enabled=True,
            max_actions_per_hour=50, granted_by=user.id,
        )
    return user


def _server(db: Session, name: str = "Minecraft Projekt") -> Server:
    server = Server(name=name, game_type="dayz", install_dir="/tmp/rechte", status="stopped")
    db.add(server)
    db.commit()
    db.refresh(server)
    return server


def _gespraech(db: Session, user: User) -> AiConversation:
    vorhanden = db.query(AiConversation).filter(AiConversation.user_id == user.id).first()
    if vorhanden is not None:
        return vorhanden
    gespraech = AiConversation(id=str(uuid4()), user_id=user.id, server_id=None, title="Rechte")
    db.add(gespraech)
    db.commit()
    return gespraech


def _vorschlag(db: Session, user: User, werkzeug: str, **argumente) -> AiActionProposal:
    return ai_proposal_service.create_proposal(
        db,
        user=user,
        conversation=_gespraech(db, user),
        tool_name=werkzeug,
        arguments={"reason": "Test", "expected_effect": "Test", **argumente},
        correlation_id=str(uuid4()),
    )


def _bestaetigen(db: Session, user: User, vorschlag: AiActionProposal) -> dict:
    _, token = ai_proposal_service.confirm_proposal(db, proposal_id=vorschlag.id, user=user)
    _, ergebnis = ai_proposal_service.execute_proposal(
        db, proposal_id=vorschlag.id, user=user, confirmation_token=token
    )
    return ergebnis


def _lesen(db: Session, user: User, werkzeug: str, **argumente) -> dict:
    return ai_action_service._execute_global_read_tool(
        db, user=user, tool_name=werkzeug, arguments=argumente
    )


# ── Katalog ───────────────────────────────────────────────────────────────


def test_kritisch_ist_alles_was_nicht_ausdruecklich_unkritisch_ist() -> None:
    """Ein neuer Serverschluessel ist kritisch, bis ihn jemand einordnet.

    Waere die kritische Menge aufgezaehlt, liefe jedes neue Recht still als
    unkritisch durch den autonomen Modus.
    """
    assert UNCRITICAL_SERVER_PERMISSIONS <= SERVER_KEYS
    assert CRITICAL_SERVER_PERMISSIONS == SERVER_KEYS - UNCRITICAL_SERVER_PERMISSIONS
    assert {
        "server.console.exec", "server.console.write", "server.files.delete",
        "server.backups.restore", "server.credentials.manage", "server.kill",
    } <= CRITICAL_SERVER_PERMISSIONS


def test_nur_ein_worker_schreibt_rechte() -> None:
    """Das Gehirn liest Benutzer und Rollen, aendern kann es sie nicht."""
    lesend = {"list_users", "read_user_permissions", "list_roles"}
    assert lesend <= ai_tool_registry.GEHIRN_TOOLS
    assert not ai_tool_registry.RECHTE_SCHREIBEN & ai_tool_registry.GEHIRN_TOOLS
    assert ai_tool_registry.RECHTE_SCHREIBEN <= ai_tool_registry.WRITE_TOOLS
    # Der Worker bekommt sie, ohne dass jemand sie eintraegt.
    assert not ai_tool_registry.RECHTE_SCHREIBEN & ai_tool_registry.worker_ausschluss()
    # Und kein Lauf ohne Menschen davor fasst Rechte an.
    for menge in (
        ai_tool_registry.GUARDIAN_HEILUNG_TOOLS,
        ai_tool_registry.aufgaben_tools("act"),
    ):
        assert not ai_tool_registry.RECHTE_SCHREIBEN & menge


def test_der_umweg_ueber_execute_server_action_reicht_keine_rechte_weiter(
    db: Session, owner_user: User
) -> None:
    """Die Stimme (und das Gehirn) koennten sonst ueber den Dispatcher doch
    selbst schreiben — genau der Weg, den `RECHTE_SCHREIBEN` schliesst."""
    from services.ai_voice.voice_dispatcher import dispatch_voice_action

    server = _server(db)
    ziel = _konto(db, "ziel-umweg")
    wert, fehler, _, vorschlaege = dispatch_voice_action(
        owner_user.id,
        {
            "tool_name": "propose_user_server_permission",
            "server_id": server.id,
            "parameters": {"user_id": ziel.id, "permissions": ["server.view"]},
        },
    )
    assert fehler == "Aktion nicht verfügbar"
    assert vorschlaege == []
    assert db.query(AiActionProposal).count() == 0


# ── Lesen ─────────────────────────────────────────────────────────────────


def test_die_suche_findet_ungefaehre_namen_und_juengste_konten(db: Session) -> None:
    verwalter = _verwalter(db)
    gesucht = _konto(db, "GamerXYZ")
    _konto(db, "xXGamerXx")
    alt = _konto(db, "Altbestand")
    alt.created_at = datetime.now(timezone.utc) - timedelta(days=30)
    db.commit()

    # Gesprochen kommt der Name selten buchstabengetreu an.
    treffer = _lesen(db, verwalter, "list_users", query="gamer x y z")
    assert treffer["users"][0]["user_id"] == gesucht.id
    namen = {u["username"] for u in _lesen(db, verwalter, "list_users", query="gamer")["users"]}
    assert {"GamerXYZ", "xXGamerXx"} <= namen
    assert "Altbestand" not in namen

    # "Hat sich vorhin registriert": das alte Konto faellt heraus.
    kuerzlich = {u["user_id"] for u in _lesen(db, verwalter, "list_users", recent_hours=24)["users"]}
    assert gesucht.id in kuerzlich
    assert alt.id not in kuerzlich

    # Wer sich vorhin angemeldet hat, ist ebenfalls "kuerzlich".
    db.add(RefreshToken(
        user_id=alt.id, token_hash=uuid4().hex, family=uuid4().hex,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    ))
    db.commit()
    kuerzlich = {u["user_id"] for u in _lesen(db, verwalter, "list_users", recent_hours=24)["users"]}
    assert alt.id in kuerzlich


def test_die_suche_gibt_keine_geheimnisse_heraus(db: Session) -> None:
    verwalter = _verwalter(db)
    ziel = _konto(db, "geheimnistraeger")
    ziel.two_factor_enabled = True
    db.commit()

    ergebnis = json.dumps(_lesen(db, verwalter, "list_users", query="geheimnis"))
    assert "geheimnistraeger@test.de" not in ergebnis
    for feld in ("email", "password", "two_factor", "token", "hash"):
        assert feld not in ergebnis


def test_lesen_braucht_die_rechte_des_panels(db: Session, regular_user: User) -> None:
    for werkzeug, argumente in (
        ("list_users", {}),
        ("read_user_permissions", {"user_id": regular_user.id}),
        ("list_roles", {}),
    ):
        with pytest.raises(AiActionValidationError, match="nicht erlaubt"):
            _lesen(db, regular_user, werkzeug, **argumente)


def test_rechte_eines_benutzers_und_die_rollenliste(db: Session) -> None:
    verwalter = _verwalter(db)
    server = _server(db)
    ziel = _konto(db, "mitspieler")
    db.add(ServerPermission(user_id=ziel.id, server_id=server.id, permission_key="server.view"))
    db.commit()

    rechte = _lesen(db, verwalter, "read_user_permissions", user_id=ziel.id)
    assert rechte["server_permissions"] == [{
        "server_id": server.id,
        "server_name": "Minecraft Projekt",
        "permissions": ["server.view"],
        "only_uncritical": True,
    }]

    rollen = _lesen(db, verwalter, "list_roles")
    assert rollen["uncritical_server_permissions"] == sorted(UNCRITICAL_SERVER_PERMISSIONS)
    admin = next(r for r in rollen["roles"] if r["name"] == "admin")
    assert admin["all_permissions"] is True and admin["is_system"] is True


# ── Serverrechte vergeben ─────────────────────────────────────────────────


def test_unkritisches_laeuft_mit_freigabe_ohne_rueckfrage(db: Session) -> None:
    """Der Kern des Plans: "gib ihm die normalen Rechte" ohne Karte.

    Und `server.view` kommt mit, auch wenn das Modell es vergisst — ohne es
    sieht der Benutzer den Server gar nicht.
    """
    verwalter = _verwalter(db, autonom=True)
    server = _server(db)
    ziel = _konto(db, "kollege")

    vorschlag = _vorschlag(
        db, verwalter, "propose_user_server_permission",
        server_id=server.id, user_id=ziel.id,
        permissions=["server.start", "server.stop", "server.console.read"],
    )
    assert vorschlag.autonomous is True
    vorschau = json.loads(vorschlag.preview_json)
    assert "always_confirm" not in vorschau
    assert vorschau["permissions_added"] == [
        "server.console.read", "server.start", "server.stop", "server.view",
    ]

    ai_proposal_service.execute_autonomously(db, proposal_id=vorschlag.id, user=verwalter)
    assert sorted(list_user_server_permission_keys(db, ziel.id, server.id)) == [
        "server.console.read", "server.start", "server.stop", "server.view",
    ]


@pytest.mark.parametrize(
    ("vorher", "argumente"),
    [
        # Ein kritisches Recht — auch wenn der Verwalter es selbst haelt.
        ((), {"permissions": ["server.console.exec"]}),
        # Entziehen, auch von Unkritischem.
        (("server.view", "server.start"), {"permissions": ["server.start"], "mode": "remove"}),
        # Ersetzen, das etwas wegnimmt.
        (("server.view", "server.start"), {"permissions": ["server.view"], "mode": "replace"}),
    ],
)
def test_kritisches_und_entziehen_fragen_immer(db: Session, vorher, argumente) -> None:
    verwalter = _verwalter(db, autonom=True, server_rechte=SERVER_KEYS)
    server = _server(db)
    ziel = _konto(db, f"ziel-{uuid4().hex[:6]}")
    for key in vorher:
        db.add(ServerPermission(user_id=ziel.id, server_id=server.id, permission_key=key))
    db.commit()

    vorschlag = _vorschlag(
        db, verwalter, "propose_user_server_permission",
        server_id=server.id, user_id=ziel.id, **argumente,
    )
    assert vorschlag.autonomous is False
    assert vorschlag.requires_confirmation is True
    vorschau = json.loads(vorschlag.preview_json)
    assert vorschau["always_confirm"] is True
    # Per Stimme reicht dafuer kein Ja.
    assert voice_interactions.klick_noetig(vorschlag.tool_name, vorschau) is True
    with pytest.raises(AiActionStateError, match="AI_ACTION_NOT_AUTONOMOUS"):
        ai_proposal_service.execute_autonomously(db, proposal_id=vorschlag.id, user=verwalter)


def test_niemand_vergibt_mehr_als_er_selbst_haelt(db: Session) -> None:
    """`_ensure_no_server_escalation` des Panels, am KI-Pfad."""
    verwalter = _verwalter(db)  # haelt nur die unkritischen Serverrechte
    server = _server(db)
    ziel = _konto(db, "eskalation")

    with pytest.raises(AiActionValidationError, match="Fehlend.*server.console.exec"):
        _vorschlag(
            db, verwalter, "propose_user_server_permission",
            server_id=server.id, user_id=ziel.id, permissions=["server.console.exec"],
        )
    assert db.query(AiActionProposal).count() == 0


def test_die_grenze_gilt_auch_beim_klick(db: Session) -> None:
    """Verliert der Verwalter sein Recht zwischen Karte und Klick, gilt das."""
    verwalter = _verwalter(db)
    server = _server(db)
    ziel = _konto(db, "spaeter")
    vorschlag = _vorschlag(
        db, verwalter, "propose_user_server_permission",
        server_id=server.id, user_id=ziel.id, permissions=["server.start"],
    )
    rolle = get_role_by_name(db, f"rolle-{verwalter.username}")
    db.query(RolePermission).filter(
        RolePermission.role_id == rolle.id, RolePermission.permission_key == "server.start"
    ).delete()
    db.commit()

    with pytest.raises(AiActionStateError, match="AI_ACTION_ACCESS_REVOKED"):
        _bestaetigen(db, verwalter, vorschlag)
    assert list_user_server_permission_keys(db, ziel.id, server.id) == []


def test_ein_geaenderter_stand_fuehrt_den_alten_plan_nicht_aus(db: Session) -> None:
    verwalter = _verwalter(db)
    server = _server(db)
    ziel = _konto(db, "gleichzeitig")
    vorschlag = _vorschlag(
        db, verwalter, "propose_user_server_permission",
        server_id=server.id, user_id=ziel.id, permissions=["server.start"],
    )
    # Inzwischen hat jemand im Panel etwas vergeben.
    db.add(ServerPermission(user_id=ziel.id, server_id=server.id, permission_key="server.files.read"))
    db.commit()

    with pytest.raises(AiActionStateError, match="AI_ACTION_REVISION_CONFLICT"):
        _bestaetigen(db, verwalter, vorschlag)
    assert list_user_server_permission_keys(db, ziel.id, server.id) == ["server.files.read"]


def test_owner_und_leere_aenderungen_werden_abgewiesen(db: Session, owner_user: User) -> None:
    verwalter = _verwalter(db)
    server = _server(db)
    with pytest.raises(AiActionValidationError, match="Owner"):
        _vorschlag(
            db, verwalter, "propose_user_server_permission",
            server_id=server.id, user_id=owner_user.id, permissions=["server.view"],
        )
    ziel = _konto(db, "hatschon")
    db.add(ServerPermission(user_id=ziel.id, server_id=server.id, permission_key="server.view"))
    db.commit()
    with pytest.raises(AiActionValidationError, match="Keine Änderung"):
        _vorschlag(
            db, verwalter, "propose_user_server_permission",
            server_id=server.id, user_id=ziel.id, permissions=["server.view"],
        )
    with pytest.raises(AiActionValidationError, match="Unbekannte Rechte"):
        _vorschlag(
            db, verwalter, "propose_user_server_permission",
            server_id=server.id, user_id=ziel.id, permissions=["server.alles"],
        )


def test_argumente_werden_nachsichtig_gelesen(db: Session) -> None:
    """"add" meint dasselbe wie "merge", ein Komma-Text dasselbe wie eine Liste."""
    verwalter = _verwalter(db)
    server = _server(db)
    ziel = _konto(db, "nachsicht")
    vorschlag = _vorschlag(
        db, verwalter, "propose_user_server_permission",
        server_id=server.id, user_id=str(ziel.id),
        permissions="server.view, server.start", mode="add",
    )
    _bestaetigen(db, verwalter, vorschlag)
    assert sorted(list_user_server_permission_keys(db, ziel.id, server.id)) == [
        "server.start", "server.view",
    ]


# ── Rollen ────────────────────────────────────────────────────────────────


def test_systemrollen_fasst_die_ki_nicht_an(db: Session, owner_user: User) -> None:
    for name in ("admin", "user"):
        rolle = get_role_by_name(db, name)
        with pytest.raises(AiActionValidationError, match="Systemrolle"):
            _vorschlag(db, owner_user, "propose_role_set", role_id=rolle.id, description="neu")
        with pytest.raises(AiActionValidationError, match="Systemrolle"):
            _vorschlag(db, owner_user, "propose_role_delete", role_id=rolle.id)


def test_eine_rolle_traegt_nur_eigene_rechte(db: Session) -> None:
    verwalter = _verwalter(db)
    with pytest.raises(AiActionValidationError, match="Fehlend.*servers.delete"):
        _vorschlag(db, verwalter, "propose_role_set", name="loescher", permissions=["servers.delete"])


def test_rolle_anlegen_aendern_und_loeschen(db: Session) -> None:
    verwalter = _verwalter(db, autonom=True)

    anlegen = _vorschlag(
        db, verwalter, "propose_role_set",
        name="spielleitung", permissions=["server.view", "server.start"],
    )
    assert anlegen.autonomous is True
    ai_proposal_service.execute_autonomously(db, proposal_id=anlegen.id, user=verwalter)
    rolle = get_role_by_name(db, "spielleitung")
    assert rolle is not None

    # Ein Recht wegnehmen trifft jeden mit dieser Rolle — immer mit Karte.
    aendern = _vorschlag(
        db, verwalter, "propose_role_set", role_id=rolle.id, permissions=["server.view"],
    )
    assert aendern.autonomous is False
    assert json.loads(aendern.preview_json)["permissions_removed"] == ["server.start"]
    _bestaetigen(db, verwalter, aendern)

    loeschen = _vorschlag(db, verwalter, "propose_role_delete", role_id=rolle.id)
    assert "propose_role_delete" in ai_tool_registry.ALWAYS_CONFIRM_TOOLS
    assert loeschen.autonomous is False
    _bestaetigen(db, verwalter, loeschen)
    assert get_role_by_name(db, "spielleitung") is None


def test_eine_zugewiesene_rolle_wird_nicht_geloescht(db: Session) -> None:
    verwalter = _verwalter(db)
    rolle = rechtevergabe_service.create_role(db, verwalter, "belegt", None, ["server.view"])
    ziel = _konto(db, "traeger")
    set_user_roles(db, ziel, [rolle.id])
    with pytest.raises(AiActionValidationError, match="noch 1 Benutzer"):
        _vorschlag(db, verwalter, "propose_role_delete", role_id=rolle.id)


def test_rollen_zuweisen_mit_den_grenzen_des_panels(db: Session, owner_user: User) -> None:
    verwalter = _verwalter(db, autonom=True)
    ziel = _konto(db, "rollenziel")

    # `admin` vergibt nur der Owner — wie `_assign_roles` im Panel.
    with pytest.raises(AiActionValidationError, match="Nur Owner"):
        _vorschlag(db, verwalter, "propose_user_roles", user_id=ziel.id, role_ids=[get_role_by_name(db, "admin").id])
    with pytest.raises(AiActionValidationError, match="Owner"):
        _vorschlag(db, verwalter, "propose_user_roles", user_id=owner_user.id, role_ids=[1])
    with pytest.raises(AiActionValidationError, match="eigene Rolle"):
        _vorschlag(db, verwalter, "propose_user_roles", user_id=verwalter.id, role_ids=[1])

    rolle = rechtevergabe_service.create_role(db, verwalter, "spieler", None, ["server.view"])
    zuweisen = _vorschlag(db, verwalter, "propose_user_roles", user_id=ziel.id, role_ids=[rolle.id])
    assert zuweisen.autonomous is True
    ai_proposal_service.execute_autonomously(db, proposal_id=zuweisen.id, user=verwalter)
    db.refresh(ziel)
    assert rolle.id in ziel.role_ids

    wegnehmen = _vorschlag(
        db, verwalter, "propose_user_roles", user_id=ziel.id, role_ids=[rolle.id], mode="remove",
    )
    assert wegnehmen.autonomous is False


# ── Stimme ────────────────────────────────────────────────────────────────


def test_die_stimme_liest_selbst_und_uebergibt_die_aenderung(
    db: Session, regular_user: User, monkeypatch
) -> None:
    from services.ai_voice import realtime_session

    monkeypatch.setattr(
        realtime_session.ai_action_service,
        "angebotene_werkzeuge",
        lambda *_args: {
            "list_users", "read_user_permissions", "list_roles", "worker_start",
            "worker_cancel", "propose_user_server_permission",
        },
    )
    provider = type("Anbieter", (), {"provider_kind": "openai", "realtime_model": "x"})()
    namen = {
        eintrag["name"]
        for eintrag in realtime_session.angebotene_werkzeuge(
            db, provider=provider, user=regular_user, herkunft="panel"
        )
    }
    assert {"list_users", "read_user_permissions", "list_roles", "worker_start"} <= namen
    assert "worker_cancel" not in namen
    assert "propose_user_server_permission" not in namen


def test_klick_noetig_liest_die_vorschau() -> None:
    assert voice_interactions.klick_noetig("propose_user_server_permission", {}) is False
    assert voice_interactions.klick_noetig(
        "propose_user_server_permission", {"always_confirm": True}
    ) is True
    # Das Modell kann die Vorschau nicht setzen; ein Text statt True zaehlt nicht.
    assert voice_interactions.klick_noetig(
        "propose_user_server_permission", {"always_confirm": "ja"}
    ) is False
    assert voice_interactions.klick_noetig("propose_role_delete") is True


def test_die_aehnlichkeit_trennt_treffer_von_zufall() -> None:
    assert user_tools.aehnlichkeit("GamerXYZ", "gamerxyz") == 1.0
    assert user_tools.aehnlichkeit("gamer", "xXGamerXx") >= user_tools.MIN_MATCH
    assert user_tools.aehnlichkeit("max", "admin") < user_tools.MIN_MATCH
