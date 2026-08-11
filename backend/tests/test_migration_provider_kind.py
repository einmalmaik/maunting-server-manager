"""Der Umstieg auf benannte Anbieter darf keine Konfiguration verschlucken.

Diese Migration ist die einzige der Kette, die **Daten bewertet** statt nur
Spalten zu verschieben: sie entscheidet je Providerzeile, ob deren Adresse zu
einem unterstützten Anbieter gehört. Damit hat sie ein Risiko, das eine reine
Schemaänderung nicht hat — sie kann etwas still abschalten oder, schlimmer,
etwas laufen lassen, dessen Ziel MSM nicht mehr kennt.

Geprüft wird deshalb beides: dass ein OpenRouter-Zugang **weiterläuft** und dass
alles andere **sichtbar abgeschaltet, aber nicht gelöscht** wird.

Die Tests laufen auf SQLite, das Panel auf PostgreSQL. Geprüft wird deshalb nur,
was auf beiden gleich sein muss.
"""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

import models  # noqa: F401
from config import settings
from database import Base


VORHER = "20260810_06"
NACHHER = "20260811_01"


def _config(backend_dir: Path) -> Config:
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "migrations"))
    return config


def _spalten(engine, tabelle: str) -> set[str]:
    return {spalte["name"] for spalte in inspect(engine).get_columns(tabelle)}


def test_provider_kind_migration_keeps_openrouter_and_parks_the_rest(
    tmp_path: Path,
) -> None:
    db_url = f"sqlite:///{tmp_path / 'provider-kind.db'}"
    previous_database_url = settings.database_url
    settings.database_url = db_url
    engine = create_engine(db_url)
    backend_dir = Path(__file__).resolve().parent.parent
    config = _config(backend_dir)
    try:
        Base.metadata.create_all(engine)
        command.stamp(config, NACHHER)
        command.downgrade(config, VORHER)

        spalten = _spalten(engine, "ai_providers")
        assert "base_url" in spalten
        assert "provider_kind" not in spalten

        # Vier Zeilen, wie sie im Bestand vorkommen: die kanonische Adresse, eine
        # mit mitkopiertem Endpunktpfad, ein lokales Ollama und ein Host, der
        # den Namen nur im Pfad trägt (der naheliegende Fehlgriff einer
        # LIKE-Zuordnung).
        with engine.begin() as conn:
            for zeile_id, name, url in [
                (1, "OpenRouter", "https://openrouter.ai/api/v1"),
                (2, "OpenRouter verrutscht", "https://openrouter.ai/api/v1/chat/completions"),
                (3, "Lokales Ollama", "http://localhost:11434/v1"),
                (4, "Falsche Faehrte", "https://boeser-host.invalid/openrouter.ai/v1"),
            ]:
                conn.execute(
                    text(
                        "INSERT INTO ai_providers "
                        "(id, name, base_url, default_model, enabled, requires_api_key, "
                        " allow_private_network, operator_api_key_hint, created_at, updated_at) "
                        "VALUES (:id, :name, :url, 'model-a', 1, 1, 0, '****abcd', "
                        " '2026-08-11 00:00:00', '2026-08-11 00:00:00')"
                    ),
                    {"id": zeile_id, "name": name, "url": url},
                )

        command.upgrade(config, NACHHER)

        spalten = _spalten(engine, "ai_providers")
        assert "provider_kind" in spalten
        assert "base_url" not in spalten
        assert "allow_private_network" not in spalten
        assert "reasoning_effort" in _spalten(engine, "ai_runs")
        assert "max_reasoning_effort" in _spalten(engine, "role_ai_limits")

        with engine.begin() as conn:
            zeilen = {
                row.id: row
                for row in conn.execute(
                    text("SELECT id, name, provider_kind, enabled, operator_api_key_hint "
                         "FROM ai_providers")
                )
            }

        # Keine Zeile ist verschwunden — Abschalten statt Löschen.
        assert set(zeilen) == {1, 2, 3, 4}
        # Und keine hat ihren Schlüssel verloren.
        assert all(row.operator_api_key_hint == "****abcd" for row in zeilen.values())

        # Die kanonische Adresse läuft weiter.
        assert zeilen[1].provider_kind == "openrouter"
        assert bool(zeilen[1].enabled) is True

        # Der mitkopierte Endpunktpfad ändert den Host nicht — dieselbe Zuordnung.
        assert zeilen[2].provider_kind == "openrouter"
        assert bool(zeilen[2].enabled) is True

        # Ein lokales Ollama gibt es nicht mehr: abgeschaltet, aber auffindbar.
        # Und ausdrücklich **ohne** Anbieter — mit dem server_default
        # "openrouter" sähe es wie ein OpenRouter-Zugang aus, und ein Haken bei
        # „aktiv“ schickte den Ollama-Schlüssel an einen fremden Dienst.
        assert bool(zeilen[3].enabled) is False
        assert zeilen[3].provider_kind == ""

        # Der Name im *Pfad* darf nicht zuordnen. Eine LIKE-Suche auf der
        # ganzen URL hätte diese Zeile OpenRouter zugeschlagen und damit einen
        # fremden Host mit dem Schlüssel des Betreibers weiterlaufen lassen.
        assert bool(zeilen[4].enabled) is False
        assert zeilen[4].provider_kind == ""

        command.downgrade(config, VORHER)
        spalten = _spalten(engine, "ai_providers")
        assert "base_url" in spalten
        assert "provider_kind" not in spalten
    finally:
        engine.dispose()
        settings.database_url = previous_database_url
