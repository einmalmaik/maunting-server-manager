"""Verschluesselt Klartext, der noch aus der Zeit vor ``DisText`` stammt.

``DisText`` liest Altbestand unveraendert, schreibt ihn aber erst bei der
naechsten Aenderung verschluesselt. Chatnachrichten aendert niemand mehr,
ohne diesen Lauf blieben sie fuer immer lesbar. Er startet mit dem Panel
im Hintergrund und findet die Spalten selbst: jede Spalte vom Typ
``DisText`` gehoert dazu, eine neue muss hier nicht eingetragen werden.

Geschrieben wird nur, wenn der Wert noch der gelesene ist (``AND spalte =
:alt``). Aendert das Panel eine Zeile, waehrend dieser Lauf sie
verschluesselt, gewinnt die Aenderung. Sie ist dann schon verschluesselt,
und der Lauf findet die Zeile beim naechsten Stapel nicht mehr.

**Warum danach noch VACUUM FULL.** Ein UPDATE ueberschreibt in PostgreSQL
nichts: die alte Zeilenversion samt Klartext bleibt in der Datei, bis ein
VACUUM ihren Platz freigibt, und selbst dann liegt sie noch auf der Platte,
bis etwas Neues darueber geschrieben wird. Dasselbe gilt fuer geloeschte
Mails und fuer Spalten, die eine Migration entfernt hat (`embedding_json`,
der Klartextname im Gedaechtnis). Erst VACUUM FULL schreibt die Tabelle neu,
ohne diese Reste. `beim_start` tut das einmal nach dem Update fuer alle
betroffenen Tabellen, danach nur noch, wenn der Nachzug wieder Klartext
gefunden hat (etwa nach dem Einspielen eines alten Backups).
"""

from __future__ import annotations

import logging

from sqlalchemy import Text, select, text, type_coerce
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from database import Base
from models.dis_text import DisText
from services.dis_client import DisClient

logger = logging.getLogger(__name__)

STAPEL = 500

#: Merkt sich, dass die erste Bereinigung nach dem Update durch ist. Ein neuer
#: Wert erzwingt sie noch einmal, etwa wenn eine spaetere Migration wieder
#: Klartext entfernt.
MARKE = "dis.klartext_bereinigt"
MARKE_WERT = "20260926"

#: Ohne Frist wartete VACUUM FULL auf jede laufende Anfrage, und alles, was
#: danach kommt, wartete auf VACUUM FULL. So scheitert es nach 10 s und wird
#: beim naechsten Start wiederholt.
SPERRFRIST = "10s"


def nachziehen(db: Session) -> dict[str, int]:
    """Verschluesselt allen Altbestand. Gibt je Tabelle die Zahl der Werte zurueck."""
    je_tabelle: dict[str, int] = {}
    for tabelle in Base.metadata.sorted_tables:
        for spalte in tabelle.columns:
            if not isinstance(spalte.type, DisText):
                continue
            (schluessel,) = tabelle.primary_key.columns
            # Ohne type_coerce entschluesselte DisText den Wert schon beim Lesen.
            roh = type_coerce(spalte, Text)
            aendern = text(
                f"UPDATE {tabelle.name} SET {spalte.name} = :neu "
                f"WHERE {schluessel.name} = :id AND {spalte.name} = :alt"
            )
            while True:
                zeilen = db.execute(
                    select(schluessel, roh)
                    .where(roh.is_not(None), ~roh.startswith(DisClient.PRAEFIX, autoescape=True))
                    .limit(STAPEL)
                ).all()
                if not zeilen:
                    break
                geschrieben = 0
                for zeilen_id, alt in zeilen:
                    neu = DisClient.encrypt(alt, aad=spalte.type.aad)
                    geschrieben += db.execute(aendern, {"neu": neu, "id": zeilen_id, "alt": alt}).rowcount
                db.commit()
                if geschrieben:
                    je_tabelle[tabelle.name] = je_tabelle.get(tabelle.name, 0) + geschrieben
                if not geschrieben:
                    # Kein einziger Treffer heisst: dieselben Zeilen kaemen
                    # beim naechsten Stapel wieder. Lieber aufhoeren als kreisen.
                    logger.warning("DIS-Altbestand %s.%s: Stapel ohne Treffer, abgebrochen.", tabelle.name, spalte.name)
                    break
    if je_tabelle:
        logger.info("DIS-Altbestand: %d Werte verschluesselt.", sum(je_tabelle.values()))
    return je_tabelle


def dis_tabellen() -> set[str]:
    """Jede Tabelle mit einer DisText-Spalte, dazu das Gedaechtnis."""
    tabellen = {
        tabelle.name for tabelle in Base.metadata.sorted_tables
        if any(isinstance(spalte.type, DisText) for spalte in tabelle.columns)
    }
    # Keine DisText-Spalte, aber derselbe Befund: Name und Vektoren lagen
    # dort im Klartext, bis 20260926_08 und `schluessel_nachziehen` sie
    # ersetzt haben.
    return tabellen | {"ai_memory_entries"}


def klartextreste_entfernen(engine: Engine, tabellen: set[str]) -> bool:
    """VACUUM FULL auf ``tabellen``. Nur PostgreSQL; gibt zurueck, ob alles lief."""
    if engine.dialect.name != "postgresql" or not tabellen:
        return True
    alles = True
    # VACUUM laeuft nicht in einer Transaktion, daher AUTOCOMMIT.
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.exec_driver_sql(f"SET lock_timeout = '{SPERRFRIST}'")
        for name in sorted(tabellen):
            try:
                conn.exec_driver_sql(f'VACUUM FULL "{name}"')
            except Exception as exc:  # noqa: BLE001 - ein Fehlschlag haelt die anderen nicht auf
                alles = False
                logger.warning("VACUUM FULL %s nicht moeglich: %s", name, type(exc).__name__)
    return alles


def beim_start(db: Session) -> None:
    """Nachzug, Gedaechtnisnamen und, wo noetig, VACUUM FULL. Laeuft beim Start."""
    from services.ai_memory_service import schluessel_nachziehen
    from services.panel_settings_service import PanelSettingsService

    betroffen = set(nachziehen(db))
    if schluessel_nachziehen(db):
        betroffen.add("ai_memory_entries")
    erstes_mal = PanelSettingsService.get(MARKE, "", db) != MARKE_WERT
    if erstes_mal:
        betroffen |= dis_tabellen()
    # Keine offene Transaktion dieser Sitzung: VACUUM FULL wartete sonst auf
    # die eigene Lesesperre, bis die Frist ablaeuft.
    db.commit()
    if klartextreste_entfernen(db.get_bind(), betroffen) and erstes_mal:
        PanelSettingsService.set(MARKE, MARKE_WERT, db)
        db.commit()
