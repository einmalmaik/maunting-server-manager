#!/usr/bin/env python3
"""Interactive operator CLI for a failure-safe server-to-node migration."""

from __future__ import annotations

import argparse
import sys

from database import SessionLocal
from models import Node, Server
from services.server_node_migration_service import (
    ServerNodeMigrationError,
    migrate_server_to_node,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Einen gestoppten Game-Server sicher auf einen anderen MSM-Node kopieren.",
    )
    parser.add_argument("--server-id", type=int)
    parser.add_argument("--target-node-id", type=int)
    parser.add_argument(
        "--target-bind-ip",
        help="Öffentliche Bind-IP auf dem Zielnode; leer bedeutet 0.0.0.0.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Explizite Textbestätigung überspringen (nur für geprüfte Automation).",
    )
    return parser


def _select_id(prompt: str, value: int | None) -> int:
    if value is not None:
        return value
    raw = input(prompt).strip()
    try:
        selected = int(raw)
    except ValueError as exc:
        raise ServerNodeMigrationError("Ungültige numerische Auswahl") from exc
    if selected <= 0:
        raise ServerNodeMigrationError("Auswahl muss größer als null sein")
    return selected


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    db = SessionLocal()
    try:
        print("Verfügbare Server:")
        for server in db.query(Server).order_by(Server.id.asc()).all():
            node_name = server.node.name if server.node is not None else "ohne Node"
            print(f"  {server.id}: {server.name} [{server.status}] auf {node_name}")

        print("\nVerfügbare Zielnodes:")
        for node in db.query(Node).order_by(Node.id.asc()).all():
            print(f"  {node.id}: {node.name} [{node.status}]")

        server_id = _select_id("\nServer-ID: ", args.server_id)
        target_node_id = _select_id("Zielnode-ID: ", args.target_node_id)
        server = db.query(Server).filter(Server.id == server_id).first()
        target = db.query(Node).filter(Node.id == target_node_id).first()
        if server is None or target is None:
            raise ServerNodeMigrationError("Server oder Zielnode wurde nicht gefunden")

        target_bind_ip = args.target_bind_ip
        if target_bind_ip is None and server.public_bind_ip:
            target_bind_ip = input(
                "Der Server nutzt eine feste Quell-IP. Neue Bind-IP auf dem Zielnode: "
            ).strip()

        if not args.yes:
            expected = f"MIGRATE {server_id}"
            confirmation = input(
                f"\nQuelle bleibt erhalten. Zum Start exakt '{expected}' eingeben: "
            ).strip()
            if confirmation != expected:
                print("Abgebrochen; es wurde nichts verändert.")
                return 2

        result = migrate_server_to_node(
            db,
            server_id=server_id,
            target_node_id=target_node_id,
            target_bind_ip=target_bind_ip,
        )
        print(
            f"Migration abgeschlossen: Server {result['server_id']} liegt jetzt auf "
            f"Node {result['target_node_id']}. Die Quelldaten wurden beibehalten."
        )
        if result.get("cleanup_pending"):
            print("Hinweis: Ein temporärer Rollbackstand auf dem Ziel muss bereinigt werden.")
        return 0
    except ServerNodeMigrationError as exc:
        print(f"Migration abgebrochen: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
