"""Was eine Sprachsitzung kostet — und wann sie deshalb aufhört.

Der Sprachmodus ist der erste Weg im Panel, bei dem der Verbrauch nicht in einer
Anfrage steckt, sondern über Minuten anfällt. Der Chat reserviert, ruft einmal
an und schliesst ab; eine Sprachsitzung redet weiter, solange jemand redet.

Deshalb prüfen die Tests hier drei Dinge, die im Chat gar nicht auftreten
können: dass eine Sitzung mit leerem Kontingent erst gar nicht aufgeht, dass sie
mittendrin aufhört, wenn der Freiraum aufgebraucht ist, und — das ist der
unangenehmste Fall — dass die Reservierung auch dann geschlossen wird, wenn die
Sitzung abstürzt. Eine offene Reservierung läuft nie ab; sie würde den Benutzer
dauerhaft aussperren, und niemand fände den Grund.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from models import AiProvider, AiUsageEvent, Role
from services import ai_voice_usage
from services.ai_limit_service import LIMIT_FIELDS, set_role_limit
from services.ai_voice_usage import Sitzungsverbrauch
from services.role_service import set_user_roles


def _zugang(db, *, preis: int | None = None) -> AiProvider:
    zugang = AiProvider(
        name="Sprache",
        provider_kind="openai_realtime",
        default_model="gpt-realtime-2.1",
        enabled=True,
        requires_api_key=True,
        token_price_micro_usd_per_million=preis,
    )
    db.add(zugang)
    db.commit()
    return zugang


def _grenze(db, user, *, tageslimit: int) -> None:
    """Gibt dem Benutzer eine Rolle mit Tagesgrenze."""
    rolle = Role(name=f"sprache-{uuid4().hex[:8]}", description=None, is_system=False)
    db.add(rolle)
    db.flush()
    grenzen = {feld: None for feld in LIMIT_FIELDS}
    grenzen["daily_token_limit"] = tageslimit
    set_role_limit(db, rolle.id, grenzen)
    db.commit()
    set_user_roles(db, user, [rolle.id])
    db.commit()


# ── Die Zählung ───────────────────────────────────────────────────────────


def test_the_token_count_prefers_the_reported_total() -> None:
    verbrauch = Sitzungsverbrauch(request_id=uuid4(), freiraum=None, reserviert=0)

    verbrauch.melden({"total_tokens": 1234, "input_tokens": 1, "output_tokens": 2})

    assert verbrauch.verbraucht == 1234


def test_without_a_total_input_and_output_are_summed() -> None:
    verbrauch = Sitzungsverbrauch(request_id=uuid4(), freiraum=None, reserviert=0)

    verbrauch.melden({"input_tokens": 300, "output_tokens": 700})

    assert verbrauch.verbraucht == 1000


def test_a_report_without_numbers_counts_nothing() -> None:
    """Eine fehlende Angabe ist keine Null.

    Eine erfundene Null wäre hier ein Freifahrtschein: die Sitzung liefe
    weiter, obwohl niemand weiss, was sie verbraucht hat.
    """
    verbrauch = Sitzungsverbrauch(request_id=uuid4(), freiraum=10, reserviert=0)

    assert verbrauch.melden({}) is True
    assert verbrauch.melden({"total_tokens": "viele"}) is True
    assert verbrauch.verbraucht == 0


def test_negative_numbers_are_ignored() -> None:
    verbrauch = Sitzungsverbrauch(request_id=uuid4(), freiraum=None, reserviert=0)

    verbrauch.melden({"input_tokens": -5, "output_tokens": 10})

    assert verbrauch.verbraucht == 10


def test_the_session_stops_when_the_headroom_is_used_up() -> None:
    verbrauch = Sitzungsverbrauch(request_id=uuid4(), freiraum=1_000, reserviert=0)

    assert verbrauch.melden({"total_tokens": 600}) is True
    assert verbrauch.melden({"total_tokens": 400}) is True   # genau aufgebraucht
    assert verbrauch.melden({"total_tokens": 1}) is False


def test_without_a_limit_the_session_simply_runs() -> None:
    """``None`` heisst unbegrenzt und nie „null frei"."""
    verbrauch = Sitzungsverbrauch(request_id=uuid4(), freiraum=None, reserviert=0)

    assert verbrauch.melden({"total_tokens": 10_000_000}) is True


# ── Die Buchung ───────────────────────────────────────────────────────────


def test_the_booking_never_falls_below_the_reservation() -> None:
    """Die Anweisungen gingen hinaus, ob geantwortet wurde oder nicht."""
    verbrauch = Sitzungsverbrauch(request_id=uuid4(), freiraum=None, reserviert=500)

    verbrauch.melden({"total_tokens": 10})

    assert verbrauch.gesamt == 500


def test_without_a_maintained_price_the_cost_is_honestly_zero() -> None:
    """OpenAIs Modellliste nennt keine Preise. MSM erfindet keinen."""
    verbrauch = Sitzungsverbrauch(
        request_id=uuid4(), freiraum=None, reserviert=0, preis=None, verbraucht=1_000_000
    )

    assert verbrauch.kosten() == (0, "none")


def test_with_a_maintained_price_the_cost_is_marked_as_an_estimate() -> None:
    verbrauch = Sitzungsverbrauch(
        request_id=uuid4(), freiraum=None, reserviert=0, preis=32_000_000, verbraucht=1_000_000
    )

    # Eine Näherung mit *einem* Preis auf *alle* Tokens — und als solche
    # markiert, damit sie in der Übersicht nicht wie eine Messung aussieht.
    assert verbrauch.kosten() == (32_000_000, "estimate")


# ── Das Öffnen ────────────────────────────────────────────────────────────


def test_opening_reserves_and_reports_the_headroom(db, owner_user) -> None:
    zugang = _zugang(db)
    _grenze(db, owner_user, tageslimit=50_000)

    verbrauch = ai_voice_usage.oeffnen(db, owner_user, zugang, geschaetzt=2_000)

    assert verbrauch is not None
    # Der Freiraum wird **vor** der eigenen Reservierung gelesen: die Sitzung
    # soll das, was sie selbst gerade gebucht hat, noch verbrauchen dürfen.
    assert verbrauch.freiraum == 50_000
    ereignis = (
        db.query(AiUsageEvent)
        .filter(AiUsageEvent.request_id == str(verbrauch.request_id))
        .one()
    )
    assert ereignis.status == "reserved"
    assert ereignis.accounted_tokens == 2_000
    assert ereignis.model == "gpt-realtime-2.1"


def test_an_exhausted_quota_refuses_the_session(db, owner_user) -> None:
    zugang = _zugang(db)
    _grenze(db, owner_user, tageslimit=100)

    assert ai_voice_usage.oeffnen(db, owner_user, zugang, geschaetzt=5_000) is None
    assert db.query(AiUsageEvent).count() == 0


def test_without_any_limit_the_headroom_is_unbounded(db, owner_user) -> None:
    zugang = _zugang(db)

    verbrauch = ai_voice_usage.oeffnen(db, owner_user, zugang, geschaetzt=10)

    assert verbrauch is not None
    assert verbrauch.freiraum is None


# ── Der Abschluss ─────────────────────────────────────────────────────────


def test_closing_books_what_was_really_spoken(db, owner_user) -> None:
    zugang = _zugang(db, preis=32_000_000)
    verbrauch = ai_voice_usage.oeffnen(db, owner_user, zugang, geschaetzt=1_000)
    assert verbrauch is not None
    verbrauch.melden({"total_tokens": 7_500})

    ai_voice_usage.abschliessen(verbrauch)

    ereignis = (
        db.query(AiUsageEvent)
        .filter(AiUsageEvent.request_id == str(verbrauch.request_id))
        .one()
    )
    db.refresh(ereignis)
    assert ereignis.status == "completed"
    assert ereignis.accounted_tokens == 7_500
    assert ereignis.cost_source == "estimate"


def test_a_session_that_never_started_releases_its_reservation(db, owner_user) -> None:
    """Nichts gehört, nichts gesagt — dann wird auch nichts gebucht."""
    zugang = _zugang(db)
    verbrauch = ai_voice_usage.oeffnen(db, owner_user, zugang, geschaetzt=1_000)
    assert verbrauch is not None

    ai_voice_usage.abschliessen(verbrauch, gescheitert=True)

    ereignis = (
        db.query(AiUsageEvent)
        .filter(AiUsageEvent.request_id == str(verbrauch.request_id))
        .one()
    )
    db.refresh(ereignis)
    assert ereignis.status == "failed"
    assert ereignis.accounted_tokens == 0


def test_a_crashed_session_still_books_what_it_used(db, owner_user) -> None:
    """Ein Abbruch mitten im Gespräch ist kein Grund, das Gesagte zu verschenken."""
    zugang = _zugang(db)
    verbrauch = ai_voice_usage.oeffnen(db, owner_user, zugang, geschaetzt=1_000)
    assert verbrauch is not None
    verbrauch.melden({"total_tokens": 4_000})

    ai_voice_usage.abschliessen(verbrauch, gescheitert=True)

    ereignis = (
        db.query(AiUsageEvent)
        .filter(AiUsageEvent.request_id == str(verbrauch.request_id))
        .one()
    )
    db.refresh(ereignis)
    assert ereignis.status == "completed"
    assert ereignis.accounted_tokens == 4_000


def test_closing_twice_is_not_a_conflict(db, owner_user) -> None:
    """Der Anrufer schliesst im ``finally``; ein zweiter Aufruf darf nicht werfen."""
    zugang = _zugang(db)
    verbrauch = ai_voice_usage.oeffnen(db, owner_user, zugang, geschaetzt=100)
    assert verbrauch is not None

    ai_voice_usage.abschliessen(verbrauch)
    ai_voice_usage.abschliessen(verbrauch)

    ereignis = (
        db.query(AiUsageEvent)
        .filter(AiUsageEvent.request_id == str(verbrauch.request_id))
        .one()
    )
    db.refresh(ereignis)
    assert ereignis.status == "completed"


# ── Die Schätzung ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "anweisungen, zeichen, erwartet",
    [
        ("", 0, 1),          # nie null: eine Sitzung kostet immer etwas
        ("a" * 400, 0, 100),
        ("", 800, 200),
        ("a" * 400, 800, 300),
    ],
)
def test_the_estimate_is_the_usual_rough_rule(
    anweisungen: str, zeichen: int, erwartet: int
) -> None:
    assert ai_voice_usage.schaetzung(anweisungen, zeichen) == erwartet
