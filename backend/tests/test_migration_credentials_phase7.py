"""Rolling-Upgrade-Test fuer die getrennten Zugangsdaten (Phase 7)."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

import models  # noqa: F401
from config import settings
from database import Base


CREDENTIAL_TABLES = {"user_credentials", "server_credential_bindings"}


def test_scoped_credentials_migration_roundtrip(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'credentials-phase7.db'}"
    previous_database_url = settings.database_url
    settings.database_url = db_url
    engine = create_engine(db_url)
    backend_dir = Path(__file__).resolve().parent.parent
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "migrations"))
    try:
        Base.metadata.create_all(engine)
        command.stamp(config, "20260807_03")

        # Downgrade: eine Installation ohne eigene Zugangsdaten bleibt nutzbar.
        command.downgrade(config, "20260807_02")
        before = set(inspect(engine).get_table_names())
        assert not (CREDENTIAL_TABLES & before)
        assert "hoster_integrations" in before

        command.upgrade(config, "20260807_03")
        inspector = inspect(engine)
        assert CREDENTIAL_TABLES.issubset(set(inspector.get_table_names()))

        columns = {c["name"] for c in inspector.get_columns("user_credentials")}
        # Nur Ciphertext und ein nicht umkehrbarer Hinweis — nie das Geheimnis.
        assert {"secret_encrypted", "secret_hint", "kind", "label"}.issubset(columns)
        assert "secret" not in columns
        assert "password" not in columns

        labels = {
            tuple(item["column_names"])
            for item in inspector.get_unique_constraints("user_credentials")
        }
        assert ("user_id", "kind", "label") in labels

        binding = inspector.get_columns("server_credential_bindings")
        binding_names = {c["name"] for c in binding}
        assert {"server_id", "kind", "credential_id"}.issubset(binding_names)
        # Die Bindung verweist auf das Credential, statt seinen Wert zu kopieren.
        assert "secret_encrypted" not in binding_names
    finally:
        engine.dispose()
        settings.database_url = previous_database_url
