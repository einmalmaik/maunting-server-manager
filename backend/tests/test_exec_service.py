"""Tests fuer backend/services/exec_service.py.

Exec-Tab (v1.4.7): User koennen Befehle IM Container ausfuehren. Wichtig:
- Container-Name wird ausschliesslich aus ``container_name_for(server.id)``
  gebildet -- kein User-Input fliesst in den Container-Namen.
- Output wird auf 256 KiB gedeckelt mit [truncated]-Marker.
- Audit-Log enthaelt argv, NICHT stdout/stderr.
"""
from __future__ import annotations

import logging


# ── Task 3: Delegation an docker_service.exec_in ────────────────────────


def test_run_in_container_delegates_to_docker_exec_in(monkeypatch):
    from services import exec_service

    seen: dict = {}

    def fake_exec_in(name, command, timeout):
        seen["name"] = name
        seen["command"] = command
        seen["timeout"] = timeout
        return {"ok": True, "stdout": "hello", "stderr": ""}

    monkeypatch.setattr(exec_service.docker_service, "exec_in", fake_exec_in)
    monkeypatch.setattr(
        exec_service, "container_name_for", lambda sid: f"msm-server-{sid}"
    )

    result = exec_service.run_in_container(
        server_id=42, command=["ls", "-la"], timeout=30
    )
    assert result["ok"] is True
    assert result["stdout"] == "hello"
    assert seen["name"] == "msm-server-42"  # NICHT aus User-Input
    assert seen["command"] == ["ls", "-la"]  # argv, kein String
    assert seen["timeout"] == 30


def test_run_in_container_passes_argv_verbatim_no_shell_escape(monkeypatch):
    """Sicherheits-kritisch: ein ``;`` im argv wird als literaler Filename
    behandelt, NICHT als Shell-Metazeichen. Der Befehl geht als argv-Liste an
    ``container.exec_run``, der keine Shell dazwischen schaltet.
    """
    from services import exec_service

    seen: dict = {}

    def fake_exec_in(name, command, timeout):
        seen["command"] = command
        return {"ok": True, "stdout": "", "stderr": ""}

    monkeypatch.setattr(exec_service.docker_service, "exec_in", fake_exec_in)
    monkeypatch.setattr(exec_service, "container_name_for", lambda sid: "c")

    dangerous_argv = ["ls", "/data; rm -rf /tmp/x"]
    exec_service.run_in_container(server_id=1, command=dangerous_argv, timeout=10)
    # Genau dieses argv muss angekommen sein -- als argv, nicht via sh -c.
    assert seen["command"] == dangerous_argv
    assert all(isinstance(a, str) for a in seen["command"])


def test_run_in_container_rejects_when_container_missing(monkeypatch):
    """Wenn docker_service.exec_in mit 'Container laeuft nicht' antwortet,
    gibt der Service das unveraendert weiter (kein Mock-Bypass).
    """
    from services import exec_service

    monkeypatch.setattr(
        exec_service.docker_service,
        "exec_in",
        lambda *a, **kw: {
            "ok": False,
            "error": "Container laeuft nicht",
            "stdout": "",
            "stderr": "",
        },
    )
    result = exec_service.run_in_container(
        server_id=1, command=["ls"], timeout=10
    )
    assert result["ok"] is False
    assert "Container" in result["error"]


# ── Task 4: Output-Truncation ───────────────────────────────────────────


def test_truncate_output_under_limit_unchanged():
    from services.exec_service import _truncate_output
    s = "hello world\n"
    assert _truncate_output(s, max_bytes=256 * 1024) == s


def test_truncate_output_at_limit_no_marker():
    from services.exec_service import _truncate_output
    # Genau am Limit: kein Marker, weil nichts abgeschnitten wurde.
    s = "x" * (256 * 1024)
    out = _truncate_output(s, max_bytes=256 * 1024)
    assert out == s
    assert "[truncated]" not in out


def test_truncate_output_over_limit_marker_added():
    from services.exec_service import _truncate_output
    s = "x" * (300 * 1024)
    out = _truncate_output(s, max_bytes=256 * 1024)
    assert out.endswith("\n...[truncated]")
    # Output-Bytes vor dem Marker <= Limit
    assert len(out) <= 256 * 1024 + len("\n...[truncated]")


def test_truncate_output_unicode_safe():
    """UTF-8-Multibyte darf nicht mittendrin abgeschnitten werden."""
    from services.exec_service import _truncate_output
    # 300k Emoji = 300k * 4 Bytes = 1.2 MB
    s = "🚀" * 300_000
    out = _truncate_output(s, max_bytes=256 * 1024)
    # Wenn abgeschnitten, endet es mit dem truncated-Marker
    assert out.endswith("\n...[truncated]")
    # Vor dem Marker: valides UTF-8 (kein ``UnicodeDecodeError`` beim Empfang)
    marker_pos = out.rfind("\n...[truncated]")
    head = out[:marker_pos]
    head.encode("utf-8")  # raises wenn kaputt -- wir wollen dass es klappt


# ── Task 5: Audit-Log ohne Output ───────────────────────────────────────


def test_run_in_container_writes_audit_log_with_argv_not_output(
    monkeypatch, caplog
):
    """Audit-Log enthaelt: server_id, user_id, argv. NICHT: stdout/stderr.

    Hintergrund: Output kann sensible Daten enthalten (Secrets, Tokens,
    User-PII). Wir loggen NUR, welcher Befehl von wem gegen welchen Server
    gelaufen ist -- das reicht fuer Forensik.
    """
    from services import exec_service

    monkeypatch.setattr(
        exec_service.docker_service,
        "exec_in",
        lambda *a, **kw: {
            "ok": True,
            "stdout": "secret-payload-NEVER-LOG-12345",
            "stderr": "neither-this",
        },
    )
    monkeypatch.setattr(exec_service, "container_name_for", lambda sid: "c")

    with caplog.at_level(logging.INFO, logger="msm.audit.exec"):
        exec_service.run_in_container(
            server_id=42,
            command=["cat", "/etc/secret"],
            timeout=30,
            user_id=7,
        )

    text = caplog.text
    assert "cat" in text  # argv ist im Log
    assert "/etc/secret" in text
    assert "42" in text and "7" in text
    # Output darf NICHT im Log auftauchen
    assert "secret-payload-NEVER-LOG-12345" not in text
    assert "neither-this" not in text


def test_run_in_container_logs_failure_path(monkeypatch, caplog):
    """Auch Fehlschlag (exit != 0) wird auditiert -- mit 'ok=False' Marker."""
    from services import exec_service

    monkeypatch.setattr(
        exec_service.docker_service,
        "exec_in",
        lambda *a, **kw: {
            "ok": False,
            "error": "exit 1",
            "stdout": "leaky-stdout",
            "stderr": "leaky-stderr",
        },
    )
    monkeypatch.setattr(exec_service, "container_name_for", lambda sid: "c")

    with caplog.at_level(logging.INFO, logger="msm.audit.exec"):
        result = exec_service.run_in_container(
            server_id=99,
            command=["false"],
            timeout=10,
            user_id=3,
        )

    assert result["ok"] is False
    text = caplog.text
    assert "false" in text and "99" in text and "3" in text
    assert "leaky-stdout" not in text
    assert "leaky-stderr" not in text


# ── Task 7: Blueprint-Lookup-Helper ─────────────────────────────────────


def test_load_blueprint_for_server_uses_plugin(monkeypatch):
    """Der Helper delegiert an ``get_plugin(server.game_type).get_blueprint()``
    -- das ist der einzige existierende Pfad, einen Blueprint pro Server zu
    bekommen. Wichtig: Wenn kein Plugin gefunden wird (Server kaputt),
    gibt der Helper None zurueck statt zu crashen.
    """
    from services import exec_service

    fake_bp = type("BP", (), {"runtime": type("R", (), {"enableExec": True})()})()

    class _FakePlugin:
        def get_blueprint(self):
            return fake_bp

    monkeypatch.setattr(
        exec_service, "get_plugin", lambda gt: _FakePlugin()
    )
    fake_server = type("S", (), {"game_type": "singra_backend"})()
    bp = exec_service.load_blueprint_for_server(fake_server)
    assert bp is fake_bp
    assert bp.runtime.enableExec is True


def test_load_blueprint_for_server_returns_none_when_no_plugin(monkeypatch):
    from services import exec_service
    monkeypatch.setattr(exec_service, "get_plugin", lambda gt: None)
    fake_server = type("S", (), {"game_type": "unknown_game_xyz"})()
    assert exec_service.load_blueprint_for_server(fake_server) is None