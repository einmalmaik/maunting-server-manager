"""Skills als Prosa — erlernte Vorgehensweisen, keine gespeicherten Klickfolgen.

Der Vorgaenger war ein Makro: eine feste Folge von hoechstens zwanzig
Tool-Aufrufen aus einer engen Allowlist. Das ist ein anderes Werkzeug als das,
was ChatGPT, Claude oder Hermes unter einem Skill verstehen — dort ist ein
Skill eine **Textdatei**, die beschreibt, *wie* man an eine Sache herangeht.
Das Modell liest sie und entscheidet weiter selbst.

Der Unterschied ist nicht nur begrifflich. Prosa fuehrt nichts aus. Ein
automatisch gelernter Skill kann damit nichts, was das Modell nicht ohnehin
duerfte — er aendert nur die Herangehensweise. Genau deshalb ist Selbstlernen
hier vertretbar, waehrend das automatische Erzeugen ausfuehrbarer Schrittfolgen
es nicht waere.

`scope_identity` traegt die Zugehoerigkeit als Zeichenkette ("global" oder
"team:7") statt als nullbare Spalte: in PostgreSQL kollidieren NULL-Werte in
einer UNIQUE-Bedingung nicht, zwei globale Skills mit demselben Schluessel
waeren also erlaubt gewesen. Dasselbe Muster wie beim Memory.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column


from database import Base


class AiSkill(Base):
    __tablename__ = "ai_skills"
    __table_args__ = (
        CheckConstraint("origin IN ('operator', 'ai')", name="ck_ai_skills_origin"),
        CheckConstraint("status IN ('active', 'pending')", name="ck_ai_skills_status"),
        UniqueConstraint("scope_identity", "skill_key", name="uq_ai_skills_scope_key"),
        Index("ix_ai_skills_scope", "scope_identity", "enabled"),
        Index("ix_ai_skills_team", "team_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    # "global" oder "team:{id}". Persoenliche Skills gibt es bewusst nicht —
    # wer allein arbeitet, hat sein Ein-Mann-Team.
    scope_identity: Mapped[str] = mapped_column(String(64), nullable=False)
    team_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("teams.id", ondelete="CASCADE"), nullable=True
    )
    skill_key: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # Entscheidet, ob das Modell den Skill ueberhaupt anfasst: nur `name` und
    # `description` stehen dauerhaft im Systemprompt. Die Beschreibung muss
    # deshalb sagen, *was* der Skill tut und *wann* er passt.
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    # Der eigentliche Text. Wird erst geladen, wenn das Modell ihn anfordert.
    body: Mapped[str] = mapped_column(Text, nullable=False)
    # "operator" = ein Mensch hat ihn geschrieben, "ai" = selbst gelernt.
    origin: Mapped[str] = mapped_column(String(16), nullable=False, default="operator")
    # "pending" gibt es nur bei global gelernten Skills, die aus einem Gespraech
    # ohne `ai.skills.manage` entstanden sind — sie warten auf die Freigabe des
    # Betreibers und wirken bis dahin fuer niemanden.
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # Fuer die Auswahl, wenn mehr Skills vorhanden sind als in den Prompt
    # passen. Dieselbe Bauart wie beim Memory: Klartextvektor neben dem
    # ohnehin unverschluesselten Namen.
    embedding_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
