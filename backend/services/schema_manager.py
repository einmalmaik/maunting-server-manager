"""PostgreSQL schema bootstrap and Alembic upgrade gates."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.engine import Engine

from database import Base
import models  # noqa: F401 - ensure complete metadata


def _alembic_config() -> Config:
    backend_dir = Path(__file__).resolve().parent.parent
    return Config(str(backend_dir / "alembic.ini"))


def _missing_model_schema(engine: Engine) -> list[str]:
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    missing: list[str] = []
    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            missing.append(table.name)
            continue
        existing_columns = {
            column["name"] for column in inspector.get_columns(table.name)
        }
        for column in table.columns:
            if column.name not in existing_columns:
                missing.append(f"{table.name}.{column.name}")
    return missing


def initialize_or_upgrade_schema(engine: Engine) -> str:
    """Create/stamp a fresh schema or upgrade an already managed schema.

    An unversioned existing installation is accepted only if its schema exactly
    matches the current models. This prevents Alembic from stamping a partially
    migrated production database as healthy.
    """

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    app_tables = tables & {table.name for table in Base.metadata.sorted_tables}
    config = _alembic_config()

    if "alembic_version" not in tables:
        if not app_tables:
            Base.metadata.create_all(bind=engine)
        missing = _missing_model_schema(engine)
        if missing:
            preview = ", ".join(missing[:8])
            raise RuntimeError(
                "Bestehendes PostgreSQL-Schema ist nicht auf Phase-8-Stand: "
                f"{preview}. Führe zuerst den Legacy-Upgradepfad aus."
            )
        command.stamp(config, "head")
        return "initialized"

    command.upgrade(config, "head")
    missing = _missing_model_schema(engine)
    if missing:
        raise RuntimeError(
            "Alembic-Upgrade unvollständig; fehlendes Schema: "
            + ", ".join(missing[:8])
        )
    return "upgraded"
