"""Gedächtnis v2, Stufe 3: der Import liest mit dem Gedächtnismodell.

Was diese Tests festhalten:

* Die Vorschau schreibt nichts. Der Text geht redigiert an den Anbieter; ein
  Geheimnis kommt weder dorthin noch zurück.
* Ein langer Text geht in Teilen, jeder mit der Überschrift, unter der er
  steht — Überschriften sind Zusammenhang, keine Erinnerung.
* Bestand wird ersetzt statt verdoppelt; was schon dasteht, fällt heraus.
* Übernommen wird mit demselben Kontingent wie von Hand: eine Zahl,
  unbegrenzt, 0 heißt gesperrt.
* Wer nicht schreiben darf, bekommt die Absage, bevor der Text beim Anbieter
  ist.
* Je Benutzer liest ein Import zugleich; ein abgebrochener Aufruf schließt
  seine Reservierung ohne Kosten.

Gefälscht wird nur der Anbieter (`stream_chat_completion`). Alle Personen
sind erfunden.
"""

from __future__ import annotations

import time
from typing import Any, Callable
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import AiMemoryEntry, AiMemoryVersion, AiProvider, AiUsageEvent, Role, RolePermission, User
from services import ai_gedaechtnis_schreiber, ai_memory_import_service, ai_memory_service, ai_reasoning
from services.ai_limit_service import LIMIT_FIELDS, set_role_limit
from services.ai_memory_import_service import WERKZEUG_NAME, teile
from services.openai_compatible_adapter import AiProviderRequestError, ProviderToolCall, StreamChunk
from services.role_service import set_user_roles
from tests._einbettung import modell_ersetzen
from tests.test_ai_gedaechtnis_schreiber import _benutzer

# Zusammengesetzt, nie als Literal: das Repository ist öffentlich, und ein
# Scanner unterscheidet einen Testwert nicht von einem echten.
GEHEIMNIS = "super" + "geheim" + "123"
GEHEIME_ZEILE = "Das Passwort ist pass" + "word: " + GEHEIMNIS

ANTWORT = f"""### 1. Demografische Informationen
* Der Name der Person ist Jonas.
* Die Person wohnt in Köln.

### 5. Anweisungen
* Antworte immer auf Deutsch.
* {GEHEIME_ZEILE}

Importiert aus: Gemini
"""


def _freischalten(db: Session, user: User, *, mit_anbieter: bool = True, **limits: int | None) -> None:
    role = Role(name=f"import-{user.id}-{uuid4().hex[:6]}", is_system=False)
    db.add(role)
    db.flush()
    db.add(RolePermission(role_id=role.id, permission_key="ai.memory.use"))
    werte: dict[str, int | None] = {field: None for field in LIMIT_FIELDS}
    werte.update(limits)
    set_role_limit(db, role.id, werte)
    db.commit()
    set_user_roles(db, user, [role.id])
    if mit_anbieter:
        provider = AiProvider(
            name=f"Import-{uuid4().hex[:8]}", provider_kind="openrouter",
            default_model="model-a", enabled=True, requires_api_key=False,
        )
        db.add(provider)
        db.commit()
        user.ai_provider_id = provider.id
        db.commit()


def _csrf(cookies: dict) -> dict[str, str]:
    return {"X-CSRF-Token": cookies.get("__Secure-csrf_token", "")}


def _modell(monkeypatch, antwort: Callable[[str], Any] | Any) -> list[dict]:
    """Ersetzt den Anbieter. ``antwort`` bekommt den Nutzertext eines Aufrufs.

    Gibt die Liste der Aufrufe zurück — die Teile laufen nebeneinander, die
    Reihenfolge ist also nicht fest.
    """
    aufrufe: list[dict] = []

    async def fake(_client, *, provider, api_key, messages, usage, model=None, tools=None,
                   tool_choice=None, reasoning=False, reasoning_effort=None, **_rest):
        del provider, api_key, reasoning, reasoning_effort
        aufrufe.append({"messages": messages, "model": model, "tools": tools, "tool_choice": tool_choice})
        ergebnis = antwort(messages[1]["content"]) if callable(antwort) else antwort
        if isinstance(ergebnis, Exception):
            raise ergebnis
        usage.prompt_tokens = 900
        usage.completion_tokens = 100
        usage.total_tokens = 1_000
        if ergebnis is not None:
            usage.tool_calls.append(ProviderToolCall(id="c1", name=WERKZEUG_NAME, arguments=ergebnis))
        if False:  # pragma: no cover - macht die Funktion zum Generator
            yield StreamChunk("content", "")

    async def ohne_denken(*_a, **_k):
        return False, None

    monkeypatch.setattr(ai_gedaechtnis_schreiber, "stream_chat_completion", fake)
    monkeypatch.setattr(ai_reasoning, "aus_fuer", ohne_denken)
    return aufrufe


def _vorschau(client: TestClient, cookies: dict, text: str, **ziel):
    return client.post(
        "/api/ai/memory/import/preview",
        json={"raw_text": text, "scope": "user", **ziel},
        cookies=cookies, headers=_csrf(cookies),
    )


def _uebernehmen(client: TestClient, cookies: dict, items: list[dict], **ziel):
    return client.post(
        "/api/ai/memory/import",
        json={"scope": "user", "items": items, **ziel},
        cookies=cookies, headers=_csrf(cookies),
    )


def _anlegen(db: Session, user: User, text: str, **felder) -> AiMemoryEntry:
    row, _ = ai_memory_service.erinnerung_anlegen(
        db, user=user, scope="user", text=text, origin=felder.pop("origin", "ai"), **felder,
    )
    return row


# ── Vorschau ────────────────────────────────────────────────────────────


def test_die_vorschau_liest_mit_dem_modell_und_schreibt_nichts(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict, monkeypatch,
) -> None:
    _freischalten(db, regular_user)
    aufrufe = _modell(monkeypatch, {"erinnerungen": [
        {"text": "Der Benutzer heißt Jonas und lebt in Köln.", "titel": "Name und Wohnort",
         "thema": "Über mich", "art": "fakt", "wichtigkeit": 5},
        {"text": "Singra antwortet dem Benutzer immer auf Deutsch.", "titel": "Sprache",
         "thema": "Anweisungen", "art": "anweisung"},
        {"text": GEHEIME_ZEILE, "titel": "Zugang"},
    ]})

    antwort = _vorschau(client, user_cookies, ANTWORT)

    assert antwort.status_code == 200, antwort.text
    daten = antwort.json()
    assert [item["text"] for item in daten["items"]] == [
        "Der Benutzer heißt Jonas und lebt in Köln.",
        "Singra antwortet dem Benutzer immer auf Deutsch.",
    ]
    assert (daten["items"][0]["titel"], daten["items"][0]["thema"], daten["items"][0]["wichtigkeit"]) == (
        "Name und Wohnort", "Über mich", 5,
    )
    assert daten["items"][1]["art"] == "anweisung"
    assert daten["total_secrets_blocked"] == 1
    assert daten["detected_source"] == "Gemini"
    # Leeres Feld heißt unbegrenzt — dann gibt es keine Platzzahl.
    assert daten["available_slots"] is None
    # Redigiert an den Anbieter, und nichts davon zurück.
    assert len(aufrufe) == 1
    assert GEHEIMNIS not in aufrufe[0]["messages"][1]["content"]
    assert GEHEIMNIS not in antwort.text
    assert aufrufe[0]["tool_choice"] == {"type": "function", "function": {"name": WERKZEUG_NAME}}
    assert db.query(AiMemoryEntry).count() == 0
    db.expire_all()
    buchung = db.query(AiUsageEvent).filter(AiUsageEvent.user_id == regular_user.id).one()
    assert buchung.zweck == "gedaechtnis"


def test_ein_langer_text_geht_in_teilen_mit_seiner_ueberschrift(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict, monkeypatch,
) -> None:
    absatz = "Ein Satz über die Jahre am Meer. " * 280  # gut 9.000 Zeichen
    text = f"# Kapitel 1\n\n{absatz}\n\n# Kapitel 2\n\n{absatz}\n\n{absatz}"

    stuecke = teile(text)

    assert [(teil.ueberschrift, teil.text[:11]) for teil in stuecke] == [
        (None, "# Kapitel 1"),
        (None, "# Kapitel 2"),
        ("Kapitel 2", absatz[:11]),
    ]

    _freischalten(db, regular_user)
    aufrufe = _modell(monkeypatch, {"erinnerungen": []})
    antwort = _vorschau(client, user_cookies, text)

    assert antwort.status_code == 200, antwort.text
    assert antwort.json()["items"] == []
    inhalte = sorted(aufruf["messages"][1]["content"] for aufruf in aufrufe)
    assert len(inhalte) == 3
    dritter = next(inhalt for inhalt in inhalte if "Teil 3 von 3" in inhalt)
    assert "Teil 3 von 3, steht unter der Überschrift „Kapitel 2“" in dritter
    # Der Anfang reist als Zusammenhang mit — dort steht meist, wer „ich“ ist.
    assert "Anfang des Textes" in dritter


def test_bestand_wird_ersetzt_und_schon_gespeichertes_faellt_heraus(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict, monkeypatch,
) -> None:
    _freischalten(db, regular_user)
    kaffee = _anlegen(db, regular_user, "Mag Kaffee.")
    morgens = _anlegen(db, regular_user, "Trinkt morgens Kaffee.")
    _anlegen(db, regular_user, "Der Benutzer wohnt in Köln.")
    aufrufe = _modell(monkeypatch, {"erinnerungen": [
        {"text": "Der Benutzer trinkt morgens gern Kaffee.", "titel": "Kaffee am Morgen",
         "thema": "Vorlieben", "ersetzt": ["E1", "E2"]},
        {"text": "Der Benutzer wohnt in Köln.", "titel": "Wohnort", "thema": "Über mich"},
        {"text": "Der Benutzer spielt Schach im Verein.", "titel": "Schach", "thema": "Hobbys",
         "ersetzt": ["E1"]},
    ]})

    daten = _vorschau(client, user_cookies, "Ich trinke morgens gern Kaffee.").json()

    assert "E1 (von Singra) Mag Kaffee." in aufrufe[0]["messages"][1]["content"]
    assert [item["text"] for item in daten["items"]] == [
        "Der Benutzer trinkt morgens gern Kaffee.",
        "Der Benutzer spielt Schach im Verein.",
    ]
    ersetzt = daten["items"][0]["ersetzt"]
    assert [(e["id"], e["fassung"], e["text"]) for e in ersetzt] == [
        (kaffee.id, 1, "Mag Kaffee."), (morgens.id, 1, "Trinkt morgens Kaffee."),
    ]
    # Ein Eintrag geht nur in einem Vorschlag auf.
    assert daten["items"][1]["ersetzt"] == []
    assert daten["total_known"] == 1


def test_fast_gleiches_gilt_als_schon_gespeichert(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict, monkeypatch,
) -> None:
    def rechner(texte: list[str]) -> list[list[float]]:
        vektoren = []
        for text in texte:
            vektor = [0.0] * 256
            vektor[0 if "Köln" in text else 1 + len(text) % 200] = 1.0
            vektoren.append(vektor)
        return vektoren

    modell_ersetzen(monkeypatch, rechner)
    _freischalten(db, regular_user)
    _anlegen(db, regular_user, "Lebt in Köln.")
    _modell(monkeypatch, {"erinnerungen": [
        {"text": "Der Benutzer lebt in Köln.", "titel": "Wohnort", "thema": "Über mich"},
        {"text": "Der Benutzer spielt Schach.", "titel": "Schach", "thema": "Hobbys"},
    ]})

    daten = _vorschau(client, user_cookies, "Ich lebe in Köln und spiele Schach.").json()

    assert [item["text"] for item in daten["items"]] == ["Der Benutzer spielt Schach."]
    assert daten["total_known"] == 1


def test_ein_teil_ohne_antwort_fehlt_und_wird_gezaehlt(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict, monkeypatch,
) -> None:
    absatz = "Noch ein Satz aus dem Leben. " * 600  # gut 17.000 Zeichen: zwei Teile
    _freischalten(db, regular_user)

    def antwort(inhalt: str):
        if "Teil 2 von" in inhalt:
            return AiProviderRequestError("Anbieter antwortet nicht")
        return {"erinnerungen": [{"text": "Der Benutzer erzählt gern.", "titel": "Erzählen", "thema": "Art"}]}

    _modell(monkeypatch, antwort)
    daten = _vorschau(client, user_cookies, absatz).json()

    assert [item["text"] for item in daten["items"]] == ["Der Benutzer erzählt gern."]
    assert daten["unread_parts"] == 1

    _modell(monkeypatch, AiProviderRequestError("Anbieter antwortet nicht"))
    gescheitert = _vorschau(client, user_cookies, "Kurz.")
    assert gescheitert.status_code == 502


def test_ein_abgebrochener_aufruf_kostet_nichts_und_haelt_den_naechsten_nicht_auf(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict, monkeypatch,
) -> None:
    _freischalten(db, regular_user)

    async def abbruch(*_a, **_k):
        raise RuntimeError("Verbindung weg")

    monkeypatch.setattr(ai_gedaechtnis_schreiber, "_formular", abbruch)

    assert _vorschau(client, user_cookies, "Ich heiße Jonas.").status_code == 502

    # Offen gelassen buchte das Aufräumen beim nächsten Start die volle Schätzung.
    db.expire_all()
    buchung = db.query(AiUsageEvent).filter(AiUsageEvent.user_id == regular_user.id).one()
    assert (buchung.status, buchung.accounted_tokens or 0) == ("failed", 0)
    # Die Sperre „einer je Benutzer“ fällt auch nach einem Fehler.
    assert regular_user.id not in ai_memory_import_service._LAUFENDE
    monkeypatch.undo()
    _modell(monkeypatch, {"erinnerungen": [{"text": "Der Benutzer heißt Jonas."}]})
    assert _vorschau(client, user_cookies, "Ich heiße Jonas.").status_code == 200


def test_ein_zweiter_import_desselben_benutzers_wartet(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict, monkeypatch,
) -> None:
    _freischalten(db, regular_user)
    aufrufe = _modell(monkeypatch, {"erinnerungen": []})
    monkeypatch.setattr(ai_memory_import_service, "_LAUFENDE", {regular_user.id})

    antwort = _vorschau(client, user_cookies, "Ich heiße Jonas.")

    assert antwort.status_code == 429
    assert aufrufe == []


def test_ohne_modell_gibt_es_eine_erklaerung_statt_eines_fehlers(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict, monkeypatch,
) -> None:
    _freischalten(db, regular_user, mit_anbieter=False)
    aufrufe = _modell(monkeypatch, {"erinnerungen": []})

    antwort = _vorschau(client, user_cookies, "Ich heiße Jonas.")

    assert antwort.status_code == 409
    assert "kein KI-Modell" in antwort.json()["detail"]
    assert aufrufe == []


def test_ohne_ki_kontingent_liest_niemand(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict, monkeypatch,
) -> None:
    _freischalten(db, regular_user, daily_token_limit=10)
    aufrufe = _modell(monkeypatch, {"erinnerungen": []})

    antwort = _vorschau(client, user_cookies, "Ich heiße Jonas.")

    assert antwort.status_code == 409
    assert "Kontingent" in antwort.json()["detail"]
    assert aufrufe == []


def test_wer_nicht_schreiben_darf_bekommt_die_absage_vor_dem_anbieter(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict, monkeypatch,
) -> None:
    _freischalten(db, regular_user)
    aufrufe = _modell(monkeypatch, {"erinnerungen": []})

    panel = _vorschau(client, user_cookies, "Name: Jonas", scope="panel")
    fremdes_team = _vorschau(client, user_cookies, "Name: Jonas", scope="team", team_id=999)

    assert panel.status_code == 403
    assert fremdes_team.status_code == 404
    assert aufrufe == []


def test_die_vorschau_sagt_wenn_das_persoenliche_gedaechtnis_aus_ist(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict, monkeypatch,
) -> None:
    _freischalten(db, regular_user)
    ai_memory_service.set_preference(db, regular_user, False)
    _modell(monkeypatch, {"erinnerungen": [{"text": "Der Benutzer heißt Jonas.", "titel": "Name", "thema": "Über mich"}]})

    daten = _vorschau(client, user_cookies, "Ich heiße Jonas.").json()

    assert daten["memory_enabled"] is False


# ── Übernahme ───────────────────────────────────────────────────────────


def test_uebernehmen_ersetzt_fuehrt_zusammen_und_legt_neu_an(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict,
) -> None:
    _freischalten(db, regular_user)
    kaffee = _anlegen(db, regular_user, "Mag Kaffee.")
    morgens = _anlegen(db, regular_user, "Trinkt morgens Kaffee.")
    kaffee_id, morgens_id = kaffee.id, morgens.id

    antwort = _uebernehmen(client, user_cookies, [
        {"text": "Der Benutzer spielt Schach im Verein.", "titel": "Schach", "thema": "Hobbys",
         "art": "vorliebe", "wichtigkeit": 2},
        {"text": "Der Benutzer trinkt morgens gern Kaffee.", "titel": "Kaffee am Morgen",
         "thema": "Vorlieben", "ersetzt": [{"id": kaffee_id, "fassung": 1}, {"id": morgens_id, "fassung": 1}]},
    ], source_provider="Gemini")

    assert antwort.status_code == 200, antwort.text
    assert (antwort.json()["imported_count"], antwort.json()["updated_count"]) == (1, 1)
    # Nicht nur verfallen lassen: der entschlüsselte Titel hängt am Objekt,
    # und das hat die Anfrage nie gesehen.
    db.expunge_all()
    ziel = db.get(AiMemoryEntry, kaffee_id)
    assert (ziel.status, ziel.origin, ziel.fassung, ziel.titel) == ("aktiv", "user", 2, "Kaffee am Morgen")
    assert ai_memory_service._entschluesseln_lesbare([ziel])[0][1] == "Der Benutzer trinkt morgens gern Kaffee."
    assert db.get(AiMemoryEntry, morgens_id).status == "vergessen"
    gruende = {v.grund for v in db.query(AiMemoryVersion).filter(AiMemoryVersion.memory_id == kaffee_id).all()}
    assert gruende == {"bearbeitet", "aufgenommen"}
    (neu,) = db.query(AiMemoryEntry).filter(AiMemoryEntry.quelle == "import").all()
    assert (neu.origin, neu.art, neu.wichtigkeit) == ("user", "vorliebe", 2)
    assert neu.quelle_ref.startswith("import:")
    assert "Schach" not in neu.value_encrypted


def test_eine_veraltete_fassung_wird_nicht_still_ueberschrieben(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict,
) -> None:
    _freischalten(db, regular_user)
    row = _anlegen(db, regular_user, "Mag Kaffee.")
    ai_memory_service.erinnerung_aendern(db, user=regular_user, entry_id=row.id, text="Mag Tee.")

    antwort = _uebernehmen(client, user_cookies, [
        {"text": "Der Benutzer mag Kaffee.", "ersetzt": [{"id": row.id, "fassung": 1}]},
    ]).json()

    assert antwort["skipped"] == [{"index": 0, "reason": "conflict"}]
    db.expire_all()
    assert ai_memory_service._entschluesseln_lesbare([db.get(AiMemoryEntry, row.id)])[0][1] == "Mag Tee."


def test_eine_fremde_erinnerung_wird_nicht_angefasst(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict,
) -> None:
    _freischalten(db, regular_user)
    jonas = _benutzer(db, "jonas")
    _freischalten(db, jonas)
    fremd = _anlegen(db, jonas, "Mag Kaffee.")
    doppelt = _anlegen(db, regular_user, "Spielt Schach.")

    antwort = _uebernehmen(client, user_cookies, [
        {"text": "Der Benutzer mag Tee.", "ersetzt": [{"id": fremd.id, "fassung": 1}]},
        {"text": "Der Benutzer spielt Schach.", "ersetzt": [{"id": doppelt.id, "fassung": 1}]},
        {"text": "Der Benutzer spielt Schach im Verein.", "ersetzt": [{"id": doppelt.id, "fassung": 1}]},
    ]).json()

    assert antwort["skipped"] == [
        {"index": 0, "reason": "rejected"}, {"index": 2, "reason": "duplicate"},
    ]
    db.expire_all()
    assert db.get(AiMemoryEntry, fremd.id).fassung == 1


@pytest.mark.parametrize(
    ("grenze", "vorher", "erwartet_frei", "erwartet_neu"),
    [(2, 1, 1, 1), (None, 1, None, 2)],
    ids=["zahl", "unbegrenzt"],
)
def test_der_import_haelt_das_kontingent_der_rolle(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict, monkeypatch,
    grenze: int | None, vorher: int, erwartet_frei: int | None, erwartet_neu: int,
) -> None:
    _freischalten(db, regular_user, max_memory_entries=grenze)
    for nummer in range(vorher):
        _anlegen(db, regular_user, f"Bestand {nummer}.")
    _modell(monkeypatch, {"erinnerungen": []})

    vorschau = _vorschau(client, user_cookies, "Ich heiße Jonas und wohne in Köln.").json()
    antwort = _uebernehmen(client, user_cookies, [
        {"text": "Der Benutzer heißt Jonas."}, {"text": "Der Benutzer wohnt in Köln."},
    ]).json()

    assert vorschau["available_slots"] == erwartet_frei
    assert antwort["imported_count"] == erwartet_neu
    if grenze is not None:
        assert antwort["skipped"] == [{"index": 1, "reason": "full"}]
    db.expire_all()
    assert db.query(AiMemoryEntry).filter(AiMemoryEntry.status == "aktiv").count() == vorher + erwartet_neu


def test_kontingent_null_sperrt_vorschau_und_uebernahme(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict, monkeypatch,
) -> None:
    _freischalten(db, regular_user)
    row = _anlegen(db, regular_user, "Mag Kaffee.")
    rolle = db.query(Role).filter(Role.name.like(f"import-{regular_user.id}-%")).one()
    werte: dict[str, int | None] = {field: None for field in LIMIT_FIELDS}
    werte["max_memory_entries"] = 0
    set_role_limit(db, rolle.id, werte)
    db.commit()
    aufrufe = _modell(monkeypatch, {"erinnerungen": []})

    vorschau = _vorschau(client, user_cookies, "Ich heiße Jonas.")
    antwort = _uebernehmen(client, user_cookies, [
        {"text": "Der Benutzer heißt Jonas."},
        {"text": "Der Benutzer mag Tee.", "ersetzt": [{"id": row.id, "fassung": 1}]},
    ])

    assert (vorschau.status_code, antwort.status_code) == (409, 409)
    assert aufrufe == []
    db.expire_all()
    assert db.get(AiMemoryEntry, row.id).fassung == 1
    assert db.query(AiMemoryEntry).count() == 1


def test_der_import_braucht_csrf(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict,
) -> None:
    _freischalten(db, regular_user)

    antwort = client.post(
        "/api/ai/memory/import",
        json={"scope": "user", "items": [{"text": "Der Benutzer heißt Jonas."}]},
        cookies=user_cookies,
    )

    assert antwort.status_code == 403
    assert db.query(AiMemoryEntry).count() == 0


def test_die_quelle_steht_in_der_schlusszeile_und_kostet_keine_zeit() -> None:
    quelle = ai_memory_import_service._quelle
    assert quelle("Text\n\n**Importiert aus:** Gemini.") == "Gemini"
    assert quelle("> imported from ChatGPT") == "ChatGPT"
    assert quelle("Importiert aus: A\nImportiert aus: B") == "B"
    assert quelle("Nichts davon.") is None
    assert quelle("Importiert aus: pass" + "word: " + GEHEIMNIS) is None
    # Ein Ausdruck, dessen Leerraum Zeilenumbrüche mitnimmt, kostete hier bei
    # 2.000 leeren Zeilen 15 s — und hielt den ganzen Prozess an.
    start = time.perf_counter()
    for text in ("a" + "\n" * 100_000, "Importiert aus:" + " " * 100_000 + "y", "\n " * 50_000):
        quelle(text)
    assert time.perf_counter() - start < 2


def test_nachsichtig_gelesen_aber_nicht_erraten() -> None:
    lesen = ai_memory_import_service._erinnerungen_lesen
    assert lesen('{"erinnerungen": [{"text": "a"}]}') == [{"text": "a"}]
    assert lesen({"memories": [{"text": "a"}]}) == [{"text": "a"}]
    assert lesen({"text": "a"}) == [{"text": "a"}]
    assert lesen([{"text": "a"}, "kein Objekt"]) == [{"text": "a"}]
    assert lesen({}) == []
    assert lesen({"erinnerungen": None}) == []
    assert lesen({"etwas": "anderes"}) is None
    assert lesen("kein json") is None
