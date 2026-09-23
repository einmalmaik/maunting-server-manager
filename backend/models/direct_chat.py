from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class DirectChat(Base):
    """Ein Direktgespräch — und seit Stufe 6b nichts weiter als seine Mailbox.

    Bis 09/2026 standen hier `user_a_id`, `user_b_id` und `initiated_by_user_id`
    im Klartext. Das war die Tabelle, die auf die Frage „wer schreibt mit wem?"
    antwortete: ein `SELECT` je Konto, und das ganze soziale Netz lag offen —
    ohne eine einzige Nachricht zu entschlüsseln. Wer über wen etwas erfahren
    will, braucht selten den Inhalt; es genügt zu wissen, dass jemand mit einer
    Suchtberatung, einer Kanzlei oder einer Gewerkschaft schreibt.

    Diese drei Spalten sind entfernt. Was bleibt, ist die Kennung der Mailbox
    und wann sie zuletzt gebraucht wurde. Die Zeile sagt damit: „dieses
    Gespräch gibt es", nicht „diese beiden Menschen führen es".

    *Wofür sie dann noch da ist:* als Sperre. `_mailbox_ist_ableitbar` fragt
    sie, damit ein Fremder eine bestehende Gesprächsmailbox nicht mit einem
    eigenen Besitznachweis belegen und die echten Teilnehmer aussperren kann.
    Dafür braucht es keinen Namen, nur die Kennung.

    *Was das nicht heilt, und das gehört gesagt:* Eine **abgeleitete** Kennung
    (`sha256("msm:dm:<min>:<max>")`) ist aus zwei kleinen Ganzzahlen
    nachrechenbar. Wer die Datenbank hat, kann sie für alle Paare durchprobieren
    und die Zuordnung rekonstruieren. Das Gegenmittel ist nicht diese Migration,
    sondern Stufe 3d: sobald beide Seiten das Chatgeheimnis haben, zieht das
    Gespräch in eine Mailbox um, die aus Zufall entsteht und die niemand
    ausrechnen kann. Was hier verschwindet, ist die bequeme Liste — und die
    Verpflichtung, sie zu führen.
    """

    __tablename__ = "direct_chats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True, autoincrement=True)
    blind_mailbox_id: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True, unique=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )
