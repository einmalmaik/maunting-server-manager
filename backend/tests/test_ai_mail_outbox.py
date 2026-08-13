"""Zusagen fuer den Ausgangskorb der KI-Mails.

Der Anlass ist eine Rechnung, die niemand aufgemacht hatte: `ai_mail.zustellen`
startete **je Mail** einen Betriebssystem-Thread mit eigener Ereignisschleife,
ohne Obergrenze, und darunter baute `aiosmtplib.send` je Mail eine neue
SMTP-Verbindung auf. Zehntausend stehende Auftraege, alle auf 18:00 gestellt,
waren damit zehntausend Threads. Was daran scheiterte, verschwand ersatzlos —
der Versand endete in `except Exception: return False`.

Deshalb pruefen diese Tests drei Dinge, die man einzeln fuer selbstverstaendlich
haelt und die zusammen die eigentliche Zusage sind:

* Genau **einmal** zugestellt. Nicht zweimal, wenn zwei Durchgaenge sich
  ueberschneiden.
* **Nie verloren.** Ein Versand, der wirft, kommt zurueck in die Warteschlange;
  erst nach `VERSUCHE_MAX` Anlaeufen wird aufgegeben, und dann steht der Grund
  daneben.
* **Nie mehr als erlaubt gleichzeitig.** Bei zehntausend faelligen Zeilen wird
  der Hoechststand mitgezaehlt, und die Zahl der Threads ebenso.

Seit die Naht zum Verfassungsschritt geschlossen ist, kommt eine vierte dazu:

* **Verfasst wird beim Versand, nicht beim Einreihen.** Der Korb speichert die
  Fakten, der Arbeiter laesst daraus schreiben — innerhalb derselben Schranke.
  Misslingt das, geht der beim Einreihen gerenderte Text hinaus, und zwar genau
  einmal: ein misslungener Verfassungsschritt ist kein Zustellfehler.

Verschickt wird in keinem dieser Tests wirklich etwas: gefangen wird bei
`ai_mail_outbox._versenden`, der einen Naht, die genau dafuer da ist. Das Modell
wird bei `ai_mail_text.verfassen` ersetzt — auch dort geht kein Byte ins Netz.
"""

import asyncio
import json
import threading
import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, insert, inspect, select
from sqlalchemy.orm import Session

from config import settings
from database import Base
from models import AiMailOutbox, User
from services import ai_mail, ai_mail_outbox, ai_mail_text
from services.auth_service import AuthService
from services.email_service import EmailService


@pytest.fixture(autouse=True)
def _korb_zuruecksetzen(monkeypatch):
    """Jeder Test bekommt einen Arbeiter, der noch nirgends haengt.

    Eine Aufgabe aus der Ereignisschleife des vorigen Tests wird nie mehr
    fertig — dieselbe Falle wie beim Modellkatalog, und dort schon einmal
    zugeschnappt.

    `is_configured` gilt hier durchgehend als erfuellt: ohne einen eingerichteten
    Versandweg gaebe `ai_mail.empfaenger` fuer jede Zeile ``None`` zurueck, und
    saemtliche Tests prueften dann nur noch, dass ein nicht eingerichtetes Panel
    keine Mails schickt.
    """
    ai_mail_outbox.zuruecksetzen_fuer_tests()
    monkeypatch.setattr(EmailService, "is_configured", staticmethod(lambda: True))
    yield
    ai_mail_outbox.zuruecksetzen_fuer_tests()


def _empfaenger(db: Session, name: str = "korbnutzer") -> User:
    user = AuthService.create_user(db, name, f"{name}@test.de", "KorbPass123!")
    user.email_verified = True
    user.email_notifications = True
    db.commit()
    db.refresh(user)
    return user


def _zeile(db: Session, user: User, *, betreff: str = "Bericht") -> str:
    kennung = ai_mail.einreihen(
        db,
        user_id=user.id,
        anlass="ai-task-report",
        betreff=betreff,
        text="Deine Server laufen.",
        html="<p>Deine Server laufen.</p>",
    )
    assert kennung is not None
    return kennung


#: Der feste Text, der in jeder Zeile mit Fakten als Rueckfall danebensteht.
#: Als Konstante, weil zwei Zusagen daran haengen: er geht hinaus, wenn das
#: Modell klemmt — und er geht **nicht** hinaus, wenn es geliefert hat.
RUECKFALL = "Der Assistent hat keine Zusammenfassung hinterlassen."


def _rahmen(*, geschafft: bool = True) -> dict:
    """Der Panelanteil eines Aufgabenberichts, so wie ihn `ai_task_report` baut."""
    rahmen = EmailService.ai_rahmen_task(
        "korbnutzer", task_title="Serverstatus", plan_text="täglich",
        geschafft=geschafft,
    )
    rahmen["provider_id"] = None
    return rahmen


def _zeile_mit_fakten(
    db: Session, user: User, *, rahmen: dict | None = None
) -> tuple[str, dict]:
    """Reiht eine Mail so ein, wie es ein Berichtspfad tut: Rueckfall **und** Fakten."""
    rahmen = rahmen if rahmen is not None else _rahmen()
    betreff, text, html = EmailService.ai_mail_rendern(rahmen, rueckfall=RUECKFALL)
    kennung = ai_mail.einreihen(
        db,
        user_id=user.id,
        anlass="ai-task-report",
        betreff=betreff,
        text=text,
        html=html,
        fakten="Ergebnis laut Panel: erledigt\nAbschlussbericht: alles ruhig.",
        rahmen=rahmen,
    )
    assert kennung is not None
    return kennung, rahmen


def _modell(
    monkeypatch,
    *,
    betreff: str | None = "Alle drei Server laufen",
    absaetze: tuple[str, ...] = ("Ich habe die drei Server geprüft.",),
    platzt: Exception | None = None,
) -> list[dict]:
    """Ersetzt den Verfassungsschritt. Gibt zurueck, womit er gerufen wurde.

    ``betreff=None`` ist der Fall „das Modell hat nichts Brauchbares geliefert“
    (`verfassen` gibt dann selbst ``None`` zurueck), ``platzt`` der Fall „es hat
    geworfen“. Beide muessen dasselbe bewirken: der Rueckfall geht hinaus.
    """
    gerufen: list[dict] = []

    async def _fake(*, user_id, anlass, fakten, provider_id=None, client=None):
        gerufen.append(
            {
                "user_id": user_id,
                "anlass": anlass,
                "fakten": fakten,
                "provider_id": provider_id,
            }
        )
        if platzt is not None:
            raise platzt
        if betreff is None:
            return None
        return ai_mail_text.Mailtext(betreff=betreff, absaetze=list(absaetze))

    monkeypatch.setattr(ai_mail_text, "verfassen", _fake)
    return gerufen


def _mitschnitt(monkeypatch) -> list[tuple[str, ai_mail_outbox._Auftrag]]:
    """Faengt den Versand ab und haelt fest, **was** hinausgegangen waere."""
    versandt: list[tuple[str, ai_mail_outbox._Auftrag]] = []

    async def _fake(adresse, auftrag):
        versandt.append((adresse, auftrag))
        return True

    monkeypatch.setattr(ai_mail_outbox, "_versenden", _fake)
    return versandt


def test_queueing_a_mail_writes_a_row_and_starts_no_thread(db: Session) -> None:
    """Der Kern des Umbaus: einreihen ist ein INSERT, kein Thread.

    Gezaehlt wird die Zahl der lebenden Threads vor und nach dem Aufruf. Vorher
    waere sie um genau eins gestiegen — und bei zehntausend faelligen Auftraegen
    um zehntausend.

    Gerufen wird ueber `zustellen`, nicht ueber `einreihen`: geprueft werden
    soll der Weg, den die Berichtspfade nehmen, samt der Umlenkung darin.
    """
    user = _empfaenger(db)
    vorher = threading.active_count()

    ai_mail.zustellen(
        db=db,
        user_id=user.id,
        betreff="Nachtbericht",
        text="Alles ruhig.",
        name="ai-task-report",
    )

    assert threading.active_count() == vorher
    zeile = db.execute(
        select(AiMailOutbox).where(AiMailOutbox.betreff == "Nachtbericht")
    ).scalar_one()
    assert zeile.status == "offen"
    assert zeile.versuche == 0
    assert zeile.anlass == "ai-task-report"
    assert zeile.betreff == "Nachtbericht"


def test_the_old_coroutine_form_of_zustellen_still_works() -> None:
    """Der alte Weg bleibt fahrbar, solange noch jemand auf ihm faehrt.

    Beim Umbau konnten nicht alle Aufrufer gleichzeitig umgestellt werden. Eine
    Signatur stillschweigend zu brechen waere die schlechtere Haelfte beider
    Moeglichkeiten: der alte Aufruf liefe weiter durch, wuerde aber nichts mehr
    tun — und niemand saehe es, weil `zustellen` nie wirft.
    """
    gelaufen = threading.Event()

    async def _bauen() -> bool:
        gelaufen.set()
        return True

    ai_mail.zustellen(_bauen, name="ai-test-email")

    assert gelaufen.wait(5.0), "die alte Koroutinenform wurde nicht mehr ausgefuehrt"


def test_zustellen_without_any_usable_form_does_not_raise(caplog) -> None:
    """Ein falsch gerufenes `zustellen` kostet eine Mail, nie einen Lauf.

    Der Fall entsteht beim Umstellen der Aufrufer: jemand gibt `betreff` an,
    vergisst aber `db`. Frueher waere das ein `TypeError` mitten im Abschluss
    eines KI-Laufs gewesen.
    """
    ai_mail.zustellen(name="ai-task-report", betreff="Ohne Sitzung", text="Text")

    assert "nicht zustellbar" in caplog.text


def test_the_queued_row_never_holds_the_email_address(db: Session) -> None:
    """Die Adresse gehoert dem DIS-Sidecar, nicht dieser Tabelle.

    Eine Klartextkopie in einer neuen Tabelle waere ein Bruch der Grenze, die
    `User.email` zieht — und ein zweiter Weg an `empfaenger` vorbei, wo
    Abbestellung und Versandweg geprueft werden.
    """
    user = _empfaenger(db, "adresspruefung")
    kennung = _zeile(db, user)

    zeile = db.get(AiMailOutbox, kennung)
    spalten = {spalte.name for spalte in AiMailOutbox.__table__.columns}
    assert "email" not in spalten and "adresse" not in spalten
    inhalt = " ".join(
        str(getattr(zeile, spalte) or "") for spalte in spalten
    )
    assert "adresspruefung@test.de" not in inhalt


@pytest.mark.asyncio
async def test_an_enqueued_mail_is_delivered_exactly_once(
    db: Session, monkeypatch
) -> None:
    """Zwei Durchgaenge, eine Mail — nicht zwei.

    Der zweite Durchgang laeuft absichtlich unmittelbar nach dem ersten. Ohne
    die befristete Uebernahme (`naechster_versuch_at` wird beim Greifen nach
    vorn geschoben) faende er dieselbe Zeile erneut, und der Betreiber bekaeme
    denselben Bericht zweimal.
    """
    user = _empfaenger(db)
    kennung = _zeile(db, user)
    versandt: list[str] = []

    async def _fake(adresse, auftrag):
        versandt.append(adresse)
        return True

    monkeypatch.setattr(ai_mail_outbox, "_versenden", _fake)

    assert await ai_mail_outbox.runde() == 1
    assert await ai_mail_outbox.runde() == 0

    assert versandt == ["korbnutzer@test.de"]
    db.expire_all()
    zeile = db.get(AiMailOutbox, kennung)
    assert zeile.status == "zugestellt"
    assert zeile.sent_at is not None
    assert zeile.versuche == 1


@pytest.mark.asyncio
async def test_a_failing_delivery_returns_to_the_queue_instead_of_vanishing(
    db: Session, monkeypatch
) -> None:
    """Der Kern der Beanstandung: eine geworfene Ausnahme darf keine Mail kosten.

    Frueher endete genau dieser Fall in `except Exception: return False` — die
    Nachricht existierte danach nirgends mehr, und im Log stand eine Warnung
    ohne Bezug zu irgendetwas, das man noch haette nachholen koennen.
    """
    user = _empfaenger(db)
    kennung = _zeile(db, user)

    async def _wirft(adresse, auftrag):
        raise ConnectionResetError("SMTP hat aufgelegt")

    monkeypatch.setattr(ai_mail_outbox, "_versenden", _wirft)

    await ai_mail_outbox.runde()

    db.expire_all()
    zeile = db.get(AiMailOutbox, kennung)
    assert zeile.status == "offen"
    assert zeile.versuche == 1
    assert "ConnectionResetError" in zeile.letzter_fehler
    # Und sie ist nicht sofort wieder faellig: der Abstand waechst, sonst
    # drehte der Arbeiter bei einem ausgefallenen Anbieter mit voller
    # Geschwindigkeit im Kreis.
    assert await ai_mail_outbox.runde() == 0


@pytest.mark.asyncio
async def test_after_the_last_attempt_the_mail_is_given_up_with_its_error(
    db: Session, monkeypatch
) -> None:
    """Aufgeben ist erlaubt — verschweigen nicht.

    Ein falsch eingetragenes SMTP-Passwort wird auch beim hundertsten Versuch
    nicht richtig. Nach `VERSUCHE_MAX` Anlaeufen steht die Zeile deshalb auf
    `aufgegeben`, und der letzte Fehler steht daneben: er ist das Einzige, womit
    der Betreiber hinterher etwas anfangen kann.
    """
    user = _empfaenger(db)
    kennung = _zeile(db, user)

    async def _wirft(adresse, auftrag):
        raise TimeoutError("keine Antwort")

    monkeypatch.setattr(ai_mail_outbox, "_versenden", _wirft)
    # Ohne Wartezeit zwischen den Versuchen — geprueft wird die Zaehlung, nicht
    # die Uhr. Der Abstand selbst steht im Test darueber.
    monkeypatch.setattr(ai_mail_outbox, "ABSTAND_BASIS", 0.0)

    for _ in range(ai_mail_outbox.VERSUCHE_MAX):
        assert await ai_mail_outbox.runde() == 1

    db.expire_all()
    zeile = db.get(AiMailOutbox, kennung)
    assert zeile.status == "aufgegeben"
    assert zeile.versuche == ai_mail_outbox.VERSUCHE_MAX
    assert "TimeoutError" in zeile.letzter_fehler
    # Aufgegeben heisst auch: nicht mehr angefasst. Sonst waere die Aufgabe nur
    # ein Vermerk und der Arbeiter versuchte es weiter.
    assert await ai_mail_outbox.runde() == 0


@pytest.mark.asyncio
async def test_a_delivery_that_reports_failure_is_retried_too(
    db: Session, monkeypatch
) -> None:
    """`send_email` wirft nicht, es gibt `False` zurueck — beides ist ein Fehlschlag.

    Der Unterschied ist leicht zu uebersehen: eine Funktion, die nie wirft, sieht
    an der Aufrufstelle wie eine aus, die immer gelingt.
    """
    user = _empfaenger(db)
    kennung = _zeile(db, user)

    async def _meldet_falsch(adresse, auftrag):
        return False

    monkeypatch.setattr(ai_mail_outbox, "_versenden", _meldet_falsch)

    await ai_mail_outbox.runde()

    db.expire_all()
    zeile = db.get(AiMailOutbox, kennung)
    assert zeile.status == "offen"
    assert zeile.letzter_fehler == "Versand meldete Fehlschlag"


@pytest.mark.asyncio
async def test_unsubscribing_between_queueing_and_delivery_stops_the_mail(
    db: Session, monkeypatch
) -> None:
    """Wer abbestellt, bekommt auch das nicht mehr, was schon im Korb liegt.

    Genau deshalb steht in der Tabelle `user_id` und nicht die Adresse: die
    Entscheidung "darf ich diesem Menschen schreiben" faellt beim Versand, nicht
    beim Einreihen. Zwischen einem stehenden Auftrag um 08:00 und seinem Bericht
    koennen Minuten liegen, und in denen kann der Schalter umgelegt werden.
    """
    user = _empfaenger(db)
    kennung = _zeile(db, user)

    user.email_notifications = False
    db.commit()

    versandt: list[str] = []

    async def _fake(adresse, auftrag):
        versandt.append(adresse)
        return True

    monkeypatch.setattr(ai_mail_outbox, "_versenden", _fake)

    await ai_mail_outbox.runde()

    assert versandt == []
    db.expire_all()
    zeile = db.get(AiMailOutbox, kennung)
    # Aufgegeben und nicht wiederholt: eine Abbestellung ist keine Stoerung, die
    # sich in einer Minute von selbst behebt.
    assert zeile.status == "aufgegeben"
    assert "kein Empfaenger" in zeile.letzter_fehler


def test_a_queued_mail_does_not_outlive_its_recipient(db: Session) -> None:
    """ON DELETE CASCADE, geprueft an der Datenbank statt an einer Absicht.

    Diese Zusage gehoert eigentlich zu `test_schema_constraints.py` und sollte
    beim naechsten Aufraeumen dorthin wandern; sie steht hier, weil beim Bau
    dieses Korbs nur die eigenen Dateien angefasst werden durften. Der Inhalt
    ist derselbe: eine Mail an ein geloeschtes Konto hat keinen Empfaenger mehr,
    und eine Zeile mit einer `user_id` ins Leere ist nicht wiederherstellbar,
    sondern nur unzustellbar.
    """
    user = _empfaenger(db)
    kennung = _zeile(db, user)

    db.delete(user)
    db.commit()

    assert db.get(AiMailOutbox, kennung) is None


def test_the_migration_carries_the_same_on_delete_as_the_model(tmp_path) -> None:
    """Modell und Migration muessen dasselbe `ON DELETE` tragen.

    Die Testsuite baut ihr Schema mit `create_all` aus den Modellen, der Betrieb
    mit Alembic. Steht die Kaskade nur an einer der beiden Stellen, ist die
    Suite gruen und die Datenbank des Betreibers anders — genau die
    Konstellation, aus der `test_schema_constraints.py` entstanden ist. Der
    Rueckbau bis vor diese Revision und wieder vor beweist ausserdem, dass die
    Tabelle wirklich aus der Migrationskette kommt und nicht aus `create_all`.

    Auch dieser Test gehoert perspektivisch nach `test_schema_constraints.py`.
    """
    db_url = f"sqlite:///{tmp_path / 'outbox.db'}"
    vorher = settings.database_url
    settings.database_url = db_url
    backend_dir = Path(__file__).resolve().parent.parent
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "migrations"))
    engine = create_engine(db_url)
    try:
        Base.metadata.create_all(engine)
        command.stamp(config, "head")

        # Erst nur die Spalten von `20260813_04` zurueck: sie muessen aus der
        # Kette kommen und nicht aus `create_all`. Ein Betreiber, der die
        # Migration faehrt, bekaeme sonst einen Arbeiter, der `fakten` liest,
        # und eine Tabelle, die die Spalte nicht hat.
        command.downgrade(config, "20260813_03")
        engine.dispose()
        spalten = {
            spalte["name"] for spalte in inspect(engine).get_columns("ai_mail_outbox")
        }
        assert "fakten" not in spalten and "rahmen_json" not in spalten

        command.downgrade(config, "20260813_02")
        engine.dispose()
        assert "ai_mail_outbox" not in inspect(engine).get_table_names()

        command.upgrade(config, "head")
        engine.dispose()
        pruefer = inspect(engine)
        fremdschluessel = [
            fk
            for fk in pruefer.get_foreign_keys("ai_mail_outbox")
            if fk.get("constrained_columns") == ["user_id"]
        ]
        assert fremdschluessel, "ai_mail_outbox.user_id hat keinen Fremdschluessel"
        assert fremdschluessel[0]["options"] == {"ondelete": "CASCADE"}
        assert "ix_ai_mail_outbox_faellig" in {
            index["name"] for index in pruefer.get_indexes("ai_mail_outbox")
        }
        # Und die beiden Spalten sind wieder da — nullable, denn eine Zeile aus
        # der Zeit davor ist eine gueltige Mail ohne Angaben fuer das Modell.
        neue = {
            spalte["name"]: spalte
            for spalte in pruefer.get_columns("ai_mail_outbox")
            if spalte["name"] in ("fakten", "rahmen_json")
        }
        assert set(neue) == {"fakten", "rahmen_json"}
        assert all(spalte["nullable"] for spalte in neue.values())
    finally:
        engine.dispose()
        settings.database_url = vorher


@pytest.mark.asyncio
async def test_ten_thousand_due_mails_stay_within_the_allowed_concurrency(
    db: Session, monkeypatch
) -> None:
    """Die Zusage, wegen der es diese Tabelle gibt.

    Zehntausend Auftraege, alle auf dieselbe Minute gestellt. Frueher waren das
    zehntausend Threads und zehntausend SMTP-Verbindungen; niemand haette
    gemessen, ab welcher Zahl der Anbieter dichtmacht, weil der Prozess vorher
    ausgeht.

    Gemessen wird deshalb dreierlei: der **Hoechststand** gleichzeitiger
    Zustellungen (nie mehr als `GLEICHZEITIG`), die Zahl der Threads (sie bleibt
    stehen) und am Ende, dass **jede einzelne** Zeile zugestellt ist. Der Versand
    ist gefaelscht — es geht kein Byte ins Netz.
    """
    user = _empfaenger(db)
    zweiter = _empfaenger(db, "korbnutzer2")

    anzahl = 10_000
    jetzt = datetime.now(timezone.utc)
    db.execute(
        insert(AiMailOutbox),
        [
            {
                "id": str(uuid.uuid4()),
                "user_id": user.id if index % 2 == 0 else zweiter.id,
                "anlass": "ai-task-report",
                "betreff": f"Bericht {index}",
                "text_body": "Alles ruhig.",
                "html_body": None,
                "status": "offen",
                "versuche": 0,
                "naechster_versuch_at": jetzt - timedelta(seconds=1),
                "created_at": jetzt,
            }
            for index in range(anzahl)
        ],
    )
    db.commit()

    laufend = 0
    hoechststand = 0
    zugestellt = 0

    async def _fake(adresse, auftrag):
        nonlocal laufend, hoechststand, zugestellt
        laufend += 1
        hoechststand = max(hoechststand, laufend)
        # Ein echter Versand wartet auf die Gegenseite. Ohne diesen
        # Aufgabenwechsel liefe jede Zustellung am Stueck durch und der
        # Hoechststand waere immer 1 — der Test saehe dann nichts.
        await asyncio.sleep(0)
        laufend -= 1
        zugestellt += 1
        return True

    monkeypatch.setattr(ai_mail_outbox, "_versenden", _fake)
    # Groessere Stapel, kein Warten zwischen den Durchgaengen: gemessen wird die
    # Schranke, nicht die Taktzahl. Die Schranke selbst bleibt unberuehrt.
    monkeypatch.setattr(ai_mail_outbox, "STAPEL", 250)
    monkeypatch.setattr(ai_mail_outbox, "TAKT", 0.01)

    threads_vorher = threading.active_count()
    assert ai_mail_outbox.arbeiter_starten() is True
    try:
        for _ in range(6000):
            offen = db.execute(
                select(AiMailOutbox.id).where(AiMailOutbox.status == "offen").limit(1)
            ).first()
            if offen is None:
                break
            db.expire_all()
            await asyncio.sleep(0.01)
    finally:
        await ai_mail_outbox.aufraeumen()

    assert hoechststand <= ai_mail_outbox.GLEICHZEITIG
    assert hoechststand > 1, "der Test haette eine Ueberschreitung nicht sehen koennen"
    # Kein Thread je Mail. Ein einziger Arbeiter auf der Ereignisschleife der
    # Anwendung reicht fuer zehntausend Nachrichten.
    assert threading.active_count() <= threads_vorher
    assert zugestellt == anzahl

    db.expire_all()
    fertig = db.execute(
        select(AiMailOutbox.status, AiMailOutbox.id).where(
            AiMailOutbox.status == "zugestellt"
        )
    ).all()
    assert len(fertig) == anzahl


# ── Der Verfassungsschritt liegt im Arbeiter ──────────────────────────────


@pytest.mark.asyncio
async def test_the_worker_composes_the_mail_and_sends_that_version(
    db: Session, monkeypatch
) -> None:
    """Die Naht, um die es geht: der Korb haelt die Fakten, der Arbeiter schreibt.

    Vorher endete jeder Berichtspfad im Koroutinenweg, weil der
    Verfassungsschritt asynchron ist und das Einreihen eine fertige Mail
    verlangte. Die Folge war, dass die Mail an einem Prozess hing statt an einer
    Zeile. Hier steht das Gegenteil: eingereiht wird der feste Text, hinaus geht
    der verfasste.
    """
    user = _empfaenger(db)
    kennung, _ = _zeile_mit_fakten(db, user)
    gerufen = _modell(monkeypatch)
    versandt = _mitschnitt(monkeypatch)

    assert await ai_mail_outbox.runde() == 1

    assert len(versandt) == 1
    adresse, auftrag = versandt[0]
    assert adresse == "korbnutzer@test.de"
    assert "Alle drei Server laufen" in auftrag.betreff
    assert "Ich habe die drei Server geprüft." in auftrag.text_body
    assert "Ich habe die drei Server geprüft." in (auftrag.html_body or "")
    # Der feste Text steht nicht zusaetzlich darin — sonst saehe der Betreiber
    # dieselbe Aussage zweimal, einmal vom Modell und einmal aus dem Code.
    assert RUECKFALL not in auftrag.text_body

    # Gerufen wurde mit genau dem, was in der Zeile stand.
    assert len(gerufen) == 1
    assert gerufen[0]["anlass"] == "ai-task-report"
    assert gerufen[0]["user_id"] == user.id
    assert "Ergebnis laut Panel: erledigt" in gerufen[0]["fakten"]

    db.expire_all()
    zeile = db.get(AiMailOutbox, kennung)
    assert zeile.status == "zugestellt"
    # Die Zeile selbst bleibt, wie sie eingereiht wurde. Die verfasste Fassung
    # dort hineinzuschreiben waere eine zweite Wahrheit ueber dieselbe Mail —
    # und beim naechsten Versuch waere unklar, welche der Rueckfall ist.
    assert RUECKFALL in zeile.text_body


@pytest.mark.asyncio
async def test_the_composition_step_is_skipped_when_the_row_carries_no_facts(
    db: Session, monkeypatch
) -> None:
    """Eine Zeile ohne Fakten geht so hinaus, wie sie eingereiht wurde.

    Das ist der Zustand jeder Zeile, die vor `20260813_04` angelegt wurde, und
    der Weg jeder Mail, die gar keinen Modelltext haben soll. Ein Modellaufruf
    dafuer waere Verbrauch ohne Anlass.
    """
    user = _empfaenger(db)
    _zeile(db, user, betreff="Ohne Fakten")
    gerufen = _modell(monkeypatch)
    versandt = _mitschnitt(monkeypatch)

    await ai_mail_outbox.runde()

    assert gerufen == []
    assert versandt[0][1].betreff == "Ohne Fakten"


@pytest.mark.asyncio
async def test_a_failing_composition_sends_the_fallback_exactly_once(
    db: Session, monkeypatch
) -> None:
    """Ein klemmendes Modell kostet den schoenen Text, nie die Mail — und nie zweimal.

    Den Fehlschlag beim Verfassen als Zustellfehler zu werten waere die
    schlimmste der moeglichen Antworten: die Zeile ginge zurueck in die
    Warteschlange, und der Betreiber bekaeme bei einem laenger klemmenden Modell
    dieselbe Mail `VERSUCHE_MAX`-mal mit festem Text.
    """
    user = _empfaenger(db)
    kennung, _ = _zeile_mit_fakten(db, user)
    _modell(monkeypatch, platzt=RuntimeError("Anbieter antwortet nicht"))
    versandt = _mitschnitt(monkeypatch)

    assert await ai_mail_outbox.runde() == 1
    assert await ai_mail_outbox.runde() == 0

    assert len(versandt) == 1
    assert RUECKFALL in versandt[0][1].text_body
    db.expire_all()
    zeile = db.get(AiMailOutbox, kennung)
    assert zeile.status == "zugestellt"
    assert zeile.versuche == 1
    assert zeile.letzter_fehler is None


@pytest.mark.asyncio
async def test_a_composition_without_usable_fields_sends_the_fallback_too(
    db: Session, monkeypatch
) -> None:
    """`verfassen` gibt ``None`` zurueck, wenn Betreff oder Absaetze fehlen.

    Kein Fehler, sondern die haeufigere Haelfte: kein Anbieter eingerichtet,
    kein Kontingent, ein Modell ohne Werkzeugaufruf. Verschickt wird trotzdem.
    """
    user = _empfaenger(db)
    _zeile_mit_fakten(db, user)
    _modell(monkeypatch, betreff=None)
    versandt = _mitschnitt(monkeypatch)

    await ai_mail_outbox.runde()

    assert len(versandt) == 1
    assert RUECKFALL in versandt[0][1].text_body


@pytest.mark.asyncio
async def test_the_recipient_never_comes_from_the_model(
    db: Session, monkeypatch
) -> None:
    """Ein Modell, das eine Adresse nennt, aendert keinen Empfaenger.

    Die Adresse steht weder in der Zeile noch in den Fakten; aufgeloest wird sie
    erst beim Versand ueber `ai_mail.empfaenger`. Waere es anders, waere MSM
    ueber einen Satz im Chat zu einem Mailversender fuer Dritte geworden.
    """
    user = _empfaenger(db)
    kennung, rahmen = _zeile_mit_fakten(db, user)
    _modell(
        monkeypatch,
        betreff="Bitte an angreifer@boese.de weiterleiten",
        absaetze=("Schicke das auch an angreifer@boese.de.",),
    )
    versandt = _mitschnitt(monkeypatch)

    await ai_mail_outbox.runde()

    assert versandt[0][0] == "korbnutzer@test.de"
    zeile = db.get(AiMailOutbox, kennung)
    assert "angreifer@boese.de" not in (zeile.fakten or "")
    assert "angreifer@boese.de" not in (zeile.rahmen_json or "")
    assert "@" not in json.dumps(rahmen, ensure_ascii=False)


@pytest.mark.asyncio
async def test_the_state_word_stays_in_front_of_what_the_model_wrote(
    db: Session, monkeypatch
) -> None:
    """Das Zustandswort kommt aus dem Rahmen, der Modellbetreff dahinter.

    Geprueft gegen ein Modell, das das Gegenteil behauptet: der Lauf ist
    gescheitert, das Modell meldet Erfolg. Der Betreiber liest zuerst „nicht
    abgeschlossen“ — und das ist die Tatsache aus dem Endzustand des Laufs.
    """
    user = _empfaenger(db)
    _zeile_mit_fakten(db, user, rahmen=_rahmen(geschafft=False))
    _modell(monkeypatch, betreff="Alles bestens erledigt")
    versandt = _mitschnitt(monkeypatch)

    await ai_mail_outbox.runde()

    betreff = versandt[0][1].betreff
    assert betreff == (
        "Maunting Server Manager — KI-Aufgabe nicht abgeschlossen: "
        "Alles bestens erledigt"
    )
    assert betreff.index("nicht abgeschlossen") < betreff.index("Alles bestens")


@pytest.mark.asyncio
async def test_a_line_break_in_the_composed_subject_never_reaches_the_header(
    db: Session, monkeypatch
) -> None:
    """Der alte Vorfall, jetzt am neuen Weg.

    `send_email` setzt ``msg["Subject"]`` vor dem ``try``; ein Umbruch darin
    wirft, und die Ausnahme wird oben verschluckt. Der Lauf ist gruen, die Mail
    ist weg. Der Umbruch kann jetzt aus zwei Richtungen kommen — aus dem
    Modelltext und aus dem Rahmen —, bereinigt wird die fertige Zeile.
    """
    user = _empfaenger(db)
    _zeile_mit_fakten(db, user)
    _modell(monkeypatch, betreff="Bericht\r\nBcc: fremd@boese.de\x00jetzt")
    versandt = _mitschnitt(monkeypatch)

    await ai_mail_outbox.runde()

    betreff = versandt[0][1].betreff
    assert "\n" not in betreff and "\r" not in betreff and "\x00" not in betreff
    # Der Beweis am fertigen Kopf und nicht an der Hilfsfunktion: was hier ohne
    # Ausnahme durchgeht, geht auch in `_send_smtp` durch.
    nachricht = EmailMessage()
    nachricht["Subject"] = betreff
    assert nachricht["Subject"] == betreff


@pytest.mark.asyncio
async def test_a_restart_between_queueing_and_sending_loses_nothing(
    db: Session, monkeypatch
) -> None:
    """Der eigentliche Gewinn: die Mail haengt an einer Zeile, nicht an einem Prozess.

    Nachgestellt wird ein Neustart, indem der Arbeiter erst **nach** dem
    Einreihen entsteht — mit `zuruecksetzen_fuer_tests` dazwischen, also ohne
    jeden Rest aus der Zeit davor. Frueher lag die Mail in diesem Moment in
    einem Thread, der mit dem Prozess starb: der Bericht ueber den Auftrag, bei
    dem niemand davorsass, war ersatzlos weg.
    """
    user = _empfaenger(db)
    kennung, _ = _zeile_mit_fakten(db, user)

    # Der Prozess geht — was im Speicher stand, ist damit fort.
    ai_mail_outbox.zuruecksetzen_fuer_tests()

    _modell(monkeypatch)
    versandt = _mitschnitt(monkeypatch)
    monkeypatch.setattr(ai_mail_outbox, "TAKT", 0.01)

    assert ai_mail_outbox.arbeiter_starten() is True
    try:
        for _ in range(500):
            db.expire_all()
            if db.get(AiMailOutbox, kennung).status == "zugestellt":
                break
            await asyncio.sleep(0.01)
    finally:
        await ai_mail_outbox.aufraeumen()

    assert len(versandt) == 1
    assert "Alle drei Server laufen" in versandt[0][1].betreff
    db.expire_all()
    assert db.get(AiMailOutbox, kennung).status == "zugestellt"


@pytest.mark.asyncio
async def test_ten_thousand_mails_with_facts_stay_within_the_same_limit(
    db: Session, monkeypatch
) -> None:
    """Die Lastmessung noch einmal — jetzt einschliesslich der Modellaufrufe.

    Genau dafuer liegt der Verfassungsschritt im Arbeiter und nicht im
    Einreihen. Beim Einreihen zu verfassen hiesse, dass zehntausend gleichzeitig
    endende Auftraege zehntausend gleichzeitige Modellaufrufe ausloesen — die
    Rechnung, wegen der es diesen Korb gibt, nur mit einer teureren Gegenseite.
    Gezaehlt wird deshalb **ein** Hoechststand ueber beide Schritte.
    """
    user = _empfaenger(db, "lastnutzer")
    rahmen = _rahmen()
    rahmen_json = json.dumps(rahmen, ensure_ascii=False)

    anzahl = 10_000
    jetzt = datetime.now(timezone.utc)
    db.execute(
        insert(AiMailOutbox),
        [
            {
                "id": str(uuid.uuid4()),
                "user_id": user.id,
                "anlass": "ai-task-report",
                "betreff": f"Bericht {index}",
                "text_body": RUECKFALL,
                "html_body": None,
                "fakten": "Ergebnis laut Panel: erledigt",
                "rahmen_json": rahmen_json,
                "status": "offen",
                "versuche": 0,
                "naechster_versuch_at": jetzt - timedelta(seconds=1),
                "created_at": jetzt,
            }
            for index in range(anzahl)
        ],
    )
    db.commit()

    laufend = 0
    hoechststand = 0
    verfasst = 0
    zugestellt = 0

    async def _fake_verfassen(
        *, user_id, anlass, fakten, provider_id=None, client=None
    ):
        nonlocal laufend, hoechststand, verfasst
        laufend += 1
        hoechststand = max(hoechststand, laufend)
        # Ein echter Modellaufruf wartet auf die Gegenseite, und zwar laenger
        # als ein SMTP-Versand. Ohne diesen Aufgabenwechsel saehe der Test die
        # Gleichzeitigkeit nicht.
        await asyncio.sleep(0)
        laufend -= 1
        verfasst += 1
        return ai_mail_text.Mailtext(betreff="Kurz", absaetze=["Alles ruhig."])

    async def _fake_versenden(adresse, auftrag):
        nonlocal laufend, hoechststand, zugestellt
        laufend += 1
        hoechststand = max(hoechststand, laufend)
        await asyncio.sleep(0)
        laufend -= 1
        zugestellt += 1
        return True

    monkeypatch.setattr(ai_mail_text, "verfassen", _fake_verfassen)
    monkeypatch.setattr(ai_mail_outbox, "_versenden", _fake_versenden)
    monkeypatch.setattr(ai_mail_outbox, "STAPEL", 250)
    monkeypatch.setattr(ai_mail_outbox, "TAKT", 0.01)

    threads_vorher = threading.active_count()
    assert ai_mail_outbox.arbeiter_starten() is True
    try:
        for _ in range(6000):
            offen = db.execute(
                select(AiMailOutbox.id).where(AiMailOutbox.status == "offen").limit(1)
            ).first()
            if offen is None:
                break
            db.expire_all()
            await asyncio.sleep(0.01)
    finally:
        await ai_mail_outbox.aufraeumen()

    assert hoechststand <= ai_mail_outbox.GLEICHZEITIG
    assert hoechststand > 1, "der Test haette eine Ueberschreitung nicht sehen koennen"
    # Der Modellaufruf bringt keinen Thread mit. Er laeuft in derselben Aufgabe
    # wie der Versand, unter derselben Schranke.
    assert threading.active_count() <= threads_vorher
    assert verfasst == anzahl
    assert zugestellt == anzahl
