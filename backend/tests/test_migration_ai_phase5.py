"""Rolling-Upgrade-Test fuer Memory, Skills und Anhaenge."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

import models  # noqa: F401
from config import settings
from database import Base


def test_ai_phase5_migration_roundtrip(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'ai-phase5.db'}"
    previous_database_url = settings.database_url
    settings.database_url = db_url
    engine = create_engine(db_url)
    backend_dir = Path(__file__).resolve().parent.parent
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "migrations"))
    try:
        Base.metadata.create_all(engine)
        command.stamp(config, "20260801_06")
        command.downgrade(config, "20260801_05")
        phase4_tables = inspect(engine).get_table_names()
        assert "ai_action_proposals" in phase4_tables
        assert "ai_memory_entries" not in phase4_tables

        command.upgrade(config, "20260801_06")
        inspector = inspect(engine)
        assert {
            "ai_memory_preferences", "ai_memory_entries", "ai_skills", "ai_attachments"
        }.issubset(inspector.get_table_names())
        memory_columns = {
            column["name"] for column in inspector.get_columns("ai_memory_entries")
        }
        assert {"scope_identity", "value_encrypted"}.issubset(memory_columns)
        attachment_columns = {
            column["name"] for column in inspector.get_columns("ai_attachments")
        }
        assert {"content_encrypted", "extracted_text_encrypted", "sha256"}.issubset(
            attachment_columns
        )
    finally:
        engine.dispose()
        settings.database_url = previous_database_url
