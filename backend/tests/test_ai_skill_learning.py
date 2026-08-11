"""Die KI kennt Skills, nutzt sie und legt selbst welche an.

Vor dieser Phase war der Zustand messbar folgender: null Erwaehnungen im
Systemprompt, null Skill-Werkzeuge im Katalog. Sechs mitgelieferte
Vorgehensweisen haetten bereitliegen koennen und waeren nie angefasst worden.

Der Kern dessen, was der Betreiber verlangt hat — *"die KI erstellt Skills
automatisch, ohne dass der User was machen muss"* — steht in
`test_learning_needs_no_user_interaction`. Alles andere hier sind die Grenzen,
innerhalb derer das vertretbar ist.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from models import AiConversation, AiSkill, Role, RolePermission, Team, User
from services import ai_action_service, ai_learning_policy, ai_skill_service, team_service
from services.ai_action_errors import AiActionValidationError
from services.ai_context_service import build_provider_messages
from services.auth_service import AuthService
from services.role_service import set_user_roles


def _user(db: Session, name: str) -> User:
    user = AuthService.create_user(db, name, f"{name}@test.de", "LearnPass123!")
    user.email_verified = True
    db.commit()
    db.refresh(user)
    return user


def _allow(db: Session, user: User, *keys: str) -> None:
    role = Role(name=f"lern-{user.username}", description=None, is_system=False)
    db.add(role)
    db.flush()
    for key in keys:
        db.add(RolePermission(role_id=role.id, permission_key=key))
    db.commit()
    set_user_roles(db, user, [role.id])
    db.commit()


def _team(db: Session, owner: User, *members: User) -> Team:
    team = team_service.create_team(db, user=owner, name=f"team-{owner.username}")
    for member in members:
        team_service.add_member(
            db, team=team, user=owner, new_user_id=member.id,
            can_manage_skills=True, can_manage_memory=True,
        )
    return team


def _conversation(db: Session, user: User) -> AiConversation:
    conversation = AiConversation(
        id=str(uuid4()), user_id=user.id, server_id=None, title="Skills"
    )
    db.add(conversation)
    db.commit()
    return conversation


def _system_prompt(db: Session, user: User, query: str = "") -> str:
    messages = build_provider_messages(db, _conversation(db, user), query)
    return next(item["content"] for item in messages if item["role"] == "system")


@pytest.fixture(autouse=True)
def _reset_policy():
    ai_skill_service.reset_shipped_cache_for_tests()
    yield
    ai_skill_service.reset_shipped_cache_for_tests()


# ── Die KI weiss, dass es Skills gibt ─────────────────────────────────


def test_the_system_prompt_lists_available_skills(db: Session, regular_user: User) -> None:
    """Ohne dieses Verzeichnis waeren die mitgelieferten Skills totes Gewicht."""
    _allow(db, regular_user, "ai.chat.use", "ai.skills.use")

    prompt = _system_prompt(db, regular_user)

    assert "Verfuegbare Skills" in prompt
    assert "server-nicht-erreichbar" in prompt
    assert "read_skill" in prompt


def test_the_index_carries_descriptions_but_no_bodies(
    db: Session, regular_user: User
) -> None:
    """Stufe eins kostet rund hundert Tokens je Skill, nicht den ganzen Text."""
    _allow(db, regular_user, "ai.chat.use", "ai.skills.use")

    prompt = _system_prompt(db, regular_user)

    shipped = ai_skill_service.shipped_skills()["server-nicht-erreichbar"]
    assert shipped.description[:40] in prompt
    # Eine Zeile, die nur im Fliesstext vorkommt, darf hier nicht auftauchen.
    assert "Hairpin-NAT" not in prompt


def test_without_the_permission_there_is_no_index(db: Session, regular_user: User) -> None:
    _allow(db, regular_user, "ai.chat.use")

    assert "Verfuegbare Skills" not in _system_prompt(db, regular_user)


def test_a_foreign_team_skill_never_reaches_the_prompt(
    db: Session, regular_user: User
) -> None:
    stranger = _user(db, "fremder")
    _allow(db, stranger, "ai.chat.use", "ai.skills.use")
    _allow(db, regular_user, "ai.chat.use", "ai.skills.use", "teams.create")
    team = _team(db, regular_user)
    ai_skill_service.upsert_skill(
        db, user=regular_user, skill_key="intern", name="Interne Vorgehensweise",
        description="Etwas, das ausschliesslich dieses Team betrifft und sonst niemanden.",
        body="Vertraulich.", team_id=team.id,
    )

    # Auf den Namen pruefen, nicht auf den Schluessel: "intern" steckt auch
    # in "interne Pfade" weiter oben im Prompt.
    assert "Interne Vorgehensweise" in _system_prompt(db, regular_user)
    assert "Interne Vorgehensweise" not in _system_prompt(db, stranger)


# ── read_skill ────────────────────────────────────────────────────────


def test_read_skill_loads_the_body(db: Session, regular_user: User) -> None:
    _allow(db, regular_user, "ai.skills.use")

    result = ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="read_skill",
        arguments={"skill_key": "server-nicht-erreichbar"},
    )

    assert result["skill_key"] == "server-nicht-erreichbar"
    assert "check_server_reachability" in result["body"]
    # Ein Skill ist eine Anleitung, kein Befehl — und ein Team-Skill ist
    # woertlich Text, den ein anderer Mensch geschrieben hat.
    assert result["untrusted"] is True


def test_read_skill_refuses_a_foreign_team_skill(db: Session, regular_user: User) -> None:
    stranger = _user(db, "fremder")
    _allow(db, stranger, "ai.skills.use")
    _allow(db, regular_user, "ai.skills.use", "teams.create")
    team = _team(db, regular_user)
    ai_skill_service.upsert_skill(
        db, user=regular_user, skill_key="intern", name="Interne Vorgehensweise",
        description="Etwas, das ausschliesslich dieses Team betrifft und sonst niemanden.",
        body="Vertraulich.", team_id=team.id,
    )

    with pytest.raises(AiActionValidationError):
        ai_action_service.execute_read_tool(
            db, user=stranger, tool_name="read_skill", arguments={"skill_key": "intern"},
        )


def test_read_skill_requires_the_permission(db: Session, regular_user: User) -> None:
    with pytest.raises(AiActionValidationError):
        ai_action_service.execute_read_tool(
            db, user=regular_user, tool_name="read_skill",
            arguments={"skill_key": "server-nicht-erreichbar"},
        )


# ── learn_skill: der Kern des Versprechens ────────────────────────────


def test_learning_needs_no_user_interaction(db: Session, regular_user: User) -> None:
    """Kein Formular, kein Knopf, keine Bestaetigung.

    Vertretbar ist das, weil Prosa nichts ausfuehrt: der Skill aendert die
    Herangehensweise des Modells, nicht seine Rechte. Jeder daraus folgende
    Schritt laeuft weiterhin ueber RBAC und die Bestaetigungspflicht.
    """
    _allow(db, regular_user, "ai.skills.use")

    result = ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="learn_skill",
        arguments={
            "skill_key": "valheim-ram",
            "name": "Valheim braucht mindestens 6 GB",
            "description": "Wenn ein Valheim-Server ohne Fehlermeldung endet, zuerst den Arbeitsspeicher pruefen.",
            "body": "Exit code 137 plus abrupt endender Log heisst OOM-Kill.",
            "scope": "team",
        },
    )

    assert result["learned"] is True
    assert result["status"] == "active"
    # Und er steht sofort im Verzeichnis — ohne dass jemand etwas bestaetigt hat.
    assert "valheim-ram" in {
        view.skill_key for view in ai_skill_service.visible_skills(db, regular_user)
    }


def test_learning_lands_in_the_single_real_team(db: Session, regular_user: User) -> None:
    colleague = _user(db, "kollege")
    _allow(db, colleague, "ai.skills.use")
    _allow(db, regular_user, "ai.skills.use", "teams.create")
    team = _team(db, regular_user, colleague)

    ai_action_service.execute_read_tool(
        db, user=colleague, tool_name="learn_skill",
        arguments={
            "skill_key": "hausregel", "name": "Unsere Hausregel",
            "description": "Wie dieses Team mit Wartungsfenstern umgeht und wen es vorher informiert.",
            "body": "Erst ankuendigen, dann neustarten.", "scope": "team",
        },
    )

    row = db.query(AiSkill).filter(AiSkill.skill_key == "hausregel").one()
    assert row.team_id == team.id
    # Der Kollege sieht ihn auch — das ist der Zweck.
    assert "hausregel" in {
        view.skill_key for view in ai_skill_service.visible_skills(db, regular_user)
    }


def test_learning_asks_back_when_several_teams_are_possible(
    db: Session, regular_user: User
) -> None:
    colleague = _user(db, "kollege")
    other_owner = _user(db, "zweiter")
    _allow(db, colleague, "ai.skills.use")
    _allow(db, regular_user, "ai.skills.use", "teams.create")
    _allow(db, other_owner, "ai.skills.use", "teams.create")
    _team(db, regular_user, colleague)
    _team(db, other_owner, colleague)

    result = ai_action_service.execute_read_tool(
        db, user=colleague, tool_name="learn_skill",
        arguments={
            "skill_key": "unklar", "name": "Unklar",
            "description": "Etwas, das in mehreren Teams gelten koennte und deshalb eine Rueckfrage braucht.",
            "body": "Inhalt.", "scope": "team",
        },
    )

    assert result["learned"] is False
    assert "mehreren Teams" in result["ask_user"]
    assert db.query(AiSkill).filter(AiSkill.skill_key == "unklar").count() == 0


# ── Global: sofort oder in die Warteschlange ──────────────────────────


def _learn_global(db: Session, user: User, key: str) -> dict:
    return ai_action_service.execute_read_tool(
        db, user=user, tool_name="learn_skill",
        arguments={
            "skill_key": key, "name": "Allgemeingueltig",
            "description": "Eine Erkenntnis, die bei jedem Betreiber gleichermassen zutrifft.",
            "body": "Gilt ueberall.", "scope": "global",
        },
    )


def test_staff_learns_globally_with_immediate_effect(
    db: Session, regular_user: User
) -> None:
    ai_learning_policy.set_policy("review")
    _allow(db, regular_user, "ai.skills.use", "ai.skills.manage")

    result = _learn_global(db, regular_user, "sofort")

    assert result["status"] == "active"
    assert "sofort" in {
        view.skill_key for view in ai_skill_service.visible_skills(db, regular_user)
    }


def test_a_customer_learns_globally_into_the_queue(db: Session, regular_user: User) -> None:
    """Der Lerneffekt aus Kundengespraechen geht nicht verloren — wirkt aber nicht.

    Ohne die Warteschlange waere das ein offener Weg, ueber den ein Kunde die
    KI zu einem sofort wirksamen Text im Kontext *aller* anderen Kunden
    ueberreden koennte.
    """
    ai_learning_policy.set_policy("review")
    _allow(db, regular_user, "ai.skills.use")

    result = _learn_global(db, regular_user, "wartend")

    assert result["status"] == "pending"
    assert result["note"] is not None
    assert "wartend" not in {
        view.skill_key for view in ai_skill_service.visible_skills(db, regular_user)
    }
    assert [row.skill_key for row in ai_skill_service.pending_skills(db)] == ["wartend"]


def test_a_waiting_skill_does_not_hide_a_shipped_one(
    db: Session, regular_user: User
) -> None:
    """Die Warteschlange darf keine mitgelieferte Anleitung abschalten.

    Der Weg dorthin war offen und brauchte keine Berechtigung: unter der
    Lernpolitik `review` legt jedes Kundengespraech wartende Zeilen an, und
    `visible_skills` blendete die gleichnamige mitgelieferte Datei aus, sobald
    eine solche Zeile existierte. Ein einziger `learn_skill`-Aufruf unter dem
    Schluessel `portkonflikt` haette damit die Portkonflikt-Anleitung fuer
    **jeden** Benutzer des Panels entfernt — ohne dass irgendjemand etwas
    freigegeben oder auch nur gesehen haette.

    Abschalten ist eine Handlung des Betreibers. Warten ist keine.
    """
    ai_learning_policy.set_policy("review")
    _allow(db, regular_user, "ai.skills.use")
    ai_skill_service.reset_shipped_cache_for_tests()
    assert "portkonflikt" in ai_skill_service.shipped_skills()

    ergebnis = _learn_global(db, regular_user, "portkonflikt")
    assert ergebnis["status"] == "pending"

    sichtbar = {
        view.skill_key: view for view in ai_skill_service.visible_skills(db, regular_user)
    }
    assert "portkonflikt" in sichtbar
    # Und zwar unveraendert die mitgelieferte, nicht die wartende Fassung.
    assert sichtbar["portkonflikt"].scope == "shipped"
    _, body = ai_skill_service.read_body(db, regular_user, "portkonflikt")
    assert body != "Gilt ueberall."


def test_a_waiting_skill_does_not_devalue_a_released_one(
    db: Session, regular_user: User
) -> None:
    """Dasselbe eine Ebene tiefer: der Update-Zweig setzte den Status mit.

    Ein bereits freigegebener globaler Skill wurde beim Ueberschreiben aus
    einem Kundengespraech auf `pending` zurueckgestuft — und war damit fuer
    alle weg, obwohl der Betreiber ihn genau dafuer freigegeben hatte.
    Ablehnen ist hier richtiger als Ersetzen: die Warteschlange ist fuer neue
    Erkenntnisse da, nicht zum Zuruecknehmen bestehender.
    """
    ai_learning_policy.set_policy("review")
    betreiber = _user(db, "betreiber-skills")
    _allow(db, betreiber, "ai.skills.use", "ai.skills.manage")
    _allow(db, regular_user, "ai.skills.use")

    assert _learn_global(db, betreiber, "geteilt")["status"] == "active"

    with pytest.raises(AiActionValidationError) as fehler:
        _learn_global(db, regular_user, "geteilt")
    assert "team" in str(fehler.value).lower()

    # Der freigegebene Skill steht unveraendert und wirkt weiter.
    zeile = db.query(AiSkill).filter(AiSkill.skill_key == "geteilt").one()
    assert zeile.status == "active"
    assert "geteilt" in {
        view.skill_key for view in ai_skill_service.visible_skills(db, regular_user)
    }


def test_instant_policy_lets_everyone_write_globally(
    db: Session, regular_user: User
) -> None:
    ai_learning_policy.set_policy("instant")
    _allow(db, regular_user, "ai.skills.use")

    assert _learn_global(db, regular_user, "sofort")["status"] == "active"


def test_disabled_policy_tells_the_model_instead_of_failing(
    db: Session, regular_user: User
) -> None:
    """Ein Werkzeug, das immer scheitert, laesst das Modell mehrfach versuchen."""
    ai_learning_policy.set_policy("off")
    _allow(db, regular_user, "ai.skills.use", "ai.skills.manage")

    result = _learn_global(db, regular_user, "verboten")

    assert result["learned"] is False
    assert "abgeschaltet" in result["reason"]
    assert db.query(AiSkill).filter(AiSkill.skill_key == "verboten").count() == 0


def test_disabled_policy_removes_global_from_the_tool_schema(db: Session) -> None:
    """Was nicht geht, wird gar nicht erst angeboten."""
    ai_learning_policy.set_policy("off")
    definitions = ai_action_service.provider_tool_definitions()
    learn = next(
        item for item in definitions if item["function"]["name"] == "learn_skill"
    )
    assert learn["function"]["parameters"]["properties"]["scope"]["enum"] == ["team"]

    ai_learning_policy.set_policy("review")
    definitions = ai_action_service.provider_tool_definitions()
    learn = next(
        item for item in definitions if item["function"]["name"] == "learn_skill"
    )
    assert set(learn["function"]["parameters"]["properties"]["scope"]["enum"]) == {
        "team", "global",
    }


def test_learning_requires_the_skill_permission(db: Session, regular_user: User) -> None:
    with pytest.raises(AiActionValidationError):
        ai_action_service.execute_read_tool(
            db, user=regular_user, tool_name="learn_skill",
            arguments={
                "skill_key": "ohne-recht", "name": "Ohne Recht",
                "description": "Ein Versuch ohne die noetige Berechtigung fuer Skills.",
                "body": "Inhalt.", "scope": "team",
            },
        )


# ── Skills verwalten ──────────────────────────────────────────────────


def test_the_ai_can_delete_a_skill_it_learned(db: Session, regular_user: User) -> None:
    """Was die KI angelegt hat, kann sie auch wieder wegnehmen."""
    _allow(db, regular_user, "ai.skills.use")
    ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="learn_skill",
        arguments={
            "skill_key": "ueberholt", "name": "Ueberholte Vorgehensweise",
            "description": "Etwas, das sich als falsch herausgestellt hat und weg soll.",
            "body": "Falsch.", "scope": "team",
        },
    )
    assert "ueberholt" in {
        view.skill_key for view in ai_skill_service.visible_skills(db, regular_user)
    }

    result = ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="forget_skill",
        arguments={"skill_key": "ueberholt"},
    )

    assert result["forgotten"] is True
    assert "ueberholt" not in {
        view.skill_key for view in ai_skill_service.visible_skills(db, regular_user)
    }


def test_a_shipped_skill_cannot_be_deleted(db: Session, regular_user: User) -> None:
    """Eine Datei auf der Platte zu "loeschen" waere ein Versprechen, das das
    naechste Update zuruecknimmt.

    Stattdessen bekommt das Modell den richtigen Weg genannt: unter demselben
    Schluessel einen eigenen anlegen. Der ueberschreibt die Vorgabe dauerhaft.
    """
    ai_skill_service.reset_shipped_cache_for_tests()
    _allow(db, regular_user, "ai.skills.use")

    result = ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="forget_skill",
        arguments={"skill_key": "portkonflikt"},
    )

    assert result["forgotten"] is False
    assert "learn_skill" in result["reason"]
    assert "portkonflikt" in {
        view.skill_key for view in ai_skill_service.visible_skills(db, regular_user)
    }


def test_a_foreign_team_skill_cannot_be_deleted(db: Session, regular_user: User) -> None:
    """Ein erratener Schluessel darf auch nichts zerstoeren, nicht nur nichts oeffnen."""
    stranger = _user(db, "fremder")
    _allow(db, stranger, "ai.skills.use")
    _allow(db, regular_user, "ai.skills.use", "teams.create")
    team = _team(db, regular_user)
    ai_skill_service.upsert_skill(
        db, user=regular_user, skill_key="intern", name="Interne Vorgehensweise",
        description="Etwas, das ausschliesslich dieses Team betrifft und sonst niemanden.",
        body="Vertraulich.", team_id=team.id,
    )

    with pytest.raises(AiActionValidationError):
        ai_action_service.execute_read_tool(
            db, user=stranger, tool_name="forget_skill",
            arguments={"skill_key": "intern"},
        )
    assert db.query(AiSkill).filter(AiSkill.skill_key == "intern").count() == 1


def test_changing_a_skill_keeps_a_single_entry(db: Session, regular_user: User) -> None:
    """Aendern laeuft ueber denselben Schluessel, nicht ueber loeschen und neu."""
    _allow(db, regular_user, "ai.skills.use")
    for body in ("Erste Fassung.", "Zweite, bessere Fassung."):
        ai_action_service.execute_read_tool(
            db, user=regular_user, tool_name="learn_skill",
            arguments={
                "skill_key": "wandelbar", "name": "Wandelbar",
                "description": "Eine Vorgehensweise, die im Laufe der Zeit besser wird.",
                "body": body, "scope": "team",
            },
        )

    rows = db.query(AiSkill).filter(AiSkill.skill_key == "wandelbar").all()
    assert len(rows) == 1
    assert rows[0].body == "Zweite, bessere Fassung."
