"""Tests fuer Live-Konsole: SSE-Stream + stdin-Eingabe.

Decken die Sicherheitsinvarianten und das KISS-Stream-Design ab:
- Stream-Endpoint verlangt ``server.console.read``.
- Stream liefert zuerst den MSM-Logdatei-Backlog (Install/Lifecycle),
  dann live Rootless-Docker-Logs zusammen mit neuen Datei-Eintraegen.
- Input-Endpoint verlangt ``server.console.write``.
- Input-POST loggt den Inhalt NICHT (z. B. OAuth-Code).
- ``send_stdin`` nutzt den Docker-SDK-Exec-Pfad mit dem korrekten Aufruf.
- ``run_container`` startet den Container mit ``--interactive`` (sonst geht
  stdin nicht).
"""

import os
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import Server, User


class TestSendStdin:
    """``docker_service.send_stdin`` schreibt in den Container-stdin."""

    def test_invokes_docker_exec_i_with_pipe_to_pid1(self):
        from services import docker_service

        raw_socket = MagicMock()
        raw_socket.recv.return_value = b""
        exec_socket = type("ExecSocket", (), {"_sock": raw_socket})()
        container = type("Container", (), {"id": "cid", "status": "running"})()
        client = MagicMock()
        client.api.exec_create.return_value = {"Id": "exec-id"}
        client.api.exec_start.return_value = exec_socket
        client.api.exec_inspect.return_value = {"ExitCode": 0}

        with patch("services.docker_service._container", return_value=container), \
             patch("services.docker_service._client_or_error", return_value=(client, None)):
            result = docker_service.send_stdin("msm-srv-1", "/auth login device\n")

        assert result["ok"] is True
        args, kwargs = client.api.exec_create.call_args
        cmd = args[1]
        # Der eigentliche Schreibvorgang muss in PID-1-stdin gehen.
        assert any("/proc/1/fd/0" in part for part in cmd)
        # Inhalt MUSS via stdin uebergeben werden — niemals als argv-Element.
        # Das ist die Security-Invariante: kein Leak in Process-Listings.
        raw_socket.sendall.assert_called_once_with(b"/auth login device\n")
        assert "/auth login device\n" not in cmd

    def test_refuses_when_container_not_running(self):
        from services import docker_service

        with patch("services.docker_service._container", return_value=None):
            result = docker_service.send_stdin("msm-srv-1", "anything\n")

        assert result["ok"] is False


class TestRunContainerKeepsStdinOpen:
    """``run_container`` muss ``--interactive`` setzen, sonst gehen Konsolen-
    Eingaben (Hytale-OAuth, EULA-Bestaetigung, RCON) lautlos verloren."""

    def test_run_container_includes_interactive_flag(self):
        from services import docker_service

        client = MagicMock()
        client.containers.get.side_effect = docker_service.NotFound("missing")
        client.containers.run.return_value = type("Container", (), {"id": "abc"})()

        with patch("services.docker_service._client_or_error", return_value=(client, None)):
            docker_service.run_container(
                name="msm-srv-1",
                image="alpine:3.20",
            )

        kwargs = client.containers.run.call_args.kwargs
        assert kwargs["detach"] is True
        assert kwargs["stdin_open"] is True


class TestConsoleInputEndpoint:
    """POST /api/servers/{id}/console/input. RBAC + Security-Invarianten."""

    def test_owner_can_send_input(
        self,
        client: TestClient,
        owner_cookies: dict,
        csrf_token: str,
        test_server: Server,
    ):
        with patch("routers.servers.docker_service.is_running", return_value=True), \
             patch("routers.servers.docker_service.send_stdin") as mock_send:
            mock_send.return_value = {"ok": True, "stdout": "", "stderr": ""}
            response = client.post(
                f"/api/servers/{test_server.id}/console/input",
                json={"line": "/say hello"},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )

        assert response.status_code == 200
        assert response.json() == {"ok": True}
        # ``send_stdin`` bekommt die Zeile mit Newline (zeilenbasierte Games).
        mock_send.assert_called_once()
        args, _ = mock_send.call_args
        assert args[1].endswith("\n")
        assert "/say hello" in args[1]

    def test_rejects_user_without_console_write(
        self,
        client: TestClient,
        user_cookies: dict,
        user_csrf_token: str,
        test_server: Server,
    ):
        # KEIN ``user_permission``-Fixture — der User hat keine Server-Rechte.
        with patch("routers.servers.docker_service.send_stdin") as mock_send:
            response = client.post(
                f"/api/servers/{test_server.id}/console/input",
                json={"line": "rm -rf /"},
                cookies=user_cookies,
                headers={"X-CSRF-Token": user_csrf_token},
            )

        assert response.status_code == 403
        # Wichtig: send_stdin DARF nicht aufgerufen worden sein.
        mock_send.assert_not_called()

    def test_rejects_when_container_not_running(
        self,
        client: TestClient,
        owner_cookies: dict,
        csrf_token: str,
        test_server: Server,
    ):
        with patch("routers.servers.docker_service.is_running", return_value=False), \
             patch("routers.servers.docker_service.send_stdin") as mock_send:
            response = client.post(
                f"/api/servers/{test_server.id}/console/input",
                json={"line": "anything"},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )

        assert response.status_code == 409
        mock_send.assert_not_called()

    def test_rejects_oversized_input(
        self,
        client: TestClient,
        owner_cookies: dict,
        csrf_token: str,
        test_server: Server,
    ):
        big = "x" * 2048  # > 1 KiB Limit
        response = client.post(
            f"/api/servers/{test_server.id}/console/input",
            json={"line": big},
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
        )
        assert response.status_code == 422

    def test_input_value_is_never_logged(
        self,
        client: TestClient,
        owner_cookies: dict,
        csrf_token: str,
        test_server: Server,
        caplog,
    ):
        """Security: Konsole-Input (potenziell OAuth-Code, RCON-Token) darf
        NICHT im Server-Log auftauchen.
        """
        secret = "OAUTH_DEVICE_CODE_ABC123_KEEP_THIS_OUT_OF_LOGS"
        import logging
        with caplog.at_level(logging.DEBUG), \
             patch("routers.servers.docker_service.is_running", return_value=True), \
             patch("routers.servers.docker_service.send_stdin") as mock_send:
            mock_send.return_value = {"ok": True, "stdout": "", "stderr": ""}
            response = client.post(
                f"/api/servers/{test_server.id}/console/input",
                json={"line": secret},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )

        assert response.status_code == 200
        # Keine einzelne Log-Zeile darf den Geheim-Wert enthalten.
        for record in caplog.records:
            assert secret not in record.getMessage()


class TestConsoleStreamRBAC:
    """SSE-Stream-Endpoint: nur mit ``server.console.read`` erreichbar."""

    def test_rejects_user_without_console_read(
        self,
        client: TestClient,
        user_cookies: dict,
        test_server: Server,
    ):
        # KEIN ``user_permission``-Fixture — keine Server-Rechte.
        response = client.get(
            f"/api/servers/{test_server.id}/console/stream",
            cookies=user_cookies,
        )
        assert response.status_code == 403


class _StubRequest:
    """Minimaler Request-Stub: kontrolliert, wann der Stream beendet wird."""

    def __init__(self, disconnect_after: int = 1) -> None:
        self._calls = 0
        self._disconnect_after = disconnect_after

    async def is_disconnected(self) -> bool:
        self._calls += 1
        return self._calls > self._disconnect_after


async def _drain_stream(gen, max_frames: int = 50) -> list[str]:
    payloads: list[str] = []
    async for chunk in gen:
        for line in chunk.split("\n"):
            if line.startswith("data: "):
                payloads.append(line[len("data: "):])
        if len(payloads) >= max_frames:
            break
    return payloads


class TestConsoleStreamGenerator:
    """Direkter Test des Stream-Generators — unabhängig von HTTP-Transport.

    KISS-Invariante: die MSM-Console-Logdatei ist die single source of truth.
    Backlog (Install-Output, Lifecycle-Events) wird sofort bei Verbindungs-
    aufbau geliefert, damit die Konsole während Install **und** Betrieb
    nie leer ist.
    """

    def test_replays_existing_msm_log_backlog(self, test_server: Server):
        import asyncio

        from games.base import _console_log_path
        from routers.servers import _console_event_stream

        log_path = _console_log_path(test_server.id)
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        try:
            with open(log_path, "w", encoding="utf-8") as f:
                f.write("[MSM] SteamCMD startet für App 12345 (Docker)\n")
                f.write("Success! App '12345' fully installed.\n")
                f.write("[MSM] Container msm-srv-1 gestartet\n")

            with patch("routers.servers.shutil.which", return_value=None):
                payloads = asyncio.run(
                    _drain_stream(
                        _console_event_stream(
                            _StubRequest(disconnect_after=0),
                            container="msm-srv-x",
                            log_path=log_path,
                        ),
                        max_frames=10,
                    )
                )

            assert "[MSM] SteamCMD startet für App 12345 (Docker)" in payloads
            assert "Success! App '12345' fully installed." in payloads
            assert "[MSM] Container msm-srv-1 gestartet" in payloads
        finally:
            if os.path.exists(log_path):
                os.remove(log_path)

    def test_reports_missing_docker_as_visible_data_line(self, test_server: Server):
        import asyncio

        from games.base import _console_log_path
        from routers.servers import _console_event_stream

        log_path = _console_log_path(test_server.id)
        if os.path.exists(log_path):
            os.remove(log_path)

        with patch("routers.servers.docker_service.is_available", return_value=False):
            payloads = asyncio.run(
                _drain_stream(
                    _console_event_stream(
                        _StubRequest(disconnect_after=2),
                        container="msm-srv-x",
                        log_path=log_path,
                    ),
                    max_frames=5,
                )
            )

        # Sichtbare ``data:``-Zeile statt verstecktem ``event: error``.
        assert any("Rootless Docker Daemon not running for user msm" in p for p in payloads)
