"""Den API-Schluessel stellt der Betreiber, nicht der Nutzer.

Revision ID: 20260810_04
Revises: 20260810_03
Create Date: 2026-08-10

MSM konnte bisher beides: der Betreiber hinterlegt einen Schluessel je Provider,
und zusaetzlich durfte jeder Benutzer einen eigenen mitbringen (BYOK).
`resolve_api_key` nahm den Benutzerschluessel **vor** dem des Betreibers.

Fuer ein Panel, das ein Hoster betreibt, ist das der falsche Weg herum. Der Kunde
zahlt fuer den Dienst; laesst man ihn einen eigenen Schluessel hinterlegen,
entsteht ein zweiter Abrechnungspfad neben dem, den der Betreiber kalkuliert hat
— und der Betreiber kann die Funktion nicht mehr als seine anbieten. Ausdrueckliche
Entscheidung des Betreibers: Schluessel, Modell und Providerkonfiguration liegen
bei ihm.

Die Tabelle faellt mitsamt Inhalt. Die darin liegenden Schluessel sind
DIS-verschluesselt und wuerden ab sofort nie wieder gelesen; sie liegenzulassen
hiesse, Geheimnisse ohne Zweck aufzubewahren. `downgrade` legt die Struktur leer
wieder an — die Schluessel selbst kaeme niemand zurueck, und das soll die
Migration auch nicht vorgaukeln.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260810_04"
down_revision: Union[str, None] = "20260810_03"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("ai_user_credentials")


def downgrade() -> None:
    op.create_table(
        "ai_user_credentials",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "provider_id",
            sa.Integer(),
            sa.ForeignKey("ai_providers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("api_key_encrypted", sa.String(length=4096), nullable=False),
        sa.Column("api_key_hint", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "user_id", "provider_id", name="uq_ai_user_credentials_user_provider"
        ),
    )
    op.create_index(
        "ix_ai_user_credentials_user_id", "ai_user_credentials", ["user_id"]
    )
    op.create_index(
        "ix_ai_user_credentials_provider_id", "ai_user_credentials", ["provider_id"]
    )
