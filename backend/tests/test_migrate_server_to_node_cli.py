from __future__ import annotations

from scripts.migrate_server_to_node import build_parser


def test_cli_accepts_explicit_automation_inputs() -> None:
    args = build_parser().parse_args(
        [
            "--server-id",
            "12",
            "--target-node-id",
            "4",
            "--target-bind-ip",
            "198.51.100.40",
            "--yes",
            "--preflight-only",
        ]
    )

    assert args.server_id == 12
    assert args.target_node_id == 4
    assert args.target_bind_ip == "198.51.100.40"
    assert args.yes is True
    assert args.preflight_only is True
