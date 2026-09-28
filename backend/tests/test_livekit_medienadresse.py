"""Der LiveKit-Sidecar bekommt seine Medienadresse vom Host, nie per STUN.

Am 27.09.2026 startete der Sidecar im Minutentakt neu: `use_external_ip` fragte
per STUN bei Google nach, und im rootless Container zeigte die Namensaufloesung
auf 127.0.0.53, den es dort nicht gibt. Das Panel meldete nur „antwortet nicht“.
"""

from __future__ import annotations

import importlib.util
import socket
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SKRIPT = ROOT / "livekit-sidecar" / "medienadresse.py"

_spec = importlib.util.spec_from_file_location("medienadresse", SKRIPT)
medienadresse = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(medienadresse)


def _aufloesung(monkeypatch, antworten: dict[str, list[str]]) -> list[str]:
    gefragt: list[str] = []

    def _getaddrinfo(host, _port, family=0, *_a, **_k):
        assert family == socket.AF_INET
        gefragt.append(host)
        if host not in antworten:
            raise socket.gaierror("unbekannt")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in antworten[host]]

    monkeypatch.setattr(medienadresse.socket, "getaddrinfo", _getaddrinfo)
    return gefragt


def test_nimmt_die_adresse_der_domain_in_der_reihenfolge_des_backends(monkeypatch) -> None:
    gefragt = _aufloesung(
        monkeypatch,
        {"medien.example.de": ["203.0.113.5"], "api.example.de": ["203.0.113.9"]},
    )
    env = {
        "MSM_LIVEKIT_URL": "wss://medien.example.de/livekit",
        "MSM_API_URL": "https://api.example.de",
        "MSM_PANEL_URL": "https://panel.example.de",
    }
    assert medienadresse.medienadresse(env) == "203.0.113.5"
    assert gefragt == ["medien.example.de"]

    del env["MSM_LIVEKIT_URL"]
    assert medienadresse.medienadresse(env) == "203.0.113.9"


def test_vorgabe_des_betreibers_schlaegt_die_domain(monkeypatch) -> None:
    gefragt = _aufloesung(monkeypatch, {"api.example.de": ["203.0.113.9"]})
    env = {"MSM_API_URL": "https://api.example.de", "MSM_LIVEKIT_NODE_IP": "198.51.100.4"}
    assert medienadresse.medienadresse(env) == "198.51.100.4"
    assert gefragt == []

    env["MSM_LIVEKIT_NODE_IP"] = "kein-ip"
    assert medienadresse.medienadresse(env) == ""


def test_loopback_aus_etc_hosts_ist_keine_medienadresse(monkeypatch) -> None:
    # Debian traegt den eigenen Hostnamen gern als 127.0.1.1 ein.
    _aufloesung(monkeypatch, {"api.example.de": ["127.0.1.1"]})
    assert medienadresse.medienadresse({"MSM_API_URL": "https://api.example.de"}) == ""


def test_oeffentlich_vor_privat_privat_nur_wenn_allein(monkeypatch) -> None:
    _aufloesung(monkeypatch, {"a.example.de": ["10.0.0.5", "8.8.8.8"], "lan.example": ["192.168.1.10"]})
    assert medienadresse.medienadresse({"MSM_API_URL": "https://a.example.de"}) == "8.8.8.8"
    # Ein Panel nur im eigenen Netz ist ein gueltiger Aufbau.
    assert medienadresse.medienadresse({"MSM_API_URL": "http://lan.example:8000"}) == "192.168.1.10"


def test_nicht_aufloesbar_liefert_leer_statt_zu_scheitern(monkeypatch) -> None:
    _aufloesung(monkeypatch, {})
    assert medienadresse.medienadresse({"MSM_API_URL": "https://weg.example.de"}) == ""
    assert medienadresse.medienadresse({}) == ""


def test_skript_liest_die_backend_env_wie_install_sh_sie_schreibt(tmp_path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        '# Kommentar\nMSM_PANEL_URL="https://198.51.100.7"\nMSM_LIVEKIT_API_KEY="APIx"\n',
        encoding="utf-8",
    )
    ergebnis = subprocess.run(
        [sys.executable, str(SKRIPT), str(env)], capture_output=True, text=True, check=True
    )
    assert ergebnis.stdout.strip() == "198.51.100.7"


def test_sidecar_fragt_nicht_per_stun_und_bekommt_node_ip() -> None:
    konfig = (ROOT / "livekit-sidecar" / "livekit.yaml").read_text(encoding="utf-8")
    assert "use_external_ip: false" in konfig
    assert "use_external_ip: true" not in konfig

    for skript in ("install.sh", "update.sh"):
        text = (ROOT / skript).read_text(encoding="utf-8")
        assert 'livekit-sidecar/medienadresse.py" "$ENV_FILE"' in text, skript
        assert 'echo "NODE_IP=\\"$LIVEKIT_NODE_IP\\"" >> "$LIVEKIT_ENV_FILE"' in text, skript
        assert "MSM_LIVEKIT_NODE_IP" in text, skript


@pytest.mark.parametrize("skript", ["install.sh", "update.sh"])
def test_sidecar_start_wird_geprueft_statt_behauptet(skript: str) -> None:
    # Am 27.09.2026 hielt ein fremder LiveKit 7881/7882. Der zweite Startversuch
    # nahm den liegengebliebenen Container ohne Portfreigabe, systemd meldete
    # „Started“, und der Lauf sagte „LiveKit Sidecar bereit.“
    text = (ROOT / skript).read_text(encoding="utf-8")
    assert text.count("ExecStart=/usr/bin/docker compose up --force-recreate") == 2
    assert "ExecStart=/usr/bin/docker compose up\n" not in text

    start = text.split('log "Starte LiveKit Media Sidecar..."', 1)[1]
    bereit = start.index('ok "LiveKit Sidecar bereit."')
    assert start.index("curl -fsS --max-time 2 http://127.0.0.1:7880") < bereit
    assert 'if [[ "$_livekit_ok" == true ]]; then' in start[:bereit]
    assert "ss -Hlntup '( sport = :7880 or sport = :7881 or sport = :7882 )'" in start


@pytest.mark.parametrize("ordner", ["livekit-sidecar", "searxng-sidecar"])
def test_sidecar_liegt_im_panel_release(ordner: str) -> None:
    # install.sh und update.sh richten den Dienst nur ein, wenn der Ordner da
    # ist. Fehlt er im Tarball, laeuft auf 7880 nie etwas.
    packaging = (ROOT / "scripts" / "build-release-artifacts.sh").read_text(encoding="utf-8")
    liste = packaging.split('archive_tracked "$PANEL_ROOT"', 1)[1].split("\nrm ", 1)[0]
    assert ordner in liste.split()

    workflow = (ROOT / ".github" / "workflows" / "release-artifacts.yml").read_text(
        encoding="utf-8"
    )
    assert f"/{ordner}/docker-compose.yml$" in workflow
