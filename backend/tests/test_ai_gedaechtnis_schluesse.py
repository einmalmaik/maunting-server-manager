"""Schlüsse der nächtlichen Pflege: folgern ja, erfinden nein.

Was diese Tests festhalten:

- Ein Schluss wird nur gespeichert, wenn ein zweiter, getrennter Aufruf ihn
  gegen seine Belege bestätigt — und dieser Prüfer sieht nur die Belege.
- Was schon an der Form scheitert (ein Beleg, eine erfundene Zahl, ein
  „vermutlich“, ein Schluss als Beleg), erreicht den Prüfer gar nicht.
- Ein Schluss gilt nur, solange seine Belege gelten: ändert sich einer,
  wird er vergessen oder gelöscht, vergisst das Gedächtnis den Schluss mit.
- Ein Schluss geht in keiner Zusammenführung auf; wer ihn umschreibt, macht
  ihn zu einer eigenen Aussage.

Gefälscht wird nur der Anbieter. Alle Personen sind erfunden.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import AiMemoryBeleg, AiMemoryEntry, AiUsageEvent, User
from services import ai_gedaechtnis_pflege, ai_gedaechtnis_schreiber, ai_memory_service, ai_reasoning
from services.ai_gedaechtnis_pflege import PRUEF_NAME, PRUEFPROMPT, WERKZEUG_NAME, Pflege
from services.openai_compatible_adapter import ProviderToolCall, StreamChunk
from tests._einbettung import modell_ersetzen
from tests.test_ai_gedaechtnis_pflege import _achsen, _merken, _text
from tests.test_ai_gedaechtnis_schreiber import _erlauben


@pytest.fixture(autouse=True)
def _frischer_takt(monkeypatch) -> None:
    monkeypatch.setattr(ai_gedaechtnis_pflege, "_STAND", {})
    monkeypatch.setattr(ai_gedaechtnis_pflege, "_LAUFEND", {})
    monkeypatch.setattr(ai_gedaechtnis_pflege, "_LEERLAUF_BIS", None)
    monkeypatch.setattr(ai_gedaechtnis_pflege, "_RANG_TAG", None)


def _modelle(monkeypatch, aenderungen: list[dict], urteile: list[dict] | None) -> list:
    """Pflege und Prüfer gefälscht. Gibt die Aufrufe zurück: (Werkzeug, Nachrichten)."""
    aufrufe: list = []

    async def fake(_client, *, provider, api_key, messages, usage, model=None, tools=None,
                   tool_choice=None, reasoning=False, reasoning_effort=None, **_rest):
        del provider, api_key, model, reasoning, reasoning_effort, tool_choice
        name = tools[0]["function"]["name"]
        aufrufe.append((name, messages))
        usage.prompt_tokens = 900
        usage.completion_tokens = 100
        usage.total_tokens = 1_000
        argumente = {"aenderungen": aenderungen} if name == WERKZEUG_NAME else (
            {"urteile": urteile} if urteile is not None else None
        )
        if argumente is not None:
            usage.tool_calls.append(ProviderToolCall(id="c1", name=name, arguments=argumente))
        if False:  # pragma: no cover - macht die Funktion zum Generator
            yield StreamChunk("content", "")

    async def ohne_denken(*_a, **_k):
        return False, None

    monkeypatch.setattr(ai_gedaechtnis_schreiber, "stream_chat_completion", fake)
    monkeypatch.setattr(ai_reasoning, "aus_fuer", ohne_denken)
    return aufrufe


def _pflegen(db: Session, user: User) -> Pflege:
    ergebnis = asyncio.run(ai_gedaechtnis_pflege.bereich_pflegen(f"user:{user.id}"))
    db.expire_all()
    return ergebnis


def _schluesse(db: Session) -> list[AiMemoryEntry]:
    return db.query(AiMemoryEntry).filter(AiMemoryEntry.art == ai_memory_service.SCHLUSS).all()


def _belege_von(db: Session, schluss_id: str) -> set[str]:
    return {
        beleg for (beleg,) in db.query(AiMemoryBeleg.beleg_id).filter(AiMemoryBeleg.schluss_id == schluss_id)
    }


def _zwei_belege(db: Session, user: User) -> tuple[AiMemoryEntry, AiMemoryEntry]:
    runde = _merken(db, user, "Die Kaffee-Runde im Team beginnt um 9 Uhr.")
    dienst = _merken(db, user, "Jonas hat jeden Montag Kaffee-Dienst.", origin="user")
    _merken(db, user, "Jonas hat eine Katze namens Pixel.")
    return runde, dienst


SCHLUSS = "Montags um 9 Uhr ist Jonas für die Kaffee-Runde zuständig."


# ── Speichern nur mit Bestätigung ───────────────────────────────────────


def test_ein_bestaetigter_schluss_wird_mit_seinen_belegen_gespeichert(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    modell_ersetzen(monkeypatch, _achsen)
    _erlauben(db, regular_user)
    runde, dienst = _zwei_belege(db, regular_user)
    aufrufe = _modelle(
        monkeypatch,
        [{"aktion": "schluss", "belege": ["E1", "E2"], "text": SCHLUSS, "titel": "Kaffee-Dienst",
          "wichtigkeit": 5}],
        [{"schluss": "S1", "grund": "Steht in beiden.", "belegt": True}],
    )

    ergebnis = _pflegen(db, regular_user)

    assert (ergebnis.status, ergebnis.geschlossen, ergebnis.verworfen) == ("ok", 1, 0)
    [schluss] = _schluesse(db)
    assert (schluss.quelle, schluss.origin, schluss.status) == ("pflege", "ai", "aktiv")
    # Nie wichtiger als der wichtigste Beleg (beide 3).
    assert schluss.wichtigkeit == 3
    assert _text(db, schluss.id)[1] == SCHLUSS
    assert _belege_von(db, schluss.id) == {runde.id, dienst.id}
    # Der Prüfer ist ein eigener Aufruf und sieht nur die Belege.
    assert [name for name, _ in aufrufe] == [WERKZEUG_NAME, PRUEF_NAME]
    pruefung = aufrufe[1][1]
    assert pruefung[0]["content"] == PRUEFPROMPT
    assert "S1: " + SCHLUSS in pruefung[1]["content"]
    assert "9 Uhr" in pruefung[1]["content"] and "Pixel" not in pruefung[1]["content"]
    # Beide Aufrufe sind gebucht, beim Besitzer, als Gedächtnis.
    buchungen = db.query(AiUsageEvent).all()
    assert len(buchungen) == 2
    assert {(b.user_id, b.zweck) for b in buchungen} == {(regular_user.id, "gedaechtnis")}
    # Im Gespräch steht er als geschlossen, nicht als gesagt oder gemerkt.
    assert ai_memory_service._memory_line(schluss, SCHLUSS).startswith("[user/geschlossen] ")


@pytest.mark.parametrize("urteile", [
    [{"schluss": "S1", "grund": "Montag steht da, 9 Uhr auch, zuständig nicht.", "belegt": False}],
    [],
    # Zwei Urteile über denselben Schluss, die sich widersprechen: nein.
    [{"schluss": "S1", "belegt": True}, {"schluss": "S1", "belegt": False}],
    None,
])
def test_ohne_ausdrueckliche_bestaetigung_kein_schluss(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch, urteile,
) -> None:
    modell_ersetzen(monkeypatch, _achsen)
    _erlauben(db, regular_user)
    _zwei_belege(db, regular_user)
    aufrufe = _modelle(
        monkeypatch, [{"aktion": "schluss", "belege": ["E1", "E2"], "text": SCHLUSS}], urteile,
    )

    ergebnis = _pflegen(db, regular_user)

    assert (ergebnis.status, ergebnis.geschlossen, ergebnis.verworfen) == ("ok", 0, 1)
    assert len(aufrufe) == 2
    assert _schluesse(db) == []


def test_ohne_kontingent_fuer_die_pruefung_wird_nichts_geschlossen(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    modell_ersetzen(monkeypatch, _achsen)
    _erlauben(db, regular_user)
    _zwei_belege(db, regular_user)
    aufrufe = _modelle(
        monkeypatch, [{"aktion": "schluss", "belege": ["E1", "E2"], "text": SCHLUSS}],
        [{"schluss": "S1", "belegt": True}],
    )
    echt = ai_gedaechtnis_schreiber._reservieren
    gezaehlt = {"n": 0}

    def nur_einmal(*args, **kwargs):
        gezaehlt["n"] += 1
        return echt(*args, **kwargs) if gezaehlt["n"] == 1 else None

    monkeypatch.setattr(ai_gedaechtnis_schreiber, "_reservieren", nur_einmal)

    ergebnis = _pflegen(db, regular_user)

    assert (ergebnis.geschlossen, ergebnis.verworfen) == (0, 1)
    assert [name for name, _ in aufrufe] == [WERKZEUG_NAME]
    assert _schluesse(db) == []


# ── Was an der Form scheitert ───────────────────────────────────────────


def test_was_an_der_form_scheitert_erreicht_den_pruefer_nicht(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    modell_ersetzen(monkeypatch, _achsen)
    _erlauben(db, regular_user)
    _zwei_belege(db, regular_user)
    aufrufe = _modelle(monkeypatch, [
        # Ein Beleg ist kein Schluss, sondern eine Wiederholung.
        {"aktion": "schluss", "belege": ["E1"], "text": "Die Kaffee-Runde ist morgens."},
        # Eine Zahl, die in keinem Beleg steht.
        {"aktion": "schluss", "belege": ["E1", "E2"], "text": "Jonas kocht montags um 8 Uhr Kaffee."},
        # Eine Vermutung.
        {"aktion": "schluss", "belege": ["E1", "E2"], "text": "Jonas trinkt vermutlich viel Kaffee."},
        {"aktion": "schluss", "belege": ["E1", "E2"], "text": "Jonas probably likes coffee."},
        # Nur ein Beleg wiederholt.
        {"aktion": "schluss", "belege": ["E1", "E2"], "text": "Jonas hat jeden Montag Kaffee-Dienst."},
        # Leer und zu lang.
        {"aktion": "schluss", "belege": ["E1", "E2"], "text": "  "},
        {"aktion": "schluss", "belege": ["E1", "E2"], "text": "Kaffee " * 80},
    ], [{"schluss": f"S{n}", "belegt": True} for n in range(1, 8)])

    ergebnis = _pflegen(db, regular_user)

    assert (ergebnis.geschlossen, ergebnis.verworfen) == (0, 7)
    assert [name for name, _ in aufrufe] == [WERKZEUG_NAME]
    assert _schluesse(db) == []


def test_hoechstens_drei_schluesse_je_lauf(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    modell_ersetzen(monkeypatch, _achsen)
    _erlauben(db, regular_user)
    _zwei_belege(db, regular_user)
    aufrufe = _modelle(
        monkeypatch,
        # Nachsichtig gelesen: Belege unter eintrag und aufgenommen.
        [{"aktion": "schluss", "eintrag": "E1", "aufgenommen": ["E2"], "text": f"{SCHLUSS} Fall {n}."}
         for n in "ABCDE"],
        [{"schluss": f"S{n}", "belegt": True} for n in range(1, 6)],
    )

    ergebnis = _pflegen(db, regular_user)

    assert (ergebnis.geschlossen, ergebnis.verworfen) == (3, 2)
    assert aufrufe[1][1][1]["content"].count("Belege:") == 3


def test_ein_schluss_ist_kein_beleg_und_geht_nirgends_auf(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    modell_ersetzen(monkeypatch, _achsen)
    _erlauben(db, regular_user)
    runde, dienst = _zwei_belege(db, regular_user)
    alt, _ = ai_memory_service.schluss_anlegen(
        db, user=regular_user, belege=[runde.id, dienst.id], text=SCHLUSS,
    )
    db.commit()
    aufrufe = _modelle(monkeypatch, [
        {"aktion": "schluss", "belege": ["E1", "E4"], "text": "Die Kaffee-Runde hat einen Dienst."},
        {"aktion": "zusammenfuehren", "eintrag": "E1", "aufgenommen": ["E4"], "text": "Kaffee."},
        {"aktion": "zusammenfuehren", "eintrag": "E4", "aufgenommen": ["E1"], "text": "Kaffee."},
    ], [{"schluss": "S1", "belegt": True}])

    ergebnis = _pflegen(db, regular_user)

    assert (ergebnis.geschlossen, ergebnis.zusammengefuehrt, ergebnis.verworfen) == (0, 0, 3)
    assert "Schluss von Singra" in aufrufe[0][1][1]["content"]
    assert [name for name, _ in aufrufe] == [WERKZEUG_NAME]
    assert db.get(AiMemoryEntry, alt.id).status == "aktiv"
    assert db.get(AiMemoryEntry, runde.id).fassung == 1


def test_ein_im_selben_lauf_geaenderter_beleg_traegt_keinen_schluss(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    modell_ersetzen(monkeypatch, _achsen)
    _erlauben(db, regular_user)
    _zwei_belege(db, regular_user)
    _merken(db, regular_user, "Die Kaffee-Runde trifft sich in der Küche.")
    _modelle(monkeypatch, [
        {"aktion": "schluss", "belege": ["E1", "E2"], "text": SCHLUSS},
        {"aktion": "zusammenfuehren", "eintrag": "E1", "aufgenommen": ["E4"],
         "text": "Die Kaffee-Runde im Team beginnt um 9 Uhr in der Küche."},
    ], [{"schluss": "S1", "belegt": True}])

    ergebnis = _pflegen(db, regular_user)

    # Der Prüfer las den alten Stand von E1: dann lieber nichts.
    assert (ergebnis.zusammengefuehrt, ergebnis.geschlossen, ergebnis.verworfen) == (1, 0, 1)
    assert _schluesse(db) == []


# ── Ein Schluss lebt so lange wie seine Belege ──────────────────────────


@pytest.mark.parametrize("wie", ["text", "vergessen", "loeschen", "zusammengefuehrt"])
def test_ein_schluss_faellt_mit_seinem_beleg(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch, wie: str,
) -> None:
    modell_ersetzen(monkeypatch, _achsen)
    _erlauben(db, regular_user)
    runde, dienst = _zwei_belege(db, regular_user)
    schluss, _ = ai_memory_service.schluss_anlegen(
        db, user=regular_user, belege=[runde.id, dienst.id], text=SCHLUSS,
    )
    db.commit()

    if wie == "text":
        ai_memory_service.erinnerung_aendern(
            db, user=regular_user, entry_id=runde.id, text="Die Kaffee-Runde beginnt um 10 Uhr.",
        )
    elif wie == "vergessen":
        ai_memory_service.erinnerung_vergessen(db, user=regular_user, entry_id=dienst.id)
    elif wie == "loeschen":
        ai_memory_service.delete_entry(db, regular_user, runde.id)
    else:
        weitere = _merken(db, regular_user, "Die Kaffee-Runde ist um neun.")
        ai_memory_service.erinnerung_aufnehmen(
            db, user=regular_user, ziel_id=weitere.id, quelle_id=runde.id,
        )
    db.expire_all()

    zeile = db.get(AiMemoryEntry, schluss.id)
    assert (zeile.status, zeile.vergessen_am is not None) == ("vergessen", True)
    # Zurückholbar wie alles, was die KI vergisst.
    ai_memory_service.erinnerung_zurueckholen(db, user=regular_user, entry_id=schluss.id)
    assert db.get(AiMemoryEntry, schluss.id).status == "aktiv"


def test_titel_wichtigkeit_und_thema_eines_belegs_lassen_den_schluss_stehen(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    modell_ersetzen(monkeypatch, _achsen)
    _erlauben(db, regular_user)
    runde, dienst = _zwei_belege(db, regular_user)
    schluss, _ = ai_memory_service.schluss_anlegen(
        db, user=regular_user, belege=[runde.id, dienst.id], text=SCHLUSS,
    )
    db.commit()

    ai_memory_service.erinnerung_aendern(
        db, user=regular_user, entry_id=runde.id, titel="Kaffee", thema="Team", wichtigkeit=5,
    )

    assert db.get(AiMemoryEntry, schluss.id).status == "aktiv"


def test_wer_einen_schluss_umschreibt_macht_ihn_zur_eigenen_aussage(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    modell_ersetzen(monkeypatch, _achsen)
    _erlauben(db, regular_user)
    runde, dienst = _zwei_belege(db, regular_user)
    schluss, _ = ai_memory_service.schluss_anlegen(
        db, user=regular_user, belege=[runde.id, dienst.id], text=SCHLUSS,
    )
    db.commit()

    ai_memory_service.erinnerung_aendern(
        db, user=regular_user, entry_id=schluss.id, text="Montags kocht Jonas den Kaffee.",
    )
    ai_memory_service.erinnerung_vergessen(db, user=regular_user, entry_id=runde.id)

    zeile = db.get(AiMemoryEntry, schluss.id)
    assert (zeile.status, zeile.art, zeile.origin) == ("aktiv", None, "user")
    assert _belege_von(db, schluss.id) == set()


def test_ein_schluss_braucht_geltende_belege_aus_einem_bereich(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import HTTPException

    modell_ersetzen(monkeypatch, _achsen)
    _erlauben(db, regular_user)
    runde, dienst = _zwei_belege(db, regular_user)
    vergessen = _merken(db, regular_user, "Jonas mochte früher Tee.")
    ai_memory_service.erinnerung_vergessen(db, user=regular_user, entry_id=vergessen.id)
    schluss, _ = ai_memory_service.schluss_anlegen(
        db, user=regular_user, belege=[runde.id, dienst.id], text=SCHLUSS,
    )
    db.commit()

    for belege in ([runde.id], [runde.id, runde.id], [runde.id, vergessen.id], [runde.id, schluss.id]):
        with pytest.raises(HTTPException):
            ai_memory_service.schluss_anlegen(db, user=regular_user, belege=belege, text="Etwas.")
        db.rollback()


# ── Über die Routen ─────────────────────────────────────────────────────


def test_die_ansicht_nennt_die_belege_eines_schlusses(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    modell_ersetzen(monkeypatch, _achsen)
    _erlauben(db, regular_user)
    runde, dienst = _zwei_belege(db, regular_user)
    schluss, _ = ai_memory_service.schluss_anlegen(
        db, user=regular_user, belege=[runde.id, dienst.id], text=SCHLUSS,
    )
    db.commit()

    seite = client.get("/api/ai/memory/personal", cookies=user_cookies)
    einzeln = client.get(f"/api/ai/memory/{schluss.id}", cookies=user_cookies)

    assert seite.status_code == 200, seite.text
    eintraege = {eintrag["id"]: eintrag for eintrag in seite.json()["entries"]}
    assert eintraege[schluss.id]["art"] == "schluss"
    assert {b["text"] for b in eintraege[schluss.id]["belege"]} == {
        "Die Kaffee-Runde im Team beginnt um 9 Uhr.", "Jonas hat jeden Montag Kaffee-Dienst.",
    }
    assert eintraege[runde.id]["belege"] == []
    assert len(einzeln.json()["belege"]) == 2
