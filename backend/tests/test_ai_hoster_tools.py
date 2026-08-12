"""Die KI richtet die Shop-Anbindung ein — ohne zu raten und ohne Geheimnisse.

Zwei Zusagen tragen dieses Werkzeugpaar, und beide koennen still brechen.

Die erste ist **kein Schluessel im Verlauf**. Die Redaktion greift hier
nachweislich nicht: `redact_sensitive_text` ist namensgebunden, und ein
`secrets.token_urlsafe(32)` passt auf kein Muster. Die einzige tragfaehige
Fassung ist deshalb, dass das Werkzeugergebnis den Schluessel gar nicht erst
enthaelt — und genau das prueft diese Datei, nicht dass ein Filter ihn faengt.

Die zweite ist **`withheld` statt Weglassen**. Eine Liste, die fehlt, weil das
Recht fehlt, sieht sonst aus wie eine Liste, die leer ist. Das Modell schloesse
daraus, es gebe keine Rollen, und schluege vor, welche anzulegen.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from models import (
    AiConversation,
    AiToolResult,
    HosterIntegration,
    HosterProduct,
    Role,
    RolePermission,
    User,
)
from services import (
    ai_action_service,
    ai_proposal_service,
    hoster_integration_service,
)
from services.ai_action_errors import AiActionValidationError
from services.auth_service import AuthService
from services.role_service import set_user_roles


def _mit_rechten(db: Session, name: str, keys: tuple[str, ...]) -> User:
    user = AuthService.create_user(db, name, f"{name}@test.de", "ShopPass123!")
    rolle = Role(name=f"rolle-{name}", is_system=False)
    db.add(rolle)
    db.flush()
    for key in keys:
        db.add(RolePermission(role_id=rolle.id, permission_key=key))
    db.commit()
    set_user_roles(db, user, [rolle.id])
    db.refresh(user)
    return user


@pytest.fixture
def dienstbenutzer(db: Session) -> User:
    return _mit_rechten(db, "shop-service", ("servers.create",))


@pytest.fixture
def integration(db: Session, dienstbenutzer: User) -> tuple[HosterIntegration, str]:
    row, api_key = hoster_integration_service.create_integration(
        db,
        name="Testshop",
        slug="testshop",
        enabled=True,
        service_user_id=dienstbenutzer.id,
        webhook_url="https://shop.example/msm-webhook",
        terminate_grace_days=7,
    )
    hoster_integration_service.upsert_product(
        db,
        integration=row,
        external_product_key="GAME-MC-PRO",
        game_type="minecraft_vanilla",
        ram_limit_mb=8192,
        cpu_limit_percent=200,
        disk_limit_gb=40,
        node_id=None,
        backup_interval_hours=24,
        role_id=None,
        enabled=True,
    )
    db.commit()
    db.refresh(row)
    return row, api_key


def _werkzeug(db: Session, user: User, name: str, **args) -> dict:
    return ai_action_service._execute_global_read_tool(
        db, user=user, tool_name=name, arguments=args
    )


# ── Rechte ───────────────────────────────────────────────────────────────

def test_reading_the_setup_needs_the_hoster_read_permission(
    db: Session, regular_user: User, integration
) -> None:
    """`global_read` wertet `recht` in der Registry nicht aus.

    Die Pruefung muss deshalb im Zweig selbst stehen. Faellt sie weg, sieht
    jeder angemeldete Benutzer Slugs, Dienstbenutzer und Webhook-Ziele des
    Betreibers.
    """
    with pytest.raises(AiActionValidationError, match="nicht erlaubt"):
        _werkzeug(db, regular_user, "read_hoster_setup")


def test_the_guide_needs_the_same_permission(
    db: Session, regular_user: User, integration
) -> None:
    row, _ = integration
    with pytest.raises(AiActionValidationError, match="nicht erlaubt"):
        _werkzeug(db, regular_user, "read_hoster_integration_guide", integration_id=row.id)


# ── Geheimnisse ──────────────────────────────────────────────────────────

def test_no_tool_result_ever_carries_a_secret(
    db: Session, owner_user: User, integration
) -> None:
    """Der Klartextschluessel darf in keinem der beiden Ergebnisse stehen.

    Bewusst gegen den **serialisierten** Text geprueft: genau in dieser Form
    landet das Ergebnis in `ai_tool_results.result_json`, in
    `ai_runs.state_json` und in der Anfrage an den Modellanbieter.
    """
    row, api_key = integration
    webhook_secret = hoster_integration_service.set_webhook_secret(db, row)
    db.commit()

    uebersicht = json.dumps(_werkzeug(db, owner_user, "read_hoster_setup"))
    guide = json.dumps(
        _werkzeug(db, owner_user, "read_hoster_integration_guide", integration_id=row.id)
    )
    for text in (uebersicht, guide):
        assert api_key not in text
        assert webhook_secret not in text
        assert row.api_key_hash not in text
        assert (row.webhook_secret_encrypted or "\x00") not in text

    # Der Hinweis darf und soll drin sein: er zeigt, *welcher* Schluessel
    # gemeint ist, und taugt nicht, ihn zu benutzen.
    assert row.api_key_hint in uebersicht


# ── withheld ─────────────────────────────────────────────────────────────

def test_missing_side_permissions_say_withheld_not_nothing(
    db: Session, integration
) -> None:
    """Fehlt das Recht, steht `withheld` da — keine leere Liste.

    Der Unterschied ist der zwischen "du darfst es nicht sehen" und "es gibt
    keine". Nur die zweite Auskunft waere falsch, und genau sie wuerde das
    Modell weitergeben.
    """
    nur_hoster = _mit_rechten(db, "hoster-leser", ("panel.hoster.read",))
    ergebnis = _werkzeug(db, nur_hoster, "read_hoster_setup")
    assert ergebnis["grantable_roles"] == "withheld"
    assert ergebnis["service_user_candidates"] == "withheld"


def test_with_the_permissions_the_lists_are_real(
    db: Session, owner_user: User, integration, dienstbenutzer: User
) -> None:
    ergebnis = _werkzeug(db, owner_user, "read_hoster_setup")
    assert dienstbenutzer.id in [
        e["user_id"] for e in ergebnis["service_user_candidates"]
    ]
    assert ergebnis["grantable_roles"] != "withheld"


def test_only_roles_the_actor_may_grant_are_offered(db: Session, integration) -> None:
    """Sonst schlaegt das Modell eine Rolle vor, die der Schreibpfad abweist.

    Dieselben drei Regeln wie `_ensure_actor_may_grant_role` im Router: eine
    Rolle mit `servers.delete` taucht bei einem Akteur ohne dieses Recht gar
    nicht erst auf.
    """
    akteur = _mit_rechten(
        db, "einrichter", ("panel.hoster.read", "panel.hoster.write", "roles.manage")
    )
    zu_maechtig = Role(name="tarif-gross", is_system=False)
    db.add(zu_maechtig)
    db.flush()
    db.add(RolePermission(role_id=zu_maechtig.id, permission_key="servers.delete"))
    harmlos = Role(name="tarif-klein", is_system=False)
    db.add(harmlos)
    db.commit()

    namen = [r["name"] for r in _werkzeug(db, akteur, "read_hoster_setup")["grantable_roles"]]
    assert "tarif-klein" in namen
    assert "tarif-gross" not in namen


# ── Bestandsaufnahme ─────────────────────────────────────────────────────

def test_the_setup_shows_what_cannot_be_guessed(
    db: Session, owner_user: User, integration, dienstbenutzer: User
) -> None:
    """Slug, Dienstbenutzername und Produktkennung sind die drei Werte, an denen
    ein geratener Vorschlag scheitert."""
    row, _ = integration
    ergebnis = _werkzeug(db, owner_user, "read_hoster_setup")
    eintrag = next(e for e in ergebnis["integrations"] if e["integration_id"] == row.id)
    assert eintrag["slug"] == "testshop"
    assert eintrag["service_user"] == dienstbenutzer.username
    assert "GAME-MC-PRO" in [p["external_product_key"] for p in eintrag["products"]]
    assert ergebnis["used_slugs"] == ["testshop"]


# ── Der woertliche Block ─────────────────────────────────────────────────

def test_the_guide_is_built_from_code_not_from_prose(
    db: Session, owner_user: User, integration
) -> None:
    """Jeder Wert im Block muss aus derselben Quelle stammen wie die Durchsetzung.

    Eine abgeschriebene Liste waere die zweite Doku, die niemand pflegen will —
    und die erste, die falsch wird. Der Test haelt den Block gegen die Router
    und die Konstanten, nicht gegen sich selbst.
    """
    from routers import hoster_api
    from services.hoster_service_lifecycle import DESIRED_STATES, SERVICE_STATUSES

    row, _ = integration
    block = _werkzeug(db, owner_user, "read_hoster_integration_guide", integration_id=row.id)

    assert block["verbatim"] is True
    assert block["auth_header"] == hoster_integration_service.API_KEY_HEADER
    assert sorted(block["desired_states"]) == sorted(DESIRED_STATES)
    assert block["service_statuses"] == list(SERVICE_STATUSES)
    assert block["webhook_events"] == [f"service.{s}" for s in SERVICE_STATUSES]

    echte_pfade = {
        getattr(route, "path")
        for router in (hoster_api.router, hoster_api.redeem_router)
        for route in router.routes
        if getattr(route, "path", None)
    }
    genannte_pfade = {eintrag.split(" ", 1)[1] for eintrag in block["endpoints"]}
    assert genannte_pfade == echte_pfade

    # Die Produktkennung dieser Anlage, nicht ein Platzhalter.
    assert block["product_keys"] == ["GAME-MC-PRO"]


def test_the_guide_points_at_the_docs_for_what_is_not_a_constant(
    db: Session, owner_user: User, integration
) -> None:
    """Die `status_code`-Bedeutungen stehen als Tabelle in der Doku und in
    keiner Konstante.

    Sie hier aufzuzaehlen hiesse, eine Liste zu pflegen, die morgen
    unvollstaendig ist — und eine unvollstaendige Liste liest sich wie eine
    vollstaendige.
    """
    from services import ai_docs_corpus

    row, _ = integration
    block = _werkzeug(db, owner_user, "read_hoster_integration_guide", integration_id=row.id)
    zeiger = block["status_codes_documented_at"]
    abschnitte = [
        a["section"] for a in ai_docs_corpus.verzeichnis(zeiger["page"])["sections"]
    ]
    assert zeiger["section"] in abschnitte


def test_an_unknown_integration_is_refused(db: Session, owner_user: User) -> None:
    with pytest.raises(AiActionValidationError, match="gibt es nicht"):
        _werkzeug(db, owner_user, "read_hoster_integration_guide", integration_id=9999)


def test_a_missing_integration_id_names_where_to_get_it(
    db: Session, owner_user: User
) -> None:
    """Eine Ablehnung, die den Weg nennt, beendet die Rateschleife nach einer
    Runde."""
    with pytest.raises(AiActionValidationError, match="read_hoster_setup"):
        _werkzeug(db, owner_user, "read_hoster_integration_guide", integration_id=None)


# ── Schreibwerkzeuge ─────────────────────────────────────────────────────

def _gespraech(db: Session, user: User) -> AiConversation:
    """Ein Benutzer hat genau ein Gespraech (`UNIQUE(user_id)`) — der Einzelchat.

    Ein zweites anzulegen bricht mit einem IntegrityError, der nach einem Fehler
    im Vorschlagspfad aussieht und keiner ist.
    """
    row = db.query(AiConversation).filter(AiConversation.user_id == user.id).first()
    if row is None:
        row = AiConversation(id=str(uuid4()), user_id=user.id, server_id=None, title="Shop")
        db.add(row)
        db.flush()
    return row


def _vorschlag(db: Session, user: User, tool: str, **args):
    return ai_proposal_service.create_proposal(
        db,
        user=user,
        conversation=_gespraech(db, user),
        tool_name=tool,
        arguments={
            "reason": "Testbegruendung",
            "expected_effect": "Testwirkung",
            **args,
        },
        correlation_id=str(uuid4()),
    )


def _ausfuehren(db: Session, user: User, proposal):
    db.commit()
    _, token = ai_proposal_service.confirm_proposal(
        db, proposal_id=proposal.id, user=user
    )
    return ai_proposal_service.execute_proposal(
        db, proposal_id=proposal.id, user=user, confirmation_token=token
    )


def test_creating_an_integration_shows_the_key_once_and_stores_it_nowhere(
    db: Session, owner_user: User, dienstbenutzer: User
) -> None:
    """Die tragende Zusage dieses Vorhabens.

    Der Klartextschluessel geht ueber `AiActionExecuteResponse.result` an die
    Karte und **nirgendwo sonst** hin. Der Test prueft das dort, wo es zaehlt:
    in `preview_json` (Klartext in der Datenbank, geht bei jedem `listActions()`
    erneut an den Browser) und in `ai_tool_results.result_json`. Auf die
    Redaktion ist kein Verlass — `token_urlsafe(32)` passt auf keines ihrer
    Muster.
    """
    vorschlag = _vorschlag(
        db, owner_user, "propose_hoster_integration",
        name="Mein Shop",
        slug="mein-shop",
        service_user_id=dienstbenutzer.id,
        webhook_url="https://shop.example/hook",
        terminate_grace_days=14,
    )
    vorschau = json.loads(vorschlag.preview_json)
    # Panel-Tatsachen statt Modellprosa: der Name des Dienstbenutzers steht
    # aufgeloest da, damit der Bestaetigende ihn lesen kann.
    assert vorschau["service_user"] == dienstbenutzer.username
    assert vorschau["webhook_secret_will_be_created"] is True

    _, ergebnis = _ausfuehren(db, owner_user, vorschlag)
    schluessel = {eintrag["value"] for eintrag in ergebnis["secrets"]}
    assert len(schluessel) == 2  # API-Key und Webhook-Secret

    db.refresh(vorschlag)
    for geheimnis in schluessel:
        assert geheimnis not in (vorschlag.preview_json or "")
        assert geheimnis not in json.dumps(ergebnis["integration_id"])
        for row in db.query(AiToolResult).all():
            assert geheimnis not in (row.result_json or "")

    integration = (
        db.query(HosterIntegration)
        .filter(HosterIntegration.id == ergebnis["integration_id"])
        .first()
    )
    assert integration.slug == "mein-shop"
    assert integration.terminate_grace_days == 14
    # Ein Ziel ohne Secret stellt nichts zu — beides entsteht in einem Zug.
    assert integration.webhook_secret_encrypted is not None


def test_a_taken_slug_is_refused_before_anyone_confirms(
    db: Session, owner_user: User, dienstbenutzer: User, integration
) -> None:
    """Ein Vorschlag, der erst nach der Bestaetigung scheitert, hat den Menschen
    umsonst zustimmen lassen."""
    with pytest.raises(AiActionValidationError, match="bereits vergeben"):
        _vorschlag(
            db, owner_user, "propose_hoster_integration",
            name="Zweiter Shop",
            slug="testshop",
            service_user_id=dienstbenutzer.id,
            terminate_grace_days=7,
        )


def test_a_tariff_role_is_created_without_any_permission(
    db: Session, owner_user: User
) -> None:
    """Die leere Rechteliste ist der Sicherheitsentwurf, nicht eine Sparmassnahme.

    Eine Rolle ohne Permission-Keys kann ueber `ensure_actor_may_grant_role` nie
    mehr vergeben, als der Akteur selbst hat — die Fehlmenge ist immer leer.
    Eskalation ist damit strukturell ausgeschlossen statt durch eine Pruefung
    verhindert.
    """
    from services import ai_limit_service, role_service

    vorschlag = _vorschlag(
        db, owner_user, "propose_ai_tarif_role",
        name="tarif-pro",
        description="Shop-Tarif Pro",
        daily_token_limit=200_000,
    )
    vorschau = json.loads(vorschlag.preview_json)
    assert vorschau["permissions"] == []

    _, ergebnis = _ausfuehren(db, owner_user, vorschlag)
    rolle = db.query(Role).filter(Role.id == ergebnis["role_id"]).first()
    assert rolle.name == "tarif-pro"
    assert role_service.role_permission_keys(db, rolle.id) == []
    grenze = ai_limit_service.get_role_limit(db, rolle.id)
    assert grenze.daily_token_limit == 200_000
    # `None` heisst unbegrenzt und ist eine Aussage, kein fehlender Wert.
    assert grenze.monthly_token_limit is None


def test_the_ai_path_is_never_weaker_than_the_panel_button(db: Session) -> None:
    """Die Akteursschranke gilt auch fuer einen bestaetigten KI-Vorschlag.

    Sie stand frueher nur im Router. Ein zweiter Weg zu `upsert_product` haette
    sie umgangen — und `ensure_role_is_delegatable` allein prueft gegen den
    Dienstbenutzer, den der Akteur selbst aussucht.
    """
    maechtiger_dienst = _mit_rechten(
        db, "dienst-gross", ("servers.create", "servers.delete")
    )
    row, _ = hoster_integration_service.create_integration(
        db,
        name="Grossshop",
        slug="grossshop",
        enabled=True,
        service_user_id=maechtiger_dienst.id,
        webhook_url=None,
        terminate_grace_days=7,
    )
    zu_maechtig = Role(name="tarif-mit-loeschrecht", is_system=False)
    db.add(zu_maechtig)
    db.flush()
    db.add(RolePermission(role_id=zu_maechtig.id, permission_key="servers.delete"))
    db.commit()

    # Der Akteur darf Produkte pflegen, aber `servers.delete` hat er nicht.
    akteur = _mit_rechten(
        db, "einrichter2", ("panel.hoster.read", "panel.hoster.write")
    )
    with pytest.raises(AiActionValidationError, match="Rechte du selbst besitzt"):
        _vorschlag(
            db, akteur, "propose_hoster_product",
            integration_id=row.id,
            external_product_key="GAME-X",
            game_type="minecraft_vanilla",
            role_id=zu_maechtig.id,
        )


def test_a_product_proposal_shows_the_role_by_name_and_rights(
    db: Session, owner_user: User, integration
) -> None:
    """"Welche Rolle bekommt jeder Kaeufer" ist die Frage dieses Vorschlags.

    Eine Rollennummer beantwortet sie nicht — und die Karte zeigte sonst nur
    `reason` und `expected_effect`, also Text des Modells.
    """
    row, _ = integration
    tarif = Role(name="tarif-klein", is_system=False)
    db.add(tarif)
    db.commit()

    vorschlag = _vorschlag(
        db, owner_user, "propose_hoster_product",
        integration_id=row.id,
        external_product_key="GAME-MC-BASIC",
        game_type="minecraft_vanilla",
        ram_limit_mb=4096,
        role_id=tarif.id,
    )
    vorschau = json.loads(vorschlag.preview_json)
    assert vorschau["role"] == "tarif-klein"
    assert vorschau["role_permissions"] == []
    assert vorschau["path"] == "testshop/GAME-MC-BASIC"

    _, ergebnis = _ausfuehren(db, owner_user, vorschlag)
    produkt = (
        db.query(HosterProduct)
        .filter(HosterProduct.id == ergebnis["product_id"])
        .first()
    )
    assert produkt.role_id == tarif.id
    assert produkt.ram_limit_mb == 4096


def test_an_unknown_blueprint_is_refused_at_proposal_time(
    db: Session, owner_user: User, integration
) -> None:
    row, _ = integration
    with pytest.raises(AiActionValidationError, match="Unbekannter Blueprint"):
        _vorschlag(
            db, owner_user, "propose_hoster_product",
            integration_id=row.id,
            external_product_key="GAME-Y",
            game_type="gibt-es-nicht",
        )


def test_what_the_ai_built_is_what_the_shop_can_actually_use(
    db: Session, owner_user: User, dienstbenutzer: User
) -> None:
    """Der eigentliche Beweis: die KI-Einrichtung traegt eine echte Bestellung.

    Alles davor prueft, dass Zeilen richtig geschrieben werden. Hier wird der
    Weg gegangen, den der Shop geht — mit dem Schluessel, den die KI-Karte genau
    einmal gezeigt hat, und mit der Produktkennung aus dem Einbindungsblock. Ein
    Block, der stimmt, und eine Anbindung, die daran scheitert, waeren zwei
    verschiedene Dinge; nur dieser Test haelt sie zusammen.
    """
    _, integration_ergebnis = _ausfuehren(
        db, owner_user,
        _vorschlag(
            db, owner_user, "propose_hoster_integration",
            name="KI-Shop", slug="ki-shop",
            service_user_id=dienstbenutzer.id,
            webhook_url="https://shop.example/hook",
            terminate_grace_days=30,
        ),
    )
    api_key = next(
        eintrag["value"] for eintrag in integration_ergebnis["secrets"]
        if eintrag["label"] == "API-Key"
    )
    _ausfuehren(
        db, owner_user,
        _vorschlag(
            db, owner_user, "propose_hoster_product",
            integration_id=integration_ergebnis["integration_id"],
            external_product_key="TARIF-PRO",
            game_type="minecraft_vanilla",
            ram_limit_mb=8192,
        ),
    )

    # Genau die zwei Schritte, mit denen jede eingehende Bestellung beginnt.
    integration = hoster_integration_service.authenticate(db, api_key)
    assert integration.id == integration_ergebnis["integration_id"]

    block = _werkzeug(
        db, owner_user, "read_hoster_integration_guide",
        integration_id=integration.id,
    )
    for kennung in block["product_keys"]:
        produkt = hoster_integration_service.get_product(db, integration, kennung)
        assert produkt.enabled is True
        assert produkt.game_type == "minecraft_vanilla"


def test_the_three_tools_never_run_autonomously() -> None:
    """Bei der Integration ist das eine Funktionsbedingung, keine Vorsicht.

    Im autonomen Modus ruft `ai_stream_service` `execute_autonomously` und
    verwirft dessen Rueckgabewert — genau darin steckt der einmalige
    Klartextschluessel. Eine autonom angelegte Integration waere unbenutzbar und
    nur ueber eine Rotation zu retten.
    """
    from services.ai_tool_registry import ALWAYS_CONFIRM_TOOLS

    assert {
        "propose_hoster_integration",
        "propose_hoster_product",
        "propose_ai_tarif_role",
    } <= ALWAYS_CONFIRM_TOOLS
