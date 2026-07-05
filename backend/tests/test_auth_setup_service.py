"""Tests fuer auth_setup_service — generischer Auth-Recovery fuer Game-Server-Container."""
from __future__ import annotations

from pathlib import Path
from services.auth_setup_service import detect_auth_required


def test_detects_hytale_auth_prompt():
    logs = [
        "Picked up JAVA_TOOL_OPTIONS",
        "ℹ Checking for updates...",
        "Please visit the following URL to authenticate:\nhttps://oauth.accounts.hytale.com/oauth2/device/verify?user_code=ABC123",
    ]
    assert detect_auth_required(logs) is True


def test_detects_oauth_invalid_grant():
    logs = [
        'oauth2: "invalid_grant" "The refresh token expired"',
    ]
    assert detect_auth_required(logs) is True


def test_detects_refresh_token_expired_bare():
    logs = [
        "Some preceeding line",
        "refresh token expired",
        "tail",
    ]
    assert detect_auth_required(logs) is True


def test_detects_signed_url_failure():
    logs = [
        "could not get signed URL for manifest",
    ]
    assert detect_auth_required(logs) is True


def test_detects_authorization_code_line():
    logs = [
        "Authorization code: HrREE3kK",
    ]
    assert detect_auth_required(logs) is True


def test_does_not_match_unrelated_crash():
    logs = [
        "java.lang.OutOfMemoryError",
        "  at sun.misc.Unsafe.allocateMemory(Native Method)",
    ]
    assert detect_auth_required(logs) is False


def test_does_not_match_when_no_url_or_oauth_keyword():
    logs = [
        "Server started on port 25565",
        "Done (5.2s)!",
    ]
    assert detect_auth_required(logs) is False


def test_does_not_match_on_empty_logs():
    assert detect_auth_required([]) is False


def test_case_insensitive():
    logs = ["OAUTH2: \"INVALID_GRANT\""]
    assert detect_auth_required(logs) is True