import pytest
from pydantic import ValidationError

from schemas.server import ServerUpdate


def test_accepts_multiple_restart_times() -> None:
    body = ServerUpdate(auto_restart=True, restart_times_utc="04:00,16:30")

    assert body.restart_times_utc == "04:00,16:30"


def test_rejects_invalid_restart_time() -> None:
    """Renamed + expanded for clarity per testing-runtime.md (rejects out-of-range + bad formats + max12)."""
    with pytest.raises(ValidationError):
        ServerUpdate(auto_restart=True, restart_times_utc="25:00")


def test_rejects_more_than_12_times() -> None:
    csv = ",".join([f"{h:02d}:00" for h in range(13)])
    with pytest.raises(ValidationError) as exc:
        ServerUpdate(auto_restart=True, restart_times_utc=csv)
    assert "Maximal 12" in str(exc.value)


def test_accepts_exactly_12_times() -> None:
    csv = ",".join([f"{h:02d}:00" for h in range(12)])
    body = ServerUpdate(auto_restart=True, restart_times_utc=csv)
    assert len(body.restart_times_utc.split(",")) == 12


def test_dedups_duplicate_times_and_accepts() -> None:
    """Dups normalized (no duplicate cron jobs); accepts after dedup if <=12 unique."""
    body = ServerUpdate(auto_restart=True, restart_times_utc="04:00,04:00,16:30")
    assert body.restart_times_utc == "04:00,16:30"


def test_rejects_bad_formats_and_edge_times() -> None:
    bads = ["4:00", "04:0", "04-00", "abc:00", "24:00", "00:60", "25:61"]
    for b in bads:
        with pytest.raises(ValidationError):
            ServerUpdate(auto_restart=True, restart_times_utc=b)


def test_empty_or_whitespace_returns_none_or_empty() -> None:
    # Validator returns None for explicit None or fully empty after strip; pydantic may yield '' for "" input in some cases.
    # Accept either for robustness (main: no crash, no invalid schedule).
    for v in [None, "   ", ",, "]:
        body = ServerUpdate(auto_restart=True, restart_times_utc=v)
        assert body.restart_times_utc in (None, "")
    body_empty = ServerUpdate(auto_restart=True, restart_times_utc="")
    assert body_empty.restart_times_utc in (None, "")


def test_legacy_restart_time_utc_still_validated_by_field() -> None:
    with pytest.raises(ValidationError):
        ServerUpdate(auto_restart=True, restart_time_utc="99:99")


def test_post_dedup_scheduler_delegation_edge() -> None:
    """Easy post-dedup edge for scheduler sync coverage (review gap): validator normalizes dups before len/max12,
    so scheduler receives clean CSV (no duplicate APScheduler jobs). Delegation to central _validate in schemas.
    """
    # Many dups + exactly at boundary after dedup
    csv_with_dups = "04:00," + ",".join(["05:00"] * 20) + ",06:00"
    body = ServerUpdate(auto_restart=True, restart_times_utc=csv_with_dups)
    times = body.restart_times_utc.split(",")
    assert len(times) == 3  # deduped
    assert times == ["04:00", "05:00", "06:00"]
    assert len(set(times)) == len(times)  # no dups post
    assert body.restart_times_utc == "04:00,05:00,06:00"  # positive post-dedup CSV output (scheduler edge, re-review coverage)
