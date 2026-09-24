"""Memory Bridge: Erinnerungen einer fremden KI zerlegen, prüfen, übernehmen.

Drei Zusagen tragen den Import, und jede hat hier ihren Test:

* Die Vorschau schreibt nichts — und ein Geheimnis kommt nicht einmal
  zurück über die Leitung, weder im Wert noch im Schlüssel.
* Übernommen wird über denselben Weg wie ein Eintrag von Hand: verschlüsselt,
  gegen die Bereichsgrenze gezählt, mit Herkunft ``user``.
* Ein belegter Schlüssel wird nie still überschrieben.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import AiMemoryEntry, Role, RolePermission, User
from services import ai_memory_service
from services.ai_limit_service import LIMIT_FIELDS, set_role_limit
from services.ai_memory_import_service import normalize_key, parse_import_text
from services.role_service import set_user_roles
from tests._einbettung import modell_ersetzen

# Zusammengesetzt, nie als Literal: das Repository ist öffentlich, und ein
# Scanner unterscheidet einen Testwert nicht von einem echten.
GEHEIMNIS = "super" + "geheim" + "123"
GEHEIME_ZEILE = "Das Passwort ist pass" + "word: " + GEHEIMNIS

ANTWORT = f"""### 1. Demografische Informationen
* Der Name der Person ist Alex.
    * Beleg: Die Person hat gesagt: „Nenn mich Alex“. Datum: [03.02.2026].
* Die Person wohnt in Berlin.

**2. Interessen und Vorlieben**
* Die Person spielt regelmäßig ARK und Minecraft.

3. Soziales Umfeld
* Die Person hat eine Schwester namens Mia.

4. Termine, Projekte und Pläne mit Datum
* Die Person baut seit März 2026 ein Server-Panel.

5. Anweisungen
* Antworte immer auf Deutsch.
* {GEHEIME_ZEILE}

Importiert aus: Gemini
"""


def _freischalten(db: Session, user: User, **limits: int | None) -> None:
    role = Role(name=f"import-{user.id}", is_system=False)
    db.add(role)
    db.flush()
    db.add(RolePermission(role_id=role.id, permission_key="ai.memory.use"))
    werte: dict[str, int | None] = {field: None for field in LIMIT_FIELDS}
    werte.update(limits)
    set_role_limit(db, role.id, werte)
    db.commit()
    set_user_roles(db, user, [role.id])


def _csrf(cookies: dict) -> dict[str, str]:
    return {"X-CSRF-Token": cookies.get("__Secure-csrf_token", "")}


def _themen(texte: list[str]) -> list[list[float]]:
    """Ein Vektor je Thema — gleich, wenn der Wert dasselbe Thema trägt.

    Verglichen wird nur der Wert hinter dem Schlüssel: so hängt die Ähnlichkeit
    am Inhalt und nicht daran, wie der Import den Schlüssel benennt.
    """
    themen = ("deutsch", "berlin", "minecraft")
    vektoren: list[list[float]] = []
    for nummer, text in enumerate(texte):
        wert = text.split(": ", 1)[-1].lower()
        vektor = [0.0] * 256
        treffer = next((i for i, thema in enumerate(themen) if thema in wert), None)
        vektor[treffer if treffer is not None else 10 + (hash(wert) % 200)] = 1.0
        vektoren.append(vektor)
        del nummer
    return vektoren


# ---------------------------------------------------------------------------
# Zerlegung
# ---------------------------------------------------------------------------


def test_the_bridge_format_yields_categories_keys_evidence_and_source() -> None:
    zerlegt = parse_import_text(ANTWORT)

    assert zerlegt.quelle == "Gemini"
    kategorien = [fakt.kategorie for fakt in zerlegt.fakten]
    assert kategorien == [
        "demografie", "demografie", "vorlieben", "soziales", "projekte",
        "anweisung", "anweisung",
    ]
    name = zerlegt.fakten[0]
    assert normalize_key(name.text, name.schluessel) == "name"
    assert name.beleg is not None and "Nenn mich Alex" in name.beleg
    wohnort = zerlegt.fakten[1]
    assert normalize_key(wohnort.text) == "wohnort"
    # Die Quellenzeile ist kein Fakt.
    assert all("Importiert" not in fakt.text for fakt in zerlegt.fakten)


def test_a_numbered_fact_starting_like_a_heading_stays_a_fact() -> None:
    """"2. Interessiert sich für Rust" ist ein Listenpunkt, keine Überschrift."""
    zerlegt = parse_import_text(
        "1. Spricht Deutsch\n2. Interessiert sich für Rust\n3. Plans a trip to Japan in October\n"
    )

    assert [fakt.text for fakt in zerlegt.fakten] == [
        "Spricht Deutsch", "Interessiert sich für Rust", "Plans a trip to Japan in October",
    ]
    assert {fakt.kategorie for fakt in zerlegt.fakten} == {"allgemein"}


def test_a_chatgpt_memory_dump_becomes_one_fact_per_line() -> None:
    zerlegt = parse_import_text("Loves Ark and Minecraft\nSpeaks German\n")

    assert [fakt.text for fakt in zerlegt.fakten] == ["Loves Ark and Minecraft", "Speaks German"]
    assert normalize_key("Loves Ark and Minecraft") == "loves_ark_minecraft"


def test_key_value_lines_keep_their_own_key() -> None:
    zerlegt = parse_import_text("Name: Alex\nWohnort: Berlin\nLieblingsspiel: ARK\n")

    paare = [(normalize_key(f.text, f.schluessel), f.text) for f in zerlegt.fakten]
    assert paare == [("name", "Alex"), ("wohnort", "Berlin"), ("lieblingsspiel", "ARK")]


@pytest.mark.parametrize(
    "text",
    [
        '[{"key": "Sprache", "value": "Antworte auf Deutsch"}]',
        '{"memories": [{"key": "Sprache", "value": "Antworte auf Deutsch"}]}',
        '```json\n{"Sprache": "Antworte auf Deutsch"}\n```',
    ],
)
def test_json_exports_are_read_in_every_common_shape(text: str) -> None:
    zerlegt = parse_import_text(text)

    assert len(zerlegt.fakten) == 1
    fakt = zerlegt.fakten[0]
    assert normalize_key(fakt.text, fakt.schluessel) == "sprache"
    assert fakt.text == "Antworte auf Deutsch"


def test_generated_keys_always_fit_the_key_pattern() -> None:
    import re

    lang = "Überaus " + "sehrlangeswortohneende" * 10
    schluessel = normalize_key(lang)
    assert re.fullmatch(r"[A-Za-z0-9_.-]+", schluessel)
    assert len(schluessel) <= 64
    assert normalize_key("!!!") == "eintrag"


# ---------------------------------------------------------------------------
# Vorschau
# ---------------------------------------------------------------------------


def test_preview_writes_nothing_and_never_echoes_a_secret(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict,
) -> None:
    _freischalten(db, regular_user)

    antwort = client.post(
        "/api/ai/memory/import/preview",
        json={"raw_text": ANTWORT, "scope": "user"},
        cookies=user_cookies, headers=_csrf(user_cookies),
    )

    assert antwort.status_code == 200, antwort.text
    daten = antwort.json()
    assert daten["detected_source"] == "Gemini"
    assert daten["total_detected"] == 7
    assert daten["total_secrets_blocked"] == 1
    assert daten["available_slots"] == 100
    gesperrt = [item for item in daten["items"] if item["status"] == "has_secret"]
    assert len(gesperrt) == 1
    # Weder im Wert noch im Schlüssel — der Schlüssel entsteht aus den ersten
    # Wörtern des Fakts und damit sonst aus dem Geheimnis selbst.
    assert GEHEIMNIS not in antwort.text
    assert db.query(AiMemoryEntry).count() == 0


def test_preview_marks_an_existing_key_and_a_similar_entry(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    modell_ersetzen(monkeypatch, _themen)
    _freischalten(db, regular_user)
    ai_memory_service.upsert_entry(
        db, user=regular_user, scope="user", server_id=None,
        key="name", value="Heißt Alexander",
    )
    ai_memory_service.upsert_entry(
        db, user=regular_user, scope="user", server_id=None,
        key="sprach_praeferenz", value="Standard-Sprache: Deutsch",
    )

    daten = client.post(
        "/api/ai/memory/import/preview",
        json={"raw_text": ANTWORT, "scope": "user"},
        cookies=user_cookies, headers=_csrf(user_cookies),
    ).json()

    nach_schluessel = {item["key"]: item for item in daten["items"]}
    name = nach_schluessel["name"]
    assert name["status"] == "exact_duplicate"
    assert name["existing_value"] == "Heißt Alexander"
    sprache = next(item for item in daten["items"] if "Deutsch" in item["value"])
    assert sprache["status"] == "similar_existing"
    assert sprache["existing_key"] == "sprach_praeferenz"
    assert sprache["existing_value"] == "Standard-Sprache: Deutsch"
    assert sprache["similarity"] >= ai_memory_service.IMPORT_HINWEIS_AB
    assert daten["total_conflicts"] == 2
    assert daten["available_slots"] == 98


def test_preview_hints_below_the_merge_threshold_but_not_at_unrelated_facts(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Die Vorschau zeigt schon ab `IMPORT_HINWEIS_AB`, nicht erst ab `DUPLIKAT_AB`.

    Gemessen: "Antworte auf Deutsch." zu "Antworte immer auf Deutsch." liegt
    beim echten Modell bei 0,61 — unter der Duplikatschwelle. Mit ihr bliebe
    genau das typische Doppel eines Imports unbemerkt.
    """
    import math

    def rechner(texte: list[str]) -> list[list[float]]:
        vektoren = []
        for text in texte:
            anteil = 1.0 if "Bestand" in text else 0.6 if "Nah" in text else 0.3
            vektor = [0.0] * 256
            vektor[0], vektor[1] = anteil, math.sqrt(1 - anteil * anteil)
            vektoren.append(vektor)
        return vektoren

    modell_ersetzen(monkeypatch, rechner)
    _freischalten(db, regular_user)
    ai_memory_service.upsert_entry(
        db, user=regular_user, scope="user", server_id=None, key="alt", value="Bestand",
    )

    daten = client.post(
        "/api/ai/memory/import/preview",
        json={"raw_text": "Nah: Nah dran\nFern: Weit weg\n", "scope": "user"},
        cookies=user_cookies, headers=_csrf(user_cookies),
    ).json()

    status = {item["key"]: item["status"] for item in daten["items"]}
    assert 0.6 < ai_memory_service.DUPLIKAT_AB
    assert status == {"nah": "similar_existing", "fern": "new"}


def test_preview_says_when_the_personal_memory_is_switched_off(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict,
) -> None:
    _freischalten(db, regular_user)
    ai_memory_service.set_preference(db, regular_user, False)

    daten = client.post(
        "/api/ai/memory/import/preview",
        json={"raw_text": "Name: Alex", "scope": "user"},
        cookies=user_cookies, headers=_csrf(user_cookies),
    ).json()

    assert daten["memory_enabled"] is False


def test_preview_refuses_a_scope_the_user_may_not_write(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict,
) -> None:
    """Die Absage kommt in der Vorschau, nicht erst nach dem Aussuchen."""
    _freischalten(db, regular_user)

    panel = client.post(
        "/api/ai/memory/import/preview",
        json={"raw_text": "Name: Alex", "scope": "panel"},
        cookies=user_cookies, headers=_csrf(user_cookies),
    )
    fremdes_team = client.post(
        "/api/ai/memory/import/preview",
        json={"raw_text": "Name: Alex", "scope": "team", "team_id": 999},
        cookies=user_cookies, headers=_csrf(user_cookies),
    )

    assert panel.status_code == 403
    assert fremdes_team.status_code == 404


# ---------------------------------------------------------------------------
# Übernahme
# ---------------------------------------------------------------------------


def test_import_writes_encrypted_user_entries_and_reports_each_skip(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict,
) -> None:
    _freischalten(db, regular_user)
    ai_memory_service.upsert_entry(
        db, user=regular_user, scope="user", server_id=None,
        key="demografie.name", value="Heißt Alexander", origin="ai",
    )
    ai_memory_service.upsert_entry(
        db, user=regular_user, scope="user", server_id=None,
        key="demografie.wohnort", value="Wohnt in Hamburg",
    )

    antwort = client.post(
        "/api/ai/memory/import",
        json={
            "scope": "user",
            "source_provider": "Gemini",
            "items": [
                {"key": "vorlieben.spiele", "value": "Spielt ARK und Minecraft"},
                {"key": "demografie.name", "value": "Heißt Alex", "replace_existing": True},
                {"key": "demografie.wohnort", "value": "Wohnt in Berlin"},
                {"key": "anweisung.geheim", "value": GEHEIME_ZEILE},
                {"key": "vorlieben.spiele", "value": "Doppelt"},
            ],
        },
        cookies=user_cookies, headers=_csrf(user_cookies),
    )

    assert antwort.status_code == 200, antwort.text
    daten = antwort.json()
    assert daten["imported_count"] == 1
    assert daten["updated_count"] == 1
    assert daten["skipped_count"] == 3
    assert {(s["key"], s["reason"]) for s in daten["skipped"]} == {
        ("demografie.wohnort", "exists"),
        ("anweisung.geheim", "rejected"),
        ("vorlieben.spiele", "duplicate"),
    }

    werte = dict(
        (row.key, (value, row.origin))
        for row, value in ai_memory_service.list_entries(db, regular_user, "user", None)
    )
    assert werte["vorlieben.spiele"] == ("Spielt ARK und Minecraft", "user")
    # Bestätigt heißt: aus einer Ableitung der KI wird eine eigene Ansage.
    assert werte["demografie.name"] == ("Heißt Alex", "user")
    assert werte["demografie.wohnort"][0] == "Wohnt in Hamburg"
    assert "anweisung.geheim" not in werte
    neu = db.query(AiMemoryEntry).filter(AiMemoryEntry.key == "vorlieben.spiele").one()
    assert "ARK" not in neu.value_encrypted


def test_import_stops_at_the_scope_limit_without_losing_what_fit(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict,
) -> None:
    _freischalten(db, regular_user, max_memory_entries=2)
    ai_memory_service.upsert_entry(
        db, user=regular_user, scope="user", server_id=None, key="alt", value="Bestand",
    )

    vorschau = client.post(
        "/api/ai/memory/import/preview",
        json={"raw_text": "Name: Alex\nWohnort: Berlin\n", "scope": "user"},
        cookies=user_cookies, headers=_csrf(user_cookies),
    ).json()
    antwort = client.post(
        "/api/ai/memory/import",
        json={"scope": "user", "items": [
            {"key": "name", "value": "Alex"},
            {"key": "wohnort", "value": "Berlin"},
        ]},
        cookies=user_cookies, headers=_csrf(user_cookies),
    ).json()

    assert vorschau["available_slots"] == 1
    assert antwort["imported_count"] == 1
    assert antwort["skipped"] == [{"key": "wohnort", "reason": "full"}]
    assert db.query(AiMemoryEntry).count() == 2


def test_import_requires_csrf(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict,
) -> None:
    _freischalten(db, regular_user)

    antwort = client.post(
        "/api/ai/memory/import",
        json={"scope": "user", "items": [{"key": "name", "value": "Alex"}]},
        cookies=user_cookies,
    )

    assert antwort.status_code == 403
    assert db.query(AiMemoryEntry).count() == 0
