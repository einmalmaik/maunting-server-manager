"""Rolling-Upgrade-Test fuer Provider- und Chat-Persistenz."""

from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

import models  # noqa: F401 - registriert das vollstaendige ORM-Schema
from config import settings
from database import Base
from models import AiUsageEvent, User


def test_ai_phase3_migration_upgrades_phase2_schema(tmp_path: Path) -> None:
    """Bestehende Usage-Zeilen bleiben erhalten und erhalten NULL-Metadaten."""
    db_url = f"sqlite:///{tmp_path / 'ai-phase3.db'}"
    previous_database_url = settings.database_url
    settings.database_url = db_url
    engine = create_engine(db_url)
    backend_dir = Path(__file__).resolve().parent.parent
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "migrations"))
    try:
        Base.metadata.create_all(engine)
        request_id = str(uuid4())
        with Session(engine) as db:
            user = User(
                username="migration-user",
                email_plain="migration@example.invalid",
                password_hash="disabled",
                is_active=True,
            )
            db.add(user)
            db.flush()
            db.add(
                AiUsageEvent(
                    request_id=request_id,
                    user_id=user.id,
                    status="completed",
                    reserved_tokens=10,
                    reserved_cost_microunits=0,
                    accounted_tokens=8,
                    accounted_cost_microunits=0,
                )
            )
            db.commit()

        command.stamp(config, "20260801_04")
        command.downgrade(config, "20260801_03")
        phase2_inspector = inspect(engine)
        assert "ai_providers" not in phase2_inspector.get_table_names()
        assert "provider_id" not in {
            column["name"] for column in phase2_inspector.get_columns("ai_usage_events")
        }

        command.upgrade(config, "20260801_04")

        inspector = inspect(engine)
        assert {
            "ai_providers",
            "ai_user_credentials",
            "ai_conversations",
            "ai_messages",
        }.issubset(inspector.get_table_names())
        usage_columns = {column["name"] for column in inspector.get_columns("ai_usage_events")}
        assert {"provider_id", "model"}.issubset(usage_columns)
        message_checks = {item["name"] for item in inspector.get_check_constraints("ai_messages")}
        assert {"ck_ai_messages_role", "ck_ai_messages_status"}.issubset(message_checks)
        with engine.connect() as connection:
            preserved = connection.execute(
                text(
                    "SELECT accounted_tokens, provider_id, model "
                    "FROM ai_usage_events WHERE request_id=:request_id"
                ),
                {"request_id": request_id},
            ).one()
        assert preserved.accounted_tokens == 8
        assert preserved.provider_id is None
        assert preserved.model is None
    finally:
        engine.dispose()
        settings.database_url = previous_database_url
