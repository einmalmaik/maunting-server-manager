from __future__ import annotations

from scripts.handoff_local_node import build_parser


def test_cli_accepts_explicit_handoff_inputs() -> None:
    args = build_parser().parse_args(
        ["--replacement-node-id", "7", "--yes"]
    )

    assert args.replacement_node_id == 7
    assert args.yes is True
