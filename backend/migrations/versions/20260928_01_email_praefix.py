"""Eigenes Praefix fuer ``users.email_encrypted``: ``msm-email-v1:``.

Bisher stand dort ``msm-dis-v1:`` (seit 20260926_09) oder, auf aelteren
Staenden, gar nichts. Dahinter bleibt dasselbe DIS-Chiffrat; nur das Praefix
sagt in der Tabelle jetzt, dass es eine E-Mail ist. ``models/user.py`` setzt
vor dem Entschluesseln wieder ``msm-dis-v1:`` davor. Deshalb braucht es keinen
Sidecar, und ``downgrade`` tauscht das Praefix einfach zurueck.

Nicht angefasst werden Fernet-Werte (``gAAAAA``), die ``scripts/migrate_to_dis.py``
noch erkennen muss, und Werte, die mit dem laengeren Praefix nicht mehr in
die Spalte passten. Sie bekommen es beim naechsten Speichern.

Revision ID: 20260928_01
Revises: 20260927_02
Create Date: 2026-09-28
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260928_01"
down_revision: Union[str, None] = "20260927_02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EMAIL = "msm-email-v1:"
DIS = "msm-dis-v1:"


def _laenge() -> int | None:
    for spalte in sa.inspect(op.get_bind()).get_columns("users"):
        if spalte["name"] == "email_encrypted":
            return getattr(spalte["type"], "length", None) or 0
    return None


def upgrade() -> None:
    laenge = _laenge()
    if laenge is None:
        return
    conn = op.get_bind()
    passt = f" AND length(email_encrypted) <= {laenge - len(EMAIL)}" if laenge else ""
    conn.execute(
        sa.text(
            f"UPDATE users SET email_encrypted = :email || substr(email_encrypted, {len(DIS) + 1}) "
            f"WHERE email_encrypted LIKE :dis{passt}"
        ),
        {"email": EMAIL, "dis": f"{DIS}%"},
    )
    conn.execute(
        sa.text(
            "UPDATE users SET email_encrypted = :email || email_encrypted "
            "WHERE email_encrypted IS NOT NULL AND email_encrypted <> '' "
            f"AND email_encrypted NOT LIKE 'msm-%' AND email_encrypted NOT LIKE 'gAAAAA%'{passt}"
        ),
        {"email": EMAIL},
    )


def downgrade() -> None:
    if _laenge() is None:
        return
    op.get_bind().execute(
        sa.text(
            f"UPDATE users SET email_encrypted = :dis || substr(email_encrypted, {len(EMAIL) + 1}) "
            "WHERE email_encrypted LIKE :email"
        ),
        {"dis": DIS, "email": f"{EMAIL}%"},
    )
