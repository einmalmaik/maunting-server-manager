"""Die Worker-Rolle am Provider-Zugang und ihre Betreiber-Deckel.

docs/agentic-framework.md (Abschnitt 5): Der Betreiber legt je Chatzugang
fest, mit welchem Modell und welcher festen Denkstufe Worker arbeiten, und
deckelt, wie viele gleichzeitig laufen und wie viele Runden jeder bekommt.
Der Kunde stellt Worker nicht ein — er bekommt die Felder nicht einmal zu
sehen.

Die Tests hier binden drei Zusagen aneinander, die sonst still
auseinanderlaufen: die Validierung im Service (keine tote Konfiguration),
die Sichtbarkeitsgrenze zwischen Betreiber- und Kundenantwort, und die
Deckel-Vorgaben, die ohne jede Konfiguration gelten muessen.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

import models  # noqa: F401 - registriert das vollstaendige ORM-Schema
from config import settings
from database import Base
from models import AiProvider, Role, RolePermission, User
from services import ai_provider_service, ai_worker_limits
from services.ai_provider_service import AiProviderConfigurationError
from services.role_service import set_user_roles


def _csrf(cookies: dict) -> dict[str, str]:
    return {"X-CSRF-Token": cookies.get("__Secure-csrf_token", "")}


def _chat_provider(db: Session, **extra) -> AiProvider:
    provider = ai_provider_service.create_provider(
        db,
        name=extra.pop("name", "Zugang"),
        provider_kind="openrouter",
        default_model="schnelles-modell",
        enabled=True,
        requires_api_key=True,
        operator_api_key="sk-or-v1-test",
        **extra,
    )
    db.commit()
    return provider


# ── Die Validierung: keine tote Konfiguration ─────────────────────────────


class TestWorkerRolleValidierung:
    def test_worker_rolle_wird_gespeichert(self, db: Session) -> None:
        provider = _chat_provider(
            db, worker_model="gruendliches-modell", worker_reasoning_effort="high"
        )

        db.refresh(provider)
        assert provider.worker_model == "gruendliches-modell"
        assert provider.worker_reasoning_effort == "high"

    def test_leer_heisst_keine_worker_rolle(self, db: Session) -> None:
        """NULL ist der Ein-Modell-Betrieb — der Fallback aus Abschnitt 5."""
        provider = _chat_provider(db, worker_model="  ", worker_reasoning_effort="")

        assert provider.worker_model is None
        assert provider.worker_reasoning_effort is None

    def test_ein_erfundenes_stufenwort_faellt_beim_speichern_auf(
        self, db: Session
    ) -> None:
        """Und nicht erst als 400 des Anbieters bei jedem Worker-Segment."""
        with pytest.raises(AiProviderConfigurationError, match="Worker-Denkstufe"):
            _chat_provider(
                db, worker_model="modell", worker_reasoning_effort="ultra"
            )

    def test_eine_stufe_ohne_worker_modell_gibt_es_nicht(self, db: Session) -> None:
        with pytest.raises(AiProviderConfigurationError, match="Worker-Modell"):
            _chat_provider(db, worker_reasoning_effort="high")

    def test_ein_worker_modell_braucht_das_standardmodell(self, db: Session) -> None:
        """Worker erben den Zugang des Gehirns — allein truege das Modell nichts.

        Ein Zugang mit Stimme und Worker-Modell, aber ohne Standardmodell,
        saehe konfiguriert aus und taete nie etwas: `worker_start` gibt es nur
        in einem Gehirn-Lauf, und den traegt dieser Zugang nicht.
        """
        with pytest.raises(AiProviderConfigurationError, match="Standardmodell"):
            ai_provider_service.create_provider(
                db,
                name="Nur Worker",
                provider_kind="elevenlabs",
                enabled=True,
                requires_api_key=True,
                operator_api_key=None,
                default_voice="21m00Tcm4TlvDq8ikWAM",
                worker_model="gruendliches-modell",
            )

    def test_das_standardmodell_leeren_laesst_kein_worker_modell_zurueck(
        self, db: Session
    ) -> None:
        """Die Pruefung gilt dem Zielzustand, nicht der Eingabe allein."""
        provider = _chat_provider(
            db, worker_model="gruendliches-modell", transcription_model="hoer-modell"
        )

        with pytest.raises(AiProviderConfigurationError, match="Standardmodell"):
            ai_provider_service.update_provider(
                db,
                provider,
                values={"default_model": None},
                operator_api_key=None,
                clear_operator_api_key=False,
            )

    def test_das_worker_modell_leeren_laesst_keine_stufe_zurueck(
        self, db: Session
    ) -> None:
        provider = _chat_provider(
            db, worker_model="gruendliches-modell", worker_reasoning_effort="high"
        )

        with pytest.raises(AiProviderConfigurationError, match="Worker-Modell"):
            ai_provider_service.update_provider(
                db,
                provider,
                values={"worker_model": None},
                operator_api_key=None,
                clear_operator_api_key=False,
            )

    def test_die_worker_rolle_laesst_sich_ganz_abschalten(self, db: Session) -> None:
        provider = _chat_provider(
            db, worker_model="gruendliches-modell", worker_reasoning_effort="high"
        )

        ai_provider_service.update_provider(
            db,
            provider,
            values={"worker_model": None, "worker_reasoning_effort": None},
            operator_api_key=None,
            clear_operator_api_key=False,
        )
        db.commit()

        assert provider.worker_model is None
        assert provider.worker_reasoning_effort is None


# ── Die Sichtbarkeitsgrenze: Betreiber sieht, Kunde nicht ─────────────────


def _mit_chatrecht(db: Session, user: User) -> None:
    role = Role(name=f"chat-{user.id}", description=None, is_system=False)
    db.add(role)
    db.flush()
    db.add(RolePermission(role_id=role.id, permission_key="ai.chat.use"))
    db.commit()
    set_user_roles(db, user, [role.id])


def test_der_betreiber_sieht_die_worker_rolle_in_den_einstellungen(
    client: TestClient, db: Session, owner_cookies: dict
) -> None:
    response = client.post(
        "/api/ai/settings/providers",
        json={
            "name": "Mit Worker",
            "provider_kind": "openrouter",
            "default_model": "schnelles-modell",
            "worker_model": "gruendliches-modell",
            "worker_reasoning_effort": "high",
        },
        cookies=owner_cookies,
        headers=_csrf(owner_cookies),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["worker_model"] == "gruendliches-modell"
    assert body["worker_reasoning_effort"] == "high"


def test_der_kunde_sieht_die_worker_rolle_nirgends(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict
) -> None:
    """„Der Kunde stellt Worker nicht ein" — er sieht die Felder nicht einmal.

    Die Kundenantwort (`AiProviderAvailableResponse`) traegt bewusst kein
    Worker-Feld. Der Test prueft den Antworttext und nicht ein Schema, damit
    auch ein spaeter hinzugefuegtes Feld auffaellt, das die Grenze verschiebt.
    """
    _chat_provider(
        db,
        name="Kundensicht",
        worker_model="gruendliches-modell",
        worker_reasoning_effort="high",
    )
    _mit_chatrecht(db, regular_user)

    response = client.get("/api/ai/providers", cookies=user_cookies)

    assert response.status_code == 200
    assert "worker_model" not in response.text
    assert "worker_reasoning_effort" not in response.text
    assert "gruendliches-modell" not in response.text


# ── Die Deckel: gelten ohne jede Konfiguration ────────────────────────────


class TestWorkerDeckel:
    def test_die_vorgaben_gelten_ohne_konfiguration(self, db: Session) -> None:
        assert ai_worker_limits.max_worker_je_benutzer() == ai_worker_limits.STANDARD_WORKER
        assert ai_worker_limits.rundenbudget_je_worker() == ai_worker_limits.STANDARD_RUNDEN
        assert ai_worker_limits.STANDARD_WORKER >= 1
        assert ai_worker_limits.STANDARD_RUNDEN >= ai_worker_limits.MIN_RUNDEN

    def test_gesetzte_deckel_kommen_zurueck(self, db: Session) -> None:
        try:
            assert ai_worker_limits.set_max_worker_je_benutzer(5) == 5
            assert ai_worker_limits.set_rundenbudget_je_worker(12) == 12
            assert ai_worker_limits.max_worker_je_benutzer() == 5
            assert ai_worker_limits.rundenbudget_je_worker() == 12
        finally:
            ai_worker_limits.set_max_worker_je_benutzer(ai_worker_limits.STANDARD_WORKER)
            ai_worker_limits.set_rundenbudget_je_worker(ai_worker_limits.STANDARD_RUNDEN)

    @pytest.mark.parametrize("wert", [0, -1, 17, True])
    def test_unsinnige_worker_zahlen_werden_abgewiesen(self, wert) -> None:
        with pytest.raises(ValueError):
            ai_worker_limits.set_max_worker_je_benutzer(wert)

    @pytest.mark.parametrize("wert", [0, 3, 49])
    def test_unsinnige_rundenbudgets_werden_abgewiesen(self, wert: int) -> None:
        with pytest.raises(ValueError):
            ai_worker_limits.set_rundenbudget_je_worker(wert)

    def test_das_rundenmaximum_ist_die_harte_code_kappe(self) -> None:
        """Der Betreiber kann Worker knapper halten als den Chat, nie grosszuegiger.

        `ai_worker_limits` darf `ai_stream_service` nicht importieren (der
        Stream-Service wird die Deckel lesen — die Gegenrichtung waere ein
        Zyklus). Die Gleichheit der Obergrenzen haelt deshalb dieser Test.
        """
        from services.ai_stream_service import MAX_TOOL_ROUNDS

        assert ai_worker_limits.MAX_RUNDEN == MAX_TOOL_ROUNDS


def test_die_deckel_endpunkte_gehoeren_dem_betreiber(
    client: TestClient, db: Session, owner_cookies: dict, user_cookies: dict
) -> None:
    verboten = client.get("/api/ai/settings/worker", cookies=user_cookies)
    assert verboten.status_code == 403

    vorher = client.get("/api/ai/settings/worker", cookies=owner_cookies)
    assert vorher.status_code == 200
    assert vorher.json()["max_parallel_workers"] == ai_worker_limits.STANDARD_WORKER

    try:
        gesetzt = client.put(
            "/api/ai/settings/worker",
            json={"max_parallel_workers": 2, "rounds_per_worker": 16},
            cookies=owner_cookies,
            headers=_csrf(owner_cookies),
        )
        assert gesetzt.status_code == 200
        assert gesetzt.json()["max_parallel_workers"] == 2
        assert gesetzt.json()["rounds_per_worker"] == 16

        from models import AuditLog

        assert (
            db.query(AuditLog)
            .filter(AuditLog.action == "ai.worker.limits.updated")
            .count()
            == 1
        )
    finally:
        ai_worker_limits.set_max_worker_je_benutzer(ai_worker_limits.STANDARD_WORKER)
        ai_worker_limits.set_rundenbudget_je_worker(ai_worker_limits.STANDARD_RUNDEN)


# ── Modell und Migration tragen dasselbe ──────────────────────────────────


def _frisch(engine):
    engine.dispose()
    return inspect(engine)


def test_die_migration_traegt_die_worker_spalten(tmp_path: Path) -> None:
    """Rueckbau auf 20260818_01 beweist: die Spalten stammen aus der Kette.

    Dieselbe Lektion wie bei allen Schema-Zusagen (SQLite-
    Fremdschluesselblindheit): ein create_all-Test bliebe gruen, waehrend eine
    echte Anlage die Spalten nie bekaeme.
    """
    db_url = f"sqlite:///{tmp_path / 'worker_spalten.db'}"
    vorher = settings.database_url
    settings.database_url = db_url
    backend_dir = Path(__file__).resolve().parent.parent
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "migrations"))
    engine = create_engine(db_url)
    try:
        Base.metadata.create_all(engine)
        command.stamp(config, "head")

        command.downgrade(config, "20260818_01")
        spalten = {s["name"] for s in _frisch(engine).get_columns("ai_providers")}
        assert "worker_model" not in spalten
        assert "worker_reasoning_effort" not in spalten

        command.upgrade(config, "head")
        spalten = {
            s["name"]: s for s in _frisch(engine).get_columns("ai_providers")
        }
        assert spalten["worker_model"]["nullable"] is True
        assert spalten["worker_reasoning_effort"]["nullable"] is True
    finally:
        engine.dispose()
        settings.database_url = vorher
