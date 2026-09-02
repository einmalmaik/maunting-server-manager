"""Die Stimme wechselt den Anbieter, das Gehör bekommt eine eigene Spalte.

Revision ID: 20260816_02
Revises: 20260816_01
Create Date: 2026-08-16

Einen Tag nach `20260816_01` steht in ``default_voice`` etwas anderes als beim
Anlegen gedacht. Dort war es eine der acht Stimmen von OpenAIs Realtime-API,
hier ist es eine Stimm-Kennung aus dem ElevenLabs-Konto des Betreibers. Der
Sprachmodus spricht nicht mehr selbst mit einem zweiten Modell — er lässt
dasselbe Chatmodell antworten wie der getippte Chat und legt nur Gehör davor und
Stimme dahinter.

**Warum die Spalte bleibt und nicht neu angelegt wird:** sie beantwortet
weiterhin dieselbe Frage — „mit welcher Stimme spricht dieses Panel?". Was sich
geändert hat, ist der Katalog, aus dem die Antwort stammt. Eine zweite Spalte
neben der ersten hätte zwei Wahrheiten ergeben und die Frage offengelassen,
welche gilt.

**Warum sie trotzdem breiter wird:** ``alloy`` sind fünf Zeichen,
``21m00Tcm4TlvDq8ikWAM`` sind zwanzig, und ElevenLabs verspricht nirgends, dass
es dabei bleibt. 32 hätten heute gereicht. 64 reichen auch dann noch, wenn nicht
— und der Unterschied kostet nichts.

Bestandswerte werden **gelöscht**, und das ist die eigentliche Entscheidung
dieser Migration. Ein stehengebliebenes ``alloy`` wäre bei ElevenLabs keine
Stimme, sondern eine Kennung, die es nicht gibt: der erste Satz liefe in ein 404
der Gegenstelle statt in eine Fehlermeldung an dem Feld, in das der Betreiber
getippt hat. Leer heißt „nichts hinterlegt", und der Sprachmodus sagt dann klar,
dass die Stimme fehlt.

``transcription_model`` ist neu und steht am **Chatzugang**, nicht am
Sprachzugang: gesprochene Sprache wird bei OpenRouter zu Text, indem sie als
``input_audio`` in eine ganz gewöhnliche Chatanfrage geht. Einen
Transkriptions-Endpunkt gibt es dort nicht (am 2026-08-16 geprüft). NULL heißt
auch hier „nichts hinterlegt" — dann gibt es über diesen Zugang keinen
Sprachmodus, und niemand rät ein Modell, das der Betreiber bezahlen müsste.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260816_02"
down_revision: Union[str, None] = "20260816_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("ai_providers") as batch:
        batch.alter_column(
            "default_voice",
            existing_type=sa.String(length=32),
            type_=sa.String(length=64),
            existing_nullable=True,
        )
        batch.add_column(
            sa.Column("transcription_model", sa.String(length=256), nullable=True)
        )
    # Erst die Spalte verbreitern, dann leeren. Andersherum ginge es auch, aber
    # nur solange die Werte kurz sind — und genau darauf soll sich hier nichts
    # verlassen.
    op.execute(sa.text("UPDATE ai_providers SET default_voice = NULL"))


def downgrade() -> None:
    # Die Werte kommen nicht zurück. Sie könnten es auch nicht: ein Rückweg
    # müsste raten, welche der acht OpenAI-Stimmen der Betreiber vor dem Wechsel
    # gewählt hatte, und NULL ist die einzige ehrliche Antwort darauf.
    with op.batch_alter_table("ai_providers") as batch:
        batch.drop_column("transcription_model")
        batch.alter_column(
            "default_voice",
            existing_type=sa.String(length=64),
            type_=sa.String(length=32),
            existing_nullable=True,
        )
