"""Feld username_gewaehlt auf users: hat der Mensch seinen Namen selbst gewaehlt?

Bis 09/2026 bekamen Social-Login- und Hoster-Shop-Konten einen Namen, den
niemand gewaehlt hat: bei Google die E-Mail ohne Sonderzeichen
(``mauntingstudiosgmailcom``), im Shop ``<slug>-kunde_3``. Solche Konten
sehen beim naechsten Oeffnen des Panels einmal die Seite "Benutzername
waehlen". Wer ueber Registrierung, Einrichtung oder Admin angelegt wurde,
hat seinen Namen getippt und bleibt unberuehrt.

Erkannt werden die Konten wie in 20260927_02: am Audit-Eintrag
``oauth_user.registered``, dazu jede Zeile in ``hoster_identities``.

Revision ID: 20260928_02
Revises: 20260928_01
Create Date: 2026-09-28
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260928_02"
down_revision: Union[str, None] = "20260928_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {col["name"] for col in sa.inspect(bind).get_columns("users")}
    if "username_gewaehlt" not in columns:
        op.add_column(
            "users",
            sa.Column(
                "username_gewaehlt",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            ),
        )

    tabellen = set(sa.inspect(bind).get_table_names())
    quellen = []
    if "audit_logs" in tabellen:
        quellen.append("SELECT user_id FROM audit_logs WHERE action = 'oauth_user.registered'")
    if "hoster_identities" in tabellen:
        quellen.append("SELECT user_id FROM hoster_identities")
    if quellen:
        bind.execute(
            sa.text(
                "UPDATE users SET username_gewaehlt = :falsch "
                f"WHERE id IN ({' UNION '.join(quellen)})"
            ),
            {"falsch": False},
        )


def downgrade() -> None:
    bind = op.get_bind()
    columns = {col["name"] for col in sa.inspect(bind).get_columns("users")}
    if "username_gewaehlt" in columns:
        op.drop_column("users", "username_gewaehlt")
