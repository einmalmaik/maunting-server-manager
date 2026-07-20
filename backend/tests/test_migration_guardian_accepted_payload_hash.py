import pytest
from sqlalchemy import text
from alembic.config import Config
from alembic import command
import os
from pathlib import Path

def test_guardian_accepted_payload_hash_migration(tmp_path):
    """
    Testet das Upgrade von base/head_without_this_migration zu head.
    Prüft, ob die Spalte 'guardian_accepted_payload_hash' vor dem Upgrade

    nicht existiert und danach existiert.
    """
    from sqlalchemy import create_engine
    from services.schema_manager import initialize_or_upgrade_schema
    import os
    
    db_path = tmp_path / "test_migration.db"
    db_url = f"sqlite:///{db_path}"
    os.environ["DATABASE_URL"] = db_url
    
    # Reload settings to pick up new DATABASE_URL
    from config import settings
    settings.database_url = db_url
    
    engine = create_engine(db_url)
    initialize_or_upgrade_schema(engine)
    
    backend_dir = Path(__file__).resolve().parent.parent
    alembic_ini = backend_dir / "alembic.ini"
    alembic_cfg = Config(str(alembic_ini))
    alembic_cfg.set_main_option("script_location", str(backend_dir / "migrations"))

    # We are at "head". Let's downgrade to the previous revision to test removal.
    command.downgrade(alembic_cfg, "20260720_02")

    # Now test the column does NOT exist
    with engine.begin() as conn:
        with pytest.raises(Exception):
            conn.execute(text("SELECT guardian_accepted_payload_hash FROM servers LIMIT 1"))
        
    # Upgrade again to our new revision
    command.upgrade(alembic_cfg, "head")
    
    # Now test the column DOES exist (should not raise)
    with engine.begin() as conn:
        conn.execute(text("SELECT guardian_accepted_payload_hash FROM servers LIMIT 1"))
