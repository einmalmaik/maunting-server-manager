"""Rolling-Upgrade-Test für persistente Operation-Tasks."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

import models  # noqa: F401 - registriert das vollständige ORM-Schema
from config import settings
from database import Base


def test_operation_task_migration_upgrades_phase1_schema(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'operation-tasks.db'}"
    previous_database_url = settings.database_url
    settings.database_url = db_url
    engine = create_engine(db_url)
    try:
        Base.metadata.create_all(engine)
        OperationTask = Base.metadata.tables["operation_tasks"]
        OperationTask.drop(engine)

        backend_dir = Path(__file__).resolve().parent.parent
        config = Config(str(backend_dir / "alembic.ini"))
        config.set_main_option("script_location", str(backend_dir / "migrations"))
        command.stamp(config, "20260801_02")
        command.upgrade(config, "20260801_03")

        inspector = inspect(engine)
        assert "operation_tasks" in inspector.get_table_names()
        columns = {column["name"] for column in inspector.get_columns("operation_tasks")}
        assert {
            "id",
            "actor_user_id",
            "idempotency_key_hash",
            "request_hash",
            "retry_of_id",
            "error_code",
        }.issubset(columns)
        unique_constraints = inspector.get_unique_constraints("operation_tasks")
        assert any(
            set(item["column_names"])
            == {"actor_user_id", "task_type", "idempotency_key_hash"}
            for item in unique_constraints
        )
    finally:
        engine.dispose()
        settings.database_url = previous_database_url
