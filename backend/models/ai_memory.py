"""Verschluesseltes, explizit steuerbares AI-Memory."""

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class AiMemoryPreference(Base):
    """Ob die KI sich etwas merken darf — und ob noch danach gefragt wird.

    Standard ist **aus**. Ein Gedaechtnis, das ungefragt mitschreibt, ist keine
    Einstellung, sondern eine Zumutung: der Inhalt geht bei jeder Anfrage an
    einen externen KI-Anbieter, und das muss jemand wissen, bevor es passiert
    und nicht danach.
    """

    __tablename__ = "ai_memory_preferences"

    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Wann der Hinweis zuletzt gezeigt wurde. NULL heisst: noch nie.
    notice_last_shown_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # "Nicht mehr anzeigen". Schaltet den Hinweis ab, nicht das Gedaechtnis —
    # aktivieren laesst es sich danach weiterhin unter Profil > Memory.
    notice_hidden: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
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

    Das Vektorfeld weiter unten stand hier lange als
    ausdrueckliches *Nein*: bei hoechstens 100 Eintraegen je Scope passe ohnehin
    alles gleichzeitig in den Kontext. Die Annahme fiel zweimal — erst am
    Sprachwechsel (ein deutscher Eintrag und eine englische Frage teilen kein
    Wort), dann an der Zahl: 100 ist seit dem konfigurierbaren Rollenlimit nur
    noch der Ausgangswert, ein Bereich fasst bis zu 5.000 Einträge
    (``ai_limit_service.MAX_MEMORY_ENTRIES_MAX``). So viel geht nicht mehr am
    Stueck mit, deshalb waehlt ``provider_memory_context`` aus.

    Ihr zweiter Teil traegt weiter: der Vektor kam als zusaetzliche Spalte und
    nicht als Umbau, und einen Vektor*index* gibt es nach wie vor bewusst nicht.
    Bei 5.000 Zeilen kostet das Lesen der Vektoren gemessene 3 ms und das
    Skalarprodukt in numpy 5 ms — beides zusammen weniger als ein Zwanzigstel
    des Wegs in die Datenbank, der dieselben Zeilen ohnehin holen muss. Teuer
    war an dieser Stelle nie das Rechnen, sondern bis zum 19.08.2026 das
    Format: als JSON kosteten dieselben Vektoren 381 ms. Dünner begründet
    ist die Absage trotzdem: zur Menge, ab der sich ein Index lohnt, ist es
    keine Zehnerpotenz mehr, sondern Faktor zwei. Der nächste Anstieg des
    Deckels ist die Prüfung, die diesmal noch ausgegangen ist; sie steht in
    ``docs/agent-rules/dependencies.md`` bei ``model2vec``.
    """

    __tablename__ = "ai_memory_entries"
    __table_args__ = (
        CheckConstraint(
            # 'server' und 'server_shared' sind beide an einen Server gebunden
            # und trotzdem verschieden: 'server' ist die **persoenliche** Notiz
            # eines Menschen zu dieser Anlage, 'server_shared' gehoert der
            # Anlage selbst und ueberlebt jeden, der sie aufgeschrieben hat.
            "scope IN ('user', 'server', 'server_shared', 'team', 'panel')",
            name="ck_ai_memory_entries_scope",
        ),
        CheckConstraint("origin IN ('user', 'ai')", name="ck_ai_memory_entries_origin"),
        UniqueConstraint("scope_identity", "key", name="uq_ai_memory_scope_key"),
        Index("ix_ai_memory_owner_scope", "owner_user_id", "scope"),
        Index("ix_ai_memory_team", "team_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    # Gesetzt in den Scopes "server" und "server_shared". `CASCADE` ist hier
    # richtig und bei `ai_runs.last_server_id` falsch, und der Unterschied ist
    # nicht willkuerlich: eine Notiz *ueber* einen Server hat ohne ihn keinen
    # Gegenstand mehr, ein Lauf dagegen ist ein Beleg der Unterhaltung und
    # gehoert dem Benutzer.
    server_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("servers.id", ondelete="CASCADE"), nullable=True, index=True)
    # Gesetzt nur im Scope "team". Der Eintrag gehoert dann dem Team, nicht dem
    # Benutzer, der ihn angelegt hat — er bleibt bestehen, wenn dieser geht.
    team_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("teams.id", ondelete="CASCADE"), nullable=True)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_identity: Mapped[str] = mapped_column(String(128), nullable=False)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    value_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    # "user" = ausdruecklich hinterlegt, "ai" = von der KI gemerkt.
    origin: Mapped[str] = mapped_column(String(8), nullable=False, default="user")
    # Welche AAD beim Verschluesseln verwendet wurde. 1 = nur die Eintrags-ID,
    # 2 = zusaetzlich der Scope. Version 2 macht das Umhaengen eines Eintrags
    # auf einen anderen Besitzer per Datenbankzugriff unmoeglich: der Text
    # liesse sich danach nicht mehr entschluesseln. Bestandszeilen bleiben auf
    # 1, bis sie das naechste Mal geschrieben werden.
    aad_version: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    use_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Lokal berechneter Vektor als rohe float32-Bytes, Little-Endian. Bewusst
    # *nicht* verschlüsselt: der `key` daneben steht ohnehin im Klartext und
    # verrät mehr, und die Rangfolge kann ihn so ohne einen weiteren
    # Sidecar-Aufruf je Zeile lesen. Die Auswahl selbst findet **nach** dem
    # Entschlüsseln statt — sie bewertet neben der Bedeutung auch die
    # Wortüberschneidung im Wert und braucht ihn dafür im Klartext. NULL heißt:
    # noch nicht berechnet.
    embedding_bytes: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    # Dieselben Zahlen in der alten Form. Sie steht hier nur noch für den
    # Bestand: 5.000 Vektoren als Text zu lesen kostete gemessen 381 ms und
    # 26,7 MB, als Bytes sind es 4 ms und 5,1 MB — über die Hälfte der
    # Rechenzeit eines Chatabrufs für einen reinen Formatwechsel.
    #
    # Warum die Spalte trotzdem bleibt: zwischen dem Einspielen des neuen Codes
    # und dem Durchlauf der Migration liegen bei jedem Betreiber ein paar
    # Sekunden, und in denen darf das Gedächtnis nicht blind werden.
    # `_stored_vector` liest deshalb bevorzugt Bytes und fällt auf diese Spalte
    # zurück. Geschrieben wird sie nicht mehr; sie verschwindet, wenn kein
    # unterstützter Bestand sie mehr braucht.
    embedding_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Womit gerechnet wurde. Passt es nicht zum geladenen Modell, wird der
    # Vektor ignoriert statt falsche Aehnlichkeiten zu liefern.
    embedding_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
