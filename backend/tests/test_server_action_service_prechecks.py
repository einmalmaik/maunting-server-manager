"""Pre-Checks vor einer Lifecycle-Aktion.

Der Fokus liegt auf der Manual-Upload-Pruefung: sie liest das Dateisystem des
Panels. Bei einem Remote-Node liegen die Dateien aber auf dem Agent, der
Panel-Pfad existiert dort nicht — die Pruefung wuerde also immer fehlende
Dateien melden und den Server dauerhaft unstartbar machen.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from blueprints.schema import BlueprintSourceType
from models import Node, Server, User
from services.actor_context import ActorContext
from services.server_action_service import _validate_lifecycle_request


def _manual_plugin(required: list[str]):
    """Minimaler Plugin-Stub mit einer Manual-Upload-Blueprint."""
    blueprint = SimpleNamespace(
        source=SimpleNamespace(
            type=BlueprintSourceType.MANUAL_UPLOAD,
            manual=SimpleNamespace(requiredFiles=required),
        )
    )
    return SimpleNamespace(get_blueprint=lambda: blueprint)


def _server(db: Session, tmp_path: Path, *, node: Node | None) -> Server:
    install_dir = tmp_path / "manual-server"
    install_dir.mkdir(parents=True, exist_ok=True)
    server = Server(
        name="Manual Upload",
        game_type="dayz",
        install_dir=str(install_dir),
        status="stopped",
        public_bind_ip="10.1.2.3",
        node_id=node.id if node is not None else None,
    )
    db.add(server)
    db.commit()
    db.refresh(server)
    return server


def _remote_node(db: Session) -> Node:
    node = Node(
        name="edge-1",
        host="10.0.0.9",
        auth_token_enc="dummy",
        is_local=False,
        status="online",
    )
    db.add(node)
    db.commit()
    db.refresh(node)
    return node


def test_local_server_still_requires_the_manually_uploaded_files(
    db: Session, owner_user: User, tmp_path: Path
) -> None:
    """Lokal bleibt die Pruefung unveraendert scharf."""
    server = _server(db, tmp_path, node=None)

    with patch(
        "services.server_action_service.get_plugin",
        return_value=_manual_plugin(["server.exe"]),
    ), patch("services.node_service.is_node_offline", return_value=False):
        with pytest.raises(HTTPException) as excinfo:
            _validate_lifecycle_request(
                db, ActorContext.for_user(owner_user), server.id, "start"
            )

    assert excinfo.value.status_code == 400
    assert "server.exe" in str(excinfo.value.detail)


def test_remote_server_is_not_blocked_by_a_panel_side_file_check(
    db: Session, owner_user: User, tmp_path: Path
) -> None:
    """Auf einem Remote-Node darf die Panel-Pfad-Pruefung den Start nicht verhindern.

    Die Dateien liegen dort beim Agent. Die Runtime-Vorbereitung auf dem Agent
    uebernimmt die Validierung — genau wie beim Installationspfad.
    """
    node = _remote_node(db)
    server = _server(db, tmp_path, node=node)

    with patch(
        "services.server_action_service.get_plugin",
        return_value=_manual_plugin(["server.exe"]),
    ), patch("services.node_service.is_node_offline", return_value=False):
        validated = _validate_lifecycle_request(
            db, ActorContext.for_user(owner_user), server.id, "start"
        )

    assert validated.id == server.id
