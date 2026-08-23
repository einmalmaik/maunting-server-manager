"""Einziger Ort, an dem eine angemeldete Panel-Sitzung entsteht.

Vorher existierte dieselbe vierzeilige Hilfsfunktion wortgleich in
`routers/auth.py` und `routers/oauth.py`. Mit der Hoster-Anbindung waere eine
dritte Kopie entstanden — und damit drei Stellen, die bei einer Aenderung an
Token-Format, Blacklisting oder Cookie-Attributen auseinanderlaufen koennen.
Deshalb gibt es die Sitzungsausstellung genau einmal.
"""

from __future__ import annotations

import secrets
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
    geraet: str | None = None,
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

    `geraet` sagt, von welcher Art Client die Sitzung stammt — `"desktop"` für
    ein gekoppeltes Gerät, `None` für den Browser. Es geht an zwei Orte: in den
    Token-Claim, damit jede Anfrage es ohne Datenbankfrage kennt, und an die
    Refresh-Zeile, damit es die Rotation überlebt. Es ist der Grund, warum
    „aus dem Panel erreicht nichts den Rechner des Benutzers" eine Schranke ist
    und nicht eine Bitte an den Client: er kann sie nicht mehr selbst erklären
    (`dependencies.session_herkunft`).

    **Die Familie sagt, *welches* Gerät.** `geraet` unterscheidet nur App von
    Browser; ein Benutzer darf aber mehrere Rechner koppeln
    (`device_pairing_service.geraete`, Geräteliste mit einzelnem Widerruf), und
    die Refresh-Familie ist genau der Wert, der einen davon benennt: sie
    entsteht mit der Sitzung, überlebt jede Rotation und steht in
    `device_pairings.family` neben dem Namen, den der Mensch dem Gerät gegeben
    hat. Deshalb wird sie **hier** erzeugt statt in `create_refresh_token` —
    nur so kann derselbe Wert zusätzlich in das Access-Token, wo ihn jede
    Anfrage ohne Datenbankfrage liest (`dependencies.session_familie`). Sie ist
    kein Geheimnis: der Browser bekommt sie ohnehin über `GET /auth/devices`
    und adressiert mit ihr `DELETE /auth/devices/{family}`.
    """
    familie = family or secrets.token_urlsafe(16)
    ansprueche = {
        "sub": user.username,
        "user_id": user.id,
        "jti": str(uuid.uuid4()),
        "familie": familie,
    }
    if geraet:
        ansprueche["geraet"] = geraet
    access_token = AuthService.create_access_token(ansprueche)
    refresh_token = AuthService.create_refresh_token(
        db, user.id, family=familie, geraet=geraet
    )
    csrf_token = AuthService.create_csrf_token()
    _set_auth_cookies(response, access_token, refresh_token, csrf_token)
    return SessionTokens(access_token, refresh_token, csrf_token)
