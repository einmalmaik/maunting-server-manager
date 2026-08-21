"""Ein Auftrag an den Rechner des Benutzers — die eine Bruecke nach draussen.

Alle bisherigen Werkzeuge der KI laufen **im Panel**: sie lesen die Datenbank
oder rufen einen Node an, dessen Adresse das Panel kennt. Die Werkzeuge des
Smart Systems koennen das nicht. Ihre Arbeit passiert auf dem Rechner, der vor
dem Benutzer steht — hinter dessen Router, ohne feste Adresse, oft gar nicht
eingeschaltet. Das Panel kann ihn nicht anrufen.

Also dreht sich die Richtung um, genau wie beim Node — nur andersherum: **der
Rechner fragt, das Panel antwortet.** Ein Werkzeugaufruf wird zu einer Zeile
hier; die Desktop-App holt sie ab, arbeitet sie ab und meldet das Ergebnis
zurueck. Das Ergebnis weckt den Lauf (``ai_run_service.lauf_fortsetzen``) — der
gleiche Weg, den eine Bestaetigung und ein fertiger Server-Vorgang schon gehen.

Warum eine Tabelle und keine offene Verbindung: eine Liste verbundener Clients
im Speicher waere bei mehreren Arbeitsprozessen falsch (der Auftrag landet im
Prozess ohne die Verbindung), ueberlebt keinen Neustart und muesste jeden
Abbruch selbst bemerken. Eine Zeile mit Frist kann all das nicht falsch machen.
"""

from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


# Offen heisst: der Lauf wartet noch darauf. Genau diese beiden Zustaende
# zaehlt `desktop_job_service.offene`, bevor ein Lauf geweckt wird.
OFFEN = ("pending", "taken")
ABGESCHLOSSEN = ("done", "failed", "expired")
ZUSTAENDE = (*OFFEN, *ABGESCHLOSSEN)


class DesktopJob(Base):
    """Ein Werkzeugaufruf, der auf dem Rechner des Benutzers ausgefuehrt wird."""

    __tablename__ = "desktop_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN (" + ", ".join(f"'{zustand}'" for zustand in ZUSTAENDE) + ")",
            name="ck_desktop_jobs_status",
        ),
        # Die eine Abfrage, die im Betrieb oft laeuft: "hat mein Rechner etwas
        # zu tun?" — je Benutzer, nach Zustand, aelteste zuerst.
        Index("ix_desktop_jobs_user_status", "user_id", "status", "created_at"),
        # Und die Gegenfrage beim Wecken: "wartet dieser Lauf noch auf etwas?"
        Index("ix_desktop_jobs_run_status", "run_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # Welcher Lauf auf das Ergebnis wartet. `CASCADE`: ohne den Lauf ist der
    # Auftrag sinnlos — niemand koennte sein Ergebnis mehr entgegennehmen.
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("ai_runs.id", ondelete="CASCADE"), nullable=False
    )
    # Die `tool_call_id` der Runde. Sie steht hier, weil das Modell zu jedem
    # Aufruf genau eine Antwort erwartet und die Zuordnung sonst raten muesste.
    tool_call_id: Mapped[str] = mapped_column(String(64), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)

    # Argumente und Ergebnis liegen verschluesselt (DIS, AAD an die Auftrags-ID
    # gebunden). Beides kann Dateiinhalte und Pfade aus dem Rechner des
    # Benutzers tragen — das ist genau die Sorte Daten, die in der Datenbank
    # eines Panels nichts im Klartext zu suchen hat.
    payload_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    result_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    # Kurzcode fuer den Fehlschlag, unverschluesselt: er enthaelt keine Daten
    # des Benutzers, nur die Art des Scheiterns (Protokoll, Anzeige).
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Ab wann der Auftrag verfallen ist. Ohne Frist bliebe ein Lauf ewig
    # stehen, wenn der Rechner zwischendurch ausgeht — die Frist ist der
    # Unterschied zwischen "wartet" und "haengt".
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    taken_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
