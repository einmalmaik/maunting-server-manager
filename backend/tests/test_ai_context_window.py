"""Das Kontextfenster kommt aus dem Katalog — und darf nie zu gross geraten.

Vorher galten fuer jedes Modell dieselben 24.000 Zeichen. Gegenueber einem
Fenster von einer Million Token war das ein halbes Prozent; gegenueber einem
Modell mit 4.096 Token waere es das Sechsfache. Beide Richtungen sind hier
festgehalten, denn nur eine davon faellt im Betrieb auf: ein zu kleines Fenster
laesst den Chat still frueher vergessen, ein zu grosses erzeugt eine Absage des
Anbieters mitten im Gespraech.

Die Zahlen in den Beispielen stammen aus dem OpenRouter-Katalog vom 2026-08-11.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import AiProvider, Role, RolePermission, User
from services import ai_context_service, ai_context_window
from services.ai_provider_registry import Modell
from services.panel_settings_service import PanelSettingsService
from services.role_service import set_user_roles


def _modell(kontext: int | None, ausgabe: int | None = None) -> Modell:
    return Modell(
        model_id="test/modell", name="Test", denkt=False,
        kontext_tokens=kontext, max_ausgabe_tokens=ausgabe,
    )


@pytest.fixture(autouse=True)
def _saubere_einstellung():
    PanelSettingsService.invalidate_cache()
    yield
    PanelSettingsService.invalidate_cache()


def test_a_known_window_is_used_almost_entirely() -> None:
    """Der Sinn der Uebung: ein grosses Fenster wird auch gefuellt."""
    fenster = ai_context_window.aus_modell(_modell(128_000, 16_384))

    assert fenster.bekannt is True
    assert fenster.fenster_tokens == 128_000
    # Ausgabegrenze ab, dann der Sicherheitsabschlag — deutlich mehr als die
    # 6.000 Token, die vorher fuer jedes Modell galten.
    assert fenster.nutzbar_tokens == int((128_000 - 16_384) * ai_context_window.SICHERHEIT)
    assert fenster.zeichen == fenster.nutzbar_tokens * ai_context_window.ZEICHEN_JE_TOKEN


def test_a_generous_output_limit_never_eats_more_than_a_quarter() -> None:
    """Die Reserve ist eine Reserve, keine zweite Obergrenze.

    Ein Modell darf mehr Ausgabe erlauben, als es fuer die Eingabe uebrig
    laesst — 128k Fenster bei 32.768 Ausgabetokens ist ein Alltagsfall. Voll
    abgezogen schnitte das ein Viertel des Kontexts weg, das in der Praxis
    niemand fuer eine Antwort braucht.
    """
    fenster = ai_context_window.aus_modell(_modell(128_000, 32_768))

    assert fenster.nutzbar_tokens == int(
        (128_000 - 128_000 // 4) * ai_context_window.SICHERHEIT
    )


def test_a_million_token_window_reaches_the_history() -> None:
    fenster = ai_context_window.aus_modell(_modell(1_000_000, 65_536))
    grenzen = ai_context_service.teilbudgets(fenster.zeichen)

    assert fenster.nutzbar_tokens > 800_000
    # Der frueher feste Deckel von 20 Nachrichten war bei einem solchen Fenster
    # die eigentliche Ursache des Vergessens — das Zeichenbudget kam nie zum
    # Zug.
    assert grenzen.historie_zeilen == 2_000


def test_a_model_that_reserves_its_whole_window_for_output_still_works() -> None:
    """``context_length == max_completion_tokens`` kommt im Katalog wirklich vor.

    Nemotron 3.5 Lightning meldet 262144 zu 262144. Ohne die Klemmung der
    Reserve auf ein Viertel bliebe fuer die Eingabe nichts — ein Modell mit
    einem Viertelmillionen-Fenster faende sich beim Rueckfallwert wieder.
    """
    fenster = ai_context_window.aus_modell(_modell(262_144, 262_144))

    assert fenster.bekannt is True
    assert fenster.nutzbar_tokens > 150_000


def test_a_genuinely_small_window_is_respected_not_raised() -> None:
    """Der Rueckfall gilt fuer Unwissen, nicht fuer ein kleines Modell.

    Die Versuchung waere, nie unter die frueheren 6.000 Token zu gehen. Gegen
    ein Modell mit 4.096 waere das schlicht falsch: die Anfrage liefe dann nicht
    knapper, sondern gar nicht.
    """
    fenster = ai_context_window.aus_modell(_modell(4_096))

    assert fenster.nutzbar_tokens < ai_context_window.RUECKFALL_NUTZBAR_TOKENS
    grenzen = ai_context_service.teilbudgets(fenster.zeichen)
    # Und der Deckel für Werkzeugdaten (16.000 Zeichen) darf hier nicht den
    # halben Kontext beanspruchen.
    assert grenzen.werkzeug_zeichen <= grenzen.gesamt // 2


@pytest.mark.parametrize("modell", [None, _modell(None), _modell(0)])
def test_without_catalog_knowledge_only_the_tool_reflux_is_narrower(modell) -> None:
    """Katalog nicht erreichbar, Modell unbekannt, Auto Router ohne Fenster.

    Alle drei landen im selben Zustand: 6.000 Token, 24.000 Zeichen, und bis
    auf eine Ausnahme dieselben Teilbudgets wie vor der Fensterberechnung.

    Die Ausnahme ist der Rückfluss der Werkzeugergebnisse. Seit sein Deckel bei
    16.000 steht, bindet hier nicht mehr er, sondern der Anteil `gesamt // 2` —
    also 12.000. Beim Anbieter ändert das die Menge nicht: die Kürzungsgrenze im
    Lauf ist `max(24.000 - Werkzeugkatalog, 4.000)` und liegt gemessen zwischen
    4.000 und 16.036 Zeichen, der Block wird dort also ohnehin geschnitten.
    """
    fenster = ai_context_window.aus_modell(modell)

    assert fenster.bekannt is False
    assert fenster.zeichen == ai_context_service.MAX_CONTEXT_CHARS
    grenzen = ai_context_service.teilbudgets(None)
    assert grenzen.werkzeug_zeichen == grenzen.gesamt // 2
    assert grenzen.werkzeug_zeichen < ai_context_service.MAX_TOOL_RESULT_CONTEXT_CHARS
    assert grenzen.werkzeug_anzahl == ai_context_service.MAX_TOOL_RESULTS
    assert grenzen.zusammenfassung_zeichen == ai_context_service.MAX_SUMMARY_CHARS


def test_the_compaction_mark_defaults_to_seventy_five_percent() -> None:
    assert ai_context_window.schwelle_prozent() == ai_context_window.STANDARD_SCHWELLE


def test_the_compaction_mark_can_be_set_within_its_bounds() -> None:
    ai_context_window.set_schwelle_prozent(60)
    assert ai_context_window.schwelle_prozent() == 60
    assert ai_context_window.faltmarke_zeichen_aus_budget(100_000) == 60_000


@pytest.mark.parametrize("wert", [0, 49, 96, 200, True])
def test_a_mark_outside_the_bounds_is_refused(wert) -> None:
    """Unter 50 % faltet der Chat staendig, ueber 95 % bleibt kein Platz."""
    with pytest.raises(ValueError):
        ai_context_window.set_schwelle_prozent(wert)


def test_an_unreadable_setting_falls_back_to_the_default() -> None:
    """Die Marke wird am Ende jedes Streams gelesen und darf dort nichts umwerfen."""
    PanelSettingsService.set(ai_context_window.SETTINGS_KEY, "sehr voll")

    assert ai_context_window.schwelle_prozent() == ai_context_window.STANDARD_SCHWELLE


def test_a_stored_value_outside_the_bounds_is_ignored() -> None:
    """Auch von Hand in die Datenbank geschrieben gelten die Grenzen."""
    PanelSettingsService.set(ai_context_window.SETTINGS_KEY, "999")

    assert ai_context_window.schwelle_prozent() == ai_context_window.STANDARD_SCHWELLE


# ── Ueber die Schnittstelle ───────────────────────────────────────────


def _csrf(cookies: dict) -> dict[str, str]:
    return {"X-CSRF-Token": cookies.get("__Secure-csrf_token", "")}


def _chat_erlauben(db: Session, user: User) -> None:
    role = Role(name=f"kontext-{user.id}", is_system=False)
    db.add(role)
    db.flush()
    db.add(RolePermission(role_id=role.id, permission_key="ai.chat.use"))
    db.commit()
    set_user_roles(db, user, [role.id])


def test_the_meter_reports_a_fallback_when_the_catalog_is_silent(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict
) -> None:
    """Kein Katalog, kein Prozentwert — aber auch kein Fehler.

    In der Testumgebung ist der Anbieter nicht erreichbar. Genau dieser Fall
    entscheidet, ob der Ring eine Zahl erfindet oder ehrlich sagt, dass er das
    Fenster nicht kennt.
    """
    _chat_erlauben(db, regular_user)
    provider = AiProvider(
        name="Ring", provider_kind="openrouter", default_model="model-a",
        enabled=True, requires_api_key=False,
    )
    db.add(provider)
    db.commit()
    db.refresh(provider)

    antwort = client.get(
        f"/api/ai/conversation/context?provider_id={provider.id}", cookies=user_cookies
    )

    assert antwort.status_code == 200
    daten = antwort.json()
    assert daten["known"] is False
    assert daten["window_tokens"] is None
    assert daten["usable_tokens"] == ai_context_window.RUECKFALL_NUTZBAR_TOKENS
    # Der Systemprompt allein belegt schon etwas — eine Null waere hier falsch.
    assert daten["used_tokens"] > 0
    assert daten["compaction_percent"] == ai_context_window.STANDARD_SCHWELLE


def test_the_meter_needs_a_provider_that_exists(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict
) -> None:
    _chat_erlauben(db, regular_user)

    antwort = client.get(
        "/api/ai/conversation/context?provider_id=999999", cookies=user_cookies
    )

    assert antwort.status_code == 404


def test_the_operator_can_move_the_mark_over_the_api(
    client: TestClient, owner_cookies: dict
) -> None:
    gelesen = client.get("/api/ai/settings/context", cookies=owner_cookies)
    assert gelesen.status_code == 200
    assert gelesen.json()["compaction_percent"] == ai_context_window.STANDARD_SCHWELLE

    gesetzt = client.put(
        "/api/ai/settings/context",
        json={"compaction_percent": 60},
        cookies=owner_cookies,
        headers=_csrf(owner_cookies),
    )

    assert gesetzt.status_code == 200
    assert gesetzt.json()["compaction_percent"] == 60
    assert ai_context_window.schwelle_prozent() == 60


def test_an_impossible_mark_is_refused_by_the_api(
    client: TestClient, owner_cookies: dict
) -> None:
    """Ueber 95 % bliebe kein Platz fuer die Antwort und die ausloesende Frage."""
    antwort = client.put(
        "/api/ai/settings/context",
        json={"compaction_percent": 99},
        cookies=owner_cookies,
        headers=_csrf(owner_cookies),
    )

    assert antwort.status_code == 422
    assert ai_context_window.schwelle_prozent() == ai_context_window.STANDARD_SCHWELLE


def test_an_ordinary_user_cannot_move_the_mark(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict
) -> None:
    """Die Marke gilt panelweit — sie gehoert dem Betreiber."""
    _chat_erlauben(db, regular_user)

    antwort = client.put(
        "/api/ai/settings/context",
        json={"compaction_percent": 60},
        cookies=user_cookies,
        headers=_csrf(user_cookies),
    )

    assert antwort.status_code == 403
    assert ai_context_window.schwelle_prozent() == ai_context_window.STANDARD_SCHWELLE
