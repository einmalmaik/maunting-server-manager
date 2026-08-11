"""Anbieter statt Adresse, Denkstufe statt Schalter.

Revision ID: 20260811_01
Revises: 20260810_06
Create Date: 2026-08-11

Drei Aenderungen, die zusammengehoeren, weil die dritte die erste voraussetzt.

**1. `ai_providers.base_url` weicht `provider_kind`.** Die frei eintragbare
Adresse war flexibel und teuer zugleich: MSM wusste nichts ueber das Ziel und
brauchte deshalb eine SSRF-Pruefung mit IP-Pinning; ueber das Modell dahinter
konnte es gar keine Aussage treffen. Mit einem Anbieter aus
`services/ai_provider_registry.py` gehoert die Adresse dem Programm — und mit ihr
kommt der Modellkatalog, aus dem die Denkfaehigkeiten stammen.

**Was mit Bestandszeilen passiert.** Wer auf `openrouter.ai` zeigte, bekommt
`provider_kind='openrouter'` und laeuft weiter. Alles Uebrige wird
**abgeschaltet, nicht geloescht**: `enabled=false`, `provider_kind=''`, Name und
Schluessel bleiben stehen. Der Grund ist eine Abwaegung zwischen zwei Uebeln —
ein stillschweigend weiterlaufender Provider, dessen Adresse MSM nicht mehr
kennt, waere schlimmer als ein sichtbar abgeschalteter, und ein geloeschter
waere Datenverlust ohne Rueckfrage. Der Betreiber sieht die Zeile in den
Einstellungen und entscheidet selbst.

Der **leere** Anbieterschluessel ist dabei kein Versehen. Der server_default
lautet "openrouter"; bliebe er stehen, saehe ein lokales Ollama nach dem Upgrade
wie ein OpenRouter-Zugang aus, und ein Betreiber, der die Zeile nur wieder
anhakt, schickte seine Anfragen samt Schluessel an einen fremden Dienst. Leer
ist in `ai_provider_registry` unbekannt und faellt auf.

Die Zuordnung laeuft ueber den **Host**, nicht ueber die ganze Zeichenkette:
`https://openrouter.ai/api/v1` und `https://openrouter.ai/api/v1/` sind
dieselbe Adresse, und `.../v1/chat/completions` (ein haeufiger Bedienfehler, den
`validate_provider_base_url` bisher aufgefangen hat) ist es auch.

**2. `ai_runs.reasoning_effort`** neben dem vorhandenen Boolean. Zwei Felder und
nicht eines, weil die Anbieter selbst zwei Dinge kennen: gemessen koennen 145
der 272 denkenden Modelle bei OpenRouter *nur* an/aus und nennen keine Stufen.
Ein einziges Stufenfeld muesste fuer die Mehrheit einen Wert erfinden.
Bestandszeilen bekommen NULL — das heisst „keine Stufe gewaehlt“ und ist fuer
einen Boolean-Lauf genau richtig.

**3. `role_ai_limits.max_reasoning_effort`** als Rang (0 = gar nicht bis 6 =
max), NULL = unbegrenzt. Der Typ ist Integer, damit sich das Feld in
`ai_limit_service.LIMIT_FIELDS` einreiht und dessen `max()`-Aufloesung samt
„None heisst unbegrenzt“ unveraendert erbt.

Bestandszeilen bekommen bewusst **NULL** und nicht 0: eine bestehende
Rollenkonfiguration hat zu Denktiefe nie etwas gesagt, und aus Schweigen ein
Verbot zu machen waere eine Politik, die niemand hinterlegt hat — derselbe
Fehler, den `ai_limit_service` im Modul-Docstring fuer die Tokenlimits
beschreibt (leere Zeilenmenge ergab dort einmal ein effektives Limit von 0 und
damit eine KI, die jede Anfrage abwies).

Zum Downgrade: `base_url` kann nicht sinnvoll wiederhergestellt werden — die
Information ist nach dem Drop weg. Der Rueckweg setzt deshalb die Adresse des
jeweiligen Anbieters ein und schaltet ab, was er nicht kennt. Das ist ehrlicher
als eine leere Spalte, die beim naechsten Aufruf in einen Verbindungsfehler
laeuft.
"""

from __future__ import annotations

from typing import Sequence, Union
from urllib.parse import urlparse

from alembic import op
import sqlalchemy as sa


revision: str = "20260811_01"
down_revision: Union[str, None] = "20260810_06"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: Host → Anbieterschluessel. Absichtlich hier hartkodiert statt aus
#: `ai_provider_registry` importiert: eine Migration muss auch dann noch das
#: tun, was sie am Tag ihrer Entstehung tat, wenn die Registry laengst weitere
#: Anbieter kennt. Ein Import waere eine Zeitbombe, keine Ersparnis.
_HOST_ZU_KIND = {"openrouter.ai": "openrouter"}


def _kind_fuer(base_url: str | None) -> str | None:
    host = (urlparse((base_url or "").strip()).hostname or "").lower()
    return _HOST_ZU_KIND.get(host)


def upgrade() -> None:
    verbindung = op.get_bind()

    with op.batch_alter_table("ai_providers") as batch:
        batch.add_column(
            sa.Column(
                "provider_kind",
                sa.String(length=32),
                nullable=False,
                server_default="openrouter",
            )
        )

    # Zuordnung in Python statt in SQL: die Hostauswertung soll dieselbe sein,
    # die auch `urlparse` im Anwendungscode vornimmt, und ein LIKE-Muster auf
    # der ganzen URL wuerde `https://boeser-host/openrouter.ai/` mittreffen.
    zeilen = verbindung.execute(
        sa.text("SELECT id, base_url FROM ai_providers")
    ).fetchall()
    for zeile in zeilen:
        kind = _kind_fuer(zeile.base_url)
        if kind is None:
            # Abschalten **und** den Anbieter leeren. Der server_default waere
            # sonst "openrouter": ein lokales Ollama sähe nach dem Upgrade wie
            # ein OpenRouter-Zugang aus, und ein Betreiber, der die Zeile nur
            # wieder anhakt, schickte seine Anfragen samt Schluessel an einen
            # fremden Dienst. Ein leerer Wert ist in `ai_provider_registry`
            # unbekannt und faellt damit sichtbar auf, statt still etwas
            # Falsches zu tun.
            verbindung.execute(
                sa.text(
                    "UPDATE ai_providers SET enabled = :aus, provider_kind = :leer "
                    "WHERE id = :id"
                ),
                {"aus": False, "leer": "", "id": zeile.id},
            )
            continue
        verbindung.execute(
            sa.text("UPDATE ai_providers SET provider_kind = :kind WHERE id = :id"),
            {"kind": kind, "id": zeile.id},
        )

    with op.batch_alter_table("ai_providers") as batch:
        batch.drop_column("base_url")
        batch.drop_column("allow_private_network")

    with op.batch_alter_table("ai_runs") as batch:
        batch.add_column(sa.Column("reasoning_effort", sa.String(length=16), nullable=True))

    with op.batch_alter_table("role_ai_limits") as batch:
        batch.add_column(sa.Column("max_reasoning_effort", sa.Integer(), nullable=True))


def downgrade() -> None:
    verbindung = op.get_bind()

    with op.batch_alter_table("role_ai_limits") as batch:
        batch.drop_column("max_reasoning_effort")

    with op.batch_alter_table("ai_runs") as batch:
        batch.drop_column("reasoning_effort")

    with op.batch_alter_table("ai_providers") as batch:
        batch.add_column(
            sa.Column(
                "base_url",
                sa.String(length=1024),
                nullable=False,
                server_default="https://openrouter.ai/api/v1",
            )
        )
        batch.add_column(
            sa.Column(
                "allow_private_network",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )

    # Was kein bekannter Anbieter ist, kann keine Adresse bekommen — der
    # Vorwaertsschritt hat sie geloescht. Abschalten statt auf gut Glueck eine
    # falsche Adresse eintragen.
    verbindung.execute(
        sa.text(
            "UPDATE ai_providers SET enabled = :aus WHERE provider_kind <> :kind"
        ),
        {"aus": False, "kind": "openrouter"},
    )

    with op.batch_alter_table("ai_providers") as batch:
        batch.drop_column("provider_kind")
