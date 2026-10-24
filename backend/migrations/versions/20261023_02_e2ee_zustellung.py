"""Die gerätebezogene Zustellung: Inbox, Chiffretextobjekt, Auftrag, Idempotenzrest.

Bis 23.10.2026 teilten sich alle Geraete eines Kontos eine blinde Mailbox, und
der Chiffretext lag dort 30 Tage, auch wenn ihn niemand mehr brauchte. Diese
Migration fuehrt den zweiten Weg ein: je Geraet eine opake Zustelladresse, je
Zustellung ein Auftrag, und ein Quittung, das den Auftrag loescht.

Keine dieser Tabellen hat einen Fremdschluessel auf ``users`` — sie haengen an
der Geraetezeile, deren Kaskade sie mit dem Konto aufraeumt. Der Zustellbestand
fuehrt keinen Kontaktgraph: die Mailbox des Gespraechs steht nur in der
Anfrage, nicht in den Zeilen.

Revision ID: 20261023_02
Revises: 20261023_01
Create Date: 2026-10-23
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261023_02"
down_revision: Union[str, None] = "20261023_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tabellen() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    # Datenbanken aus `create_all` kennen die Tabellen schon; dann gibt es
    # nichts zu tun.
    vorhanden = _tabellen()
    if "e2ee_device_inboxes" in vorhanden:
        return

    op.create_table(
        "e2ee_device_inboxes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "device_id",
            sa.Integer(),
            sa.ForeignKey("user_e2ee_devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("inbox_hash", sa.String(length=64), nullable=False),
        sa.Column("erstellt_am", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ablauf_am", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("device_id", name="uq_e2ee_device_inbox_device"),
        sa.UniqueConstraint("inbox_hash", name="uq_e2ee_device_inbox_hash"),
    )
    op.create_index(
        "ix_e2ee_device_inbox_device", "e2ee_device_inboxes", ["device_id"]
    )

    # Die Annahmenummer: eine Folge fuer alle Zustellungen. Sie wird vor der
    # Tabelle angelegt, weil die Spalte sie als Vorgabewert nennt.
    op.execute("CREATE SEQUENCE IF NOT EXISTS e2ee_delivery_seq")

    op.create_table(
        "e2ee_delivery_payloads",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("ciphertext_sha256", sa.String(length=64), nullable=False),
        sa.Column("groesse", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("angenommen_am", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ablauf_am", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_e2ee_delivery_payload_ablauf", "e2ee_delivery_payloads", ["ablauf_am"]
    )
    op.create_index(
        "ix_e2ee_delivery_payloads_ciphertext_sha256",
        "e2ee_delivery_payloads",
        ["ciphertext_sha256"],
    )

    op.create_table(
        "e2ee_deliveries",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "payload_id",
            sa.String(length=36),
            sa.ForeignKey("e2ee_delivery_payloads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "inbox_hash",
            sa.String(length=64),
            sa.ForeignKey("e2ee_device_inboxes.inbox_hash", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "laufende_seq",
            sa.BigInteger(),
            server_default=sa.text("nextval('e2ee_delivery_seq')"),
            nullable=False,
        ),
        sa.Column("angenommen_am", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ablauf_am", sa.DateTime(timezone=True), nullable=False),
        sa.Column("quittiert_am", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("inbox_hash", "id", name="uq_e2ee_delivery_inbox_id"),
    )
    op.create_index(
        "ix_e2ee_delivery_inbox_seq", "e2ee_deliveries", ["inbox_hash", "laufende_seq"]
    )
    op.create_index("ix_e2ee_delivery_ablauf", "e2ee_deliveries", ["ablauf_am"])
    op.create_index(
        "ix_e2ee_delivery_seq", "e2ee_deliveries", ["laufende_seq"], unique=True
    )

    op.create_table(
        "e2ee_delivery_receipts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("inbox_hash", sa.String(length=64), nullable=False),
        sa.Column("delivery_id", sa.String(length=36), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("ergebnis", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("angenommen_am", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ablauf_am", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("inbox_hash", "delivery_id", name="uq_e2ee_delivery_receipt"),
    )
    op.create_index(
        "ix_e2ee_delivery_receipt_ablauf", "e2ee_delivery_receipts", ["ablauf_am"]
    )


def _indizes(tabelle: str) -> set[str]:
    """Die Indizes einer Tabelle — Datenbanken aus `create_all` tragen andere Namen."""
    return {
        index["name"]
        for index in sa.inspect(op.get_bind()).get_indexes(tabelle)
        if index.get("name")
    }


def _drop_index(name: str, tabelle: str) -> None:
    if name in _indizes(tabelle):
        op.drop_index(name, table_name=tabelle)


def downgrade() -> None:
    vorhanden = _tabellen()
    # Die Reihenfolge ist umgekehrt: erst die Kinder, dann die Eltern.
    for tabelle in (
        "e2ee_delivery_receipts",
        "e2ee_deliveries",
        "e2ee_delivery_payloads",
        "e2ee_device_inboxes",
    ):
        if tabelle not in vorhanden:
            continue
        if tabelle == "e2ee_delivery_receipts":
            _drop_index("ix_e2ee_delivery_receipt_ablauf", tabelle)
        elif tabelle == "e2ee_deliveries":
            _drop_index("ix_e2ee_delivery_ablauf", tabelle)
            _drop_index("ix_e2ee_delivery_seq", tabelle)
            _drop_index("ix_e2ee_delivery_inbox_seq", tabelle)
        elif tabelle == "e2ee_delivery_payloads":
            _drop_index("ix_e2ee_delivery_payloads_ciphertext_sha256", tabelle)
            _drop_index("ix_e2ee_delivery_payload_ablauf", tabelle)
        elif tabelle == "e2ee_device_inboxes":
            _drop_index("ix_e2ee_device_inbox_device", tabelle)
        op.drop_table(tabelle)
    # Die Annahmenummer haengt an keiner Tabelle mehr, seit sie weg ist.
    op.execute("DROP SEQUENCE IF EXISTS e2ee_delivery_seq")
