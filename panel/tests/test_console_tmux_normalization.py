from __future__ import annotations

from app.api.console import _diff_tmux_snapshots, _normalize_tmux_snapshot


def test_normalize_tmux_snapshot_strips_ansi_and_outer_padding():
    snapshot = "\n\n\x1b[31malpha\x1b[0m\nbeta   \n\n"

    assert _normalize_tmux_snapshot(snapshot) == ["alpha", "beta"]


def test_normalize_tmux_snapshot_preserves_internal_blank_lines():
    snapshot = "alpha\n\nbeta\n"

    assert _normalize_tmux_snapshot(snapshot) == ["alpha", "", "beta"]


def test_diff_tmux_snapshots_ignores_identical_output():
    lines = ["alpha", "beta"]

    assert _diff_tmux_snapshots(lines, lines) == []


def test_diff_tmux_snapshots_returns_only_new_lines():
    previous = _normalize_tmux_snapshot("alpha\nbeta\n")
    current = _normalize_tmux_snapshot("alpha\nbeta\ngamma\n")

    assert _diff_tmux_snapshots(previous, current) == ["gamma"]
