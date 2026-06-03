"""Tests fuer Blueprint-gesteuerte Stop-Grace-Period.

Provider-neutral: Testet nur, dass der Hook in ``base.py`` den Wert aus der
Blueprint liest und den Default nutzt, wenn keine Blueprint vorhanden ist.
"""

from types import SimpleNamespace
from unittest.mock import patch

from games.base import GamePlugin


class _StubPlugin(GamePlugin):
    """Minimale Plugin-Implementierung."""

    docker_image = "stub/image:latest"
    supports_mods = False

    def __init__(self, bp):
        self._bp = bp

    def get_blueprint(self):
        return self._bp

    def install(self, server):
        return {"ok": True}

    def get_config_schema(self):
        return []

    def get_config_files(self):
        return []

    def get_logs(self, server, lines=100):
        return ""

    def build_container_command(self, server):
        return []

    def build_container_env(self, server):
        return {}

    def build_port_publishes(self, server):
        return []

    def build_volume_binds(self, server):
        return []


def test_stop_grace_period_uses_blueprint_value():
    bp = SimpleNamespace(runtime=SimpleNamespace(stopGracePeriodSeconds=120))
    plugin = _StubPlugin(bp)
    assert plugin.stop_grace_period_seconds(None) == 120


def test_stop_grace_period_default_when_runtime_missing():
    plugin = _StubPlugin(SimpleNamespace(runtime=None))
    assert plugin.stop_grace_period_seconds(None) == 30


def test_stop_grace_period_default_when_field_missing():
    bp = SimpleNamespace(runtime=SimpleNamespace())
    plugin = _StubPlugin(bp)
    assert plugin.stop_grace_period_seconds(None) == 30


def test_stop_grace_period_default_when_no_blueprint():
    plugin = _StubPlugin(None)
    assert plugin.stop_grace_period_seconds(None) == 30


def test_stop_calls_docker_service_with_blueprint_timeout():
    bp = SimpleNamespace(runtime=SimpleNamespace(stopGracePeriodSeconds=45))
    plugin = _StubPlugin(bp)

    with patch("games.base.docker_service") as mock_docker:
        mock_docker.stop.return_value = {"ok": True, "stdout": "", "stderr": ""}
        result = plugin.stop(SimpleNamespace(id=1, name="x"))

    assert mock_docker.stop.called
    call_kwargs = mock_docker.stop.call_args.kwargs
    assert call_kwargs["timeout"] == 45
    assert result.get("message") == "Server gestoppt"
