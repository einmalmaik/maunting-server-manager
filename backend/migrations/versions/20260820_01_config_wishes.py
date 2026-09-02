"""Dauerhafte Konfigurationswerte je Server.

Revision ID: 20260820_01
Revises: 20260819_01
Create Date: 2026-08-20

Eine Aenderung an einer Konfigurationsdatei ist nur so dauerhaft, wie der
Prozess sie laesst, dem die Datei gehoert. Gemessen auf Server 107
(15.–19.08.2026): ein ausgefuehrter Vorschlag stand vier Tage spaeter nicht
mehr in der Datei — ARK schreibt ``GameUserSettings.ini`` beim Autosave
vollstaendig neu und verwirft dabei, was der laufende Prozess nicht kennt.

Diese Spalte haelt den *Wunsch* fest, nicht nur das Ergebnis: ``prepare_runtime``
schreibt ihn vor jedem Start erneut in die Datei. Damit muss niemand wissen,
welches Spiel seine Konfiguration zurueckschreibt — eine solche Liste muesste
gepflegt werden und wuerde beim ersten vergessenen Eintrag still wieder Werte
verlieren.

``nullable=True`` ohne Default und ohne Backfill: bestehende Server haben keine
Wuensche, und ein leerer Speicher ist genau die richtige Aussage ueber sie. Die
Migration fasst keine vorhandenen Daten an.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260820_01"
down_revision: Union[str, None] = "20260819_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "servers",
        sa.Column("config_wishes_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    # Die Werte in den Dateien bleiben stehen; nur die Durchsetzung bei
    # kuenftigen Starts entfaellt. Das ist kein Datenverlust am Spielstand,
    # aber die Wuensche selbst sind danach weg.
    op.drop_column("servers", "config_wishes_json")
