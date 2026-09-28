"""Umhuellt Passwort-Hashes aus der Zeit vor DIS mit ``msm-pw-v1:``.

Konten von vor DIS tragen noch ``$argon2id$v=19$...`` von passlib. Neu
hashen geht nur mit dem Klartext, also beim Login, und wer sich nie
anmeldet, behielte das alte Format fuer immer. Darum hasht der Sidecar den
alten Digest selbst mit DIS (``/wrap-legacy-password``): das Passwort bleibt
gueltig, in der Tabelle steht danach nur noch ``msm-pw-v1:``. Beim naechsten
Login wird daraus ein frischer Hash (``AuthService.rehash_password_if_needed``).

Nicht in Alembic, weil der Sidecar waehrend der Migration nicht sicher laeuft
(siehe 20260926_08). Geschrieben wird nur, wenn der Hash noch der gelesene
ist: aendert jemand sein Passwort, waehrend dieser Lauf umhuellt, gewinnt die
Aenderung.

Den Rueckfall auf passlib in ``AuthService.verify_password`` gibt es weiter,
fuer eingespielte alte Backups. Ein Hash, den der Sidecar nicht umhuellen
kann (argon2i, andere Version), bleibt stehen und wird gemeldet.
"""

from __future__ import annotations

import logging

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from models.user import User
from services.dis_client import DisClient, DisKeinAltHash

logger = logging.getLogger(__name__)

STAPEL = 100


def umhuellen(db: Session) -> int:
    """Umhuellt jeden passlib-Hash in ``users``. Gibt die Zahl der Konten zurueck."""
    geschrieben = 0
    uebrig: list[int] = []
    while True:
        abfrage = select(User.id, User.password_hash).where(
            ~User.password_hash.startswith("msm-pw-", autoescape=True)
        )
        if uebrig:
            abfrage = abfrage.where(User.id.not_in(uebrig))
        zeilen = db.execute(abfrage.order_by(User.id).limit(STAPEL)).all()
        if not zeilen:
            break
        for user_id, alt in zeilen:
            try:
                neu = DisClient.wrap_legacy_password(alt)
            except DisKeinAltHash:
                # Kein passlib-Argon2id: bleibt stehen und kommt nicht wieder.
                uebrig.append(user_id)
                continue
            geschrieben += db.execute(
                update(User)
                .where(User.id == user_id, User.password_hash == alt)
                .values(password_hash=neu)
                .execution_options(synchronize_session=False)
            ).rowcount
        db.commit()
    if uebrig:
        logger.warning("Passwort-Altbestand: %d Hashes lassen sich nicht umhuellen.", len(uebrig))
    if geschrieben:
        logger.info("Passwort-Altbestand: %d Hashes mit msm-pw-v1 umhuellt.", geschrieben)
    return geschrieben
