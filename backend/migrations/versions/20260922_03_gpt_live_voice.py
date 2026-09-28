"""GPT-Live als zweiter OpenAI-Sprachweg: Backend-Modell und Minutenpreis.

GPT-Live (`POST /v1/live/sessions`) spricht selbst und lässt ein zweites
Modell nachdenken und Werkzeuge rufen („Responses delegation"). Dieses Modell
braucht eine eigene Spalte, und die Sitzung einen eigenen Preis: OpenAI rechnet
sie nach Dauer ab, sekundengenau, nicht nach Audio-Token.

Beide Spalten sind nullable. ``realtime_backend_model`` leer heisst „das
Standardmodell des Zugangs"; ein fehlender Minutenpreis heisst wie bei den
übrigen Realtime-Preisen „ohne Preisquelle", nicht „kostenlos geschätzt".

Revision ID: 20260922_03
Revises: 20260922_02
Create Date: 2026-09-22
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260922_03"
down_revision: Union[str, None] = "20260922_02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SPALTEN = (
    ("realtime_backend_model", sa.String(length=256)),
    ("realtime_minute_price_micro_usd", sa.BigInteger()),
)


def _vorhanden() -> set[str]:
    """Die Spalten von ``ai_providers`` — leer, wenn es die Tabelle nicht gibt.

    Idempotent wie die Migrationen davor: ein Schema, das
    ``Base.metadata.create_all`` schon aus den Modellen gebaut hat, trägt beide
    Spalten bereits (`test_schema_constraints`).
    """
    inspector = sa.inspect(op.get_bind())
    if "ai_providers" not in set(inspector.get_table_names()):
        return set()
    return {spalte["name"] for spalte in inspector.get_columns("ai_providers")}


def upgrade() -> None:
    vorhanden = _vorhanden()
    fehlend = [(name, typ) for name, typ in SPALTEN if name not in vorhanden]
    if not vorhanden or not fehlend:
        return
    with op.batch_alter_table("ai_providers") as batch:
        for name, typ in fehlend:
            batch.add_column(sa.Column(name, typ, nullable=True))


def downgrade() -> None:
    vorhanden = _vorhanden()
    da = [name for name, _typ in reversed(SPALTEN) if name in vorhanden]
    if not da:
        return
    with op.batch_alter_table("ai_providers") as batch:
        for name in da:
            batch.drop_column(name)
