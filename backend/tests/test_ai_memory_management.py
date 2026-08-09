"""Die KI findet Erinnerungen nach Bedeutung und loescht sie auf Zuruf.

Der Fall aus der Beschreibung: *"loesch alles was ich ueber meinen Hund gesagt
habe"*. Das setzt zweierlei voraus — die Eintraege zu **finden**, auch wenn das
Wort "Hund" gar nicht darin vorkommt, und sie danach gezielt zu **loeschen**.

Beides ist bewusst getrennt. Eine Vektoraehnlichkeit von 0,4 ist eine
brauchbare Grundlage dafuer, jemandem etwas anzuzeigen, und eine schlechte
dafuer, es zu vernichten. Deshalb sucht das Modell zuerst, nennt was es
gefunden hat, und loescht danach benannte Schluessel.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from models import AiMemoryEntry, Role, RolePermission, Team, User
from services import ai_action_service, ai_embedding_service, ai_memory_service, team_service
from services.ai_action_errors import AiActionValidationError
from services.auth_service import AuthService
from services.role_service import set_user_roles


def _user(db: Session, name: str) -> User:
    user = AuthService.create_user(db, name, f"{name}@test.de", "MgmtPass123!")
    user.email_verified = True
    db.commit()
    db.refresh(user)
    return user


def _allow(db: Session, user: User, *keys: str) -> None:
    role = Role(name=f"mgmt-{user.username}", description=None, is_system=False)
    db.add(role)
    db.flush()
    for key in keys:
        db.add(RolePermission(role_id=role.id, permission_key=key))
    db.commit()
    set_user_roles(db, user, [role.id])
    db.commit()
    if "ai.memory.use" in keys:
        ai_memory_service.set_preference(db, user, True)


def _remember(db: Session, user: User, key: str, value: str) -> None:
    ai_memory_service.upsert_entry(
        db, user=user, scope="user", server_id=None, key=key, value=value,
    )


def _keys_of(result: dict) -> set[str]:
    return {item["key"] for item in result["results"]}


# ── Finden ────────────────────────────────────────────────────────────


def test_search_finds_entries_the_user_can_see(db: Session, regular_user: User) -> None:
    _allow(db, regular_user, "ai.memory.use")
    _remember(db, regular_user, "hund.name", "Mein Hund heisst Bello")
    _remember(db, regular_user, "ram.bevorzugt", "8 GB fuer neue Server")

    result = ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="search_memory",
        arguments={"query": "Hund"},
    )

    assert "hund.name" in _keys_of(result)
    # Der Klartext gehoert dazu: wer loeschen soll, muss sehen was.
    treffer = next(item for item in result["results"] if item["key"] == "hund.name")
    assert "Bello" in treffer["value"]
    # Fremdtext bleibt als solcher gekennzeichnet.
    assert result["untrusted"] is True


def test_search_never_reaches_another_users_memory(
    db: Session, regular_user: User
) -> None:
    """Die Suche nutzt denselben Sichtbarkeitsfilter wie der Abruf.

    Sonst waere sie ein Weg, an Eintraege zu kommen, die im Kontext nie
    auftauchen wuerden — eine Hintertuer um die Trennung herum.
    """
    other = _user(db, "andere")
    _allow(db, regular_user, "ai.memory.use")
    _allow(db, other, "ai.memory.use")
    _remember(db, other, "gehalt", "Verdient 4200 Euro im Monat")

    result = ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="search_memory",
        arguments={"query": "Gehalt Einkommen Verdienst"},
    )

    assert result["results"] == []


@pytest.mark.skipif(
    not ai_embedding_service.is_available(),
    reason="Lokales Embeddingmodell nicht installiert",
)
def test_search_finds_what_is_worded_differently(
    db: Session, regular_user: User
) -> None:
    """Der eigentliche Zweck: "mein Hund" findet den Eintrag ueber Bello.

    Ein reiner Wortabgleich fiele hier durch — im Eintrag steht "Hund" gar
    nicht. Genau dafuer liegt neben jedem Eintrag ein Vektor.
    """
    _allow(db, regular_user, "ai.memory.use")
    _remember(db, regular_user, "bello", "Bello ist ein Golden Retriever und drei Jahre alt")
    _remember(db, regular_user, "backup.zeit", "Backups laufen nachts um drei")

    result = ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="search_memory",
        arguments={"query": "alles ueber meinen Hund"},
    )

    # Der Hundeeintrag muss vor dem Backupeintrag stehen.
    assert result["results"][0]["key"] == "bello"


# ── Loeschen ──────────────────────────────────────────────────────────


def test_deletion_removes_exactly_the_named_keys(
    db: Session, regular_user: User
) -> None:
    _allow(db, regular_user, "ai.memory.use")
    _remember(db, regular_user, "hund.name", "Mein Hund heisst Bello")
    _remember(db, regular_user, "hund.rasse", "Golden Retriever")
    _remember(db, regular_user, "ram.bevorzugt", "8 GB fuer neue Server")

    result = ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="forget_memory",
        arguments={"scope": "user", "keys": ["hund.name", "hund.rasse"]},
    )

    assert result["forgotten"] == ["hund.name", "hund.rasse"]
    verbleibend = {
        row.key for row in
        db.query(AiMemoryEntry).filter(
            AiMemoryEntry.scope_identity == f"user:{regular_user.id}"
        ).all()
    }
    assert verbleibend == {"ram.bevorzugt"}


def test_a_key_that_does_not_exist_is_reported(db: Session, regular_user: User) -> None:
    """Sonst meldet das Modell ein Loeschen, das nie stattgefunden hat."""
    _allow(db, regular_user, "ai.memory.use")
    _remember(db, regular_user, "hund.name", "Mein Hund heisst Bello")

    result = ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="forget_memory",
        arguments={"scope": "user", "keys": ["hund.name", "gibt.es.nicht"]},
    )

    assert result["forgotten"] == ["hund.name"]
    assert result["not_found"] == ["gibt.es.nicht"]


def test_deletion_cannot_reach_another_users_memory(
    db: Session, regular_user: User
) -> None:
    """Derselbe Schluessel bei zwei Benutzern sind zwei verschiedene Zeilen."""
    other = _user(db, "andere")
    _allow(db, regular_user, "ai.memory.use")
    _allow(db, other, "ai.memory.use")
    _remember(db, regular_user, "zeitzone", "Europe/Berlin")
    _remember(db, other, "zeitzone", "America/New_York")

    ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="forget_memory",
        arguments={"scope": "user", "keys": ["zeitzone"]},
    )

    uebrig = db.query(AiMemoryEntry).filter(AiMemoryEntry.key == "zeitzone").all()
    assert len(uebrig) == 1
    assert uebrig[0].scope_identity == f"user:{other.id}"


def test_panel_memory_is_out_of_reach(db: Session, regular_user: User) -> None:
    """Was fuer alle gilt, loescht die KI nicht auf Zuruf eines Einzelnen."""
    _allow(db, regular_user, "ai.memory.use")

    with pytest.raises(AiActionValidationError):
        ai_action_service.execute_read_tool(
            db, user=regular_user, tool_name="forget_memory",
            arguments={"scope": "panel", "keys": ["irgendwas"]},
        )


def test_team_deletion_requires_the_switch(db: Session, regular_user: User) -> None:
    colleague = _user(db, "kollege")
    _allow(db, regular_user, "ai.memory.use", "teams.create")
    _allow(db, colleague, "ai.memory.use")
    team = team_service.create_team(db, user=regular_user, name="Betrieb")
    team_service.add_member(
        db, team=team, user=regular_user, new_user_id=colleague.id,
        can_manage_skills=False, can_manage_memory=False,
    )
    ai_memory_service.upsert_entry(
        db, user=regular_user, scope="team", server_id=None, team_id=team.id,
        key="valheim.ram", value="Valheim braucht mindestens 6 GB",
    )

    # Der Kollege darf das Teamwissen nicht pflegen — sein Loeschversuch
    # landet deshalb im persoenlichen Bereich und laesst das Team unberuehrt.
    ai_action_service.execute_read_tool(
        db, user=colleague, tool_name="forget_memory",
        arguments={"scope": "team", "keys": ["valheim.ram"]},
    )

    assert db.query(AiMemoryEntry).filter(
        AiMemoryEntry.scope_identity == f"team:{team.id}"
    ).count() == 1


def test_deletion_without_the_permission_is_refused(
    db: Session, regular_user: User
) -> None:
    with pytest.raises(AiActionValidationError):
        ai_action_service.execute_read_tool(
            db, user=regular_user, tool_name="forget_memory",
            arguments={"scope": "user", "keys": ["egal"]},
        )


def test_an_empty_key_list_is_refused(db: Session, regular_user: User) -> None:
    """Ohne Schluessel gibt es nichts zu loeschen — und kein "alles"."""
    _allow(db, regular_user, "ai.memory.use")

    with pytest.raises(AiActionValidationError):
        ai_action_service.execute_read_tool(
            db, user=regular_user, tool_name="forget_memory",
            arguments={"scope": "user", "keys": []},
        )


# ── Korrigieren ───────────────────────────────────────────────────────


def test_the_ai_does_not_silently_overwrite_what_the_user_said(
    db: Session, regular_user: User
) -> None:
    """Der Schutz gilt gegen die *stillschweigende* Korrektur.

    Die KI leitet nebenbei etwas ab und ueberschreibt damit, was der Benutzer
    selbst gesagt hat — das soll nicht passieren.
    """
    _allow(db, regular_user, "ai.memory.use")
    _remember(db, regular_user, "hund.name", "Mein Hund heisst Bello")

    with pytest.raises(AiActionValidationError) as exc:
        ai_action_service.execute_read_tool(
            db, user=regular_user, tool_name="remember",
            arguments={
                "scope": "user", "key": "hund.name", "value": "Mein Hund heisst Rex",
            },
        )
    # Die Meldung muss den richtigen Weg nennen. Frueher stand dort "verwende
    # einen anderen Schluessel" — genau das erzeugt die Dubletten, die wir
    # vermeiden wollen.
    assert "replace_user_entry" in str(exc.value)


def test_an_explicit_correction_overwrites_instead_of_duplicating(
    db: Session, regular_user: User
) -> None:
    """"Nein, er heisst Rex" soll nicht zu zwei Hunden fuehren.

    Verlangt der Benutzer die Korrektur ausdruecklich, ist das Ueberschreiben
    genau das Gewuenschte — der Schutz oben zielt auf etwas anderes.
    """
    _allow(db, regular_user, "ai.memory.use")
    _remember(db, regular_user, "hund.name", "Mein Hund heisst Bello")

    ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="remember",
        arguments={
            "scope": "user", "key": "hund.name", "value": "Mein Hund heisst Rex",
            "replace_user_entry": True,
        },
    )

    rows = db.query(AiMemoryEntry).filter(
        AiMemoryEntry.scope_identity == f"user:{regular_user.id}"
    ).all()
    assert len(rows) == 1
    _row, value = ai_memory_service.list_entries(db, regular_user, "user", None)[0]
    assert "Rex" in value
