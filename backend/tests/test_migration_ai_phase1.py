"""Rolling-Upgrade-Test für AI-Limits, Usage und Audit-Kontext."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

import models  # noqa: F401 - registriert das vollständige ORM-Schema
from config import settings
from database import Base


def test_ai_phase1_migration_upgrades_existing_schema(tmp_path: Path) -> None:
    """Vorgänger-Schema und alte Audit-Zeilen bleiben beim Upgrade verwendbar."""
    db_url = f"sqlite:///{tmp_path / 'ai-phase1.db'}"
    previous_database_url = settings.database_url
    settings.database_url = db_url
    engine = create_engine(db_url)
    try:
        Base.metadata.create_all(engine)
        with engine.begin() as connection:
            connection.execute(text("DROP TABLE ai_usage_events"))
            connection.execute(text("DROP TABLE role_ai_limits"))
            connection.execute(text("DROP INDEX ix_audit_logs_correlation_id"))
            connection.execute(text("ALTER TABLE audit_logs DROP COLUMN correlation_id"))
            connection.execute(text("ALTER TABLE audit_logs DROP COLUMN origin"))
            connection.execute(text(
                "INSERT INTO audit_logs "
                "(user_id, action, target_type, target_id, details, created_at) "
                "VALUES (NULL, 'legacy.test', NULL, NULL, NULL, CURRENT_TIMESTAMP)"
            ))

        backend_dir = Path(__file__).resolve().parent.parent
        config = Config(str(backend_dir / "alembic.ini"))
        config.set_main_option("script_location", str(backend_dir / "migrations"))
        command.stamp(config, "20260801_01")
        command.upgrade(config, "20260801_02")

        inspector = inspect(engine)
        assert {"role_ai_limits", "ai_usage_events"}.issubset(inspector.get_table_names())
        usage_columns = {column["name"] for column in inspector.get_columns("ai_usage_events")}
        assert {"reserved_tokens", "reserved_cost_microunits", "accounted_tokens"}.issubset(usage_columns)
        audit_columns = {column["name"] for column in inspector.get_columns("audit_logs")}
        assert {"origin", "correlation_id"}.issubset(audit_columns)
        with engine.connect() as connection:
            legacy = connection.execute(text(
                "SELECT origin, correlation_id FROM audit_logs WHERE action='legacy.test'"
            )).one()
        assert legacy.origin == "direct"
        assert legacy.correlation_id is None
    finally:
        engine.dispose()
        settings.database_url = previous_database_url
