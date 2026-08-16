"""Der Reparaturauftrag: ein Vorfall, viele Anlaeufe.

Bis hierher bekam ein Vorfall genau **einen** Lauf. Die Notiz mit
``mode='healing'`` entstand beim *Start* (``ai_guardian_service``), und beide
Filter des Takts uebersprangen den Vorfall danach fuer immer. Endete der Lauf
nach achtundvierzig Leserunden auf ``stop_reason='budget'`` — was als
``status='completed'`` verbucht wird —, war das das Ende: der Server blieb
stehen, die Mail sagte "nicht behoben", und nichts fasste den Vorfall je wieder
an.

Das ist die falsche Einheit. Ein Rundenbudget ist keine Aussage ueber den
Server, sondern ueber einen Anbieteraufruf. Die Einheit, die zaehlt, ist der
**Auftrag**: *diesen Vorfall in Ordnung bringen* — und der ueberlebt einen
erschoepften Lauf so, wie er einen Neustart des Panels ueberlebt.

Die Tabelle ist die Wahrheit, nicht ein Scheduler-Auftrag. Wortgleich der
Grund aus ``ai_task``: APScheduler haelt seine Jobs in MSM rein im Speicher
(kein ``SQLAlchemyJobStore``), ein Auftrag je Reparatur waere nach jedem
Neustart weg. Deshalb steht der naechste Weckruf hier, und der vorhandene
60-Sekunden-Takt fragt ``next_run_at <= now`` ab.

Die Phasen
----------

``diagnose`` → ``eingriff`` → ``beobachtung`` → ein Endzustand.

Die Leiter ist bewusst fest und nicht eine Entscheidung des Modells. Sie ist
die Antwort auf zwei gemessene Fehlverhalten: das Modell liest, redet und hoert
auf, ohne etwas zu tun — und es erklaert einen Vorfall fuer erledigt, sobald es
einen Container gestartet hat. Eine Phase, die von aussen gesetzt wird, laesst
beides nicht zu: nach der Diagnose kommt der Eingriff, und nach dem Eingriff
kommt das Zusehen, ob es gehalten hat.

``beobachtung`` ist dabei kein Warten *im* Lauf. ``MAX_GLEICHE_POLLING_AUFRUFE``
schneidet acht gleiche Abfragen hintereinander ab; "eine Stunde zusehen"
innerhalb eines Segments gibt es also nicht. Der Lauf endet stattdessen
absichtlich, und ``next_run_at`` traegt das Zusehen — Minuten spaeter, ohne
Tokens dazwischen.

Was hier **nicht** steht
------------------------

Keine ``conversation_id``. Es gibt je Benutzer und Art genau eine Unterhaltung
(``uq_ai_conversations_user_kind``), und ein Reparaturlauf schreibt immer in
dieselbe: das Guardian-Fenster. Sie ist aus ``user_id`` ableitbar, und eine
gespeicherte Kennung waere nur die Gelegenheit, irgendwann auf eine geloeschte
zu zeigen — derselbe Verzicht und dieselbe Begruendung wie in ``ai_task``.

Keine eigene Kennung fuer die Guardian-Aussetzung und die Quarantaenefreigabe.
Beide verlangen eine kanonische UUID als ``operation_id``, und ``id`` ist
bereits eine — als ``uuid4``-Zeichenkette in genau der Form, die
``guardian_state_service`` prueft. Wer eine zweite Spalte dafuer anlegt, hat
zwei Kennungen fuer denselben Vorgang und irgendwann eine Aussetzung, die
niemand mehr aufheben kann, weil beide auseinandergelaufen sind.

Kein Feld fuer den Ausgang des letzten Laufs. Der steht am Lauf
(``ai_runs.status``, ``stop_reason``); ihn hier zu spiegeln hiesse, zwei
Wahrheiten zu pflegen — derselbe Verzicht wie in ``ai_guardian_notice``.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


#: Die Phasen, in denen der Auftrag noch etwas vorhat. Jede von ihnen bekommt
#: einen eigenen Lauf mit einem eigenen Auftragstext.
ARBEITSPHASEN = ("diagnose", "eingriff", "beobachtung")

#: Und die, in denen er fertig ist. ``next_run_at`` ist dann NULL.
#:
#: * ``erledigt``    — die Anlage zeigt es: Vorfall geloest, Server laeuft.
#: * ``eskaliert``   — es haengt an einer Entscheidung, die nur ein Mensch
#:   treffen darf. Gesetzt wird das erst mit der E-Mail-Freigabe; bis dahin
#:   fuehrt derselbe Fall ueber ``aufgegeben``.
#: * ``aufgegeben``  — Frist abgelaufen oder Versuche aufgebraucht.
#: * ``abgebrochen`` — ein Mensch hat uebernommen.
ENDPHASEN = ("erledigt", "eskaliert", "aufgegeben", "abgebrochen")

PHASEN = ARBEITSPHASEN + ENDPHASEN


class AiGuardianRepair(Base):
    __tablename__ = "ai_guardian_repairs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    # CASCADE: ohne den Vorfall gibt es nichts zu reparieren. Anders als bei der
    # Notiz ist die Zeile hier kein Beleg ueber die Vergangenheit, sondern ein
    # laufendes Vorhaben — eines, das ins Leere liefe.
    incident_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # SET NULL: wird der Server geloescht, waehrend der Auftrag laeuft, soll die
    # Zeile nicht verschwinden — der Takt findet sie, sieht keinen Server mehr
    # und beendet sie ordentlich als ``abgebrochen``. Ein stilles CASCADE haette
    # denselben Effekt ohne Spur.
    server_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("servers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # CASCADE: gehandelt wird in seinem Namen, mit seinen Rechten, auf seine
    # Freigabe hin. Ist er weg, gibt es niemanden, als den der Auftrag laufen
    # koennte.
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    phase: Mapped[str] = mapped_column(String(16), nullable=False, default="diagnose")
    #: Wieviele Laeufe dieser Auftrag schon verbraucht hat. Der Deckel steht im
    #: Dienst und nicht hier: er ist eine Betriebsentscheidung ueber Kosten,
    #: keine Eigenschaft der Daten.
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    #: Der naechste Weckruf in UTC. ``NULL`` heisst "nie wieder" — jede Endphase
    #: traegt ihn so. Ein Auftrag ohne Termin kann vom Takt nicht mehr gefunden
    #: werden, und das ist die einzige Bremse, die auch nach einem Neustart haelt.
    next_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Wann spaetestens Schluss ist, egal wie weit er gekommen ist. Ohne diese
    #: Spalte kann ein Auftrag, der bei jedem Anlauf ein bisschen weiterkommt,
    #: den Server tagelang beschaeftigen und Kosten verursachen, ohne dass
    #: jemals eine Mail den Betreiber erreicht.
    deadline_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # SET NULL wie bei ``ai_tasks.last_run_id``: raeumt jemand alte Laeufe ab,
    # bleibt der Auftrag bestehen. Der Lauf ist hier ein Beleg, kein Besitz.
    last_run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("ai_runs.id", ondelete="SET NULL"), nullable=True
    )

    #: Was aus den bisherigen Anlaeufen mitgeht.
    #:
    #: Der einzige Weg, auf dem ein Auftrag ueber Laufgrenzen hinweg etwas weiss.
    #: ``arbeitsspeicher_leeren`` wirft ``provider_messages`` bei **jedem**
    #: Endzustand weg — ein beendeter Lauf laesst sich nie fortsetzen, und das
    #: ist Absicht: dort steht der entschluesselte Gedaechtnisblock des
    #: Benutzers im Klartext. Was der naechste Anlauf braucht, muss deshalb
    #: ausdruecklich hier landen, geschwaerzt und gedeckelt, und geht als
    #: Paneltext in seinen Auftrag.
    erkenntnisse: Mapped[str | None] = mapped_column(Text, nullable=True)

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
        # Ein Auftrag je Vorfall und Freigeber. Die Eindeutigkeit liegt in der
        # Datenbank und nicht in einer Pruefung davor: laeuft das Panel je mit
        # mehreren Arbeitsprozessen, gibt es den Scheduler mehrfach, und
        # ``max_instances=1`` gilt nur innerhalb eines Prozesses.
        UniqueConstraint(
            "incident_id", "user_id", name="uq_ai_guardian_repairs_incident_user"
        ),
        # Genau die Abfrage des Takts, sechzigmal in der Stunde.
        Index("ix_ai_guardian_repairs_next", "next_run_at"),
        CheckConstraint(
            "phase IN (" + ", ".join(f"'{wert}'" for wert in PHASEN) + ")",
            name="ck_ai_guardian_repairs_phase",
        ),
    )
