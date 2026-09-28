"""Passkeys serverseitig: Tabelle ``user_passkeys``, Altbestand abgeschaltet.

Bis hierher war „2FA per Passkey" eine Abfrage im Browser. Der Server kannte
keinen Schluessel und glaubte dem Feld ``passkey_verified``. Ein solches Konto
hatte also nie einen zweiten Faktor, der etwas pruefte — und kann auch jetzt
keinen haben: es gibt keinen Schluessel, gegen den geprueft werden koennte.

Deshalb schaltet diese Migration die 2FA dieser Konten ab, statt sie mit einem
Faktor stehen zu lassen, den niemand mehr erfuellen kann. Sie melden sich mit
Passwort an wie zuvor (das war de facto schon so) und richten den Passkey im
Profil neu ein.

Erkannt wird ein solches Konto an der letzten Einschaltung im Audit-Log
(``auth.2fa.passkey.enable`` nach ``auth.2fa.enable``) oder daran, dass es
aktiv ist, aber kein TOTP-Geheimnis hat. Das Geheimnis allein reicht nicht:
das Profil rief vor jeder Einrichtung ``/2fa/setup`` auf, auch vor einem
Passkey — es blieb liegen.

Revision ID: 20260927_01
Revises: 20260926_09
Create Date: 2026-09-27
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260927_01"
down_revision: Union[str, None] = "20260926_09"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    # `create_all` kann die Tabelle schon angelegt haben (Neuinstallation,
    # `schema_manager`); die Bereinigung des Altbestands laeuft trotzdem.
    if "user_passkeys" not in set(sa.inspect(bind).get_table_names()):
        _tabelle_anlegen()
    _altbestand_abschalten(bind)


def _tabelle_anlegen() -> None:
    op.create_table(
        "user_passkeys",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("credential_id", sa.String(length=1400), nullable=False),
        sa.Column("public_key", sa.Text(), nullable=False),
        sa.Column("algorithm", sa.Integer(), nullable=False),
        sa.Column("rp_id", sa.String(length=253), nullable=False),
        sa.Column("sign_count", sa.BigInteger(), nullable=False),
        sa.Column("transports", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("credential_id"),
    )
    op.create_index("ix_user_passkeys_user_id", "user_passkeys", ["user_id"])


def _altbestand_abschalten(bind) -> None:
    aktive = bind.execute(
        sa.text(
            "SELECT id, two_factor_secret_encrypted FROM users "
            "WHERE two_factor_enabled = :wahr"
        ),
        {"wahr": True},
    ).fetchall()
    for user_id, geheimnis in aktive:
        # Hat das Konto schon einen echten Passkey (neuer Code lief vor der
        # Migration), bleibt es, wie es ist.
        echte = bind.execute(
            sa.text("SELECT COUNT(*) FROM user_passkeys WHERE user_id = :uid"), {"uid": user_id}
        ).scalar()
        if echte:
            continue
        letzte = bind.execute(
            sa.text(
                "SELECT action FROM audit_logs WHERE user_id = :uid "
                "AND action IN ('auth.2fa.enable', 'auth.2fa.passkey.enable') "
                "ORDER BY created_at DESC, id DESC LIMIT 1"
            ),
            {"uid": user_id},
        ).scalar()
        if geheimnis and letzte != "auth.2fa.passkey.enable":
            continue
        bind.execute(
            sa.text(
                "UPDATE users SET two_factor_enabled = :falsch, "
                "two_factor_secret_encrypted = NULL WHERE id = :uid"
            ),
            {"falsch": False, "uid": user_id},
        )
        bind.execute(sa.text("DELETE FROM backup_codes WHERE user_id = :uid"), {"uid": user_id})


def downgrade() -> None:
    # Abgeschaltete Altkonten bleiben aus: ihr „Passkey" hat nie etwas geprueft.
    if "user_passkeys" not in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    op.drop_index("ix_user_passkeys_user_id", table_name="user_passkeys")
    op.drop_table("user_passkeys")
