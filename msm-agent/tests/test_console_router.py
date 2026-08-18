from __future__ import annotations

import asyncio
from typing import Any, cast

from routers import console


class _DisconnectingWebSocket:
    def __init__(self) -> None:
        self.accepted = False
        self.sent: list[str] = []

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int = 1000) -> None:
        pass

    async def send_text(self, text: str) -> None:
        self.sent.append(text)

    async def receive(self) -> dict[str, str]:
        return {"type": "websocket.disconnect"}


def test_console_passes_disconnect_signal_to_docker_log_stream(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_stream(_name: str, tail: int = 200, *, stop_event) -> object:
        captured["stop_event"] = stop_event
        if False:
            yield ""

    monkeypatch.setattr(console.docker_service, "assert_msm_container_name", lambda value: value)
    monkeypatch.setattr(console, "console_log_config", lambda _server_id: None)
    monkeypatch.setattr(console.docker_service, "stream_logs_sync", fake_stream)

    websocket = _DisconnectingWebSocket()
    asyncio.run(console.console_ws(cast(Any, websocket), "msm-srv-107"))

    assert websocket.accepted is True
    assert cast(Any, captured["stop_event"]).is_set() is True
