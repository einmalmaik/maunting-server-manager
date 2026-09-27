"""Feld has_password auf users: unterscheidet Passwort- von reinen OAuth-Konten.

Bei der OAuth-Registrierung wird bisher ein zufälliger Hashwert abgelegt.
Nutzer mit reiner OAuth-Registrierung haben daher kein bekanntes Passwort und
sollen im Profil die Option „Passwort festlegen“ statt „Passwort ändern“
angezeigt bekommen.

Revision ID: 20260927_02
Revises: 20260927_01
Create Date: 2026-09-27
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260927_02"
down_revision: Union[str, None] = "20260927_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {col["name"] for col in sa.inspect(bind).get_columns("users")}
    if "has_password" not in columns:
        op.add_column(
            "users",
            sa.Column(
                "has_password",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            ),
        )

    # Bestandskonten: Wer ausschließlich über OAuth registriert wurde und
    # nie sein Passwort geändert hat, erhält has_password = False.
    tabellen = set(sa.inspect(bind).get_table_names())
    if "audit_logs" in tabellen and "oauth_user_links" in tabellen:
        # Finde Benutzer, die eine oauth_user.registered Aktion haben
        # und keine nachfolgende auth.password.change oder auth.password.set Aktion.
        bind.execute(
            sa.text(
                """
                UPDATE users
                SET has_password = :falsch
                WHERE id IN (
                    SELECT DISTINCT a.user_id
                    FROM audit_logs a
                    JOIN oauth_user_links l ON l.user_id = a.user_id
                    WHERE a.action = 'oauth_user.registered'
                    AND a.user_id NOT IN (
                        SELECT DISTINCT user_id
                        FROM audit_logs
                        WHERE action IN ('auth.password.change', 'auth.password.set')
                    )
                )
                """
            ),
            {"falsch": False},
        )


def downgrade() -> None:
    bind = op.get_bind()
    columns = {col["name"] for col in sa.inspect(bind).get_columns("users")}
    if "has_password" in columns:
        op.drop_column("users", "has_password")
