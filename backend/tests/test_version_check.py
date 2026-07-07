"""Unit-Tests fuer die Versionsnormalisierung und den SemVer-Vergleich
im system-Router (_strip_version, _version_newer).

Testet die Root-Cause des False-Positive-Update-Banners:
- v-Prefix-Mismatch (z.B. '1.7.7' vs 'v1.7.7')
- git-describe-Suffixe (z.B. 'v1.7.7-2-gabcdef')
- Korrekte numerische Reihenfolge statt String-Vergleich
"""
import pytest

from routers.system import _strip_version, _version_newer


class TestStripVersion:
    """_strip_version normalisiert Versions-Strings fuer den Vergleich."""

    def test_strips_v_prefix(self):
        assert _strip_version("v1.7.7") == "1.7.7"

    def test_no_prefix_unchanged(self):
        assert _strip_version("1.7.7") == "1.7.7"

    def test_strips_git_describe_suffix(self):
        assert _strip_version("v1.7.7-2-gabcdef") == "1.7.7"

    def test_strips_git_describe_suffix_no_v(self):
        assert _strip_version("1.7.7-5-g1234567") == "1.7.7"

    def test_whitespace_stripped(self):
        assert _strip_version("  v1.7.7\n") == "1.7.7"

    def test_non_semver_passthrough(self):
        assert _strip_version("abc123") == "abc123"

    def test_unknown_passthrough(self):
        assert _strip_version("unknown") == "unknown"

    def test_empty_string(self):
        assert _strip_version("") == ""


class TestVersionNewer:
    """_version_newer prueft ob latest > current (numerisch)."""

    def test_same_version_not_newer(self):
        assert _version_newer("1.7.7", "1.7.7") is False

    def test_higher_patch_is_newer(self):
        assert _version_newer("1.7.8", "1.7.7") is True

    def test_higher_minor_is_newer(self):
        assert _version_newer("1.8.0", "1.7.7") is True

    def test_higher_major_is_newer(self):
        assert _version_newer("2.0.0", "1.7.7") is True

    def test_lower_patch_not_newer(self):
        assert _version_newer("1.7.6", "1.7.7") is False

    def test_lower_minor_not_newer(self):
        assert _version_newer("1.6.9", "1.7.7") is False

    def test_lower_major_not_newer(self):
        assert _version_newer("0.9.9", "1.7.7") is False

    def test_non_parsable_latest_returns_false(self):
        assert _version_newer("unknown", "1.7.7") is False

    def test_non_parsable_current_returns_false(self):
        assert _version_newer("1.7.7", "unknown") is False

    def test_empty_strings_return_false(self):
        assert _version_newer("", "") is False

    def test_numeric_not_string_comparison(self):
        """Stellt sicher, dass '1.10.0' > '1.9.0' (nicht String '10' < '9')."""
        assert _version_newer("1.10.0", "1.9.0") is True


class TestEndToEndScenarios:
    """Reproduziert das gemeldete Problem: gleiche Version, falsches Banner."""

    def test_git_describe_matches_release_tag(self):
        """v1.7.7 (git describe) vs v1.7.7 (GitHub tag) -> kein Update."""
        current_raw = "v1.7.7"
        latest_raw = "v1.7.7"
        norm_c = _strip_version(current_raw)
        norm_l = _strip_version(latest_raw)
        assert _version_newer(norm_l, norm_c) is False

    def test_git_describe_with_suffix_same_base(self):
        """v1.7.7-2-gabcdef (git describe) vs v1.7.7 (GitHub tag) -> kein Update."""
        current_raw = "v1.7.7-2-gabcdef"
        latest_raw = "v1.7.7"
        norm_c = _strip_version(current_raw)
        norm_l = _strip_version(latest_raw)
        assert _version_newer(norm_l, norm_c) is False

    def test_no_v_prefix_current(self):
        """1.7.7 (kein v-Prefix) vs v1.7.7 (GitHub) -> kein Update."""
        current_raw = "1.7.7"
        latest_raw = "v1.7.7"
        norm_c = _strip_version(current_raw)
        norm_l = _strip_version(latest_raw)
        assert _version_newer(norm_l, norm_c) is False

    def test_actual_update_available(self):
        """v1.7.6 (lokal) vs v1.7.7 (GitHub) -> Update verfuegbar."""
        current_raw = "v1.7.6"
        latest_raw = "v1.7.7"
        norm_c = _strip_version(current_raw)
        norm_l = _strip_version(latest_raw)
        assert _version_newer(norm_l, norm_c) is True

    def test_ahead_of_release(self):
        """v1.7.8-1-g1234 (lokal, ahead) vs v1.7.7 (GitHub) -> kein Update."""
        current_raw = "v1.7.8-1-g1234"
        latest_raw = "v1.7.7"
        norm_c = _strip_version(current_raw)
        norm_l = _strip_version(latest_raw)
        assert _version_newer(norm_l, norm_c) is False
