"""Jede Beratung der Ethics Engine steht als Verbrauch beim Benutzer.

Befund vom 24.09.2026: seit dem 23.09. berät die Engine vor jedem
folgenreichen Werkzeug, und jeder Rat ist ein Modellaufruf auf Rechnung des
Betreibers. Gebucht wurde keiner; die Preisspalten `ethics_*_price` lagen
ungelesen am Zugang.

Geprüft wird:

* Preis: die Ethikpreise des Zugangs, getrennt nach Eingabe, Ausgabe, Cache;
  der Betrag des Anbieters geht vor, der Rückfallpreis gilt nur ohne sie.
* Kontingent: gebucht wird nachträglich und ohne Prüfung. Die Beratung
  läuft auch bei ausgeschöpften Grenzen, zählt in Tokens und Kosten, aber
  nicht als Anfrage pro Minute.
* Was nie ankam, kostet nichts; ein Fehler beim Buchen kostet nie den Rat.

Gefälscht ist nur das Ethikmodell (`stream_chat_completion`), samt dem, was
der Anbieter an Verbrauch meldet.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from models import AiUsageEvent, Role, RolePermission, User
from services import ai_ethics_service, ai_usage_service
from services.ai_limit_service import LIMIT_FIELDS, set_role_limit
from services.ai_usage_service import AiQuotaExceeded, abrechnung, reserve_ai_usage
from services.openai_compatible_adapter import StreamChunk, StreamUsage
from services.role_service import set_user_roles
from tests.test_ai_ethik_beratung import BEDENKEN, _ethikanbieter, _neustart


#: Je Million Tokens, in US-Cent-Microunits wie alle Preise am Zugang.
EINGABE, AUSGABE, CACHE = 10_000_000, 40_000_000, 1_000_000
#: So hoch, dass ein versehentlich benutzter Rückfallpreis auffiele.
RUECKFALL = 900_000_000


def _ethikmodell(
    monkeypatch: pytest.MonkeyPatch,
    *,
    eingabe: int | None = 1_000,
    ausgabe: int | None = 200,
    gelesen: int = 400,
    betrag: int | None = None,
    text: str | None = None,
    fehler: Exception | None = None,
) -> None:
    """Das Ethikmodell, mit dem Verbrauch, den der Anbieter am Ende meldet.

    ``eingabe=None`` heißt: der Anbieter meldet nichts. ``fehler`` fällt vor
    der ersten Antwort, der Aufruf ist dann nie angekommen.
    """

    async def fake(_client, *, provider, api_key, messages, usage: StreamUsage, **_):
        del provider, api_key, messages
        if fehler is not None:
            raise fehler
        yield StreamChunk("content", text if text is not None else json.dumps(BEDENKEN))
        usage.output_chars += len(text if text is not None else json.dumps(BEDENKEN))
        if eingabe is None:
            return
        usage.prompt_tokens = eingabe
        usage.completion_tokens = ausgabe
        usage.total_tokens = eingabe + (ausgabe or 0)
        usage.cached_tokens = gelesen
        usage.anfragen = 1
        usage.vom_anbieter = True
        usage.cost_micro_usd = betrag

    monkeypatch.setattr(ai_ethics_service, "stream_chat_completion", fake)


def _mit_preisen(db: Session, **felder):
    werte = {
        "ethics_input_price_micro_usd_per_million": EINGABE,
        "ethics_output_price_micro_usd_per_million": AUSGABE,
        "ethics_cache_price_micro_usd_per_million": CACHE,
        "token_price_micro_usd_per_million": RUECKFALL,
    }
    werte.update(felder)
    return _ethikanbieter(db, **werte)


def _grenzen(db: Session, user: User, **werte: int) -> None:
    """Eine Rolle mit Chatrecht und genau diesen KI-Grenzen."""
    rolle = Role(name=f"grenzen-{uuid4().hex[:8]}", description=None, is_system=False)
    db.add(rolle)
    db.flush()
    db.add(RolePermission(role_id=rolle.id, permission_key="ai.chat.use"))
    set_role_limit(db, rolle.id, {feld: werte.get(feld) for feld in LIMIT_FIELDS})
    db.commit()
    set_user_roles(db, user, [rolle.id])


async def _beraten(user: User, anbieter) -> dict:
    return await ai_ethics_service.beraten(
        user_id=user.id, aufrufe=[_neustart()], bevorzugt_id=anbieter.id
    )


def _buchungen(db: Session, user: User) -> list[AiUsageEvent]:
    db.expire_all()
    return db.query(AiUsageEvent).filter(AiUsageEvent.user_id == user.id).all()


# ── Was gebucht wird ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_die_beratung_steht_mit_dem_ethikpreis_beim_benutzer(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    anbieter = _mit_preisen(db)
    _ethikmodell(monkeypatch)

    hinweise = await _beraten(regular_user, anbieter)

    assert list(hinweise) == ["w1"]
    [zeile] = _buchungen(db, regular_user)
    assert zeile.zweck == "ethik"
    assert zeile.status == "completed"
    assert zeile.provider_id == anbieter.id
    assert zeile.model == "ethik-modell"
    assert zeile.accounted_tokens == 1_200
    assert (zeile.prompt_tokens, zeile.completion_tokens, zeile.cached_tokens) == (1_000, 200, 400)
    assert zeile.provider_requests == 1
    # 600 frische Eingabe, 400 aus dem Cache, 200 Ausgabe, je zu ihrem Preis.
    assert zeile.accounted_cost_microunits == (600 * EINGABE + 400 * CACHE + 200 * AUSGABE) // 1_000_000
    assert zeile.cost_source == "estimate"


@pytest.mark.asyncio
async def test_der_betrag_des_anbieters_geht_vor(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    anbieter = _mit_preisen(db)
    _ethikmodell(monkeypatch, betrag=777)

    await _beraten(regular_user, anbieter)

    [zeile] = _buchungen(db, regular_user)
    assert (zeile.accounted_cost_microunits, zeile.cost_source) == (777, "provider")


@pytest.mark.asyncio
async def test_ohne_ethikpreis_gilt_der_rueckfallpreis(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wie im Chat, bei Workern, Verdichtung und Abschrift: ein Preis auf alles."""
    anbieter = _mit_preisen(
        db,
        ethics_output_price_micro_usd_per_million=None,
        token_price_micro_usd_per_million=2_000_000,
    )
    _ethikmodell(monkeypatch)

    await _beraten(regular_user, anbieter)

    [zeile] = _buchungen(db, regular_user)
    assert (zeile.accounted_cost_microunits, zeile.cost_source) == (1_200 * 2, "estimate")


@pytest.mark.asyncio
async def test_schweigt_der_anbieter_wird_aus_den_zeichen_geschaetzt(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    anbieter = _mit_preisen(db)
    _ethikmodell(monkeypatch, eingabe=None)

    await _beraten(regular_user, anbieter)

    [zeile] = _buchungen(db, regular_user)
    assert zeile.prompt_tokens is None  # nicht gemeldet heißt nicht null
    assert zeile.accounted_tokens > 0
    assert 0 < zeile.accounted_cost_microunits < zeile.accounted_tokens * AUSGABE // 1_000_000
    assert zeile.cost_source == "estimate"


def test_rollenpreise_rechnen_mit_der_geschaetzten_aufteilung() -> None:
    """Stufe 2 von `abrechnung`, ohne Engine drumherum."""
    stumm = StreamUsage()

    tokens, kosten, herkunft = abrechnung(
        stumm, reserved_tokens=0, estimated_actual_tokens=300,
        rollenpreise=(EINGABE, AUSGABE, None), geschaetzte_teile=(200, 100),
    )

    assert (tokens, herkunft) == (300, "estimate")
    assert kosten == (200 * EINGABE + 100 * AUSGABE) // 1_000_000
    # Ohne Aufteilung und ohne Rückfallpreis erfindet MSM nichts.
    assert abrechnung(
        stumm, reserved_tokens=0, estimated_actual_tokens=300,
        rollenpreise=(EINGABE, AUSGABE, None),
    ) == (300, 0, "none")


# ── Kontingent ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ausgeschoepfte_grenzen_verhindern_den_rat_nicht(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Lauf hält seine Reservierung, alle Grenzen sind voll — beraten wird trotzdem.

    Mit einer Reservierung vor der Beratung schwiege die Engine bei
    *gleichzeitigen Vorgängen* = 1 immer: der Lauf selbst belegt den Platz.
    """
    anbieter = _mit_preisen(db)
    _grenzen(db, regular_user, concurrent_operations=1, requests_per_minute=1, daily_token_limit=10)
    reserve_ai_usage(db, regular_user, request_id=uuid4(), estimated_tokens=10)
    db.commit()
    _ethikmodell(monkeypatch)

    hinweise = await _beraten(regular_user, anbieter)

    assert hinweise["w1"]["einschaetzung"] == "review"
    ethik = [zeile for zeile in _buchungen(db, regular_user) if zeile.zweck == "ethik"]
    assert [zeile.accounted_tokens for zeile in ethik] == [1_200]


@pytest.mark.asyncio
async def test_die_beratung_zaehlt_in_tokens_nicht_als_anfrage(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die nächste Reservierung sieht die Tokens, aber keine Anfrage.

    *Anfragen pro Minute* wird zuerst geprüft. Fällt die Reservierung an der
    Tagesgrenze, ist sie an der Minutengrenze vorbeigekommen — obwohl die
    Beratung in derselben Minute lag und die Grenze eins ist.
    """
    anbieter = _mit_preisen(db)
    _grenzen(db, regular_user, requests_per_minute=1, daily_token_limit=1_500)
    _ethikmodell(monkeypatch)
    await _beraten(regular_user, anbieter)

    with pytest.raises(AiQuotaExceeded) as abgewiesen:
        reserve_ai_usage(db, regular_user, request_id=uuid4(), estimated_tokens=400)
    db.rollback()

    assert abgewiesen.value.reason == "daily_token_limit"
    reserve_ai_usage(db, regular_user, request_id=uuid4(), estimated_tokens=300)


# ── Was nichts kostet, und was nichts aufhält ──────────────────────────────


@pytest.mark.asyncio
async def test_ein_aufruf_der_nie_ankam_kostet_nichts(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    anbieter = _mit_preisen(db)
    _ethikmodell(monkeypatch, fehler=RuntimeError("Verbindung abgelehnt"))

    assert await _beraten(regular_user, anbieter) == {}
    assert _buchungen(db, regular_user) == []


@pytest.mark.asyncio
async def test_eine_unbrauchbare_antwort_kostet_trotzdem(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kein Rat, aber der Anbieter hat geantwortet und rechnet ab."""
    anbieter = _mit_preisen(db)
    _ethikmodell(monkeypatch, text="Das ist kein JSON.")

    assert await _beraten(regular_user, anbieter) == {}
    [zeile] = _buchungen(db, regular_user)
    assert zeile.accounted_tokens == 1_200


@pytest.mark.asyncio
async def test_ein_fehler_beim_buchen_kostet_nicht_den_rat(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    anbieter = _mit_preisen(db)
    _ethikmodell(monkeypatch)

    def kaputt(*_args, **_kwargs):
        raise RuntimeError("Datenbank weg")

    monkeypatch.setattr(ai_usage_service, "nachtraeglich_buchen", kaputt)

    hinweise = await _beraten(regular_user, anbieter)

    assert hinweise["w1"]["einschaetzung"] == "review"
    assert _buchungen(db, regular_user) == []
