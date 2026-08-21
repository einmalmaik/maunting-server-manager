"""Einziger Ort, an dem eine angemeldete Panel-Sitzung entsteht.

Vorher existierte dieselbe vierzeilige Hilfsfunktion wortgleich in
`routers/auth.py` und `routers/oauth.py`. Mit der Hoster-Anbindung waere eine
dritte Kopie entstanden — und damit drei Stellen, die bei einer Aenderung an
Token-Format, Blacklisting oder Cookie-Attributen auseinanderlaufen koennen.
Deshalb gibt es die Sitzungsausstellung genau einmal.
"""

from __future__ import annotations

import uuid
from typing import NamedTuple

from fastapi import Response
from sqlalchemy.orm import Session

from cookies import _set_auth_cookies
from models import User
from services.auth_service import AuthService


class SessionTokens(NamedTuple):
    """Die drei Token einer Sitzung — für native Clients auch im Body.

    Die Werte sind Geheimnisse: sie gehören in Cookies oder in den
    Response-Body eines authentifizierten Aufrufers, niemals in Logs,
    Audit-Einträge oder Fehlermeldungen.
    """

    access_token: str
    refresh_token: str
    csrf_token: str


def issue_session(
    response: Response,
    db: Session,
    user: User,
    family: str | None = None,
) -> SessionTokens:
    """Stellt Access-, Refresh- und CSRF-Token aus und setzt die Auth-Cookies.

    Die `jti` wird bewusst immer gesetzt: nur damit kann ein Access-Token beim
    Logout widerrufen werden.

    `family` gibt es, weil auch die Rotation in `/api/auth/refresh` eine
    vollwertige Sitzung ausstellt und deshalb hierher gehoert. Ohne diesen
    Parameter musste `refresh` die Token selbst bauen — und tat das ohne `jti`,
    womit die Zusage im Absatz darueber nach dem ersten Refresh still nicht mehr
    galt. Wird `family` uebergeben, bleibt das neue Refresh-Token in derselben
    Familie; nur so greift die Wiederverwendungserkennung ueber die ganze Kette
    statt bei jeder Rotation neu anzufangen.

    Die Token werden **zusätzlich zurückgegeben**, seit native Clients
    (Smart-System-Desktop-App) sie im Response-Body brauchen: WebViews außerhalb
    des Panel-Origins können httponly-Cookies nicht verwalten. Der Rückgabewert
    ändert nichts am Cookie-Weg — Browser-Aufrufer ignorieren ihn einfach. Ein
    zweiter Ausstellungspfad nur für native Clients wäre der Fehler, gegen den
    dieses Modul existiert (siehe Modul-Docstring und die jti-Geschichte oben).
    """
    access_token = AuthService.create_access_token(
        {"sub": user.username, "user_id": user.id, "jti": str(uuid.uuid4())}
    )
    refresh_token = AuthService.create_refresh_token(db, user.id, family=family)
    csrf_token = AuthService.create_csrf_token()
    _set_auth_cookies(response, access_token, refresh_token, csrf_token)
    return SessionTokens(access_token, refresh_token, csrf_token)
