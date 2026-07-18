#!/usr/bin/env python3
"""Operator CLI for converting an all-in-one local node to a remote node."""

from __future__ import annotations

import argparse
import sys

from database import SessionLocal
from models import Node, Server
from services.local_node_handoff_service import (
    LocalNodeHandoffError,
    handoff_local_node,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Den lokalen All-in-one-Node nach verifiziertem Agent-Handoff in einen "
            "eigenstaendigen Node umwandeln. Dateien und Container bleiben am Ort."
        )
    )
    parser.add_argument("--replacement-node-id", type=int)
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Explizite Textbestaetigung ueberspringen (nur fuer gepruefte Automation).",
    )
    return parser


def _select_replacement(value: int | None) -> int:
    if value is not None:
        if value <= 0:
            raise LocalNodeHandoffError("Node-ID muss groesser als null sein")
        return value
    try:
        selected = int(input("Ersatz-Node-ID: ").strip())
    except ValueError as exc:
        raise LocalNodeHandoffError("Ungueltige numerische Node-ID") from exc
    if selected <= 0:
        raise LocalNodeHandoffError("Node-ID muss groesser als null sein")
    return selected


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    db = SessionLocal()
    try:
        local = db.query(Node).filter(Node.is_local.is_(True)).first()
        if local is None:
            raise LocalNodeHandoffError("Kein lokaler Node registriert")

        servers = db.query(Server).filter(Server.node_id == local.id).order_by(Server.id).all()
        print(f"Lokaler Node: {local.name} (ID {local.id})")
        print(f"Zugeordnete Gameserver: {len(servers)}")
        for node in db.query(Node).filter(Node.is_local.is_(False)).order_by(Node.id).all():
            print(f"  {node.id}: {node.name} [{node.status}]")

        replacement_id = _select_replacement(args.replacement_node_id)
        if not args.yes:
            expected = f"HANDOFF {replacement_id}"
            confirmation = input(
                f"Zum sicheren Agent-Handoff exakt '{expected}' eingeben: "
            ).strip()
            if confirmation != expected:
                print("Abgebrochen; es wurde nichts veraendert.")
                return 2

        result = handoff_local_node(db, replacement_node_id=replacement_id)
        print(
            f"Handoff abgeschlossen: {len(result['server_ids'])} Gameserver verwenden "
            f"jetzt Node {result['replacement_node_id']}. Daten und Container blieben am Ort."
        )
        return 0
    except LocalNodeHandoffError as exc:
        print(f"Handoff abgebrochen: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
