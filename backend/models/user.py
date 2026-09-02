from datetime import datetime, timezone
import hashlib

from sqlalchemy import Boolean, CheckConstraint, String, DateTime, ForeignKey, Integer, text, true, false
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


#: Wie weit die KI auf dem Rechner des Benutzers **ausserhalb** des freigegebenen
#: Ordners gehen darf. Die drei Werte sind aufsteigend zu lesen:
#:
#: * ``aus``       — der Systembereich ist fuer die KI nicht da.
#: * ``lesen``     — sie darf hineinsehen, aber nichts aendern.
#: * ``schreiben`` — sie darf dort arbeiten (nach Bestaetigung durch den Menschen).
#:
#: Wahrheitsquelle wie ueberall im Haus: der CheckConstraint unten wird daraus
#: erzeugt (`models/ai_meldung.MELDUNGSARTEN` geht denselben Weg).
SYSTEMBEREICHE = ("aus", "lesen", "schreiben")

#: Der Wert, auf den ein neues Konto und jeder unlesbare Bestandswert faellt.
#:
#: ``lesen`` ist hier **nicht** die goldene Mitte, sondern der heutige Zustand:
#: ``desktop_system`` listet seit dem 21.08.2026 jedes Verzeichnis des Rechners
#: auf, auch ``C:\Windows``. Ein Standard ``aus`` waere damit eine stille
#: Verschaerfung von etwas, das laeuft — der Betreiber verloere ohne sein Zutun
#: Auskuenfte, die er gestern noch bekommen hat. Ein Standard ``schreiben`` waere
#: die stille Lockerung in die andere Richtung. Beide Schritte darf nur der
#: Betreiber selbst gehen, und zwar sichtbar in den Einstellungen.
SYSTEMBEREICH_STANDARD = "lesen"


def systembereich_des_benutzers(user: "User") -> str:
    """Was dieses Konto der KI im Systembereich erlaubt — nie mehr als hinterlegt.

    Steht in der Spalte etwas, das diese Fassung des Panels nicht kennt (ein
    Downgrade auf eine aeltere Version, ein direkter Datenbankzugriff, ein
    Tippfehler in einer kuenftigen Migration), faellt die Antwort auf
    `SYSTEMBEREICH_STANDARD` zurueck und nicht auf den hoechsten Wert. Ein
    eingefrorener Wert darf nie mehr freigeben als der Betreiber gewaehlt hat —
    dieselbe Richtung wie bei den Rollenlimits und beim Autonomiezustand.

    ``getattr`` statt direktem Zugriff wie in `ai_lage.zone_des_benutzers`: die
    Testdoubles der Werkzeugschicht sind keine echten ORM-Zeilen, und eine
    fehlende Spalte darf dort keinen AttributeError werfen, sondern muss zur
    engeren Antwort fuehren.
    """
    wert = (getattr(user, "ai_desktop_systembereich", None) or "").strip()
    if wert in SYSTEMBEREICHE:
        return wert
    return SYSTEMBEREICH_STANDARD


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "ai_desktop_systembereich IN ("
            + ", ".join(f"'{bereich}'" for bereich in SYSTEMBEREICHE)
            + ")",
            name="ck_users_ai_desktop_systembereich",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)

    # E-Mail: verschluesselt mit DIS (AES-256-GCM, AAD msm:user:email).
    # email_hash (SHA-256 mit Pepper) fuer SQL-Lookup (WHERE email_hash = ?).
    # email_plain ist die Legacy-Spalte (DB-Name "email"), nach Migration
    # nur noch Platzhalter (der Hash-Wert), keine Klartext-E-Mail mehr.
    email_plain: Mapped[str | None] = mapped_column("email", String(255), unique=True, index=True, nullable=True)
    email_encrypted: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    email_hash: Mapped[str | None] = mapped_column(String(64), unique=True, index=True, nullable=True)

    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    is_owner: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Globale Rolle (Phase 3 RBAC). NULL fuer Owner-Bootstrap akzeptabel, da is_owner alles bypassed.
    role_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("roles.id", ondelete="SET NULL"), nullable=True, index=True
    )

    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)

    two_factor_secret_encrypted: Mapped[str | None] = mapped_column(String(255), nullable=True)
    two_factor_enabled: Mapped[bool] = mapped_column(Boolean, default=False)

    email_notifications: Mapped[bool] = mapped_column(Boolean, default=True)
    # Meldungen der KI im Panel — getrennt von den E-Mails, weil es zwei
    # verschiedene Dinge sind: die KI verschickt keine E-Mails, und wer keine
    # Post will, will deswegen nicht auch keine Hinweise mehr sehen, dass ein
    # laufender Auftrag auf seine Bestaetigung wartet.
    ai_notifications: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False,
                                                  server_default=true())
    # Geräte-Benachrichtigungen (Pop-ups auf Windows und Android).
    device_notifications: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False,
                                                      server_default=true())
    # IANA-Zeitzone des Benutzers (z. B. 'Europe/Berlin').
    # Einzige kanonische Zeitzonen-Quelle für Lageblock, Chat-Zeitstempel und Aufgaben.
    time_zone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Ausschließlich eine Einwilligung. Eine konkrete Position gehört nie in
    # das Konto; sie darf nur für die aktuelle Ortsanfrage im Speicher leben.
    location_sharing_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default=false(),
    )

    # Rufname des Assistenten für dieses Konto (Panel und Smart System).
    # NULL heisst: Standardname 'Singra' (services/ai_lage.py). Der Wert fliesst
    # in den Lageblock als späte system-Nachricht, nie in den statischen
    # Systemprompt — ein Name im Prompt wäre je Benutzer verschieden und
    # entwertete das Prompt-Caching des Anbieters an erster Stelle.
    agent_name: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Vom Benutzer gewählter KI-Zugang — für Chat **und** Sprachmodus. Die Wahl
    # folgt dem Konto, nicht dem Browser: localStorage gehört der Herkunft, und
    # die Desktop-App (tauri.localhost) lief vor diesem Feld still auf dem
    # erstbesten Zugang — ein anderes (womöglich langsameres) Modell, als der
    # Benutzer im Panel gewählt hatte. NULL heißt: keine Wahl getroffen, es
    # gilt die bisherige Reihenfolge. SET NULL: ein gelöschter Zugang nimmt
    # nur die Wahl mit, nie das Konto.
    ai_provider_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("ai_providers.id", ondelete="SET NULL"), nullable=True
    )

    # Wie weit die KI auf dem Rechner dieses Benutzers aus dem freigegebenen
    # Ordner heraus darf (`SYSTEMBEREICHE` oben). Die Einstellung haengt am
    # Konto und nicht am Rechner: derselbe Mensch sitzt abends an einem anderen
    # Geraet, und was er der KI ueber seine Systemordner erlaubt, ist eine
    # Aussage ueber ihn, nicht ueber die Maschine.
    #
    # ``NOT NULL`` mit ``server_default``: es gibt keinen Zustand "nicht
    # eingestellt". Ein ``NULL`` muesste an jeder Lesestelle erneut gedeutet
    # werden, und die erste Stelle, die es als "darf alles" liest, hat die
    # Einstellung dann ausgehebelt.
    ai_desktop_systembereich: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=SYSTEMBEREICH_STANDARD,
        server_default=SYSTEMBEREICH_STANDARD,
    )

    password_reset_token: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_reset_expires: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    avatar_url: Mapped[str | None] = mapped_column(String(512), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    role: Mapped["Role | None"] = relationship("Role", back_populates="users")
    role_assignments: Mapped[list["UserRole"]] = relationship(
        "UserRole",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    server_permissions: Mapped[list["ServerPermission"]] = relationship(
        "ServerPermission",
        foreign_keys="ServerPermission.user_id",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    refresh_tokens: Mapped[list["RefreshToken"]] = relationship("RefreshToken", back_populates="user", cascade="all, delete-orphan")
    backup_codes: Mapped[list["BackupCode"]] = relationship("BackupCode", back_populates="user", cascade="all, delete-orphan")

    @property
    def role_ids(self) -> list[int]:
        """Liefert alle Rollen-IDs inklusive der kompatiblen Legacy-Primärrolle."""
        assigned = {assignment.role_id for assignment in self.role_assignments}
        if self.role_id is not None:
            assigned.add(self.role_id)
        return sorted(assigned)

    # ── E-Mail Property (transparente DIS-Ver-/Entschluesselung) ──

    @staticmethod
    def _email_hash(email: str) -> str:
        """SHA-256(email + pepper) fuer SQL-Lookup. Pepper = settings.secret_key."""
        from config import settings
        return hashlib.sha256((email + settings.secret_key).encode()).hexdigest()

    @property
    def email(self) -> str | None:
        if self.email_encrypted:
            from services.dis_client import DisClient
            return DisClient.decrypt(self.email_encrypted, aad="msm:user:email")
        if self.email_plain:
            # Echte Pre-Migration Erkennung: falls email_plain ein SHA-256 Hash ist, handelt es sich
            # um eine bereits migrierte Zeile, bei der aber email_encrypted fehlt (Datenkorruption/Fehler).
            if len(self.email_plain) == 64 and all(c in "0123456789abcdefABCDEF" for c in self.email_plain):
                from services.dis_client import DisDecryptionError
                raise DisDecryptionError("Inconsistent database state: email_encrypted is missing but email_plain is hashed.")
            return self.email_plain
        return None

    @email.setter
    def email(self, value: str | None) -> None:
        if value:
            from services.dis_client import DisClient
            self.email_encrypted = DisClient.encrypt(value, aad="msm:user:email")
            self.email_hash = self._email_hash(value)
            # Platzhalter in Legacy-Spalte (NOT NULL in alten Schemas).
            # Nach Migration steht hier der Hash, keine Klartext-E-Mail.
            self.email_plain = self.email_hash
        else:
            self.email_encrypted = None
            self.email_hash = None
            self.email_plain = None
