"""Der Ausgangskorb der KI — eine Mail, die noch nicht verschickt ist.

Diese Tabelle gibt es wegen einer Rechnung, die man einmal aufmachen muss:
zehntausend Aufgaben, die alle auf 18:00 stehen, waren bisher zehntausend
Betriebssystem-Threads. `ai_mail.zustellen` startete je Mail einen eigenen
Thread mit eigener Ereignisschleife, ohne Obergrenze, und dahinter baute
`EmailService._send_smtp` je Mail eine eigene SMTP-Verbindung auf. Kein Anbieter
nimmt zehntausend gleichzeitige Verbindungen an, und kein Prozess ueberlebt
zehntausend Threads — aber das Schlimmste stand woanders: der Versand endete in
`except Exception: return False`. Was nicht durchkam, war weg. Ohne Vermerk,
ohne zweiten Versuch, ohne Spur im Log ausser einer Warnung.

Eine Zeile hier ist die Zusage, dass die Nachricht existiert, auch wenn der
Versand gerade nicht geht: der Anbieter ist ueberlastet, der Prozess wird
neugestartet, das Netz ist weg. Der Arbeiter in `services/ai_mail_outbox.py`
nimmt sich wenige davon gleichzeitig vor und kommt spaeter wieder.

**Was hier ausdruecklich nicht steht: die E-Mail-Adresse.** Sie ist bei MSM
verschluesselt und liegt nur beim DIS-Sidecar im Klartext vor
(`User.email` entschluesselt beim Lesen). Eine Kopie in dieser Tabelle waere
ein Klartextspeicher an einer Stelle, die niemand als solchen erwartet — und
sie waere ein zweiter, stiller Umgehungsweg um `ai_mail.empfaenger`. Deshalb
steht hier `user_id`, und die Adresse wird erst unmittelbar vor dem Versand
aufgeloest. Das hat einen zweiten, fachlichen Nutzen: wer zwischen dem
Einreihen und der Zustellung seine Benachrichtigungen abschaltet, bekommt keine
Mail mehr — die Entscheidung faellt beim Verschicken, nicht beim Anlegen.

**Warum Betreff und Text hier stehen und keine Vorlagenkennung.** Die KI
schreibt beides selbst; es gibt keine Vorlage, aus der sich der Text spaeter
noch einmal herstellen liesse. Der fertige Text ist das Einzige, was den
Neustart eines Prozesses ueberdauern kann.

**Warum kein `zugestellt_an`-Feld und kein Anhang.** Beides waere die naechste
Kopie einer Angabe, die anderswo gepflegt wird. Wer wissen will, an welches
Postfach etwas ging, findet es im Mailserverlog des Betreibers; MSM fuehrt
darueber bewusst kein zweites Buch.
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
)
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


#: Der Lebenslauf einer Zeile. Bewusst kein Zustand "laeuft gerade": ein
#: solcher Zustand ueberlebt einen Prozessabsturz und blockiert die Zeile
#: danach fuer immer, weil niemand mehr da ist, der ihn zuruecksetzt. Die
#: Uebernahme wird stattdessen ueber `naechster_versuch_at` befristet — laeuft
#: die Frist ab, nimmt sich der naechste Arbeiter die Zeile wieder vor.
ZUSTAENDE = ("offen", "zugestellt", "aufgegeben")


class AiMailOutbox(Base):
    __tablename__ = "ai_mail_outbox"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    # CASCADE: eine Mail an ein geloeschtes Konto hat keinen Empfaenger mehr.
    # Sie stehen zu lassen hiesse, eine Zeile aufzubewahren, die nie wieder
    # zustellbar ist — und deren `user_id` ins Leere zeigt.
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: Woher die Mail kommt: `ai-task-report`, `ai-guardian-report`,
    #: `ai-test-email`. Kein Fremdschluessel auf die Aufgabe oder den Lauf: die
    #: koennen zwischen Einreihen und Zustellen geloescht werden, und dann duerfte
    #: die Mail trotzdem noch hinausgehen — sie beschreibt etwas, das passiert
    #: ist. Der Wert ist reine Zuordnung fuer Log und Nachschau.
    anlass: Mapped[str] = mapped_column(String(48), nullable=False)

    #: Betreff und Text stammen vom Modell (siehe `ai_mail`), sind also bereits
    #: redigiert und maskiert, wenn sie hier ankommen. Diese Tabelle prueft das
    #: nicht nach — sie ist ein Korb, kein zweites Regelwerk.
    betreff: Mapped[str] = mapped_column(String(255), nullable=False)
    text_body: Mapped[str] = mapped_column(Text, nullable=False)
    #: Darf fehlen. Eine reine Textmail ist eine gueltige Mail; eine leere
    #: HTML-Fassung waere eine kaputte.
    html_body: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="offen", server_default="offen"
    )
    #: Wie oft die Zustellung schon angefangen wurde — hochgezaehlt bei der
    #: **Uebernahme**, nicht beim Fehlschlag. Wer erst hinterher zaehlt, zaehlt
    #: einen Absturz mitten im Versand nie mit und versucht dieselbe Zeile
    #: unbegrenzt oft erneut.
    versuche: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    #: Fruehester naechster Versuch, in UTC. Traegt zwei Bedeutungen, und beide
    #: sind dieselbe Frage "ab wann darf sich jemand diese Zeile nehmen?":
    #: der wachsende Abstand nach einem Fehlschlag und die befristete
    #: Uebernahme durch einen Arbeiter.
    naechster_versuch_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Warum es zuletzt nicht ging. Gekuerzt gespeichert: eine Fehlermeldung aus
    #: einer fremden Bibliothek kann Kilobyte lang sein, und diese Spalte wird
    #: bei jedem Fehlversuch neu geschrieben.
    letzter_fehler: Mapped[str | None] = mapped_column(String(500), nullable=True)

    __table_args__ = (
        # Genau die Abfrage des Arbeiters. Ohne ihn liest er bei jedem Takt die
        # ganze Tabelle — und die Tabelle waechst mit jeder zugestellten Mail,
        # bis jemand aufraeumt.
        Index("ix_ai_mail_outbox_faellig", "status", "naechster_versuch_at"),
        CheckConstraint(
            "status IN (" + ", ".join(f"'{wert}'" for wert in ZUSTAENDE) + ")",
            name="ck_ai_mail_outbox_status",
        ),
    )
