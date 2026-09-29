"""Mehrere zweite Faktoren gleichzeitig: TOTP und beliebig viele Passkeys.

Bis hierher hatte ein Konto genau einen zweiten Faktor. Ob TOTP oder Passkey,
leitete ``User.two_factor_method`` ab: aktiv mit Geheimnis hiess App, aktiv
ohne hiess Passkey. Jetzt koennen beide nebeneinander gelten.

- ``users.two_factor_secret_pending_encrypted``: ein Geheimnis in Einrichtung.
  Bisher schrieb ``/2fa/setup`` direkt in ``two_factor_secret_encrypted``; das
  ging nur, weil 2FA dabei aus sein musste. Neben einem Passkey waere ein
  unbestaetigtes Geheimnis dort sofort gueltig.
- ``users.two_factor_last_method``: welcher Faktor beim Login zuerst kommt.
- ``user_passkeys.name``: damit man mehrere Passkeys auseinanderhaelt
  (``DisText``, also nur verschluesselt).

Aufgeraeumt wird, was unter der alten Regel liegen blieb: ein Geheimnis bei
abgeschalteter 2FA (Admin-Abschalten liess es stehen, ``/2fa/setup`` ohne
Abschluss ebenso) und ein ``two_factor_enabled`` ohne jeden Faktor.

Revision ID: 20260929_01
Revises: 20260928_03
Create Date: 2026-09-29
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260929_01"
down_revision: Union[str, None] = "20260928_03"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _spalten(tabelle: str) -> set[str]:
    return {s["name"] for s in sa.inspect(op.get_bind()).get_columns(tabelle)}


def upgrade() -> None:
    users = _spalten("users")
    if "two_factor_secret_pending_encrypted" not in users:
        op.add_column("users", sa.Column("two_factor_secret_pending_encrypted", sa.String(255), nullable=True))
    if "two_factor_last_method" not in users:
        op.add_column("users", sa.Column("two_factor_last_method", sa.String(16), nullable=True))
    if "name" not in _spalten("user_passkeys"):
        op.add_column("user_passkeys", sa.Column("name", sa.Text(), nullable=True))

    bind = op.get_bind()
    bind.execute(sa.text(
        "UPDATE users SET two_factor_secret_encrypted = NULL "
        "WHERE two_factor_enabled = false AND two_factor_secret_encrypted IS NOT NULL"
    ))
    bind.execute(sa.text(
        "UPDATE users SET two_factor_enabled = false "
        "WHERE two_factor_enabled = true AND two_factor_secret_encrypted IS NULL "
        "AND NOT EXISTS (SELECT 1 FROM user_passkeys p WHERE p.user_id = users.id)"
    ))


def downgrade() -> None:
    # Die alte Regel kennt TOTP neben Passkeys nicht: aktiv mit Geheimnis
    # heisst dort App. Wer beides hat, behaelt im alten Stand die App.
    if "name" in _spalten("user_passkeys"):
        op.drop_column("user_passkeys", "name")
    users = _spalten("users")
    if "two_factor_last_method" in users:
        op.drop_column("users", "two_factor_last_method")
    if "two_factor_secret_pending_encrypted" in users:
        op.drop_column("users", "two_factor_secret_pending_encrypted")
