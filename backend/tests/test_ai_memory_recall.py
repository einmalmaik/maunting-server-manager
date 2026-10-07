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

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from models import AiMemoryEntry, Role, RolePermission, User
from services import (
    ai_embedding_service,
    ai_gedaechtnis_abruf,
    ai_gedaechtnis_pflege,
    ai_memory_service,
    permission_service,
)
from services.role_service import set_user_roles
from tests._einbettung import ohne_modell


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
    # Ohne Modell: sonst kämen Kandidaten aus dem Vektorspeicher dazu, und
    # gezählt würde, was auf dieser Maschine liegt, nicht der Deckel.
    ohne_modell(monkeypatch)
    _allow_memory(db, regular_user)
    for nummer in range(20):
        _write(db, regular_user, f"eintrag{nummer:02d}", f"Wert {nummer}")
    monkeypatch.setattr(ai_memory_service, "MAX_CONTEXT_ROWS", 3)
    zaehler = _zaehle_entschluesselungen(monkeypatch)

    ai_memory_service.provider_memory_context(
        db, regular_user, query="Was weisst du?"
    )
    beim_sockel = zaehler.texte
    ai_memory_service.provider_memory_context(
        db, regular_user, query="Was weisst du?",
        budget=4 * ai_memory_service.MAX_CONTEXT_CHARS,
    )

    assert beim_sockel == 3
    # Viermal soviel Platz, viermal soviele Kandidaten — und nicht mehr.
    assert zaehler.texte - beim_sockel == 12


def test_im_kopf_entscheidet_die_wichtigkeit_nicht_die_nutzung(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Was ohne passende Frage vorn steht, legt Wichtigkeit × Präsenz fest.

    Bis Stufe 4 entschied bei einer fremdsprachigen Frage die Nutzung: was
    oft abgerufen wurde, blieb. Die Zahl der Abrufe hängt aber an Fragen von
    gestern; der Kopf trägt, was wichtig ist und noch präsent
    (`ai_gedaechtnis_pflege.rang_auffrischen`).
    """
    _allow_memory(db, regular_user)
    wichtig = _write(db, regular_user, "wichtig", "Etwas dauerhaft Wichtiges")
    oft = _write(db, regular_user, "oft", "Etwas oft Gebrauchtes")
    ballast = _write(db, regular_user, "ballast", "Ein langer Eintrag, damit nicht alles ins Budget passt")
    wichtig.wichtigkeit = 5
    oft.use_count = 15
    oft.last_used_at = datetime.now(timezone.utc)
    ballast.wichtigkeit = 1
    db.commit()
    ai_gedaechtnis_pflege.rang_auffrischen(db)
    db.commit()

    # Die Hälfte von 120 fasst genau eine Zeile.
    block = ai_memory_service.provider_memory_context(
        db, regular_user, query="please summarise my setup", budget=120
    )

    assert "dauerhaft Wichtiges" in block
    assert "oft Gebrauchtes" not in block


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
    kennungen = ai_gedaechtnis_abruf.sichtbare_bereiche(
        db, regular_user, persoenlich=True, anlage=True
    )

    assert [kennung for kennung in kennungen if kennung.startswith("server:")] == []


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
    from tests._entschluesselung import unlesbar_machen

    _allow_memory(db, regular_user)
    kaputt = _write(db, regular_user, "kaputt", "Unlesbarer Wert")
    _write(db, regular_user, "heil", "Lesbarer Wert")
    unlesbar_machen(monkeypatch, kaputt.id)

    block = ai_memory_service.provider_memory_context(db, regular_user, query="Was weisst du?")

    assert block is not None
    assert "Lesbarer Wert" in block
    assert "Unlesbarer Wert" not in block

    treffer = ai_memory_service.search_entries(db, regular_user, query="Wert")

    assert [row.key for row, _value, _score in treffer] == ["heil"]


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
    """Frisch Gemerktes braucht eine Chance, obwohl ihm die Historie fehlt.

    Bei gleicher Wichtigkeit steht im Kopf das Präsentere vorn; ein Eintrag,
    den 120 Tage niemand gebraucht hat, ist es nicht mehr.
    """
    _allow_memory(db, regular_user)
    old = _write(db, regular_user, "alt", "Lange her und nie gebraucht")
    fresh = _write(db, regular_user, "neu", "Gerade eben gemerkt")
    ballast = _write(db, regular_user, "ballast", "Ein langer Eintrag, damit nicht alles ins Budget passt")
    old.created_at = datetime.now(timezone.utc) - timedelta(days=120)
    old.last_used_at = None
    fresh.last_used_at = None
    ballast.wichtigkeit = 1
    db.commit()
    ai_gedaechtnis_pflege.rang_auffrischen(db)
    db.commit()

    # Die Hälfte von 120 fasst genau eine Zeile.
    block = ai_memory_service.provider_memory_context(
        db, regular_user, query="unrelated question in english", budget=120
    )

    assert "Gerade eben gemerkt" in block
    assert "Lange her" not in block


def _zaehle_entschluesselungen(monkeypatch: pytest.MonkeyPatch):
    """Zaehlt die geoeffneten Erinnerungstexte — die Groesse, um die es geht.

    An der Laenge des Ergebnisses laesst sich das nicht ablesen: der Block wird
    danach ohnehin auf `MAX_CONTEXT_CHARS` gekuerzt und sieht mit und ohne
    Deckel gleich aus. Gemessen werden muss der Aufwand, nicht das Ergebnis.
    Seit die Texte gebuendelt durch den Sidecar gehen, ist das die Zahl der
    Texte und nicht mehr die der Aufrufe (`tests._entschluesselung`).
    """
    from tests._entschluesselung import mitzaehlen

    return mitzaehlen(monkeypatch)


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
    ohne_modell(monkeypatch)
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
    assert zaehler.texte == 3


def test_unterhalb_des_deckels_bleibt_alles_wie_es_war(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Deckel ist kein stiller Umbau der Auswahl.

    Solange weniger Zeilen anfallen als er erlaubt, steht alles im Kopf
    (`ai_gedaechtnis_abruf._kopf`) — dieselben Eintraege, dieselbe Zahl an
    Entschluesselungen. Sonst haette diese Aenderung den Normalfall
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
    assert zaehler.texte == 2
    # Und der Block behauptet nicht, es fehle etwas.
    assert "gekuerzt" not in block.lower() and "nicht alles" not in block.lower()


def test_was_nicht_in_den_kopf_passt_kommt_auf_die_frage_nach(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Kopf ist gekuerzt, die Antwort fehlt trotzdem nicht.

    Der gesuchte Eintrag ist der aelteste und steht deshalb nicht im Kopf; der
    Wortindex findet ihn, ohne einen Text zu oeffnen.
    """
    _allow_memory(db, regular_user)
    gesucht = _write(db, regular_user, "wartungsfenster", "Sonntags ab drei Uhr")
    gesucht.created_at = datetime.now(timezone.utc) - timedelta(days=30)
    db.commit()
    for nummer in range(6):
        _write(db, regular_user, f"belanglos{nummer}", f"Fuellwert {nummer}")
    monkeypatch.setattr(ai_memory_service, "MAX_CONTEXT_ROWS", 2)

    block = ai_memory_service.provider_memory_context(
        db, regular_user, query="Wann ist das wartungsfenster?"
    )

    assert "Sonntags ab drei Uhr" in block


def test_hinten_kommt_was_die_frage_trifft_und_nicht_was_oft_gebraucht_wurde(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Was nach dem Kopf mitkommt, entscheidet allein die Frage.

    Solange die Nutzung bei der Auswahl mitzaehlte, gewann eine oft gebrauchte
    Zeile gegen eine, die die Frage woertlich trifft — und zwar umso sicherer,
    je groesser der Vorrat wird: gemessen am 19.08.2026 bei 5.000 Eintraegen
    ueberlebten damit 4 von 10 gesuchten Eintraegen den Schnitt, ohne den
    Nutzungsterm 7. Seit Stufe 4 kommt hinten nur mit, wen die Frage trifft;
    die Nutzung ordnet hoechstens die Treffer untereinander.
    """
    _allow_memory(db, regular_user)
    gesucht = _write(db, regular_user, "wartungsfenster", "Sonntags ab drei Uhr")
    # Der gesuchte Eintrag ist der aeltere und nie gebrauchte — nach jedem
    # Massstab ausser dem Bezug zur Frage der schlechtere.
    gesucht.created_at = datetime.now(timezone.utc) - timedelta(days=30)
    for nummer in range(3):
        vielgenutzt = _write(db, regular_user, f"farbe{nummer}", f"Blau, seit jeher {nummer}")
        vielgenutzt.use_count = 20
        vielgenutzt.last_used_at = datetime.now(timezone.utc)
        vielgenutzt.created_at = datetime.now(timezone.utc) - timedelta(days=40)
    _write(db, regular_user, "neu", "Gerade eben gemerkt")
    db.commit()
    # Im Kopf steht genau eine Zeile: die neueste.
    monkeypatch.setattr(ai_memory_service, "MAX_CONTEXT_ROWS", 1)

    abruf = ai_gedaechtnis_abruf.abrufen(
        db, regular_user, query="Wann ist das wartungsfenster?"
    )

    assert abruf.passend is not None
    assert "Sonntags ab drei Uhr" in abruf.passend
    assert "Blau" not in abruf.passend


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


def test_wer_gekuerzt_bekommt_erfaehrt_es_auch(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hat der Zeilendeckel gekuerzt, sagt der Block es — wie beim Budgetschnitt.

    Ein stilles Weglassen waere die Unehrlichkeit, gegen die der Hinweis
    ueberhaupt existiert: das Modell soll aus einer Luecke nicht schliessen, es
    gebe nichts. Der Hinweis haengt nicht nur am Budget; die zweite Engstelle
    muss ihn genauso setzen.
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


def test_der_hinweis_zaehlt_auch_was_der_kopf_nie_oeffnete(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Eine 0 im Hinweis behauptet Vollstaendigkeit — genau verkehrt herum.

    Die Zahl stand als `len(decoded) - len(selected)`. Kuerzt allein der
    Zeilendeckel und passt der Rest danach ins Budget, ist das ausnahmslos 0: das
    Modell las "0 weitere Eintraege wurden aus Platzgruenden ausgelassen",
    nachdem bei 5.000 Eintraegen 4.700 Zeilen nie entschluesselt worden waren.
    Den Hinweis gibt es, damit das Modell aus einer Luecke nicht schliesst, es
    gebe nichts — diese 0 sagte ihm das Gegenteil.
    """
    _allow_memory(db, regular_user)
    for nummer in range(5):
        _write(db, regular_user, f"eintrag{nummer}", f"Wert {nummer}")
    # Nur der Zeilendeckel kuerzt: fuenf kurze Zeilen passen locker ins Budget.
    monkeypatch.setattr(ai_memory_service, "MAX_CONTEXT_ROWS", 2)

    block = ai_memory_service.provider_memory_context(
        db, regular_user, query="Was weisst du?"
    )

    assert "[Hinweis] 3 weitere" in block
    assert len([zeile for zeile in block.splitlines() if zeile.startswith("[user")]) == 2


def test_der_abruf_oeffnet_alle_zeilen_in_einem_aufruf(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die Wartezeit am Sidecar darf nicht mit der Zahl der Eintraege wachsen.

    Am Oeffnen einer Zeile ist fast nichts Rechnung — der Sidecar entschluesselt
    ein paar hundert Byte, alles andere ist der Weg hin und zurueck. Zeile fuer
    Zeile addierte sich genau diese Wartezeit vor dem ersten Byte der Antwort:
    gemessen 150 bis 600 ms bei 300 Zeilen und 10,3 s bei 5.000. Bis Gedaechtnis
    v2 liefen dafuer acht Einzelaufrufe zugleich in Threads; seitdem gehen alle
    Texte zusammen durch `/decrypt-many`.

    Gezaehlt wird darum beides: kein einziger Einzelaufruf, und alle vier Texte
    in **demselben** Stapel. Wer auf den Einzelweg zurueckfaellt, faellt hier
    auf, statt nur langsamer zu werden.
    """
    _allow_memory(db, regular_user)
    for nummer in range(4):
        _write(db, regular_user, f"eintrag{nummer}", f"Wert {nummer}")
    zaehler = _zaehle_entschluesselungen(monkeypatch)

    block = ai_memory_service.provider_memory_context(
        db, regular_user, query="Was weisst du?"
    )

    from tests._entschluesselung import ist_text

    assert block is not None
    assert len(block.splitlines()) == 4
    assert zaehler.einzeln == []
    mit_text = [aads for aads in zaehler.stapel if any(ist_text(aad) for aad in aads)]
    assert len(mit_text) == 1
    assert sum(1 for aad in mit_text[0] if ist_text(aad)) == 4


def test_die_suche_entschluesselt_ebenfalls_nicht_alles(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`search_memory` gab fuenfzehn Treffer zurueck und oeffnete dafuer alles.

    Seit Stufe 4 oeffnet sie nur die Kandidaten aus Wortindex und
    Vektorspeicher — hier ohne Modell genau den einen, dessen Wort passt.
    """
    ohne_modell(monkeypatch)
    _allow_memory(db, regular_user)
    for nummer in range(6):
        _write(db, regular_user, f"belanglos{nummer}", f"Fuellwert {nummer}")
    _write(db, regular_user, "wartungsfenster", "Sonntags ab drei Uhr")
    zaehler = _zaehle_entschluesselungen(monkeypatch)

    treffer = ai_memory_service.search_entries(
        db, regular_user, query="wartungsfenster"
    )

    assert zaehler.texte == 1
    assert "wartungsfenster" in [row.key for row, _value, _score in treffer]
