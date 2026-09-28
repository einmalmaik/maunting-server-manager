from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class E2eeBlindMailbox(Base):
    """Der Besitznachweis einer Mailbox — ohne zu wissen, wem sie gehört.

    Zwei Spalten, und beide sagen nichts über Menschen: die Kennung der Mailbox
    und der SHA-256 eines Tokens, das der Client aus eigenem Schlüsselmaterial
    ableitet. Wer das Token vorlegt, darf lesen und schreiben. Der Server
    erfährt dabei nicht, wer das ist — er kann es nicht einmal nachrechnen,
    denn er sieht das Token nie im Klartext und die Kennung entsteht aus einem
    Geheimnis, das nur die Mitglieder haben.

    **Kein `user_id`, kein Fremdschlüssel, keine Adresse.** Dieselbe Bauform
    wie `vault_blind_buckets`, und aus demselben Grund: eine Spalte, die auf
    ein Konto zeigt, macht die ganze Übung zunichte.

    Diese Tabelle ist in Stufe 3 ein **zusätzliches** Schloss, nicht das
    einzige. `assert_mailbox_participant` bleibt davor stehen, solange die
    Mitgliedschaft nur als Datenbankzeile existiert und nicht kryptographisch
    getragen wird. Erst mit MLS fällt die kontogebundene Prüfung weg; bis
    dahin wäre ein Nachweis allein zu wenig, weil ein hinausgeworfenes
    Mitglied das Gruppengeheimnis kennt und sich seinen Nachweis jederzeit
    selbst ausrechnen könnte.
    """

    __tablename__ = "e2ee_blind_mailboxes"

    mailbox_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # SHA-256 als Hex ist genau 64 Zeichen. Länger anzulegen würde nur
    # suggerieren, hier käme auch etwas anderes hinein.
    auth_verifier: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)
