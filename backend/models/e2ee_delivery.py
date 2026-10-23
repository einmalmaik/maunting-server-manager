"""Die gerätebezogene Zustellung — der Transport mit Quittung.

Bis 23.10.2026 lief die Zustellung über **blind geteilte** Mailboxen: der
Absender legte eine Kopie je Zielgeraet in die Mailbox des Gespraechs, und die
Kopie blieb dort liegen, bis die 30-Tage-Frist ablief. Zwei Geraete desselben
Kontos teilten sich dieselbe Mailbox, und ein Geraet, das die Nachricht nicht
lesen konnte, blockierte die fuer die anderen.

Diese Tabellen fuehren den zweiten Weg: **je Geraet eine opake Inbox-Adresse,
je Zustellung ein Auftrag, und ein Quittung, das den Auftrag loescht.** Der
Inhalt bleibt derselbe: Chiffretext, den nur das Zielgeraet oeffnen kann.

Die Schranken:

* Keine dieser Tabellen hat einen Fremdschluessel auf ``users``. Sie haengen
  an der Geraetezeile, und deren Kaskade raeumt sie mit dem Konto auf.
* Die Inbox-Adresse ist ein Hash ueber ein Geheimnis, das **nur** das Geraet
  kennt. Der Server kann sie nicht nachrechnen — er kennt nur den oeffentlichen
  Signaturschluessel des Geraets.
* Die Nutzlast traegt keinen Inhalt, keinen Absender und keine Kennung eines
  Kontos. Sie ist Chiffretext, eine Pruefsumme, eine Groesse und eine Frist.
* Der Idempotenzrest nach dem Loeschen der Nutzlast nennt nur noch die
  Kennung, den Hash der Anfrage, das Ergebnis und die urspruengliche Frist.
  Er beantwortet eine wiederholte Anfrage, ohne ein zweites Versandarchiv
  anzulegen.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Sequence,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


def _jetzt() -> datetime:
    return datetime.now(timezone.utc)


class E2eeDeviceInbox(Base):
    """Die opake Zustelladresse eines Geraets.

    Sie haengt an der Geraetezeile und faellt mit ihr weg. Der Hash traegt
    keine Kontonummer: er enthaelt ein Geheimnis, das nur das Geraet kennt.
    """

    __tablename__ = "e2ee_device_inboxes"
    __table_args__ = (
        UniqueConstraint("device_id", name="uq_e2ee_device_inbox_device"),
        UniqueConstraint("inbox_hash", name="uq_e2ee_device_inbox_hash"),
        Index("ix_e2ee_device_inbox_device", "device_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user_e2ee_devices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 64 Hex-Zeichen (SHA-256) ueber ein Geraetgeheimnis.
    inbox_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    erstellt_am: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_jetzt, nullable=False)
    # Die Frist der Inbox. Eine Inbox laeuft nicht ewig: nach 30 Tagen ist
    # das Geraet entweder weg oder neu eingetragen.
    ablauf_am: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class E2eeDeliveryPayload(Base):
    """Das Chiffretextobjekt — ohne Ziel, ohne Absender, ohne Inhalt.

    Mehrere Zustellungen koennen dasselbe Objekt tragen (ein Gruppenpaket an
    viele Geraete). Es faellt, sobald keine offene Zustellung mehr darauf zeigt.
    """

    __tablename__ = "e2ee_delivery_payloads"
    __table_args__ = (
        Index("ix_e2ee_delivery_payload_ablauf", "ablauf_am"),
    )

    # Die Objektkennung ist eine UUID — vom Client gewaehlt, damit er einen
    # wiederholten Versand unter derselben Kennung erkennen kann.
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    ciphertext_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    groesse: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    angenommen_am: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_jetzt, nullable=False)
    # Die Frist steht bei der Annahme fest. Eine Wiederholung verlaengert sie
    # nicht.
    ablauf_am: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class E2eeDelivery(Base):
    """Ein offener Zustellauftrag: ein Chiffretextobjekt fuer eine Inbox."""

    __tablename__ = "e2ee_deliveries"
    __table_args__ = (
        UniqueConstraint("inbox_hash", "id", name="uq_e2ee_delivery_inbox_id"),
        Index("ix_e2ee_delivery_inbox_seq", "inbox_hash", "laufende_seq"),
        Index("ix_e2ee_delivery_ablauf", "ablauf_am"),
    )

    # Die Zustellkennung: vom Client gewaehlt (UUID) und fuer alle Geraete
    # verschieden. Sie ist der Schluessel des Idempotenzrests.
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    payload_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("e2ee_delivery_payloads.id", ondelete="CASCADE"), nullable=False
    )
    inbox_hash: Mapped[str] = mapped_column(
        String(64), ForeignKey("e2ee_device_inboxes.inbox_hash", ondelete="CASCADE"), nullable=False
    )
    # Die Annahmenummer. Sie ist **global** und waechst nur — aus demselben
    # Grund, aus dem die Kennung eines Umschlags in der alten Mailbox global
    # wuchs: der Absender rechnet seine Haekchen darueber. Eine Nummer je Inbox
    # waere fuer zwei Geraete desselben Kontos nicht vergleichbar, und die
    # Quittung des einen markierte beim anderen die falsche Nachricht.
    #
    # Sie dient zugleich als Cursor des Abrufs: der Client liest ab hier
    # weiter. Die Nummern einer Inbox sind darin enthalten, also monoton.
    laufende_seq: Mapped[int] = mapped_column(
        BigInteger,
        Sequence("e2ee_delivery_seq"),
        nullable=False,
        unique=True,
        index=True,
    )
    angenommen_am: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_jetzt, nullable=False)
    ablauf_am: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Gesetzt, wenn der Auftrag quittiert wurde. Die Zeile bleibt als
    # Idempotenzrest, bis die Frist ablaeuft.
    quittiert_am: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Die Beziehungen kennt die Datenbank ohnehin — sie stehen als
    # Fremdschluessel in den Spalten. Fuer die Schreibmaschine sind sie
    # noetig: ohne sie weiss sie nicht, dass der Auftrag **nach** dem
    # Chiffretextobjekt geschrieben werden muss, und der Fremdschluessel
    # schlaegt fehl.
    payload: Mapped["E2eeDeliveryPayload"] = relationship()
    inbox: Mapped["E2eeDeviceInbox"] = relationship()


class E2eeDeliveryReceipt(Base):
    """Der begrenzte Idempotenzrest.

    Nach dem Loeschen der Nutzlast bleibt nur, was eine wiederholte Anfrage
    beantworten muss: die Kennung, der Hash der Anfrage, das Ergebnis und die
    urspruengliche Frist. Kein Inhalt, kein Chiffretext, keine Groesse.
    """

    __tablename__ = "e2ee_delivery_receipts"
    __table_args__ = (
        UniqueConstraint("inbox_hash", "delivery_id", name="uq_e2ee_delivery_receipt"),
        Index("ix_e2ee_delivery_receipt_ablauf", "ablauf_am"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    inbox_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    delivery_id: Mapped[str] = mapped_column(String(36), nullable=False)
    # Der Hash der Anfrage. Gleiche Kennung mit anderen Bytes ist ein
    # Konflikt, keine Wiederholung.
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # 1 = angenommen, 2 = Konflikt (gleiche Kennung, andere Bytes).
    ergebnis: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    angenommen_am: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_jetzt, nullable=False)
    ablauf_am: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
