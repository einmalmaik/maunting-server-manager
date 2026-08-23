"""Der Systembereich: eine Einstellung, die nur enger werden darf als gedacht.

`users.ai_desktop_systembereich` entscheidet, wie weit die KI auf dem Rechner
des Benutzers aus dem freigegebenen Ordner heraus darf. Drei Dinge daran
koennen still kaputtgehen, und jedes davon hat hier einen Test:

* Der **Vorgabewert**. ``lesen`` ist der heutige Zustand (``desktop_system``
  listet schon jedes Verzeichnis auf). Ein Update, das ihn auf ``aus`` oder
  ``schreiben`` verschiebt, aendert das Verhalten aller Bestandskonten, ohne
  dass jemand einen Schalter angefasst hat.
* Der **Rueckfall** bei einem Wert, den diese Fassung nicht kennt. Er muss zur
  Vorgabe fuehren und nie zum hoechsten Wert — sonst oeffnet ausgerechnet ein
  kaputter Datensatz den Systembereich.
* Die **Datenbank** selbst. Eine Aufzaehlung, die nur im Python-Code steht, ist
  gegen einen direkten Datenbankzugriff oder eine spaetere Migration wehrlos
  (derselbe Grund wie in `test_schema_constraints.py`).

Anders als bei Fremdschluesseln setzt SQLite CHECK-Bedingungen von sich aus
durch; der Test misst hier also dieselbe Zusage wie der Betrieb.
"""

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
from models.user import (
    SYSTEMBEREICHE,
    SYSTEMBEREICH_STANDARD,
    User,
    systembereich_des_benutzers,
)


def _frisch(engine):
    """Ein Inspector auf einer **neuen** Verbindung.

    Die Migration baut ``users`` auf SQLite komplett neu auf
    (``batch_alter_table``). Eine Verbindung aus dem Pool, die vorher geoeffnet
    wurde, behaelt dabei ihren alten Schema-Cache und meldet die Spalte je nach
    Frage einmal so und einmal anders. Woertlich uebernommen aus
    `test_schema_constraints._frisch`, wo dieselbe Falle ausfuehrlich steht.
    """
    engine.dispose()
    return inspect(engine)


def _alembic(backend_dir: Path) -> Config:
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "migrations"))
    return config


def test_ein_frisches_konto_darf_lesen(regular_user: User) -> None:
    """Der Vorgabewert ist der heutige Zustand — nicht die vorsichtigste Wahl.

    ``aus`` waere hier verlockend ("sicher ist sicher"), aber falsch: das Panel
    kann heute schon jedes Verzeichnis des Rechners auflisten. Wer nach dem
    Update fragt, wo sein Platz hingeht, bekaeme ploetzlich eine Absage auf eine
    Auskunft, die er gestern noch bekommen hat — und wuerde das fuer einen
    Fehler halten, nicht fuer eine Entscheidung.
    """
    assert regular_user.ai_desktop_systembereich == "lesen"
    assert systembereich_des_benutzers(regular_user) == "lesen"
    assert SYSTEMBEREICH_STANDARD == "lesen"


@pytest.mark.parametrize("kaputt", ["vollzugriff", "root", "LESEN", "", None])
def test_ein_unbekannter_wert_wird_als_lesen_gelesen(
    regular_user: User, kaputt: str | None
) -> None:
    """Ein Wert, den diese Fassung nicht kennt, darf nie mehr freigeben.

    Die Faelle sind nicht ausgedacht: ein Downgrade auf eine aeltere Panel-
    Fassung, ein Betreiber mit ``psql``, ein Tippfehler in einer kuenftigen
    Migration. ``'LESEN'`` ist der Weg ueber die Tastatur — Grossschreibung ist
    schlicht ein anderer Wert. ``None`` deckt den Fall ab, dass eine kuenftige
    Migration den ``server_default`` vergisst und die Spalte leer bleibt.

    Entscheidend ist die Richtung: keiner dieser Werte darf als ``schreiben``
    durchgehen. Ausgerechnet ein kaputter Datensatz waere sonst der Schluessel
    zum Systembereich.
    """
    regular_user.ai_desktop_systembereich = kaputt

    gelesen = systembereich_des_benutzers(regular_user)

    assert gelesen == "lesen"
    assert gelesen != "schreiben"


def test_die_drei_zonen_sind_die_einzigen(regular_user: User) -> None:
    """Was das Modell erlaubt, muss die Lesefunktion auch zurueckgeben.

    Der Test haelt beide Seiten zusammen: eine vierte Zone im Tupel ohne
    passende Migration faellt hier auf, und eine Lesefunktion, die einen
    gueltigen Wert stillschweigend zur Vorgabe macht, ebenso.
    """
    assert SYSTEMBEREICHE == ("aus", "lesen", "schreiben")

    for bereich in SYSTEMBEREICHE:
        regular_user.ai_desktop_systembereich = bereich
        assert systembereich_des_benutzers(regular_user) == bereich

    # Nachsichtig lesen, streng speichern: ein Rand aus einem Eingabefeld ist
    # derselbe Bereich und kein unbekannter Wert. In die Datenbank kaeme er
    # ohnehin nicht (der CHECK unten), aber die Werkzeugschicht reicht auch
    # Objekte herein, die nie eine Zeile waren.
    regular_user.ai_desktop_systembereich = "  schreiben  "
    assert systembereich_des_benutzers(regular_user) == "schreiben"


def test_die_datenbank_weist_eine_vierte_zone_ab(db: Session, regular_user: User) -> None:
    """Die Aufzaehlung haelt die Datenbank, nicht der Python-Code.

    Ein ``UPDATE`` von Hand ist der realistische Weg an der Anwendung vorbei —
    und ``'vollzugriff'`` ist genau die Sorte Wert, die jemand fuer die
    naechstgroessere Stufe halten koennte. Ohne CHECK stuende er in der Zeile,
    die Lesefunktion machte daraus ``lesen``, und der Betreiber saehe in der
    Oberflaeche etwas, das nirgends wirkt.
    """
    for bereich in SYSTEMBEREICHE:
        db.execute(
            text("UPDATE users SET ai_desktop_systembereich = :wert WHERE id = :id"),
            {"wert": bereich, "id": regular_user.id},
        )

    with pytest.raises(IntegrityError):
        db.execute(
            text("UPDATE users SET ai_desktop_systembereich = :wert WHERE id = :id"),
            {"wert": "vollzugriff", "id": regular_user.id},
        )
    db.rollback()


def test_die_migration_traegt_den_systembereich(tmp_path: Path) -> None:
    """Modell und Migration duerfen nicht auseinanderlaufen.

    Die Tests oben pruefen das `create_all`-Schema aus den Modellen; ein
    Betreiber faehrt aber die Alembic-Kette. Der Rueckbau bis vor
    ``20260823_01`` und wieder hoch beweist, dass Spalte, Vorgabewert und CHECK
    wirklich aus der Kette stammen.

    Der Kern ist die **Bestandszeile**: ein Konto, das vor dem Update angelegt
    wurde, muss danach ``lesen`` tragen. Eine Migration ohne ``server_default``
    liefe auf SQLite noch durch und liesse dort ``NULL`` stehen — auf
    PostgreSQL scheiterte sie am ``NOT NULL``, und zwar erst beim Betreiber.
    """
    db_url = f"sqlite:///{tmp_path / 'systembereich.db'}"
    vorher = settings.database_url
    settings.database_url = db_url
    backend_dir = Path(__file__).resolve().parent.parent
    config = _alembic(backend_dir)
    engine = create_engine(db_url)
    try:
        Base.metadata.create_all(engine)
        command.stamp(config, "head")

        command.downgrade(config, "20260822_01")
        assert "ai_desktop_systembereich" not in {
            spalte["name"] for spalte in _frisch(engine).get_columns("users")
        }

        # Ein Konto aus der Zeit vor der Einstellung.
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO users (username, password_hash, is_owner, is_active,"
                    " email_verified, two_factor_enabled, email_notifications,"
                    " ai_notifications, created_at)"
                    " VALUES ('bestand', 'x', 0, 1, 1, 0, 1, 1, '2026-08-22')"
                )
            )

        command.upgrade(config, "head")

        inspector = _frisch(engine)
        spalten = {spalte["name"]: spalte for spalte in inspector.get_columns("users")}
        assert spalten["ai_desktop_systembereich"]["nullable"] is False
        assert "lesen" in str(spalten["ai_desktop_systembereich"]["default"])

        pruefungen = {
            pruefung["name"]: str(pruefung.get("sqltext") or "")
            for pruefung in inspector.get_check_constraints("users")
        }
        for bereich in SYSTEMBEREICHE:
            assert f"'{bereich}'" in pruefungen["ck_users_ai_desktop_systembereich"]

        with engine.connect() as conn:
            bestand = conn.execute(
                text(
                    "SELECT ai_desktop_systembereich FROM users"
                    " WHERE username = 'bestand'"
                )
            ).scalar()
        assert bestand == "lesen"
    finally:
        engine.dispose()
        settings.database_url = vorher
