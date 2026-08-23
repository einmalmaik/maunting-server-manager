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
        team_service.invite_member(
            db, team=team, user=owner, new_user_id=member.id,
            can_manage_skills=True, can_manage_memory=True,
        )
        team_service.accept_invitation(db, user=member, team_id=team.id)
    return team


def _conversation(db: Session, user: User) -> AiConversation:
    conversation = AiConversation(
        id=str(uuid4()), user_id=user.id, server_id=None, title="Skills"
    )
    db.add(conversation)
    db.commit()
    return conversation


def _frueher_kontext(
    db: Session, user: User, query: str = "", unbeaufsichtigt: bool = False
) -> str:
    """Systemprompt plus Skill-Verzeichnis — die beiden fruehen Bloecke.

    Das Verzeichnis steht seit seinem Umzug nicht mehr **im** Systemprompt,
    sondern als eigene, als Daten gekennzeichnete `user`-Nachricht direkt
    dahinter (`ai_context_service._skill_index_message`). Fuer die Zusagen
    dieser Tests — wer sieht welchen Skill, und wann gar keinen — zaehlt
    beides zusammen; wo genau es steht, prueft `test_ai_prompt_caching`.
    """
    messages = build_provider_messages(
        db, _conversation(db, user), query, unbeaufsichtigt=unbeaufsichtigt
    )
    teile = [messages[0]["content"]]
    if len(messages) > 1 and "Skill-Verzeichnis" in str(messages[1].get("content", "")):
        teile.append(messages[1]["content"])
    return "\n".join(teile)


@pytest.fixture(autouse=True)
def _reset_policy():
    ai_skill_service.reset_shipped_cache_for_tests()
    yield
    ai_skill_service.reset_shipped_cache_for_tests()


# ── Die KI weiss, dass es Skills gibt ─────────────────────────────────


def test_the_system_prompt_lists_available_skills(db: Session, regular_user: User) -> None:
    """Ohne dieses Verzeichnis waeren die mitgelieferten Skills totes Gewicht."""
    _allow(db, regular_user, "ai.chat.use", "ai.skills.use")

    prompt = _frueher_kontext(db, regular_user)

    assert "Skill-Verzeichnis" in prompt
    assert "server-nicht-erreichbar" in prompt
    assert "read_skill" in prompt


def test_the_index_carries_descriptions_but_no_bodies(
    db: Session, regular_user: User
) -> None:
    """Stufe eins kostet rund hundert Tokens je Skill, nicht den ganzen Text."""
    _allow(db, regular_user, "ai.chat.use", "ai.skills.use")

    prompt = _frueher_kontext(db, regular_user)

    shipped = ai_skill_service.shipped_skills()["server-nicht-erreichbar"]
    assert shipped.description[:40] in prompt
    # Eine Zeile, die nur im Fliesstext vorkommt, darf hier nicht auftauchen.
    assert "Hairpin-NAT" not in prompt


def test_without_the_permission_there_is_no_index(db: Session, regular_user: User) -> None:
    _allow(db, regular_user, "ai.chat.use")

    assert "Skill-Verzeichnis" not in _frueher_kontext(db, regular_user)


def test_a_run_without_witnesses_is_not_asked_to_read_what_it_cannot_read(
    db: Session, regular_user: User
) -> None:
    """Ein Heilungs- oder Aufgabenlauf sieht kein Skill-Verzeichnis.

    Der Block nennt die sechs Störungsdrehbücher beim Namen und fordert auf,
    das passende mit ``read_skill`` zu lesen — und genau dieses Werkzeug steht
    weder in ``GUARDIAN_HEILUNG_TOOLS`` noch in ``AUFGABEN_LESEN``. Ausgerechnet
    der Lauf, für den diese Drehbücher geschrieben wurden, bekam also eine
    Aufforderung, der er nicht nachkommen kann: eine verlorene Runde, und in
    jeder weiteren Runde die Tokens des Verzeichnisses.

    Der Freigeber einer Heilung ist üblicherweise der Owner, und
    ``has_global_permission`` sagt für ihn zu allem ja — das Recht
    ``ai.skills.use`` allein hat den Block also nie zurückgehalten.
    """
    _allow(db, regular_user, "ai.chat.use", "ai.skills.use")
    # Dieselbe Unterhaltung für beide Prompts: ein Benutzer hat genau eine.
    conversation = _conversation(db, regular_user)

    def gesamter_kontext(unbeaufsichtigt: bool) -> str:
        nachrichten = build_provider_messages(
            db, conversation, unbeaufsichtigt=unbeaufsichtigt
        )
        # Über **alle** Nachrichten, nicht nur den Systemprompt: seit dem
        # Umzug in eine eigene Nachricht dürfte das Verzeichnis sonst dort
        # weiterleben, und dieser Test sähe es nicht.
        return "\n".join(str(item.get("content", "")) for item in nachrichten)

    ohne_zeugen = gesamter_kontext(True)
    assert "Skill-Verzeichnis" not in ohne_zeugen
    assert "server-nicht-erreichbar" not in ohne_zeugen
    assert "read_skill" not in ohne_zeugen
    # Der Chat des Menschen daneben behält es unverändert.
    assert "Skill-Verzeichnis" in gesamter_kontext(False)


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
    assert "Interne Vorgehensweise" in _frueher_kontext(db, regular_user)
    assert "Interne Vorgehensweise" not in _frueher_kontext(db, stranger)


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


def test_the_same_key_in_two_scopes_is_never_deleted_by_guessing(
    db: Session, regular_user: User
) -> None:
    """Zwei Bereiche, ein Schluessel — geloescht wird erst nach der Rueckfrage.

    `forget_skill` loeste den Schluessel frueher ueber `read_body` auf, also
    ueber die Sichtbarkeitsueberlagerung. Die kennt je Schluessel einen
    Gewinner, und welcher das bei Gleichstand ist, entscheidet die
    Zeilenreihenfolge der Datenbank. Wer "vergiss backup-routine" sagt und den
    Skill seines Teams meint, loeschte damit womoeglich die panelweite Zeile —
    die fuer jeden Kunden des Betreibers gilt — waehrend die gemeinte stehen
    blieb. Lesen darf unscharf sein, Loeschen nicht: es ist die eine Stelle,
    an der ein Fehlgriff nicht zurueckzunehmen ist.
    """
    _allow(db, regular_user, "ai.skills.use", "ai.skills.manage", "teams.create")
    team = _team(db, regular_user)
    for bereich in (None, team.id):
        ai_skill_service.upsert_skill(
            db, user=regular_user, skill_key="backup-routine",
            name="Backup-Routine",
            description="Wie ein Backup geprueft wird, bevor man sich darauf verlaesst.",
            # Beide Zeilen gelernt: hier geht es um die Mehrdeutigkeit der
            # Bereiche, nicht um die Herkunft. Menschentext hat einen eigenen
            # Test direkt darunter und wird gar nicht erst geloescht.
            body=f"Fassung fuer Bereich {bereich}.", team_id=bereich, origin="ai",
        )
    assert db.query(AiSkill).filter(AiSkill.skill_key == "backup-routine").count() == 2

    frage = ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="forget_skill",
        arguments={"skill_key": "backup-routine"},
    )

    assert frage["forgotten"] is False
    assert "ask_user" in frage
    assert set(frage["scopes"]) == {"panelweit", team.name}
    # Solange die Frage offen ist, ist nichts angefasst.
    assert db.query(AiSkill).filter(AiSkill.skill_key == "backup-routine").count() == 2

    ergebnis = ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="forget_skill",
        arguments={"skill_key": "backup-routine", "scope": "team", "team": team.name},
    )

    assert ergebnis["forgotten"] is True
    # Der Bereich gehoert ins Ergebnis: ohne ihn kann das Modell nicht
    # berichten, was es geloescht hat, und ein Irrtum faellt nie auf.
    assert ergebnis["scope"] == "team"
    assert ergebnis["bereich"] == team.name
    verbleibend = db.query(AiSkill).filter(AiSkill.skill_key == "backup-routine").all()
    assert [row.team_id for row in verbleibend] == [None]


def test_was_ein_mensch_geschrieben_hat_loescht_die_ki_nicht(
    db: Session, regular_user: User
) -> None:
    """Die Überschreib-Schranke war in zwei Zügen zu umgehen.

    `upsert_skill` weist einen KI-Text ab, der einen von Hand geschriebenen
    Skill ersetzen will. `forget_skill` prüfte die Herkunft nicht — erst
    löschen, dann unter demselben Schlüssel neu lernen, und wo die Hausregel
    des Betreibers stand, stand Modelltext.

    Das ist die teuerste Stelle für eine präparierte Logzeile: ein Skill wirkt
    in jedem künftigen Lauf des Panels, und `upsert_skill` führt bewusst keine
    Versionen — zurückzuholen gibt es nichts.
    """
    _allow(db, regular_user, "ai.skills.use", "ai.skills.manage")
    ai_skill_service.upsert_skill(
        db, user=regular_user, skill_key="hausregel", name="Hausregel",
        description="Die vom Betreiber von Hand geschriebene Vorgehensweise bei Stoerungen.",
        body="Erst den Menschen fragen.", team_id=None, origin="operator",
    )

    ergebnis = ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="forget_skill",
        arguments={"skill_key": "hausregel"},
    )

    # Eine Absage mit Weg, keine Ausnahme: das Modell soll es dem Benutzer
    # sagen und nicht eine Fehlerrunde drehen.
    assert ergebnis["forgotten"] is False
    assert "Mensch" in ergebnis["reason"]
    row = db.query(AiSkill).filter(AiSkill.skill_key == "hausregel").one()
    assert row.origin == "operator"
    assert row.body == "Erst den Menschen fragen."

    # Und der zweite Zug bleibt versperrt, wie er es immer war.
    with pytest.raises(AiActionValidationError):
        ai_action_service.execute_read_tool(
            db, user=regular_user, tool_name="learn_skill",
            arguments={
                "skill_key": "hausregel", "name": "Neue Fassung",
                "description": "Ein gelernter Ersatz, der die Hausregel des Betreibers ablösen will.",
                "body": "Einfach machen.", "scope": "global",
            },
        )


def test_ihre_eigenen_skills_raeumt_die_ki_weiter_selbst_weg(
    db: Session, regular_user: User
) -> None:
    """Die Gegenprobe zur Schranke darüber — sonst wäre sie eine Fessel.

    Gelernt wird ohne Formular und ohne Knopf; weggeräumt muss es genauso
    gehen, sonst sammelt sich überholtes Wissen an, das niemand anfasst. Der
    Test steht hier und nicht nur bei `test_the_ai_can_delete_a_skill_it_learned`,
    weil die Herkunftsprüfung genau diese Zusage brechen könnte.
    """
    _allow(db, regular_user, "ai.skills.use", "ai.skills.manage", "teams.create")
    team = _team(db, regular_user)
    for team_id in (None, team.id):
        ai_skill_service.upsert_skill(
            db, user=regular_user, skill_key=f"gelernt-{team_id}",
            name="Gelernt",
            description="Eine Erkenntnis, die die KI selbst aufgeschrieben hat und wieder verwirft.",
            body="Ueberholt.", team_id=team_id, origin="ai",
        )

        ergebnis = ai_action_service.execute_read_tool(
            db, user=regular_user, tool_name="forget_skill",
            arguments={"skill_key": f"gelernt-{team_id}"},
        )

        assert ergebnis["forgotten"] is True
        assert db.query(AiSkill).filter(
            AiSkill.skill_key == f"gelernt-{team_id}"
        ).count() == 0


def test_einen_abgeschalteten_skill_belebt_die_ki_nicht_in_zwei_zuegen(
    db: Session, regular_user: User
) -> None:
    """Abschalten ist das Gegenmittel gegen einen per Injection gelernten Skill.

    `upsert_skill` lässt den Schalter `enabled` nur von einem Menschen
    anfassen — ein Lernvorgang holt eine abgeschaltete Zeile nicht zurück.
    `forget_skill` prüfte aber allein die Herkunft: die Zeile stammte von der
    KI, sie durfte sie also löschen, und das direkt folgende `learn_skill`
    landete im Anlege-Zweig, wo `enabled` wieder auf ``True`` steht. Das
    Gegenmittel war damit genauso wirkungslos wie vorher, nur einen Zug später
    — und der Betreiber hätte denselben Skill jeden Tag erneut abgeschaltet,
    ohne je zu erfahren, warum er zurückkommt.

    Derselbe Zug geht auch eine Etage tiefer und kommt dort ganz ohne Löschen
    aus: dieselbe Erkenntnis mit scope='team' anlegen. Die neue Zeile entstand
    im Anlege-Zweig mit `enabled=True` und gewann nach `_overlay_rank` gegen
    die abgeschaltete globale. Dafür braucht es nicht einmal `ai.skills.manage`
    oder Kollegen — ohne echtes Team fällt `learning_team` auf das persönliche
    zurück, und der Weg steht jedem mit `ai.skills.use` offen.
    """
    _allow(db, regular_user, "ai.skills.use", "ai.skills.manage")
    row = ai_skill_service.upsert_skill(
        db, user=regular_user, skill_key="vergiftet", name="Erste Fassung",
        description="Eine gelernte Vorgehensweise, die sich als schädlich herausgestellt hat.",
        body="Erste Fassung.", team_id=None, origin="ai",
    )
    kennung = row.id
    ai_skill_service.set_enabled(db, user=regular_user, skill_id=kennung, enabled=False)

    ergebnis = ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="forget_skill",
        arguments={"skill_key": "vergiftet"},
    )

    # Absage mit Weg, keine Ausnahme — wie bei einem von Hand geschriebenen
    # Skill: das Modell soll es dem Benutzer sagen, nicht eine Runde drehen.
    assert ergebnis["forgotten"] is False
    assert "abgeschaltet" in ergebnis["reason"]

    # Der zweite Zug bleibt der KI offen: sie schreibt die Zeile weiter neu.
    # Nur ist es dieselbe Zeile, und die bleibt verdeckt, bis ein Mensch sie
    # zurückholt.
    gelernt = ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="learn_skill",
        arguments={
            "skill_key": "vergiftet", "name": "Zweite Fassung",
            "description": "Dieselbe Erkenntnis, von der KI noch einmal aufgeschrieben.",
            "body": "Zweite Fassung.", "scope": "global",
        },
    )
    assert gelernt["learned"] is True

    frisch = db.query(AiSkill).filter(AiSkill.skill_key == "vergiftet").one()
    assert frisch.id == kennung
    assert frisch.enabled is False
    assert "vergiftet" not in {
        view.skill_key for view in ai_skill_service.visible_skills(db, regular_user)
    }

    # Und derselbe Zug eine Etage tiefer, der eigentliche Rest der Lücke: nicht
    # dieselbe Zeile überschreiben, sondern eine Team-Zeile daneben anlegen.
    # Sie entstünde im Anlege-Zweig mit `enabled=True`, und `_overlay_rank`
    # liesse sie gegen die abgeschaltete globale gewinnen. Der Weg steht jedem
    # mit `ai.skills.use` offen: `learning_team` fällt ohne echtes Team auf das
    # persönliche Ein-Mann-Team zurück, es braucht dafür weder Kollegen noch
    # `ai.skills.manage`.
    with pytest.raises(AiActionValidationError) as exc:
        ai_action_service.execute_read_tool(
            db, user=regular_user, tool_name="learn_skill",
            arguments={
                "skill_key": "vergiftet", "name": "Dritte Fassung",
                "description": "Dieselbe Erkenntnis, diesmal für das Team aufgeschrieben.",
                "body": "Dritte Fassung.", "scope": "team",
            },
        )
    assert "sag dem Benutzer" in str(exc.value)

    assert db.query(AiSkill).filter(AiSkill.skill_key == "vergiftet").count() == 1
    assert "vergiftet" not in {
        view.skill_key for view in ai_skill_service.visible_skills(db, regular_user)
    }

    # Die Gegenprobe, und sie ist der Grund für die Form der Schranke: eine
    # **eingeschaltete** globale KI-Zeile ist keine Entscheidung eines
    # Menschen. Über die legt die KI weiterhin eine engere Team-Fassung, wie
    # sie es immer durfte — die Schranke hängt am Abschalten, nicht daran,
    # dass es den Schlüssel panelweit schon gibt.
    ai_skill_service.upsert_skill(
        db, user=regular_user, skill_key="offen", name="Globale Fassung",
        description="Eine gelernte Vorgehensweise, die niemand abgeschaltet hat.",
        body="Globale Fassung.", team_id=None, origin="ai",
    )

    gelernt = ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="learn_skill",
        arguments={
            "skill_key": "offen", "name": "Team-Fassung",
            "description": "Dieselbe Erkenntnis, für das Team genauer gefasst.",
            "body": "Team-Fassung.", "scope": "team",
        },
    )
    assert gelernt["learned"] is True
    assert gelernt["scope"] == "team"
    sicht = {view.skill_key: view for view in ai_skill_service.visible_skills(db, regular_user)}
    assert sicht["offen"].name == "Team-Fassung"
    assert sicht["offen"].scope == "team"


def test_eine_abgeschaltete_team_zeile_belebt_die_ki_nicht_panelweit(
    db: Session, regular_user: User
) -> None:
    """Dieselbe Wiederbelebung, nur nach oben statt nach unten.

    Die Schranke im Anlege-Zweig war einseitig: sie sah von der neuen Team-Zeile
    auf die panelweite Vorgabe, nie umgekehrt. Wer also einen per Injection
    gelernten Team-Skill abschaltete, hatte ihn mit einem einzigen
    `learn_skill(scope='global')` zurück — der globale Anlege-Zweig prüfte gar
    keine kollidierende Zeile, und verdeckt wird dabei auch nichts: eine
    abgeschaltete Zeile verdeckt selbst nichts (`_overlay`), die neue aktive
    scheint schlicht durch sie hindurch. Danach stand der Text nicht mehr nur im
    Team, sondern im Verzeichnis **jedes** Benutzers des Panels.

    Damit wäre die Zusage von `set_enabled` an den Bereich gebunden gewesen:
    "abgeschaltet" hätte "abgeschaltet, bis die KI eine Etage höher schreibt"
    geheissen. Abschalten ist aber das Gegenmittel gegen einen per Injection
    gelernten Skill; ein Gegenmittel, das ein Zug aushebelt, ist keines.
    """
    ai_learning_policy.set_policy("review")
    # Mit `ai.skills.manage` wirkt die globale Zeile sofort statt zu warten —
    # die schärfste Form des Wegs. Unter der Lernpolitik `instant` reicht dafür
    # `ai.skills.use`, dann steht er jedem Gespräch offen.
    _allow(db, regular_user, "ai.skills.use", "ai.skills.manage")

    gelernt = ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="learn_skill",
        arguments={
            "skill_key": "vergiftet", "name": "Erste Fassung",
            "description": "Eine im Team gelernte Vorgehensweise, die sich als schädlich erwiesen hat.",
            "body": "Erste Fassung.", "scope": "team",
        },
    )
    assert gelernt["learned"] is True
    # Ohne echtes Team fällt `learning_team` auf das persönliche zurück — dafür
    # braucht es weder Kollegen noch einen Teamverwalter.
    zeile = db.query(AiSkill).filter(AiSkill.skill_key == "vergiftet").one()
    assert zeile.team_id is not None

    ai_skill_service.set_enabled(db, user=regular_user, skill_id=zeile.id, enabled=False)
    assert "vergiftet" not in {
        view.skill_key for view in ai_skill_service.visible_skills(db, regular_user)
    }

    with pytest.raises(AiActionValidationError) as exc:
        ai_action_service.execute_read_tool(
            db, user=regular_user, tool_name="learn_skill",
            arguments={
                "skill_key": "vergiftet", "name": "Zweite Fassung",
                "description": "Dieselbe Erkenntnis, diesmal panelweit aufgeschrieben.",
                "body": "Zweite Fassung.", "scope": "global",
            },
        )
    # Dieselbe Antwortform wie in der Gegenrichtung: eine Absage, die einen Weg
    # nennt, statt einer nackten Ablehnung.
    assert "sag dem Benutzer" in str(exc.value)

    assert db.query(AiSkill).filter(AiSkill.skill_key == "vergiftet").count() == 1
    assert "vergiftet" not in {
        view.skill_key for view in ai_skill_service.visible_skills(db, regular_user)
    }

    # Die Gegenprobe, und sie ist der Preis der Schranke: eine **aktive**
    # Team-Zeile ist keine Entscheidung eines Menschen. Über die legt die KI
    # weiterhin eine panelweite Fassung, wie sie es immer durfte — die Schranke
    # hängt am Abschalten, nicht daran, dass es den Schlüssel schon gibt.
    ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="learn_skill",
        arguments={
            "skill_key": "offen", "name": "Team-Fassung",
            "description": "Eine Erkenntnis fürs Team, die niemand abgeschaltet hat.",
            "body": "Team-Fassung.", "scope": "team",
        },
    )

    gelernt = ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="learn_skill",
        arguments={
            "skill_key": "offen", "name": "Panelweite Fassung",
            "description": "Dieselbe Erkenntnis, allgemeiner gefasst und für alle gültig.",
            "body": "Panelweite Fassung.", "scope": "global",
        },
    )
    assert gelernt["learned"] is True
    assert gelernt["scope"] == "global"
    assert db.query(AiSkill).filter(AiSkill.skill_key == "offen").count() == 2
    # Zu sehen bekommt der Benutzer weiter die engere Fassung — `_overlay_rank`
    # entscheidet das, nicht diese Schranke.
    sicht = {view.skill_key: view for view in ai_skill_service.visible_skills(db, regular_user)}
    assert sicht["offen"].scope == "team"

    # Und der Zuschnitt: geprüft wird, was **dieser** Benutzer sieht. Ein
    # abgeschalteter Skill in einem fremden Team geht ihn nichts an — würde er
    # blocken, könnte jeder Teamverwalter der KI panelweit Schlüssel sperren,
    # für Leute, die von seinem Team nie gehört haben.
    fremder = _user(db, "fremder")
    _allow(db, fremder, "ai.skills.use", "teams.create")
    fremdes_team = _team(db, fremder)
    fremde_zeile = ai_skill_service.upsert_skill(
        db, user=fremder, skill_key="fremd-aus", name="Fremde Fassung",
        description="Eine Erkenntnis in einem Team, in dem dieser Benutzer nicht ist.",
        body="Fremde Fassung.", team_id=fremdes_team.id, origin="ai",
    )
    ai_skill_service.set_enabled(db, user=fremder, skill_id=fremde_zeile.id, enabled=False)

    gelernt = _learn_global(db, regular_user, "fremd-aus")
    assert gelernt["learned"] is True
    assert "fremd-aus" in {
        view.skill_key for view in ai_skill_service.visible_skills(db, regular_user)
    }


def test_eine_team_zeile_verdeckt_die_panelweite_vorgabe_nicht(
    db: Session, regular_user: User
) -> None:
    """Der Weg um die Überschreib-Schranke herum: eine Etage tiefer anlegen.

    `upsert_skill` sucht die Kollisionszeile über `scope_identity` und findet
    die globale deshalb nicht. Unter dem Schlüssel einer panelweiten
    Betreiber-Vorgabe legte `learn_skill` mit scope='team' also schlicht eine
    **neue** Zeile an — und `_overlay_rank` lässt die engere gewinnen. Für
    jedes Mitglied des Teams stand danach Modelltext, wo der Betreiber seine
    Vorgabe hingeschrieben hat; die Vorgabe selbst blieb unverändert in der
    Datenbank stehen, sodass niemandem etwas auffiel.

    Abgesichert war das allein durch einen Satz im Absagetext des anderen
    Zweigs — also durch eine Anweisung an genau das Modell, das im
    Bedrohungsmodell fremdgesteuert ist.
    """
    kollege = _user(db, "teamkollege")
    _allow(db, regular_user, "ai.skills.use", "ai.skills.manage", "teams.create")
    _allow(db, kollege, "ai.skills.use")
    team = _team(db, regular_user, kollege)
    ai_skill_service.upsert_skill(
        db, user=regular_user, skill_key="hausregel", name="Hausregel",
        description="Die vom Betreiber von Hand geschriebene Vorgehensweise bei Störungen.",
        body="Erst den Menschen fragen.", team_id=None, origin="operator",
    )

    with pytest.raises(AiActionValidationError) as exc:
        ai_action_service.execute_read_tool(
            db, user=regular_user, tool_name="learn_skill",
            arguments={
                "skill_key": "hausregel", "name": "Team-Fassung",
                "description": "Ein gelernter Ersatz, der die Vorgabe des Betreibers verdecken würde.",
                "body": "Einfach machen.", "scope": "team", "team": team.name,
            },
        )
    # Dieselbe Antwortform wie beim direkten Überschreiben: die Absage nennt
    # einen Weg, der kein Duplikat erzeugt.
    assert "sag dem Benutzer" in str(exc.value)

    assert db.query(AiSkill).filter(AiSkill.skill_key == "hausregel").count() == 1
    for leser in (regular_user, kollege):
        sicht = {view.skill_key: view for view in ai_skill_service.visible_skills(db, leser)}
        assert sicht["hausregel"].name == "Hausregel"
        assert sicht["hausregel"].scope == "global"

    # Die Gegenprobe: ohne panelweite Vorgabe lernt die KI ins Team wie bisher.
    # Die Schranke hängt an der Kollision, nicht am Bereich.
    gelernt = ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="learn_skill",
        arguments={
            "skill_key": "teameigen", "name": "Team-Fassung",
            "description": "Eine Erkenntnis, zu der es keine Vorgabe des Betreibers gibt.",
            "body": "So machen wir das hier.", "scope": "team", "team": team.name,
        },
    )
    assert gelernt["learned"] is True
    assert gelernt["scope"] == "team"
