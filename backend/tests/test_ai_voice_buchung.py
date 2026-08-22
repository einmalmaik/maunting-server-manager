"""Das Zuhören steht auf der Rechnung — und seit dem 17.08.2026 auch im Buch.

Die Abschrift lief vollständig an Token- und Kostengrenzen vorbei: `_abhoeren`
rief `ai_stt.hoeren` ohne `usage`, und die Grenzen des Betreibers deckten den
Denk- und den Sprechweg, nicht das Ohr. Jetzt verbucht die Brücke jede
gelungene Abschrift als eigenen Verbrauch (`_abschrift_verbuchen`) — über
dieselben öffentlichen Funktionen wie Chat und Verdichtung
(`reserve_ai_usage`, `abrechnung`, `complete_ai_usage`).

Die Reihenfolge ist Teil der Zusage: gebucht wird **nach** dem Hören, nicht
davor. Eine Reservierung vor der Abschrift würfe die Äusserung weg, bevor
irgendwer weiss, was gesagt wurde. Und die Buchung darf das Gespräch nicht
abreissen: ein erschöpftes Kontingent wird als Störung gemeldet, jeder andere
Buchungsfehler landet im Protokoll statt auf dem Ohr.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from models import AiProvider, AiUsageEvent, User
from services import ai_stt, ai_usage_service, ai_voice_bridge


class _Attrappe(ai_voice_bridge.Sprachbruecke):
    """Eine Brücke ohne Browser: gesendete Ereignisse landen in einer Liste."""

    def __init__(self, benutzer_id: int, zugang_id: int) -> None:  # noqa: D107
        super().__init__(
            browser=None,  # type: ignore[arg-type]
            user_id=benutzer_id,
            conversation_id="egal",
            chat_provider_id=zugang_id,
            stimm_kind="elevenlabs",
            stimm_adresse="wss://example.invalid/",
            stimm_schluessel="egal",
            http_client=None,  # type: ignore[arg-type]
        )
        self.ereignisse: list[dict] = []

    async def _senden(self, nutzlast: dict) -> None:
        self.ereignisse.append(nutzlast)


def _zugang(db) -> AiProvider:
    """Ein Chatzugang mit hörendem Modell — ohne Schlüsselpflicht, damit der
    Test keinen Verschlüsselungsaufbau braucht; die Buchung fragt nie danach."""
    zugang = AiProvider(
        name="Gehoer",
        provider_kind="openrouter",
        default_model="openai/gpt-5.6-luna",
        transcription_model="openai/gpt-transcribe",
        enabled=True,
        requires_api_key=False,
    )
    db.add(zugang)
    db.commit()
    return zugang


def _aeusserung() -> SimpleNamespace:
    return SimpleNamespace(pcm=b"\x00\x00" * (ai_stt.ABTASTRATE * 2), sekunden=2.0)


def _hoeren_attrappe(monkeypatch, *, tokens: int | None) -> None:
    """Ersetzt das Gehör: liefert einen Wortlaut und meldet Messwerte."""

    async def hoeren(_client, *, provider, api_key, pcm, usage=None, **_rest):
        if usage is not None and tokens is not None:
            usage.prompt_tokens = tokens - 8
            usage.completion_tokens = 8
            usage.total_tokens = tokens
            usage.vom_anbieter = True
        return "Starte den Server neu"

    monkeypatch.setattr(ai_stt, "hoeren", hoeren)


@pytest.mark.asyncio
async def test_eine_gelungene_abschrift_wird_als_verbrauch_verbucht(
    db, owner_user: User, monkeypatch
) -> None:
    """Die Kernzusage: Zuhören zählt gegen dieselben Grenzen wie Denken.

    Gebucht wird, was der Anbieter meldet — als abgeschlossenes Ereignis mit
    dem hörenden Modell, nicht dem denkenden: auf der Abrechnung soll stehen,
    **wer** die Tokens verbraucht hat.
    """
    zugang = _zugang(db)
    _hoeren_attrappe(monkeypatch, tokens=128)
    bruecke = _Attrappe(owner_user.id, zugang.id)

    wortlaut = await bruecke._abhoeren(_aeusserung())

    assert wortlaut == "Starte den Server neu"
    db.expire_all()
    ereignisse = db.query(AiUsageEvent).all()
    assert len(ereignisse) == 1
    buchung = ereignisse[0]
    assert buchung.status == "completed"
    assert buchung.accounted_tokens == 128
    assert buchung.user_id == owner_user.id
    assert buchung.provider_id == zugang.id
    assert buchung.model == "openai/gpt-transcribe"


@pytest.mark.asyncio
async def test_ohne_gemeldete_zahlen_wird_die_zeichennaeherung_gebucht(
    db, owner_user: User, monkeypatch
) -> None:
    """Ein schweigender Anbieter heisst nicht: kostenlos.

    Dieselbe Näherung wie überall sonst — Zeichen durch vier. Der Tonanteil
    bleibt dabei ungezählt, denn MSM erfindet keine Zahl für etwas, das der
    Anbieter nicht meldet.
    """
    zugang = _zugang(db)
    _hoeren_attrappe(monkeypatch, tokens=None)
    bruecke = _Attrappe(owner_user.id, zugang.id)

    wortlaut = await bruecke._abhoeren(_aeusserung())

    assert wortlaut == "Starte den Server neu"
    db.expire_all()
    buchung = db.query(AiUsageEvent).one()
    assert buchung.status == "completed"
    assert buchung.accounted_tokens == max(1, len("Starte den Server neu") // 4)


@pytest.mark.asyncio
async def test_ohne_kontingent_wird_die_stoerung_gemeldet_statt_zu_crashen(
    db, owner_user: User, monkeypatch
) -> None:
    """Ein erschöpftes Kontingent beendet den Zug — hörbar, nicht als Absturz.

    Der Sprechende sitzt davor: er bekommt dieselbe Störung wie bei einem
    abgelehnten Lauf und die Sitzung geht zurück auf „bereit", statt dass
    eine Ausnahme sie mitnimmt.
    """
    zugang = _zugang(db)
    _hoeren_attrappe(monkeypatch, tokens=128)

    def erschoepft(*_args, **_kwargs):
        raise ai_usage_service.AiQuotaExceeded("daily_token_limit")

    monkeypatch.setattr(ai_usage_service, "reserve_ai_usage", erschoepft)
    bruecke = _Attrappe(owner_user.id, zugang.id)

    wortlaut = await bruecke._abhoeren(_aeusserung())

    assert wortlaut is None
    # Mit `grund`: „warte eine Minute" ist eine andere Auskunft als „etwas ist
    # kaputt", und die Oberflaeche unterscheidet die beiden an diesem Feld.
    assert {"art": "stoerung", "grund": "kontingent"} in bruecke.ereignisse
    zustaende = [
        ereignis["zustand"]
        for ereignis in bruecke.ereignisse
        if ereignis.get("art") == "zustand"
    ]
    assert zustaende[-1] == ai_voice_bridge.ZUSTAND_BEREIT
    assert db.query(AiUsageEvent).count() == 0


@pytest.mark.asyncio
async def test_ein_buchungsfehler_reisst_das_gespraech_nicht_ab(
    db, owner_user: User, monkeypatch
) -> None:
    """Nur das Kontingent darf den Zug beenden — kein Buchhaltungsfehler.

    Die Anbieterkosten sind längst entstanden; den Sprechenden für eine
    kaputte Buchung zu bestrafen zöge die falsche Konsequenz. Der Fehler
    landet im Protokoll, der Wortlaut geht seinen Weg.
    """
    zugang = _zugang(db)
    _hoeren_attrappe(monkeypatch, tokens=128)

    def kaputt(*_args, **_kwargs):
        raise RuntimeError("Datenbank weg")

    monkeypatch.setattr(ai_usage_service, "reserve_ai_usage", kaputt)
    bruecke = _Attrappe(owner_user.id, zugang.id)

    wortlaut = await bruecke._abhoeren(_aeusserung())

    assert wortlaut == "Starte den Server neu"
    assert {"art": "stoerung"} not in bruecke.ereignisse


@pytest.mark.asyncio
async def test_auch_die_lauf_buchung_meldet_ihr_kontingent_mit_grund(
    db, owner_user: User, monkeypatch
) -> None:
    """Die **zweite** Buchung des Zugs — der Lauf — trifft die Grenze zuerst.

    Ein `requests_per_minute`-Limit zaehlt beide Buchungen; die Abschrift
    (klein, zuerst) geht dann noch durch, der Lauf (die zweite Zaehlung)
    nicht mehr. Und dieser Fehler kommt nicht als Ausnahme an:
    `lauf_beginnen_nebenher` faengt `AiQuotaExceeded` selbst und liefert
    `(None, ("AI_QUOTA_…", …))` zurueck. Ohne die Weiche am Rueckgabewert
    hoerte der Sprechende „etwas ist kaputt", wo „warte eine Minute" die
    Auskunft ist — genau der Fall, fuer den `grund` gebaut wurde.
    """
    from services import ai_stream_service

    zugang = _zugang(db)

    async def abgelehnt(**_kwargs):
        return None, ("AI_QUOTA_REQUESTS_PER_MINUTE", "ai.chat.errors.quota")

    monkeypatch.setattr(ai_stream_service, "lauf_beginnen_nebenher", abgelehnt)
    bruecke = _Attrappe(owner_user.id, zugang.id)

    await bruecke._antworten("Starte den Server neu")

    assert {"art": "stoerung", "grund": "kontingent"} in bruecke.ereignisse
    assert {"art": "stoerung"} not in bruecke.ereignisse


@pytest.mark.asyncio
async def test_eine_andere_ablehnung_des_laufs_behauptet_kein_kontingent(
    db, owner_user: User, monkeypatch
) -> None:
    """Die Gegenprobe: `grund` nur, wo Warten wirklich hilft.

    Ein fehlender Schluessel wird durch Geduld nicht besser — eine
    Kontingentmeldung dafuer schickte den Sprechenden in eine Warteschleife
    vor einer Tuer, die sich nie oeffnet.
    """
    from services import ai_stream_service

    zugang = _zugang(db)

    async def abgelehnt(**_kwargs):
        return None, ("AI_CREDENTIAL_UNAVAILABLE", "ai.chat.errors.credential")

    monkeypatch.setattr(ai_stream_service, "lauf_beginnen_nebenher", abgelehnt)
    bruecke = _Attrappe(owner_user.id, zugang.id)

    await bruecke._antworten("Starte den Server neu")

    assert {"art": "stoerung"} in bruecke.ereignisse
    assert not any(
        ereignis.get("grund") == "kontingent" for ereignis in bruecke.ereignisse
    )


@pytest.mark.asyncio
async def test_die_denkwahl_geht_in_der_mundart_des_modells_hinaus(
    db, owner_user: User, monkeypatch
) -> None:
    """„Nicht nachdenken" wird durchgesetzt, nicht gewünscht.

    Hier ging hart ``reasoning=False`` ohne Stufe hinaus. Bei Modellen mit
    Denkzwang setzt das nichts durch — der Anbieter nimmt seine Vorgabe, und
    der Mensch hört Sekunden Stille vor dem ersten Wort. Seit dem Fix fragt
    die Brücke `ai_reasoning.aus_fuer` (dieselbe Klemme wie Verdichtung, Mail
    und Diktat) und reicht deren Antwort wörtlich an den Lauf weiter.
    """
    from services import ai_reasoning, ai_stream_service

    zugang = _zugang(db)

    async def aus_fuer(_client, provider, *, api_key=None, model_id=None):
        # Der Denkzwang-Fall: „aus" gibt es nicht, die flachste Stufe geht.
        return True, "low"

    monkeypatch.setattr(ai_reasoning, "aus_fuer", aus_fuer)
    erfasst: dict = {}

    async def lauf(**kwargs):
        erfasst.update(kwargs)
        return None, ("AI_PROVIDER_UNAVAILABLE", "egal")

    monkeypatch.setattr(ai_stream_service, "lauf_beginnen_nebenher", lauf)
    bruecke = _Attrappe(owner_user.id, zugang.id)

    await bruecke._antworten("Starte den Server neu")

    assert erfasst["reasoning"] is True
    assert erfasst["reasoning_effort"] == "low"


@pytest.mark.asyncio
async def test_ein_katalogfehler_macht_die_stimme_nicht_stumm(
    db, owner_user: User, monkeypatch
) -> None:
    """Scheitert der Katalog, geht der blosse Schalter hinaus — kein Abbruch.

    Die Denkwahl ist eine Zusatzauskunft. Ein Netz- oder Katalogfehler darf
    das Gespräch nicht abreissen; schlimmstenfalls denkt ein Denkzwang-Modell
    in seiner Vorgabe, wie vor dem Fix.
    """
    from services import ai_reasoning, ai_stream_service

    zugang = _zugang(db)

    async def kaputt(_client, provider, *, api_key=None, model_id=None):
        raise RuntimeError("Katalog nicht erreichbar")

    monkeypatch.setattr(ai_reasoning, "aus_fuer", kaputt)
    erfasst: dict = {}

    async def lauf(**kwargs):
        erfasst.update(kwargs)
        return None, ("AI_PROVIDER_UNAVAILABLE", "egal")

    monkeypatch.setattr(ai_stream_service, "lauf_beginnen_nebenher", lauf)
    bruecke = _Attrappe(owner_user.id, zugang.id)

    await bruecke._antworten("Starte den Server neu")

    assert erfasst["reasoning"] is False
    assert erfasst["reasoning_effort"] is None
