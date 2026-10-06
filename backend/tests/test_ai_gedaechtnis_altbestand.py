"""Gedächtnis v2, Stufe 2: Erinnerungen mit Namen werden Sätze.

Was diese Tests festhalten:

- Umgeschrieben wird die Form, nicht die Aussage: der Stand davor bleibt als
  Fassung, mit dem alten Namen als Titel, und die Herkunft bleibt.
- Was in der Antwort fehlt, wird mechanisch umgestellt — Wert bleibt Text,
  Name wird Titel. Was das Modell erfindet, trifft nichts.
- Bezahlt wird nur, wo jemand zahlen darf: der eigene Bereich mit
  Einwilligung, Teamwissen beim Gründer, Anlagenwissen beim Betreiberkonto.
- Je Takt höchstens zwei Bereiche, und nichts läuft endlos gegen dieselbe Wand.

Gefälscht wird nur der Anbieter. Alle Personen sind erfunden.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from models import AiMemoryEntry, AiUsageEvent, User
from services import (
    ai_gedaechtnis_altbestand,
    ai_gedaechtnis_schreiber,
    ai_memory_service,
    ai_reasoning,
    team_service,
)
from services.ai_gedaechtnis_altbestand import WERKZEUG_NAME, Umschrieb
from services.openai_compatible_adapter import AiProviderRequestError, ProviderToolCall, StreamChunk
from tests.test_ai_gedaechtnis_schreiber import _anbieter, _benutzer, _erlauben, _mit_teams, _server


# ── Hilfen ──────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _frischer_takt(monkeypatch) -> None:
    """Was ein Bereich erlebt hat, lebt im Prozess — je Test von vorn."""
    monkeypatch.setattr(ai_gedaechtnis_altbestand, "_STAND", {})
    monkeypatch.setattr(ai_gedaechtnis_altbestand, "_LAUFEND", {})
    monkeypatch.setattr(ai_gedaechtnis_altbestand, "_LEERLAUF_BIS", None)


def _alt(db: Session, user: User, key: str, value: str, *, origin: str = "user", **bereich) -> AiMemoryEntry:
    """Ein Eintrag, wie es ihn vor Gedächtnis v2 gab: Name und Wert."""
    bereich.setdefault("scope", "user")
    bereich.setdefault("server_id", None)
    row, _ = ai_memory_service.upsert_entry(db, user=user, key=key, value=value, origin=origin, **bereich)
    assert row.key_index is not None
    return row


def _modell(monkeypatch, argumente: dict | None = None, *, platzt: Exception | None = None) -> dict:
    """Ersetzt den Anbieter. Gibt zurück, was er zu sehen bekam."""
    gesehen: dict = {"aufrufe": 0}

    async def fake(_client, *, provider, api_key, messages, usage, model=None, tools=None,
                   tool_choice=None, reasoning=False, reasoning_effort=None, **_rest):
        del provider, api_key, model, reasoning, reasoning_effort
        gesehen["aufrufe"] += 1
        gesehen["messages"] = messages
        gesehen["tools"] = tools
        gesehen["tool_choice"] = tool_choice
        if platzt is not None:
            raise platzt
        usage.prompt_tokens = 900
        usage.completion_tokens = 100
        usage.total_tokens = 1_000
        if argumente is not None:
            usage.tool_calls.append(ProviderToolCall(id="c1", name=WERKZEUG_NAME, arguments=argumente))
        if False:  # pragma: no cover - macht die Funktion zum Generator
            yield StreamChunk("content", "")

    async def ohne_denken(*_a, **_k):
        return False, None

    monkeypatch.setattr(ai_gedaechtnis_schreiber, "stream_chat_completion", fake)
    monkeypatch.setattr(ai_reasoning, "aus_fuer", ohne_denken)
    return gesehen


def _umschreiben(db: Session, identity: str) -> Umschrieb:
    ergebnis = asyncio.run(ai_gedaechtnis_altbestand.bereich_umschreiben(identity))
    db.expire_all()
    return ergebnis


def _text(db: Session, row_id: str) -> tuple[AiMemoryEntry, str]:
    row = db.get(AiMemoryEntry, row_id)
    # Titel und Name merkt sich die Zeile im Klartext neben den Spalten, und
    # `expire_all` räumt nur die Spalten ab: ohne das läse der Test den Titel,
    # den die Zeile in dieser Sitzung beim Anlegen hatte.
    for gemerkt in ("_titel_klartext", "_key_klartext"):
        row.__dict__.pop(gemerkt, None)
    [(geoeffnet, text)] = ai_memory_service._entschluesseln_lesbare([row])
    return geoeffnet, text


def _nutzertext(gesehen: dict) -> str:
    return gesehen["messages"][1]["content"]


# ── Was aus einem Eintrag wird ──────────────────────────────────────────


def test_ein_eintrag_mit_namen_wird_ein_satz(db: Session, regular_user: User, monkeypatch) -> None:
    _erlauben(db, regular_user)
    alt = _alt(db, regular_user, "lieblingsessen", "Pizza")
    gesehen = _modell(monkeypatch, {"eintraege": [{
        "eintrag": "E1", "text": "Der Benutzer isst am liebsten Pizza.", "titel": "Lieblingsessen",
        "thema": "Essen", "art": "vorliebe", "wichtigkeit": 2,
    }]})

    ergebnis = _umschreiben(db, f"user:{regular_user.id}")

    assert (ergebnis.status, ergebnis.umgeschrieben, ergebnis.umgestellt) == ("ok", 1, 0)
    assert "Bereich: Persönlich: über den Benutzer" in _nutzertext(gesehen)
    assert "lieblingsessen: Pizza" in _nutzertext(gesehen)
    assert gesehen["tool_choice"] == {"type": "function", "function": {"name": WERKZEUG_NAME}}

    row, text = _text(db, alt.id)
    assert text == "Der Benutzer isst am liebsten Pizza."
    assert (row.key_encrypted, row.key_index) == (None, None)
    assert (row.titel, row.art, row.wichtigkeit, row.fassung) == ("Lieblingsessen", "vorliebe", 2, 2)
    # Die Form ist neu, die Aussage dieselbe: sie bleibt die des Menschen.
    assert row.origin == "user"
    assert ai_memory_service.themennamen(db, [(row.thema_id, row.scope_identity)]) == {row.thema_id: "Essen"}

    # Was vorher dastand, steht im Verlauf — mit dem alten Namen als Titel.
    [fassung] = ai_memory_service.fassungen(db, user=regular_user, entry_id=alt.id)
    assert (fassung.text, fassung.titel, fassung.grund, fassung.von) == (
        "Pizza", "lieblingsessen", "umgeschrieben", "ai",
    )

    buchung = db.query(AiUsageEvent).filter(AiUsageEvent.user_id == regular_user.id).one()
    assert (buchung.zweck, buchung.status) == ("gedaechtnis", "completed")


def test_was_in_der_antwort_fehlt_wird_mechanisch_umgestellt(
    db: Session, regular_user: User, monkeypatch
) -> None:
    """Fehlt ein Eintrag, bleibt der Wert der Text und der Name wird Titel.

    Eine erfundene Kennung trifft nichts: E7 gibt es in diesem Stapel nicht.
    """
    _erlauben(db, regular_user)
    erster = _alt(db, regular_user, "lieblingsessen", "Pizza")
    zweiter = _alt(db, regular_user, "backup.zeitpunkt", "Sonntags um drei Uhr nachts", origin="ai")
    _modell(monkeypatch, {"eintraege": [
        {"eintrag": "E1", "text": "Der Benutzer isst am liebsten Pizza."},
        {"eintrag": "E7", "text": "Der Benutzer heißt Jonas."},
    ]})

    ergebnis = _umschreiben(db, f"user:{regular_user.id}")

    assert (ergebnis.umgeschrieben, ergebnis.umgestellt) == (1, 1)
    row, text = _text(db, zweiter.id)
    assert text == "Sonntags um drei Uhr nachts"
    assert (row.titel, row.key_encrypted, row.origin, row.fassung) == ("Backup zeitpunkt", None, "ai", 2)
    # Der Text ist derselbe, die Fassung entsteht trotzdem: sonst stünde der
    # Name nirgends mehr.
    [fassung] = ai_memory_service.fassungen(db, user=regular_user, entry_id=zweiter.id)
    assert (fassung.text, fassung.titel, fassung.grund) == (
        "Sonntags um drei Uhr nachts", "backup.zeitpunkt", "umgeschrieben",
    )
    # Ohne Titel in der Antwort wird der alte Name Titel.
    assert _text(db, erster.id)[0].titel == "Lieblingsessen"
    texte = [text for _row, text in ai_memory_service.list_entries(db, regular_user, "user", None)]
    assert not any("Jonas" in text for text in texte)


def test_ein_titel_vom_menschen_bleibt_und_der_name_steht_im_verlauf(
    db: Session, regular_user: User, monkeypatch
) -> None:
    """Einen Titel, den die Erinnerung schon trägt, hat ein Mensch gesetzt.

    Bis zum Prüfbefund vom 06.10.2026 ging in genau diesem Fall der Name
    verloren: eine Fassung entstand nur bei neuem Text, und ihr Titel war der
    vorhandene, nicht der Name.
    """
    _erlauben(db, regular_user)
    alt = _alt(db, regular_user, "lieblingsessen", "Pizza")
    alt.titel = "Was ich gern esse"
    db.commit()
    _modell(monkeypatch, {"eintraege": [{"eintrag": "E1", "text": "Pizza", "titel": "Lieblingsessen"}]})

    assert _umschreiben(db, f"user:{regular_user.id}").umgeschrieben == 1

    row, text = _text(db, alt.id)
    assert (text, row.titel, row.key_encrypted) == ("Pizza", "Was ich gern esse", None)
    [fassung] = ai_memory_service.fassungen(db, user=regular_user, entry_id=alt.id)
    assert (fassung.text, fassung.titel) == ("Pizza", "lieblingsessen")


def test_zugangsdaten_im_neuen_text_lassen_den_alten_stehen(
    db: Session, regular_user: User, monkeypatch
) -> None:
    _erlauben(db, regular_user)
    alt = _alt(db, regular_user, "lieblingsessen", "Pizza")
    geheim = "api_key=" + "x" * 12
    _modell(monkeypatch, {"eintraege": [{"eintrag": "E1", "text": f"Der Benutzer nutzt {geheim}."}]})

    ergebnis = _umschreiben(db, f"user:{regular_user.id}")

    assert (ergebnis.umgeschrieben, ergebnis.umgestellt) == (0, 1)
    row, text = _text(db, alt.id)
    assert (text, row.titel, row.key_encrypted) == ("Pizza", "Lieblingsessen", None)


def test_ein_bereich_ohne_altbestand_kostet_nichts(db: Session, regular_user: User, monkeypatch) -> None:
    _erlauben(db, regular_user)
    ai_memory_service.erinnerung_anlegen(
        db, user=regular_user, scope="user", text="Der Benutzer isst am liebsten Pizza.",
    )
    gesehen = _modell(monkeypatch, {"eintraege": []})

    assert _umschreiben(db, f"user:{regular_user.id}").status == "leer"
    assert gesehen["aufrufe"] == 0


# ── Wer zahlt ───────────────────────────────────────────────────────────


def test_ohne_einwilligung_wartet_der_eigene_bereich(db: Session, regular_user: User, monkeypatch) -> None:
    """Das eigene Gedächtnis ginge dafür an den Anbieter — ohne Einwilligung nicht.

    Die Einträge mit Namen funktionieren bis dahin wie bisher; der Takt fragt
    den Bereich gar nicht erst.
    """
    _erlauben(db, regular_user, einwilligung=False)
    alt = _alt(db, regular_user, "lieblingsessen", "Pizza")
    gesehen = _modell(monkeypatch, {"eintraege": []})

    assert ai_gedaechtnis_altbestand.faellige_starten(db) == []
    assert _umschreiben(db, f"user:{regular_user.id}").status == "spaeter"

    assert gesehen["aufrufe"] == 0
    assert db.get(AiMemoryEntry, alt.id).key_encrypted is not None
    assert db.query(AiUsageEvent).count() == 0


def test_teamwissen_zahlt_der_gruender(db: Session, regular_user: User, monkeypatch) -> None:
    _mit_teams(db, regular_user, einwilligung=False)
    team = team_service.create_team(db, user=regular_user, name="Betrieb")
    kollege = _benutzer(db, "kollege")
    team_service.personal_team(db, kollege)
    team_service.invite_member(
        db, team=team, user=regular_user, new_user_id=kollege.id,
        can_manage_skills=False, can_manage_memory=True,
    )
    team_service.accept_invitation(db, user=kollege, team_id=team.id)
    # Geschrieben hat der Kollege; das Team gehört trotzdem nicht ihm.
    alt = _alt(db, kollege, "wartung", "sonntags", scope="team", team_id=team.id)
    gesehen = _modell(monkeypatch, {"eintraege": [
        {"eintrag": "E1", "text": "Wartung ist im Team sonntags."},
    ]})

    assert _umschreiben(db, f"team:{team.id}").umgeschrieben == 1

    assert "Bereich: Teamwissen „Betrieb“" in _nutzertext(gesehen)
    assert _text(db, alt.id)[1] == "Wartung ist im Team sonntags."
    buchung = db.query(AiUsageEvent).one()
    assert (buchung.user_id, buchung.zweck) == (regular_user.id, "gedaechtnis")


def test_anlagenwissen_zahlt_das_betreiberkonto(
    db: Session, regular_user: User, owner_user: User, monkeypatch
) -> None:
    """Das Wissen einer Anlage gehört keinem Menschen; es zahlt der Betrieb."""
    _erlauben(db, regular_user, einwilligung=False)
    owner_user.ai_provider_id = _anbieter(db).id
    db.commit()
    server = _server(db, regular_user, "server.config.write")
    alt = _alt(db, regular_user, "mod_reihenfolge", "CF vor Expansion", scope="server_shared", server_id=server.id)
    gesehen = _modell(monkeypatch, {"eintraege": [{
        "eintrag": "E1", "text": "Auf dem Server Werkstatt muss CF vor Expansion geladen werden.",
    }]})

    assert _umschreiben(db, f"server:{server.id}:shared").umgeschrieben == 1

    assert "Bereich: Wissen des Servers „Werkstatt“" in _nutzertext(gesehen)
    assert db.get(AiMemoryEntry, alt.id).key_encrypted is None
    buchung = db.query(AiUsageEvent).one()
    assert buchung.user_id == owner_user.id
    # Wer das Wissen nur geschrieben hat, zahlt nichts.
    assert db.query(AiUsageEvent).filter(AiUsageEvent.user_id == regular_user.id).count() == 0


# ── Takt und Fehlschlag ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_je_takt_hoechstens_zwei_bereiche(db: Session, regular_user: User, monkeypatch) -> None:
    benutzer = [regular_user, _benutzer(db, "zweite"), _benutzer(db, "dritte")]
    for user in benutzer:
        _erlauben(db, user)
        _alt(db, user, "lieblingsessen", "Pizza")
    stumm = _benutzer(db, "stumm")
    _erlauben(db, stumm, einwilligung=False)
    _alt(db, stumm, "lieblingsessen", "Pizza")
    freigabe = asyncio.Event()

    async def fake(identity: str) -> Umschrieb:
        await freigabe.wait()
        # Kein Zugang, sagen wir: der Bereich ruht eine Weile.
        ergebnis = Umschrieb("spaeter")
        ai_gedaechtnis_altbestand._merken(identity, ergebnis)
        return ergebnis

    monkeypatch.setattr(ai_gedaechtnis_altbestand, "bereich_umschreiben", fake)

    erste = ai_gedaechtnis_altbestand.faellige_starten(db)
    voll = ai_gedaechtnis_altbestand.faellige_starten(db)
    freigabe.set()
    await asyncio.gather(*list(ai_gedaechtnis_altbestand._LAUFEND.values()))
    dann = ai_gedaechtnis_altbestand.faellige_starten(db)
    await asyncio.gather(*list(ai_gedaechtnis_altbestand._LAUFEND.values()))

    assert len(erste) == 2
    assert voll == []
    # Was ruht, kommt nicht dran; der dritte Bereich schon.
    assert len(dann) == 1
    assert set(erste) | set(dann) == {f"user:{user.id}" for user in benutzer}


def test_dreimal_unbrauchbar_stellt_mechanisch_um(db: Session, regular_user: User, monkeypatch) -> None:
    """Dasselbe Material liefe sonst bei jedem Versuch gegen dieselbe Wand."""
    _erlauben(db, regular_user)
    alt = _alt(db, regular_user, "lieblingsessen", "Pizza")
    gesehen = _modell(monkeypatch, {"eintraege": []})
    identity = f"user:{regular_user.id}"

    for _ in range(ai_gedaechtnis_altbestand.MAX_UNBRAUCHBAR - 1):
        assert _umschreiben(db, identity).status == "unbrauchbar"
        assert db.get(AiMemoryEntry, alt.id).key_encrypted is not None
    stand = ai_gedaechtnis_altbestand._STAND[identity]
    assert stand.ruhe_bis > datetime.now(timezone.utc) + timedelta(minutes=5)

    ergebnis = _umschreiben(db, identity)

    assert (ergebnis.status, ergebnis.umgestellt) == ("ok", 1)
    row, text = _text(db, alt.id)
    assert (text, row.titel, row.key_encrypted) == ("Pizza", "Lieblingsessen", None)
    assert identity not in ai_gedaechtnis_altbestand._STAND
    assert gesehen["aufrufe"] == ai_gedaechtnis_altbestand.MAX_UNBRAUCHBAR


def test_ein_stummer_anbieter_stellt_nichts_um(db: Session, regular_user: User, monkeypatch) -> None:
    """Scheitert der Anbieter, wird gewartet — mechanisch nur nach Antworten.

    Ein Ausfall sagt nichts über den Stapel; nach drei Stunden Störung stünde
    sonst Bestand mechanisch umgestellt da, den das Modell danach gut
    umgeschrieben hätte.
    """
    _erlauben(db, regular_user)
    alt = _alt(db, regular_user, "lieblingsessen", "Pizza")
    _modell(monkeypatch, platzt=AiProviderRequestError("AI_PROVIDER_UNAVAILABLE", "kaputt"))
    identity = f"user:{regular_user.id}"

    for _ in range(ai_gedaechtnis_altbestand.MAX_UNBRAUCHBAR + 1):
        assert _umschreiben(db, identity).status == "fehler"

    assert db.get(AiMemoryEntry, alt.id).key_encrypted is not None
    stand = ai_gedaechtnis_altbestand._STAND[identity]
    assert (stand.fehlversuche, stand.unbrauchbar) == (ai_gedaechtnis_altbestand.MAX_UNBRAUCHBAR + 1, 0)
    assert db.query(AiUsageEvent).filter(AiUsageEvent.status == "completed").count() == 0


def test_eine_lange_stoerung_laeuft_nicht_ueber() -> None:
    """Zehn Tage Störung sind vierzig Fehlschläge; `timedelta` lief dort über.

    Der Fehler fiel in `_merken`, der Bereich behielt seine alte Ruhezeit und
    kam bei jedem Takt wieder dran.
    """
    identity = "user:1"
    ai_gedaechtnis_altbestand._STAND[identity] = ai_gedaechtnis_altbestand._Stand(fehlversuche=50)

    ai_gedaechtnis_altbestand._merken(identity, Umschrieb("fehler"))

    stand = ai_gedaechtnis_altbestand._STAND[identity]
    assert stand.ruhe_bis > datetime.now(timezone.utc) + timedelta(hours=5)
