from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ChatGroupConfig(Base):
    """Der Gruppenzustand — verschlüsselt, mit fortlaufender Revision.

    Hier stehen die eigenen Rollen einer Gruppe: Name, Beschreibung, Rechte.
    Bis 09/2026 standen sie **nirgends**: ``handleCreateRole`` im Rechte-Dialog
    rief ``setRoles`` auf React-State und meldete „Rolle erstellt". Beim
    Schliessen des Dialogs war sie weg. Es gab weder Tabelle noch Route.

    **Warum ein undurchsichtiger Block und keine Spalten.** Betreibervorgabe
    vom 22.09.2026: *„niemals im Klartext"*, und der Schlüssel liegt bei den
    Geräten der Mitglieder, nicht auf dem Server. Der DIS-Sidecar scheidet
    dafür aus — sein Schlüssel steht in der Serverkonfiguration, der Betreiber
    könnte also mitlesen. ``blob`` ist deshalb ein gewöhnlicher
    ``sv-e2ee-group-v1:``-Umschlag unter dem Gruppenschlüssel, genau wie eine
    Nachricht. Der Server speichert ihn und liest ihn nie.

    **Was der Server trotzdem kann und muss: die Revision.** Zero-Knowledge
    schützt den Inhalt, nicht die Ablage — dieselbe Lehre wie beim
    Passwort-Tresor (``vault_entry.py``). Ohne eine monotone Revision könnte
    jeder Mitschreibende einen alten Stand zurückspielen und damit einen
    Rechteentzug rückgängig machen, ohne je etwas entschlüsselt zu haben. Der
    Server vergleicht deshalb Zahlen, die er versteht, und weist alles ab, was
    nicht genau eins weiter ist.

    **Wer schreiben darf, steht im Block.** Die Unterschrift liegt *innerhalb*
    des Umschlags (``nutzlastSignatur.ts``) und nicht in einer eigenen Spalte.
    Eine Spalte „unterschrieben von Gerät X" wäre wieder eine Metadatenzeile
    im Klartext — sie verriete, wer diese Gruppe verwaltet. Die Mitglieder
    prüfen die Unterschrift nach dem Entschlüsseln; der Server prüft nur, dass
    der Aufrufer überhaupt Mitglied ist und verwalten darf.

    Genau eine Zeile je Gruppe: der jeweils gültige Stand. Alte Revisionen
    werden nicht aufgehoben — sie wären ein wachsender Bestand verschlüsselter
    Gruppendaten ohne Nutzen, und ein Rückspielschutz sind sie nicht.
    """

    __tablename__ = "chat_group_configs"

    group_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("chat_groups.id", ondelete="CASCADE"),
        primary_key=True,
    )
    blob: Mapped[str] = mapped_column(Text, nullable=False)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )
