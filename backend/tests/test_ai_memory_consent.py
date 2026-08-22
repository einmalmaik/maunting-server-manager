"""Das Gedaechtnis ist aus, bis jemand zustimmt.

Der Betreiber hat den Ablauf genau beschrieben: standardmaessig deaktiviert,
ein Hinweis vor der ersten Nachricht, "Nein" fragt nach 24 Stunden erneut,
"nicht mehr anzeigen" beendet das Fragen — aber nicht die Moeglichkeit, es
spaeter im Profil einzuschalten.

Warum das mehr ist als eine Einstellung: Der Inhalt des Gedaechtnisses geht
bei **jeder** Anfrage an einen externen KI-Anbieter. Verschluesselung im
Ruhezustand aendert daran nichts. Die Einwilligung ist deshalb der eigentliche
Schutz, nicht die Krypto.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from models import AiMemoryEntry, AiMemoryPreference, Role, RolePermission, User
from services import ai_action_service, ai_memory_service
from services.role_service import set_user_roles


def _allow(db: Session, user: User) -> None:
    role = Role(name=f"mem-{user.id}", description=None, is_system=False)
    db.add(role)
    db.flush()
    db.add(RolePermission(role_id=role.id, permission_key="ai.memory.use"))
    db.commit()
    set_user_roles(db, user, [role.id])
    db.commit()


def test_memory_is_off_for_a_fresh_account(db: Session, regular_user: User) -> None:
    """Der Kern der Aenderung: ohne Zustimmung passiert nichts."""
    assert ai_memory_service.preference(db, regular_user.id) is False


def test_nothing_reaches_the_model_while_memory_is_off(
    db: Session, regular_user: User
) -> None:
    """Auch vorhandene Eintraege bleiben stumm, solange es aus ist.

    Wichtig fuer den Fall, dass jemand das Gedaechtnis nachtraeglich abschaltet:
    das Abschalten muss sofort wirken, nicht erst nach dem Loeschen.
    """
    _allow(db, regular_user)
    ai_memory_service.set_preference(db, regular_user, True)
    ai_memory_service.upsert_entry(
        db, user=regular_user, scope="user", server_id=None,
        key="ram.bevorzugt", value="8 GB fuer neue Server",
    )
    assert "8 GB" in (ai_memory_service.provider_memory_context(db, regular_user) or "")
    db.commit()

    ai_memory_service.set_preference(db, regular_user, False)
    assert ai_memory_service.provider_memory_context(db, regular_user) is None


def test_the_ai_does_not_write_while_memory_is_off(
    db: Session, regular_user: User
) -> None:
    """Stummschalten ist nicht dasselbe wie nicht mitschreiben.

    Die Einwilligung wurde bisher nur beim **Lesen** geprüft. Bei
    abgeschaltetem Schalter legte `remember` weiter Zeilen an — sie wurden nur
    nicht mehr in den Kontext gegeben. Zwei Folgen, beide schlecht:

    * Die Oberfläche sagt „Derzeit ist das Gedächtnis deaktiviert", während im
      Hintergrund gesammelt wird. Der Systemprompt weist das Modell
      ausdrücklich an, Vorlieben **ungefragt** abzulegen — es genügt also, dass
      der Benutzer beiläufig „ich nehme immer 8 GB" schreibt.
    * Wer den Schalter später umlegt, bekommt schlagartig alles zu sehen, was
      in der Zwischenzeit über ihn gesammelt wurde. Das ist die Umkehrung
      dessen, wofür die Einwilligung da ist.

    Das Ergebnis ist bewusst eine Auskunft und keine Ausnahme: das Modell soll
    dem Benutzer sagen können, warum es sich nichts merkt.
    """
    _allow(db, regular_user)
    assert ai_memory_service.preference(db, regular_user.id) is False

    ergebnis = ai_action_service.execute_read_tool(
        db,
        user=regular_user,
        tool_name="remember",
        arguments={"scope": "user", "key": "ram.bevorzugt", "value": "8 GB"},
    )
    assert ergebnis["remembered"] is False
    assert ergebnis["reason"] == "memory_disabled"
    # **Und der Fehlschlag ist nicht mehr lautlos.**
    #
    # `ai_prompt.GEDAECHTNIS` verlangt, dass Merken und Nachschlagen lautlos
    # passieren — zu Recht, ein Gedaechtnis soll wirken und nicht auftreten.
    # Genau das machte diesen Fall unsichtbar: das Modell versuchte es
    # korrekt, scheiterte korrekt und schwieg korrekt. Der Betreiber am
    # 22.08.2026: "die KI merkt sich auch gar nichts" — er konnte es nicht
    # wissen. Die Ausnahme steht hier und nicht im Prompt, weil nur hier
    # bekannt ist, dass sie zutrifft.
    assert "Lautlosigkeit ausnahmsweise nicht" in ergebnis["message"]
    assert "Profil > KI" in ergebnis["message"]
    # Einmal, nicht in jeder Antwort — sonst wird aus der Auskunft eine
    # Mahnung.
    assert "Einmal, nicht in jeder Antwort" in ergebnis["message"]
    # Und es liegt wirklich nichts in der Datenbank.
    assert db.query(AiMemoryEntry).count() == 0

    # Mit Einwilligung geht es — sonst wäre der Test oben auch dann grün, wenn
    # `remember` gar nicht mehr funktionierte.
    ai_memory_service.set_preference(db, regular_user, True)
    db.commit()
    ergebnis = ai_action_service.execute_read_tool(
        db,
        user=regular_user,
        tool_name="remember",
        arguments={"scope": "user", "key": "ram.bevorzugt", "value": "8 GB"},
    )
    assert ergebnis["remembered"] is True
    assert db.query(AiMemoryEntry).count() == 1


def test_the_notice_is_due_for_a_fresh_account(db: Session, regular_user: User) -> None:
    assert ai_memory_service.notice_due(db, regular_user.id) is True


def test_saying_yes_enables_memory_and_ends_the_notice(
    db: Session, regular_user: User
) -> None:
    ai_memory_service.record_notice_answer(
        db, regular_user, enable=True, hide_future=False
    )
    assert ai_memory_service.preference(db, regular_user.id) is True
    # Ist es an, gibt es nichts mehr zu fragen.
    assert ai_memory_service.notice_due(db, regular_user.id) is False


def test_saying_no_asks_again_after_a_day(db: Session, regular_user: User) -> None:
    ai_memory_service.record_notice_answer(
        db, regular_user, enable=False, hide_future=False
    )
    assert ai_memory_service.preference(db, regular_user.id) is False
    assert ai_memory_service.notice_due(db, regular_user.id) is False

    row = db.get(AiMemoryPreference, regular_user.id)
    row.notice_last_shown_at = datetime.now(timezone.utc) - timedelta(
        hours=ai_memory_service.NOTICE_REPEAT_HOURS + 1
    )
    db.commit()

    assert ai_memory_service.notice_due(db, regular_user.id) is True


def test_hiding_the_notice_keeps_the_switch_reachable(
    db: Session, regular_user: User
) -> None:
    """"Nicht mehr anzeigen" beendet das Fragen, nicht die Funktion."""
    ai_memory_service.record_notice_answer(
        db, regular_user, enable=False, hide_future=True
    )
    assert ai_memory_service.notice_due(db, regular_user.id) is False

    # Auch nach beliebig langer Zeit wird nicht wieder gefragt.
    row = db.get(AiMemoryPreference, regular_user.id)
    row.notice_last_shown_at = datetime.now(timezone.utc) - timedelta(days=365)
    db.commit()
    assert ai_memory_service.notice_due(db, regular_user.id) is False

    # Der Weg ueber das Profil bleibt offen.
    ai_memory_service.set_preference(db, regular_user, True)
    assert ai_memory_service.preference(db, regular_user.id) is True


def test_the_notice_endpoint_reports_the_new_state(
    client, db: Session, regular_user: User, user_cookies: dict, user_csrf_token
) -> None:
    _allow(db, regular_user)

    before = client.get("/api/ai/memory/preference", cookies=user_cookies)
    assert before.status_code == 200
    assert before.json() == {"enabled": False, "notice_due": True, "notice_hidden": False}

    answer = client.post(
        "/api/ai/memory/notice",
        json={"enable": False, "hide_future": True},
        cookies=user_cookies,
        headers={"X-CSRF-Token": user_csrf_token} if user_csrf_token else {},
    )
    assert answer.status_code == 200
    assert answer.json() == {
        "enabled": False, "notice_due": False, "notice_hidden": True,
    }


def test_notice_requires_the_memory_permission(
    client, db: Session, regular_user: User, user_cookies: dict, user_csrf_token
) -> None:
    """Ohne `ai.memory.use` gibt es auch keinen Hinweis zu beantworten."""
    response = client.post(
        "/api/ai/memory/notice",
        json={"enable": True, "hide_future": False},
        cookies=user_cookies,
        headers={"X-CSRF-Token": user_csrf_token} if user_csrf_token else {},
    )
    assert response.status_code == 403


def test_teamwissen_haengt_nicht_am_persoenlichen_schalter(
    db: Session, regular_user: User
) -> None:
    """Die Einwilligung gilt dem eigenen Gedaechtnis, nicht dem des Teams.

    Vorher endete `provider_memory_context` bei fehlender Einwilligung sofort,
    und `_visible_scope_rows` enthaelt auch die Teamzeilen. Wer sein eigenes
    Gedaechtnis abschaltete, nahm dem Assistenten damit still das Wissen aller
    seiner Teams — an einer Stelle, an der niemand danach sucht.

    Der Betreiber hat die Trennung ausdruecklich so entschieden: Teamwissen ist
    Firmeninhalt, nicht der eines Mitglieds. Fuer ihn sind Mitgliedschaft und
    die Anbieterwahl des Betreibers die Einwilligung.
    """
    from services import team_service

    _allow(db, regular_user)
    rolle = db.query(Role).filter(Role.name == f"mem-{regular_user.id}").first()
    db.add(RolePermission(role_id=rolle.id, permission_key="teams.create"))
    db.commit()
    team = team_service.create_team(db, user=regular_user, name="Betrieb")

    ai_memory_service.set_preference(db, regular_user, True)
    ai_memory_service.upsert_entry(
        db, user=regular_user, scope="user", server_id=None,
        key="ram.bevorzugt", value="Ich nehme immer 8 GB",
    )
    ai_memory_service.upsert_entry(
        db, user=regular_user, scope="team", server_id=None, team_id=team.id,
        key="valheim.ram", value="Valheim braucht mindestens 6 GB",
    )
    db.commit()

    ai_memory_service.set_preference(db, regular_user, False)
    block = ai_memory_service.provider_memory_context(db, regular_user) or ""
    assert "Valheim" in block, "Teamwissen darf nicht am eigenen Schalter haengen"
    assert "8 GB" not in block, "Persoenliches bleibt ohne Einwilligung draussen"


def test_die_suche_findet_ohne_einwilligung_nichts_persoenliches(
    db: Session, regular_user: User
) -> None:
    """Derselbe Massstab fuer die Suche.

    `search_entries` prueft die Einwilligung bisher **gar nicht**. `search_memory`
    legte dem Modell damit persoenliche Eintraege vor, denen nie jemand
    zugestimmt hatte — waehrend derselbe Eintrag ueber den Kontextaufbau
    richtigerweise draussen blieb. Zwei Massstaebe fuer dieselbe Frage.
    """
    _allow(db, regular_user)
    ai_memory_service.set_preference(db, regular_user, True)
    ai_memory_service.upsert_entry(
        db, user=regular_user, scope="user", server_id=None,
        key="hund.name", value="Mein Hund heisst Bello",
    )
    db.commit()
    assert ai_memory_service.search_entries(db, regular_user, "Hund")

    ai_memory_service.set_preference(db, regular_user, False)
    assert ai_memory_service.search_entries(db, regular_user, "Hund") == []
