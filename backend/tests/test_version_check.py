"""Unit-Tests fuer die Versionsnormalisierung und den SemVer-Vergleich
im system-Router (_strip_version, _version_newer).

Testet die Root-Cause des False-Positive-Update-Banners:
- v-Prefix-Mismatch (z.B. '1.7.7' vs 'v1.7.7')
- git-describe-Suffixe (z.B. 'v1.7.7-2-gabcdef')
- Korrekte numerische Reihenfolge statt String-Vergleich

Dazu der Zwischenspeicher des GitHub-Release-Checks: die Fusszeile fragt
/version bei jedem Seitenaufbau ab, der Netzaufruf darf höchstens einmal
pro TTL stattfinden.
"""
import httpx
import pytest

import routers.system as system_router
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


class _FakeResponse:
    """Minimale httpx-Antwort für den gemockten Release-Aufruf."""

    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


@pytest.fixture
def leerer_release_cache(monkeypatch):
    """Leert den Modul-Cache; monkeypatch stellt ihn nach dem Test wieder her."""
    monkeypatch.setattr(system_router, "_GITHUB_RELEASE_CACHE", None)


class TestReleaseCache:
    """_get_latest_release ruft GitHub höchstens einmal pro TTL an."""

    def test_zweiter_aufruf_geht_nicht_ins_netz(self, monkeypatch, leerer_release_cache):
        aufrufe = []

        def fake_get(url, **kwargs):
            aufrufe.append(url)
            return _FakeResponse(200, {"tag_name": "v1.7.8", "html_url": "https://example.test/r"})

        monkeypatch.setattr(httpx, "get", fake_get)

        erste = system_router._get_latest_release()
        zweite = system_router._get_latest_release()

        assert len(aufrufe) == 1
        assert erste == {"tag_name": "v1.7.8", "html_url": "https://example.test/r"}
        assert zweite == erste

    def test_abgelaufener_eintrag_fragt_erneut(self, monkeypatch, leerer_release_cache):
        aufrufe = []

        def fake_get(url, **kwargs):
            aufrufe.append(url)
            return _FakeResponse(200, {"tag_name": "v1.7.8", "html_url": ""})

        monkeypatch.setattr(httpx, "get", fake_get)

        system_router._get_latest_release()
        # Zeitstempel künstlich altern lassen (älter als die TTL).
        ts, daten = system_router._GITHUB_RELEASE_CACHE
        system_router._GITHUB_RELEASE_CACHE = (
            ts - system_router._GITHUB_RELEASE_CACHE_TTL_SECONDS - 1,
            daten,
        )
        system_router._get_latest_release()

        assert len(aufrufe) == 2

    def test_fehlschlag_wird_ebenfalls_gemerkt(self, monkeypatch, leerer_release_cache):
        """Ohne Internetausgang darf nicht jeder Seitenaufbau in den Timeout laufen."""
        aufrufe = []

        def fake_get(url, **kwargs):
            aufrufe.append(url)
            raise httpx.ConnectError("kein Netz")

        monkeypatch.setattr(httpx, "get", fake_get)

        assert system_router._get_latest_release() == {}
        assert system_router._get_latest_release() == {}
        assert len(aufrufe) == 1

    def test_rate_limit_liefert_keine_felder(self, monkeypatch, leerer_release_cache):
        """403 (GitHub-Limit) -> leeres Ergebnis, keine erfundenen Versionsangaben."""
        monkeypatch.setattr(
            httpx, "get", lambda url, **kwargs: _FakeResponse(403, {"message": "rate limit"})
        )

        assert system_router._get_latest_release() == {}
