"""Skills als Prosa: Sichtbarkeit, Ueberlagerung und Teamgrenzen.

Diese Datei ersetzt `test_ai_skills.py` und `test_ai_skill_limits.py`. Beide
prueften das Makro-System — eine gespeicherte Folge von Tool-Aufrufen, die es
nicht mehr gibt. Insbesondere die Kontingentpruefung aus `test_ai_skill_limits`
hat sich erledigt: ein Prosa-Skill wird nicht ausgefuehrt, sondern gelesen, und
das Lesen laeuft als gewoehnlicher Werkzeugaufruf im Chat gegen dieselben
Kontingente wie jede andere Anfrage.

Was bleibt und hier geprueft wird, ist die Sichtbarkeit: wer welchen Skill
sieht, und dass ein Team-Skill das Team nicht verlaesst.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from models import AiSkill, Role, RolePermission, Team, User
from services import ai_embedding_service, ai_skill_service, team_service
from services.auth_service import AuthService
from services.role_service import set_user_roles


def _user(db: Session, name: str) -> User:
    user = AuthService.create_user(db, name, f"{name}@test.de", "SkillPass123!")
    user.email_verified = True
    db.commit()
    db.refresh(user)
    return user


def _allow(db: Session, user: User, *keys: str) -> None:
    role = Role(name=f"skill-{user.username}", description=None, is_system=False)
    db.add(role)
    db.flush()
    for key in keys:
        db.add(RolePermission(role_id=role.id, permission_key=key))
    db.commit()
    set_user_roles(db, user, [role.id])
    db.commit()


def _team(db: Session, owner: User, *members: User) -> Team:
    _allow(db, owner, "teams.create", "ai.skills.use")
    team = team_service.create_team(db, user=owner, name=f"team-{owner.username}")
    for member in members:
        team_service.add_member(
            db, team=team, user=owner, new_user_id=member.id,
            can_manage_skills=True, can_manage_memory=True,
        )
    return team


def _keys(views) -> set[str]:
    return {view.skill_key for view in views}


# ── Die mitgelieferten Skills ─────────────────────────────────────────


def test_shipped_skills_load_from_disk(db: Session, regular_user: User) -> None:
    """Ohne jede Datenbankzeile ist die KI am ersten Tag nuetzlich.

    Das ist der Grund, warum die mitgelieferten Skills Dateien sind und keine
    Migration: ein MSM-Update verbessert damit die KI jeder Installation.
    """
    ai_skill_service.reset_shipped_cache_for_tests()
    shipped = ai_skill_service.shipped_skills()

    assert "server-nicht-erreichbar" in shipped
    assert "zu-wenig-arbeitsspeicher" in shipped
    for skill in shipped.values():
        assert skill.name and skill.description and skill.body
        # Die Beschreibung entscheidet, ob das Modell den Skill anfasst — sie
        # muss sagen, wann er passt, nicht nur was er ist.
        assert len(skill.description) > 40


def test_a_broken_shipped_file_does_not_break_the_rest(tmp_path, monkeypatch) -> None:
    """Eine beschaedigte Datei faellt heraus, statt das Panel aufzuhalten."""
    (tmp_path / "kaputt.md").write_text("kein Frontmatter hier", encoding="utf-8")
    (tmp_path / "heil.md").write_text(
        "---\nname: Heil\ndescription: Eine gueltige Beschreibung mit genug Text.\n---\nInhalt",
        encoding="utf-8",
    )
    monkeypatch.setattr(ai_skill_service, "shipped_directory", lambda: tmp_path)
    ai_skill_service.reset_shipped_cache_for_tests()

    found = ai_skill_service.shipped_skills()
    assert set(found) == {"heil"}
    ai_skill_service.reset_shipped_cache_for_tests()


def test_a_missing_directory_is_a_state_not_a_crash(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(ai_skill_service, "shipped_directory", lambda: tmp_path / "weg")
    ai_skill_service.reset_shipped_cache_for_tests()
    assert ai_skill_service.shipped_skills() == {}
    ai_skill_service.reset_shipped_cache_for_tests()


# ── Datenbank schlaegt Datei ──────────────────────────────────────────


def test_a_global_row_replaces_the_shipped_file(db: Session, regular_user: User) -> None:
    """So ueberschreibt ein Betreiber eine MSM-Vorgabe, ohne sie zu verlieren."""
    ai_skill_service.reset_shipped_cache_for_tests()
    _allow(db, regular_user, "ai.skills.use", "ai.skills.manage")

    ai_skill_service.upsert_skill(
        db, user=regular_user, skill_key="portkonflikt", name="Eigene Fassung",
        description="Die hauseigene Vorgehensweise bei Portkonflikten, abweichend von der Vorgabe.",
        body="Bei uns gilt: immer den neuen Server verschieben.", team_id=None,
    )

    views = {view.skill_key: view for view in ai_skill_service.visible_skills(db, regular_user)}
    assert views["portkonflikt"].name == "Eigene Fassung"
    assert views["portkonflikt"].scope == "global"
    # Die uebrigen Vorgaben bleiben unberuehrt.
    assert "server-nicht-erreichbar" in views


def test_disabling_a_global_row_hides_the_shipped_file_too(
    db: Session, regular_user: User
) -> None:
    """Ohne diesen Weg waere eine mitgelieferte Vorgabe nicht abschaltbar."""
    ai_skill_service.reset_shipped_cache_for_tests()
    _allow(db, regular_user, "ai.skills.use", "ai.skills.manage")

    row = ai_skill_service.upsert_skill(
        db, user=regular_user, skill_key="portkonflikt", name="Abgeschaltet",
        description="Diese Vorgehensweise passt nicht zu unserem Betrieb und bleibt aus.",
        body="Nicht verwenden.", team_id=None,
    )
    ai_skill_service.set_enabled(db, user=regular_user, skill_id=row.id, enabled=False)

    assert "portkonflikt" not in _keys(ai_skill_service.visible_skills(db, regular_user))


# ── Teamgrenzen ───────────────────────────────────────────────────────


def test_a_team_skill_never_leaves_its_team(db: Session, regular_user: User) -> None:
    colleague = _user(db, "kollege")
    stranger = _user(db, "fremder")
    _allow(db, colleague, "ai.skills.use")
    _allow(db, stranger, "ai.skills.use")
    team = _team(db, regular_user, colleague)

    ai_skill_service.upsert_skill(
        db, user=regular_user, skill_key="hausregel", name="Unsere Hausregel",
        description="Wie wir in diesem Team mit Wartungsfenstern umgehen und wen wir informieren.",
        body="Immer erst im Discord ankuendigen.", team_id=team.id,
    )

    assert "hausregel" in _keys(ai_skill_service.visible_skills(db, regular_user))
    assert "hausregel" in _keys(ai_skill_service.visible_skills(db, colleague))
    assert "hausregel" not in _keys(ai_skill_service.visible_skills(db, stranger))


def test_leaving_the_team_removes_the_skill_immediately(
    db: Session, regular_user: User
) -> None:
    colleague = _user(db, "kollege")
    _allow(db, colleague, "ai.skills.use")
    team = _team(db, regular_user, colleague)
    ai_skill_service.upsert_skill(
        db, user=regular_user, skill_key="hausregel", name="Unsere Hausregel",
        description="Wie wir in diesem Team mit Wartungsfenstern umgehen und wen wir informieren.",
        body="Immer erst im Discord ankuendigen.", team_id=team.id,
    )
    assert "hausregel" in _keys(ai_skill_service.visible_skills(db, colleague))

    team_service.remove_member(db, team=team, user=regular_user, member_user_id=colleague.id)

    assert "hausregel" not in _keys(ai_skill_service.visible_skills(db, colleague))


def test_reading_a_foreign_team_skill_is_refused(db: Session, regular_user: User) -> None:
    """Ein erratener Schluessel darf nichts oeffnen — auch nicht per Prompt."""
    stranger = _user(db, "fremder")
    _allow(db, stranger, "ai.skills.use")
    team = _team(db, regular_user)
    ai_skill_service.upsert_skill(
        db, user=regular_user, skill_key="geheim", name="Interne Vorgehensweise",
        description="Etwas, das nur dieses Team etwas angeht und niemanden sonst betrifft.",
        body="Vertraulicher Ablauf.", team_id=team.id,
    )

    with pytest.raises(Exception) as exc:
        ai_skill_service.read_body(db, stranger, "geheim")
    assert getattr(exc.value, "status_code", None) == 404


# ── Wer schreiben darf ────────────────────────────────────────────────


def test_global_writing_requires_the_manage_permission(
    db: Session, regular_user: User
) -> None:
    """Ein globaler Skill wirkt fuer jeden Kunden des Panels."""
    _allow(db, regular_user, "ai.skills.use")

    with pytest.raises(Exception) as exc:
        ai_skill_service.upsert_skill(
            db, user=regular_user, skill_key="global-versuch", name="Versuch",
            description="Ein Versuch, ohne die noetige Berechtigung global zu schreiben.",
            body="Sollte nicht ankommen.", team_id=None,
        )
    assert getattr(exc.value, "status_code", None) == 403


def test_a_member_without_the_switch_cannot_write_team_skills(
    db: Session, regular_user: User
) -> None:
    colleague = _user(db, "kollege")
    _allow(db, colleague, "ai.skills.use")
    _allow(db, regular_user, "teams.create", "ai.skills.use")
    team = team_service.create_team(db, user=regular_user, name="Nur lesen")
    team_service.add_member(
        db, team=team, user=regular_user, new_user_id=colleague.id,
        can_manage_skills=False, can_manage_memory=False,
    )

    with pytest.raises(Exception) as exc:
        ai_skill_service.upsert_skill(
            db, user=colleague, skill_key="unerlaubt", name="Unerlaubt",
            description="Ein Versuch ohne den Schalter fuer die Skill-Verwaltung im Team.",
            body="Sollte nicht ankommen.", team_id=team.id,
        )
    assert getattr(exc.value, "status_code", None) == 403


def test_the_ai_does_not_silently_overwrite_a_human_skill(
    db: Session, regular_user: User
) -> None:
    """Dieselbe Regel wie beim Gedaechtnis, aus demselben Grund."""
    _allow(db, regular_user, "ai.skills.use", "ai.skills.manage")
    ai_skill_service.upsert_skill(
        db, user=regular_user, skill_key="handarbeit", name="Von Hand",
        description="Eine bewusst formulierte Vorgehensweise, die so bleiben soll wie sie ist.",
        body="Sorgfaeltig geschrieben.", team_id=None, origin="operator",
    )

    with pytest.raises(Exception) as exc:
        ai_skill_service.upsert_skill(
            db, user=regular_user, skill_key="handarbeit", name="Von der KI",
            description="Ein automatisch gelernter Ersatz, der die Handarbeit ueberschreiben will.",
            body="Automatisch erzeugt.", team_id=None, origin="ai",
        )
    assert getattr(exc.value, "status_code", None) == 409


def test_credentials_are_refused_in_every_field(db: Session, regular_user: User) -> None:
    _allow(db, regular_user, "ai.skills.use", "ai.skills.manage")

    with pytest.raises(Exception) as exc:
        ai_skill_service.upsert_skill(
            db, user=regular_user, skill_key="mit-secret", name="Mit Zugangsdaten",
            description="Eine harmlose Beschreibung, die lang genug fuer die Pruefung ist.",
            body="Melde dich mit password=geheim123 an.", team_id=None,
        )
    assert getattr(exc.value, "status_code", None) == 422


# ── Die Warteschlange ─────────────────────────────────────────────────


def test_pending_skills_reach_nobody(db: Session, regular_user: User) -> None:
    """Ein global gelernter Skill wartet, ohne zu wirken.

    Auch fuer den Benutzer nicht, aus dessen Gespraech er entstanden ist —
    sonst haette man eine Vorschau, die andere nicht sehen, und damit zwei
    Wahrheiten im selben Panel.
    """
    _allow(db, regular_user, "ai.skills.use", "ai.skills.manage")
    row = ai_skill_service.upsert_skill(
        db, user=regular_user, skill_key="wartend", name="Wartend",
        description="Ein global gelernter Skill, der noch auf die Freigabe des Betreibers wartet.",
        body="Noch nicht freigegeben.", team_id=None, origin="ai", status="pending",
    )

    assert "wartend" not in _keys(ai_skill_service.visible_skills(db, regular_user))
    assert [item.id for item in ai_skill_service.pending_skills(db)] == [row.id]

    ai_skill_service.approve(db, user=regular_user, skill_id=row.id)

    assert "wartend" in _keys(ai_skill_service.visible_skills(db, regular_user))
    assert ai_skill_service.pending_skills(db) == []


def test_only_switching_off_hides_a_shipped_skill(db: Session, regular_user: User) -> None:
    """Der Unterschied zwischen „abgeschaltet“ und „wartend“, an einem Schluessel.

    Beides sind Zeilen, die nicht selbst gelten — aber nur eine davon ist eine
    Entscheidung. Eine abgeschaltete globale Zeile muss die mitgelieferte Datei
    weiterhin verdecken, sonst liesse sich eine MSM-Vorgabe gar nicht loswerden.
    Eine wartende darf es nicht, denn sie entsteht ohne jedes Zutun eines
    Menschen.
    """
    _allow(db, regular_user, "ai.skills.use", "ai.skills.manage")
    ai_skill_service.reset_shipped_cache_for_tests()

    wartend = ai_skill_service.upsert_skill(
        db, user=regular_user, skill_key="portkonflikt", name="Eigene Fassung",
        description="Eine gelernte Fassung, die noch auf die Freigabe des Betreibers wartet.",
        body="Wartet.", team_id=None, origin="ai", status="pending",
    )
    assert "portkonflikt" in _keys(ai_skill_service.visible_skills(db, regular_user))

    # Dieselbe Zeile, jetzt ausdruecklich abgeschaltet: nun verdeckt sie.
    ai_skill_service.approve(db, user=regular_user, skill_id=wartend.id)
    ai_skill_service.set_enabled(db, user=regular_user, skill_id=wartend.id, enabled=False)

    assert "portkonflikt" not in _keys(ai_skill_service.visible_skills(db, regular_user))


def test_approval_requires_the_manage_permission(db: Session, regular_user: User) -> None:
    other = _user(db, "ohne-recht")
    _allow(db, other, "ai.skills.use")
    _allow(db, regular_user, "ai.skills.use", "ai.skills.manage")
    row = ai_skill_service.upsert_skill(
        db, user=regular_user, skill_key="wartend", name="Wartend",
        description="Ein global gelernter Skill, der noch auf die Freigabe des Betreibers wartet.",
        body="Noch nicht freigegeben.", team_id=None, origin="ai", status="pending",
    )

    with pytest.raises(Exception) as exc:
        ai_skill_service.approve(db, user=other, skill_id=row.id)
    assert getattr(exc.value, "status_code", None) == 403


# ── Das Verzeichnis fuer den Prompt ───────────────────────────────────


def test_the_index_is_capped(db: Session, regular_user: User) -> None:
    """Mehr Skills als Platz: das Verzeichnis waechst nicht unbegrenzt."""
    ai_skill_service.reset_shipped_cache_for_tests()
    _allow(db, regular_user, "ai.skills.use", "ai.skills.manage")
    for index in range(ai_skill_service.MAX_INDEXED_SKILLS + 10):
        ai_skill_service.upsert_skill(
            db, user=regular_user, skill_key=f"fuell-{index:02d}", name=f"Fuellskill {index}",
            description=f"Ein Fuelleintrag mit der Nummer {index}, lang genug fuer die Pruefung.",
            body=f"Inhalt {index}", team_id=None,
        )

    index = ai_skill_service.skill_index(db, regular_user, "Server nicht erreichbar")
    assert len(index) == ai_skill_service.MAX_INDEXED_SKILLS


def test_the_index_carries_no_bodies(db: Session, regular_user: User) -> None:
    """Stufe eins traegt nur Name und Beschreibung — sonst waere sie sinnlos."""
    ai_skill_service.reset_shipped_cache_for_tests()
    _allow(db, regular_user, "ai.skills.use")

    for view in ai_skill_service.skill_index(db, regular_user):
        assert not hasattr(view, "body")


# ── Sprachgrenzen der Auswahl ─────────────────────────────────────────


@pytest.mark.skipif(
    not ai_embedding_service.is_available(),
    reason="Lokales Embeddingmodell nicht installiert",
)
def test_the_index_selection_crosses_the_language_barrier(
    db: Session, regular_user: User
) -> None:
    """Die Auswahl bei vielen Skills traegt ueber Sprachgrenzen — teilweise.

    **Gemessen, nicht behauptet.** Mit den sechs mitgelieferten Skills und je
    einer Frage pro Sprache trifft die Vektoraehnlichkeit:

    - Deutsch "nicht erreichbar"              -> richtig (0,61)
    - Englisch "nobody can connect"           -> richtig (0,49)
    - Franzoesisch "personne ne peut"         -> richtig (0,54)
    - Tuerkisch "kimse baglanamiyor"          -> **falsch** (0,26)
    - Deutsch "stuerzt ohne Fehlermeldung"    -> richtig (0,54)
    - Englisch "crashes with no error"        -> **falsch** (0,42)

    Vier von sechs. Statische Embeddings kennen keinen Satzkontext, und die
    Sprachbruecke traegt ungleichmaessig — dasselbe Bild wie beim Gedaechtnis
    in Phase D.

    Das ist vertretbar, weil diese Auswahl **erst ueber 25 Skills** ueberhaupt
    stattfindet. Darunter kommen alle in den Prompt und das Sprachmodell stellt
    den Bezug selbst her — zuverlaessiger, als es ein statischer Vektor kann.
    Der Test sichert deshalb die beiden Sprachen, in denen MSM seine Vorgaben
    ausliefert, und haelt die Grenze schriftlich fest statt sie zu verschweigen.
    """
    ai_skill_service.reset_shipped_cache_for_tests()
    _allow(db, regular_user, "ai.skills.use", "ai.skills.manage")
    # Ueber die Grenze druecken, damit ueberhaupt ausgewaehlt wird.
    for index in range(ai_skill_service.MAX_INDEXED_SKILLS):
        ai_skill_service.upsert_skill(
            db, user=regular_user, skill_key=f"unbezogen-{index:02d}",
            name=f"Unbezogener Eintrag {index}",
            description=f"Etwas voellig anderes ohne Bezug zu Netzwerk oder Speicher, Nummer {index}.",
            body="Inhalt.", team_id=None,
        )

    for question in (
        "Mein Server laeuft, aber niemand kann sich verbinden",
        "My server is running but nobody can connect to it",
    ):
        selected = {
            view.skill_key
            for view in ai_skill_service.skill_index(db, regular_user, question)
        }
        assert "server-nicht-erreichbar" in selected, question


# ── Vorrang bei gleichem Schluessel ───────────────────────────────────
#
# Diese drei Tests gehen absichtlich direkt an `_overlay` statt ueber
# `visible_skills`. Der Fehler lag in der Reihenfolge, in der die Zeilen aus
# der Datenbank kommen — und die laesst sich nicht bestellen: SQLite liefert
# die globale Zeile zufaellig zuerst, also genau die guenstige Ordnung.
# Ein Test ueber die Abfrage waere auch ohne den Fix gruen gewesen.


def _row(
    skill_key: str, *, team_id: int | None, enabled: bool = True,
    status: str = "active", name: str = "Fassung",
) -> AiSkill:
    """Eine Skill-Zeile ohne Sitzung — `_overlay` liest nur Felder."""
    return AiSkill(
        id=str(uuid4()), scope_identity=ai_skill_service._scope_identity(team_id),
        team_id=team_id, skill_key=skill_key, name=name,
        description="Eine Fassung dieses Skills, lang genug fuer die Pruefung.",
        body="Text.", origin="operator", status=status, enabled=enabled,
    )


def test_switching_off_a_global_row_never_removes_a_team_skill() -> None:
    """Der Betreiber schaltet die mitgelieferte Vorgabe ab — das Team behaelt seine.

    Die Zeilen kommen hier in der unguenstigen Reihenfolge herein, Team zuerst:
    genau die liefert PostgreSQL, denn `set_enabled` ist ein UPDATE und die neu
    geschriebene globale Zeile landet am Ende des Heaps. Vorher nahm der Pop
    weg, was gerade unter dem Schluessel stand — also den eingeschalteten Skill
    des Teams, den niemand abgeschaltet hatte.
    """
    by_key = {
        "portkonflikt": ai_skill_service.SkillView(
            skill_key="portkonflikt", name="Mitgeliefert",
            description="Die Vorgabe von MSM.", scope="shipped", origin="shipped",
            team_id=None, status="active", enabled=True, editable=False,
        )
    }
    rows = [
        _row("portkonflikt", team_id=7, name="Fassung des Teams"),
        _row("portkonflikt", team_id=None, enabled=False),
    ]

    ai_skill_service._overlay(by_key, rows)

    assert "portkonflikt" in by_key
    assert by_key["portkonflikt"].name == "Fassung des Teams"
    assert by_key["portkonflikt"].scope == "team"


def test_a_team_row_wins_over_the_global_row_in_either_order() -> None:
    """Eng schlaegt weit — und zwar unabhaengig davon, wie die Zeilen kommen."""
    global_row = _row("portkonflikt", team_id=None, name="Panelweite Fassung")
    team_row = _row("portkonflikt", team_id=7, name="Fassung des Teams")

    for rows in ([global_row, team_row], [team_row, global_row]):
        by_key: dict[str, ai_skill_service.SkillView] = {}
        ai_skill_service._overlay(by_key, rows)
        assert by_key["portkonflikt"].name == "Fassung des Teams"


def test_the_same_key_in_two_teams_always_resolves_the_same_way() -> None:
    """Zwei Teams, ein Schluessel: eine Fassung gewinnt — aber immer dieselbe.

    Welche es ist, ist eine Setzung (das zuletzt angelegte Team). Dass sie nicht
    an der Reihenfolge der Datenbankzeilen haengt, ist der Punkt: sonst
    beantwortet dieselbe Frage zweimal hintereinander mit zwei Texten.
    """
    vorwaerts = [
        _row("portkonflikt", team_id=3, name="Team drei"),
        _row("portkonflikt", team_id=7, name="Team sieben"),
    ]
    ergebnisse = []
    for rows in (vorwaerts, list(reversed(vorwaerts))):
        by_key: dict[str, ai_skill_service.SkillView] = {}
        ai_skill_service._overlay(by_key, rows)
        ergebnisse.append(by_key["portkonflikt"].name)

    assert ergebnisse == ["Team sieben", "Team sieben"]


# ── Der gespeicherte Auswahlvektor ────────────────────────────────────


def test_the_index_reuses_the_stored_vector(
    db: Session, regular_user: User, monkeypatch
) -> None:
    """Was beim Speichern kodiert wurde, wird beim Auswaehlen gelesen.

    `embedding_json` wurde bei jedem Speichern geschrieben und von keiner Stelle
    je gelesen; stattdessen kodierte `skill_index` bei **jeder** Chatnachricht
    saemtliche sichtbaren Skills neu. Der Test haelt die Aufteilung fest: die
    Frage wird kodiert, die mitgelieferten Dateien auch — sie haben keine Zeile
    und damit keinen Vektor —, die Datenbankskills nicht mehr.
    """
    ai_skill_service.reset_shipped_cache_for_tests()
    _allow(db, regular_user, "ai.skills.use", "ai.skills.manage")

    kodiert: list[str] = []

    def _fake_encode(texts: list[str]) -> list[list[float]]:
        kodiert.extend(texts)
        laenge = ai_embedding_service.EMBEDDING_DIMENSIONS
        return [[1.0] + [0.0] * (laenge - 1) for _ in texts]

    monkeypatch.setattr(ai_embedding_service, "encode", _fake_encode)

    for index in range(ai_skill_service.MAX_INDEXED_SKILLS + 5):
        ai_skill_service.upsert_skill(
            db, user=regular_user, skill_key=f"gespeichert-{index:02d}",
            name=f"Gespeicherter Skill {index}",
            description=f"Ein Eintrag mit fertigem Vektor in der Datenbank, Nummer {index}.",
            body=f"Inhalt {index}", team_id=None,
        )
    abgelegt = db.query(AiSkill).filter(AiSkill.skill_key == "gespeichert-00").one()
    assert abgelegt.embedding_model == ai_skill_service._EMBEDDING_MODEL_TAG

    kodiert.clear()
    frage = "Wie loese ich einen Portkonflikt?"
    auswahl = ai_skill_service.skill_index(db, regular_user, frage)

    assert len(auswahl) == ai_skill_service.MAX_INDEXED_SKILLS
    assert frage in kodiert
    assert [text for text in kodiert if text.startswith("gespeichert ")] == []


# ── Die Warteschlange als eigenes Kontingent ──────────────────────────


def _fill_pending(db: Session, user: User, count: int) -> None:
    """Legt wartende globale Zeilen an — so, wie die Lernpolitik `review` sie erzeugt."""
    for index in range(count):
        db.add(AiSkill(
            id=str(uuid4()), scope_identity="global", team_id=None,
            skill_key=f"vorschlag-{index:03d}", name=f"Vorschlag {index}",
            description=f"Aus einem Kundengespraech entstanden, Nummer {index}.",
            body="Wartet auf Freigabe.", origin="ai", status="pending",
            enabled=True, created_by=user.id,
        ))
    db.commit()


def test_a_full_queue_does_not_lock_out_the_operator(
    db: Session, regular_user: User
) -> None:
    """Wartende Vorschlaege duerfen dem Betreiber nicht das Kontingent wegnehmen.

    Sie entstehen ohne Entscheidung eines Menschen: unter `review` legt jedes
    Kundengespraech welche an, und die Berechtigungspruefung entfaellt dabei
    ausdruecklich. Zaehlten sie mit, koennte ein einzelner Benutzer mit
    `ai.skills.use` den globalen Bereich fuellen und dem Betreiber damit das
    Anlegen eigener Skills sperren — obwohl keiner der Vorschlaege je gewirkt hat.
    """
    _allow(db, regular_user, "ai.skills.use", "ai.skills.manage")
    _fill_pending(db, regular_user, ai_skill_service.MAX_SKILLS_PER_SCOPE)

    row = ai_skill_service.upsert_skill(
        db, user=regular_user, skill_key="eigener-skill", name="Eigener Skill",
        description="Ein Skill, den der Betreiber selbst anlegt, lang genug fuer die Pruefung.",
        body="Inhalt.", team_id=None,
    )

    assert row.status == "active"


def test_the_queue_has_a_limit_of_its_own(db: Session, regular_user: User) -> None:
    """Und sie waechst nicht unbegrenzt.

    Niemand raeumt die Warteschlange automatisch auf, einen Verfall gibt es
    nicht — ohne eigene Grenze wuechse sie im Normalbetrieb dauerhaft.
    """
    _allow(db, regular_user, "ai.skills.use")
    _fill_pending(db, regular_user, ai_skill_service.MAX_PENDING_PER_SCOPE)

    with pytest.raises(Exception) as exc:
        ai_skill_service.upsert_skill(
            db, user=regular_user, skill_key="noch-ein-vorschlag", name="Noch einer",
            description="Ein weiterer Vorschlag aus einem Gespraech, lang genug fuer die Pruefung.",
            body="Wartet.", team_id=None, origin="ai", status="pending",
            skip_permission_check=True,
        )
    assert getattr(exc.value, "status_code", None) == 409
