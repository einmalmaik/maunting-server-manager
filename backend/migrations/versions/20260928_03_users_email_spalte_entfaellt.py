"""Die alte Spalte ``users.email`` entfaellt.

Vor DIS stand dort die Adresse im Klartext. Seitdem steht sie verschluesselt
in ``email_encrypted``, und ``email`` trug nur noch eine Kopie von
``email_hash``: zwei Spalten mit demselben Wert, von denen nur eine gelesen
wurde.

Eine Zeile mit Adresse (``@``) und ohne Chiffrat verloere beim Entfernen ihre
Adresse. Dann bricht die Migration mit einer Meldung ab, statt still zu
loeschen. Beim Panelstart verschluesselt ``services/email_altbestand.py``
solche Zeilen **vor** den Migrationen; der Abbruch trifft also nur, wer
Alembic von Hand ohne Sidecar faehrt.

``email_altbestand`` schreibt die rohe Sidecar-Form ``msm-dis-v1:`` (siehe
dort). Lief er erst auf 20260928_02, hat 20260928_01 sie nicht mehr gesehen;
deshalb stellt diese Migration uebrig gebliebene Werte auf ``msm-email-v1:``
um, genau wie 20260928_01.

``downgrade`` legt die Spalte wieder an und fuellt sie wie frueher mit dem
Pruefwert. Die Klartext-Adressen von damals kommen nicht zurueck.

Revision ID: 20260928_03
Revises: 20260928_02
Create Date: 2026-09-28
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260928_03"
down_revision: Union[str, None] = "20260928_02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DIS = "msm-dis-v1:"
EMAIL = "msm-email-v1:"


def _spalten() -> set[str]:
    return {s["name"] for s in sa.inspect(op.get_bind()).get_columns("users")}


def upgrade() -> None:
    if "email" not in _spalten():
        return
    bind = op.get_bind()
    offen = bind.execute(
        sa.text("SELECT count(*) FROM users WHERE email_encrypted IS NULL AND email LIKE '%@%'")
    ).scalar()
    if offen:
        raise RuntimeError(
            f"{offen} Konto/Konten tragen die E-Mail-Adresse noch im Klartext in users.email "
            "und nicht verschluesselt. Die Spalte wird nicht entfernt, sonst gingen die "
            "Adressen verloren. Das Panel verschluesselt sie beim naechsten Start vor den "
            "Migrationen (Sidecar muss laufen); danach laeuft diese Migration durch."
        )
    laenge = next(
        (getattr(s["type"], "length", None) for s in sa.inspect(bind).get_columns("users") if s["name"] == "email_encrypted"),
        None,
    )
    passt = f" AND length(email_encrypted) <= {laenge - len(EMAIL) + len(DIS)}" if laenge else ""
    bind.execute(
        sa.text(
            f"UPDATE users SET email_encrypted = :email || substr(email_encrypted, {len(DIS) + 1}) "
            f"WHERE email_encrypted LIKE :dis{passt}"
        ),
        {"email": EMAIL, "dis": f"{DIS}%"},
    )
    for index in sa.inspect(bind).get_indexes("users"):
        if index["column_names"] == ["email"]:
            op.drop_index(index["name"], table_name="users")
    for bedingung in sa.inspect(bind).get_unique_constraints("users"):
        if bedingung["column_names"] == ["email"]:
            op.drop_constraint(bedingung["name"], "users", type_="unique")
    op.drop_column("users", "email")


def downgrade() -> None:
    if "email" in _spalten():
        return
    op.add_column("users", sa.Column("email", sa.String(255), nullable=True))
    op.get_bind().execute(sa.text("UPDATE users SET email = email_hash"))
    op.create_index("ix_users_email", "users", ["email"], unique=True)
