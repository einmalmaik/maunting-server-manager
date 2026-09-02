"""Rolling-Upgrade-Test fuer persistente AI-Aktionsvorschlaege."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

import models  # noqa: F401
from config import settings
from database import Base


def test_ai_phase4_migration_roundtrip(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'ai-phase4.db'}"
    previous_database_url = settings.database_url
    settings.database_url = db_url
    engine = create_engine(db_url)
    backend_dir = Path(__file__).resolve().parent.parent
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "migrations"))
    try:
        Base.metadata.create_all(engine)
        command.stamp(config, "20260801_05")
        command.downgrade(config, "20260801_04")
        assert "ai_action_proposals" not in inspect(engine).get_table_names()

        command.upgrade(config, "20260801_05")
        inspector = inspect(engine)
        assert "ai_action_proposals" in inspector.get_table_names()
        columns = {column["name"] for column in inspector.get_columns("ai_action_proposals")}
        assert {
            "payload_encrypted",
            "confirmation_token_hash",
            "confirmation_expires_at",
            "correlation_id",
        }.issubset(columns)
        checks = {item["name"] for item in inspector.get_check_constraints("ai_action_proposals")}
        assert "ck_ai_action_proposals_status" in checks
    finally:
        engine.dispose()
        settings.database_url = previous_database_url
