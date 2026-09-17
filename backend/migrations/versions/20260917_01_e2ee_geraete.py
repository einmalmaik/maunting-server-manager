"""Ein Schluesselbegriff im Messenger: das Geraet.

Der Kontoschluesselbund faellt weg. Er existierte, weil alle Geraete eines
Kontos dieselbe Nachricht entschluesseln koennen mussten — mit dem Double
Ratchet ist das genau falsch: er ist eine lineare Kette, die pro Geraet
weiterlaeuft, und zwei Geraete mit demselben privaten Schluessel driften
auseinander und verklemmen die Sitzung.

Deshalb:

* ``user_e2ee_devices`` nimmt je Geraet einen oeffentlichen Schluessel auf.
  Der private Teil wird clientseitig erzeugt und verlaesst das Geraet nie.
* ``users.social_e2ee_public_key`` faellt. Die Spalte wurde ausserdem
  serverseitig befuellt (``AuthService.generate_e2ee_public_key_jwk`` erzeugte
  ein RSA-Paar und warf den privaten Teil weg) — jedes Konto hatte damit einen
  veroeffentlichten Schluessel, zu dem es nirgends einen privaten gab. Wer
  dagegen verschluesselte, schrieb in ein schwarzes Loch.
* ``social_e2ee_wrapped_keyring`` und ``social_e2ee_keyring_version`` fallen mit
  dem Wiederherstellungsschluessel.
* ``device_pairings`` bekommt zwei Spalten fuer den Verlaufs-Erstabgleich: ein
  frisch gekoppeltes Geraet hat keine Ratchet-Sitzungen und liest deshalb nichts
  Rueckwirkendes vom Server. Das eingerichtete Geraet legt seinen Verlauf
  versiegelt gegen den Geraeteschluessel des neuen dort ab; der Blob lebt
  hoechstens so lange wie der Kopplungscode.

**Der Verlauf, der bisher gegen den Kontoschluessel verschluesselt wurde,
bleibt als Umschlag liegen.** Lesbar ist er nur noch auf Geraeten, die den Bund
schon lokal hatten und ihn vor dem Update einmal uebernommen haben.

Revision ID: 20260917_01
Revises: 20260916_01
Create Date: 2026-09-17
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260917_01"
down_revision: Union[str, None] = "20260916_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tabellen = set(inspector.get_table_names())

    if "user_e2ee_devices" not in tabellen:
        op.create_table(
            "user_e2ee_devices",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "user_id",
                sa.Integer(),
                # Das ON DELETE CASCADE muss hier genauso stehen wie im Modell.
                # SQLite uebernimmt eine Zusage aus dem Modell nicht von selbst;
                # was die Datenbank nicht kennt, sichert sie auch nicht zu.
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("device_id", sa.String(length=64), nullable=False),
            sa.Column("public_key_jwk", sa.Text(), nullable=False),
            sa.Column("label", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("user_id", "device_id", name="uq_user_e2ee_device"),
        )
        op.create_index("ix_user_e2ee_devices_user", "user_e2ee_devices", ["user_id"])

    if "users" in tabellen:
        spalten = {col["name"] for col in inspector.get_columns("users")}
        with op.batch_alter_table("users") as batch_op:
            for name in (
                "social_e2ee_keyring_version",
                "social_e2ee_wrapped_keyring",
                "social_e2ee_public_key",
            ):
                if name in spalten:
                    batch_op.drop_column(name)

    if "device_pairings" in tabellen:
        spalten = {col["name"] for col in inspector.get_columns("device_pairings")}
        with op.batch_alter_table("device_pairings") as batch_op:
            if "verlauf_blob" not in spalten:
                batch_op.add_column(sa.Column("verlauf_blob", sa.Text(), nullable=True))
            if "verlauf_abgelegt_am" not in spalten:
                batch_op.add_column(
                    sa.Column("verlauf_abgelegt_am", sa.DateTime(timezone=True), nullable=True)
                )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tabellen = set(inspector.get_table_names())

    if "device_pairings" in tabellen:
        spalten = {col["name"] for col in inspector.get_columns("device_pairings")}
        with op.batch_alter_table("device_pairings") as batch_op:
            if "verlauf_abgelegt_am" in spalten:
                batch_op.drop_column("verlauf_abgelegt_am")
            if "verlauf_blob" in spalten:
                batch_op.drop_column("verlauf_blob")

    if "users" in tabellen:
        spalten = {col["name"] for col in inspector.get_columns("users")}
        with op.batch_alter_table("users") as batch_op:
            if "social_e2ee_public_key" not in spalten:
                batch_op.add_column(sa.Column("social_e2ee_public_key", sa.Text(), nullable=True))
            if "social_e2ee_wrapped_keyring" not in spalten:
                batch_op.add_column(
                    sa.Column("social_e2ee_wrapped_keyring", sa.Text(), nullable=True)
                )
            if "social_e2ee_keyring_version" not in spalten:
                batch_op.add_column(
                    sa.Column(
                        "social_e2ee_keyring_version",
                        sa.Integer(),
                        nullable=False,
                        server_default="0",
                    )
                )

    if "user_e2ee_devices" in tabellen:
        op.drop_index("ix_user_e2ee_devices_user", table_name="user_e2ee_devices")
        op.drop_table("user_e2ee_devices")
