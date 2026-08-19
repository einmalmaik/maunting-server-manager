"""Bedeutungssuche im Gedaechtnis — und der Weg ohne sie.

Der Wortabgleich greift nur innerhalb einer Sprache. Ein deutscher Eintrag und
eine franzoesische Frage haben null gemeinsame Woerter; in dem Moment wirkte
das Gedaechtnis kaputt, obwohl der passende Eintrag danebenlag.

Zwei Dinge muessen deshalb gleichzeitig stimmen: die Bedeutungssuche muss ueber
Sprachgrenzen tragen, **und** ihr Ausfall darf nichts kaputtmachen. Ein
fehlendes Modell — abgebrochener Download, Airgap, unvollstaendiges Update —
ist ein Betriebszustand, kein Fehler.

Ein Betriebszustand ist aber nichts, was heimlich bis zum nächsten Neustart
gilt. Deshalb prüft diese Datei zusätzlich beides, was ihn erträglich macht: er
heilt von selbst, sobald das Verzeichnis wieder in Ordnung ist, und der
Betreiber sieht ihn über die Einstellungen statt nur im Log.
"""

from __future__ import annotations

import json
import struct
import sys
import types

import pytest
from fastapi.testclient import TestClient
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

    assert row.embedding_bytes is None
    assert row.embedding_json is None
    block = ai_memory_service.provider_memory_context(
        db, regular_user, query="Wieviel RAM?"
    )
    assert "8 GB fuer Minecraft" in block


def test_a_broken_model_directory_is_reported_once_and_then_ignored(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Ein defektes Modellverzeichnis darf nicht bei jeder Anfrage neu scheitern.

    „Gemerkt" heißt seit dem Neuversuch: für die Dauer von
    ``NEUVERSUCH_NACH_SEKUNDEN``. Innerhalb dieser Frist verhält sich der
    Dienst wie vorher — keine zweite Warnung, kein zweiter Ladeversuch.
    """
    from config import settings

    ai_embedding_service.reset_for_tests()
    monkeypatch.setattr(settings, "ai_embedding_model_dir", str(tmp_path / "gibt-es-nicht"))
    try:
        assert ai_embedding_service.is_available() is False
        assert ai_embedding_service.encode(["irgendwas"]) is None
        # Zweiter Aufruf: der Fehlschlag ist gemerkt, es wird nicht erneut geladen.
        assert ai_embedding_service.encode(["irgendwas"]) is None
        # Und er ist abfragbar, statt nur im Log zu stehen.
        assert ai_embedding_service.is_ready() is False
    finally:
        ai_embedding_service.reset_for_tests()


class _FakeModel:
    """Ein Modell, das genau so viel kann, wie ``encode`` von ihm verlangt."""

    def encode(self, texts: list[str]) -> list[list[float]]:
        erste_achse = [1.0] + [0.0] * (ai_embedding_service.EMBEDDING_DIMENSIONS - 1)
        return [list(erste_achse) for _ in texts]


def test_a_stumbled_load_is_retried_after_the_window(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ein einmal gescheitertes Laden darf nicht bis zum Neustart gelten.

    Der Fall aus dem Betrieb: die Gewichte werden gerade entpackt, ein Update
    läuft, der Speicher ist für einen Moment knapp — ``from_pretrained`` wirft.
    Vorher merkte sich der Dienst das für den Rest des Prozesslebens; das
    Gedächtnis arbeitete bis zum nächsten Neustart des Panels ohne
    Bedeutungssuche weiter, und niemand erfuhr davon.

    Die Uhr wird hier nicht gefälscht, sondern der gemerkte Zeitpunkt
    zurückdatiert — das ist dasselbe Ereignis („die Frist ist abgelaufen") mit
    einem Bruchteil des Aufwands.
    """
    versuche: list[str] = []

    class FakeStaticModel:
        @staticmethod
        def from_pretrained(pfad: str) -> _FakeModel:
            versuche.append(pfad)
            if len(versuche) == 1:
                raise RuntimeError("Gewichte unvollstaendig")
            return _FakeModel()

    fake_modul = types.ModuleType("model2vec")
    fake_modul.StaticModel = FakeStaticModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "model2vec", fake_modul)
    # Die Dateien liegen da — genau der Fall, den `is_available` nicht sieht.
    monkeypatch.setattr(ai_embedding_service, "is_available", lambda: True)

    ai_embedding_service.reset_for_tests()
    try:
        assert ai_embedding_service.encode(["irgendwas"]) is None
        assert ai_embedding_service.is_ready() is False
        # Innerhalb der Frist wird nicht erneut geladen: sonst waere ein
        # defektes Modell ein Ladeversuch je Chatnachricht.
        assert ai_embedding_service.encode(["irgendwas"]) is None
        assert len(versuche) == 1

        ai_embedding_service._letzter_fehlschlag -= (
            ai_embedding_service.NEUVERSUCH_NACH_SEKUNDEN + 1
        )

        vektoren = ai_embedding_service.encode(["irgendwas"])
        assert vektoren is not None
        assert len(vektoren[0]) == ai_embedding_service.EMBEDDING_DIMENSIONS
        assert len(versuche) == 2
        assert ai_embedding_service.is_ready() is True
    finally:
        ai_embedding_service.reset_for_tests()


def test_the_operator_sees_whether_the_search_can_compute(
    client: TestClient, owner_cookies: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Zustand gehört an eine Oberfläche, nicht nur ins Log.

    Ohne dieses Feld war ein fehlendes oder beschädigtes Modell für den
    Betreiber genau eine Zeile im Log beim Start — danach fand sein Gedächtnis
    über Sprachgrenzen hinweg nichts mehr, ohne dass irgendwo etwas anders
    aussah.
    """
    monkeypatch.setattr(ai_embedding_service, "is_ready", lambda: False)

    antwort = client.get("/api/ai/settings/context", cookies=owner_cookies)

    assert antwort.status_code == 200
    assert antwort.json()["memory_search_ready"] is False

    monkeypatch.setattr(ai_embedding_service, "is_ready", lambda: True)

    assert client.get(
        "/api/ai/settings/context", cookies=owner_cookies
    ).json()["memory_search_ready"] is True


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

    assert row.embedding_bytes is None
    assert row.embedding_json is None
    assert row.embedding_model is None
    assert ai_memory_service._stored_vector(row) is None


def _vektoren_fuer(texts: list[str]) -> list[list[float]]:
    """Ein Modellersatz: je Text ein brauchbarer, normierter Vektor.

    Bewusst einer je Eingabe — genau die Zusage, auf die sich das Nachziehen
    verlässt, wenn es Vektoren und Zeilen wieder zusammenführt.
    """
    return [
        [1.0] + [0.0] * (ai_embedding_service.EMBEDDING_DIMENSIONS - 1)
        for _ in texts
    ]


def test_a_missing_vector_is_recomputed_on_the_next_recall(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Eine Ausfallphase des Modells darf keinen Eintrag dauerhaft blind machen.

    `refresh_embedding` verwirft den Vektor, wenn `encode` beim Schreiben
    nichts liefert — richtig, denn ein stehengebliebener Vektor beschriebe
    danach den *alten* Text. Nachgeholt wurde es aber nie: `upsert_entry` ist
    der einzige Aufrufer, und niemand fasst die Zeile je wieder an. Wer während
    eines abgebrochenen Downloads etwas merkte, hatte einen Eintrag, der für
    Bedeutungsrang und Verblassen-Reiz für immer unsichtbar blieb.

    Nachgezogen wird dort, wo der Klartext ohnehin offenliegt: beim Abruf in
    den Kontext.
    """
    _allow_memory(db, regular_user)
    monkeypatch.setattr(ai_embedding_service, "encode", lambda texts: None)
    row = _write(db, regular_user, "zeitzone", "Die Anlage steht auf Europe/Berlin")
    assert row.embedding_json is None, "ohne Modell entsteht kein Vektor"

    # Das Modell ist wieder da — die nächste Anfrage muss aufholen.
    monkeypatch.setattr(ai_embedding_service, "encode", _vektoren_fuer)
    ai_memory_service.provider_memory_context(db, regular_user, query="Zeitzone?")

    db.refresh(row)
    assert row.embedding_model == ai_memory_service._EMBEDDING_MODEL_TAG
    vektor = ai_memory_service._stored_vector(row)
    assert vektor is not None
    assert len(vektor) == ai_embedding_service.EMBEDDING_DIMENSIONS


def test_without_a_model_the_recall_leaves_the_missing_vector_alone(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fehlt das Modell weiterhin, passiert schlicht nichts — und nichts wirft.

    Das Nachziehen darf kein zweiter Weg sein, auf dem ein fehlendes Modell
    das Gedächtnis kaputtmacht.
    """
    _allow_memory(db, regular_user)
    monkeypatch.setattr(ai_embedding_service, "encode", lambda texts: None)
    row = _write(db, regular_user, "zeitzone", "Die Anlage steht auf Europe/Berlin")

    block = ai_memory_service.provider_memory_context(db, regular_user, query="Zeitzone?")

    assert block is not None and "Europe/Berlin" in block
    db.refresh(row)
    assert row.embedding_json is None
    assert row.embedding_model is None


def test_a_vector_from_a_different_model_is_ignored(
    db: Session, regular_user: User
) -> None:
    """Ein Modellwechsel darf keine falschen Aehnlichkeiten erzeugen."""
    _allow_memory(db, regular_user)
    row = _write(db, regular_user, "test", "Wert")
    row.embedding_bytes = ai_embedding_service.vektor_zu_bytes(
        [0.1] * ai_embedding_service.EMBEDDING_DIMENSIONS
    )
    row.embedding_model = "irgendein-anderes-modell"
    db.commit()

    assert ai_memory_service._stored_vector(row) is None


def test_a_vector_with_the_wrong_length_is_ignored(
    db: Session, regular_user: User
) -> None:
    """Eine beschaedigte Zeile darf die Rechnung nicht sprengen."""
    _allow_memory(db, regular_user)
    row = _write(db, regular_user, "test", "Wert")
    row.embedding_bytes = ai_embedding_service.vektor_zu_bytes([0.1, 0.2, 0.3])
    row.embedding_json = None
    row.embedding_model = ai_memory_service._EMBEDDING_MODEL_TAG
    db.commit()

    assert ai_memory_service._stored_vector(row) is None


# ── Die Speicherform des Vektors ──────────────────────────────────────────
#
# Gemessen am 19.08.2026: 5.000 Vektoren aus JSON zu lesen kostete 381 ms von
# 717 ms Gesamtrechenzeit eines Chatabrufs, dieselben Zahlen als float32-Bytes
# 4 ms. Der Wechsel ist ein reiner Formatwechsel — und genau deshalb muss er
# an drei Stellen festgehalten werden: geschrieben wird die neue Form, gelesen
# werden beide, und was auf der Platte liegt, hängt nicht an der Maschine.

def test_a_written_vector_lands_in_the_byte_column(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Geschrieben wird als Bytes — und die alte Textspalte bleibt leer.

    Beides gehört zusammen. Bliebe der JSON-Stand daneben stehen, trüge die
    Zeile zwei Vektoren; nach einer Berichtigung beschriebe der zweite den
    alten Text, und der Rückfall in `_stored_vector` griffe genau dann darauf
    zurück, wenn die Bytes einmal fehlen.
    """
    _allow_memory(db, regular_user)
    monkeypatch.setattr(ai_embedding_service, "encode", _vektoren_fuer)

    row = _write(db, regular_user, "zeitzone", "Die Anlage steht auf Europe/Berlin")

    assert row.embedding_json is None, "die alte Form darf nicht mitgeschrieben werden"
    assert row.embedding_bytes is not None
    assert len(row.embedding_bytes) == ai_embedding_service.EMBEDDING_BYTES
    vektor = ai_memory_service._stored_vector(row)
    assert vektor is not None and len(vektor) == ai_embedding_service.EMBEDDING_DIMENSIONS


def test_an_entry_from_before_the_migration_is_still_read(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zwischen Code-Update und Migrationslauf darf nichts blind werden.

    Der Zeitraum ist kurz, aber er ist echt: der neue Code läuft schon, die
    Migration hat die Bestandszeilen noch nicht umgerechnet. Ohne den Rückfall
    auf ``embedding_json`` fände das Gedächtnis in diesen Sekunden zu keiner
    Frage mehr etwas — und der Betreiber sähe nur einen Assistenten, der ihn
    plötzlich nicht mehr kennt.
    """
    _allow_memory(db, regular_user)
    monkeypatch.setattr(ai_embedding_service, "encode", _vektoren_fuer)
    row = _write(db, regular_user, "zeitzone", "Die Anlage steht auf Europe/Berlin")
    # Der Stand vor der Migration: Vektor als Text, Byte-Spalte noch leer.
    row.embedding_json = json.dumps(list(_vektoren_fuer(["egal"])[0]))
    row.embedding_bytes = None
    db.commit()

    vektor = ai_memory_service._stored_vector(row)

    assert vektor is not None
    assert len(vektor) == ai_embedding_service.EMBEDDING_DIMENSIONS


def test_a_truncated_byte_vector_counts_as_missing(db: Session) -> None:
    """Eine falsche Byteslänge ist ein beschädigter Vektor, kein kurzer.

    Die Länge steht fest bei 256 Zahlen. Würde eine abgeschnittene Zeile
    klaglos als drei Zahlen gelesen, verglichen numpy sie gegen 256 — und der
    ganze Stapel scheiterte an einer Stelle, die mit der Ursache nichts mehr zu
    tun hat. Fehlend ist die einzige ehrliche Lesart.
    """
    voll = [1.0] + [0.0] * (ai_embedding_service.EMBEDDING_DIMENSIONS - 1)
    roh = ai_embedding_service.vektor_zu_bytes(voll)

    assert ai_embedding_service.bytes_zu_vektor(roh) is not None
    assert ai_embedding_service.bytes_zu_vektor(roh[:-4]) is None, "zu kurz"
    assert ai_embedding_service.bytes_zu_vektor(roh + roh[:4]) is None, "zu lang"
    assert ai_embedding_service.bytes_zu_vektor(b"") is None
    assert ai_embedding_service.bytes_zu_vektor(None) is None


def test_the_stored_form_is_a_bare_run_of_float32() -> None:
    """Die Form auf der Platte ist eine Zusage über Versionen hinweg.

    Sie steht in der Datenbank und wird von einem späteren Stand des Panels
    wieder gelesen. Ohne diesen Test wäre ein Wechsel des Zahlentyps — von
    ``float`` auf ``double`` etwa — eine Zeile Code und ein Bestand, den
    danach niemand mehr entziffert. Festgehalten ist deshalb genau das, was
    ein anderer Leser annehmen darf: 256 Zahlen zu vier Bytes, hintereinander,
    ohne Rahmen und ohne Kopf.

    Der Vergleich läuft über ``struct`` und nicht über dieselbe Funktion,
    die geprüft wird — sonst bestätigte sich hier nur der Code selbst.
    """
    werte = [1.5, -2.25, 0.75] + [0.0] * (
        ai_embedding_service.EMBEDDING_DIMENSIONS - 3
    )

    roh = ai_embedding_service.vektor_zu_bytes(werte)

    assert len(roh) == ai_embedding_service.EMBEDDING_BYTES
    assert roh == struct.pack(
        f"<{ai_embedding_service.EMBEDDING_DIMENSIONS}f", *werte
    )
    assert list(ai_embedding_service.bytes_zu_vektor(roh)) == werte


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
