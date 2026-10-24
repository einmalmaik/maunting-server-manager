"""Öffentlicher Einstieg in die Social-Domänen.

Die Umsetzung liegt in ``services/social/``; dieses Modul hält die gewohnten
Namen und die Sitzungs-Hooks. Bestehende Importe, Tests und Aufrufer bleiben
damit unverändert.
"""

from __future__ import annotations

import logging

from sqlalchemy import event
from sqlalchemy.orm import Session

from models import User
from services.social import (
    DirektchatMixin,
    FreundschaftenMixin,
    GruppenMixin,
    GruppenRollenMixin,
    MailboxMixin,
    MailboxZugangMixin,
    PraesenzMixin,
    ProfilMixin,
    StoriesMixin,
)
from services.social.basis import _GEGENUEBER_ABLAGE
from services.social.grenzen import (
    E2EE_ENVELOPE_RETENTION_DAYS,
    E2EE_RELAY_KIB_PRO_TAG,
    GROUP_CALL_PERMISSIONS,
    GROUP_MODERATION_PERMISSIONS,
    GROUP_PERMISSION_ALIASES,
    GROUP_PERMISSIONS,
    GROUP_ROLE_ONLY_PERMISSIONS,
    MAX_AKTIVE_STORIES,
)

logger = logging.getLogger(__name__)

__all__ = [
    "E2EE_ENVELOPE_RETENTION_DAYS",
    "E2EE_RELAY_KIB_PRO_TAG",
    "GROUP_CALL_PERMISSIONS",
    "GROUP_MODERATION_PERMISSIONS",
    "GROUP_PERMISSION_ALIASES",
    "GROUP_PERMISSIONS",
    "GROUP_ROLE_ONLY_PERMISSIONS",
    "MAX_AKTIVE_STORIES",
    "SocialService",
]


@event.listens_for(Session, "after_flush")
def _gegenueber_vergessen(session: Session, _kontext) -> None:
    """Ein neues oder geändertes Konto in dieser Sitzung macht die Karte ungültig."""
    if _GEGENUEBER_ABLAGE not in session.info:
        return
    if any(isinstance(o, User) for o in (*session.new, *session.dirty, *session.deleted)):
        session.info.pop(_GEGENUEBER_ABLAGE, None)


@event.listens_for(Session, "after_commit")
@event.listens_for(Session, "after_soft_rollback")
def _gegenueber_nach_transaktion_vergessen(session: Session, *_args) -> None:
    session.info.pop(_GEGENUEBER_ABLAGE, None)


@event.listens_for(Session, "do_orm_execute")
def _gegenueber_nach_massenaenderung_vergessen(zustand) -> None:
    # `update(User)` (etwa ein Konto sperren) laeuft am Flush vorbei.
    if not zustand.is_select:
        zustand.session.info.pop(_GEGENUEBER_ABLAGE, None)


class SocialService(
    FreundschaftenMixin,
    PraesenzMixin,
    ProfilMixin,
    DirektchatMixin,
    MailboxMixin,
    MailboxZugangMixin,
    GruppenRollenMixin,
    GruppenMixin,
    StoriesMixin,
):
    """Freundschaften, Präsenz, Profile, Direktchat, Mailbox, Gruppen, Rollen und Stories."""
