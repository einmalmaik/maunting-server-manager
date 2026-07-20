import sys
import os
sys.path.insert(0, os.path.abspath('backend'))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models.database import Base
from models import Server, Node
from models.server_port import ServerPort
from services.guardian_sync_service import compile_desired_state
from blueprints.schema import load_blueprint_dict
from unittest.mock import MagicMock, patch
import json

engine = create_engine('sqlite:///:memory:')
Base.metadata.create_all(engine)
SessionLocal = sessionmaker(bind=engine)
db = SessionLocal()

node = Node(id=1, name="node-1", host="http://127.0.0.1", status="online", auth_token_enc="enc")
server = Server(
    id=42,
    name="TestSrv",
    game_type="minecraft",
    install_dir="/tmp/test",
    status="stopped",
    desired_power_state="running",
    desired_state_generation=7,
    guardian_observed_state="unknown",
    public_bind_ip="127.0.0.1",
)
server.ports = [ServerPort(role="game", port=25565, protocol="tcp")]
server.node = node
db.add_all([node, server])
db.commit()
db.refresh(server)

bp_dict = {
    "version": 1,
    "meta": {
        "id": "minecraft",
        "name": "Minecraft",
        "category": "steam_game",
        "description": "desc",
    },
    "runtime": {
        "image": "ubuntu:latest",
        "startup": "echo",
    },
    "ports": [],
    "source": {
        "type": "dockerOnly",
        "updateStrategy": "none",
    },
    "health": {},
}
blueprint = load_blueprint_dict(bp_dict)
plugin = MagicMock()
plugin.get_blueprint.return_value = blueprint

with patch("services.guardian_sync_service.get_plugin", return_value=plugin):
    payload = compile_desired_state(server)
    print(json.dumps(payload, indent=2))
