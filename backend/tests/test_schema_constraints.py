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

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

import models  # noqa: F401 - registriert das vollstaendige ORM-Schema
from config import settings
from database import Base


def _fremdschluessel(inspector, tabelle: str, spalte: str) -> dict:
    for fk in inspector.get_foreign_keys(tabelle):
        if fk.get("constrained_columns") == [spalte]:
            return fk
    raise AssertionError(f"{tabelle}.{spalte} hat gar keinen Fremdschluessel mehr")


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
