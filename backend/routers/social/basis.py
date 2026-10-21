"""Gemeinsame Abhängigkeiten der Social-Router."""

from __future__ import annotations

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from database import get_db
from services.social_service import SocialService


def _check_social_enabled(db: Session = Depends(get_db)) -> None:
    """Betreiber-Toggle vor jedem Social-Endpunkt."""
    SocialService.assert_social_enabled(db)


def mailbox_token(
    x_mailbox_token: str | None = Header(default=None, alias="X-Mailbox-Token"),
) -> str | None:
    """Der Besitznachweis, den der Client zu einer Mailbox mitschickt.

    Als Kopfzeile, nicht als Abfrageparameter: was in der Adresse steht, steht
    im Zugriffsprotokoll des Webservers und in jedem Zwischenspeicher davor.
    Ein Nachweis, der dort landet, ist keiner mehr.

    Fehlt er, ist das für sich kein Fehler — für Mailboxen ohne hinterlegten
    Nachweis ändert sich nichts. Erst `assert_mailbox_token` entscheidet, ob
    an dieser Stelle einer nötig gewesen wäre.
    """
    return x_mailbox_token
