"""Was eine Sprachsitzung kostet — und wann sie deshalb aufhört.

**Eine Sitzung ist eine Buchung.** Nicht eine je Antwort. Das ist die eine
Entscheidung, aus der alles andere hier folgt, und sie hat einen konkreten
Grund: `requests_per_minute` und `concurrent_operations` zählen Anfragen. Wer
jede gesprochene Antwort als Anfrage bucht, reisst einem lebhaften Gespräch nach
zehn Sätzen die Leitung ab — nicht wegen der Kosten, sondern wegen einer Grenze,
die für getippte Fragen gedacht war. Eine Sitzung als eine Anfrage zu zählen
bildet dagegen genau das ab, was passiert: der Mensch hat einmal auf das
Mikrofon gedrückt.

Damit die Tokengrenzen trotzdem greifen, gibt es zwei Zeitpunkte:

1. **Beim Öffnen** wird reserviert — mit einer Schätzung aus Anweisungen und
   Verlauf. Ist das Kontingent schon erschöpft, kommt die Sitzung nicht zustande.
2. **Während der Sitzung** zählt `melden` die gemeldeten Tokens mit und sagt,
   wann Schluss ist. Das ist reine Arithmetik gegen einen Freiraum, der beim
   Öffnen einmal ausgerechnet wurde — kein Datenbankzugriff je Antwort, sonst
   stünde die Ereignisschleife mitten im Satz.
3. **Am Ende** wird die Reservierung mit der wirklich verbrauchten Zahl
   abgeschlossen.

**Was der Sprachweg über Geld nicht weiss, und das gehört gesagt:** OpenAIs
``/v1/models`` nennt keine Preise. Ohne einen vom Betreiber gepflegten
``token_price_micro_usd_per_million`` am Zugang wird die Kostenspalte deshalb
null — und die Monatsgrenze in Cent bindet den Sprachmodus nicht. Die
Tokengrenzen binden ihn sehr wohl. MSM erfindet keinen Preis; wer die
Kostengrenze auch hier greifen lassen will, trägt den Preis am Zugang ein.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from database import SessionLocal
from models import AiProvider, AiUsageEvent, User
from services.ai_provider_service import estimate_cost_microunits
from services.ai_usage_service import (
    MAX_COST_MICROUNITS,
    AiQuotaExceeded,
    AiUsageConflict,
    complete_ai_usage,
    fail_ai_usage,
    reserve_ai_usage,
    verbleibende_tokens,
)

logger = logging.getLogger(__name__)


@dataclass
class Sitzungsverbrauch:
    """Die laufende Rechnung einer Sprachsitzung."""

    request_id: UUID
    #: Wieviele Tokens diese Sitzung noch verbrauchen darf. ``None`` heisst
    #: unbegrenzt — dann hat der Betreiber für diesen Benutzer keine Tokengrenze
    #: gesetzt, und MSM erfindet keine.
    freiraum: int | None
    #: Was beim Öffnen reserviert wurde. Die Untergrenze der Abschlussbuchung:
    #: die Anweisungen und der Verlauf gingen hinaus, ob geantwortet wurde oder
    #: nicht.
    reserviert: int
    #: Der am Zugang gepflegte Tokenpreis, oder ``None``. Mitgeführt, weil die
    #: Abschlussbuchung in einer anderen Datenbanksitzung stattfindet und den
    #: Zugang dort nicht mehr hat.
    preis: int | None = None
    verbraucht: int = 0

    def melden(self, usage: dict) -> bool:
        """Eine ``response.done``-Meldung mitzählen.

        Gibt zurück, ob die Sitzung weiterlaufen darf. ``False`` heisst: das
        Kontingent ist aufgebraucht, der Anrufer schliesst.

        Fehlt ``total_tokens``, wird aus Ein- und Ausgabe summiert; fehlt auch
        das, wird nichts gezählt. Eine Meldung ohne Zahlen ist keine Null — sie
        ist eine fehlende Angabe, und eine erfundene Null wäre hier ein
        Freifahrtschein.
        """
        self.verbraucht += _tokens(usage)
        if self.freiraum is None:
            return True
        return self.verbraucht <= self.freiraum

    @property
    def gesamt(self) -> int:
        """Was gebucht wird: das Gemessene, mindestens aber das Reservierte."""
        return max(self.reserviert, self.verbraucht)

    def kosten(self) -> tuple[int, str]:
        """Betrag und Herkunft der Zahl.

        Ohne gepflegten Preis am Zugang ist beides ehrlich leer: null und
        ``none``. Eine geschätzte Zahl ohne Grundlage sähe im Verbrauch genauso
        aus wie eine gemessene, und wer seine Rechnung nachprüfen will, könnte
        die beiden nicht auseinanderhalten.
        """
        if not self.preis:
            return 0, "none"
        betrag = (self.gesamt * int(self.preis)) // 1_000_000
        return min(MAX_COST_MICROUNITS, max(0, betrag)), "estimate"


def _zahl(quelle: dict, name: str) -> int:
    wert = quelle.get(name)
    return wert if isinstance(wert, int) and wert >= 0 else 0


def _tokens(usage: dict) -> int:
    """Die Tokenzahl einer Realtime-Verbrauchsmeldung."""
    if not isinstance(usage, dict):
        return 0
    gesamt = usage.get("total_tokens")
    if isinstance(gesamt, int) and gesamt >= 0:
        return gesamt
    return _zahl(usage, "input_tokens") + _zahl(usage, "output_tokens")


def schaetzung(anweisungen: str, verlauf_zeichen: int) -> int:
    """Was die Sitzung mindestens kostet, bevor jemand ein Wort gesagt hat.

    Dieselbe grobe Regel wie überall im Panel — Zeichen durch vier. Sie ist zu
    ungenau, um damit abzurechnen, und genau genug, um zu verhindern, dass
    jemand mit leerem Kontingent noch eine Sitzung aufmacht. Abgerechnet wird
    hinterher mit den gemeldeten Zahlen.
    """
    return max(1, (len(anweisungen) + max(0, verlauf_zeichen)) // 4)


def oeffnen(
    db: Session,
    user: User,
    zugang: AiProvider,
    *,
    geschaetzt: int,
) -> Sitzungsverbrauch | None:
    """Kontingent prüfen und die Sitzung reservieren. ``None`` heisst: nein.

    Der Anrufer schliesst dann die Verbindung. Der Benutzer erfährt nicht,
    welche Grenze es war — das steht in seiner Verbrauchsübersicht, die er ohne
    Sonderrecht einsehen kann.
    """
    request_id = uuid4()
    try:
        frei = verbleibende_tokens(db, user)
        reserve_ai_usage(
            db,
            user,
            request_id=request_id,
            estimated_tokens=geschaetzt,
            estimated_cost_microunits=estimate_cost_microunits(zugang, geschaetzt),
            provider_id=zugang.id,
            model=zugang.default_model,
        )
        db.commit()
    except (AiQuotaExceeded, AiUsageConflict):
        db.rollback()
        return None
    return Sitzungsverbrauch(
        request_id=request_id,
        freiraum=frei,
        reserviert=geschaetzt,
        preis=zugang.token_price_micro_usd_per_million,
    )


def abschliessen(verbrauch: Sitzungsverbrauch, *, gescheitert: bool = False) -> None:
    """Die Reservierung schliessen — mit eigener Datenbanksitzung.

    Eigene Sitzung, weil die Sprachsitzung minutenlang ohne eine läuft (die des
    Requests ist längst zu). Und ausdrücklich in einem ``finally`` des
    Anrufers: eine Reservierung, die offen stehen bleibt, zählt dauerhaft gegen
    das Kontingent des Benutzers, ohne je abzulaufen. Das wäre schlimmer als
    eine zu hohe Buchung.
    """
    try:
        with SessionLocal() as db:
            ereignis = (
                db.query(AiUsageEvent)
                .filter(AiUsageEvent.request_id == str(verbrauch.request_id))
                .first()
            )
            if ereignis is None or ereignis.status != "reserved":
                return
            if gescheitert and verbrauch.verbraucht == 0:
                # Nichts gehört, nichts gesagt: die Sitzung kam nicht zustande.
                fail_ai_usage(db, ereignis)
                db.commit()
                return
            betrag, herkunft = verbrauch.kosten()
            complete_ai_usage(
                db,
                ereignis,
                actual_tokens=verbrauch.gesamt,
                actual_cost_microunits=betrag,
                cost_source=herkunft,
            )
            db.commit()
    except Exception:  # pragma: no cover - die Sitzung ist ohnehin vorbei
        # Ein Fehlschlag beim Buchen darf den Abbau der Verbindung nicht
        # aufhalten. Er gehört ins Protokoll, nicht in den Rückgabeweg.
        logger.warning("Sprachsitzung: Verbrauch konnte nicht gebucht werden", exc_info=True)
