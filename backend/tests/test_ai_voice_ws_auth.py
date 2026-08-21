"""Der Voice-WebSocket und die nativen Clients.

Bis zum 21.08.2026 las `get_current_user_for_ws` ausschliesslich das Cookie —
die gekoppelte Desktop-App hat keins und kam am Sprachmodus schlicht nicht an.
Seitdem darf ein Client sein Access-Token als Subprotokoll anbieten:
``Sec-WebSocket-Protocol: msm.bearer, <token>``.

Die Invarianten, an denen dieser Weg haengt:

1. Ein Header, nie die URL — ein Token im Query-String landet in Zugriffs- und
   Proxy-Logs (CLAUDE.md § 4).
2. Dieselbe Pruefstrecke wie ueberall: `_user_from_token` mit jti-Pflicht und
   Blacklist. Das Subprotokoll ist ein Transportweg, keine zweite Auth.
3. Subprotokoll vor Cookie — Herkunft und Identitaet gehoeren demselben Token.
4. Die Herkunft des Sprachlaufs kommt aus dem `geraet`-Anspruch dieses Tokens;
   ein Browser bleibt "panel", die App wird "desktop".
"""

from starlette.datastructures import Headers

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from dependencies import (
    WS_BEARER_PROTOKOLL,
    get_current_user_for_ws,
    ws_session_herkunft,
    ws_subprotokoll,
)
from models import User
from services.auth_service import AuthService


class _Handshake:
    """Ein WS-Handshake, auf das reduziert, was die Abhaengigkeiten lesen.

    Echte `Headers`-Struktur statt dict: Starlette liest die Namen
    gross-/kleinschreibungsunabhaengig — ein dict taete das nicht, und ein
    Test, der deshalb "panel" misst, pruefte gar nichts (dieselbe Falle wie
    beim HTTP-Stub in test_device_pairing).
    """

    def __init__(self, *, protokolle: str | None = None, cookie: str | None = None):
        self.headers = Headers(
            {"sec-websocket-protocol": protokolle} if protokolle else {}
        )
        self.cookies = {"__Secure-access_token": cookie} if cookie else {}


def _token(user: User, jti: str, geraet: str | None = None) -> str:
    ansprueche: dict = {"sub": user.username, "user_id": user.id, "jti": jti}
    if geraet:
        ansprueche["geraet"] = geraet
    return AuthService.create_access_token(ansprueche)


class TestSubprotokollAuth:
    def test_die_app_kommt_mit_dem_subprotokoll_herein(
        self, db: Session, regular_user: User
    ):
        marke = _token(regular_user, "ws-a", geraet="desktop")
        ws = _Handshake(protokolle=f"{WS_BEARER_PROTOKOLL}, {marke}")
        assert get_current_user_for_ws(ws, db).id == regular_user.id

    def test_der_cookie_weg_bleibt_unveraendert(self, db: Session, regular_user: User):
        ws = _Handshake(cookie=_token(regular_user, "ws-b"))
        assert get_current_user_for_ws(ws, db).id == regular_user.id

    def test_ein_kaputtes_token_im_subprotokoll_ist_keine_anmeldung(
        self, db: Session, regular_user: User
    ):
        ws = _Handshake(protokolle=f"{WS_BEARER_PROTOKOLL}, kaputt")
        with pytest.raises(HTTPException) as fehler:
            get_current_user_for_ws(ws, db)
        assert fehler.value.status_code == 401

    def test_ein_marker_ohne_token_ist_keine_anmeldung(
        self, db: Session, regular_user: User
    ):
        # `msm.bearer` als letzter Eintrag: es gibt kein Token danach, und der
        # Rueckfall aufs (fehlende) Cookie muss greifen — nicht ein Indexfehler.
        ws = _Handshake(protokolle=WS_BEARER_PROTOKOLL)
        with pytest.raises(HTTPException):
            get_current_user_for_ws(ws, db)

    def test_das_subprotokoll_schlaegt_das_cookie(
        self, db: Session, regular_user: User, owner_user: User
    ):
        # Liegt beides an, gilt das angebotene Token — dieselbe Rangfolge wie
        # bei HTTP (`get_current_user`), damit Identitaet und Herkunft nie aus
        # zwei verschiedenen Token stammen.
        marke = _token(regular_user, "ws-c", geraet="desktop")
        ws = _Handshake(
            protokolle=f"{WS_BEARER_PROTOKOLL}, {marke}",
            cookie=_token(owner_user, "ws-d"),
        )
        assert get_current_user_for_ws(ws, db).id == regular_user.id
        assert ws_session_herkunft(ws) == "desktop"


class TestSubprotokollSpiegeln:
    def test_wer_es_anbietet_bekommt_es_gespiegelt(self, regular_user: User):
        marke = _token(regular_user, "ws-e")
        ws = _Handshake(protokolle=f"{WS_BEARER_PROTOKOLL}, {marke}")
        assert ws_subprotokoll(ws) == WS_BEARER_PROTOKOLL

    def test_ein_cookie_client_bekommt_none(self, regular_user: User):
        # `None` ist der Vorgabewert von `accept()` — der Browser-Handshake
        # bleibt exakt, wie er vor dem Bearer-Weg war.
        assert ws_subprotokoll(_Handshake(cookie="egal")) is None
        assert ws_subprotokoll(_Handshake()) is None


class TestHerkunftAmHandshake:
    def test_desktop_token_ergibt_desktop(self, regular_user: User):
        marke = _token(regular_user, "ws-f", geraet="desktop")
        ws = _Handshake(protokolle=f"{WS_BEARER_PROTOKOLL}, {marke}")
        assert ws_session_herkunft(ws) == "desktop"

    def test_browser_cookie_ergibt_panel(self, regular_user: User):
        ws = _Handshake(cookie=_token(regular_user, "ws-g"))
        assert ws_session_herkunft(ws) == "panel"

    def test_alles_unklare_faellt_auf_panel(self):
        assert ws_session_herkunft(_Handshake()) == "panel"
        assert ws_session_herkunft(_Handshake(cookie="kaputt")) == "panel"


class TestBrueckeTraegtDieHerkunft:
    @pytest.mark.asyncio
    async def test_der_sprachlauf_bekommt_die_herkunft_der_sitzung(
        self, monkeypatch, regular_user: User
    ):
        """Von `__init__` bis `lauf_beginnen_nebenher` — die eine Durchreiche.

        Der Lauf wird nicht gestartet: `lauf_beginnen_nebenher` wird abgefangen
        und antwortet mit einer Ablehnung. Geprueft wird nur, was ankommt —
        genau das Feld, das bis zum 21.08. ein Festwert war.
        """
        import httpx

        from services import ai_stream_service, ai_voice_bridge

        angekommen: dict = {}

        async def _abfangen(**kwargs):
            angekommen.update(kwargs)
            return None, ("AI_PROVIDER_UNAVAILABLE", "test")

        monkeypatch.setattr(ai_stream_service, "lauf_beginnen_nebenher", _abfangen)

        from starlette.websockets import WebSocketState

        gesendet: list[str] = []

        class _StummerBrowser:
            client_state = WebSocketState.CONNECTED

            async def send_text(self, daten: str) -> None:
                gesendet.append(daten)

        bruecke = ai_voice_bridge.Sprachbruecke(
            _StummerBrowser(),
            user_id=regular_user.id,
            conversation_id="gespraech-1",
            chat_provider_id=1,
            stimm_kind="elevenlabs",
            stimm_adresse="wss://beispiel.invalid",
            stimm_schluessel="k",
            http_client=httpx.AsyncClient(),
            herkunft="desktop",
        )
        await bruecke._antworten("mach das Licht an")

        assert angekommen["herkunft"] == "desktop"
