"""Jede Migration dieses Branches muss ihr `upgrade()` wirklich ausfuehren.

Die bestehenden Migrationstests bauen das Schema per
`Base.metadata.create_all()` auf und setzen danach nur einen Stempel. Sie
belegen damit, dass die *Modelle* zusammenpassen — nicht, dass die Migrationen
laufen. Von den neun Migrationen dieses Branches wurde das `upgrade()` zweier
Migrationen in keinem Test je ausgefuehrt.

Ein Lauf von `alembic upgrade head` auf einer **leeren** Datenbank ist bewusst
nicht der Pruefpunkt: `20260716_01` ist eine No-op-Baseline. Neue Installationen
erzeugen das Schema aus den Modellen und werden dann gestempelt; erst danach
tragen die Migrationen echte Deltas. Der Pruefpunkt ist deshalb der Weg, den ein
Betreiber tatsaechlich geht — von der letzten Revision vor diesem Branch bis zum
Head und zurueck.
"""

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect

import models  # noqa: F401
from config import settings
from database import Base


# Letzte Revision auf `main`. Alles danach ist neu in diesem Branch.
BASE_REVISION = "20260731_01"

BRANCH_TABLES = (
    "user_roles",
    "role_ai_limits",
    "operation_tasks",
    "ai_providers",
    "ai_conversations",
    "ai_action_proposals",
    "ai_memory_entries",
    "ai_skills",
    "hoster_integrations",
    "user_credentials",
    "ai_autonomy_grants",
    "ai_tool_results",
    "ai_tasks",
)


def _config(backend_dir: Path) -> Config:
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "migrations"))
    return config


def test_every_branch_migration_runs_both_ways(tmp_path: Path) -> None:
    """Downgrade auf den Stand von main und wieder hoch — echte DDL, kein Stempel."""
    db_url = f"sqlite:///{tmp_path / 'chain.db'}"
    previous = settings.database_url
    settings.database_url = db_url
    backend_dir = Path(__file__).resolve().parent.parent
    config = _config(backend_dir)
    engine = create_engine(db_url)
    try:
        Base.metadata.create_all(engine)
        command.stamp(config, "head")

        command.downgrade(config, BASE_REVISION)
        after_downgrade = set(inspect(engine).get_table_names())
        assert not (set(BRANCH_TABLES) & after_downgrade), (
            "Diese Tabellen ueberleben den Downgrade: "
            + ", ".join(sorted(set(BRANCH_TABLES) & after_downgrade))
        )
        # Die Basis muss stehen bleiben — ein Downgrade darf keine Nutzdaten
        # eines Betreibers mitnehmen, der die neuen Funktionen nie genutzt hat.
        assert {"users", "servers", "roles"}.issubset(after_downgrade)

        command.upgrade(config, "head")
        after_upgrade = set(inspect(engine).get_table_names())
        for table in BRANCH_TABLES:
            assert table in after_upgrade, f"Tabelle {table} fehlt nach `upgrade head`"
        # Spalten, die eine spaetere Migration nachtraegt: eine fehlende Tabelle
        # faellt sofort auf, eine fehlende Spalte erst im Betrieb.
        assert "run_id" in {
            spalte["name"] for spalte in inspect(engine).get_columns("ai_tool_results")
        }, "ai_tool_results.run_id fehlt nach `upgrade head`"
    finally:
        engine.dispose()
        settings.database_url = previous


def test_the_chain_has_exactly_one_head() -> None:
    """Zwei Heads waeren im Betrieb ein harter Fehler, kein Schoenheitsfehler."""
    backend_dir = Path(__file__).resolve().parent.parent
    script = ScriptDirectory.from_config(_config(backend_dir))

    heads = script.get_heads()

    assert len(heads) == 1, f"Die Migrationskette hat {len(heads)} Heads: {heads}"


def test_every_revision_is_reachable_from_the_head() -> None:
    """Eine Revision ohne Weg zum Head laeuft bei keinem Betreiber je."""
    backend_dir = Path(__file__).resolve().parent.parent
    script = ScriptDirectory.from_config(_config(backend_dir))

    all_revisions = {revision.revision for revision in script.walk_revisions()}
    reachable = {
        revision.revision
        for revision in script.iterate_revisions(script.get_heads()[0], "base")
    }

    assert all_revisions == reachable, (
        "Nicht erreichbare Revisionen: " + ", ".join(sorted(all_revisions - reachable))
    )


def test_downgrade_of_the_newest_revision_is_reversible(tmp_path: Path) -> None:
    """Ein Rollback muss moeglich sein, sonst ist ein Fehlschlag endgueltig."""
    db_url = f"sqlite:///{tmp_path / 'downgrade.db'}"
    previous = settings.database_url
    settings.database_url = db_url
    backend_dir = Path(__file__).resolve().parent.parent
    config = _config(backend_dir)
    engine = create_engine(db_url)
    try:
        Base.metadata.create_all(engine)
        command.stamp(config, "head")
        command.downgrade(config, "20260807_03")

        tables = set(inspect(engine).get_table_names())
        assert "ai_autonomy_grants" not in tables
        assert "ai_tool_results" not in tables
        columns = {c["name"] for c in inspect(engine).get_columns("ai_action_proposals")}
        assert "autonomous" not in columns

        command.upgrade(config, "head")
        columns = {c["name"] for c in inspect(engine).get_columns("ai_action_proposals")}
        assert {"autonomous", "reason", "expected_effect"}.issubset(columns)
    finally:
        engine.dispose()
        settings.database_url = previous
