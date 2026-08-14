"""Bedeutungssuche im Gedaechtnis — und der Weg ohne sie.

Der Wortabgleich greift nur innerhalb einer Sprache. Ein deutscher Eintrag und
eine franzoesische Frage haben null gemeinsame Woerter; in dem Moment wirkte
das Gedaechtnis kaputt, obwohl der passende Eintrag danebenlag.

Zwei Dinge muessen deshalb gleichzeitig stimmen: die Bedeutungssuche muss ueber
Sprachgrenzen tragen, **und** ihr Ausfall darf nichts kaputtmachen. Ein
fehlendes Modell — abgebrochener Download, Airgap, unvollstaendiges Update —
ist ein Betriebszustand, kein Fehler.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy.orm import Session

from models import AiMemoryEntry, Role, RolePermission, User
from services import ai_embedding_service, ai_memory_service
from services.role_service import set_user_roles


needs_model = pytest.mark.skipif(
    not ai_embedding_service.is_available(),
    reason="Lokales Embeddingmodell nicht installiert",
)


def _allow_memory(db: Session, user: User) -> None:
    role = Role(name=f"emb-{user.id}", description=None, is_system=False)
    db.add(role)
    db.flush()
    db.add(RolePermission(role_id=role.id, permission_key="ai.memory.use"))
    db.commit()
    set_user_roles(db, user, [role.id])
    # Seit dem Einwilligungsschritt ist das Gedaechtnis standardmaessig aus.
    # Ein Test, der Erinnerungen im Kontext erwartet, muss es einschalten —
    # genau wie ein Benutzer es tun muesste.
    ai_memory_service.set_preference(db, user, True)


def _write(db: Session, user: User, key: str, value: str) -> AiMemoryEntry:
    row, _ = ai_memory_service.upsert_entry(
        db, user=user, scope="user", server_id=None, key=key, value=value,
    )
    return row


# ── Der Weg ohne Modell ───────────────────────────────────────────────────

def test_without_a_model_nothing_breaks(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fehlt das Modell, laeuft das Gedaechtnis ohne Vektoren weiter.

    Das ist der wichtigste Test dieser Datei: ein 507-MB-Download darf keine
    Betriebsvoraussetzung sein. Wer ihn nicht hat, bekommt eine schlechtere
    Auswahl — kein kaputtes Panel.
    """
    monkeypatch.setattr(ai_embedding_service, "encode", lambda texts: None)
    _allow_memory(db, regular_user)
    row = _write(db, regular_user, "ram.bevorzugt", "8 GB fuer Minecraft")

    assert row.embedding_json is None
    block = ai_memory_service.provider_memory_context(
        db, regular_user, query="Wieviel RAM?"
    )
    assert "8 GB fuer Minecraft" in block


def test_a_broken_model_directory_is_reported_once_and_then_ignored(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Ein defektes Modellverzeichnis darf nicht bei jeder Anfrage neu scheitern."""
    from config import settings

    ai_embedding_service.reset_for_tests()
    monkeypatch.setattr(settings, "ai_embedding_model_dir", str(tmp_path / "gibt-es-nicht"))
    try:
        assert ai_embedding_service.is_available() is False
        assert ai_embedding_service.encode(["irgendwas"]) is None
        # Zweiter Aufruf: der Fehlschlag ist gemerkt, es wird nicht erneut geladen.
        assert ai_embedding_service.encode(["irgendwas"]) is None
    finally:
        ai_embedding_service.reset_for_tests()


def test_a_failed_write_discards_the_old_vector(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ein Vektor darf nie den *alten* Text beschreiben.

    Der Fall: ein Eintrag wird berichtigt — "Minecraft" wird zu "Factorio" —,
    und genau bei diesem Schreibvorgang liefert `encode` nichts (das Modell ist
    weg oder stolpert einmal; anders als `_load` merkt sich `encode` einen
    solchen Fehlschlag nicht). Blieb der alte Vektor stehen, wurde der Eintrag
    bei knappem Platz danach dauerhaft für Minecraft-Fragen hochgezogen — und
    nichts rechnet ihn je nach, denn eine Nachberechnung gibt es für das
    Gedächtnis nicht.
    """
    vorhanden = [[0.0] * ai_embedding_service.EMBEDDING_DIMENSIONS]
    vorhanden[0][0] = 1.0
    monkeypatch.setattr(ai_embedding_service, "encode", lambda texts: vorhanden)
    _allow_memory(db, regular_user)
    row = _write(db, regular_user, "lieblingsspiel", "Am liebsten spiele ich Minecraft")
    assert ai_memory_service._stored_vector(row) is not None

    monkeypatch.setattr(ai_embedding_service, "encode", lambda texts: None)
    row = _write(db, regular_user, "lieblingsspiel", "Am liebsten spiele ich Factorio")

    assert row.embedding_json is None
    assert row.embedding_model is None
    assert ai_memory_service._stored_vector(row) is None


def test_a_vector_from_a_different_model_is_ignored(
    db: Session, regular_user: User
) -> None:
    """Ein Modellwechsel darf keine falschen Aehnlichkeiten erzeugen."""
    _allow_memory(db, regular_user)
    row = _write(db, regular_user, "test", "Wert")
    row.embedding_json = json.dumps([0.1] * ai_embedding_service.EMBEDDING_DIMENSIONS)
    row.embedding_model = "irgendein-anderes-modell"
    db.commit()

    assert ai_memory_service._stored_vector(row) is None


def test_a_vector_with_the_wrong_length_is_ignored(
    db: Session, regular_user: User
) -> None:
    """Eine beschaedigte Zeile darf die Rechnung nicht sprengen."""
    _allow_memory(db, regular_user)
    row = _write(db, regular_user, "test", "Wert")
    row.embedding_json = json.dumps([0.1, 0.2, 0.3])
    row.embedding_model = ai_memory_service._EMBEDDING_MODEL_TAG
    db.commit()

    assert ai_memory_service._stored_vector(row) is None


# ── Mit Modell ────────────────────────────────────────────────────────────

@needs_model
def test_writing_an_entry_stores_a_usable_vector(
    db: Session, regular_user: User
) -> None:
    ai_embedding_service.reset_for_tests()
    _allow_memory(db, regular_user)

    row = _write(db, regular_user, "ram.bevorzugt", "8 GB fuer neue Minecraft-Server")

    assert row.embedding_model == ai_memory_service._EMBEDDING_MODEL_TAG
    vector = ai_memory_service._stored_vector(row)
    assert vector is not None
    assert len(vector) == ai_embedding_service.EMBEDDING_DIMENSIONS
    # Normalisiert, damit die Aehnlichkeit ein reines Skalarprodukt ist.
    assert abs(sum(component * component for component in vector) - 1.0) < 0.01


@needs_model
def test_a_foreign_language_question_finds_the_german_entry(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Fall, fuer den die 507 MB da sind.

    "quel jeu je prefere" hat mit "lieblingsspiel: Am liebsten spiele ich
    Enshrouded" kein einziges Wort gemeinsam. Wortabgleich liefert hier null;
    die Bedeutungssuche findet den Eintrag.
    """
    ai_embedding_service.reset_for_tests()
    _allow_memory(db, regular_user)
    _write(db, regular_user, "lieblingsspiel", "Am liebsten spiele ich Enshrouded")
    _write(db, regular_user, "wartung", "Nur am Wochenende erreichbar")
    _write(db, regular_user, "backup.zeitpunkt", "Sicherungen laufen nachts um drei")
    # Budget so klein, dass genau ein Eintrag passt — die Auswahl muss greifen.
    monkeypatch.setattr(ai_memory_service, "MAX_CONTEXT_CHARS", 70)

    block = ai_memory_service.provider_memory_context(
        db, regular_user, query="quel jeu je prefere?"
    )

    assert "Enshrouded" in block


@needs_model
def test_the_word_overlap_still_wins_where_it_is_strong(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lehnwoerter bleiben ein starkes Signal und werden nicht verdraengt.

    Im Gameserver-Umfeld besteht die halbe Fachsprache aus englischen Woertern,
    die woertlich in deutschen Eintraegen stehen. Genau deshalb ersetzt die
    Bedeutungssuche den Wortabgleich nicht, sondern ergaenzt ihn.
    """
    ai_embedding_service.reset_for_tests()
    _allow_memory(db, regular_user)
    _write(db, regular_user, "mods.quelle", "Mods bitte immer von Modrinth holen")
    _write(db, regular_user, "wartung", "Nur am Wochenende erreichbar")
    _write(db, regular_user, "lieblingsspiel", "Am liebsten spiele ich Enshrouded")
    monkeypatch.setattr(ai_memory_service, "MAX_CONTEXT_CHARS", 75)

    block = ai_memory_service.provider_memory_context(
        db, regular_user, query="where do you get mods from?"
    )

    assert "Modrinth" in block


@needs_model
def test_unrelated_entries_score_lower_than_related_ones(
    db: Session, regular_user: User
) -> None:
    """Gemessene Grundannahme: das Modell trennt Verwandtes von Unverwandtem.

    Gemessen an Wortpaaren liegt Unverwandtes nahe 0,0 und Verwandtes bei
    0,2 bis 0,66. Diese Spanne ist die ganze Grundlage der Auswahl — bricht sie
    weg, waere die Bedeutungssuche nur teures Rauschen.
    """
    ai_embedding_service.reset_for_tests()
    vectors = ai_embedding_service.encode([
        "backup zeitpunkt: Sicherungen laufen nachts",
        "lieblingsspiel: Am liebsten spiele ich Enshrouded",
    ])
    query = ai_embedding_service.encode(["Wann laeuft meine Sicherung?"])
    assert vectors and query

    scores = ai_embedding_service.similarity(query[0], vectors)

    assert scores[0] > scores[1]
    assert scores[0] > 0.2
