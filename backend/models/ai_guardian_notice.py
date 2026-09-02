"""Was die KI zu einem Guardian-Vorfall bereits veranlasst hat.

Eine Zeile je Paar aus Vorfall und Benutzer. Sie beantwortet genau eine Frage —
*ist dieser Mensch wegen dieses Vorfalls schon versorgt worden?* — und sie
beantwortet sie fuer **beide** Wege, die es dafuer gibt:

* ``briefed``  — es gab keine Freigabe. Die KI hat den Vorfall beim naechsten
  Chat erwaehnt, mehr nicht. Kein Lauf, kein Anbieteraufruf, ``run_id`` bleibt
  NULL.
* ``healing``  — es gab eine Freigabe. Ein Heilungslauf wurde gestartet;
  ``run_id`` zeigt darauf.

Ohne diese Tabelle waere die Kopplung unbrauchbar. Der Ausloeser laeuft im
Sekundentakt eines Schedulers, und ein Vorfall bleibt offen, bis ihn jemand
loest — der Takt saehe ihn also bei jedem Durchlauf erneut und startete alle
sechzig Sekunden einen weiteren Lauf auf demselben Server. Das Kontingent des
Benutzers waere in einer Viertelstunde aufgebraucht.

Die Eindeutigkeit steht deshalb **in der Datenbank** und nicht in einer Pruefung
davor. Laeuft das Panel je mit mehreren Arbeitsprozessen, gibt es den Scheduler
mehrfach, und ``max_instances=1`` gilt nur innerhalb eines Prozesses; dann ist
diese Bedingung die einzige, die noch haelt.

Bewusst **kein** Zustandsfeld fuer den Ausgang der Heilung. Der steht am Lauf
(``ai_runs.status``, ``stop_reason``), und ihn hier zu spiegeln hiesse, zwei
Wahrheiten zu pflegen. Diese Tabelle sagt nur: es wurde etwas veranlasst.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


#: Die erlaubten Arten. Sie stehen hier und werden unten in den CHECK gegossen —
#: ein Tippfehler in einer Migration oder in einem Skript soll nicht als gueltige
#: dritte Art durchgehen und den Ausloeser stumm anders entscheiden lassen.
ARTEN = ("briefed", "healing")


class AiGuardianNotice(Base):
    __tablename__ = "ai_guardian_notices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    incident_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    # ``SET NULL`` und nicht ``CASCADE``: raeumt jemand alte Laeufe ab, ist die
    # Aussage "dieser Vorfall war versorgt" trotzdem weiterhin wahr. Mit
    # ``CASCADE`` verschwaende sie mit dem Lauf, und der Ausloeser finge von
    # vorne an — auf einem Vorfall, der laengst behandelt wurde.
    run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("ai_runs.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )

    incident: Mapped["Incident"] = relationship("Incident")

    __table_args__ = (
        UniqueConstraint("incident_id", "user_id", name="uq_ai_guardian_notices_incident_user"),
        CheckConstraint(
            "mode IN (" + ", ".join(f"'{art}'" for art in ARTEN) + ")",
            name="ck_ai_guardian_notices_mode",
        ),
    )
