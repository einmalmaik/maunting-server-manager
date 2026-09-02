"""Der Ausgangskorb der KI: eine Mail ueberlebt den Versandversuch.

Revision ID: 20260813_03
Revises: 20260813_02
Create Date: 2026-08-13

Bis hierher war eine KI-Mail ein Thread und sonst nichts. `ai_mail.zustellen`
startete je Nachricht einen eigenen Betriebssystem-Thread mit eigener
Ereignisschleife — ohne Obergrenze. Zehntausend gleichzeitig faellige Aufgaben
waren damit zehntausend Threads und, eine Ebene tiefer, zehntausend SMTP-
Verbindungen; `aiosmtplib.send` baut je Aufruf eine neue auf. Was daran
scheiterte, verschwand: der Versand endete in `except Exception: return False`,
und danach gab es die Nachricht nirgends mehr.

Diese Tabelle ist die Antwort darauf. Sie haelt den fertigen Text, bis er
tatsaechlich zugestellt ist, und ein begrenzter Arbeiter nimmt sich wenige
davon gleichzeitig vor.

**`user_id` und nicht die Adresse.** Bei MSM liegt eine Mailadresse
verschluesselt in `users.email_encrypted`; im Klartext kennt sie nur der
DIS-Sidecar. Eine Kopie in dieser Tabelle waere ein Klartextspeicher, den
niemand hier vermutet — und ein zweiter Weg an `ai_mail.empfaenger` vorbei, der
Abbestellung, fehlenden Versandweg und fehlende Adresse an *einer* Stelle
prueft. Aufgeloest wird erst beim Versand; wer zwischendurch abbestellt,
bekommt nichts mehr.

**ON DELETE CASCADE auf `users`,** und dieselbe Zusage steht am Modell
(`models/ai_mail_outbox.py`). Eine Mail an ein geloeschtes Konto hat keinen
Empfaenger; sie stehen zu lassen hiesse, eine Zeile aufzuheben, die nie wieder
zustellbar wird.

**Der Index traegt `status` zuerst.** Der Arbeiter fragt immer beides zusammen
(`status = 'offen' AND naechster_versuch_at <= now`), und `status` ist das
Feld, das die Tabelle klein haelt: zugestellte Zeilen bleiben liegen, offene
sind die Minderheit.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260813_03"
down_revision: Union[str, None] = "20260813_02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_mail_outbox",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("anlass", sa.String(length=48), nullable=False),
        sa.Column("betreff", sa.String(length=255), nullable=False),
        sa.Column("text_body", sa.Text(), nullable=False),
        sa.Column("html_body", sa.Text(), nullable=True),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="offen"
        ),
        sa.Column("versuche", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "naechster_versuch_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("letzter_fehler", sa.String(length=500), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_ai_mail_outbox_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('offen', 'zugestellt', 'aufgegeben')",
            name="ck_ai_mail_outbox_status",
        ),
    )
    op.create_index(
        "ix_ai_mail_outbox_user_id", "ai_mail_outbox", ["user_id"], unique=False
    )
    op.create_index(
        "ix_ai_mail_outbox_faellig",
        "ai_mail_outbox",
        ["status", "naechster_versuch_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_ai_mail_outbox_faellig", table_name="ai_mail_outbox")
    op.drop_index("ix_ai_mail_outbox_user_id", table_name="ai_mail_outbox")
    op.drop_table("ai_mail_outbox")
