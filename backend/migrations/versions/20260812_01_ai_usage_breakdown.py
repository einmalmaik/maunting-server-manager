"""Verbrauch wird aufgeschluesselt, Preise bekommen Nachkommastellen.

Revision ID: 20260812_01
Revises: 20260811_04
Create Date: 2026-08-12

Die Verbrauchsanzeige zeigte Zahlen, die beim Anbieter nicht wiederzufinden
waren. Der Grund steckte eine Ebene tiefer: von der Antwort des Anbieters wurde
nur ``total_tokens`` behalten. Aus einer einzigen Summe laesst sich nicht
zurueckrechnen, was Eingabe war und was Ausgabe — und weil beide
unterschiedlich viel kosten (bei vielen Modellen um das Fuenffache) und
zwischengespeicherte Eingabe nochmal rund ein Zehntel, war jede nachtraegliche
Kostenrechnung mit *einem* Preis auf *alle* Tokens zwangslaeufig daneben.

Diese Revision legt die Spalten an, in denen ab jetzt steht, was der Anbieter
tatsaechlich gemeldet hat — samt ``cost_source``, das gemessene Zeilen von
geschaetzten trennt. Ohne diese Unterscheidung sah eine geratene Zahl genauso
aus wie eine gemessene, und der Betreiber konnte nicht erkennen, welche der
beiden er gerade gegen sein Anbieter-Dashboard haelt.

``provider_requests`` beantwortet die Frage, die hinter der urspruenglichen
Beobachtung stand: warum 500.000 Tokens fuer vier Anfragen. Eine Chatnachricht
ist nicht eine Anbieteranfrage. Jede Werkzeugrunde ruft den Anbieter erneut und
schickt den inzwischen gewachsenen Verlauf komplett mit; zwoelf Runden mit
30.000 Tokens Prompt sind 360.000 abgerechnete Tokens fuer *eine* Frage. Der
Anbieter rechnet genauso ab — sichtbar war es nur nirgends.

Bestandszeilen bekommen ueberall NULL. Nachtraeglich eine Aufschluesselung zu
erfinden hiesse, aus einer Vermutung eine Tatsache zu machen; die Ansicht zeigt
dort "unbekannt".

**Der Preis wechselt die Einheit.** ``token_price_cents_per_million`` (ganze
Cent) wird zu ``token_price_micro_usd_per_million`` (1 Cent = 10.000
Microunits, dieselbe Einheit wie ``accounted_cost_microunits``). Zwei Gruende:
zwischen 1 und 2 Cent lag nichts, „1,20 €" war also nicht eintragbar; und die
Waehrung stand nirgends. Sie ist jetzt festgelegt auf USD, weil der Anbieter in
USD abrechnet und eine Umrechnung *vor* der Buchung eine zweite Fehlerquelle
waere. Bestandswerte werden mit 10.000 multipliziert und damit als US-Cent
weitergefuehrt — sie waren vorher waehrungslos, eine andere Lesart gibt es
nicht.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260812_01"
down_revision: Union[str, None] = "20260811_04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# 1 Cent = 10.000 Microunits (`ai_usage_service.MICROUNITS_PER_CENT`). Hier als
# Literal und nicht als Import: eine Migration muss auch dann noch laufen, wenn
# der Anwendungscode sich laengst weitergedreht hat.
MICROUNITS_PER_CENT = 10_000


def upgrade() -> None:
    # Ein einziger Batch-Block je Tabelle: auf SQLite wird sie dabei genau
    # einmal neu aufgebaut. Der CheckConstraint muss mit hinein und nicht
    # nachtraeglich — SQLite kennt kein ADD CONSTRAINT, und ein Constraint, der
    # nur in PostgreSQL existiert, ist eine Zusage, die die Testsuite nicht
    # prueft (Lehre aus `test_schema_constraints.py`).
    with op.batch_alter_table("ai_usage_events") as batch:
        batch.add_column(sa.Column("prompt_tokens", sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column("completion_tokens", sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column("cached_tokens", sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column("reasoning_tokens", sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column("provider_requests", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("cost_source", sa.String(length=16), nullable=True))
        batch.create_check_constraint(
            "ck_ai_usage_events_cost_source",
            "cost_source IS NULL OR cost_source IN ('provider', 'estimate', 'none')",
        )

    with op.batch_alter_table("ai_providers") as batch:
        batch.add_column(
            sa.Column("token_price_micro_usd_per_million", sa.BigInteger(), nullable=True)
        )
    op.execute(
        sa.text(
            "UPDATE ai_providers "
            "SET token_price_micro_usd_per_million = "
            f"token_price_cents_per_million * {MICROUNITS_PER_CENT} "
            "WHERE token_price_cents_per_million IS NOT NULL"
        )
    )
    with op.batch_alter_table("ai_providers") as batch:
        batch.drop_column("token_price_cents_per_million")


def downgrade() -> None:
    with op.batch_alter_table("ai_providers") as batch:
        batch.add_column(
            sa.Column("token_price_cents_per_million", sa.Integer(), nullable=True)
        )
    # Zurueck in ganze Cent, **aufgerundet**. Dieselbe Richtung wie in
    # `routers/ai_settings._cents`: ein zu niedriger Preis laesst jemanden
    # glauben, er habe noch Luft. Nachkommastellen gehen dabei verloren — das
    # ist der Preis dafuer, dass die alte Spalte sie nie fassen konnte.
    op.execute(
        sa.text(
            "UPDATE ai_providers "
            "SET token_price_cents_per_million = "
            f"(token_price_micro_usd_per_million + {MICROUNITS_PER_CENT - 1}) "
            f"/ {MICROUNITS_PER_CENT} "
            "WHERE token_price_micro_usd_per_million IS NOT NULL"
        )
    )
    with op.batch_alter_table("ai_providers") as batch:
        batch.drop_column("token_price_micro_usd_per_million")

    with op.batch_alter_table("ai_usage_events") as batch:
        batch.drop_constraint("ck_ai_usage_events_cost_source", type_="check")
        batch.drop_column("cost_source")
        batch.drop_column("provider_requests")
        batch.drop_column("reasoning_tokens")
        batch.drop_column("cached_tokens")
        batch.drop_column("completion_tokens")
        batch.drop_column("prompt_tokens")
