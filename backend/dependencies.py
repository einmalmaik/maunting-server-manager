import logging
from typing import Callable

from fastapi import Depends, HTTPException, Request, WebSocket
from sqlalchemy.orm import Session

from database import get_db
from models import User
from services.auth_service import AuthService
from services.jwt_blacklist_service import is_jwt_blacklisted
from services.permission_service import has_global_permission, has_server_permission

_log = logging.getLogger("msm.csrf")


def _user_from_token(token: str | None, db: Session) -> User:
    """Gemeinsame JWT-Validierung fuer HTTP- und WS-Pfade. Wirft HTTPException(401)."""
    if not token:
        raise HTTPException(status_code=401, detail="Nicht authentifiziert")
    payload = AuthService.decode_token(token)
    if not payload or "sub" not in payload or payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Ungültiges Token")
    # Ein Access-Token ohne `jti` ist ein Blindgaenger: der Logout kann es nicht
    # blacklisten, und die Pruefung unten findet nichts, was sie pruefen koennte.
    # Es wird deshalb abgelehnt statt geduldet — nur so ist die Zusage aus
    # services/session_service.py ("jti wird bewusst immer gesetzt") auch
    # durchgesetzt und nicht bloss aufgeschrieben. Fuer Nutzer ist das
    # folgenlos: der Client holt sich nach einem 401 automatisch ueber
    # /api/auth/refresh ein regulaeres Token (frontend/src/api/client.ts).
    jti = payload.get("jti")
    if not jti:
        raise HTTPException(status_code=401, detail="Ungültiges Token")
    if is_jwt_blacklisted(db, jti):
        raise HTTPException(status_code=401, detail="Token widerrufen")
    user = AuthService.get_user_by_username(db, payload["sub"])
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User nicht gefunden oder inaktiv")
    return user


def _bearer_token(request: Request) -> str | None:
    """Extrahiert das Bearer-Token aus dem Authorization-Header, falls vorhanden.

    Der Header kommt von nativen Clients (Smart-System-Desktop-App): Browser
    setzen ihn nie von selbst, und eine fremde Seite kann ihn cross-site nicht
    mitschicken, ohne am CORS-Preflight zu scheitern.
    """
    auth = request.headers.get("authorization", "")
    if auth[:7].lower() == "bearer ":
        token = auth[7:].strip()
        return token or None
    return None


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    # Bearer vor Cookie: Schickt ein nativer Client ein Token mit, ist das
    # seine Identität — auch wenn im selben WebView noch ein Cookie liegt. Nur
    # so gehören die CSRF-Befreiung in `verify_csrf` und die Authentifizierung
    # immer demselben Token. Für das Panel ändert sich nichts: Browser senden
    # den Header nie von selbst. Beide Wege laufen durch `_user_from_token`,
    # damit jti-Pflicht und Blacklist (Logout-Widerruf) auch für Bearer gelten.
    token = _bearer_token(request) or request.cookies.get("__Secure-access_token")
    return _user_from_token(token, db)


#: Das Subprotokoll, unter dem ein nativer Client sein Access-Token in den
#: WS-Handshake legt: ``Sec-WebSocket-Protocol: msm.bearer, <token>``.
#:
#: Ein Header und keine URL — CLAUDE.md § 4 verbietet Tokens in Query-Strings,
#: weil sie in Zugriffs- und Proxy-Logs landen. Einen Authorization-Header kann
#: ein Browser-WebSocket dagegen gar nicht setzen; das Subprotokoll-Feld ist
#: der eine Header, den `new WebSocket(url, protokolle)` erreicht. Wer es
#: anbietet, dem muss der Endpunkt ``msm.bearer`` in `accept(subprotocol=…)`
#: spiegeln (`ws_subprotokoll`), sonst bricht der Client die Verbindung ab.
WS_BEARER_PROTOKOLL = "msm.bearer"


def _ws_bearer_token(ws: WebSocket) -> str | None:
    """Das Access-Token aus dem Subprotokoll-Header, falls angeboten.

    Der Header traegt eine kommagetrennte Liste; das Token ist der Eintrag
    **nach** ``msm.bearer``. Eine fremde Seite kann diesen Header zwar setzen
    (WebSockets kennen kein CORS), aber nicht fuellen: ohne das Token des
    Opfers steht dort nichts, was `_user_from_token` gelten laesst — anders
    als beim Cookie, das der Browser ungefragt mitschickt (dafuer gibt es den
    Origin-Check der Endpunkte).
    """
    roh = ws.headers.get("sec-websocket-protocol", "")
    eintraege = [teil.strip() for teil in roh.split(",") if teil.strip()]
    for stelle, eintrag in enumerate(eintraege):
        if eintrag == WS_BEARER_PROTOKOLL and stelle + 1 < len(eintraege):
            return eintraege[stelle + 1]
    return None


def ws_subprotokoll(ws: WebSocket) -> str | None:
    """Was der Endpunkt in ``accept(subprotocol=…)`` spiegeln muss.

    ``None`` fuer Cookie-Clients (Browser ohne Protokollangebot) — genau der
    Vorgabewert von `accept()`, der Handshake bleibt dort unveraendert.
    """
    return WS_BEARER_PROTOKOLL if _ws_bearer_token(ws) is not None else None


def get_current_user_for_ws(ws: WebSocket, db: Session) -> User:
    """Auth fuer WebSocket-Endpoints. Wirft HTTPException(401) wie der HTTP-Pfad,
    muss im Endpoint aber in einen sauberen WS-Close (1008) umgesetzt werden.

    Zwei Wege, dieselbe Rangfolge wie bei HTTP (`get_current_user`): erst das
    Bearer-Token aus dem Subprotokoll (native Clients, die kein Cookie haben),
    dann das Access-Token-Cookie (Browser senden es beim WS-Upgrade von
    selbst). Keine CSRF-Pruefung noetig, weil WS-Frames keine "simple requests"
    sind und der Origin-Header im Endpoint explizit geprueft wird.
    """
    token = _ws_bearer_token(ws) or ws.cookies.get("__Secure-access_token")
    return _user_from_token(token, db)


def _familie_aus_token(token: str | None) -> str | None:
    """Die Refresh-Familie aus einem Access-Token, oder ``None``.

    Eine Stelle fuer beide Wege (HTTP und WS), weil beide dieselbe Frage
    stellen und eine auseinandergelaufene zweite Fassung genau der Fehler
    waere, der niemandem auffaellt: ein Sprachlauf ohne Kennung sieht aus wie
    einer mit.

    Das Token wird hier **nicht** geprueft — das haben `get_current_user` bzw.
    `get_current_user_for_ws` schon getan, und diese Auskunft laeuft nur neben
    ihnen. Sie entscheidet auch nichts ueber Rechte: die Familie sagt nur, an
    welches Geraet ein Auftrag gehoert.
    """
    if not token:
        return None
    familie = (AuthService.decode_token(token) or {}).get("familie")
    return str(familie) if familie else None


def ws_session_herkunft(ws: WebSocket) -> str:
    """`session_herkunft` fuer WebSocket-Handshakes: ``"panel"`` oder ``"desktop"``.

    Liest denselben Anspruch aus demselben Token wie `get_current_user_for_ws`
    — Subprotokoll vor Cookie, damit Herkunft und Identitaet immer demselben
    Token gehoeren. Alles, was nicht ausdruecklich ``"desktop"`` sagt, ist
    ``"panel"`` — die engere Seite.
    """
    token = _ws_bearer_token(ws) or ws.cookies.get("__Secure-access_token")
    if not token:
        return "panel"
    payload = AuthService.decode_token(token) or {}
    return "desktop" if payload.get("geraet") == "desktop" else "panel"


def ws_session_familie(ws: WebSocket) -> str | None:
    """`session_familie` fuer WebSocket-Handshakes: **welcher** Rechner spricht.

    Dieselbe Rangfolge wie nebenan — Subprotokoll vor Cookie, damit Kennung
    und Identitaet immer demselben Token gehoeren.

    Sie fehlte bis zum 23.08.2026, und damit war die Geraetebindung auf dem
    **Hauptweg** der App wirkungslos: der Sprachmodus ist der Weg, auf dem
    „schau auf meinen Bildschirm" ueberwiegend ankommt, und seine Laeufe
    trugen `familie=None`. Der Auftrag war damit wieder fuer jedes gekoppelte
    Geraet abholbar — obwohl feststeht, in welches Mikrofon gesprochen wurde.
    """
    return _familie_aus_token(
        _ws_bearer_token(ws) or ws.cookies.get("__Secure-access_token")
    )


def session_herkunft(request: Request) -> str:
    """Von welcher Art Client die Anfrage kommt: ``"panel"`` oder ``"desktop"``.

    Steht im Token, nicht im Request-Koerper. Bis zum 21.08.2026 schickte der
    Client das Feld selbst mit — damit war „aus dem Panel erreicht nichts den
    Rechner des Benutzers" eine Absichtserklaerung, an die sich nur hielt, wer
    wollte. Ein uebernommener Browser-Tab mit gueltiger Sitzung haette sich als
    App ausgeben und Maus und Tastatur verlangen koennen.

    Der Anspruch kommt aus derselben Ausstellung wie die Identitaet
    (`services/session_service.issue_session`) und ueberlebt die Rotation ueber
    `refresh_tokens.geraet`. Hier wird das Token **nicht** noch einmal geprueft:
    das hat `get_current_user` bereits getan, und diese Abhaengigkeit laeuft nur
    neben ihr. Sie entscheidet auch nichts ueber Rechte, nur ueber die
    Werkzeugmenge (`ai_tool_registry.herkunft_schnitt`).

    Alles, was nicht ausdruecklich ``"desktop"`` sagt, ist ``"panel"`` — die
    engere Seite.
    """
    token = _bearer_token(request) or request.cookies.get("__Secure-access_token")
    if not token:
        return "panel"
    payload = AuthService.decode_token(token) or {}
    return "desktop" if payload.get("geraet") == "desktop" else "panel"


def session_familie(request: Request) -> str | None:
    """**Welches** Geraet fragt: die Refresh-Familie dieser Sitzung.

    `session_herkunft` beantwortet "App oder Browser", diese hier "welcher
    Rechner". Beides ist noetig, seit ein Benutzer mehrere Geraete koppeln darf
    (`device_pairing_service.geraete`): ohne die Familie bekommt einen Auftrag
    fuer den Bildschirm der Rechner, der zuerst fragt, und nicht der, an dem
    der Mensch sitzt.

    Gelesen wie die Herkunft — Bearer vor Cookie, aus demselben Token wie die
    Identitaet.

    ``None`` heisst "unbekannt" und niemals "irgendeins": ein Access-Token aus
    der Zeit vor diesem Anspruch traegt sie nicht, und der Aufrufer muss diesen
    Fall ausdruecklich behandeln (`desktop_job_service.naechster`).
    """
    return _familie_aus_token(
        _bearer_token(request) or request.cookies.get("__Secure-access_token")
    )


def get_current_owner(user: User = Depends(get_current_user)) -> User:
    if not user.is_owner:
        raise HTTPException(status_code=403, detail="Nur Owner erlaubt")
    return user


def _all_cookie_values(request: Request, name: str) -> list[str]:
    """Liefert alle Werte unter `name` aus dem Cookie-Header.

    Starlette gibt ueber `request.cookies` nur einen Wert pro Name zurueck. Wenn
    ein Browser nach einer Pfad-Migration noch ein zweites Cookie mit demselben
    Namen unter einem anderen Pfad mitschickt (z. B. Path=/api aus einem
    frueheren Release zusaetzlich zu Path=/), geht der jeweils andere Wert
    verloren. Fuer die CSRF-Pruefung wollen wir alle Werte sehen.
    """
    raw = request.headers.get("cookie", "")
    values: list[str] = []
    for chunk in raw.split(";"):
        if "=" not in chunk:
            continue
        key, val = chunk.split("=", 1)
        if key.strip() == name:
            values.append(val.strip())
    return values


def verify_csrf(request: Request) -> None:
    """Double-Submit-Cookie CSRF-Schutz. Nur fuer state-changing Requests.

    Akzeptiert den Header-Wert, wenn er zu einem der vom Browser gesendeten
    CSRF-Cookies passt. Das ist noetig, weil nach einer Cookie-Pfad-Migration
    Browser zeitweise zwei Cookies mit demselben Namen unter verschiedenen
    Pfaden halten koennen — und Angreifer in beiden Faellen den Header-Wert
    nicht raten koennen (cross-origin kein Cookie-Zugriff).

    Loggt ohne Token-Werte, welche Komponente fehlt — sonst sieht im Log nur
    "403" und man weiss nicht, ob der Header oder das Cookie fehlt.

    Native Clients (Desktop-App) sind befreit: CSRF lebt davon, dass Browser
    Cookies ungefragt mitschicken — einen Authorization-Header kann eine
    fremde Seite dagegen nicht setzen, ohne am CORS-Preflight zu scheitern.
    Befreit wird aber nur, wer ein **gültig signiertes** Access-Token vorlegt
    (Signatur, Typ, jti); ein bloß vorhandener Header befreit nicht, sonst
    liefe ein Cookie-Request mit kaputtem Header ungeprüft durch. Ablauf und
    Blacklist prüft `get_current_user` ohnehin — und weil der Bearer dort vor
    dem Cookie gelesen wird, gehört die Befreiung immer demselben Token wie
    die Authentifizierung.
    """
    bearer = _bearer_token(request)
    if bearer:
        payload = AuthService.decode_token(bearer)
        if payload and payload.get("type") == "access" and payload.get("jti"):
            return
    csrf_header = request.headers.get("x-csrf-token")
    cookie_values = _all_cookie_values(request, "__Secure-csrf_token")
    path = request.url.path
    if not csrf_header and not cookie_values:
        _log.warning("CSRF check failed on %s: header and cookie both missing", path)
        raise HTTPException(status_code=403, detail="CSRF-Header und -Cookie fehlen")
    if not csrf_header:
        _log.warning("CSRF check failed on %s: X-CSRF-Token header missing (cookie present)", path)
        raise HTTPException(status_code=403, detail="CSRF-Header fehlt")
    if not cookie_values:
        _log.warning("CSRF check failed on %s: __Secure-csrf_token cookie missing (header present)", path)
        raise HTTPException(status_code=403, detail="CSRF-Cookie fehlt")
    if csrf_header not in cookie_values:
        _log.warning(
            "CSRF check failed on %s: header does not match any of %d cookie value(s)",
            path, len(cookie_values),
        )
        raise HTTPException(status_code=403, detail="CSRF-Token ungültig")


def require_server_permission(user: User, server_id: int, db: Session, key: str) -> None:
    """Prueft eine server-scoped Permission.

    Owner werden ungeprueft durchgelassen (Bootstrap-Override). Sonst entscheidet
    `services.permission_service.has_server_permission`: globale Rolle gewaehrt
    pauschal oder per-Server-Delegation gewaehrt einzeln.

    Wer den Server nicht einmal sehen darf, bekommt 404 statt 403: die
    Rechtepruefung laeuft an vielen Endpunkten vor dem Existenz-Lookup, und
    ein 403 nur fuer existierende Server waere ein Existenzorakel — per
    ID-Iteration liesse sich sonst z.B. bestimmen, welche IDs Hoster-
    Kundenserver sind (dieselbe Regel wie in `teams.py::set_server_grants`
    und `ai_action_service._resolve_server`). Wer sehen darf, aber das
    konkrete Recht nicht haelt, bekommt weiterhin 403.
    """
    if has_server_permission(db, user, server_id, key):
        return
    if key != "server.view" and has_server_permission(db, user, server_id, "server.view"):
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    raise HTTPException(status_code=404, detail="Server nicht gefunden")


def require_global(key: str) -> Callable[..., User]:
    """Dependency-Factory: erzwingt eine globale Permission.

    Beispiel: `user: User = Depends(require_global("servers.create"))`.
    """

    def _dep(
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> User:
        if not has_global_permission(db, user, key):
            raise HTTPException(status_code=403, detail="Keine Berechtigung")
        return user

    return _dep
