"""Gedächtnis v2, Stufe 5: Stärke und die Pflege in der Nacht.

Was diese Tests festhalten:

- Gebrauch verlängert die Haltbarkeit, nach einer langen Pause mehr als
  gleich danach. Was „im Kopf“ steht, ordnet sich nach Wichtigkeit ×
  Präsenz — aber erst mit der Rechnung der Pflege, nicht mitten im Gespräch.
- Die Pflege führt zusammen, was dasselbe sagt, und setzt abgelaufene Pläne
  in die Vergangenheit. Der Wortlaut des Menschen bleibt; was aufgeht, ist
  zurückholbar und bringt sein Gewicht mit.
- Ohne Ähnliches und ohne Fälliges fragt sie nicht. Sie zahlt wie alles
  im Gedächtnis, wartet ohne Einwilligung oder Kontingent, und läuft nachts.

Gefälscht wird nur der Anbieter. Alle Personen sind erfunden.
"""

from __future__ import annotations

import asyncio
import math
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from models import AiMemoryEntry, AiMemoryPflege, AiMemoryVersion, AiUsageEvent, User
from services import (
    ai_embedding_service,
    ai_gedaechtnis_abruf,
    ai_gedaechtnis_pflege,
    ai_gedaechtnis_schreiber,
    ai_memory_service,
    ai_reasoning,
)
from services.ai_gedaechtnis_pflege import WERKZEUG_NAME, Pflege
from services.openai_compatible_adapter import ProviderToolCall, StreamChunk
from tests._einbettung import modell_ersetzen, ohne_modell
from tests.test_ai_gedaechtnis_schreiber import (
    _durchgang,
    _erlauben,
    _gespraech,
)
from tests.test_ai_gedaechtnis_schreiber import _modell as _schreibermodell


# ── Hilfen ──────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _frischer_takt(monkeypatch) -> None:
    monkeypatch.setattr(ai_gedaechtnis_pflege, "_STAND", {})
    monkeypatch.setattr(ai_gedaechtnis_pflege, "_LAUFEND", {})
    monkeypatch.setattr(ai_gedaechtnis_pflege, "_LEERLAUF_BIS", None)
    monkeypatch.setattr(ai_gedaechtnis_pflege, "_RANG_TAG", None)


def _achsen(texte: list[str]) -> list[list[float]]:
    """Kaffee auf einer Achse, Katzen auf der zweiten, alles andere quer."""
    vektoren = []
    for text in texte:
        klein = text.lower()
        vektor = [0.0] * ai_embedding_service.EMBEDDING_DIMENSIONS
        if "kaffee" in klein:
            vektor[0] = 1.0
        elif "katze" in klein:
            vektor[1] = 1.0
        else:
            vektor[2 + len(klein) % 50] = 1.0
        vektoren.append(vektor)
    return vektoren


def _modell(monkeypatch, aenderungen: list[dict] | None) -> dict:
    """Ersetzt den Anbieter. Gibt zurück, was er zu sehen bekam."""
    gesehen: dict = {"aufrufe": 0}

    async def fake(_client, *, provider, api_key, messages, usage, model=None, tools=None,
                   tool_choice=None, reasoning=False, reasoning_effort=None, **_rest):
        del provider, api_key, model, reasoning, reasoning_effort, tool_choice
        gesehen["aufrufe"] += 1
        gesehen["messages"] = messages
        gesehen["tools"] = tools
        usage.prompt_tokens = 900
        usage.completion_tokens = 100
        usage.total_tokens = 1_000
        if aenderungen is not None:
            usage.tool_calls.append(ProviderToolCall(
                id="c1", name=WERKZEUG_NAME, arguments={"aenderungen": aenderungen},
            ))
        if False:  # pragma: no cover - macht die Funktion zum Generator
            yield StreamChunk("content", "")

    async def ohne_denken(*_a, **_k):
        return False, None

    monkeypatch.setattr(ai_gedaechtnis_schreiber, "stream_chat_completion", fake)
    monkeypatch.setattr(ai_reasoning, "aus_fuer", ohne_denken)
    return gesehen


def _merken(
    db: Session, user: User, text: str, *, origin: str = "ai", wichtigkeit: int = 3,
    art: str | None = None, faellig_am: date | None = None, alter_tage: int = 0,
) -> AiMemoryEntry:
    row, _ = ai_memory_service.erinnerung_anlegen(
        db, user=user, scope="user", text=text, origin=origin, wichtigkeit=wichtigkeit,
        art=art, faellig_am=faellig_am,
    )
    if alter_tage:
        row.created_at = datetime.now(timezone.utc) - timedelta(days=alter_tage)
        db.commit()
    return row


def _pflegen(db: Session, identity: str) -> Pflege:
    ergebnis = asyncio.run(ai_gedaechtnis_pflege.bereich_pflegen(identity))
    db.expire_all()
    return ergebnis


def _text(db: Session, row_id: str) -> tuple[AiMemoryEntry, str]:
    row = db.get(AiMemoryEntry, row_id)
    row.__dict__.pop("_titel_klartext", None)
    [(geoeffnet, text)] = ai_memory_service._entschluesseln_lesbare([row])
    return geoeffnet, text


def _gruende(db: Session, row_id: str) -> list[str]:
    return sorted(grund for (grund,) in db.query(AiMemoryVersion.grund).filter(AiMemoryVersion.memory_id == row_id))


def _nutzertext(gesehen: dict) -> str:
    return gesehen["messages"][1]["content"]


# ── Stärke ──────────────────────────────────────────────────────────────


def test_gebrauch_nach_langer_pause_festigt_mehr(db: Session, regular_user: User) -> None:
    _erlauben(db, regular_user)
    gleich = _merken(db, regular_user, "Jonas spielt Schach.")
    spaeter = _merken(db, regular_user, "Jonas spielt Go.")
    spaeter.last_used_at = datetime.now(timezone.utc) - timedelta(days=30)
    db.commit()

    ai_gedaechtnis_abruf._gebraucht(db, [gleich, spaeter])

    # Wichtigkeit 3 beginnt bei 30 Tagen; sofort wieder ×1,2, nach 30 Tagen ×2,2.
    assert gleich.haltbarkeit_tage == pytest.approx(36.0, rel=1e-3)
    assert spaeter.haltbarkeit_tage == pytest.approx(66.0, rel=1e-3)
    assert ai_memory_service.MAX_HALTBARKEIT_TAGE == 3_650.0


def test_der_kopf_ordnet_sich_erst_mit_der_rechnung_der_pflege(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Zwischen zwei Rechnungen bleibt der Kopf bytegleich — sonst bräche der Zwischenspeicher."""
    ohne_modell(monkeypatch)
    _erlauben(db, regular_user)
    alt = _merken(db, regular_user, "Jonas hatte früher ein Motorrad.", wichtigkeit=4, alter_tage=400)
    _merken(db, regular_user, "Jonas trinkt Tee am Abend.", wichtigkeit=2)

    vorher = ai_gedaechtnis_abruf.abrufen(db, regular_user, "").kopf
    ai_gedaechtnis_abruf._gebraucht(db, [alt])
    db.commit()
    nach_gebrauch = ai_gedaechtnis_abruf.abrufen(db, regular_user, "").kopf
    alt.last_used_at = datetime.now(timezone.utc) - timedelta(days=400)
    ai_gedaechtnis_pflege.rang_auffrischen(db)
    db.commit()
    nachher = ai_gedaechtnis_abruf.abrufen(db, regular_user, "").kopf

    assert vorher == nach_gebrauch
    assert "Motorrad" in vorher.splitlines()[0]
    # 400 Tage ohne Gebrauch: wichtig war es, präsent ist es nicht mehr.
    assert "Tee" in nachher.splitlines()[0]
    assert "Motorrad" in nachher


def test_die_rechnung_in_der_datenbank_ist_dieselbe(db: Session, regular_user: User) -> None:
    _erlauben(db, regular_user)
    jetzt = datetime.now(timezone.utc)
    zeilen = [
        _merken(db, regular_user, "Jonas mag Jazz.", wichtigkeit=5, alter_tage=100),
        _merken(db, regular_user, "Jonas mag Blues.", wichtigkeit=1, alter_tage=5_000),
        _merken(db, regular_user, "Jonas mag Soul.", wichtigkeit=3),
    ]
    zeilen[2].haltbarkeit_tage = 80.0
    zeilen[2].last_used_at = jetzt - timedelta(days=40)
    db.commit()

    assert ai_gedaechtnis_pflege.rang_auffrischen(db, jetzt=jetzt) == 3
    db.commit()
    db.expire_all()

    for row in zeilen:
        row = db.get(AiMemoryEntry, row.id)
        assert row.kopf_rang == pytest.approx(ai_memory_service.kopf_rang(row, jetzt), abs=1e-6)
    # Fünftausend Tage: kein Unterlauf, nur fast nichts.
    assert db.get(AiMemoryEntry, zeilen[1].id).kopf_rang < 1e-100
    assert db.get(AiMemoryEntry, zeilen[2].id).kopf_rang == pytest.approx(3 * math.exp(-0.5), rel=1e-4)


def test_der_rang_wird_einmal_am_tag_gerechnet(db: Session, regular_user: User) -> None:
    _erlauben(db, regular_user)
    _merken(db, regular_user, "Jonas mag Jazz.")
    nacht = datetime(2026, 10, 8, 1, 0, tzinfo=timezone.utc)

    assert ai_gedaechtnis_pflege.rang_takt(jetzt=nacht) is None
    assert ai_gedaechtnis_pflege.rang_takt(jetzt=nacht.replace(hour=3, minute=1)) == 1
    assert ai_gedaechtnis_pflege.rang_takt(jetzt=nacht.replace(hour=18)) is None
    assert ai_gedaechtnis_pflege.rang_takt(jetzt=nacht + timedelta(days=1, hours=3)) == 1


# ── Zusammenführen ──────────────────────────────────────────────────────


def test_doppeltes_von_singra_wird_eins_und_bringt_sein_gewicht_mit(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    modell_ersetzen(monkeypatch, _achsen)
    _erlauben(db, regular_user)
    erste = _merken(db, regular_user, "Jonas trinkt Kaffee schwarz.", wichtigkeit=2)
    zweite = _merken(db, regular_user, "Jonas mag seinen Kaffee ohne Milch.", wichtigkeit=4)
    zweite.use_count = 7
    db.commit()
    _merken(db, regular_user, "Jonas hat eine Katze namens Pixel.")
    gesehen = _modell(monkeypatch, [{
        "aktion": "zusammenfuehren", "eintrag": "E1", "aufgenommen": ["E2"],
        "text": "Jonas trinkt Kaffee schwarz, ohne Milch.",
    }])

    ergebnis = _pflegen(db, f"user:{regular_user.id}")

    assert (ergebnis.status, ergebnis.zusammengefuehrt) == ("ok", 1)
    assert "E1 [neu]" in _nutzertext(gesehen) and "Pixel" in _nutzertext(gesehen)
    ziel, text = _text(db, erste.id)
    assert text == "Jonas trinkt Kaffee schwarz, ohne Milch."
    assert (ziel.wichtigkeit, ziel.use_count) == (4, 7)
    assert db.get(AiMemoryEntry, zweite.id).status == "vergessen"
    assert _gruende(db, erste.id) == ["aufgenommen", "zusammengefuehrt"]
    buchung = db.query(AiUsageEvent).one()
    assert (buchung.user_id, buchung.zweck) == (regular_user.id, "gedaechtnis")
    assert db.get(AiMemoryPflege, f"user:{regular_user.id}") is not None


def test_der_wortlaut_des_menschen_bleibt(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    modell_ersetzen(monkeypatch, _achsen)
    _erlauben(db, regular_user)
    mensch = _merken(db, regular_user, "Ich trinke Kaffee immer schwarz.", origin="user")
    singra = _merken(db, regular_user, "Jonas trinkt Kaffee ohne Zucker.")
    _modell(monkeypatch, [
        # Den Satz des Menschen umschreiben: nein.
        {"aktion": "zusammenfuehren", "eintrag": "E1", "aufgenommen": ["E2"], "text": "Neu."},
        # Den Menschen in einem neuen Text aufgehen lassen: nein.
        {"aktion": "zusammenfuehren", "eintrag": "E2", "aufgenommen": ["E1"], "text": "Neu."},
        # Ohne Text falsch herum, wie in der Probe mit dem echten Modell:
        # gemeint ist, dass Singras Doppel in seinem Satz aufgeht.
        {"aktion": "zusammenfuehren", "eintrag": "E2", "aufgenommen": ["E1"]},
    ])

    ergebnis = _pflegen(db, f"user:{regular_user.id}")

    assert (ergebnis.zusammengefuehrt, ergebnis.verworfen) == (1, 2)
    assert _text(db, mensch.id)[1] == "Ich trinke Kaffee immer schwarz."
    assert db.get(AiMemoryEntry, mensch.id).origin == "user"
    assert db.get(AiMemoryEntry, singra.id).status == "vergessen"


def test_ohne_aehnliches_und_faelliges_fragt_sie_nicht(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    modell_ersetzen(monkeypatch, _achsen)
    _erlauben(db, regular_user)
    _merken(db, regular_user, "Jonas trinkt Kaffee schwarz.")
    _merken(db, regular_user, "Jonas hat eine Katze namens Pixel.")
    gesehen = _modell(monkeypatch, [])
    identity = f"user:{regular_user.id}"

    assert _pflegen(db, identity).status == "ruhig"
    assert _pflegen(db, identity).status == "leer"

    assert gesehen["aufrufe"] == 0
    assert db.get(AiMemoryPflege, identity).gepflegt_bis is not None
    assert db.query(AiUsageEvent).count() == 0



def test_was_ueber_einen_lauf_hinausgeht_kommt_im_naechsten(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    modell_ersetzen(monkeypatch, _achsen)
    monkeypatch.setattr(ai_gedaechtnis_pflege, "NEU_JE_LAUF", 1)
    _erlauben(db, regular_user)
    _merken(db, regular_user, "Jonas trinkt Kaffee schwarz.")
    _merken(db, regular_user, "Jonas hat eine Katze namens Pixel.")
    gesehen = _modell(monkeypatch, [])
    identity = f"user:{regular_user.id}"

    assert [_pflegen(db, identity).status for _ in range(3)] == ["mehr", "ruhig", "leer"]
    assert gesehen["aufrufe"] == 0


def test_ohne_sidecar_bleibt_alles_wie_es_war(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests._entschluesselung import sidecar_tot

    modell_ersetzen(monkeypatch, _achsen)
    _erlauben(db, regular_user)
    _merken(db, regular_user, "Jonas trinkt Kaffee schwarz.")
    _merken(db, regular_user, "Jonas mag Kaffee ohne Milch.")
    gesehen = _modell(monkeypatch, [])
    sidecar_tot(monkeypatch)
    identity = f"user:{regular_user.id}"

    assert _pflegen(db, identity).status == "fehler"

    assert gesehen["aufrufe"] == 0
    assert db.get(AiMemoryPflege, identity) is None
    assert ai_gedaechtnis_pflege._STAND[identity].ruhe_bis is not None

# ── Zeit ────────────────────────────────────────────────────────────────


def test_ein_abgelaufener_plan_wird_vergangenheit_ohne_ausgang(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ohne_modell(monkeypatch)
    _erlauben(db, regular_user)
    gestern = datetime.now(timezone.utc).date() - timedelta(days=1)
    # Der ältere Fälligkeitstag steht vorn: E1.
    reise = _merken(
        db, regular_user, "Ich fahre im Juli 2026 nach Singapur.", origin="user",
        art="plan", faellig_am=gestern - timedelta(days=1),
    )
    umzug = _merken(
        db, regular_user, "Jonas plant für September 2026 einen Umzug.", art="plan", faellig_am=gestern,
    )
    zukunft = _merken(
        db, regular_user, "Jonas plant für 2030 eine Weltreise.", art="plan",
        faellig_am=date(2030, 1, 1),
    )
    gesehen = _modell(monkeypatch, [
        {"aktion": "zeit", "eintrag": "E1", "text": "Der Benutzer plante für Juli 2026 eine Reise nach Singapur."},
        # Nicht fällig: bleibt.
        {"aktion": "zeit", "eintrag": "E3", "text": "Jonas plante eine Weltreise."},
    ])

    ergebnis = _pflegen(db, f"user:{regular_user.id}")

    assert (ergebnis.umgeschrieben, ergebnis.verworfen) == (1, 1)
    assert "E1 [fällig]" in _nutzertext(gesehen)
    row, text = _text(db, reise.id)
    assert text == "Der Benutzer plante für Juli 2026 eine Reise nach Singapur."
    assert (row.art, row.faellig_am, row.origin) == ("ereignis", None, "user")
    assert _gruende(db, reise.id) == ["aktualisiert"]
    # Gelesen, nicht umgeschrieben: kommt trotzdem nicht jede Nacht wieder.
    assert db.get(AiMemoryEntry, umzug.id).faellig_am is None
    assert db.get(AiMemoryEntry, zukunft.id).faellig_am == date(2030, 1, 1)


def test_der_schreiber_traegt_den_tag_eines_plans_ein(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _erlauben(db, regular_user)
    gespraech = _gespraech(db, regular_user, [("user", "Ich fahre im Juli nach Singapur.")])
    _schreibermodell(monkeypatch, {"aenderungen": [{
        "aktion": "neu", "bereich": "B1", "art": "plan", "bis": "2026-08-01",
        "text": "Der Benutzer plant für Juli 2026 eine Reise nach Singapur.", "beleg": ["N1"],
    }]})

    assert _durchgang(db, gespraech).neu == 1

    row = db.query(AiMemoryEntry).filter(AiMemoryEntry.owner_user_id == regular_user.id).one()
    assert row.faellig_am == date(2026, 8, 1)


# ── Wer zahlt, wann sie läuft ───────────────────────────────────────────


def test_ohne_einwilligung_oder_kontingent_wartet_sie(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    modell_ersetzen(monkeypatch, _achsen)
    _erlauben(db, regular_user, daily_token_limit=10)
    _merken(db, regular_user, "Jonas trinkt Kaffee schwarz.")
    _merken(db, regular_user, "Jonas mag Kaffee ohne Milch.")
    gesehen = _modell(monkeypatch, [])
    identity = f"user:{regular_user.id}"

    assert _pflegen(db, identity).status == "spaeter"
    ai_memory_service.set_preference(db, regular_user, False)
    assert ai_gedaechtnis_pflege.faellige_starten(db) == []
    assert _pflegen(db, identity).status == "spaeter"

    assert gesehen["aufrufe"] == 0
    assert db.get(AiMemoryPflege, identity) is None


@pytest.mark.asyncio
async def test_sie_laeuft_nachts_und_nach_anderthalb_tagen_auch_am_tag(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ohne_modell(monkeypatch)
    _erlauben(db, regular_user)
    _merken(db, regular_user, "Jonas trinkt Kaffee schwarz.")
    identity = f"user:{regular_user.id}"
    gelaufen: list[str] = []

    async def fake(kennung: str) -> Pflege:
        gelaufen.append(kennung)
        return Pflege("ok")

    monkeypatch.setattr(ai_gedaechtnis_pflege, "bereich_pflegen", fake)
    mittag = datetime.now(timezone.utc).replace(hour=12, minute=0)
    nacht = mittag.replace(hour=3)

    # Noch nie gepflegt: gleich, auch am Tag.
    assert ai_gedaechtnis_pflege.faellige_starten(db, jetzt=mittag) == [identity]
    await asyncio.gather(*list(ai_gedaechtnis_pflege._LAUFEND.values()))
    db.add(AiMemoryPflege(
        scope_identity=identity, gepflegt_bis=mittag - timedelta(days=2), gelaufen_am=mittag - timedelta(hours=2),
    ))
    db.commit()
    monkeypatch.setattr(ai_gedaechtnis_pflege, "_LEERLAUF_BIS", None)
    assert ai_gedaechtnis_pflege.faellige_starten(db, jetzt=mittag) == []
    monkeypatch.setattr(ai_gedaechtnis_pflege, "_LEERLAUF_BIS", None)
    assert ai_gedaechtnis_pflege.faellige_starten(db, jetzt=nacht) == [identity]
    await asyncio.gather(*list(ai_gedaechtnis_pflege._LAUFEND.values()))

    assert gelaufen == [identity, identity]


def test_wo_noch_altbestand_steht_wartet_sie(db: Session, regular_user: User) -> None:
    """Der Altbestand führt bewusst nichts zusammen; die Pflege kommt danach."""
    _erlauben(db, regular_user)
    ai_memory_service.upsert_entry(
        db, user=regular_user, scope="user", server_id=None, key="lieblingsessen", value="Pizza", origin="user",
    )

    assert ai_gedaechtnis_pflege.faellige_starten(db) == []
