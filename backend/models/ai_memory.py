"""Verschluesseltes, explizit steuerbares AI-Memory."""

from datetime import datetime, timezone

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class AiMemoryPreference(Base):
    __tablename__ = "ai_memory_preferences"

    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)


class AiMemoryEntry(Base):
    """Ein gemerkter Fakt — mit dem Wenigen, was ein Gedaechtnis ausmacht.

    Ein reiner Schluessel-Wert-Speicher ist noch kein Gedaechtnis. Drei Felder
    unterscheiden das eine vom anderen:

    - ``origin`` trennt "der Benutzer hat es gesagt" von "die KI hat es
      abgeleitet". Eine Ableitung darf vorsichtiger behandelt werden als eine
      ausdrueckliche Ansage.
    - ``use_count`` und ``last_used_at`` machen sichtbar, was tatsaechlich
      gebraucht wird. Reicht der Platz im Kontext nicht fuer alles, faellt
      zuerst weg, was nie abgerufen wurde — statt dessen, was zufaellig hinten
      im Alphabet steht.

    Bewusst **kein** Vektorfeld: bei hoechstens 100 Eintraegen je Scope passt in
    aller Regel alles gleichzeitig in den Kontext, und dann liefert das
    Sprachmodell das Verstaendnis — sprachunabhaengig und ohne Index. Ein
    Embedding waere spaeter eine zusaetzliche Spalte, kein Umbau.
    """

    __tablename__ = "ai_memory_entries"
    __table_args__ = (
        CheckConstraint("scope IN ('user', 'server', 'panel')", name="ck_ai_memory_entries_scope"),
        CheckConstraint("origin IN ('user', 'ai')", name="ck_ai_memory_entries_origin"),
        UniqueConstraint("scope_identity", "key", name="uq_ai_memory_scope_key"),
        Index("ix_ai_memory_owner_scope", "owner_user_id", "scope"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    server_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("servers.id", ondelete="CASCADE"), nullable=True, index=True)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_identity: Mapped[str] = mapped_column(String(128), nullable=False)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    value_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    # "user" = ausdruecklich hinterlegt, "ai" = von der KI gemerkt.
    origin: Mapped[str] = mapped_column(String(8), nullable=False, default="user")
    use_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Lokal berechneter Vektor als JSON-Liste. Bewusst *nicht* verschluesselt:
    # der `key` daneben steht ohnehin im Klartext und verraet mehr, und nur so
    # kann die Auswahl vor dem Entschluesseln stattfinden — was pro Chatnachricht
    # dutzende Sidecar-Aufrufe spart. NULL heisst: noch nicht berechnet.
    embedding_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Womit gerechnet wurde. Passt es nicht zum geladenen Modell, wird der
    # Vektor ignoriert statt falsche Aehnlichkeiten zu liefern.
    embedding_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
