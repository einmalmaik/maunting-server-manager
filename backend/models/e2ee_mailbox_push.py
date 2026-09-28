"""Wohin zugestellt wird, wenn die Anwendung zu ist — ohne zu wissen, wem.

`push_subscriptions` daneben beantwortet dieselbe Frage fuer ein Konto: „wohin
fuer Benutzer 42?". Genau diese Frage soll der Messenger nicht mehr stellen.
Seit die Mailbox die Adresse ist (`SyncEventService.publish(mailbox_id=...)`),
waere ein kontogebundener Push der letzte Faden, an dem die Zuordnung noch
haengt — der Server wuesste wieder, welches Konto Post in einer Mailbox bekommt,
die er sonst niemandem zuordnen kann.

Eine Zeile sagt deshalb nur: **in diese Mailbox faellt etwas — schick es an
diese Adresse.** Kein `user_id`, kein Fremdschluessel. Dieselbe Bauform wie
`e2ee_blind_mailboxes` und `vault_blind_buckets`, und aus demselben Grund.

## Was der Server hier trotzdem erfaehrt

Dass eine Browserinstallation sich fuer eine Mailbox interessiert. Das ist
Geraete-Ebene, nicht Konto-Ebene, und es ist der Preis dafuer, dass WebPush eine
Klartext-Adresse braucht — verschluesselt liesse sich dorthin nichts schicken.
Wegbauen laesst sich das nicht, also steht es hier und nicht in einer Fussnote.

**Nicht** erfaehrt er, welches Konto dahintersteht: die Adresse kommt beim
Eintragen aus dem Browser, und die Zeile behaelt von der Sitzung, die sie
angelegt hat, nichts.

## Warum `endpoint_hash` neben `endpoint`

Zweimal derselbe Wert, und beide werden gebraucht. Der Hash traegt die
Eindeutigkeit (eine `Text`-Spalte laesst sich nicht ueberall indizieren) und ist
zugleich das, was ein Absender als `push_ausnahme` mitschickt, damit sein
eigener Browser nicht die Meldung ueber seine eigene Nachricht bekommt. Die
Adresse selbst steht daneben, weil der Versand sie im Klartext braucht.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class E2eeMailboxPush(Base):
    """Eine Zustelladresse fuer genau eine blinde Mailbox."""

    __tablename__ = "e2ee_mailbox_push"
    __table_args__ = (
        # Ein Browser abonniert eine Mailbox einmal. Ein erneutes Melden — und
        # gemeldet wird bei jeder Anmeldung, siehe `mailboxPush.ts` — soll die
        # Zeile uebernehmen und keine zweite anlegen.
        UniqueConstraint("mailbox_id", "endpoint_hash", name="uq_mailbox_push_ziel"),
        # Die eine Abfrage im Betrieb: „wohin fuer diese Mailbox?"
        Index("ix_e2ee_mailbox_push_mailbox", "mailbox_id"),
        # Und die zweite, beim Abmelden: „alles, was zu diesem Browser gehoert".
        Index("ix_e2ee_mailbox_push_endpoint", "endpoint_hash"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mailbox_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # SHA-256 der Adresse als Hex, immer 64 Zeichen.
    endpoint_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Wie in `push_subscriptions`: `Text`, weil die Laenge der Hersteller-Adresse
    # nirgends zugesichert ist.
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    p256dh: Mapped[str] = mapped_column(String(255), nullable=False)
    auth: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
