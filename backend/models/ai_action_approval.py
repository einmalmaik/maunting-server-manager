"""Eine Freigabe, die per E-Mail erteilt wird.

Der Fall: der autonome Modus laeuft, ein Vorfall wird nachts bearbeitet, und
mitten in der Reparatur braucht ein Vorschlag eine Bestaetigung — weil er
loescht, weil er unumkehrbar ist, oder weil das Stundenkontingent des Benutzers
gerade ausgegangen ist. Bis hierher endete der Lauf an dieser Stelle mit dem
Satz "ohne Freigabe kann ich da nichts machen", und der Server blieb kaputt.
Der Betreiber ist einen Monat im Urlaub; eine Karte im Panel erreicht ihn nicht.

Die Reichweite ist ausdruecklich **klein**: gefragt wird nur nach dem, was der
autonome Modus ohnehin nie ohne Klick tut (`ALWAYS_CONFIRM_TOOLS`), und nur
dann, wenn ueberhaupt eine Autonomie-Freigabe erteilt wurde. Ohne sie aendert
sich nichts — kein Lauf, kein Anbieteraufruf, keine Mail.

**Form nach `models/login_challenge.py`**, und aus demselben Grund: der
Empfaenger bekommt ein zufaelliges, undurchsichtiges Token, gespeichert wird nur
sein SHA-256. Wer die Datenbank liest, kann damit keine Freigabe erteilen.

Was diese Zeile ausdruecklich **nicht** tut, ist `autonomy_allows` zu bewegen.
Eine E-Mail-Freigabe ist ein dritter Zustand — "ein Mensch hat zugestimmt,
ausser Haus" — und wird als solcher auditiert (`confirmed_via='email'`), nie als
Flag am Grant. Sonst waere aus einer einzelnen Zustimmung eine dauerhafte
Erweiterung der Autonomie geworden, und genau das hat niemand erlaubt.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


#: Was am Ende einer Freigabe stehen kann. ``None`` heisst: noch offen.
ENTSCHEIDUNGEN = ("approved", "rejected")


class AiActionApproval(Base):
    __tablename__ = "ai_action_approvals"
    __table_args__ = (
        CheckConstraint(
            "decision IS NULL OR decision IN ('approved', 'rejected')",
            name="ck_ai_action_approvals_decision",
        ),
        # Der Aufraeumlauf sucht nach abgelaufenen Zeilen, der Takt nach den
        # noch offenen. Beides laeuft ueber diesen Index.
        Index("ix_ai_action_approvals_expires", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)

    #: SHA-256 des ausgegebenen Tokens (Hex, 64 Zeichen). Der Klartext steht
    #: ausschliesslich in der Mail.
    token_hash: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )

    #: Der Vorschlag, um den es geht. CASCADE: verschwindet der Vorschlag,
    #: gibt es nichts mehr freizugeben, und ein Link auf ein Nichts ist
    #: schlimmer als ein toter Link — er koennte einen spaeteren Vorschlag
    #: derselben Kennung treffen.
    proposal_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("ai_action_proposals.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    #: Der Lauf, der geweckt wird. SET NULL: ein aufgeraeumter Lauf macht die
    #: Freigabe nicht ungueltig — die Aktion ist trotzdem auszufuehren, es hoert
    #: danach nur niemand mehr zu.
    run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("ai_runs.id", ondelete="SET NULL"), nullable=True
    )

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    #: Einmalverbrauch. Gesetzt wird das per **bedingtem UPDATE**, damit zwei
    #: gleichzeitige Klicks auf denselben Link nicht zwei Ausfuehrungen
    #: ergeben — auf SQLite gibt es kein `SELECT ... FOR UPDATE`.
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    decision: Mapped[str | None] = mapped_column(String(16), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


def hash_approval_token(token: str) -> str:
    """SHA-256 eines Freigabe-Tokens (Hex).

    Eigene Funktion statt eines Aufrufs von `hash_challenge_token`: die beiden
    Token haben verschiedene Lebensdauern und verschiedene Wirkungen, und ein
    gemeinsamer Helfer waere die Einladung, sie irgendwann in einer Tabelle
    zusammenzulegen.
    """
    return hashlib.sha256(token.encode()).hexdigest()
