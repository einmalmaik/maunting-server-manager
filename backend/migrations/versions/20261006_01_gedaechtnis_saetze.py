"""Gedaechtnis v2, Stufe 1: Saetze statt Schluessel.

Eine Erinnerung war bis hierher ein Paar aus Name und Wert, und der Name war
ihre Identitaet. Dieselbe Sache unter zwei Namen stand zweimal da, und eine als
Erzaehlung geschriebene Biografie liess sich in Namen gar nicht fassen.
Betreiberentscheid vom 06.10.2026: eine Erinnerung ist ein bis fuenf Saetze,
die ohne das Gespraech verstaendlich sind, mit Titel, Thema, Art, Quelle und
Wichtigkeit daneben.

- ``ai_memory_entries`` bekommt die neuen Spalten. ``key_encrypted`` wird
  nullbar: neue Zeilen haben keinen Namen mehr, alte behalten ihn.
- ``ai_memory_themen`` haelt die Themen je Bereich, Name verschluesselt,
  eindeutig ueber einen HMAC-Index.
- ``ai_memory_versionen`` haelt fruehere Fassungen: jede Aenderung legt den
  Stand davor ab, damit sich zurueckholen laesst, was die Pflege
  zusammenfuehrt oder umschreibt.

Was die KI bisher selbst gemerkt hat (``origin='ai'``), stammt aus einem
Gespraech und bekommt die Quelle ``gespraech``; alles andere
``eingetragen``.

Der Downgrade gibt namenlosen Zeilen einen Namen im Klartext
(``erinnerung-<id>``). Den verschluesselt der alte Stand beim Start selbst
(``schluessel_nachziehen``), genau wie Altbestand. Vergessene Zeilen fallen
dabei weg: der alte Stand kennt kein ``vergessen`` und zeigte sie sonst
wieder an, obwohl der Mensch sie loswerden wollte.

Revision ID: 20261006_01
Revises: 20261005_01
Create Date: 2026-10-06
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261006_01"
down_revision: Union[str, None] = "20261005_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ARTEN = "'fakt', 'vorliebe', 'anweisung', 'ereignis', 'plan', 'beziehung', 'wissen'"
GRUENDE = (
    "'bearbeitet', 'aktualisiert', 'zusammengefuehrt', 'aufgenommen', "
    "'umgeschrieben', 'wiederhergestellt'"
)


def _tabellen() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _spalten(tabelle: str) -> set[str]:
    return {s["name"] for s in sa.inspect(op.get_bind()).get_columns(tabelle)}


def _pruefungen(tabelle: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_check_constraints(tabelle)}


def _indizes(tabelle: str) -> set[str]:
    return {i["name"] for i in sa.inspect(op.get_bind()).get_indexes(tabelle)}


def upgrade() -> None:
    vorhanden = _tabellen()

    if "ai_memory_themen" not in vorhanden:
        op.create_table(
            "ai_memory_themen",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("scope_identity", sa.String(length=128), nullable=False),
            sa.Column(
                "owner_user_id", sa.Integer(),
                sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True,
            ),
            sa.Column(
                "server_id", sa.Integer(),
                sa.ForeignKey("servers.id", ondelete="CASCADE"), nullable=True,
            ),
            sa.Column(
                "team_id", sa.Integer(),
                sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=True,
            ),
            sa.Column("name_encrypted", sa.Text(), nullable=False),
            sa.Column("name_index", sa.String(length=64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("scope_identity", "name_index", name="uq_ai_memory_themen_name"),
        )
        op.create_index("ix_ai_memory_themen_scope_identity", "ai_memory_themen", ["scope_identity"])
        op.create_index("ix_ai_memory_themen_owner_user_id", "ai_memory_themen", ["owner_user_id"])
        op.create_index("ix_ai_memory_themen_server_id", "ai_memory_themen", ["server_id"])
        op.create_index("ix_ai_memory_themen_team_id", "ai_memory_themen", ["team_id"])

    spalten = _spalten("ai_memory_entries")
    pruefungen = _pruefungen("ai_memory_entries")
    with op.batch_alter_table("ai_memory_entries") as batch:
        batch.alter_column("key_encrypted", existing_type=sa.Text(), nullable=True)
        if "titel_encrypted" not in spalten:
            batch.add_column(sa.Column("titel_encrypted", sa.Text(), nullable=True))
        if "thema_id" not in spalten:
            batch.add_column(sa.Column("thema_id", sa.String(length=36), nullable=True))
            batch.create_foreign_key(
                "fk_ai_memory_entries_thema_id", "ai_memory_themen",
                ["thema_id"], ["id"], ondelete="SET NULL",
            )
        if "art" not in spalten:
            batch.add_column(sa.Column("art", sa.String(length=16), nullable=True))
        if "quelle" not in spalten:
            batch.add_column(
                sa.Column(
                    "quelle", sa.String(length=16), nullable=False,
                    server_default="eingetragen",
                )
            )
        if "quelle_ref" not in spalten:
            batch.add_column(sa.Column("quelle_ref", sa.String(length=80), nullable=True))
        if "wichtigkeit" not in spalten:
            batch.add_column(
                sa.Column("wichtigkeit", sa.SmallInteger(), nullable=False, server_default="3")
            )
        if "faellig_am" not in spalten:
            batch.add_column(sa.Column("faellig_am", sa.Date(), nullable=True))
        if "status" not in spalten:
            batch.add_column(
                sa.Column("status", sa.String(length=12), nullable=False, server_default="aktiv")
            )
        if "vergessen_am" not in spalten:
            batch.add_column(
                sa.Column("vergessen_am", sa.DateTime(timezone=True), nullable=True)
            )
        if "fassung" not in spalten:
            batch.add_column(
                sa.Column("fassung", sa.Integer(), nullable=False, server_default="1")
            )
        if "ck_ai_memory_entries_art" not in pruefungen:
            batch.create_check_constraint(
                "ck_ai_memory_entries_art", f"art IS NULL OR art IN ({ARTEN})"
            )
        if "ck_ai_memory_entries_quelle" not in pruefungen:
            batch.create_check_constraint(
                "ck_ai_memory_entries_quelle",
                "quelle IN ('eingetragen', 'gespraech', 'import', 'pflege')",
            )
        if "ck_ai_memory_entries_wichtigkeit" not in pruefungen:
            batch.create_check_constraint(
                "ck_ai_memory_entries_wichtigkeit", "wichtigkeit BETWEEN 1 AND 5"
            )
        if "ck_ai_memory_entries_status" not in pruefungen:
            batch.create_check_constraint(
                "ck_ai_memory_entries_status", "status IN ('aktiv', 'vergessen')"
            )

    indizes = _indizes("ai_memory_entries")
    if "ix_ai_memory_entries_thema_id" not in indizes:
        op.create_index("ix_ai_memory_entries_thema_id", "ai_memory_entries", ["thema_id"])
    if "ix_ai_memory_identity_status" not in indizes:
        op.create_index(
            "ix_ai_memory_identity_status", "ai_memory_entries", ["scope_identity", "status"]
        )

    if "quelle" not in spalten:
        op.execute("UPDATE ai_memory_entries SET quelle = 'gespraech' WHERE origin = 'ai'")

    if "ai_memory_versionen" not in vorhanden:
        op.create_table(
            "ai_memory_versionen",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "memory_id", sa.String(length=36),
                sa.ForeignKey("ai_memory_entries.id", ondelete="CASCADE"), nullable=False,
            ),
            sa.Column("scope_identity", sa.String(length=128), nullable=False),
            sa.Column("text_encrypted", sa.Text(), nullable=False),
            sa.Column("titel_encrypted", sa.Text(), nullable=True),
            sa.Column("grund", sa.String(length=20), nullable=False),
            sa.Column("von", sa.String(length=8), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint(f"grund IN ({GRUENDE})", name="ck_ai_memory_versionen_grund"),
            sa.CheckConstraint("von IN ('user', 'ai')", name="ck_ai_memory_versionen_von"),
        )
        op.create_index(
            "ix_ai_memory_versionen_eintrag", "ai_memory_versionen", ["memory_id", "created_at"]
        )


def downgrade() -> None:
    vorhanden = _tabellen()
    if "ai_memory_versionen" in vorhanden:
        op.drop_index("ix_ai_memory_versionen_eintrag", table_name="ai_memory_versionen")
        op.drop_table("ai_memory_versionen")

    spalten = _spalten("ai_memory_entries")
    if "status" in spalten:
        op.execute("DELETE FROM ai_memory_entries WHERE status = 'vergessen'")
    # Klartext mit Absicht: der alte Stand erkennt ihn am fehlenden Praefix
    # und verschluesselt ihn beim Start mit Index (`schluessel_nachziehen`).
    # Die ganze Kennung (47 von 64 Zeichen): mit acht Zeichen stiessen bei
    # unbegrenztem Vorrat zwei Namen eines Bereichs zusammen, und das UNIQUE
    # auf dem Index liess danach jeden Start scheitern.
    op.execute(
        "UPDATE ai_memory_entries SET key_encrypted = 'erinnerung-' || id, "
        "key_index = NULL WHERE key_encrypted IS NULL"
    )

    indizes = _indizes("ai_memory_entries")
    for name in ("ix_ai_memory_identity_status", "ix_ai_memory_entries_thema_id"):
        if name in indizes:
            op.drop_index(name, table_name="ai_memory_entries")

    pruefungen = _pruefungen("ai_memory_entries")
    with op.batch_alter_table("ai_memory_entries") as batch:
        for name in (
            "ck_ai_memory_entries_status",
            "ck_ai_memory_entries_wichtigkeit",
            "ck_ai_memory_entries_quelle",
            "ck_ai_memory_entries_art",
        ):
            if name in pruefungen:
                batch.drop_constraint(name, type_="check")
        # Der Fremdschluessel auf das Thema faellt mit der Spalte. Sein Name
        # haengt davon ab, ob die Datenbank aus dieser Migration oder aus
        # `create_all` stammt; beim Spaltenloeschen spielt er keine Rolle.
        for spalte in (
            "fassung", "vergessen_am", "status", "faellig_am", "wichtigkeit",
            "quelle_ref", "quelle", "art", "thema_id", "titel_encrypted",
        ):
            if spalte in spalten:
                batch.drop_column(spalte)
        batch.alter_column("key_encrypted", existing_type=sa.Text(), nullable=False)

    if "ai_memory_themen" in vorhanden:
        for name in (
            "ix_ai_memory_themen_team_id",
            "ix_ai_memory_themen_server_id",
            "ix_ai_memory_themen_owner_user_id",
            "ix_ai_memory_themen_scope_identity",
        ):
            if name in _indizes("ai_memory_themen"):
                op.drop_index(name, table_name="ai_memory_themen")
        op.drop_table("ai_memory_themen")
