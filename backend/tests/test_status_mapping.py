"""Tests fuer das Mapping von Docker-Container-Status auf MSM-Status-Codes.

Frontend kann nur eine kleine Menge MSM-Status-Codes uebersetzen
(running, stopped, starting, installing, updating, error). Docker liefert
darueber hinaus Strings wie "exited", "dead", "created", "paused", "removing",
"restarting", die ohne Mapping 1:1 im Frontend landen wuerden.
"""
import pytest

from games.base import _map_container_status


@pytest.mark.parametrize("docker_status,expected", [
    ("running", "running"),
    ("restarting", "starting"),
    ("exited", "stopped"),
    ("dead", "stopped"),
    ("created", "stopped"),
    ("paused", "stopped"),
    ("removing", "stopped"),
    ("", "stopped"),
    ("unknown_docker_state", "stopped"),
])
def test_map_container_status(docker_status: str, expected: str):
    assert _map_container_status(docker_status) == expected
