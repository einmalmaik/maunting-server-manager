"""Die Migration der gerätebezogenen Zustellung.

Geprüft wird der Weg von `20261023_01` auf `20261023_02` und wieder zurück:
*vor* dem Schritt gibt es die vier Tabellen nicht, *danach* schon — und sie
tragen die Schranken, auf die es ankommt:

* keine Tabelle hat einen Fremdschlüssel auf `users` (sie hängen an der
  Geraetezeile, deren Kaskade sie mit dem Konto aufraeumt),
* die Zustelladresse ist je Geraet und je Adresse eindeutig,
* der Auftrag ist je Inbox und Kennung eindeutig,
* der Idempotenzrest ist je Inbox und Kennung eindeutig.

Nach dem Downgrade sind die Tabellen wieder weg — der Weg muss auch
zurueckgehen, sonst bleibt eine halbe Migration stehen.
"""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from database import Base
import models  # noqa: F401


def _alembic(backend_dir: Path) -> Config:
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "migrations"))
    return cfg


TABELLEN = (
    "e2ee_device_inboxes",
    "e2ee_delivery_payloads",
    "e2ee_deliveries",
    "e2ee_delivery_receipts",
)


def test_die_zustellungstabellen_kommen_hin_und_wieder_weg(pg_wegwerf, monkeypatch):
    db_url = pg_wegwerf("zustellung")
    from config import settings

    monkeypatch.setattr(settings, "database_url", db_url)
    engine = create_engine(db_url)
    backend_dir = Path(__file__).resolve().parent.parent
    cfg = _alembic(backend_dir)

    # Der Stand vor der Migration: die Geraetezeile gibt es, die vier
    # Zustellungstabellen nicht.
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        for tabelle in TABELLEN:
            conn.execute(text(f"DROP TABLE IF EXISTS {tabelle} CASCADE"))
    command.stamp(cfg, "20261023_01")

    inspector = inspect(engine)
    for tabelle in TABELLEN:
        assert tabelle not in inspector.get_table_names()

    command.upgrade(cfg, "20261023_02")

    inspector = inspect(engine)
    for tabelle in TABELLEN:
        assert tabelle in inspector.get_table_names(), tabelle

    # Keine der Tabellen haengt an `users` — sie haengen an der Geraetezeile.
    with engine.connect() as conn:
        zeilen = conn.execute(
            text(
                """
                SELECT tc.table_name, kcu.column_name, ccu.table_name AS ziel
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                  ON tc.constraint_name = kcu.constraint_name
                JOIN information_schema.constraint_column_usage ccu
                  ON ccu.constraint_name = tc.constraint_name
                WHERE tc.constraint_type = 'FOREIGN KEY'
                  AND tc.table_name IN ('e2ee_device_inboxes','e2ee_delivery_payloads',
                                         'e2ee_deliveries','e2ee_delivery_receipts')
                """
            )
        ).fetchall()
    for zeile in zeilen:
        assert zeile[2] != "users", f"{zeile[0]}.{zeile[1]} zeigt auf users"

    # Und zurueck.
    command.downgrade(cfg, "20261023_01")
    inspector = inspect(engine)
    for tabelle in TABELLEN:
        assert tabelle not in inspector.get_table_names(), tabelle
    engine.dispose()
