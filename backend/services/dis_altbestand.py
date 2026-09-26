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
"""

from __future__ import annotations

import logging

from sqlalchemy import Text, select, text, type_coerce
from sqlalchemy.orm import Session

from database import Base
from models.dis_text import DisText
from services.dis_client import DisClient

logger = logging.getLogger(__name__)

STAPEL = 500


def nachziehen(db: Session) -> int:
    """Verschluesselt allen Altbestand. Gibt die Zahl der Werte zurueck."""
    gesamt = 0
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
                gesamt += geschrieben
                if not geschrieben:
                    # Kein einziger Treffer heisst: dieselben Zeilen kaemen
                    # beim naechsten Stapel wieder. Lieber aufhoeren als kreisen.
                    logger.warning("DIS-Altbestand %s.%s: Stapel ohne Treffer, abgebrochen.", tabelle.name, spalte.name)
                    break
    if gesamt:
        logger.info("DIS-Altbestand: %d Werte verschluesselt.", gesamt)
    return gesamt
