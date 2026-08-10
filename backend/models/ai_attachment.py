"""Isolierte, verschluesselte AI-Anhaenge ohne Klartext-Dateipfad."""

from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class AiAttachment(Base):
    __tablename__ = "ai_attachments"
    __table_args__ = (
        CheckConstraint("status IN ('quarantined', 'ready', 'rejected')", name="ck_ai_attachments_status"),
        Index("ix_ai_attachments_conversation_created", "conversation_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(36), ForeignKey("ai_conversations.id", ondelete="CASCADE"), nullable=False)
    # Die Nachricht, mit der dieser Anhang abgeschickt wurde. `None` heisst:
    # hochgeladen, aber noch nicht gesendet — er haengt dann als Chip ueber dem
    # Eingabefeld und wartet.
    #
    # **Bewusst ohne Fremdschluessel.** SQLite kann kein `ADD CONSTRAINT`; die
    # Tests bauen das Schema mit `create_all`, die Produktion mit Alembic. Ein
    # Fremdschluessel nur hier hiesse, dass die beiden Wege verschiedene
    # Tabellen erzeugen. Dieselbe Entscheidung wie bei
    # `ai_action_proposals.run_id`. Das Aufraeumen macht `truncate_from`.
    message_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    original_name: Mapped[str] = mapped_column(String(128), nullable=False)
    media_type: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    content_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    extracted_text_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="quarantined", nullable=False, index=True)
    rejection_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Wieviele Stellen beim Aufnehmen unkenntlich gemacht wurden. Frueher wurde
    # eine Datei mit einem Tokenmuster komplett abgewiesen — bei echten
    # Serverlogs passiert das staendig. Jetzt wird redigiert, und diese Zahl
    # sagt dem Benutzer, dass es passiert ist.
    redacted_spans: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
