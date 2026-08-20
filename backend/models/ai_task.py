"""Ein stehender Auftrag, den die KI zur faelligen Zeit selbst ausfuehrt.

*"Benachrichtige mich jeden Tag um 8 Uhr per Mail ueber den Zustand meiner
Server."* Der Satz faellt einmal im Chat, und danach laeuft er weiter, ohne dass
noch jemand tippt.

**Nicht zu verwechseln mit ``OperationTask``** (``operation_tasks``,
``/api/tasks``). Das dort ist ein *laufender Vorgang* — eine Serverbereitstellung,
die Minuten dauert und einen Fortschritt hat. Das hier ist ein *Termin*: er hat
keinen Fortschritt, er hat eine naechste Faelligkeit.

Die Tabelle ist die Wahrheit, nicht der Scheduler-Auftrag. APScheduler haelt
seine Jobs in MSM ausschliesslich im Speicher (kein ``SQLAlchemyJobStore``) —
nach einem Neustart des Panels gaebe es keinen einzigen mehr. Deshalb steht der
Zeitplan hier, und ein einziger Takt fragt alle sechzig Sekunden ``enabled AND
next_run_at <= now`` ab. Ein Job je Aufgabe waere eine zweite Buchfuehrung, die
nach jedem Neustart aus dieser hier wiederhergestellt werden muesste.

Zwei Felder tragen die eigentliche Entscheidung:

``kind`` unterscheidet, was ein faelliger Lauf ueberhaupt darf. ``report`` liest,
fasst zusammen und schickt das Ergebnis; ``act`` darf zusaetzlich schreiben und
verlangt dafuer ``ai.autonomous.use`` und eine aktive ``AiAutonomyGrant`` — beim
Anlegen **und** bei jedem Lauf. Ohne die zweite Pruefung liefe eine Aufgabe mit
einer Freigabe weiter, die der Betreiber laengst zurueckgenommen hat.

``time_zone`` ist keine Bequemlichkeit, sondern die Bedingung dafuer, dass "8
Uhr" ueberhaupt etwas bedeutet. MSM kennt sonst nirgends eine Benutzerzeitzone;
Auto-Neustart und Auto-Backup rechnen in UTC, und der Betreiber muss selbst
umrechnen. Fuer einen Satz, den ein Mensch in seiner Sprache diktiert, waere das
die falsche Seite der Ueberraschung — deshalb steht die IANA-Zone an der Aufgabe,
und ``next_run_at`` wird daraus berechnet. Sommerzeit verschiebt sich damit von
selbst mit.

Bewusst **kein** Feld fuer den Ausgang des letzten Laufs. Der steht am Lauf
(``ai_runs.status``, ``stop_reason``), und ihn hier zu spiegeln hiesse, zwei
Wahrheiten zu pflegen — derselbe Verzicht wie in ``ai_guardian_notice``.

``conversation_id`` zeigt seit ``20260820_03`` auf das **eigene
Hintergrundfenster** der Aufgabe (``kind='worker'``). Hier stand vorher das
Gegenteil — „ein faelliger Auftrag schreibt immer in den Dauerchat" — und der
Betreiber hat es am 20.08.2026 umgedreht: im Dauerchat steht nur, was der
Mensch schreibt; Aufgaben laufen im Hintergrund wie Worker und Guardian und
unterbrechen nie das laufende Gespraech. Ihr Ergebnis kommt als Meldung ueber
die Meldestelle in den Chat, sobald dort Ruhe ist — plus optional als E-Mail
(``channel``). **Ein** Fenster je Aufgabe, ueber alle Laeufe wiederverwendet:
eine taegliche Aufgabe hinterliesse sonst 365 Fenster im Jahr, und im
gemeinsamen Verlauf sieht das Modell, was es gestern festgestellt hat.
``SET NULL`` wie bei ``last_run_id``: verschwindet das Fenster, legt sich die
Aufgabe beim naechsten Termin ein neues an.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


#: Was ein faelliger Lauf darf. ``act`` ist nicht "mehr Rechte", sondern
#: "Schreibwerkzeuge ueberhaupt" — die Rechte selbst bleiben die des Benutzers.
ARTEN = ("report", "act")

#: Wie der Termin gerechnet wird. Mehr Formen gibt es bewusst nicht: der
#: Betreiber hat "jeden Tag um X", "alle X Stunden" und "einmal am" genannt, und
#: ein vollstaendiger Cron-Ausdruck waere eine Sprache, die niemand im Chat
#: diktiert und die die KI raten muesste.
PLANARTEN = ("daily", "interval", "once")

#: Wohin das Ergebnis geht. ``chat`` ist nie abwaehlbar — der Verlauf steht im
#: Aufgabenfenster, und die Meldestelle bringt das Ergebnis in den Dauerchat,
#: sobald dort Ruhe ist. ``email`` heisst *zusaetzlich*, nie ausschliesslich:
#: ``EmailService.is_configured()`` prueft nur, ob Zugangsdaten dastehen, nicht
#: ob sie funktionieren; eine Aufgabe, deren Ergebnis ausschliesslich per Mail
#: existierte, koennte still ins Leere laufen.
KANAELE = ("chat", "email", "both")


class AiTask(Base):
    __tablename__ = "ai_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    # CASCADE: die Aufgabe gehoert diesem Menschen. Ist er weg, gibt es niemanden
    # mehr, in dessen Namen sie laufen koennte — und sie liefe mit den Rechten
    # eines Kontos, das es nicht mehr gibt.
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    # Der Auftragstext, so wie er beim Anlegen redigiert wurde. Er wird bei jeder
    # Faelligkeit zur Benutzernachricht des Laufs — also an die Stelle mit dem
    # meisten Gewicht. Deshalb wird er **einmal** beim Anlegen geschwaerzt und
    # nicht bei jedem Lauf erneut: was hier steht, hat ein Mensch bestaetigt.
    instruction: Mapped[str] = mapped_column(Text, nullable=False)

    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    plan_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    #: ``"HH:MM"`` in ``time_zone``, nur bei ``daily``.
    time_of_day: Mapped[str | None] = mapped_column(String(5), nullable=True)
    #: ISO-Wochentage als ``"1,3,5"`` (Montag = 1, wie ``datetime.isoweekday``).
    #: Leer oder NULL heisst: an jedem Tag. Bewusst die ISO-Zaehlung und nicht
    #: die von APScheduler (dort ist Montag 0) — in der Datenbank soll stehen,
    #: was ein Mensch beim Nachsehen erwartet; die Umrechnung passiert einmal
    #: beim Bau des Triggers.
    weekdays: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: Nur bei ``interval``. Untergrenze steht im Dienst, nicht hier: sie ist
    #: eine Betriebsentscheidung ueber Kosten, keine Eigenschaft der Daten.
    interval_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Nur bei ``once`` — in UTC, wie jeder Zeitpunkt in dieser Tabelle.
    once_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: IANA-Zone, z. B. ``Europe/Berlin``. Nicht ``timezone`` genannt: das ist in
    #: PostgreSQL ein Funktionsname, und eine Spalte, die man nur in
    #: Anfuehrungszeichen ansprechen kann, ist eine Falle fuer jedes spaetere
    #: Wartungsskript.
    time_zone: Mapped[str] = mapped_column(String(64), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)

    #: Pausiert statt geloescht. Der Betreiber wollte beides getrennt haben:
    #: abschalten laesst sich zuruecknehmen, loeschen nicht.
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: Die naechste Faelligkeit in UTC. ``NULL`` heisst "nie wieder" — nach einem
    #: gelaufenen ``once`` oder wenn der Plan keinen weiteren Termin hergibt.
    next_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # SET NULL und nicht CASCADE: raeumt jemand alte Laeufe ab, bleibt die
    # Aufgabe bestehen. Der Lauf ist hier ein Beleg, kein Besitz.
    last_run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("ai_runs.id", ondelete="SET NULL"), nullable=True
    )
    #: Das Hintergrundfenster der Aufgabe (``kind='worker'``) — beim ersten
    #: faelligen Lauf angelegt, danach wiederverwendet. Siehe Docstring oben.
    conversation_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("ai_conversations.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    __table_args__ = (
        # Genau die Abfrage des Takts. Ohne ihn liest er bei jedem Durchlauf die
        # ganze Tabelle, und das sechzigmal in der Stunde.
        Index("ix_ai_tasks_enabled_next", "enabled", "next_run_at"),
        CheckConstraint(
            "kind IN (" + ", ".join(f"'{wert}'" for wert in ARTEN) + ")",
            name="ck_ai_tasks_kind",
        ),
        CheckConstraint(
            "plan_kind IN (" + ", ".join(f"'{wert}'" for wert in PLANARTEN) + ")",
            name="ck_ai_tasks_plan_kind",
        ),
        CheckConstraint(
            "channel IN (" + ", ".join(f"'{wert}'" for wert in KANAELE) + ")",
            name="ck_ai_tasks_channel",
        ),
    )
