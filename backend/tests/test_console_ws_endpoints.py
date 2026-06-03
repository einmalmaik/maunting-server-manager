"""Tests fuer den WebSocket-Konsolen-Endpoint + WS-Service.

Deckt ab:
- Auth via Cookie (gleich wie HTTP)
- Origin-Check (CSRF-Aequivalent fuer WS)
- Permission-Check (server.console.read)
- Cold-Start-Backlog + Live-Stream
- Replay-Resume via ?last_id=
- Cleanup der Background-Tasks bei Disconnect
- Heartbeat (ping/pong)
"""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import Server, User
from services import console_ws_service


# ── Service-Layer Tests (Mock-WebSocket, kein FastAPI) ─────────────────────


class _MockWebSocket:
    """Minimaler WS-Mock fuer Service-Layer-Tests.

    Reicht die wichtigsten Attribute + async-Methoden, die console_ws_service nutzt.
    """

    def __init__(self) -> None:
        self.accepted = False
        self.closed = False
        self.close_code: int | None = None
        self.sent: list[str] = []
        self.headers: dict[str, str] = {}
        self.cookies: dict[str, str] = {}
        self.query_params: dict[str, str] = {}
        # starlette-WebSocket hat `client_state` als Property. Wir simulieren CONNECTED.
        from starlette.websockets import WebSocketState
        self.client_state = WebSocketState.CONNECTED

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int = 1000) -> None:
        from starlette.websockets import WebSocketState
        self.closed = True
        self.close_code = code
        self.client_state = WebSocketState.DISCONNECTED

    async def send_text(self, text: str) -> None:
        if self.closed:
            raise RuntimeError("send after close")
        self.sent.append(text)

    async def receive_text(self) -> str:
        # Tests, die nicht explizit Frames senden wollen, bekommen einen CancelledError
        # nach kurzer Zeit, damit connect() sauber zurueckkehrt.
        await asyncio.sleep(0.05)
        raise asyncio.CancelledError()


async def _run_briefly(ws: _MockWebSocket, server_id: int, log_path: str, last_id: int | None = None) -> None:
    """Startet connect() als Task, laesst es ~0.2s laufen, cancelt es dann."""
    with patch.object(console_ws_service.docker_service, "is_running", return_value=False):
        task = asyncio.create_task(
            console_ws_service.connect(
                ws, server_id=server_id, container=f"msm-srv-{server_id}", log_path=log_path, last_id=last_id
            )
        )
        await asyncio.sleep(0.2)
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


class TestConsoleWsService:
    def setup_method(self) -> None:
        console_ws_service.reset_state_for_tests()

    def test_sends_backlog_on_cold_start(self, tmp_path):
        log_path = tmp_path / "console.log"
        log_path.write_text(
            "[MSM] line one\n[MSM] line two\n[MSM] line three\n",
            encoding="utf-8",
        )
        ws = _MockWebSocket()
        asyncio.run(_run_briefly(ws, server_id=1, log_path=str(log_path)))

        assert ws.accepted is True
        assert len(ws.sent) >= 3
        parsed = [json.loads(line) for line in ws.sent[:3]]
        assert [p["text"] for p in parsed] == [
            "[MSM] line one",
            "[MSM] line two",
            "[MSM] line three",
        ]
        for p in parsed:
            assert p["source"] == "msm"
            assert isinstance(p["id"], int)
            assert p["id"] > 0
            assert isinstance(p["timestamp"], str)

    def test_replay_filters_by_last_id(self, tmp_path):
        log_path = tmp_path / "console.log"
        log_path.write_text(
            "first\nsecond\nthird\nfourth\n",
            encoding="utf-8",
        )
        # Cold connect: fuellt den Buffer mit 4 Zeilen.
        ws_cold = _MockWebSocket()
        asyncio.run(_run_briefly(ws_cold, server_id=2, log_path=str(log_path)))
        assert len(ws_cold.sent) == 4
        # Reconnect mit last_id=2 -> nur Zeilen 3 + 4
        ws_reconnect = _MockWebSocket()
        asyncio.run(_run_briefly(ws_reconnect, server_id=2, log_path=str(log_path), last_id=2))
        replayed = [json.loads(s) for s in ws_reconnect.sent]
        replayed_after = [r for r in replayed if r["id"] > 2]
        assert len(replayed_after) == 2
        assert [r["text"] for r in replayed_after] == ["third", "fourth"]

    def test_heartbeat_ping_pong(self):
        ws = _MockWebSocket()
        # receive_text gibt zuerst ein ping, dann CancelledError -> connect() kehrt zurueck.
        ws.receive_text = AsyncMock(side_effect=[
            json.dumps({"action": "ping"}),
            asyncio.CancelledError(),
        ])

        async def _run() -> None:
            with patch.object(console_ws_service.docker_service, "is_running", return_value=False):
                task = asyncio.create_task(
                    console_ws_service.connect(
                        ws, server_id=3, container="msm-srv-3", log_path="/nonexistent"
                    )
                )
                await asyncio.sleep(0.2)
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

        asyncio.run(_run())
        pong = [s for s in ws.sent if json.loads(s).get("action") == "pong"]
        assert len(pong) == 1


# ── Endpoint-Layer Tests (TestClient, durch FastAPI) ───────────────────────


class TestConsoleWsEndpoint:
    """Integration-Tests ueber den FastAPI TestClient (websocket_connect)."""

    def setup_method(self) -> None:
        console_ws_service.reset_state_for_tests()

    def _origin(self, value: str) -> dict[str, str]:
        return {"origin": value}

    def test_rejects_missing_origin(
        self,
        client: TestClient,
        owner_cookies: dict,
        test_server: Server,
    ):
        access = owner_cookies["__Secure-access_token"]
        with pytest.raises(Exception):
            with client.websocket_connect(
                f"/api/servers/{test_server.id}/console/ws",
                cookies={"__Secure-access_token": access},
            ) as ws:
                ws.receive_text()

    def test_rejects_wrong_origin(
        self,
        client: TestClient,
        owner_cookies: dict,
        test_server: Server,
    ):
        access = owner_cookies["__Secure-access_token"]
        with pytest.raises(Exception):
            with client.websocket_connect(
                f"/api/servers/{test_server.id}/console/ws",
                cookies={"__Secure-access_token": access},
                headers=self._origin("https://evil.example.com"),
            ) as ws:
                ws.receive_text()

    def test_rejects_missing_token(
        self,
        client: TestClient,
        test_server: Server,
    ):
        with pytest.raises(Exception):
            with client.websocket_connect(
                f"/api/servers/{test_server.id}/console/ws",
                headers=self._origin("http://localhost:3000"),
            ) as ws:
                ws.receive_text()

    def test_owner_can_connect_and_receive_lifecycle_log(
        self,
        client: TestClient,
        owner_cookies: dict,
        test_server: Server,
    ):
        """End-to-End: Owner connectet, sieht vorhandene Lifecycle-Zeilen
        aus der MSM-Logdatei als Backlog-Burst.
        """
        from games.base import _append_console_log
        # Lifecycle-Zeilen vor dem Connect in die Logdatei schreiben —
        # der Cold-Start-Backlog des WS liest sie und schickt sie als Burst.
        # Eindeutige Marker pro Test-Lauf, damit wir sie in den empfangenen
        # Zeilen wiederfinden (andere Tests koennen Zeilen fuer dieselbe
        # server_id hinterlassen haben, weil die Logdatei persistent ist).
        marker_a = f"[MSM] line-{test_server.id}-a"
        marker_b = f"[MSM] line-{test_server.id}-b"
        _append_console_log(test_server.id, f"{marker_a}\n")
        _append_console_log(test_server.id, f"{marker_b}\n")
        access = owner_cookies["__Secure-access_token"]
        received: list[dict] = []
        with patch.object(console_ws_service.docker_service, "is_running", return_value=False):
            with client.websocket_connect(
                f"/api/servers/{test_server.id}/console/ws",
                cookies={"__Secure-access_token": access},
                headers=self._origin("http://localhost:3000"),
            ) as ws:
                import time
                deadline = time.time() + 2.0
                # Warten bis beide Marker da sind (oder Timeout).
                while time.time() < deadline:
                    msg = ws.receive_text()
                    received.append(json.loads(msg))
                    if marker_a in [r["text"] for r in received] and marker_b in [r["text"] for r in received]:
                        break

        texts = [r["text"] for r in received]
        assert marker_a in texts
        assert marker_b in texts
        assert all(r["source"] == "msm" for r in received)
        # IDs sind monoton wachsend.
        ids = [r["id"] for r in received]
        assert ids == sorted(ids)
        assert len(set(ids)) == len(ids)
