"""Einziger Ort, an dem eine angemeldete Panel-Sitzung entsteht.

Vorher existierte dieselbe vierzeilige Hilfsfunktion wortgleich in
`routers/auth.py` und `routers/oauth.py`. Mit der Hoster-Anbindung waere eine
dritte Kopie entstanden — und damit drei Stellen, die bei einer Aenderung an
Token-Format, Blacklisting oder Cookie-Attributen auseinanderlaufen koennen.
Deshalb gibt es die Sitzungsausstellung genau einmal.
"""

from __future__ import annotations

import uuid

from fastapi import Response
from sqlalchemy.orm import Session

from cookies import _set_auth_cookies
from models import User
from services.auth_service import AuthService


def issue_session(response: Response, db: Session, user: User) -> None:
    """Stellt Access-, Refresh- und CSRF-Token aus und setzt die Auth-Cookies.

    Die `jti` wird bewusst immer gesetzt: nur damit kann ein Access-Token beim
    Logout widerrufen werden.
    """
    access_token = AuthService.create_access_token(
        {"sub": user.username, "user_id": user.id, "jti": str(uuid.uuid4())}
    )
    refresh_token = AuthService.create_refresh_token(db, user.id)
    csrf_token = AuthService.create_csrf_token()
    _set_auth_cookies(response, access_token, refresh_token, csrf_token)
