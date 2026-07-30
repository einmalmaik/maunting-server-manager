import pytest
from sqlalchemy import text
from alembic.config import Config
from alembic import command
import os
from pathlib import Path
from sqlalchemy import create_engine
from database import Base
import models  # noqa: F401


def test_guardian_accepted_payload_hash_migration(tmp_path):
    """
    Testet das Upgrade von Revision 20260720_02 zu beef3761b732.
    Prüft, ob die Spalte 'guardian_accepted_payload_hash' vor dem Upgrade
    nicht existiert und nach dem Upgrade existiert.
    """
    db_path = tmp_path / "test_migration.db"
    db_url = f"sqlite:///{db_path}"
    os.environ["DATABASE_URL"] = db_url

    from config import settings
    settings.database_url = db_url

    engine = create_engine(db_url)

    # Create base schema
    Base.metadata.create_all(bind=engine)

    # Remove guardian_accepted_payload_hash column to simulate state at 20260720_02
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE servers DROP COLUMN guardian_accepted_payload_hash"))

    backend_dir = Path(__file__).resolve().parent.parent
    alembic_ini = backend_dir / "alembic.ini"
    alembic_cfg = Config(str(alembic_ini))
    alembic_cfg.set_main_option("script_location", str(backend_dir / "migrations"))

    command.stamp(alembic_cfg, "20260720_02")

    # Column should NOT exist at revision 20260720_02
    with engine.begin() as conn:
        with pytest.raises(Exception):
            conn.execute(text("SELECT guardian_accepted_payload_hash FROM servers LIMIT 1"))

    # Upgrade to beef3761b732 (the specific revision adding guardian_accepted_payload_hash)
    command.upgrade(alembic_cfg, "beef3761b732")

    # Column DOES exist now
    with engine.begin() as conn:
        conn.execute(text("SELECT guardian_accepted_payload_hash FROM servers LIMIT 1"))
