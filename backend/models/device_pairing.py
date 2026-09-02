"""Ein Kopplungscode — der einzige Weg, wie das Smart System hereinkommt.

Die Desktop-App meldet sich nicht mit Benutzername und Passwort an. Sie kann
es nicht einmal sinnvoll: bei aktivem Captcha verlangt ``/api/auth/login`` ein
Turnstile-Token, und ein Captcha-Widget in einem Tauri-WebView scheitert daran,
dass Cloudflare-Schluessel an Domains haengen und ``tauri.localhost`` keine ist.
Ein 2FA-Feld im Desktop-Fenster waere die naechste Kopie derselben Strecke.

Stattdessen dreht sich die Richtung: **wer schon angemeldet ist, laedt ein
Geraet ein.** Im Panel entsteht ein kurzlebiger Code, die App loest ihn ein und
bekommt dafuer eine Sitzung. Passwort, 2FA und Captcha bleiben, wo sie hin
gehoeren — im Browser, hinter dem Vorhang, den die App gar nicht erst anfasst.

Der Code ist hier das **ganze** Geheimnis. Beim Node-Enrollment
(``models/node_enrollment.py``) identifiziert der Anzeigecode nur, und ein
langes Claim-Secret authentifiziert daneben; hier tippt ihn ein Mensch ab, es
gibt also nur ihn. Deshalb: zwoelf Zeichen aus einem 32er-Alphabet, zehn
Minuten Frist, genau einmal einloesbar, und in der Datenbank steht nur sein
SHA-256. Wer die Tabelle liest, kann sich damit nicht anmelden.
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class DevicePairing(Base):
    """Eine offene Einladung an ein Geraet, gueltig fuer wenige Minuten."""

    __tablename__ = "device_pairings"
    __table_args__ = (
        # Die eine Abfrage im Betrieb: "hat dieser Benutzer noch offene
        # Einladungen?" — fuer die Anzeige und fuers Aufraeumen.
        Index("ix_device_pairings_user_expires", "user_id", "expires_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # Nur der Hash. Der Klartext existiert genau einmal, in der Antwort auf die
    # Anfrage, die ihn erzeugt hat — danach nirgendwo mehr.
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    # Wie das Geraet heissen soll ("Arbeitsrechner"). Frei gewaehlt, nur zur
    # Wiedererkennung in der Geraeteliste.
    label: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    # Die Refresh-Familie der Sitzung, die daraus entstanden ist. Erst damit
    # laesst sich ein einzelnes Geraet wieder aussperren, ohne alle anderen
    # Sitzungen des Benutzers mitzunehmen.
    family: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Gesetzt heisst: verbraucht. Ein zweites Einloesen desselben Codes wird
    # daran erkannt und abgewiesen — auch innerhalb der Frist.
    redeemed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
