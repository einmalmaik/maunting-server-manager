"""Verschluesselt Klartext-Adressen aus der alten Spalte ``users.email``.

Vor DIS lag die Adresse dort im Klartext; seitdem steht sie verschluesselt in
``email_encrypted``, und ``email`` trug nur noch eine Kopie von
``email_hash``. Migration 20260928_03 entfernt die Spalte. Hat eine Zeile
noch eine Adresse und kein Chiffrat, ginge sie dabei verloren, deshalb
bricht die Migration in diesem Fall ab.

Dieser Schritt laeuft darum **vor** den Migrationen (``main.py``): Alembic
kommt beim Start zuerst, und ohne ihn bliebe ein uralter Stand fuer immer an
der Migration haengen. Reines SQL, weil das Modell die Spalte nicht mehr
kennt. Der Sidecar laeuft zu dem Zeitpunkt schon, ``main.py`` prueft ihn
vorher.

Geschrieben wird die rohe Sidecar-Form ``msm-dis-v1:``, nicht
``msm-email-v1:``: Auf einem Stand vor 20260926_09 setzte jene Migration
sonst ihren Praefix vor den fremden, und 20260928_01 machte daraus ein
doppeltes ``msm-email-v1:``. ``msm-dis-v1:`` kennt jede Migration;
20260928_01 bzw. 20260928_03 stellen es auf ``msm-email-v1:`` um.
"""

from __future__ import annotations

import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection

from models.user import User
from services.dis_client import DisClient

logger = logging.getLogger(__name__)


def klartext_verschluesseln(conn: Connection) -> int:
    """Verschluesselt jede Adresse ohne Chiffrat. Gibt die Zahl der Konten zurueck."""
    pruefer = inspect(conn)
    if "users" not in pruefer.get_table_names():
        return 0
    spalten = {s["name"] for s in pruefer.get_columns("users")}
    if not {"email", "email_encrypted", "email_hash"} <= spalten:
        return 0
    zeilen = conn.execute(
        text("SELECT id, email FROM users WHERE email_encrypted IS NULL AND email LIKE '%@%'")
    ).all()
    for user_id, adresse in zeilen:
        pruefwert = User._email_hash(adresse)
        conn.execute(
            text("UPDATE users SET email_encrypted = :chiffrat, email_hash = :pruefwert, email = :pruefwert WHERE id = :id"),
            {
                "chiffrat": DisClient.encrypt(adresse, aad="msm:user:email"),
                "pruefwert": pruefwert,
                "id": user_id,
            },
        )
    if zeilen:
        logger.info("E-Mail-Altbestand: %d Klartext-Adressen verschluesselt.", len(zeilen))
    return len(zeilen)
