"""Add per-user credentials and their per-server binding.

Revision ID: 20260807_03
Revises: 20260807_02
Create Date: 2026-08-07

Phase 7 des v4-Plans, Zielpunkt 17.

Bisher gab es GitHub-PAT und Steam-Account ausschliesslich panelweit. Fuer einen
Self-Hosted-Betreiber ist das richtig, fuer einen Hoster nicht: dort wuerde
jeder Kundenserver die zentralen Betreiberzugaenge mitbenutzen.

Diese Migration ergaenzt zwei Ebenen, ohne die panelweite zu veraendern:

- `user_credentials` — der Tresor eines Benutzers. Mehrere Eintraege je Art
  sind erlaubt (jemand kann zwei Steam-Konten haben), unterschieden ueber ein
  Label.
- `server_credential_bindings` — welcher Server welches Credential fuer welche
  Art verwendet. Ein Server verweist damit auf ein Credential, ohne dessen
  Klartext zu sehen oder zu kopieren.

Secrets liegen ausschliesslich DIS-verschluesselt mit objektgebundener AAD vor.
Gespeichert wird zusaetzlich nur ein nicht umkehrbarer Hinweis auf die letzten
Zeichen, damit der Benutzer seine Eintraege auseinanderhalten kann.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260807_03"
down_revision: Union[str, None] = "20260807_02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_credentials",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=64), nullable=False),
        # Nur bei Steam belegt; ein GitHub-PAT hat keinen Benutzernamen.
        sa.Column("username", sa.String(length=256), nullable=True),
        sa.Column("secret_encrypted", sa.Text(), nullable=False),
        sa.Column("secret_hint", sa.String(length=16), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('github_token', 'steam_account')",
            name="ck_user_credentials_kind",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "kind", "label", name="uq_user_credentials_label"),
    )
    op.create_index("ix_user_credentials_user_id", "user_credentials", ["user_id"])

    op.create_table(
        "server_credential_bindings",
        sa.Column("server_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("credential_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('github_token', 'steam_account')",
            name="ck_server_credential_bindings_kind",
        ),
        sa.ForeignKeyConstraint(["server_id"], ["servers.id"], ondelete="CASCADE"),
        # RESTRICT: ein noch gebundenes Credential darf nicht einfach
        # verschwinden — sonst wuerde ein Server beim naechsten Install
        # unbemerkt auf den Panel-Zugang zurueckfallen.
        sa.ForeignKeyConstraint(
            ["credential_id"], ["user_credentials.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("server_id", "kind"),
    )
    op.create_index(
        "ix_server_credential_bindings_credential_id",
        "server_credential_bindings",
        ["credential_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_server_credential_bindings_credential_id",
        table_name="server_credential_bindings",
    )
    op.drop_table("server_credential_bindings")
    op.drop_index("ix_user_credentials_user_id", table_name="user_credentials")
    op.drop_table("user_credentials")
