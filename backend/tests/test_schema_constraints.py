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
