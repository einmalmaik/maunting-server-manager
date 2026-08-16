"""Zusagen, die im Schema stehen — nicht im Code.

Diese Datei gibt es wegen eines Betriebsfehlers, den 2519 gruene Tests nicht
sehen konnten. `ai_action_proposals.server_id` kaskadierte auf `servers.id`:
loeschte die KI einen Server, vernichtete PostgreSQL im selben Zug den Vorschlag,
der das Loeschen angeordnet hatte. Der Aufruf stolperte danach ueber die eigene,
verschwundene Zeile und meldete "Aktionsvorschlag nicht gefunden" — fuer einen
Vorgang, der tatsaechlich gelungen war.

Die Testsuite konnte das nicht bemerken, weil SQLite Fremdschluessel nur auf
Verlangen prueft und niemand danach verlangt hatte. Ein Verhalten, das die
Datenbank durchsetzt, gehoert deshalb hierher und nicht in die Tests des
jeweiligen Dienstes: dort wuerde es niemand vermissen, wenn es wieder verschwindet.
"""

import io
from contextlib import redirect_stdout
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import models  # noqa: F401 - registriert das vollstaendige ORM-Schema
from config import settings
from database import Base


def _fremdschluessel(inspector, tabelle: str, spalte: str) -> dict:
    for fk in inspector.get_foreign_keys(tabelle):
        if fk.get("constrained_columns") == [spalte]:
            return fk
    raise AssertionError(f"{tabelle}.{spalte} hat gar keinen Fremdschluessel mehr")


def _frisch(engine):
    """Ein Inspector auf einer **neuen** Verbindung.

    Alembic faehrt seine Migrationen ueber eine eigene Engine; der Pool der
    Engine, mit der hier geprueft wird, haelt daneben Verbindungen, die vor der
    Migration geoeffnet wurden. Fuer ein blosses ALTER faellt das nicht auf —
    aber wenn eine Migration eine Tabelle **loescht und neu anlegt**, behaelt
    eine solche Verbindung ihren alten, in sich zusammengefallenen Schema-Cache:
    `SELECT sql FROM sqlite_master` liefert den frischen Text, waehrend
    `PRAGMA foreign_key_list` leer bleibt.

    Die Reflexion in SQLAlchemy braucht **beides** und meldet die Abweichung nur
    als Warnung ("SQL-parsed foreign key constraint could not be located in
    PRAGMA foreign_keys"); `get_foreign_keys` gibt danach eine leere Liste
    zurueck. Ein Test, der darauf prueft, meldet dann "die Migration hat den
    Fremdschluessel vergessen" — fuer eine Migration, die ihn korrekt anlegt.
    Genau diese Fehldiagnose ist beim Bau von `ai_tasks` einmal passiert.

    `dispose()` wirft den Pool weg; die naechste Verbindung liest das Schema neu.
    """
    engine.dispose()
    return inspect(engine)


def test_fremdschluessel_sind_im_test_scharf(db: Session) -> None:
    """Ohne diesen Schalter ist jede Aussage ueber ON DELETE hier wertlos.

    Der Test prueft nicht ein Verhalten des Panels, sondern eine Eigenschaft des
    Pruefstands. Faellt der Listener in `conftest.py` irgendwann heraus, laufen
    alle nachfolgenden Kaskadentests weiterhin gruen — sie pruefen dann nur
    nichts mehr. Das ist die gefaehrlichere Sorte Fehlschlag, deshalb steht sie
    hier ausdruecklich.
    """
    assert db.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_ein_vorschlag_ueberlebt_seinen_server(db: Session) -> None:
    """Die Zusage auf Datenbankebene, ohne jede Fachlogik dazwischen.

    Die Dienst-Tests belegen, dass `execute_proposal` danach das Richtige tut.
    Dieser hier belegt, worauf sie sich verlassen: dass die Datenbank die Zeile
    stehen laesst. Beides zu trennen ist Absicht — sonst waere bei einem
    Fehlschlag nicht zu sehen, ob das Schema oder der Ablauf schuld ist.
    """
    inspector = inspect(db.get_bind())

    assert _fremdschluessel(inspector, "ai_action_proposals", "server_id")["options"] == {
        "ondelete": "SET NULL"
    }


def test_ein_lauf_ueberlebt_seinen_server(db: Session) -> None:
    """Derselbe Gedanke wie beim Vorschlag, eine Tabelle weiter.

    `ai_runs.last_server_id` haelt fest, um welchen Server es in diesem Zug
    ging. Der Lauf gehoert aber der Unterhaltung und nicht dem Server: mit
    `CASCADE` haette das Loeschen eines Servers rueckwirkend jeden Chat
    ausgeduennt, in dem je jemand nach ihm gefragt hat.
    """
    inspector = inspect(db.get_bind())

    assert _fremdschluessel(inspector, "ai_runs", "last_server_id")["options"] == {
        "ondelete": "SET NULL"
    }


def test_ein_werkzeugergebnis_ueberlebt_seinen_lauf(db: Session) -> None:
    """`ai_tool_results.run_id` ordnet zu, es besitzt nicht.

    Die Spalte begrenzt den Rueckfluss in den Kontext auf den letzten Lauf. Das
    Ergebnis selbst gehoert aber der Unterhaltung — dort haengt es bereits mit
    `CASCADE`. Mit `CASCADE` auch am Lauf haette das Aufraeumen alter Laeufe die
    Werkzeugbelege der Unterhaltung mitgenommen, obwohl sie ihr eigenes
    Loeschverhalten schon haben.
    """
    inspector = inspect(db.get_bind())

    assert _fremdschluessel(inspector, "ai_tool_results", "run_id")["options"] == {
        "ondelete": "SET NULL"
    }


def test_ein_produkt_ueberlebt_seine_rolle(db: Session) -> None:
    """`hoster_products.role_id` ordnet zu, es besitzt nicht.

    Die Spalte sagt nur, welche globale Rolle eine Buchung mitbringt. Mit
    `RESTRICT` waere jede Rolle unloeschbar, sobald sie einmal an einem Produkt
    hing — mit `CASCADE` haette das Loeschen einer Rolle die Produktzeile
    mitgenommen und damit stillschweigend alle laufenden Vertraege ihres
    Blueprints und ihrer Ressourcengrenzen beraubt. `SET NULL` bedeutet exakt
    das, was NULL in dieser Spalte immer bedeutet hat: keine Zusatzrolle.
    """
    inspector = inspect(db.get_bind())

    assert _fremdschluessel(inspector, "hoster_products", "role_id")["options"] == {
        "ondelete": "SET NULL"
    }


def test_ein_vertrag_ueberlebt_die_rolle_die_er_vergeben_hat(db: Session) -> None:
    """`hoster_services.granted_role_id` ist ein Beleg, kein Besitz.

    Die Spalte haelt fest, welche Rolle dieser Vertrag dem Kunden verschafft
    hat — nur daraus laesst sich spaeter ableiten, was zurueckzunehmen ist.
    `CASCADE` wuerde beim Loeschen einer Rolle den ganzen Vertrag vernichten,
    samt Serverbezug und Kuendigungsfrist. `SET NULL` ist hier auch fachlich das
    Richtige: ist die Rolle fort, gibt es nichts mehr zu entziehen.
    """
    inspector = inspect(db.get_bind())

    assert _fremdschluessel(inspector, "hoster_services", "granted_role_id")["options"] == {
        "ondelete": "SET NULL"
    }


def _notiz_anlegen(
    db: Session,
    *,
    incident_id: int,
    user_id: int,
    mode: str = "briefed",
    run_id: str | None = None,
) -> None:
    """Eine Guardian-Notiz per SQL, ohne ORM-Kaskaden dazwischen.

    Bewusst nicht ueber das Modell: geprueft werden soll, was die **Datenbank**
    tut, wenn die Zeile darueber verschwindet — nicht, was SQLAlchemy vorher
    schon von sich aus aufraeumt.
    """
    db.execute(
        text(
            "INSERT INTO ai_guardian_notices "
            "(incident_id, user_id, mode, run_id, created_at) "
            "VALUES (:incident_id, :user_id, :mode, :run_id, '2026-08-12')"
        ),
        {
            "incident_id": incident_id,
            "user_id": user_id,
            "mode": mode,
            "run_id": run_id,
        },
    )
    db.commit()


def _notizen(db: Session) -> list[tuple]:
    db.expire_all()
    return list(
        db.execute(
            text("SELECT incident_id, user_id, mode, run_id FROM ai_guardian_notices")
        )
    )


def _vorfall(db: Session, server_id: int) -> int:
    from models import Incident

    vorfall = Incident(
        server_id=server_id,
        title="Container weg",
        description="Der Container ist nicht mehr da.",
        type="container_missing",
        status="open",
        fingerprint="fp-1",
    )
    db.add(vorfall)
    db.commit()
    db.refresh(vorfall)
    return vorfall.id


def _lauf(db: Session, user_id: int) -> str:
    from models import AiConversation, AiRun

    db.add(
        AiConversation(id="conv-guardian", user_id=user_id, title="Heilung")
    )
    db.add(
        AiRun(
            id="run-guardian",
            conversation_id="conv-guardian",
            user_id=user_id,
            status="running",
        )
    )
    db.commit()
    return "run-guardian"


def test_eine_guardian_notiz_verschwindet_mit_ihrem_vorfall(
    db: Session, owner_user, test_server
) -> None:
    """Ohne Vorfall gibt es nichts mehr zu merken.

    Die Notiz beantwortet genau eine Frage: *ist dieser Mensch wegen dieses
    Vorfalls schon versorgt worden?* Ist der Vorfall fort, ist die Frage
    gegenstandslos — und eine Zeile, die auf eine verschwundene Vorfallsnummer
    zeigt, waere ab dann eine Sperre gegen einen Vorfall, den es nicht gibt.

    Das steht hier und nicht in den Tests des Guardian-Dienstes, weil es
    ausschliesslich die Datenbank durchsetzt. Die Testsuite konnte
    ``ON DELETE`` lange gar nicht beobachten, weil SQLite Fremdschluessel nur auf
    Verlangen prueft — verschwindet der Listener aus `conftest.py`, faellt das
    oben auf und nicht hier.
    """
    inspector = inspect(db.get_bind())
    assert _fremdschluessel(inspector, "ai_guardian_notices", "incident_id")[
        "options"
    ] == {"ondelete": "CASCADE"}

    incident_id = _vorfall(db, test_server.id)
    _notiz_anlegen(db, incident_id=incident_id, user_id=owner_user.id)

    db.execute(text("DELETE FROM incidents WHERE id = :id"), {"id": incident_id})
    db.commit()

    assert _notizen(db) == []


def test_eine_guardian_notiz_verschwindet_mit_ihrem_benutzer(
    db: Session, owner_user, test_server
) -> None:
    """Die Notiz gehoert einem Menschen — sie ueberlebt ihn nicht.

    Der Ausloeser fragt "wurde **dieser Benutzer** wegen dieses Vorfalls schon
    versorgt". Ist das Konto geloescht, gibt es niemanden mehr, den die Antwort
    betraefe. Ein `SET NULL` waere hier falsch: die Spalte ist Teil der
    Entdopplung, und eine Notiz ohne Benutzer wuerde in
    `uq_ai_guardian_notices_incident_user` eine Zeile belegen, die keinem mehr
    zuzuordnen ist.
    """
    inspector = inspect(db.get_bind())
    assert _fremdschluessel(inspector, "ai_guardian_notices", "user_id")[
        "options"
    ] == {"ondelete": "CASCADE"}

    incident_id = _vorfall(db, test_server.id)
    _notiz_anlegen(db, incident_id=incident_id, user_id=owner_user.id)

    db.execute(text("DELETE FROM users WHERE id = :id"), {"id": owner_user.id})
    db.commit()

    assert _notizen(db) == []


def test_eine_guardian_notiz_ueberlebt_ihren_lauf(
    db: Session, owner_user, test_server
) -> None:
    """`run_id` ist ein Verweis auf den Lauf, kein Besitz durch ihn.

    Mit `CASCADE` haette das Abraeumen alter Laeufe die Notiz mitgenommen — und
    genau daran haengt der Sinn der Tabelle. Der Ausloeser laeuft im
    Sechzig-Sekunden-Takt ueber die offenen Vorfaelle, und ein Vorfall bleibt
    offen, bis ihn jemand loest. Ohne die Notiz saehe der naechste Durchlauf
    einen laengst behandelten Vorfall als neu, startete einen weiteren
    Heilungslauf und haette das KI-Kontingent des Benutzers in einer
    Viertelstunde aufgebraucht.

    `SET NULL` haelt die Aussage, auf die es ankommt: *dieser Vorfall war
    versorgt.* Sie bleibt wahr, auch wenn niemand mehr nachlesen kann, mit
    welchem Lauf.
    """
    inspector = inspect(db.get_bind())
    assert _fremdschluessel(inspector, "ai_guardian_notices", "run_id")[
        "options"
    ] == {"ondelete": "SET NULL"}

    incident_id = _vorfall(db, test_server.id)
    run_id = _lauf(db, owner_user.id)
    _notiz_anlegen(
        db, incident_id=incident_id, user_id=owner_user.id, mode="healing", run_id=run_id
    )

    db.execute(text("DELETE FROM ai_runs WHERE id = :id"), {"id": run_id})
    db.commit()

    assert _notizen(db) == [(incident_id, owner_user.id, "healing", None)]


def test_derselbe_vorfall_wird_demselben_menschen_nur_einmal_gemeldet(
    db: Session, owner_user, test_server
) -> None:
    """Die Entdopplung steht in der Datenbank, nicht in einer Pruefung davor.

    `max_instances=1` am Scheduler gilt nur innerhalb **eines** Prozesses. Laeuft
    das Panel mit mehreren Uvicorn-Arbeitern, gibt es den Auftrag mehrfach; zwei
    Durchlaeufe sehen denselben offenen Vorfall im selben Moment, und eine
    Pruefung "gibt es schon eine Notiz?" im Python-Code liefert beiden ein Nein.
    Dann ist `uq_ai_guardian_notices_incident_user` die einzige Schranke, die
    noch traegt — und der Unterschied zwischen einem gestarteten Heilungslauf und
    zweien auf demselben Server.

    Der zweite Einfuegeversuch traegt bewusst einen anderen `mode`: es geht um
    das Paar aus Vorfall und Benutzer, nicht um eine identische Zeile.
    """
    incident_id = _vorfall(db, test_server.id)
    _notiz_anlegen(db, incident_id=incident_id, user_id=owner_user.id, mode="briefed")

    with pytest.raises(IntegrityError):
        _notiz_anlegen(
            db, incident_id=incident_id, user_id=owner_user.id, mode="healing"
        )
    db.rollback()

    assert len(_notizen(db)) == 1


def test_die_datenbank_kennt_genau_zwei_arten_von_notiz(
    db: Session, owner_user, test_server
) -> None:
    """`mode` ist eine Aufzaehlung, und die Datenbank haelt sie.

    Der Wert unterscheidet die beiden Wege, auf denen ein Mensch versorgt sein
    kann: `briefed` — es gab keine Freigabe, die KI erwaehnt den Vorfall beim
    naechsten Chat; `healing` — es gab eine, ein Lauf wurde gestartet. Eine dritte
    Art gibt es nicht, und ein Tippfehler in einer spaeteren Migration oder in
    einem Wartungsskript soll nicht stillschweigend als eine durchgehen: der
    Ausloeser entschiede dann anders, ohne dass irgendwo etwas fehlschluege.

    Anders als bei den Fremdschluesseln nebenan setzt SQLite CHECK-Bedingungen
    von sich aus durch — der Test misst hier also dieselbe Zusage wie im Betrieb.
    """
    incident_id = _vorfall(db, test_server.id)
    zweiter = _vorfall(db, test_server.id)

    _notiz_anlegen(db, incident_id=incident_id, user_id=owner_user.id, mode="briefed")
    _notiz_anlegen(db, incident_id=zweiter, user_id=owner_user.id, mode="healing")

    dritter = _vorfall(db, test_server.id)
    with pytest.raises(IntegrityError):
        _notiz_anlegen(db, incident_id=dritter, user_id=owner_user.id, mode="notified")
    db.rollback()

    assert {zeile[2] for zeile in _notizen(db)} == {"briefed", "healing"}


def _aufgabe_anlegen(
    db: Session,
    *,
    task_id: str,
    user_id: int,
    last_run_id: str | None = None,
    kind: str = "report",
    plan_kind: str = "daily",
    channel: str = "chat",
) -> None:
    """Eine KI-Aufgabe per SQL, aus demselben Grund wie `_notiz_anlegen`."""
    db.execute(
        text(
            "INSERT INTO ai_tasks "
            "(id, user_id, title, instruction, kind, plan_kind, time_of_day, "
            " time_zone, channel, enabled, next_run_at, last_run_id, "
            " created_at, updated_at) "
            "VALUES (:id, :user_id, 'Serverbericht', 'Sieh nach den Servern.', "
            " :kind, :plan_kind, '08:00', 'Europe/Berlin', :channel, 1, "
            " '2026-08-14 06:00:00', :last_run_id, '2026-08-13', '2026-08-13')"
        ),
        {
            "id": task_id,
            "user_id": user_id,
            "kind": kind,
            "plan_kind": plan_kind,
            "channel": channel,
            "last_run_id": last_run_id,
        },
    )
    db.commit()


def _aufgaben(db: Session) -> list[tuple]:
    db.expire_all()
    return list(db.execute(text("SELECT id, user_id, last_run_id FROM ai_tasks")))


def test_eine_ki_aufgabe_verschwindet_mit_ihrem_benutzer(
    db: Session, owner_user
) -> None:
    """Eine Aufgabe ohne Besitzer waere ein Termin ohne Rechte.

    Ein faelliger Lauf handelt im Namen dessen, der die Aufgabe angelegt hat: mit
    seinen Rechten, aus seinem Kontingent, an seine Adresse. Bliebe die Zeile
    nach der Loeschung des Kontos stehen, saehe der Takt alle sechzig Sekunden
    einen Termin, zu dem es keinen Akteur mehr gibt — im besten Fall eine
    Logzeile je Minute, im schlechteren ein Lauf im Namen einer Nummer, die
    inzwischen jemand anderem gehoert.
    """
    inspector = inspect(db.get_bind())
    assert _fremdschluessel(inspector, "ai_tasks", "user_id")["options"] == {
        "ondelete": "CASCADE"
    }

    _aufgabe_anlegen(db, task_id="task-1", user_id=owner_user.id)
    db.execute(text("DELETE FROM users WHERE id = :id"), {"id": owner_user.id})
    db.commit()

    assert _aufgaben(db) == []


def test_eine_ki_aufgabe_ueberlebt_ihren_letzten_lauf(
    db: Session, owner_user
) -> None:
    """`last_run_id` ist ein Beleg, kein Besitz.

    Mit `CASCADE` haette das Abraeumen alter Laeufe die **Aufgabe** geloescht —
    und damit einen stehenden Auftrag vernichtet, den ein Mensch ausdruecklich
    bestaetigt hat, ohne dass ihn je jemand danach gefragt haette. Der Verweis
    beantwortet nur "wann lief sie zuletzt"; verschwindet die Antwort, bleibt
    die Frage bestehen.
    """
    inspector = inspect(db.get_bind())
    assert _fremdschluessel(inspector, "ai_tasks", "last_run_id")["options"] == {
        "ondelete": "SET NULL"
    }

    run_id = _lauf(db, owner_user.id)
    _aufgabe_anlegen(db, task_id="task-2", user_id=owner_user.id, last_run_id=run_id)

    db.execute(text("DELETE FROM ai_runs WHERE id = :id"), {"id": run_id})
    db.commit()

    assert _aufgaben(db) == [("task-2", owner_user.id, None)]


@pytest.mark.parametrize(
    ("spalte", "wert"),
    [("kind", "alles"), ("plan_kind", "cron"), ("channel", "sms")],
)
def test_die_datenbank_weist_erfundene_aufgabenarten_ab(
    db: Session, owner_user, spalte: str, wert: str
) -> None:
    """Die drei Aufzaehlungen stehen im Schema, nicht nur in Python.

    Jede von ihnen entscheidet ueber etwas, das niemand mitliest: `kind`
    darueber, ob ein unbeaufsichtigter Lauf Schreibwerkzeuge bekommt, `plan_kind`
    darueber, wie die naechste Faelligkeit gerechnet wird, `channel` darueber,
    wohin das Ergebnis geht. Ein Tippfehler in einem spaeteren Wartungsskript
    soll hier auflaufen und nicht als stille vierte Art durchgehen.
    """
    with pytest.raises(IntegrityError):
        _aufgabe_anlegen(
            db, task_id="task-3", user_id=owner_user.id, **{spalte: wert}
        )
    db.rollback()

    assert _aufgaben(db) == []


def test_ein_backup_kann_seinen_nachweis_tragen(db: Session) -> None:
    """`sha256` und `verified_at` gibt es, und beide duerfen NULL sein.

    Ohne die beiden Spalten ist die Zusage der autonomen Heilung nicht
    einloesbar: nichts ueberschreiben oder loeschen, bevor ein Backup
    **nachweislich** geglueckt ist. Das blosse Vorhandensein einer Backup-Zeile
    taugt dafuer nicht — der Remote-Agent-Pfad legt sie vor der Arbeit des
    Agenten an — und `size_mb` ist `bytes // (1024*1024)` und damit 0 fuer jedes
    Archiv unter einem Megabyte, also ausgerechnet fuer den frisch angelegten
    Server, bei dem am wenigsten schiefgehen kann.

    Nullable ist keine Nachlaessigkeit, sondern die Richtung des Nachweises: NULL
    heisst **unbewiesen**, nie "kaputt". Ein Server-Default oder NOT NULL haette
    jeden Altbestand rueckwirkend als geprueft gelten lassen — und damit
    ausgerechnet den Bestand freigegeben, ueber den niemand etwas weiss.
    """
    spalten = {
        spalte["name"]: spalte
        for spalte in inspect(db.get_bind()).get_columns("backups")
    }

    assert spalten["sha256"]["nullable"] is True
    assert spalten["verified_at"]["nullable"] is True


def test_die_migration_traegt_backupnachweis_und_notiztabelle(tmp_path: Path) -> None:
    """Modell und Migration duerfen auch hier nicht auseinanderlaufen.

    Die Tests bauen das Schema mit `create_all` aus den Modellen, die Produktion
    mit Alembic. Steht eine Spalte oder ein `ON DELETE` nur an einer der beiden
    Stellen, ist die Suite gruen und der Betrieb kaputt — genau die
    Konstellation, aus der diese Datei entstanden ist. Der Rueckbau bis **vor**
    die beiden Revisionen und wieder vor beweist, dass sie tatsaechlich in der
    Kette stehen und nicht bloss aus `create_all` stammen.

    Fuer die Notiztabelle steht hier ausdruecklich auch das `SET NULL` am Lauf.
    Eine Migration, die dort versehentlich `CASCADE` schreibt, wuerde in den
    Tests nie auffallen: das Modell traegt es richtig, und im Betrieb faenge der
    Ausloeser nach dem naechsten Aufraeumen alter Laeufe auf laengst behandelten
    Vorfaellen von vorne an.
    """
    db_url = f"sqlite:///{tmp_path / 'guardian_constraint.db'}"
    vorher = settings.database_url
    settings.database_url = db_url
    backend_dir = Path(__file__).resolve().parent.parent
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "migrations"))
    engine = create_engine(db_url)
    try:
        Base.metadata.create_all(engine)
        command.stamp(config, "head")

        # Vor `20260812_04`: kein Nachweis am Backup, und die Notiztabelle
        # (`20260812_05`) gibt es noch gar nicht.
        command.downgrade(config, "20260812_03")
        assert {
            spalte["name"] for spalte in inspect(engine).get_columns("backups")
        } & {"sha256", "verified_at"} == set()
        assert "ai_guardian_notices" not in inspect(engine).get_table_names()

        command.upgrade(config, "head")
        gewandert = {
            spalte["name"]: spalte
            for spalte in inspect(engine).get_columns("backups")
        }
        # Dieselbe Nullbarkeit wie am Modell. Ein `nullable=False` in der
        # Migration liesse das Upgrade auf einer bestehenden Anlage mit Backups
        # sofort scheitern; ein Server-Default machte jeden Altbestand
        # rueckwirkend zum Nachweis.
        assert gewandert["sha256"]["nullable"] is True
        assert gewandert["verified_at"]["nullable"] is True

        assert _fremdschluessel(
            inspect(engine), "ai_guardian_notices", "incident_id"
        )["options"] == {"ondelete": "CASCADE"}
        assert _fremdschluessel(
            inspect(engine), "ai_guardian_notices", "user_id"
        )["options"] == {"ondelete": "CASCADE"}
        assert _fremdschluessel(
            inspect(engine), "ai_guardian_notices", "run_id"
        )["options"] == {"ondelete": "SET NULL"}
    finally:
        engine.dispose()
        settings.database_url = vorher


def test_je_benutzer_und_art_genau_eine_unterhaltung(db: Session) -> None:
    """Die Trennung von Dauerchat und Guardian-Fenster haelt die Datenbank.

    Sie ist der Grund, warum ueberhaupt repariert werden kann: solange beides in
    derselben Zeile stand, startete eine Heilung nicht, wenn der Mensch etwas
    laufen hatte, und eine getippte Nachricht loeste eine laufende Heilung ab.

    Geprueft wird beides, und das ist der Punkt: eine **zweite** Zeile derselben
    Art muss scheitern (sonst waere aus dem Fenster eine Ablage geworden, und
    `get_or_create_conversation` griffe irgendeine davon), eine Zeile der
    **anderen** Art muss durchgehen (sonst gaebe es das Fenster nicht).
    """
    from models import User

    user = User(
        username="fensterpruefung",
        email_encrypted="x",
        email_hash="fensterpruefung",
        password_hash="x",
        is_active=True,
    )
    db.add(user)
    db.flush()

    db.execute(
        text(
            "INSERT INTO ai_conversations (id, user_id, kind, title, created_at, updated_at) "
            "VALUES ('k-1', :uid, 'primary', 't', '2026-08-16', '2026-08-16')"
        ),
        {"uid": user.id},
    )
    # Andere Art, derselbe Mensch: erlaubt.
    db.execute(
        text(
            "INSERT INTO ai_conversations (id, user_id, kind, title, created_at, updated_at) "
            "VALUES ('k-2', :uid, 'guardian', 't', '2026-08-16', '2026-08-16')"
        ),
        {"uid": user.id},
    )

    # Dieselbe Art ein zweites Mal: nicht erlaubt.
    with pytest.raises(IntegrityError):
        db.execute(
            text(
                "INSERT INTO ai_conversations (id, user_id, kind, title, created_at, updated_at) "
                "VALUES ('k-3', :uid, 'guardian', 't', '2026-08-16', '2026-08-16')"
            ),
            {"uid": user.id},
        )
    db.rollback()


def test_die_datenbank_kennt_genau_zwei_unterhaltungsarten(db: Session) -> None:
    """``kind`` ist eine Aufzaehlung, und die Datenbank haelt sie.

    Dieselbe Ueberlegung wie beim Gedaechtnisbereich darunter: eine Art, die nur
    im Python-Code steht, ist gegen einen Tippfehler in einer Migration oder
    einen direkten Datenbankzugriff wehrlos. ``kind='gardian'`` waere ein
    Fenster, das keine Route je findet — und ein Reparaturlauf, der hineinredet,
    schriebe an einen Ort, den niemand aufmachen kann.
    """
    from models import User
    from models.ai_conversation import ARTEN

    assert ARTEN == ("primary", "guardian")

    user = User(
        username="artenpruefung",
        email_encrypted="x",
        email_hash="artenpruefung",
        password_hash="x",
        is_active=True,
    )
    db.add(user)
    db.flush()

    with pytest.raises(IntegrityError):
        db.execute(
            text(
                "INSERT INTO ai_conversations (id, user_id, kind, title, created_at, updated_at) "
                "VALUES ('k-x', :uid, 'gardian', 't', '2026-08-16', '2026-08-16')"
            ),
            {"uid": user.id},
        )
    db.rollback()


def test_die_migration_traegt_die_unterhaltungsart(tmp_path: Path) -> None:
    """Modell und Migration duerfen auch hier nicht auseinanderlaufen.

    Der Rueckbau bis **vor** `20260816_02` beweist, dass die Spalte aus der
    Kette stammt und nicht bloss aus `create_all`. Und er prueft die Richtung,
    die im Betrieb weh taete: das Downgrade muss den alten, engeren Index
    wiederherstellen — sonst stuende eine zurueckgerollte Anlage ohne jede
    Eindeutigkeit da, und `get_or_create_primary_conversation` legte bei jedem
    Rennen einen weiteren Chat an.
    """
    db_url = f"sqlite:///{tmp_path / 'fenster_constraint.db'}"
    vorher = settings.database_url
    settings.database_url = db_url
    backend_dir = Path(__file__).resolve().parent.parent
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "migrations"))
    engine = create_engine(db_url)
    try:
        Base.metadata.create_all(engine)
        command.stamp(config, "head")

        command.downgrade(config, "20260816_01")
        inspector = _frisch(engine)
        assert "kind" not in {
            spalte["name"] for spalte in inspector.get_columns("ai_conversations")
        }
        assert "uq_ai_conversations_user" in {
            index["name"] for index in inspector.get_indexes("ai_conversations")
        }

        command.upgrade(config, "head")
        inspector = _frisch(engine)
        spalten = {
            spalte["name"]: spalte
            for spalte in inspector.get_columns("ai_conversations")
        }
        assert spalten["kind"]["nullable"] is False
        indizes = {
            index["name"]: index for index in inspector.get_indexes("ai_conversations")
        }
        assert "uq_ai_conversations_user" not in indizes
        assert indizes["uq_ai_conversations_user_kind"]["unique"]
        assert indizes["uq_ai_conversations_user_kind"]["column_names"] == [
            "user_id",
            "kind",
        ]
    finally:
        engine.dispose()
        settings.database_url = vorher


def _scope_check(inspector) -> str:
    for pruefung in inspector.get_check_constraints("ai_memory_entries"):
        if pruefung.get("name") == "ck_ai_memory_entries_scope":
            return pruefung.get("sqltext") or ""
    raise AssertionError("Der Scope-CHECK des Gedaechtnisses ist verschwunden")


def test_die_datenbank_kennt_genau_fuenf_gedaechtnisbereiche(db: Session) -> None:
    """Der Scope ist eine Aufzaehlung — und zwar eine, die die Datenbank haelt.

    Der Wert entscheidet, wer einen Eintrag lesen darf. Eine Aufzaehlung, die
    nur im Python-Code steht, ist gegen einen Tippfehler in einer Migration
    oder einen direkten Datenbankzugriff wehrlos: `scope='server_share'` waere
    ein Eintrag, den niemand mehr sieht und niemand mehr loeschen kann.

    Anders als bei den Fremdschluesseln nebenan setzt SQLite CHECK-Bedingungen
    von sich aus durch, der Test misst hier also dieselbe Zusage wie im Betrieb.
    """
    gueltig = ("user", "server", "server_shared", "team", "panel")
    for scope in gueltig:
        db.execute(
            text(
                "INSERT INTO ai_memory_entries "
                "(id, scope, scope_identity, key, value_encrypted, origin, "
                " aad_version, use_count, created_at, updated_at) "
                "VALUES (:id, :scope, :ident, 'k', 'x', 'user', 2, 0, "
                " '2026-08-11', '2026-08-11')"
            ),
            {"id": f"id-{scope}", "scope": scope, "ident": f"ident-{scope}"},
        )

    with pytest.raises(IntegrityError):
        db.execute(
            text(
                "INSERT INTO ai_memory_entries "
                "(id, scope, scope_identity, key, value_encrypted, origin, "
                " aad_version, use_count, created_at, updated_at) "
                "VALUES ('id-x', 'server_share', 'ident-x', 'k', 'x', 'user', "
                " 2, 0, '2026-08-11', '2026-08-11')"
            )
        )
    db.rollback()

    assert set(gueltig) == {
        wert.strip().strip("'")
        for wert in _scope_check(inspect(db.get_bind()))
        .split("(", 1)[1]
        .rsplit(")", 1)[0]
        .split(",")
    }


def test_die_migration_erzeugt_dasselbe_wie_das_modell(tmp_path: Path) -> None:
    """Modell und Migration duerfen nicht auseinanderlaufen.

    Die Tests bauen das Schema mit `create_all` aus den Modellen, die Produktion
    mit Alembic. Steht das `ON DELETE` nur an einer der beiden Stellen richtig,
    ist die Suite gruen und der Betrieb kaputt — genau die Konstellation, aus
    der dieser Fehler kam. Der Test faehrt deshalb die echte Migration und
    vergleicht das Ergebnis mit dem Modell.
    """
    db_url = f"sqlite:///{tmp_path / 'constraint.db'}"
    vorher = settings.database_url
    settings.database_url = db_url
    backend_dir = Path(__file__).resolve().parent.parent
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "migrations"))
    engine = create_engine(db_url)
    try:
        Base.metadata.create_all(engine)
        command.stamp(config, "head")

        # Zurueck auf den Stand davor: dort galt noch CASCADE — das ist der
        # Zustand, in dem die Datenbank eines Betreibers gerade steht.
        command.downgrade(config, "20260810_05")
        assert _fremdschluessel(
            inspect(engine), "ai_action_proposals", "server_id"
        )["options"] == {"ondelete": "CASCADE"}

        command.upgrade(config, "head")
        assert _fremdschluessel(
            inspect(engine), "ai_action_proposals", "server_id"
        )["options"] == {"ondelete": "SET NULL"}

        # Dieselbe Zusage fuer den Lauf, und zwar aus der gefahrenen Migration
        # heraus. `create_all` oben wuerde sie auch ohne Migration erfuellen —
        # der Rueckbau bis vor die Revision und wieder vor beweist, dass sie
        # tatsaechlich in der Kette steht.
        command.downgrade(config, "20260811_01")
        assert "last_server_id" not in {
            spalte["name"] for spalte in inspect(engine).get_columns("ai_runs")
        }
        command.upgrade(config, "head")
        assert _fremdschluessel(inspect(engine), "ai_runs", "last_server_id")[
            "options"
        ] == {"ondelete": "SET NULL"}

        # Und derselbe Nachweis fuer den fuenften Gedaechtnisbereich. Ein CHECK,
        # der nur aus `create_all` stammt, laesst den Betrieb genau dann
        # auflaufen, wenn die Migration ihn vergessen hat.
        command.downgrade(config, "20260811_02")
        assert "server_shared" not in _scope_check(inspect(engine))
        command.upgrade(config, "head")
        assert "server_shared" in _scope_check(inspect(engine))

        # Und derselbe Nachweis fuer die Rolle bei Buchung. Die Spalte kommt
        # ueber einen Batch-Umbau von `hoster_products` in die Tabelle — laesst
        # dieser Umbau den Fremdschluessel weg, sieht `create_all` davon nichts
        # und der Test oben bliebe gruen.
        command.downgrade(config, "20260812_01")
        assert "role_id" not in {
            spalte["name"] for spalte in inspect(engine).get_columns("hoster_products")
        }
        command.upgrade(config, "head")
        assert _fremdschluessel(inspect(engine), "hoster_products", "role_id")[
            "options"
        ] == {"ondelete": "SET NULL"}

        # Und fuer den Beleg der Vergabe. Er kam eine Revision spaeter als die
        # Rolle am Produkt und aus gutem Grund: ohne ihn entzieht ein
        # Tarifwechsel die falsche Rolle. Faellt die Spalte aus der Migration,
        # laeuft der Betrieb wieder in genau diesen Fehler.
        command.downgrade(config, "20260812_02")
        assert "granted_role_id" not in {
            spalte["name"] for spalte in inspect(engine).get_columns("hoster_services")
        }
        command.upgrade(config, "head")
        assert _fremdschluessel(inspect(engine), "hoster_services", "granted_role_id")[
            "options"
        ] == {"ondelete": "SET NULL"}

        # Und fuer die Tabelle der stehenden KI-Auftraege. Sie ist der Ort, an
        # dem der Zeitplan lebt — APScheduler haelt seine Jobs nur im Speicher.
        # Fehlt sie in der gefahrenen Migration, startet das Panel gar nicht
        # erst (`schema_manager._missing_model_schema`), aber erst beim
        # Betreiber: `create_all` legt sie in den Tests ohnehin an.
        command.downgrade(config, "20260812_05")
        assert "ai_tasks" not in _frisch(engine).get_table_names()
        command.upgrade(config, "head")
        # `_frisch` und nicht `inspect`: diese Tabelle wird von der Migration
        # ganz geloescht und neu angelegt, und darauf reagiert eine schon
        # geoeffnete SQLite-Verbindung anders als auf ein ALTER (siehe dort).
        assert _fremdschluessel(_frisch(engine), "ai_tasks", "user_id")["options"] == {
            "ondelete": "CASCADE"
        }
        assert _fremdschluessel(_frisch(engine), "ai_tasks", "last_run_id")[
            "options"
        ] == {"ondelete": "SET NULL"}

        # Und fuer den Memory-Vorrat an der Rolle. Er ist eine blosse Spalte
        # ohne Fremdschluessel, aber der Ausfall waere derselbe: `create_all`
        # legt sie in den Tests ohnehin an, faellt das `add_column` kuenftig
        # bei einem Rebase aus der Kette, bleibt die Suite gruen. Beim
        # Bestandsbetreiber knallt es dann nach dem Upgrade bei jedem Lesen der
        # Rollenlimits — also im Chat und in den Einstellungen zugleich.
        command.downgrade(config, "20260814_01")
        assert "max_memory_entries" not in {
            spalte["name"] for spalte in _frisch(engine).get_columns("role_ai_limits")
        }
        command.upgrade(config, "head")
        # `_frisch` wie bei `ai_tasks`: die Spalte kommt ueber einen
        # Batch-Umbau, und den beantwortet SQLite je nach Alter der Verbindung
        # aus einem Schema-Cache, der die Tabelle noch ohne sie kennt.
        gewandert = {
            spalte["name"]: spalte
            for spalte in _frisch(engine).get_columns("role_ai_limits")
        }
        # Nullable, und zwar mit derselben Begruendung wie beim Backupnachweis
        # oben: NULL heisst „der Betreiber hat nichts gesagt“. Ein NOT NULL mit
        # server_default 100 haette die bisher im Code stehende Grenze als
        # seine eigene Politik in die Datenbank geschrieben — sichtbar in der
        # Maske, obwohl er sie nie gesetzt hat.
        assert gewandert["max_memory_entries"]["nullable"] is True
    finally:
        engine.dispose()
        settings.database_url = vorher


def test_das_downgrade_bleibt_nicht_am_ersten_uuid_ziel_haengen() -> None:
    """Der Rueckbau muss auf PostgreSQL laufen, nicht nur auf SQLite.

    `20260809_02` hat `audit_logs.target_id` zu Text gemacht, weil Memory und
    Skills dort UUIDs eintragen. Das Downgrade castete sie ungefiltert mit
    `target_id::integer` zurueck: auf PostgreSQL bricht das nach dem ersten
    `remember`-Aufruf mit "invalid input syntax for type integer" ab — und zwar
    als **erste** Anweisung, wodurch die gesamte Kette an dieser Revision
    haengen bleibt.

    Kein bestehender Test konnte das sehen: `test_migration_chain_upgrade.py`
    faehrt die Downgrades auf SQLite, wo `postgresql_using` gar nicht angewandt
    wird. Dieser Test erzeugt die DDL deshalb im Offline-Modus fuer den
    PostgreSQL-Dialekt — dafuer braucht es keine laufende Datenbank, nur den
    Dialektnamen in der URL.
    """
    backend_dir = Path(__file__).resolve().parent.parent
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "migrations"))

    vorher = settings.database_url
    settings.database_url = "postgresql+psycopg2://msm:msm@localhost/msm"
    puffer = io.StringIO()
    try:
        with redirect_stdout(puffer):
            command.downgrade(config, "20260809_02:20260809_01", sql=True)
    finally:
        settings.database_url = vorher

    anweisung = next(
        zeile
        for zeile in puffer.getvalue().splitlines()
        if "ALTER COLUMN target_id" in zeile
    )

    assert "USING target_id::integer" not in anweisung, (
        "Ungefilterter Cast: das Downgrade scheitert an jeder UUID in der Spalte"
    )
    # Nicht umkehrbare Werte muessen benannt behandelt werden, statt die
    # Migration abbrechen zu lassen.
    assert "CASE" in anweisung and "NULL" in anweisung


# ── Der Reparaturauftrag ──────────────────────────────────────────────────


def _auftrag_anlegen(
    db: Session,
    *,
    kennung: str,
    incident_id: int,
    server_id: int | None,
    user_id: int,
    run_id: str | None = None,
) -> None:
    """Einen Reparaturauftrag per SQL, ohne ORM-Kaskaden dazwischen.

    Bewusst nicht ueber das Modell: geprueft werden soll, was die **Datenbank**
    tut, wenn die Zeile darueber verschwindet — nicht, was SQLAlchemy vorher
    schon von sich aus aufraeumt.
    """
    db.execute(
        text(
            "INSERT INTO ai_guardian_repairs "
            "(id, incident_id, server_id, user_id, phase, attempt, "
            " next_run_at, deadline_at, last_run_id, created_at, updated_at) "
            "VALUES (:id, :incident_id, :server_id, :user_id, 'diagnose', 0, "
            " '2026-08-16', '2026-08-16', :run_id, '2026-08-16', '2026-08-16')"
        ),
        {
            "id": kennung,
            "incident_id": incident_id,
            "server_id": server_id,
            "user_id": user_id,
            "run_id": run_id,
        },
    )
    db.commit()


def _auftraege(db: Session) -> list[tuple]:
    db.expire_all()
    return list(
        db.execute(
            text(
                "SELECT id, incident_id, server_id, user_id, last_run_id "
                "FROM ai_guardian_repairs"
            )
        )
    )


def test_ein_reparaturauftrag_verschwindet_mit_seinem_vorfall(
    db: Session, owner_user, test_server
) -> None:
    """Ohne Vorfall gibt es nichts zu reparieren.

    Anders als die Notiz ist der Auftrag kein Beleg ueber die Vergangenheit,
    sondern ein laufendes Vorhaben — eines, das ins Leere liefe. Bliebe die
    Zeile stehen, waere sie ausserdem weiterhin die Sperre gegen einen neuen
    Auftrag, und zwar fuer eine Vorfallsnummer, die es nicht mehr gibt.
    """
    incident_id = _vorfall(db, test_server.id)
    _auftrag_anlegen(
        db, kennung="rep-1", incident_id=incident_id,
        server_id=test_server.id, user_id=owner_user.id,
    )

    db.execute(text("DELETE FROM incidents WHERE id = :id"), {"id": incident_id})
    db.commit()

    assert _auftraege(db) == []
    assert _fremdschluessel(
        inspect(db.get_bind()), "ai_guardian_repairs", "incident_id"
    )["options"] == {"ondelete": "CASCADE"}


def test_ein_reparaturauftrag_verschwindet_mit_seinem_freigeber(
    db: Session, owner_user, test_server
) -> None:
    """Gehandelt wird in seinem Namen, mit seinen Rechten, auf seine Freigabe hin.

    Ist er weg, gibt es niemanden, als den der Auftrag laufen koennte — und ein
    Auftrag, der mit den Rechten eines geloeschten Kontos weiterliefe, waere
    genau die Konstruktion, die dieses Modul an anderer Stelle ausdruecklich
    ausschliesst.
    """
    from models import User

    user = User(
        username="reparaturfreigeber",
        email_encrypted="x",
        email_hash="reparaturfreigeber",
        password_hash="x",
        is_active=True,
    )
    db.add(user)
    db.commit()
    _auftrag_anlegen(
        db, kennung="rep-4", incident_id=_vorfall(db, test_server.id),
        server_id=test_server.id, user_id=user.id,
    )

    db.execute(text("DELETE FROM users WHERE id = :id"), {"id": user.id})
    db.commit()

    assert _auftraege(db) == []
    assert _fremdschluessel(
        inspect(db.get_bind()), "ai_guardian_repairs", "user_id"
    )["options"] == {"ondelete": "CASCADE"}


def test_ein_reparaturauftrag_ueberlebt_seinen_letzten_lauf(
    db: Session, owner_user, test_server
) -> None:
    """Der Lauf ist hier ein Beleg, kein Besitz.

    Raeumt jemand alte Laeufe ab, bleibt der Auftrag bestehen — mit
    ``last_run_id = NULL``. Mit CASCADE verschwaende mitten in einer laufenden
    Reparatur der Auftrag, und der naechste Takt legte einen neuen an: derselbe
    Vorfall, von vorne, mit einem frischen Versuchszaehler.
    """
    run_id = _lauf(db, owner_user.id)
    _auftrag_anlegen(
        db, kennung="rep-5", incident_id=_vorfall(db, test_server.id),
        server_id=test_server.id, user_id=owner_user.id, run_id=run_id,
    )

    db.execute(text("DELETE FROM ai_runs WHERE id = :id"), {"id": run_id})
    db.commit()

    zeilen = _auftraege(db)
    assert len(zeilen) == 1
    assert zeilen[0][4] is None
    assert _fremdschluessel(
        inspect(db.get_bind()), "ai_guardian_repairs", "last_run_id"
    )["options"] == {"ondelete": "SET NULL"}


def test_ein_vorfall_bekommt_je_freigeber_nur_einen_auftrag(
    db: Session, owner_user, test_server
) -> None:
    """Die Entdopplung liegt in der Datenbank, nicht in einer Pruefung davor.

    Sie ist der Grund, warum der Takt einen Vorfall nicht alle sechzig Sekunden
    erneut uebernimmt, und sie haelt auch dann, wenn das Panel je mit mehreren
    Arbeitsprozessen laeuft: ``max_instances=1`` gilt nur innerhalb eines
    Prozesses.
    """
    incident_id = _vorfall(db, test_server.id)
    _auftrag_anlegen(
        db, kennung="rep-6", incident_id=incident_id,
        server_id=test_server.id, user_id=owner_user.id,
    )

    with pytest.raises(IntegrityError):
        _auftrag_anlegen(
            db, kennung="rep-7", incident_id=incident_id,
            server_id=test_server.id, user_id=owner_user.id,
        )
    db.rollback()


def test_die_datenbank_kennt_genau_sieben_reparaturphasen(
    db: Session, owner_user, test_server
) -> None:
    """``phase`` ist eine Aufzaehlung, und die Datenbank haelt sie.

    Eine Phase, die nur im Python-Code steht, ist gegen einen Tippfehler in
    einer Migration oder einen direkten Datenbankzugriff wehrlos. ``'eingrif'``
    waere ein Auftrag, den `faellige_bearbeiten` weckt, weil er in keiner
    Endphase steht — und den `_naechste_phase_setzen` dann in den Zweig
    "beobachtet und nicht belegt" schickt, also in einen Eingriff, den nie
    jemand angeordnet hat.
    """
    from models.ai_guardian_repair import ARBEITSPHASEN, ENDPHASEN, PHASEN

    assert ARBEITSPHASEN == ("diagnose", "eingriff", "beobachtung")
    assert ENDPHASEN == ("erledigt", "eskaliert", "aufgegeben", "abgebrochen")
    assert len(PHASEN) == 7

    incident_id = _vorfall(db, test_server.id)
    with pytest.raises(IntegrityError):
        db.execute(
            text(
                "INSERT INTO ai_guardian_repairs "
                "(id, incident_id, server_id, user_id, phase, attempt, "
                " next_run_at, deadline_at, created_at, updated_at) "
                "VALUES ('rep-x', :incident_id, :server_id, :user_id, 'eingrif', 0, "
                " '2026-08-16', '2026-08-16', '2026-08-16', '2026-08-16')"
            ),
            {
                "incident_id": incident_id,
                "server_id": test_server.id,
                "user_id": owner_user.id,
            },
        )
    db.rollback()


def test_die_migration_traegt_den_reparaturauftrag(tmp_path: Path) -> None:
    """Modell und Migration duerfen nicht auseinanderlaufen.

    Der Rueckbau bis **vor** `20260816_03` beweist, dass die Tabelle aus der
    Kette stammt und nicht bloss aus `create_all` — genau der Unterschied, den
    eine frische Testdatenbank sonst verdeckt. Und er prueft die vier
    ``ON DELETE``-Regeln dort, wo sie im Betrieb wirklich herkommen.

    ``server_id`` faellt dabei als einzige auf ``SET NULL``: verschwindet der
    Server mitten in einer Reparatur, soll der Takt die Zeile noch einmal
    finden, keinen Server sehen und den Auftrag ordentlich als ``abgebrochen``
    beenden. Ein CASCADE haette dieselbe Wirkung ohne Spur.
    """
    db_url = f"sqlite:///{tmp_path / 'reparatur_constraint.db'}"
    vorher = settings.database_url
    settings.database_url = db_url
    backend_dir = Path(__file__).resolve().parent.parent
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "migrations"))
    engine = create_engine(db_url)
    try:
        Base.metadata.create_all(engine)
        command.stamp(config, "head")

        command.downgrade(config, "20260816_02")
        assert "ai_guardian_repairs" not in _frisch(engine).get_table_names()

        command.upgrade(config, "head")
        inspector = _frisch(engine)
        assert "ai_guardian_repairs" in inspector.get_table_names()
        assert _fremdschluessel(
            inspector, "ai_guardian_repairs", "incident_id"
        )["options"] == {"ondelete": "CASCADE"}
        assert _fremdschluessel(
            inspector, "ai_guardian_repairs", "server_id"
        )["options"] == {"ondelete": "SET NULL"}
        assert _fremdschluessel(
            inspector, "ai_guardian_repairs", "user_id"
        )["options"] == {"ondelete": "CASCADE"}
        assert _fremdschluessel(
            inspector, "ai_guardian_repairs", "last_run_id"
        )["options"] == {"ondelete": "SET NULL"}
        spalten = {
            spalte["name"]: spalte
            for spalte in inspector.get_columns("ai_guardian_repairs")
        }
        # Ohne Frist kann ein Auftrag, der bei jedem Anlauf ein bisschen
        # weiterkommt, tagelang Kosten verursachen, ohne dass je eine Mail den
        # Betreiber erreicht. Deshalb NOT NULL und nicht "meistens gesetzt".
        assert spalten["deadline_at"]["nullable"] is False
        assert spalten["next_run_at"]["nullable"] is True
    finally:
        engine.dispose()
        settings.database_url = vorher


def test_die_migration_traegt_die_guardian_uebersteuerung(tmp_path: Path) -> None:
    """Die Uebersteuerung muss aus der Kette kommen, nicht aus ``create_all``.

    Eine Spalte, die nur das Modell kennt, faellt im Test nie auf und im Betrieb
    sofort: `compile_guardian_config` liest sie bei **jedem** Reconcile-Takt
    ueber **jeden** Server, und ein fehlendes Feld waere dort kein stiller
    Rueckfall auf "keine Uebersteuerung", sondern ein ``OperationalError`` in
    der Guardian-Synchronisation der ganzen Node.

    Nullable ist die Zusage dahinter: "keine Uebersteuerung" ist NULL und nicht
    ``'{}'``. Beides waere lesbar, aber nur eines davon laesst sich am Bestand
    zaehlen, ohne JSON zu parsen — und `routers/guardian.reset_overrides`
    entscheidet genau daran, ob es ueberhaupt etwas zu tun gibt.
    """
    db_url = f"sqlite:///{tmp_path / 'uebersteuerung_constraint.db'}"
    vorher = settings.database_url
    settings.database_url = db_url
    backend_dir = Path(__file__).resolve().parent.parent
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "migrations"))
    engine = create_engine(db_url)
    try:
        Base.metadata.create_all(engine)
        command.stamp(config, "head")

        command.downgrade(config, "20260816_03")
        spalten = {s["name"] for s in _frisch(engine).get_columns("servers")}
        assert "guardian_overrides_json" not in spalten

        command.upgrade(config, "head")
        spalten = {
            s["name"]: s for s in _frisch(engine).get_columns("servers")
        }
        assert "guardian_overrides_json" in spalten
        assert spalten["guardian_overrides_json"]["nullable"] is True
    finally:
        engine.dispose()
        settings.database_url = vorher
