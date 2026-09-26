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
from sqlalchemy.ext.hybrid import hybrid_property
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
        UniqueConstraint("scope_identity", "key_index", name="uq_ai_memory_scope_key_index"),
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
    # Der Name des Eintrags ("zeitzone", "character_pairing"). Er sagt oft
    # schon, worum es geht, und lag deshalb bis 26.09.2026 zu Unrecht im
    # Klartext. Gelesen und gesetzt wird er ueber die Eigenschaft `key` unten;
    # gesucht wird ueber `key_index`, ein HMAC aus dem Sidecar ueber Bereich
    # und Namen. Der Bereich steckt mit drin, damit derselbe Name bei zwei
    # Benutzern nicht denselben Index hat: sonst saehe man in der Tabelle, wer
    # sich dasselbe gemerkt hat.
    #
    # NULL im Index heisst Altbestand von vor der Umstellung, `key_encrypted`
    # traegt dann noch den Klartext. `ai_memory_service.schluessel_bedingung`
    # zieht beides im betroffenen Bereich nach, bevor gesucht wird.
    key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    key_index: Mapped[str | None] = mapped_column(String(64), nullable=True)
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
    # Lokal berechneter Vektor: 12 Byte Nonce, dahinter die 256 float32
    # (Little-Endian) unter AES-GCM. Verpackt und geöffnet wird er in
    # `ai_memory_service._vektor_verschluesseln`; NULL heißt: noch nicht
    # berechnet.
    #
    # Er lag hier lange im Klartext, mit der Begründung, der `key` daneben
    # verrate ohnehin mehr. Das stimmte nicht. Der Schlüssel fasst 64 Zeichen
    # aus [A-Za-z0-9_.-]; der Vektor entsteht aus Schlüssel **und** Wert
    # (`ai_memory_service._embedding_source`), und das Modell darunter ist ein
    # statisches — der Vektor ist im Kern das Mittel der Wortvektoren, und aus
    # so einem Mittel lässt sich der Wortbestand des Werts näherungsweise
    # zurücksuchen. Wer nur die Datenbank hatte, kam damit an den Inhalt fremder
    # Notizen, ohne die DIS-Verschlüsselung des Werts anzufassen — also an
    # genau der Zusage vorbei, die `tests/test_ai_memory_isolation.py` "gegen
    # Datenbankzugriff" nennt.
    #
    # Der Schlüssel kommt aus dem Panel-Secret und nicht aus dem DIS-Sidecar:
    # dort kostete jede Zeile einen HTTP-Roundtrip, und die Rangfolge liest bis
    # zu 5.000 Vektoren je Anfrage. Das ist ein schwächerer Schutz als beim
    # Wert — wer Datenbank *und* Panel-Umgebung hat, liest beides —, aber es
    # trennt den Vektor vom bloßen Datenbankzugriff.
    #
    # Bestandszeilen tragen hier weiterhin die nackten 1.024 Bytes. Sie werden
    # gelesen wie bisher und beim nächsten Abruf in den Kontext neu und dann
    # verpackt geschrieben (`ai_memory_service._liegt_im_klartext`) — eine
    # Migration braucht es dafür nicht, und keine Zeile verliert unterwegs
    # ihren Bedeutungsanteil.
    #
    # Die Auswahl selbst findet unverändert **nach** dem Entschlüsseln des
    # Werts statt: sie bewertet neben der Bedeutung auch die Wortüberschneidung
    # im Wert und braucht ihn dafür im Klartext.
    embedding_bytes: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    # Womit gerechnet wurde. Passt es nicht zum geladenen Modell, wird der
    # Vektor ignoriert statt falsche Aehnlichkeiten zu liefern.
    embedding_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    @hybrid_property
    def key(self) -> str:
        """Der Name im Klartext, einmal entschluesselt und dann gemerkt.

        Fuer viele Zeilen auf einmal fuellt `ai_memory_service._schluessel_laden`
        diesen Speicher mit einem einzigen Sidecar-Aufruf; ohne das kostete
        jede Zeile einen eigenen.
        """
        klar = self.__dict__.get("_key_klartext")
        if klar is None:
            from services.dis_client import DisClient

            roh = self.key_encrypted or ""
            klar = DisClient.decrypt(roh, aad=self.key_aad()) if DisClient.ist_verschluesselt(roh) else roh
            self._key_klartext = klar
        return klar

    @key.inplace.setter
    def _key_setzen(self, wert: str) -> None:
        from services.dis_client import DisClient

        self.key_encrypted = DisClient.encrypt(wert, aad=self.key_aad())
        self._key_klartext = wert

    @key.inplace.expression
    @classmethod
    def _key_in_sql(cls):
        # Mit einer schlichten `property` ergab `AiMemoryEntry.key == "x"`
        # still `False` und damit eine leere Trefferliste. Hier scheitert es
        # laut, und die Meldung sagt, wie es richtig geht.
        raise AttributeError(
            "AiMemoryEntry.key ist verschluesselt und in SQL nicht vergleichbar; "
            "ai_memory_service.schluessel_bedingung verwenden"
        )

    def key_aad(self) -> str:
        # An Bereich und Zeile gebunden wie der Wert (`ai_memory_service._aad`):
        # wer den Namen in eine fremde Zeile kopiert, bekommt ihn nicht lesbar.
        return f"msm:ai:memory:key:{self.scope_identity}:{self.id}"
