"""Rolling-Upgrade-Test fuer Teams, Team-Memory und das Audit-Ziel als Text.

Die Migrationstests laufen auf SQLite, das Panel auf PostgreSQL. Diese Datei
prueft deshalb vor allem, was auf beiden gleich sein muss: dass Tabellen und
Spalten entstehen, dass der CHECK-Constraint den neuen Scope kennt und dass ein
Rueckbau die Datenbank in den vorherigen Zustand versetzt.
"""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

import models  # noqa: F401
from config import settings
from database import Base


def _config(backend_dir: Path) -> Config:
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "migrations"))
    return config


def test_teams_migration_roundtrip(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'ai-phase6.db'}"
    previous_database_url = settings.database_url
    settings.database_url = db_url
    engine = create_engine(db_url)
    backend_dir = Path(__file__).resolve().parent.parent
    config = _config(backend_dir)
    try:
        Base.metadata.create_all(engine)
        command.stamp(config, "20260809_02")
        command.downgrade(config, "20260808_04")

        names = set(inspect(engine).get_table_names())
        assert "teams" not in names
        assert "team_members" not in names
        assert "team_server_grants" not in names

        command.upgrade(config, "20260809_02")
        inspector = inspect(engine)
        names = set(inspector.get_table_names())
        assert {"teams", "team_members", "team_server_grants"}.issubset(names)

        # Genau ein persoenliches Team je Benutzer — die Eindeutigkeit steckt
        # in der Spalte, nicht in Anwendungscode.
        unique = {item["name"] for item in inspector.get_unique_constraints("teams")}
        assert "uq_teams_personal_for_user" in unique

        member_unique = {
            item["name"] for item in inspector.get_unique_constraints("team_members")
        }
        assert "uq_team_members_team_user" in member_unique

        grant_unique = {
            item["name"] for item in inspector.get_unique_constraints("team_server_grants")
        }
        assert "uq_team_server_grants" in grant_unique
    finally:
        engine.dispose()
        settings.database_url = previous_database_url


def test_memory_gains_team_scope_and_bound_encryption(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'ai-phase6-memory.db'}"
    previous_database_url = settings.database_url
    settings.database_url = db_url
    engine = create_engine(db_url)
    backend_dir = Path(__file__).resolve().parent.parent
    config = _config(backend_dir)
    try:
        Base.metadata.create_all(engine)
        command.stamp(config, "20260809_02")
        command.downgrade(config, "20260808_04")

        columns = {c["name"] for c in inspect(engine).get_columns("ai_memory_entries")}
        assert "team_id" not in columns
        assert "aad_version" not in columns

        command.upgrade(config, "20260809_02")
        inspector = inspect(engine)
        columns = {c["name"] for c in inspector.get_columns("ai_memory_entries")}
        assert {"team_id", "aad_version"}.issubset(columns)

        # Der Scope-CHECK muss "team" erlauben — sonst schluege jeder
        # Team-Eintrag zur Laufzeit fehl statt hier.
        checks = {
            item["name"]: item["sqltext"]
            for item in inspector.get_check_constraints("ai_memory_entries")
        }
        assert "ck_ai_memory_entries_scope" in checks
        assert "team" in checks["ck_ai_memory_entries_scope"]
    finally:
        engine.dispose()
        settings.database_url = previous_database_url


def test_skills_table_is_rebuilt_for_prose(tmp_path: Path) -> None:
    """Aus der Makro-Tabelle wird eine fuer Text.

    Der Rueckbau stellt die alte Fassung wieder her, damit ein Downgrade das
    Panel nicht mit einer Tabelle zurueecklaesst, die der aeltere Code nicht
    kennt. Daten wandern in keine Richtung: es gibt keine sinnvolle
    Uebersetzung zwischen einer Aufrufliste und Fliesstext.
    """
    db_url = f"sqlite:///{tmp_path / 'ai-phase6-skills.db'}"
    previous_database_url = settings.database_url
    settings.database_url = db_url
    engine = create_engine(db_url)
    backend_dir = Path(__file__).resolve().parent.parent
    config = _config(backend_dir)
    try:
        Base.metadata.create_all(engine)
        command.stamp(config, "20260809_04")
        command.downgrade(config, "20260809_03")

        columns = {c["name"] for c in inspect(engine).get_columns("ai_skills")}
        assert "steps_json" in columns
        assert "body" not in columns

        command.upgrade(config, "20260809_04")
        inspector = inspect(engine)
        columns = {c["name"] for c in inspector.get_columns("ai_skills")}
        assert {"body", "scope_identity", "team_id", "origin", "status"}.issubset(columns)
        assert "steps_json" not in columns

        unique = {item["name"] for item in inspector.get_unique_constraints("ai_skills")}
        assert "uq_ai_skills_scope_key" in unique
        checks = {item["name"] for item in inspector.get_check_constraints("ai_skills")}
        assert {"ck_ai_skills_origin", "ck_ai_skills_status"}.issubset(checks)
    finally:
        engine.dispose()
        settings.database_url = previous_database_url


def test_audit_target_id_becomes_text(tmp_path: Path) -> None:
    """Der Fehler, an dem `remember` auf PostgreSQL scheiterte.

    Seit Phase C uebergeben Memory, Skills und Anhaenge UUIDs als Ziel-ID. Die
    Spalte war `INTEGER`. SQLite speichert einen String dort klaglos, weshalb
    die Testsuite nichts gemerkt hat — PostgreSQL weist ihn ab, und damit
    scheiterte im Betrieb jeder Schreibvorgang ins Gedaechtnis.
    """
    db_url = f"sqlite:///{tmp_path / 'ai-phase6-audit.db'}"
    previous_database_url = settings.database_url
    settings.database_url = db_url
    engine = create_engine(db_url)
    backend_dir = Path(__file__).resolve().parent.parent
    config = _config(backend_dir)
    try:
        Base.metadata.create_all(engine)
        command.stamp(config, "20260809_02")
        command.downgrade(config, "20260808_04")
        command.upgrade(config, "20260809_02")

        column = next(
            item for item in inspect(engine).get_columns("audit_logs")
            if item["name"] == "target_id"
        )
        assert "CHAR" in str(column["type"]).upper() or "TEXT" in str(column["type"]).upper()
    finally:
        engine.dispose()
        settings.database_url = previous_database_url
