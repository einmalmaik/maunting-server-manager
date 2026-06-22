"""Tests for parse_steamcmd_progress — must understand real SteamCMD output
('Update state (0x61) downloading, progress: 4.43 (209952633 / 4736388301)')
and not just the rare 'progress: NN' form. Otherwise the Mod-Manager UI
stays at 'Wird geladen / Restzeit wird berechnet' while SteamCMD is
actually streaming real progress numbers.
"""
from services.mod_install_status_service import parse_steamcmd_progress


def test_progress_comma_separated_state_line():
    line = (
        " Update state (0x61) downloading, progress: 4.43 "
        "(209952633 / 4736388301)"
    )
    progress, current, total = parse_steamcmd_progress(line)
    assert progress == 4
    assert current == 209952633
    assert total == 4736388301


def test_progress_two_digit_no_decimal():
    line = " Update state (0x61) downloading, progress: 65.44 (3099473596 / 4736388301)"
    progress, _, _ = parse_steamcmd_progress(line)
    assert progress == 65


def test_progress_legacy_colon():
    line = "progress: 27.60 (1307090642 / 4736388301)"
    progress, _, _ = parse_steamcmd_progress(line)
    assert progress == 28


def test_progress_bracket_percent():
    line = "Downloading foo.pak [42%]"
    progress, _, _ = parse_steamcmd_progress(line)
    assert progress == 42


def test_progress_no_match_returns_none():
    progress, current, total = parse_steamcmd_progress("No progress info here")
    assert progress is None
    assert current is None
    assert total is None


def test_progress_invalid_number_falls_back():
    progress, _, _ = parse_steamcmd_progress("progress: not_a_number")
    assert progress is None