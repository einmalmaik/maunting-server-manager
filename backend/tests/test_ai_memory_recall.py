"""Das Memory soll sich wie ein Gedaechtnis verhalten, nicht wie eine Liste.

Vorher hing `provider_memory_context` die Eintraege alphabetisch nach Schluessel
aneinander und brach bei 6.000 Zeichen ab. Ein Eintrag "zeitzone" fiel damit
systematisch heraus, "backup" blieb immer drin — unabhaengig davon, was
gebraucht wurde. Diese Datei haelt die drei Eigenschaften fest, die daraus ein
brauchbares Gedaechtnis machen: alles mitnehmen solange es passt, sonst nach
Relevanz auswaehlen, und Herkunft respektieren.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from models import AiMemoryEntry, Role, RolePermission, User
from services import (
    ai_action_errors,
    ai_action_service,
    ai_embedding_service,
    ai_memory_service,
    permission_service,
)
from services.role_service import set_user_roles


def _allow_memory(db: Session, user: User) -> None:
    role = Role(name=f"memory-{user.id}", description=None, is_system=False)
    db.add(role)
    db.flush()
    db.add(RolePermission(role_id=role.id, permission_key="ai.memory.use"))
    db.commit()
    set_user_roles(db, user, [role.id])
    # Seit dem Einwilligungsschritt ist das Gedaechtnis standardmaessig aus.
    # Ein Test, der Erinnerungen im Kontext erwartet, muss es einschalten —
    # genau wie ein Benutzer es tun muesste.
    ai_memory_service.set_preference(db, user, True)


def _write(db: Session, user: User, key: str, value: str, origin: str = "user") -> AiMemoryEntry:
    row, _ = ai_memory_service.upsert_entry(
        db, user=user, scope="user", server_id=None, key=key, value=value, origin=origin,
    )
    return row


def test_everything_fits_so_everything_is_sent(db: Session, regular_user: User) -> None:
    """Der Normalfall: kein Auswaehlen, kein Abschneiden, keine Sprachgrenze.

    Genau deshalb funktioniert das Gedaechtnis sprachuebergreifend ohne
    Embeddings — ein deutscher Eintrag steht auch dann im Kontext, wenn auf
    Englisch gefragt wird, weil das Sprachmodell den Bezug herstellt.
    """
    _allow_memory(db, regular_user)
    _write(db, regular_user, "ram.bevorzugt", "8 GB fuer Minecraft")
    _write(db, regular_user, "zeitzone", "Europe/Berlin")

    block = ai_memory_service.provider_memory_context(
        db, regular_user, query="what timezone do I use?"
    )

    assert "8 GB fuer Minecraft" in block
    assert "Europe/Berlin" in block
    assert "ausgelassen" not in block


def test_a_tight_budget_selects_by_relevance_instead_of_alphabet(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Passt nicht alles, entscheidet der Bezug zur Frage — nicht der Schluessel."""
    _allow_memory(db, regular_user)
    _write(db, regular_user, "aaa.irrelevant", "Voellig anderes Thema ohne Bezug")
    _write(db, regular_user, "zzz.relevant", "Der Backup-Zeitpunkt ist nachts um drei")
    # Budget so klein, dass genau ein Eintrag passt.
    monkeypatch.setattr(ai_memory_service, "MAX_CONTEXT_CHARS", 70)

    block = ai_memory_service.provider_memory_context(
        db, regular_user, query="Wann laeuft mein Backup?"
    )

    assert "Backup-Zeitpunkt" in block
    assert "Voellig anderes Thema" not in block
    # Ehrlich bleiben: das Modell erfaehrt, dass es nicht alles sieht.
    assert "ausgelassen" in block


def test_ein_großes_fenster_trägt_mehr_erinnerungen(
    db: Session, regular_user: User
) -> None:
    """Der Platz des Gedächtnisses kommt vom Aufrufer, nicht aus einer Konstante.

    Bis hierher rechnete dieser Block als einziger gegen feste 6.000 Zeichen,
    während Werkzeugdaten, Zusammenfassung und Historie mit dem Fenster des
    Modells wuchsen. Bei neunzig Notizen hieß das: das Modell bekam gut die
    Hälfte davon und dazu den Satz, dass etwas fehle — obwohl im Fenster
    daneben Zehntausende Zeichen frei blieben.

    Der Vorgabefall bleibt dieselbe Zusage wie vorher und steht hier als
    Gegenprobe daneben.
    """
    _allow_memory(db, regular_user)
    for nummer in range(90):
        _write(
            db, regular_user, f"notiz{nummer:03d}",
            f"Eine ausführliche Notiz Nummer {nummer}, {'Wortfüllung ' * 6}",
        )

    weit = ai_memory_service.provider_memory_context(
        db, regular_user, query="Was weisst du?", budget=60_000
    )
    eng = ai_memory_service.provider_memory_context(
        db, regular_user, query="Was weisst du?"
    )

    assert len(weit.splitlines()) == 90
    assert "ausgelassen" not in weit
    # Ohne Budget gilt weiterhin der Sockel — und der reicht nicht für alle.
    assert "ausgelassen" in eng


def test_der_zeilendeckel_wandert_mit_dem_budget(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Deckel vor der Entschlüsselung hängt am Budget, nicht an sich selbst.

    `MAX_CONTEXT_ROWS` ist gegen das Sockelbudget gerechnet — das Doppelte
    dessen, was bei kurzen Einträgen überhaupt hineinpasst. Bliebe er stehen,
    während das Budget mit dem Fenster wächst, wäre die Begründung der Zahl
    falsch: der Block meldete "ausgelassen", obwohl daneben Platz frei ist,
    und zwar genau bei den vielen kurzen Einträgen, für die der Deckel
    überhaupt gemacht ist.
    """
    _allow_memory(db, regular_user)
    for nummer in range(20):
        _write(db, regular_user, f"eintrag{nummer:02d}", f"Wert {nummer}")
    monkeypatch.setattr(ai_memory_service, "MAX_CONTEXT_ROWS", 3)
    zaehler = _zaehle_entschluesselungen(monkeypatch)

    ai_memory_service.provider_memory_context(
        db, regular_user, query="Was weisst du?"
    )
    beim_sockel = zaehler[0]
    ai_memory_service.provider_memory_context(
        db, regular_user, query="Was weisst du?",
        budget=4 * ai_memory_service.MAX_CONTEXT_CHARS,
    )

    assert beim_sockel == 3
    # Viermal soviel Platz, viermal soviele Kandidaten — und nicht mehr.
    assert zaehler[0] - beim_sockel == 12


def test_frequently_used_entries_survive_a_foreign_language_question(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nutzung ist der sprachunabhaengige Anteil der Auswahl.

    Ein Wortabgleich greift nur innerhalb einer Sprache. Fragt jemand auf
    Englisch, waere ein rein lexikalisches Ranking blind — dann entscheidet,
    was sich in der Vergangenheit als wichtig erwiesen hat.
    """
    _allow_memory(db, regular_user)
    important = _write(db, regular_user, "wichtig", "Etwas dauerhaft Wichtiges")
    _write(db, regular_user, "unwichtig", "Etwas nie Gebrauchtes")
    important.use_count = 15
    important.last_used_at = datetime.now(timezone.utc)
    db.commit()
    monkeypatch.setattr(ai_memory_service, "MAX_CONTEXT_CHARS", 60)

    block = ai_memory_service.provider_memory_context(
        db, regular_user, query="please summarise my setup"
    )

    assert "dauerhaft Wichtiges" in block
    assert "nie Gebrauchtes" not in block


def test_nutzung_schlaegt_nie_den_bezug_zur_frage(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die Gegenprobe zum Test darüber: bei Gleichstand ja, gegen die Frage nie.

    Nutzung ist der Ausschlag, wenn nichts anderes trägt — nicht der Maßstab.
    Genau das ging verloren, weil sie als einzige als **rohe Zahl** in die Summe
    ging: `min(use_count, 20)` reicht bis 20, während Bedeutung und Aktualität
    zwischen 0 und 1 liegen. Mit dem Gewicht 0,5 waren das bis zu 10,0 Punkte
    allein dafür, dass ein Eintrag oft abgerufen wurde, gegen höchstens 6,0 aus
    der Bedeutung.

    Gemessen am 19.08.2026 an 5.000 Einträgen: von den 65 Zeilen im Block
    standen 65 überwiegend wegen ihrer Nutzung dort. Zwei der zehn gesuchten
    Antworten fielen deshalb heraus, obwohl die Vorauswahl sie auf Platz 1
    gesetzt hatte — die zweite Stufe warf weg, was die erste als das Passendste
    ausgewählt hatte.

    Der Aufbau hier ist derselbe Fall im Kleinen: ein Eintrag, der die Frage
    trifft und noch nie gebraucht wurde, gegen drei vielgenutzte ohne jeden
    Bezug. Nimmt man die Normierung heraus, gewinnt der Ballast mit 12,0 gegen
    8,0 und dieser Test wird rot.
    """
    _allow_memory(db, regular_user)
    # Ein Wort Überlappung mit der Frage ("Sicherung"), sonst nichts.
    _write(db, regular_user, "sicherung.zeitpunkt", "Nachts um drei Uhr")
    for nummer in range(3):
        ballast = _write(
            db, regular_user, f"ballast{nummer}", "Ein oft gebrauchter Merksatz"
        )
        ballast.use_count = 20
        ballast.last_used_at = datetime.now(timezone.utc)
    db.commit()
    # Platz für genau eine Zeile — die Rangfolge entscheidet allein.
    monkeypatch.setattr(ai_memory_service, "MAX_CONTEXT_CHARS", 60)

    block = ai_memory_service.provider_memory_context(
        db, regular_user, query="Wann laeuft meine Sicherung?"
    )

    assert "Nachts um drei Uhr" in block
    assert "Merksatz" not in block


def test_reading_the_memory_records_the_usage(db: Session, regular_user: User) -> None:
    """Das Zählwerk ist das Gedächtnis des Gedächtnisses.

    Gezählt wird, wen die Frage getroffen hat: "Wert?" und der Eintrag
    überlappen im Wort, also ist er gebraucht worden.
    """
    _allow_memory(db, regular_user)
    row = _write(db, regular_user, "gezaehlt", "Wert")
    assert row.use_count == 0

    ai_memory_service.provider_memory_context(
        db, regular_user, query="Wert?")
    db.refresh(row)

    assert row.use_count == 1
    assert row.last_used_at is not None


def test_a_search_hit_counts_as_usage(db: Session, regular_user: User) -> None:
    """Die Suche ist der eine unstrittige Gebrauch.

    Hier hat jemand ausdrücklich gesucht und bekommt genau diese Zeilen
    vorgelegt — anders als beim Abruf in den Kontext, wo vieles mitgeht, um das
    niemand gebeten hat. Ohne diesen Vermerk wäre `search_memory` der einzige
    echte Zugriff, der spurlos bliebe.
    """
    _allow_memory(db, regular_user)
    row = _write(db, regular_user, "hund", "Der Hund heisst Bello")

    treffer = ai_memory_service.search_entries(db, regular_user, query="Hund")

    assert [gefunden.key for gefunden, _wert, _rang in treffer] == ["hund"]
    # Die Suche schreibt, sonst bliebe der einzige echte Zugriff spurlos.
    db.refresh(row)
    assert row.use_count == 1
    assert row.last_used_at is not None


def test_the_ai_never_silently_overwrites_what_the_user_said(
    db: Session, regular_user: User
) -> None:
    """Eine Ableitung darf keine ausdrueckliche Ansage korrigieren."""
    _allow_memory(db, regular_user)
    _write(db, regular_user, "ram.bevorzugt", "16 GB", origin="user")

    with pytest.raises(HTTPException) as excinfo:
        _write(db, regular_user, "ram.bevorzugt", "4 GB", origin="ai")

    assert excinfo.value.status_code == 409
    stored = ai_memory_service.list_entries(db, regular_user, "user", None)
    assert stored[0][1] == "16 GB"


def test_the_ai_updates_its_own_entry_under_the_same_key(
    db: Session, regular_user: User
) -> None:
    """Konsolidieren statt sammeln: derselbe Schluessel ersetzt den Wert."""
    _allow_memory(db, regular_user)
    _write(db, regular_user, "spielzeit", "abends", origin="ai")
    _write(db, regular_user, "spielzeit", "am Wochenende", origin="ai")

    stored = ai_memory_service.list_entries(db, regular_user, "user", None)

    assert len(stored) == 1
    assert stored[0][1] == "am Wochenende"


def test_server_scoped_memory_reaches_the_context_with_its_server_id(
    db: Session, regular_user: User
) -> None:
    """Regression: serverbezogenes Memory war schreibbar, aber unlesbar.

    Der Kontextaufbau uebergab fest ``server_id=None``, weil die Unterhaltung
    seit dem Einzelchat keinen Serverbezug mehr hat. Die KI konnte sich damit
    etwas zu einem Server merken und sah es nie wieder — ein Gedaechtnis, das
    nur schreibt, ist keines.

    Die Server-ID muss in der Zeile stehen, sonst wendet das Modell eine
    Eigenheit von Server A auf Server B an.
    """
    from models import Server, ServerPermission

    _allow_memory(db, regular_user)
    server = Server(
        name="Memory-Server", game_type="dayz",
        install_dir="/tmp/memory-server", status="stopped",
    )
    db.add(server)
    db.commit()
    db.add(ServerPermission(
        user_id=regular_user.id, server_id=server.id, permission_key="server.view"
    ))
    db.commit()
    ai_memory_service.upsert_entry(
        db, user=regular_user, scope="server", server_id=server.id,
        key="startzeit", value="Startet nur mit erhoehtem Timeout", origin="ai",
    )

    block = ai_memory_service.provider_memory_context(
        db, regular_user, query="Warum startet der Server so langsam?"
    )

    assert "erhoehtem Timeout" in block
    assert f"server:{server.id}" in block


def test_losing_access_to_a_server_removes_its_memory_from_the_context(
    db: Session, regular_user: User
) -> None:
    """Die Sichtbarkeit wird bei jedem Abruf neu geprueft, nicht beim Schreiben."""
    from models import Server, ServerPermission

    _allow_memory(db, regular_user)
    server = Server(
        name="Entzogen", game_type="dayz",
        install_dir="/tmp/entzogen", status="stopped",
    )
    db.add(server)
    db.commit()
    permission = ServerPermission(
        user_id=regular_user.id, server_id=server.id, permission_key="server.view"
    )
    db.add(permission)
    db.commit()
    ai_memory_service.upsert_entry(
        db, user=regular_user, scope="server", server_id=server.id,
        key="notiz", value="Etwas ueber diesen Server", origin="ai",
    )

    db.delete(permission)
    db.commit()
    block = ai_memory_service.provider_memory_context(db, regular_user, query="Notiz?")

    assert block is None or "Etwas ueber diesen Server" not in block


def test_the_prefilter_alone_holds_for_a_user_without_any_server(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Vorfilter darf aus "sieht nichts" kein "sieht alles" machen.

    `list_visible_server_ids` kennt drei Antworten: `None` heisst *alle Server*
    (Betreiber oder eine Rolle mit pauschalem `server.view`), `[]` heisst
    *keinen einzigen*. Beide sind in Python falsy; ein `if sichtbare:` an der
    Filterstelle behandelte den zweiten Fall wie den ersten.

    Die zeilenweise Nachpruefung faengt das im Betrieb ohnehin ab — genau
    deshalb wird sie hier ausgehebelt. Ohne diesen Handgriff bliebe der Test
    auch mit falschem Vorfilter gruen und wuerde nur die Nachpruefung messen,
    die er gar nicht prueft. Was hier zugesichert wird, ist die zweite
    Verteidigungslinie: der Vorfilter muss fuer sich allein richtig sein.
    """
    from models import Server, ServerPermission

    _allow_memory(db, regular_user)
    server = Server(
        name="Fremder", game_type="dayz",
        install_dir="/tmp/fremder", status="stopped",
    )
    db.add(server)
    db.commit()

    # Kurz sehen duerfen, um die Notiz ueberhaupt anlegen zu koennen ...
    permission = ServerPermission(
        user_id=regular_user.id, server_id=server.id, permission_key="server.view"
    )
    db.add(permission)
    db.commit()
    ai_memory_service.upsert_entry(
        db, user=regular_user, scope="server", server_id=server.id,
        key="geheim", value="Etwas ueber einen fremden Server", origin="ai",
    )

    # ... und danach gar keinen Server mehr sehen duerfen.
    db.delete(permission)
    db.commit()
    assert permission_service.list_visible_server_ids(db, regular_user) == []

    monkeypatch.setattr(
        ai_memory_service.permission_service, "has_server_permission",
        lambda **_kwargs: True,
    )
    rows = ai_memory_service._visible_scope_rows(db, regular_user)

    assert [row for row in rows if row.scope == "server"] == []


def test_a_server_seen_only_through_a_team_keeps_its_memory(
    db: Session, regular_user: User
) -> None:
    """Der Vorfilter darf auch nicht enger sein als die Nachpruefung.

    Das ist die gefaehrlichere Richtung: zu weit faengt die Nachpruefung ab, zu
    eng faengt niemand. Der Eintrag verschwaende dann still aus dem Kontext,
    ohne Fehler und ohne Hinweis — die KI wuesste einfach nichts mehr davon.

    `list_visible_server_ids` und `has_server_permission` muessen dieselbe Menge
    meinen. Hier zaehlt der Weg ueber ein Team, weil geliehene Teamrechte der
    uebliche Freigabeweg im Panel sind.
    """
    from models import Server, ServerPermission, Team, TeamMember, TeamServerGrant

    _allow_memory(db, regular_user)
    besitzer = User(
        username="teamowner", email="teamowner@example.com",
        password_hash="x", is_active=True,
    )
    server = Server(
        name="Teamserver", game_type="dayz",
        install_dir="/tmp/teamserver", status="stopped",
    )
    db.add_all([besitzer, server])
    db.commit()

    team = Team(name="Betrieb", owner_user_id=besitzer.id)
    db.add(team)
    db.commit()
    db.add_all([
        # Ein Team verleiht nur, was sein Gruender selbst haelt — ohne dieses
        # direkte Recht traegt die Zuteilung unten nichts.
        ServerPermission(
            user_id=besitzer.id, server_id=server.id, permission_key="server.view"
        ),
        TeamMember(team_id=team.id, user_id=regular_user.id, role="member"),
        TeamServerGrant(
            team_id=team.id, server_id=server.id,
            permission_key="server.view", granted_by=besitzer.id,
        ),
    ])
    db.commit()

    assert server.id in (permission_service.list_visible_server_ids(db, regular_user) or [])
    ai_memory_service.upsert_entry(
        db, user=regular_user, scope="server", server_id=server.id,
        key="eigenheit", value="Braucht nach dem Start zwei Minuten", origin="ai",
    )

    block = ai_memory_service.provider_memory_context(db, regular_user, query="Warum so langsam?")

    assert block is not None and "zwei Minuten" in block


def test_one_unreadable_entry_does_not_take_the_whole_chat_down(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ein Gedaechtnis ist eine Beigabe. Es darf fehlen, nicht im Weg stehen.

    Vorher entschluesselte der Abruf in einer Listenauswertung ohne `try`. Eine
    einzige Zeile, die sich nicht mehr oeffnen liess, warf bis in
    `build_provider_messages`; der Aufrufer in `ai_stream_service` faengt dort
    `DisSidecarError` und uebersetzt ihn zu `AI_CREDENTIAL_UNAVAILABLE` — der
    Lauf begann gar nicht erst, und zwar jedes Mal wieder.

    Geprueft werden beide Aufrufstellen. Sie teilen sich denselben Helfer, aber
    genau darum geht es: faellt eine davon spaeter auf die Listenauswertung
    zurueck, faellt hier auch nur diese eine Zusicherung um. Der Weg ueber die
    Suche traegt eigene Folgen — dort scheitert nicht der Lauf, sondern das
    Werkzeug `search_memory` mitten in einer Antwort.
    """
    from services.dis_client import DisClient, DisDecryptionError

    _allow_memory(db, regular_user)
    kaputt = _write(db, regular_user, "kaputt", "Unlesbarer Wert")
    _write(db, regular_user, "heil", "Lesbarer Wert")
    # Die Kennung **hier** festhalten und nicht in der Attrappe von der Zeile
    # lesen: die Attrappe laeuft in einem Arbeitsthread von
    # `_entschluesseln_nebenlaeufig`, und ein Zugriff auf ein SQLAlchemy-Objekt
    # koennte dort nachladen — auf einer Sitzung, die nur dem Hauptthread
    # gehoert. Genau die Trennung, die der Dienst selbst einhaelt.
    kaputte_id = kaputt.id

    echt = DisClient.decrypt

    def stolpert(payload, *, aad):
        if aad.endswith(kaputte_id):
            raise DisDecryptionError("AAD passt nicht mehr")
        return echt(payload, aad=aad)

    monkeypatch.setattr(DisClient, "decrypt", staticmethod(stolpert))

    block = ai_memory_service.provider_memory_context(db, regular_user, query="Was weisst du?")

    assert block is not None
    assert "Lesbarer Wert" in block
    assert "Unlesbarer Wert" not in block

    treffer = ai_memory_service.search_entries(db, regular_user, query="Wert")

    assert [row.key for row, _value, _score in treffer] == ["heil"]


def test_remember_requires_the_memory_permission(db: Session, regular_user: User) -> None:
    """Wer sein Memory nicht nutzen darf, bekommt auch keines geschrieben."""
    with pytest.raises(ai_action_errors.AiActionValidationError):
        ai_action_service.execute_read_tool(
            db, user=regular_user, tool_name="remember",
            arguments={"scope": "user", "key": "test", "value": "Wert"},
        )


def test_remember_rejects_secrets_and_the_panel_scope(
    db: Session, regular_user: User
) -> None:
    """Zwei Grenzen, die das Werkzeug nicht verschieben darf."""
    _allow_memory(db, regular_user)

    # Panelweites Memory gilt fuer alle Benutzer — das ist eine
    # Betreiberentscheidung und nicht die der KI.
    with pytest.raises(ai_action_errors.AiActionValidationError):
        ai_action_service.execute_read_tool(
            db, user=regular_user, tool_name="remember",
            arguments={"scope": "panel", "key": "regel", "value": "Wert"},
        )

    with pytest.raises(ai_action_errors.AiActionValidationError):
        ai_action_service.execute_read_tool(
            db, user=regular_user, tool_name="remember",
            arguments={
                "scope": "user", "key": "zugang",
                "value": "api_key=sk-abcdefghijklmnopqrstuvwxyz012345",
            },
        )


def test_remember_stores_a_preference_with_its_origin(
    db: Session, regular_user: User
) -> None:
    _allow_memory(db, regular_user)

    result = ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="remember",
        arguments={"scope": "user", "key": "ram.bevorzugt", "value": "8 GB"},
    )

    assert result["remembered"] is True
    row = db.query(AiMemoryEntry).filter(AiMemoryEntry.key == "ram.bevorzugt").one()
    assert row.origin == "ai"
    assert row.scope == "user"


def test_a_disabled_memory_is_not_read_at_all(db: Session, regular_user: User) -> None:
    """Der Abschalter des Benutzers gilt vor jeder Auswahl."""
    _allow_memory(db, regular_user)
    _write(db, regular_user, "vorhanden", "Wert")
    ai_memory_service.set_preference(db, regular_user, False)

    assert ai_memory_service.provider_memory_context(
        db, regular_user, query="Wert?"
    ) is None


def test_memory_of_one_user_never_reaches_another(
    db: Session, regular_user: User, owner_user: User
) -> None:
    """Die Trennung je Benutzer ist im Hoster-Betrieb die zentrale Zusage."""
    _allow_memory(db, regular_user)
    _allow_memory(db, owner_user)
    _write(db, regular_user, "privat", "Nur fuer den einen Benutzer")

    block = ai_memory_service.provider_memory_context(
        db, owner_user, query="privat"
    )

    assert block is None


def test_recency_beats_an_old_never_used_entry(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Frisch Gemerktes braucht eine Chance, obwohl ihm die Historie fehlt."""
    _allow_memory(db, regular_user)
    old = _write(db, regular_user, "alt", "Lange her und nie gebraucht")
    fresh = _write(db, regular_user, "neu", "Gerade eben gemerkt")
    old.updated_at = datetime.now(timezone.utc) - timedelta(days=120)
    old.last_used_at = None
    fresh.last_used_at = None
    db.commit()
    monkeypatch.setattr(ai_memory_service, "MAX_CONTEXT_CHARS", 60)

    block = ai_memory_service.provider_memory_context(
        db, regular_user, query="unrelated question in english"
    )

    assert "Gerade eben gemerkt" in block
    assert "Lange her" not in block


def _zaehle_entschluesselungen(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Zaehlt die Aufrufe von `DisClient.decrypt` — die Groesse, um die es geht.

    Jeder Aufruf ist ein eigener HTTP-Roundtrip zum DIS-Sidecar. An der
    Laenge des Ergebnisses laesst sich das nicht ablesen: der Block wird danach
    ohnehin auf `MAX_CONTEXT_CHARS` gekuerzt und sieht mit und ohne Deckel
    gleich aus. Gemessen werden muss der Aufwand, nicht das Ergebnis.

    Die Sperre ist keine Vorsicht auf Vorrat: seit `_entschluesseln` die Zeilen
    zu mehreren gleichzeitig oeffnet, laeuft diese Attrappe in mehreren Threads,
    und `zaehler[0] += 1` ist Lesen, Rechnen und Schreiben in drei Schritten.
    Ohne sie zaehlte der Test gelegentlich zu wenig und waere launisch statt
    aussagekraeftig.
    """
    import threading

    from services.dis_client import DisClient

    zaehler = [0]
    sperre = threading.Lock()
    echt = DisClient.decrypt

    def mitzaehlen(payload, *, aad):
        with sperre:
            zaehler[0] += 1
        return echt(payload, aad=aad)

    monkeypatch.setattr(DisClient, "decrypt", staticmethod(mitzaehlen))
    return zaehler


def _server_mit_notiz(db: Session, user: User, nummer: int, wert: str) -> None:
    """Ein sichtbarer Server plus eine persoenliche Notiz dazu — ein Bereich mehr."""
    from models import Server, ServerPermission

    server = Server(
        name=f"Anlage {nummer}", game_type="dayz",
        install_dir=f"/tmp/anlage-{nummer}", status="stopped",
    )
    db.add(server)
    db.commit()
    db.add(ServerPermission(
        user_id=user.id, server_id=server.id, permission_key="server.view"
    ))
    db.commit()
    ai_memory_service.upsert_entry(
        db, user=user, scope="server", server_id=server.id,
        key=f"eigenheit{nummer}", value=wert, origin="ai",
    )


def test_eine_anfrage_entschluesselt_nie_mehr_als_der_deckel_erlaubt(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Aufwand einer Anfrage haengt am Deckel, nicht an der Zahl der Bereiche.

    `max_memory_entries` begrenzt einen **Bereich**. Wieviele Bereiche ein
    Benutzer hat, bestimmt er selbst — mit jedem sichtbaren Server kommt einer
    hinzu. Ohne diesen Deckel war die Menge je Anfrage Bereiche mal Rollenlimit,
    und jede Zeile kostet einen synchronen Sidecar-Roundtrip **vor** dem Schnitt
    auf 6.000 Zeichen, weil sich erst am Klartext messen laesst, was hineinpasst.
    """
    _allow_memory(db, regular_user)
    _write(db, regular_user, "grundregel", "Immer erst das Backup pruefen")
    for nummer in range(6):
        _server_mit_notiz(db, regular_user, nummer, f"Eigenheit der Anlage {nummer}")
    monkeypatch.setattr(ai_memory_service, "MAX_CONTEXT_ROWS", 3)
    zaehler = _zaehle_entschluesselungen(monkeypatch)

    block = ai_memory_service.provider_memory_context(
        db, regular_user, query="Was muss ich bei den Anlagen beachten?"
    )

    assert block is not None
    # Sieben Bereiche waeren sieben Entschluesselungen gewesen.
    assert zaehler[0] == 3


def test_unterhalb_des_deckels_bleibt_alles_wie_es_war(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Deckel ist kein stiller Umbau der Auswahl.

    Solange weniger Zeilen anfallen als er erlaubt, reicht `_vorauswahl` sie
    unveraendert durch — dieselben Eintraege, dieselbe Reihenfolge, dieselbe
    Zahl an Entschluesselungen. Sonst haette diese Aenderung den Normalfall
    angefasst, um einen Randfall zu retten.
    """
    _allow_memory(db, regular_user)
    _write(db, regular_user, "alpha", "Erster Wert")
    _write(db, regular_user, "beta", "Zweiter Wert")
    monkeypatch.setattr(ai_memory_service, "MAX_CONTEXT_ROWS", 300)
    zaehler = _zaehle_entschluesselungen(monkeypatch)

    block = ai_memory_service.provider_memory_context(
        db, regular_user, query="Was weisst du?"
    )

    assert "Erster Wert" in block and "Zweiter Wert" in block
    assert zaehler[0] == 2
    # Und der Block behauptet nicht, es fehle etwas.
    assert "gekuerzt" not in block.lower() and "nicht alles" not in block.lower()


def test_die_vorauswahl_nimmt_die_passenden_und_nicht_die_ersten(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gekuerzt wird nach Rang, nicht nach Reihenfolge.

    Die Vorauswahl laeuft vor der Entschluesselung und kennt den Wert deshalb
    nicht — wohl aber den Schluessel, den gespeicherten Vektor und das Alter.
    Genau davon muss sie Gebrauch machen: ein blosses "nimm die ersten N" waere
    hier rot, weil der gesuchte Eintrag zuletzt angelegt wurde.
    """
    _allow_memory(db, regular_user)
    for nummer in range(6):
        _write(db, regular_user, f"belanglos{nummer}", f"Fuellwert {nummer}")
    _write(db, regular_user, "wartungsfenster", "Sonntags ab drei Uhr")
    monkeypatch.setattr(ai_memory_service, "MAX_CONTEXT_ROWS", 2)

    block = ai_memory_service.provider_memory_context(
        db, regular_user, query="Wann ist das wartungsfenster?"
    )

    assert "Sonntags ab drei Uhr" in block


def test_die_vorauswahl_folgt_der_frage_und_nicht_der_nutzung(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Vor dem Entschluesseln zaehlt nur, was mit der Frage zu tun hat.

    Diese Stufe entscheidet nicht, welcher Eintrag *besser* ist, sondern welcher
    ueberhaupt geoeffnet und damit dem Modell gezeigt wird. Solange die Nutzung
    dort mitzaehlte, gewann eine oft gebrauchte Zeile gegen eine, die die Frage
    woertlich trifft — und zwar umso sicherer, je groesser der Vorrat wird: bei
    5.000 Eintraegen bildeten Nutzung und Aktualitaet die Schwelle des letzten
    Platzes allein, waehrend die Bedeutung hoechstens ein Drittel davon
    beisteuern konnte. Gemessen ueberlebten damit 4 von 10 gesuchten Eintraegen
    den Schnitt, ohne den Nutzungsterm 7.

    Der Nutzungsanteil ist damit nicht abgeschafft, er steht eine Stufe spaeter
    (siehe `test_frequently_used_entries_survive_a_foreign_language_question`).
    Hier ist er falsch, weil er nichts ueber die Frage aussagt.
    """
    _allow_memory(db, regular_user)
    vielgenutzt = _write(db, regular_user, "lieblingsfarbe", "Blau, seit jeher")
    vielgenutzt.use_count = 20
    vielgenutzt.last_used_at = datetime.now(timezone.utc)
    gesucht = _write(db, regular_user, "wartungsfenster", "Sonntags ab drei Uhr")
    # Der gesuchte Eintrag ist zusaetzlich der aeltere und nie gebrauchte —
    # nach jedem Massstab ausser dem Bezug zur Frage der schlechtere.
    gesucht.last_used_at = datetime.now(timezone.utc) - timedelta(days=30)
    db.commit()
    # Genau eine Zeile ueberlebt die Vorauswahl. Welche, ist die ganze Frage.
    monkeypatch.setattr(ai_memory_service, "MAX_CONTEXT_ROWS", 1)

    block = ai_memory_service.provider_memory_context(
        db, regular_user, query="Wann ist das wartungsfenster?"
    )

    assert "Sonntags ab drei Uhr" in block
    assert "Blau" not in block


def _achsen_encode(texts: list[str]) -> list[list[float]]:
    """Ein Modellersatz mit genau zwei Bedeutungen: Wartung und Farbe.

    Reicht für die eine Frage, um die es hier geht — trägt der Vektor die
    Auswahl —, und ist unabhängig davon, ob das echte Modell installiert ist.
    """
    vektoren = []
    for text in texts:
        gesenkt = text.lower()
        achse = 0 if ("wartung" in gesenkt or "maintenance" in gesenkt) else 1
        vektor = [0.0] * ai_embedding_service.EMBEDDING_DIMENSIONS
        vektor[achse] = 1.0
        vektoren.append(vektor)
    return vektoren


def test_die_vorauswahl_liest_auch_bestandszeilen_im_alten_format(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Während des Formatwechsels muss das Gedächtnis weiter finden.

    Seit dem 19.08.2026 liegen die Vektoren als float32-Bytes statt als JSON —
    das Lesen kostete bei 5.000 Einträgen 381 ms von 717 ms Gesamtrechenzeit.
    Zwischen dem Einspielen des Codes und dem Durchlauf der Migration
    `20260819_01` steht der Bestand aber noch im alten Format, und in dieser
    Zeit darf die Vorauswahl nicht auf Aktualität zurückfallen: sie
    entscheidet, was überhaupt entschlüsselt wird, und was sie wegwirft,
    holt keine spätere Stufe zurück.

    Die Frage ist englisch und teilt mit beiden Einträgen kein Wort — es gibt
    also nichts außer dem Vektor, was sie mit dem richtigen verbindet. Der
    falsche ist zusätzlich der frischere und gewänne jede Rangfolge, die die
    Bedeutung nicht lesen kann.
    """
    _allow_memory(db, regular_user)
    monkeypatch.setattr(ai_embedding_service, "encode", _achsen_encode)
    gesucht = _write(db, regular_user, "wartungsfenster", "Sonntags ab drei Uhr")
    frisch = _write(db, regular_user, "lieblingsfarbe", "Blau, seit jeher")
    gesucht.last_used_at = datetime.now(timezone.utc) - timedelta(days=30)
    frisch.last_used_at = datetime.now(timezone.utc)
    # Der Stand vor der Migration: Vektor als Text, Byte-Spalte noch leer.
    for row in (gesucht, frisch):
        row.embedding_json = json.dumps(list(_achsen_encode([row.key])[0]))
        row.embedding_bytes = None
    db.commit()
    # Genau eine Zeile überlebt die Vorauswahl. Welche, ist die ganze Frage.
    monkeypatch.setattr(ai_memory_service, "MAX_CONTEXT_ROWS", 1)

    block = ai_memory_service.provider_memory_context(
        db, regular_user, query="When is the next maintenance?"
    )

    assert "Sonntags ab drei Uhr" in block
    assert "Blau" not in block


def test_wer_gekuerzt_bekommt_erfaehrt_es_auch(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hat die Vorauswahl gekuerzt, sagt der Block es — wie beim Budgetschnitt.

    Ein stilles Weglassen waere die Unehrlichkeit, gegen die der Hinweis
    ueberhaupt existiert: das Modell soll aus einer Luecke nicht schliessen, es
    gebe nichts. Der Hinweis haengt bisher am Budget; die zweite Engstelle muss
    ihn genauso setzen.
    """
    _allow_memory(db, regular_user)
    for nummer in range(5):
        _write(db, regular_user, f"eintrag{nummer}", f"Wert {nummer}")
    monkeypatch.setattr(ai_memory_service, "MAX_CONTEXT_ROWS", 2)

    gekuerzt = ai_memory_service.provider_memory_context(
        db, regular_user, query="Was weisst du?"
    )

    monkeypatch.setattr(ai_memory_service, "MAX_CONTEXT_ROWS", 300)
    vollstaendig = ai_memory_service.provider_memory_context(
        db, regular_user, query="Was weisst du?"
    )

    assert gekuerzt != vollstaendig
    # Der Hinweis steht im gekuerzten Block und fehlt im vollstaendigen.
    zusatz = set(gekuerzt.splitlines()) - set(vollstaendig.splitlines())
    assert zusatz, "der gekuerzte Block traegt keinen Hinweis auf das Fehlende"


def test_der_abruf_oeffnet_die_zeilen_gleichzeitig(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die Wartezeit am Sidecar darf nicht mit der Zahl der Eintraege wachsen.

    Jede Zeile ist ein eigener HTTP-Roundtrip, und daran ist fast nichts
    Rechnung — der Sidecar oeffnet ein paar hundert Byte, alles andere ist der
    Weg hin und zurueck. Nacheinander addiert sich genau diese Wartezeit vor dem
    ersten Byte der Antwort: gemessen 150 bis 600 ms bei 300 Zeilen, und in der
    ungedeckelten Profilansicht (`personal_entries`) 10,3 s bei 5.000.

    Die Sperre in diesem Test ist zugleich die Pruefung. Jede der ersten beiden
    Entschluesselungen wartet an derselben Schranke, und die oeffnet nur, wenn
    beide zugleich davorstehen. Liefe der Abruf wieder Zeile fuer Zeile, kaeme
    die zweite nie an — die erste liefe in den Zeitablauf der Schranke und der
    Test schluege fehl, statt lediglich langsamer zu sein.
    """
    import threading

    from services.dis_client import DisClient

    _allow_memory(db, regular_user)
    for nummer in range(4):
        _write(db, regular_user, f"eintrag{nummer}", f"Wert {nummer}")

    schranke = threading.Barrier(2, timeout=5)
    sperre = threading.Lock()
    gesehen: list[str] = []
    echt = DisClient.decrypt

    def wartet_aufeinander(payload, *, aad):
        with sperre:
            zuerst = len(gesehen) < 2
            gesehen.append(aad)
        if zuerst:
            schranke.wait()
        return echt(payload, aad=aad)

    monkeypatch.setattr(DisClient, "decrypt", staticmethod(wartet_aufeinander))

    block = ai_memory_service.provider_memory_context(
        db, regular_user, query="Was weisst du?"
    )

    assert block is not None
    assert len(block.splitlines()) == 4


def test_die_suche_entschluesselt_ebenfalls_nicht_alles(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`search_memory` gab fuenfzehn Treffer zurueck und oeffnete dafuer alles.

    Derselbe Deckel, derselbe Grund — und die Treffer bleiben die besten: die
    Vorauswahl bewertet nach denselben Kriterien, die `search_entries` gleich
    danach anlegt, nur ohne den Wert.
    """
    _allow_memory(db, regular_user)
    for nummer in range(6):
        _write(db, regular_user, f"belanglos{nummer}", f"Fuellwert {nummer}")
    _write(db, regular_user, "wartungsfenster", "Sonntags ab drei Uhr")
    monkeypatch.setattr(ai_memory_service, "MAX_CONTEXT_ROWS", 3)
    zaehler = _zaehle_entschluesselungen(monkeypatch)

    treffer = ai_memory_service.search_entries(
        db, regular_user, query="wartungsfenster"
    )

    assert zaehler[0] == 3
    assert "wartungsfenster" in [row.key for row, _value, _score in treffer]
