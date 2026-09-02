"""Rolling-Upgrade-Test fuer die Hoster-Anbindung (Phase 6)."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

import models  # noqa: F401
from config import settings
from database import Base


HOSTER_TABLES = {
    "hoster_integrations",
    "hoster_products",
    "hoster_identities",
    "hoster_services",
    "hoster_handoffs",
    "hoster_webhook_deliveries",
}


def test_hoster_phase6_migration_roundtrip(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'hoster-phase6.db'}"
    previous_database_url = settings.database_url
    settings.database_url = db_url
    engine = create_engine(db_url)
    backend_dir = Path(__file__).resolve().parent.parent
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "migrations"))
    try:
        Base.metadata.create_all(engine)
        command.stamp(config, "20260807_01")

        # Downgrade: eine bestehende Installation ohne Hoster-Anbindung bleibt
        # vollstaendig funktionsfaehig.
        command.downgrade(config, "20260801_06")
        before = set(inspect(engine).get_table_names())
        assert not (HOSTER_TABLES & before)
        assert "ai_memory_entries" in before

        command.upgrade(config, "20260807_01")
        inspector = inspect(engine)
        assert HOSTER_TABLES.issubset(set(inspector.get_table_names()))

        integration_columns = {
            column["name"] for column in inspector.get_columns("hoster_integrations")
        }
        # Der API-Key existiert nur als Hash, das Webhook-Secret nur verschluesselt.
        assert {"api_key_hash", "webhook_secret_encrypted", "service_user_id"}.issubset(
            integration_columns
        )
        assert "api_key" not in integration_columns

        service_columns = {
            column["name"] for column in inspector.get_columns("hoster_services")
        }
        assert {"external_service_id", "desired_state", "status", "terminate_after"}.issubset(
            service_columns
        )

        # Der Idempotenzanker muss als echte Datenbankbedingung existieren und
        # darf nicht nur in der Anwendungslogik stehen.
        service_uniques = {
            tuple(item["column_names"])
            for item in inspector.get_unique_constraints("hoster_services")
        }
        assert ("integration_id", "external_service_id") in service_uniques

        identity_uniques = {
            tuple(item["column_names"])
            for item in inspector.get_unique_constraints("hoster_identities")
        }
        assert ("integration_id", "external_subject_hash") in identity_uniques

        handoff_columns = {
            column["name"] for column in inspector.get_columns("hoster_handoffs")
        }
        assert "token_hash" in handoff_columns
        assert "token" not in handoff_columns
    finally:
        engine.dispose()
        settings.database_url = previous_database_url
