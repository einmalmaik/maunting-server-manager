"""Negativproben zum Limits-Umbau, zum Anfragedeckel und zum gekürzten Katalog.

Entstanden im Review vom 07.10.2026. Jede Probe stellt eine Frage, die der
Umbau beantworten muss, ohne dass es ein anderer Test schon tut: Was passiert
an der Grenze, bei zwei gleichzeitigen Sitzungen, bei einem kaputten
Katalogwert — und kostet ein abgewiesener Benutzer den Betreiber trotzdem Geld?
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from models import AiProvider, AiUsageEvent, Role, User
from services import ai_action_service, ai_stt, ai_usage_service
from services.ai_context_window import ANFRAGE_DECKEL_TOKENS, aus_modell
from services.ai_limit_service import LIMIT_FIELDS, set_role_limit
from services.ai_provider_registry import Modell
from services.ai_usage_service import (
    AiQuotaExceeded,
    realtime_restsekunden,
    realtime_verbrauch_ergaenzen,
    reserve_ai_usage,
)
from services.ai_voice import transcription
from services.role_service import set_user_roles


def _rolle(db: Session, user: User, name: str, **limits: int | None) -> None:
    werte: dict[str, int | None] = {feld: None for feld in LIMIT_FIELDS}
    werte.update(limits)
    rolle = Role(name=name, description=None, is_system=False)
    db.add(rolle)
    db.commit()
    set_role_limit(db, rolle.id, werte)
    db.commit()
    set_user_roles(db, user, [rolle.id])


def _sprachsitzung(db: Session, user: User, *, vor_sekunden: int) -> AiUsageEvent:
    event = reserve_ai_usage(
        db, user, request_id=uuid4(), estimated_tokens=0,
        minimum_token_headroom=1, realtime=True,
    )
    event.created_at = datetime.now(timezone.utc) - timedelta(seconds=vor_sekunden)
    db.commit()
    return event


# ── Sprachminuten ──────────────────────────────────────────────────────


def test_ein_zweiter_tab_bekommt_nur_den_rest(
    db: Session, regular_user: User,
) -> None:
    """Eine offene Sitzung zählt nach der Wanduhr, nicht erst ab ihrer nächsten Buchung.

    Sonst bekäme ein zweiter Tab beim Start den vollen Rest als Höchstdauer,
    und zwei Tabs redeten zusammen doppelt so lange, wie die Rolle erlaubt.
    """
    from services.ai_voice.realtime_session import hoechstdauer

    _rolle(db, regular_user, "ki-zweiter-tab", monthly_realtime_minutes_limit=1)
    _sprachsitzung(db, regular_user, vor_sekunden=45)
    assert realtime_restsekunden(db, regular_user) <= 15
    assert hoechstdauer(db, regular_user) <= 15


def test_zwei_gleichzeitige_sitzungen_werden_an_der_grenze_gestoppt(
    db: Session, regular_user: User,
) -> None:
    """Zwei Tabs, eine Minute, je 40 Sekunden offen: die nächste Buchung stoppt.

    Gelaufene Zeit wird trotzdem gebucht; danach ist der Monat zu.
    """
    _rolle(db, regular_user, "ki-zwei-tabs", monthly_realtime_minutes_limit=1)
    a = _sprachsitzung(db, regular_user, vor_sekunden=40)
    b = _sprachsitzung(db, regular_user, vor_sekunden=40)
    werte = dict(text_input=1, text_output=1, audio_input=1, audio_output=1, cost_microunits=0)

    for sitzung in (a, b):
        with pytest.raises(AiQuotaExceeded, match="monthly_realtime_minutes_limit"):
            realtime_verbrauch_ergaenzen(db, event_id=sitzung.id, **werte)
        db.rollback()
        realtime_verbrauch_ergaenzen(db, event_id=sitzung.id, grenzen_pruefen=False, **werte)
        db.commit()
    assert realtime_restsekunden(db, regular_user) == 0
    with pytest.raises(AiQuotaExceeded, match="monthly_realtime_minutes_limit"):
        _sprachsitzung(db, regular_user, vor_sekunden=0)


def test_eine_sitzung_aus_dem_vormonat_belastet_den_neuen_nicht(
    db: Session, regular_user: User,
) -> None:
    """Ihre Zeit gehört zum Monat, in dem sie begann — und wird nicht doppelt abgezogen."""
    _rolle(db, regular_user, "ki-monatswechsel", monthly_realtime_minutes_limit=1)
    alt = _sprachsitzung(db, regular_user, vor_sekunden=0)
    jetzt = datetime.now(timezone.utc)
    alt.created_at = jetzt.replace(day=1, hour=0, minute=0, second=0, microsecond=0) - timedelta(seconds=30)
    db.commit()
    werte = dict(text_input=1, text_output=1, audio_input=1, audio_output=1, cost_microunits=0)

    realtime_verbrauch_ergaenzen(db, event_id=alt.id, **werte)
    db.commit()
    assert realtime_restsekunden(db, regular_user) == 60


def test_eine_verwaiste_sprachsitzung_wird_mit_ihrer_laufzeit_gebucht(
    db: Session, regular_user: User,
) -> None:
    """Stirbt der Prozess, ist die Zeit seit der letzten Buchung trotzdem gelaufen."""
    from services.ai_usage_service import verwaiste_reservierungen_abgleichen

    _rolle(db, regular_user, "ki-verwaist", monthly_realtime_minutes_limit=10)
    sitzung = _sprachsitzung(db, regular_user, vor_sekunden=90)

    verwaiste_reservierungen_abgleichen(db)
    db.refresh(sitzung)

    assert sitzung.status == "completed"
    assert sitzung.realtime_seconds >= 90


def test_negative_sprachwerte_werden_nicht_gutgeschrieben(
    db: Session, regular_user: User,
) -> None:
    """Ein negativer Wert dürfte das Konto sonst wieder auffüllen."""
    _rolle(db, regular_user, "ki-negativ", monthly_realtime_minutes_limit=1)
    a = _sprachsitzung(db, regular_user, vor_sekunden=5)
    with pytest.raises(ValueError):
        realtime_verbrauch_ergaenzen(
            db, event_id=a.id, text_input=-500, text_output=0,
            audio_input=0, audio_output=0, cost_microunits=0,
        )
    with pytest.raises(ValueError):
        reserve_ai_usage(db, regular_user, request_id=uuid4(), estimated_tokens=1, dictation_seconds=-60)


# ── Diktat ─────────────────────────────────────────────────────────────


def _hoerender_zugang(db: Session) -> AiProvider:
    zugang = AiProvider(
        name="Diktat-Probe",
        provider_kind="openrouter",
        default_model="openai/gpt-5.6-luna",
        transcription_model="openai/gpt-transcribe",
        enabled=True,
        requires_api_key=False,
    )
    db.add(zugang)
    db.commit()
    return zugang


def _zaehlendes_gehoer(monkeypatch) -> list[int]:
    aufrufe: list[int] = []

    async def hoeren(_client, *, provider, api_key, pcm, usage=None, **_rest):
        aufrufe.append(len(pcm))
        if usage is not None:
            usage.total_tokens = 50
            usage.vom_anbieter = True
        return "Starte den Server neu"

    monkeypatch.setattr(ai_stt, "hoeren", hoeren)
    return aufrufe


async def _diktieren(user: User, zugang: AiProvider, sekunden: int = 2):
    from services.ai_provider_service import resolve_api_key

    return await transcription.hoeren(
        client=None,
        user_id=user.id,
        provider_id=zugang.id,
        pcm=b"\x00\x00" * (ai_stt.ABTASTRATE * sekunden),
        resolve_api_key=resolve_api_key,
    )


@pytest.mark.asyncio
async def test_ohne_diktatminuten_wird_der_anbieter_nicht_bezahlt(
    db: Session, regular_user: User, monkeypatch,
) -> None:
    """0 Minuten heisst: keine Abschrift — und auch keine Rechnung dafür.

    Früher lief die Abschrift erst, und die Buchung lehnte danach ab: der
    Benutzer bekam 429, der Betreiber die Rechnung des Anbieters, beliebig oft.
    """
    zugang = _hoerender_zugang(db)
    aufrufe = _zaehlendes_gehoer(monkeypatch)
    _rolle(db, regular_user, "ki-ohne-diktat", monthly_dictation_minutes_limit=0)

    ergebnis = await _diktieren(regular_user, zugang)

    assert ergebnis.grund == "kontingent"
    assert aufrufe == []


@pytest.mark.asyncio
async def test_aufgebrauchte_diktatminuten_fragen_den_anbieter_nicht_mehr(
    db: Session, regular_user: User, monkeypatch,
) -> None:
    zugang = _hoerender_zugang(db)
    aufrufe = _zaehlendes_gehoer(monkeypatch)
    _rolle(db, regular_user, "ki-eine-diktatminute", monthly_dictation_minutes_limit=1)

    # Erst gelingt es; die Kontrolle, dass die Attrappe greift.
    assert (await _diktieren(regular_user, zugang, sekunden=60)).grund == "ok"
    assert len(aufrufe) == 1
    assert (await _diktieren(regular_user, zugang)).grund == "kontingent"
    assert len(aufrufe) == 1


# ── Anfragedeckel ──────────────────────────────────────────────────────


def _modell(kontext: int | None, ausgabe: int | None = None) -> Modell:
    return Modell(
        model_id="probe/modell", name="Probe", denkt=False,
        kontext_tokens=kontext, max_ausgabe_tokens=ausgabe,
    )


@pytest.mark.parametrize(
    "kontext, ausgabe",
    [(1_000_000, None), (1_000_000, 1_000_000), (2_000_000, 128_000), (60_000, 8_192)],
)
def test_kein_fenster_hebt_den_deckel(kontext: int, ausgabe: int | None) -> None:
    fenster = aus_modell(_modell(kontext, ausgabe))
    assert 0 < fenster.nutzbar_tokens <= ANFRAGE_DECKEL_TOKENS


@pytest.mark.parametrize("kontext", [4_096, 8_192, 16_384, 32_768])
def test_ein_kleines_fenster_bleibt_unter_sich_selbst(kontext: int) -> None:
    """Der Deckel ist eine Obergrenze, nie eine Anhebung."""
    fenster = aus_modell(_modell(kontext, kontext))
    assert fenster.nutzbar_tokens < kontext
    assert fenster.nutzbar_tokens >= kontext // 2


@pytest.mark.parametrize("kontext", [0, -5, None])
def test_ein_kaputter_katalogwert_gilt_als_unbekannt(kontext: int | None) -> None:
    fenster = aus_modell(_modell(kontext))
    assert fenster.bekannt is False
    assert fenster.nutzbar_tokens > 1_000


# ── Faltung unter dem Deckel ───────────────────────────────────────────


def _gespraech(db: Session, user: User, *, nachrichten: int, zeichen: int):
    from uuid import uuid4 as neu

    from models import AiConversation, AiMessage

    gespraech = AiConversation(id=str(neu()), user_id=user.id, server_id=None, title="Lang")
    db.add(gespraech)
    db.flush()
    beginn = datetime.now(timezone.utc) - timedelta(hours=nachrichten)
    for index in range(nachrichten):
        db.add(AiMessage(
            id=str(neu()), conversation_id=gespraech.id,
            role="user" if index % 2 == 0 else "assistant",
            content=f"Nachricht {index} " + "x" * zeichen,
            status="complete", created_at=beginn + timedelta(minutes=index),
        ))
    db.commit()
    return gespraech


def test_eine_volle_anfrage_faltet_bevor_der_verlauf_abgeschnitten_wird(
    db: Session, regular_user: User,
) -> None:
    """Unter dem Deckel belegen Prompt und Katalog fast die Hälfte.

    Rund 84.000 Zeichen faltbarer Verlauf liegen unter der Marke von 132.000 —
    nach dem Verlauf allein wird nicht gefaltet. Die Anfrage war aber voll,
    und ohne Faltung fielen die ältesten Zeilen still heraus.
    """
    from services import ai_compaction_service

    budget = ANFRAGE_DECKEL_TOKENS * 4
    gespraech = _gespraech(db, regular_user, nachrichten=40, zeichen=3_000)

    assert ai_compaction_service.needs_compaction(db, gespraech, budget) is False
    assert ai_compaction_service.needs_compaction(
        db, gespraech, budget, anfrage_zeichen=budget - 1_000
    ) is True


def test_volle_anfrage_ohne_faltbaren_verlauf_ruft_keine_zusammenfassung(
    db: Session, regular_user: User,
) -> None:
    """Belegen Prompt und Katalog den Platz, gibt es nichts zu retten — und keinen Providerruf."""
    from services import ai_compaction_service

    budget = ANFRAGE_DECKEL_TOKENS * 4
    gespraech = _gespraech(db, regular_user, nachrichten=20, zeichen=200)

    assert ai_compaction_service.needs_compaction(
        db, gespraech, budget, anfrage_zeichen=budget
    ) is False


# ── Werkzeugkatalog ────────────────────────────────────────────────────


def test_doku_lesen_kommt_mit_der_doku_suche() -> None:
    """`search_docs` findet nur Ausschnitte; ohne `read_docs` bleibt der Abschnitt ungelesen."""
    from services.tool_selection_port import HOTSET

    assert {"search_docs", "read_docs"} <= HOTSET



def _katalog() -> list[dict]:
    return (
        ai_action_service.provider_tool_definitions()
        + ai_action_service.voice_control_tool_definitions()
    )


def test_jedes_pflichtfeld_steht_auch_im_schema() -> None:
    """Beim Kürzen wurden Zeilen gelöscht; ein verwaistes ``required`` lehnen
    strenge Anbieter (OpenAI strict) mit 400 ab — für den ganzen Lauf."""
    for definition in _katalog():
        funktion = definition["function"]
        parameter = funktion["parameters"]
        felder = parameter.get("properties", {})
        fehlend = set(parameter.get("required", [])) - set(felder)
        assert not fehlend, (funktion["name"], fehlend)
        for name, schema in felder.items():
            assert "type" in schema or "enum" in schema or "anyOf" in schema, (funktion["name"], name)


def test_jedes_werkzeug_sagt_was_es_tut() -> None:
    namen = [d["function"]["name"] for d in _katalog()]
    for definition in _katalog():
        beschreibung = definition["function"]["description"].strip()
        assert len(beschreibung) >= 20, definition["function"]["name"]
    # voice_control darf Namen mit dem Panelkatalog teilen; der Panelkatalog nicht mit sich selbst.
    panel = [d["function"]["name"] for d in ai_action_service.provider_tool_definitions()]
    assert len(panel) == len(set(panel))
    assert namen


def test_endgueltige_werkzeuge_behalten_ihre_warnung() -> None:
    """Die Kürzung durfte Prosa streichen, nicht die Sätze, die vor Verlust warnen."""
    beschreibung = {
        d["function"]["name"]: d["function"]["description"].lower()
        for d in ai_action_service.provider_tool_definitions()
    }
    assert "rueckgaengig" in beschreibung["propose_server_delete"]
    assert "bestaetigung" in beschreibung["propose_backup_restore"]
    assert "alle" in beschreibung["propose_backup_restore"]
    assert "loescht" in beschreibung["propose_server_blueprint_switch"]
    assert "nie raten" in beschreibung["propose_backup_restore"]
    assert "einmalig" in beschreibung["propose_hoster_integration"]
    assert "endgueltig" in beschreibung["propose_task_delete"]
    assert "keine zugangsdaten" in beschreibung["learn_skill"]


def test_der_katalog_ist_json_und_ohne_mojibake() -> None:
    text = json.dumps(_katalog(), ensure_ascii=False)
    for kaputt in ("Ã¤", "Ã¶", "Ã¼", "ÃŸ", "â€"):
        assert kaputt not in text
