"""Die Karte „Mailbox -> Gegenüber“ wird je Sitzung gemerkt (seit 27.09.2026).

Ein gesperrtes Konto darf danach nicht mehr als Gegenüber gelten, auch wenn
die Sperre am Flush vorbei als Massenänderung lief oder aus einer anderen
Sitzung kam und diese ihre Transaktion beendet hat.
"""

from __future__ import annotations

from sqlalchemy import update
from sqlalchemy.orm import Session

import database as db_module
from models import User
from services.social_service import SocialService


def _gegenueber(db: Session, owner_user: User, regular_user: User) -> int | None:
    kennung = SocialService.derive_blind_mailbox_id(owner_user.id, regular_user.id)
    return SocialService.gegenueber_aus_mailbox(db, owner_user.id, kennung)


def test_sperre_als_massenaenderung_wirkt_sofort(db: Session, owner_user: User, regular_user: User) -> None:
    assert _gegenueber(db, owner_user, regular_user) == regular_user.id

    db.execute(update(User).where(User.id == regular_user.id).values(is_active=False))

    assert _gegenueber(db, owner_user, regular_user) is None


def test_sperre_aus_anderer_sitzung_wirkt_nach_dem_commit(
    db: Session, owner_user: User, regular_user: User
) -> None:
    assert _gegenueber(db, owner_user, regular_user) == regular_user.id
    with db_module.SessionLocal() as andere:
        andere.execute(update(User).where(User.id == regular_user.id).values(is_active=False))
        andere.commit()

    db.commit()

    assert _gegenueber(db, owner_user, regular_user) is None
