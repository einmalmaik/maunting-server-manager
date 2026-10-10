"""Gedächtnis v2, Stufe 3: welches Modell das Gedächtnis liest.

Betreiberentscheid 07.10.2026, so einfach wie möglich: das Gedächtnismodell
des Zugangs, mit dem gesprochen wird; sonst das eines anderen Zugangs; sonst
das Standardmodell. Schreiber, Altbestand und Import nehmen denselben Weg
(`ai_gedaechtnis_schreiber.gedaechtnis_anbieter`).
"""

from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import AiProvider, AiUsageEvent, User
from services import ai_model_price_service, ai_provider_service
from services.ai_gedaechtnis_schreiber import gedaechtnis_anbieter
from services.dis_client import DisClient
from tests.test_ai_gedaechtnis_schreiber import _durchgang, _erlauben, _gespraech, _modell


def _zugang(db: Session, *, gedaechtnis: str | None = None, **felder) -> AiProvider:
    werte = dict(
        name=f"Zugang-{uuid4().hex[:8]}", provider_kind="openrouter",
        default_model="model-a", enabled=True, requires_api_key=False,
        standard_input_price_micro_usd_per_million=1_000_000,
        standard_output_price_micro_usd_per_million=4_000_000,
    )
    if gedaechtnis:
        werte.update(
            memory_model=gedaechtnis, memory_enabled=True,
            memory_input_price_micro_usd_per_million=200_000,
            memory_output_price_micro_usd_per_million=800_000,
            memory_cache_price_micro_usd_per_million=50_000,
        )
    werte.update(felder)
    provider = AiProvider(**werte)
    db.add(provider)
    db.commit()
    db.refresh(provider)
    return provider


def _am_konto(db: Session, user: User, provider: AiProvider) -> None:
    user.ai_provider_id = provider.id
    db.commit()


def test_das_eigene_gedaechtnismodell_kommt_zuerst(db: Session, regular_user: User) -> None:
    aelter = _zugang(db, gedaechtnis="merk-alt")
    eigener = _zugang(db, gedaechtnis="merk-eigen")
    _am_konto(db, regular_user, eigener)

    provider, modell, preise = gedaechtnis_anbieter(db, regular_user, None)

    assert (provider.id, modell, preise) == (eigener.id, "merk-eigen", (200_000, 800_000, 50_000))
    assert aelter.id < eigener.id


def test_ohne_eigenes_liest_das_eines_anderen_zugangs(db: Session, regular_user: User) -> None:
    """Wer es nur bei einem Anbieter einrichtet, dessen Gedächtnis liest auch
    Gespräche, die über einen anderen liefen."""
    eigener = _zugang(db)
    anderer = _zugang(db, gedaechtnis="merk-anders")
    _am_konto(db, regular_user, eigener)

    provider, modell, preise = gedaechtnis_anbieter(db, regular_user, None)

    assert (provider.id, modell, preise) == (anderer.id, "merk-anders", (200_000, 800_000, 50_000))


def test_ohne_jede_einrichtung_liest_das_standardmodell(db: Session, regular_user: User) -> None:
    eigener = _zugang(db)
    _am_konto(db, regular_user, eigener)

    provider, modell, preise = gedaechtnis_anbieter(db, regular_user, None)

    assert (provider.id, modell, preise) == (eigener.id, "model-a", (1_000_000, 4_000_000, None))


def test_ohne_zugang_kein_modell(db: Session, regular_user: User) -> None:
    assert gedaechtnis_anbieter(db, regular_user, None) is None


def test_abgeschaltet_zaehlt_nicht(db: Session, regular_user: User) -> None:
    """Ein ausgeschalteter Platz und ein ausgeschalteter Zugang zählen nicht —
    auch dann nicht, wenn ein Modell eingetragen ist."""
    eigener = _zugang(db, gedaechtnis="merk-eigen", memory_enabled=False)
    _zugang(db, gedaechtnis="merk-aus", enabled=False)
    _zugang(db, gedaechtnis="merk-platz-aus", memory_enabled=False)
    _am_konto(db, regular_user, eigener)

    provider, modell, _preise = gedaechtnis_anbieter(db, regular_user, None)

    assert (provider.id, modell) == (eigener.id, "model-a")


def test_ein_fremder_zugang_ohne_schluessel_zaehlt_nicht(db: Session, regular_user: User) -> None:
    eigener = _zugang(db)
    anderer = _zugang(db, gedaechtnis="merk-anders", requires_api_key=True)
    _am_konto(db, regular_user, eigener)

    assert gedaechtnis_anbieter(db, regular_user, None)[1] == "model-a"

    anderer.operator_api_key_encrypted = DisClient.encrypt(
        "schluessel-" + "fuer-tests", aad=ai_provider_service._operator_aad(anderer.id)
    )
    db.commit()
    assert gedaechtnis_anbieter(db, regular_user, None)[1] == "merk-anders"


def test_der_zugang_der_letzten_antwort_geht_dem_konto_vor(db: Session, regular_user: User) -> None:
    am_konto = _zugang(db, gedaechtnis="merk-konto")
    geantwortet = _zugang(db, gedaechtnis="merk-antwort")
    _am_konto(db, regular_user, am_konto)

    assert gedaechtnis_anbieter(db, regular_user, geantwortet.id)[1] == "merk-antwort"


def test_der_schreiber_liest_und_bucht_mit_dem_gedaechtnismodell(
    db: Session, regular_user: User, monkeypatch
) -> None:
    _erlauben(db, regular_user)
    gesprochen = _zugang(db)
    gedaechtnis = _zugang(db, gedaechtnis="merk-anders")
    gespraech = _gespraech(db, regular_user, [
        ("user", "Ich spiele dienstags Schach im Verein."),
        ("assistant", "Klingt gut."),
    ], provider=gesprochen)
    gesehen = _modell(monkeypatch, {"aenderungen": []})

    ergebnis = _durchgang(db, gespraech)

    assert ergebnis.status == "ok"
    assert gesehen["model"] == "merk-anders"
    buchung = db.query(AiUsageEvent).filter(AiUsageEvent.user_id == regular_user.id).one()
    assert (buchung.provider_id, buchung.model, buchung.zweck) == (gedaechtnis.id, "merk-anders", "gedaechtnis")
    # Gerechnet mit den Preisen des Gedächtnisplatzes, nicht mit denen des
    # Standardmodells: 900 × 0,20 + 100 × 0,80 statt 900 × 1 + 100 × 4.
    assert buchung.accounted_cost_microunits == 260


def test_der_gedaechtnisplatz_in_der_verwaltung(
    client: TestClient, owner_cookies: dict, csrf_token: str, monkeypatch
) -> None:
    """Modell wählen und fertig: eingeschaltet, Preise aus dem Katalog."""
    monkeypatch.setattr(ai_model_price_service, "_catalog", lambda: {
        "openai/gpt-merk": (150_000, 600_000, 75_000),
    })
    kopf = {"X-CSRF-Token": csrf_token}
    angelegt = client.post("/api/ai/settings/providers", json={
        "name": "Gedaechtnis-Zugang", "provider_kind": "openrouter",
        "default_model": "openai/gpt-chat", "memory_model": " openai/gpt-merk ",
        "enabled": True, "requires_api_key": False,
    }, cookies=owner_cookies, headers=kopf)
    assert angelegt.status_code == 201, angelegt.text
    daten = angelegt.json()
    assert (daten["memory_model"], daten["memory_enabled"]) == ("openai/gpt-merk", True)
    assert (
        daten["memory_input_price_micro_usd_per_million"],
        daten["memory_output_price_micro_usd_per_million"],
        daten["memory_cache_price_micro_usd_per_million"],
    ) == (150_000, 600_000, 75_000)

    pfad = f"/api/ai/settings/providers/{daten['id']}"
    pausiert = client.patch(pfad, json={"memory_enabled": False}, cookies=owner_cookies, headers=kopf)
    assert (pausiert.json()["memory_model"], pausiert.json()["memory_enabled"]) == ("openai/gpt-merk", False)

    weg = client.patch(
        pfad, json={"memory_model": None, "memory_enabled": True}, cookies=owner_cookies, headers=kopf
    )
    assert (weg.json()["memory_model"], weg.json()["memory_enabled"]) == (None, False)
