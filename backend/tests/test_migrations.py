"""Tests fuer einzelne Alembic-Migrationen mit Datenumwandlung.

Jeder Test baut sein Schema in einer eigenen, leeren PostgreSQL-Datenbank
(`pg_wegwerf`), stempelt es auf `head`, faehrt auf den Stand vor der
Migration zurueck, legt Bestandszeilen an und prueft Hin- und Rueckweg.
"""
from __future__ import annotations


# ── 20260819_01 — Gedächtnisvektoren von JSON auf float32-Bytes ─────────────


def _memory_migration_config(backend_dir):
    from alembic.config import Config

    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "migrations"))
    return config


def test_stored_vectors_are_repacked_without_the_embedding_model(pg_wegwerf):
    """Der Bestand wechselt das Format, ohne dass jemand rechnen muss.

    Die Umrechnung ist das eigentliche Versprechen dieser Migration: dieselben
    Zahlen, andere Form. Sie muss deshalb ohne das Embeddingmodell auskommen —
    ein Betreiber muss die 507 MB nicht installiert haben, um sein Gedächtnis
    schnell zu bekommen. Der zweite Teil des Versprechens ist die alte Spalte:
    sie bleibt stehen, damit das Panel zwischen Code-Update und diesem Lauf
    nicht blind wird und ein Rückbau möglich bleibt.
    """
    import json
    import struct
    from array import array
    from pathlib import Path

    from alembic import command
    from sqlalchemy import create_engine, inspect, text

    import models  # noqa: F401
    from config import settings
    from database import Base

    db_url = pg_wegwerf("memory-vektor")
    vorher = settings.database_url
    settings.database_url = db_url
    backend_dir = Path(__file__).resolve().parent.parent
    config = _memory_migration_config(backend_dir)
    engine = create_engine(db_url)
    try:
        Base.metadata.create_all(engine)
        command.stamp(config, "head")
        command.downgrade(config, "20260818_04")

        spalten = {c["name"] for c in inspect(engine).get_columns("ai_memory_entries")}
        assert "embedding_bytes" not in spalten, "Vorbedingung: der Stand vor 20260819_01"

        # Drei Bestandszeilen: eine gute, eine mit halbem Vektor und eine ohne.
        # Die beiden letzten dürfen die Migration nicht anhalten — und sie
        # dürfen auch nicht geraten werden.
        werte = [0.5, -0.25, 0.125] + [0.0] * 253
        with engine.begin() as conn:
            for kennung, vektor_json in (
                ("aaa", json.dumps(werte)),
                ("bbb", json.dumps([0.1, 0.2, 0.3])),
                ("ccc", None),
            ):
                conn.execute(
                    text(
                        "INSERT INTO ai_memory_entries "
                        "(id, scope, scope_identity, key, value_encrypted, origin, "
                        " aad_version, use_count, embedding_json, embedding_model, "
                        " created_at, updated_at) "
                        "VALUES (:id, 'user', 'user:1', :key, 'x', 'user', 2, 0, "
                        " :vektor, 'potion-multilingual-128M', "
                        " '2026-08-19 00:00:00', '2026-08-19 00:00:00')"
                    ),
                    {"id": kennung, "key": f"k-{kennung}", "vektor": vektor_json},
                )

        command.upgrade(config, "20260819_01")

        with engine.connect() as conn:
            zeilen = {
                zeile.id: (zeile.embedding_bytes, zeile.embedding_json)
                for zeile in conn.execute(
                    text(
                        "SELECT id, embedding_bytes, embedding_json "
                        "FROM ai_memory_entries"
                    )
                ).fetchall()
            }

        gepackt, alt = zeilen["aaa"]
        gepackt = bytes(gepackt)  # psycopg2 liefert bytea als memoryview
        assert gepackt == struct.pack("<256f", *werte), "andere Zahlen als vorher"
        assert alt is not None, "die alte Spalte trägt den Rückbau und die Übergangszeit"
        assert list(array("f", gepackt)) == werte
        # Ein halber Vektor wird übersprungen, nicht aufgefüllt: geraten
        # wären 253 Nullen, und die beschrieben einen anderen Text.
        assert zeilen["bbb"][0] is None
        assert zeilen["ccc"][0] is None

        command.downgrade(config, "20260818_04")
        with engine.connect() as conn:
            uebrig = conn.execute(
                text("SELECT embedding_json FROM ai_memory_entries WHERE id = 'aaa'")
            ).scalar()
        spalten = {c["name"] for c in inspect(engine).get_columns("ai_memory_entries")}
        assert "embedding_bytes" not in spalten
        assert uebrig is not None, "der Rückbau darf den Vektor nicht mitnehmen"
    finally:
        engine.dispose()
        settings.database_url = vorher


def test_dis_praefix_wird_nur_an_reines_chiffrat_gesetzt(pg_wegwerf):
    """20260926_09: der Altbestand bekommt `msm-dis-v1:`, aber nur dort, wo
    sicher DIS-Chiffrat steht.

    Fernet-Werte (`gAAAAA`) muss `scripts/migrate_to_dis.py` noch erkennen,
    und Klartext-Einstellungen in `panel_settings` sind kein Chiffrat.
    """
    from pathlib import Path

    from alembic import command
    from sqlalchemy import create_engine, text

    import models  # noqa: F401
    from config import settings
    from database import Base

    db_url = pg_wegwerf("dis-praefix")
    vorher = settings.database_url
    settings.database_url = db_url
    config = _memory_migration_config(Path(__file__).resolve().parent.parent)
    engine = create_engine(db_url)
    try:
        Base.metadata.create_all(engine)
        command.stamp(config, "head")
        command.downgrade(config, "20260926_08")
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO panel_settings (key, value, updated_at) VALUES "
                "('smtp_password_encrypted', 'QUJDREVG', '2026-09-26'), "
                "('steam_account_password_enc', 'gAAAAAalt', '2026-09-26'), "
                "('smtp_host', 'mail.example.org', '2026-09-26'), "
                "('github_clone_token_enc', 'msm-dis-v1:schon', '2026-09-26')"
            ))

        command.upgrade(config, "20260926_09")

        with engine.connect() as conn:
            werte = dict(conn.execute(text("SELECT key, value FROM panel_settings")).all())
        assert werte["smtp_password_encrypted"] == "msm-dis-v1:QUJDREVG"
        assert werte["steam_account_password_enc"] == "gAAAAAalt"
        assert werte["smtp_host"] == "mail.example.org"
        assert werte["github_clone_token_enc"] == "msm-dis-v1:schon"

        command.downgrade(config, "20260926_08")

        with engine.connect() as conn:
            werte = dict(conn.execute(text("SELECT key, value FROM panel_settings")).all())
        assert werte["smtp_password_encrypted"] == "QUJDREVG"
        assert werte["github_clone_token_enc"] == "schon"
        assert werte["smtp_host"] == "mail.example.org"
    finally:
        engine.dispose()
        settings.database_url = vorher
