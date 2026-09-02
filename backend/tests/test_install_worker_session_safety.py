"""Installations-Threads duerfen nicht an der Request-Session haengen.

Hintergrund
-----------
`provision_server` ruft `plugin.install(server)` auf, waehrend die Request-Session
noch offen ist. Der dort gestartete Thread laeuft aber weiter, nachdem `get_db()`
die Session geschlossen hat. Fasste der Thread danach ein nicht geladenes
Attribut des uebergebenen ORM-Objekts an — etwa die Relationship `server.node` —,
warf SQLAlchemy `DetachedInstanceError`.

Fuer einen Server auf einem REMOTE-Node war das der Normalfall, nicht die
Ausnahme: `provision_server` beruehrt `server.node` nie (es arbeitet mit einer
eigenen `target_node`-Variable) und committet unmittelbar vor dem Install,
wodurch alle Attribute als abgelaufen markiert werden. Remote-Installationen
schlugen dadurch zuverlaessig fehl.

Die Tests fixieren die Invariante: der Worker-Rumpf bekommt einen frisch
geladenen, losgeloesten Server und erreicht seinen Installationsaufruf auch
dann, wenn die Request-Session laengst geschlossen ist.
"""

from __future__ import annotations

from pathlib import Path
import threading
from unittest.mock import patch

import pytest
from sqlalchemy.orm import Session

from blueprints.schema import load_blueprint_dict, load_blueprint_file
from games import blueprint_plugin as bp_module
from games.blueprint_plugin import BlueprintPlugin
from models import Node, Server
from tests.test_blueprint_github_source import GITHUB_BOT


def _native(blueprint_id: str):
    path = (
        Path(__file__).resolve().parents[1]
        / "blueprints"
        / "native"
        / f"{blueprint_id}.blueprint.json"
    )
    return load_blueprint_file(path)


def _blueprint(kind: str):
    """Je eine Quelle pro Installations-Closure.

    valheim = Steam (ohne erzwungenen Login), hytale = HTTP, GITHUB_BOT = GitHub.
    Damit ist jeder der drei Worker-Rumpfe abgedeckt.
    """
    if kind == "github":
        return load_blueprint_dict(GITHUB_BOT)
    return _native(kind)


def _remote_server(db: Session, tmp_path: Path, game_type: str) -> Server:
    """Ein Server auf einem Remote-Node — genau die betroffene Konstellation."""
    node = Node(
        name="remote-1",
        host="10.0.0.9",
        auth_token_enc="dummy-ciphertext",
        is_local=False,
        status="online",
    )
    db.add(node)
    db.commit()
    db.refresh(node)

    install_dir = tmp_path / f"{game_type}-remote"
    install_dir.mkdir(parents=True, exist_ok=True)
    server = Server(
        name="Remote Install",
        game_type=game_type,
        install_dir=str(install_dir),
        status="installing",
        node_id=node.id,
    )
    db.add(server)
    db.commit()
    db.refresh(server)
    return server


class _FakeNodeClient:
    """Antwortet auf alle Agent-Aufrufe des Installationspfads mit Erfolg."""

    def files_cache_configs(self, *_a, **_k):
        return {"ok": True}

    def files_restore_configs(self, *_a, **_k):
        return {"ok": True}

    def files_clear_config_cache(self, *_a, **_k):
        return {"ok": True}

    def install_http_source(self, *_a, **_k):
        return {"ok": True}

    def install_github_source(self, *_a, **_k):
        return {"ok": True}


@pytest.mark.parametrize("game_type", ["valheim", "hytale", "github"])
def test_remote_install_worker_runs_after_the_request_session_is_closed(
    db: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    game_type: str,
) -> None:
    """Der Worker muss die Installation ohne die Request-Session durchfuehren.

    Alle drei Quellen (Steam, HTTP, GitHub) liefen zuvor auf einem Remote-Node
    in einen `DetachedInstanceError`.
    """
    blueprint = _blueprint(game_type)
    server = _remote_server(db, tmp_path, game_type)
    server_id = server.id

    # Statt einen echten Thread zu starten, wird der Rumpf abgefangen. So laesst
    # sich der Ausfuehrungszeitpunkt exakt hinter das Schliessen der Session
    # legen; ein echter Thread waere an dieser Stelle ein Timing-Wuerfelspiel.
    captured: dict = {}
    monkeypatch.setattr(
        bp_module,
        "_start_install_worker",
        lambda sid, name, body: captured.update(server_id=sid, body=body),
    )

    with patch(
        "services.node_client.NodeClient.from_node", return_value=_FakeNodeClient()
    ), patch(
        "games.blueprint_plugin.run_steamcmd_install", return_value={"ok": True}
    ), patch(
        "games.blueprint_plugin.finish_install"
    ) as finish:
        BlueprintPlugin(blueprint).install(server)
        assert captured.get("server_id") == server_id

        # Genau das macht get_db(), sobald die Antwort geschrieben ist.
        db.commit()
        db.close()

        # Das ist der Vertrag: der Worker laedt sich seinen Server selbst.
        reloaded = bp_module._load_detached_server(server_id)
        assert reloaded is not None
        assert reloaded.node is not None and reloaded.node.is_local is False

        captured["body"](reloaded)

    assert finish.call_count == 1
    _, result = finish.call_args[0]
    assert result.get("ok") is True, result


def test_detached_server_keeps_its_node_after_the_session_is_gone(
    db: Session,
    tmp_path: Path,
) -> None:
    """Der geladene Server bleibt ohne offene Session vollstaendig benutzbar.

    Ein Installationslauf dauert Minuten bis Stunden. Eine dafuer offen
    gehaltene Session wuerde so lange eine Verbindung aus dem Pool blockieren —
    deshalb wird bewusst losgeloest statt offen gehalten.
    """
    server = _remote_server(db, tmp_path, "valheim")
    server_id = server.id
    db.commit()
    db.close()

    detached = bp_module._load_detached_server(server_id)

    assert detached is not None
    # Ohne joinedload wuerde genau dieser Zugriff DetachedInstanceError werfen.
    assert detached.node.is_local is False
    assert detached.node.host == "10.0.0.9"
    assert detached.install_dir.endswith("valheim-remote")


def test_missing_server_is_reported_instead_of_crashing_the_worker() -> None:
    """Ein zwischenzeitlich geloeschter Server beendet den Vorgang sauber."""
    with patch("games.blueprint_plugin.finish_install") as finish:
        bp_module._start_install_worker(
            999_999, "install-missing-999999", lambda _server: None
        )
        for thread in threading.enumerate():
            if thread.name == "install-missing-999999":
                thread.join(timeout=10)

    assert finish.call_count == 1
    server_id, result = finish.call_args[0]
    assert server_id == 999_999
    assert result["ok"] is False


def test_worker_guard_reports_a_failure_instead_of_stranding_the_install(
    db: Session,
    tmp_path: Path,
) -> None:
    """`_start_install_worker` bleibt der terminale Abschluss-Schutz.

    Wirft der Rumpf, muss `finish_install` trotzdem laufen — sonst blieben
    Serverstatus, Provisionierungs-Task und die node-weite Install-Sperre
    dauerhaft haengen.
    """
    server = _remote_server(db, tmp_path, "valheim")
    server_id = server.id
    db.commit()

    def _explode(_server) -> None:
        raise RuntimeError("Quelle nicht erreichbar")

    with patch("games.blueprint_plugin.finish_install") as finish:
        bp_module._start_install_worker(server_id, f"install-test-{server_id}", _explode)
        for thread in threading.enumerate():
            if thread.name == f"install-test-{server_id}":
                thread.join(timeout=10)

    assert finish.call_count == 1
    finished_id, result = finish.call_args[0]
    assert finished_id == server_id
    assert result["ok"] is False
    # Die Meldung nennt nur den Ausnahmetyp, keine Pfade oder Providerdetails.
    assert "RuntimeError" in result["error"]
    assert "Quelle nicht erreichbar" not in result["error"]
