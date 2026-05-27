"""Tests fuer Live-Konsole: SSE-Stream + stdin-Eingabe.

Decken die Sicherheitsinvarianten ab:
- Stream-Endpoint verlangt ``server.console.read``.
- Input-Endpoint verlangt ``server.console.write``.
- Input-POST loggt den Inhalt NICHT (z. B. OAuth-Code).
- ``send_stdin`` ruft ``docker exec -i`` mit dem korrekten Aufruf auf.
- ``run_container`` startet den Container mit ``--interactive`` (sonst geht
  stdin nicht).
"""

from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import Server, User


class TestSendStdin:
    """``docker_service.send_stdin`` schreibt in den Container-stdin."""

    def test_invokes_docker_exec_i_with_pipe_to_pid1(self):
        from services import docker_service

        with patch("services.docker_service.is_running", return_value=True), \
             patch("services.docker_service._run_docker") as mock_run:
            mock_run.return_value = {"ok": True, "stdout": "", "stderr": ""}
            result = docker_service.send_stdin("msm-srv-1", "/auth login device\n")

        assert result["ok"] is True
        args, kwargs = mock_run.call_args
        cmd = args[0]
        # Pruefe nur die security-relevanten Flags. Volle Argv darf sich aendern.
        assert cmd[0] == "exec"
        assert "-i" in cmd[:3]
        assert "msm-srv-1" in cmd
        # Der eigentliche Schreibvorgang muss in PID-1-stdin gehen.
        assert any("/proc/1/fd/0" in part for part in cmd)
        # Inhalt MUSS via stdin uebergeben werden — niemals als argv-Element.
        # Das ist die Security-Invariante: kein Leak in Process-Listings.
        assert kwargs.get("stdin") == "/auth login device\n"
        assert "/auth login device\n" not in cmd

    def test_refuses_when_container_not_running(self):
        from services import docker_service

        with patch("services.docker_service.is_running", return_value=False):
            result = docker_service.send_stdin("msm-srv-1", "anything\n")

        assert result["ok"] is False


class TestRunContainerKeepsStdinOpen:
    """``run_container`` muss ``--interactive`` setzen, sonst gehen Konsolen-
    Eingaben (Hytale-OAuth, EULA-Bestaetigung, RCON) lautlos verloren."""

    def test_run_container_includes_interactive_flag(self):
        from services import docker_service

        with patch("services.docker_service.exists", return_value=False), \
             patch("services.docker_service._run_docker") as mock_run:
            mock_run.return_value = {"ok": True, "stdout": "", "stderr": ""}
            docker_service.run_container(
                name="msm-srv-1",
                image="alpine:3.20",
            )

        args, _ = mock_run.call_args
        cmd = args[0]
        assert cmd[0] == "run"
        # ``-d`` (detach) + ``-i`` (interactive) muessen beide gesetzt sein.
        assert "-d" in cmd
        assert "-i" in cmd


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


class TestConsoleStreamWhenDockerMissing:
    """Der Live-Stream darf bei fehlendem docker-Binary (z. B. eingeschränkter
    PATH im systemd-Unit) nicht mehr mit roher Exception abstürzen.
    Er muss stattdessen ein sauberes Error-Event liefern (200 OK + SSE).
    """

    def test_stream_yields_error_event_instead_of_crashing_when_docker_cli_missing(
        self,
        client: TestClient,
        owner_cookies: dict,
        test_server: Server,
    ):
        with patch("routers.servers.shutil.which", return_value=None):
            response = client.get(
                f"/api/servers/{test_server.id}/console/stream",
                cookies=owner_cookies,
            )

        # Muss 200 liefern — der Stream selbst startet, auch wenn später
        # ein Error-Event kommt. Kein 500-Crash mehr!
        assert response.status_code == 200
        # Body enthält das von uns yieldete Error-Event (text/event-stream).
        body = response.text
        assert "event: error" in body
        assert "Docker CLI nicht im PATH" in body or "nicht verfügbar" in body
