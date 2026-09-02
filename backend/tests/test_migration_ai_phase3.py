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


def _create_historic_user_credentials(engine) -> None:
    """Legt eine Tabelle an, die es im ORM nicht mehr gibt.

    Dieser Test stellt den Stand von Revision ``20260801_04`` mit
    ``create_all`` nach — also mit den **heutigen** Modellen. Das geht nur so
    lange gut, wie keine Tabelle aus dieser Revision spaeter wieder
    verschwindet. ``ai_user_credentials`` ist die erste, die das tut:
    ``20260810_04`` hat sie entfernt, weil Nutzerschluessel abgeschafft wurden.

    Ohne diese Zeilen scheitert der ``downgrade`` an ``DROP INDEX`` auf einer
    Tabelle, die es nie gab — und der Test haette einen Migrationsfehler
    gemeldet, wo in Wahrheit nur seine eigene Nachstellung unvollstaendig war.
    Der ``downgrade`` selbst bleibt unangetastet: fuer eine Datenbank, die
    tatsaechlich auf ``20260801_04`` steht, ist er richtig.
    """
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE ai_user_credentials ("
            " id INTEGER NOT NULL PRIMARY KEY,"
            " user_id INTEGER NOT NULL,"
            " provider_id INTEGER NOT NULL,"
            " api_key_encrypted VARCHAR(4096) NOT NULL,"
            " api_key_hint VARCHAR(64) NOT NULL,"
            " created_at DATETIME NOT NULL,"
            " updated_at DATETIME NOT NULL,"
            " CONSTRAINT uq_ai_user_credentials_user_provider UNIQUE (user_id, provider_id),"
            " FOREIGN KEY(provider_id) REFERENCES ai_providers (id) ON DELETE CASCADE,"
            " FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE)"
        ))
        connection.execute(text(
            "CREATE INDEX ix_ai_user_credentials_user_id"
            " ON ai_user_credentials (user_id)"
        ))
        connection.execute(text(
            "CREATE INDEX ix_ai_user_credentials_provider_id"
            " ON ai_user_credentials (provider_id)"
        ))


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
        _create_historic_user_credentials(engine)
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
