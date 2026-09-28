"""Der E2EE-Schluessel eines Geraets — der einzige Schluesselbegriff im Messenger.

Vorher gab es zwei: ``users.social_e2ee_public_key`` trug den Schluessel des
*Kontos*, dessen privater Teil mit einem abgetippten Wiederherstellungsschluessel
verpackt beim Server lag, damit jedes Geraet ihn holen konnte. Genau das steht
dem Double Ratchet im Weg: er ist eine lineare Kette, die pro Geraet
weiterlaeuft. Zwei Geraete mit demselben privaten Schluessel entschluesseln
dieselbe Nachricht, driften auseinander und verklemmen die Sitzung.

Jetzt gehoert der Schluessel dem Geraet. Jeder Browser, die Tauri-App und die
APK erzeugen sich beim ersten Oeffnen des Messengers lokal ein Paar und
veroeffentlichen hier den oeffentlichen Teil. Der private verlaesst das Geraet
nie — es gibt keinen Mechanismus mehr, der ihn irgendwohin verpacken koennte,
und damit auch nichts mehr, das ein Mensch abtippen muesste.

Ein Absender fragt die Liste ab und faechert seine Nachricht je Eintrag auf.
Was hier steht, ist deshalb reine Zustelladresse: eine bedeutungsfreie
Zufallskennung und ein oeffentlicher Schluessel. Kein Geheimnis, nichts, was
der Server nicht ohnehin weiterreichen muss.
"""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, true
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class UserE2eeDevice(Base):
    """Ein Geraet eines Kontos mit seinem veroeffentlichten E2EE-Schluessel."""

    __tablename__ = "user_e2ee_devices"
    __table_args__ = (
        # Ein Geraet meldet sich bei jedem Start erneut an. Ohne diese Schranke
        # entstuende bei jedem Start eine weitere Zeile, und der Absender
        # faecherte gegen Karteileichen auf.
        UniqueConstraint("user_id", "device_id", name="uq_user_e2ee_device"),
        # Die eine Abfrage im Betrieb: "welche Geraete hat dieser Benutzer?"
        Index("ix_user_e2ee_devices_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # Clientseitig gezogene Zufallskennung (16 Bytes hex). Bedeutungsfrei und
    # bewusst nicht aus Benutzer- oder Sitzungsdaten abgeleitet: sie steht im
    # Klartext in jedem Umschlag und darf dort nichts verraten.
    device_id: Mapped[str] = mapped_column(String(64), nullable=False)
    public_key_jwk: Mapped[str] = mapped_column(Text, nullable=False)
    # Der zweite oeffentliche Schluessel dieses Geraets: ECDSA P-256, und er
    # verschluesselt nichts. Er beglaubigt den Absender einer Gruppennachricht.
    #
    # Noetig, weil eine Gruppe einen *geteilten* Schluessel benutzt: damit kann
    # jedes Mitglied jede Nachricht der Gruppe erzeugen, und wer den Absender
    # aus der Nutzlast liest, glaubt dem Absender. Symmetrisch ist das nicht zu
    # schliessen — wer einen MAC pruefen kann, kann ihn auch rechnen.
    #
    # Nullable fuer den Bestand: ein Geraet traegt ihn beim naechsten Start
    # nach. Die Empfaengerregel steht in `gruppenSchluessel.ts`: fehlt er,
    # bleibt die Nachricht ungeprueft; ist er da, ist die Signatur Pflicht.
    signing_public_key_jwk: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Wiedererkennung in der Geraeteliste ("Arbeitsrechner"). Frei gewaehlt.
    label: Mapped[str] = mapped_column(String(64), nullable=False, default="")

    # Geraetebestaetigung: Ein neues Geraet muss auf einem bestehenden Geraet
    # bestaetigt werden, bevor es Nachrichten/Schluessel empfangen darf.
    # Erstes Geraet eines Kontos wird automatisch freigegeben.
    is_approved: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False
    )
    # Wer die Freigabe gegeben hat, und seine Unterschrift darueber. Beides geht
    # mit der Geraeteliste hinaus: die Clients pruefen die Unterschrift selbst.
    # `is_approved` allein waere ein Wort des Servers, und genau dem soll ein
    # Gegenueber nicht glauben muessen. Leer beim ersten Geraet eines Kontos und
    # beim Bestand von vor der Freigabe.
    approved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approval_signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Die Sitzungskette (Refresh-Familie), die dieses Geraet zuletzt gemeldet
    # hat. Entfernen sperrt sie aus, und das Access-Token faellt beim naechsten
    # Aufruf — nicht erst nach seinen 15 Minuten.
    auth_family: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    # Entscheidet, welches Geraet der Deckel verdraengt — nicht `created_at`:
    # das aelteste Geraet ist oft das meistgenutzte.
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
