"""Fachliche Domänen des Social-Service.

Jede Datei trägt eine abgeschlossene Domäne als Mixin. Die öffentliche Klasse
``SocialService`` setzt sie in ``services.social_service`` zusammen; dort
liegen auch weiterhin alle Namen, die von außen importiert werden.
"""

from __future__ import annotations

from .direktchat import DirektchatMixin
from .freundschaften import FreundschaftenMixin
from .gruppen import GruppenMixin
from .gruppen_rollen import GruppenRollenMixin
from .mailbox import MailboxMixin
from .mailbox_zugang import MailboxZugangMixin
from .praesenz import PraesenzMixin
from .profile import ProfilMixin
from .stories import StoriesMixin

__all__ = [
    "DirektchatMixin",
    "FreundschaftenMixin",
    "GruppenMixin",
    "GruppenRollenMixin",
    "MailboxMixin",
    "MailboxZugangMixin",
    "PraesenzMixin",
    "ProfilMixin",
    "StoriesMixin",
]
