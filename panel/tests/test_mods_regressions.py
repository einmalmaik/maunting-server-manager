from __future__ import annotations

import asyncio
import pytest
import subprocess
from pathlib import Path

from fastapi import HTTPException

from app.api import mods
from app.game_profile import CONAN_WORKSHOP_APP_ID
from app.models import User


REPO_ROOT = Path(__file__).resolve().parents[2]


def _owner_user() -> User:
    return User(id=1, username="owner", password_hash="x", role="owner", is_active=True)


def test_add_mod_auto_installs_verified_dependencies(monkeypatch):
    calls: list[tuple[str, str, str | None]] = []

    async def run() -> None:
        monkeypatch.setattr(mods, "_record_audit", lambda *args, **kwargs: None)
        async def fake_resolve(mod_id: str) -> mods.DependencyResolution:
            return mods.DependencyResolution(
                status="verified",
                mod_detail={"publishedfileid": mod_id, "result": 1},
                dependencies=[{"publishedfileid": "200", "title": "Community Framework", "result": 1}],
            )
        monkeypatch.setattr(
            mods,
            "_resolve_mod_dependencies",
            fake_resolve,
        )

        def fake_mods_add(mod_id: str, mod_name: str, server_name: str | None = None):
            calls.append((mod_id, mod_name, server_name))
            return {"ok": True}

        monkeypatch.setattr(mods, "mods_add", fake_mods_add)

        response = await mods.add_mod(
            mods.AddModBody(mod_id="100", mod_name="main mod"),
            db=None,
            user=_owner_user(),
            server="alpha",
        )

        assert response["ok"] is True
        assert response["dependency_status"] == "verified"
        assert response["installed_dependencies"] == [{"id": "200", "name": "community framework"}]
        assert calls == [
            ("100", "main mod", "alpha"),
            ("200", "community framework", "alpha"),
        ]

    asyncio.run(run())


def test_update_mods_selective_starts_async_workshop_task(monkeypatch):
    recorded_calls: list[tuple[str, tuple[str, ...], str | None, str]] = []
    recorded_audits: list[tuple[str, str | None, str, str | None]] = []

    class DummySession:
        def begin_nested(self):
            class _Ctx:
                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, exc_type, exc, tb):
                    return False

            return _Ctx()

        def add(self, entry):
            recorded_audits.append((entry.action, entry.target, entry.status, entry.detail))

        def commit(self):
            return None

    monkeypatch.setattr(
        mods,
        "invoke_core_action_async",
        lambda action_name, *args, server_name=None, task_channel="default": recorded_calls.append((action_name, args, server_name, task_channel)),
    )

    response = mods.update_mods_selective(
        body=mods.UpdateSelectiveBody(mod_ids=["111", "222"]),
        db=DummySession(),
        user=_owner_user(),
        server="alpha",
    )

    assert response == {"ok": True, "async": True}
    assert recorded_calls == [("workshop", ("111", "222"), "alpha", "workshop")]
    assert recorded_audits == [("mods.update.selective", "111,222", "started", None)]


def test_update_mods_selective_returns_409_when_workshop_is_busy(monkeypatch):
    recorded_audits: list[tuple[str, str | None, str, str | None]] = []

    class DummySession:
        def begin_nested(self):
            class _Ctx:
                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, exc_type, exc, tb):
                    return False

            return _Ctx()

        def add(self, entry):
            recorded_audits.append((entry.action, entry.target, entry.status, entry.detail))

        def commit(self):
            return None

    monkeypatch.setattr(
        mods,
        "invoke_core_action_async",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("Another action is already running for server: alpha")),
    )

    with pytest.raises(HTTPException) as exc:
        mods.update_mods_selective(
            body=mods.UpdateSelectiveBody(mod_ids=["111"]),
            db=DummySession(),
            user=_owner_user(),
            server="alpha",
        )

    assert exc.value.status_code == 409
    assert "already running" in str(exc.value.detail)
    assert recorded_audits == [("mods.update.selective", "111", "failed", "Another action is already running for server: alpha")]


def test_add_mod_requires_confirmation_when_dependencies_are_unverified(monkeypatch):
    calls: list[tuple[str, str, str | None]] = []

    async def run() -> None:
        monkeypatch.setattr(mods, "_record_audit", lambda *args, **kwargs: None)
        async def fake_resolve(_mod_id: str) -> mods.DependencyResolution:
            return mods.DependencyResolution(
                status="unverified",
                mod_detail=None,
                dependencies=[],
                message="Dependencies could not be checked.",
            )
        monkeypatch.setattr(
            mods,
            "_resolve_mod_dependencies",
            fake_resolve,
        )
        monkeypatch.setattr(
            mods,
            "mods_add",
            lambda mod_id, mod_name, server_name=None: calls.append((mod_id, mod_name, server_name)),
        )

        response = await mods.add_mod(
            mods.AddModBody(mod_id="100", mod_name="main mod"),
            db=None,
            user=_owner_user(),
            server="alpha",
        )

        assert response["ok"] is False
        assert response["confirm_required"] is True
        assert response["dependency_status"] == "unverified"
        assert calls == []

    asyncio.run(run())


def test_add_mod_allows_confirmed_unverified_dependencies(monkeypatch):
    calls: list[tuple[str, str, str | None]] = []

    async def run() -> None:
        monkeypatch.setattr(mods, "_record_audit", lambda *args, **kwargs: None)
        async def fake_resolve(_mod_id: str) -> mods.DependencyResolution:
            return mods.DependencyResolution(
                status="unverified",
                mod_detail=None,
                dependencies=[],
                message="Dependencies could not be checked.",
            )
        monkeypatch.setattr(
            mods,
            "_resolve_mod_dependencies",
            fake_resolve,
        )

        def fake_mods_add(mod_id: str, mod_name: str, server_name: str | None = None):
            calls.append((mod_id, mod_name, server_name))
            return {"ok": True}

        monkeypatch.setattr(mods, "mods_add", fake_mods_add)

        response = await mods.add_mod(
            mods.AddModBody(
                mod_id="100",
                mod_name="main mod",
                confirm_unverified_dependencies=True,
            ),
            db=None,
            user=_owner_user(),
            server="alpha",
        )

        assert response["ok"] is True
        assert response["dependency_status"] == "unverified"
        assert response["dep_warning"] == "Dependencies could not be checked."
        assert calls == [("100", "main mod", "alpha")]

    asyncio.run(run())


def test_dependency_resolution_is_unverified_when_dependency_details_request_fails(monkeypatch):
    async def run() -> None:
        async def fake_fetch(mod_ids: list[str]) -> list[dict[str, object]]:
            if mod_ids == ["100"]:
                return [{
                    "publishedfileid": "100",
                    "result": 1,
                    "children": [{"filetype": 1, "publishedfileid": "200"}],
                }]
            raise RuntimeError("Could not reach Steam to verify mod dependencies.")

        monkeypatch.setattr(mods, "_fetch_published_file_details", fake_fetch)

        resolution = await mods._resolve_mod_dependencies("100")

        assert resolution.status == "unverified"
        assert resolution.dependencies == []
        assert resolution.message == "Could not reach Steam to verify mod dependencies."

    asyncio.run(run())


def test_dependency_resolution_is_unverified_when_dependency_metadata_is_incomplete(monkeypatch):
    async def run() -> None:
        async def fake_fetch(mod_ids: list[str]) -> list[dict[str, object]]:
            if mod_ids == ["100"]:
                return [{
                    "publishedfileid": "100",
                    "result": 1,
                    "children": [{"filetype": 1, "publishedfileid": "200"}],
                }]
            return []

        monkeypatch.setattr(mods, "_fetch_published_file_details", fake_fetch)

        resolution = await mods._resolve_mod_dependencies("100")

        assert resolution.status == "unverified"
        assert resolution.dependencies == []
        assert resolution.message == "Steam returned incomplete dependency metadata for this mod."

    asyncio.run(run())


def test_dependency_resolution_recurses_nested_dependencies(monkeypatch):
    async def run() -> None:
        async def fake_fetch(mod_ids: list[str]) -> list[dict[str, object]]:
            if mod_ids == ["100"]:
                return [{
                    "publishedfileid": "100",
                    "result": 1,
                    "title": "Main Mod",
                    "children": [{"filetype": 1, "publishedfileid": "200"}],
                }]
            if mod_ids == ["200"]:
                return [{
                    "publishedfileid": "200",
                    "result": 1,
                    "title": "Dependency A",
                    "children": [{"filetype": 1, "publishedfileid": "300"}],
                }]
            if mod_ids == ["300"]:
                return [{
                    "publishedfileid": "300",
                    "result": 1,
                    "title": "Dependency B",
                    "children": [],
                }]
            return []

        async def fake_page_fetch(_mod_id: str) -> list[dict[str, str]]:
            return []

        monkeypatch.setattr(mods, "_fetch_published_file_details", fake_fetch)
        monkeypatch.setattr(mods, "_fetch_required_items_from_workshop_page", fake_page_fetch)

        resolution = await mods._resolve_mod_dependencies("100")

        assert resolution.status == "verified"
        assert [detail["publishedfileid"] for detail in resolution.dependencies] == ["200", "300"]

    asyncio.run(run())


def test_dependency_resolution_falls_back_to_workshop_required_items(monkeypatch):
    async def run() -> None:
        async def fake_fetch(mod_ids: list[str]) -> list[dict[str, object]]:
            if mod_ids == ["100"]:
                return [{
                    "publishedfileid": "100",
                    "result": 1,
                    "title": "Vehicle Shooting",
                    "children": [],
                }]
            return []

        async def fake_page_fetch(mod_id: str) -> list[dict[str, str]]:
            if mod_id != "100":
                return []
            return [{"id": "2918418331", "title": "Survivor Animations"}]

        monkeypatch.setattr(mods, "_fetch_published_file_details", fake_fetch)
        monkeypatch.setattr(mods, "_fetch_required_items_from_workshop_page", fake_page_fetch)

        resolution = await mods._resolve_mod_dependencies("100")

        assert resolution.status == "verified"
        assert resolution.dependencies == [{
            "publishedfileid": "2918418331",
            "result": 1,
            "title": "Survivor Animations",
            "preview_url": "",
            "children": [],
        }]

    asyncio.run(run())


def test_parse_required_items_from_workshop_html_reads_required_items_block():
    html = """
    <div class="requiredItemsContainer" id="RequiredItems">
      <a href="https://steamcommunity.com/workshop/filedetails/?id=2918418331" target="_blank">
        <div class="requiredItem">Survivor Animations</div>
      </a>
      <a href="https://steamcommunity.com/sharedfiles/filedetails/?id=3409752557" target="_blank">
        <div class="requiredItem">Another Dependency</div>
      </a>
    </div>
    </div>
    """

    assert mods._parse_required_items_from_workshop_html(html) == [
        {"id": "2918418331", "title": "Survivor Animations"},
        {"id": "3409752557", "title": "Another Dependency"},
    ]


def test_sanitize_mod_name_preserves_spaces_and_hyphens():
    sanitized = mods._sanitize_mod_name("  Community-Framework Expansion  ", "fallback")

    assert sanitized == "community-framework expansion"


def test_build_mod_dry_run_marks_install_update_relink_and_remove():
    analysis = {
        "mods": [
            {
                "id": "100",
                "name": "missing",
                "installed": False,
                "conflicts": [],
                "steam_timestamp": 0,
                "local_timestamp": 0,
            },
            {
                "id": "200",
                "name": "update-me",
                "installed": True,
                "conflicts": [],
                "steam_timestamp": 200,
                "local_timestamp": 100,
            },
            {
                "id": "300",
                "name": "relink-me",
                "installed": True,
                "conflicts": [{"code": "missing_symlink", "message": "missing"}],
                "steam_timestamp": 100,
                "local_timestamp": 100,
            },
        ],
        "stray_symlinks": [{"name": "old-link", "path": "@old-link", "target": "/tmp/old"}],
    }

    dry_run = mods._build_mod_dry_run(analysis)

    assert [action["type"] for action in dry_run["actions"]] == [
        "install",
        "update",
        "relink",
        "remove_symlink",
    ]
    assert dry_run["summary"]["has_changes"] is True


def test_steam_workshop_search_uses_conan_exiles_app_id(monkeypatch):
    captured_params: dict[str, object] = {}

    class DummyResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"response": {"publishedfiledetails": []}}

    class DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, _url, params):
            captured_params.update(params)
            return DummyResponse()

    async def run() -> None:
        monkeypatch.setattr(mods, "_get_steam_api_key", lambda: "synthetic-test-key")
        monkeypatch.setattr(mods.httpx, "AsyncClient", DummyAsyncClient)

        response = await mods.steam_search(q="building", user=_owner_user())

        assert response == {"response": {"publishedfiledetails": []}}
        assert captured_params["appid"] == CONAN_WORKSHOP_APP_ID

    asyncio.run(run())


def test_mod_analysis_uses_conan_workshop_content_path(tmp_path: Path, monkeypatch):
    base_dir = tmp_path / "alpha"
    serverfiles = base_dir / "serverfiles"
    workshop_dir = serverfiles / "steamapps" / "workshop" / "content" / str(CONAN_WORKSHOP_APP_ID) / "100"
    workshop_dir.mkdir(parents=True)
    (base_dir / "workshop.cfg").write_text("100 emberlight enhanced\n", encoding="utf-8")
    (base_dir / "config.ini").write_text(
        'workshop="@emberlight enhanced"\nservermods="@emberlight enhanced"\n',
        encoding="utf-8",
    )

    async def run() -> None:
        monkeypatch.setattr(mods, "get_server_base_dir", lambda _server: base_dir)
        monkeypatch.setattr(mods, "fetch_mods_timestamps", lambda server_name=None: {"timestamps": {"100": 10}})
        monkeypatch.setattr(
            mods,
            "fetch_mods_list",
            lambda server_name=None: {"mods": [{"id": "100", "name": "emberlight enhanced", "client": True, "server": True}]},
        )

        async def fake_fetch(mod_ids: list[str]) -> list[dict[str, object]]:
            assert mod_ids == ["100"]
            return [{"publishedfileid": "100", "result": 1, "title": "Emberlight Enhanced", "time_updated": 10}]

        monkeypatch.setattr(mods, "_fetch_published_file_details", fake_fetch)

        analysis = await mods._build_mod_analysis("alpha")

        row = analysis["mods"][0]
        assert row["installed"] is True
        assert f"content/{CONAN_WORKSHOP_APP_ID}/100" in row["expected_target"].replace("\\", "/")

    asyncio.run(run())


def test_add_mod_body_strips_only_unsafe_characters():
    body = mods.AddModBody(mod_id="100", mod_name='  Expansion / Core; "Main"  ')

    assert body.mod_name == "expansion core main"


def test_panel_bridge_mods_add_preserves_spaces_and_hyphens():
    script = """
set -euo pipefail
export SCRIPT_DIR="$PWD"
export SCRIPT_PATH="$PWD/conanserver.sh"
export CURRENT_LANGUAGE="en"
source "./lib/i18n.sh"
source "./lib/core.sh"
source "./lib/config.sh"
source "./lib/panel.sh"

tmpdir="./.pytest-panel-add-mod-$$"
rm -rf "$tmpdir"
mkdir -p "$tmpdir"
trap 'rm -rf "$tmpdir"' EXIT

WORKSHOP_CFG="${tmpdir}/workshop.cfg"
CONFIG_FILE="${tmpdir}/config.ini"
SERVER_DIR="${tmpdir}"

fn_panel_bridge_mods_add 123456 '  Expansion-Group Test Mod  '
grep -Fx '123456 expansion-group test mod' "$WORKSHOP_CFG"
"""

    normalized_script = script.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")

    result = subprocess.run(
        ["bash", "-s"],
        cwd=REPO_ROOT,
        capture_output=True,
        input=normalized_script,
        check=False,
    )
    stdout = result.stdout.decode("utf-8", errors="replace")
    stderr = result.stderr.decode("utf-8", errors="replace")

    assert result.returncode == 0, stderr or stdout


def test_panel_bridge_mods_add_updates_config_workshop_entry():
    script = """
set -euo pipefail
export SCRIPT_DIR="$PWD"
export SCRIPT_PATH="$PWD/conanserver.sh"
export CURRENT_LANGUAGE="en"
source "./lib/i18n.sh"
source "./lib/core.sh"
source "./lib/config.sh"
source "./lib/panel.sh"

tmpdir="./.pytest-panel-add-config-$$"
rm -rf "$tmpdir"
mkdir -p "$tmpdir"
trap 'rm -rf "$tmpdir"' EXIT

WORKSHOP_CFG="${tmpdir}/workshop.cfg"
CONFIG_FILE="${tmpdir}/config.ini"
SERVER_DIR="${tmpdir}"

printf 'workshop="@community framework"\nservermods="@server-only"\n' > "$CONFIG_FILE"

fn_panel_bridge_mods_add 123456 '  Expansion-Group Test Mod  '
grep -Fx '123456 expansion-group test mod' "$WORKSHOP_CFG"
grep -Fx 'workshop="@community framework;@expansion-group test mod"' "$CONFIG_FILE"
grep -Fx 'servermods="@server-only"' "$CONFIG_FILE"
"""

    normalized_script = script.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")

    result = subprocess.run(
        ["bash", "-s"],
        cwd=REPO_ROOT,
        capture_output=True,
        input=normalized_script,
        check=False,
    )
    stdout = result.stdout.decode("utf-8", errors="replace")
    stderr = result.stderr.decode("utf-8", errors="replace")

    assert result.returncode == 0, stderr or stdout


def test_panel_bridge_mods_list_uses_config_order_before_workshop_cfg_order():
    script = """
set -euo pipefail
export SCRIPT_DIR="$PWD"
export SCRIPT_PATH="$PWD/conanserver.sh"
export CURRENT_LANGUAGE="en"
source "./lib/i18n.sh"
source "./lib/core.sh"
source "./lib/config.sh"
source "./lib/panel.sh"

tmpdir="./.pytest-panel-list-order-$$"
rm -rf "$tmpdir"
mkdir -p "$tmpdir"
trap 'rm -rf "$tmpdir"' EXIT

WORKSHOP_CFG="${tmpdir}/workshop.cfg"
CONFIG_FILE="${tmpdir}/config.ini"
SERVER_DIR="${tmpdir}"

cat > "$WORKSHOP_CFG" <<'EOF'
111 alpha
222 beta
333 gamma
EOF

printf 'workshop="@gamma;@alpha"\nservermods="@beta"\n' > "$CONFIG_FILE"

output="$(fn_panel_bridge_mods_list)"
python3 - "$output" <<'PY'
import json
import sys

mods = json.loads(sys.argv[1])["mods"]
if [mod["id"] for mod in mods] != ["333", "111", "222"]:
    raise SystemExit(1)
PY
"""

    normalized_script = script.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")

    result = subprocess.run(
        ["bash", "-s"],
        cwd=REPO_ROOT,
        capture_output=True,
        input=normalized_script,
        check=False,
    )
    stdout = result.stdout.decode("utf-8", errors="replace")
    stderr = result.stderr.decode("utf-8", errors="replace")

    assert result.returncode == 0, stderr or stdout


def test_workshop_sync_updates_registered_mod_names_and_config():
    script = """
set -euo pipefail
export SCRIPT_DIR="$PWD"
export SCRIPT_PATH="$PWD/conanserver.sh"
export CURRENT_LANGUAGE="en"
source "./lib/i18n.sh"
source "./lib/core.sh"
source "./lib/config.sh"
source "./lib/workshop.sh"

tmpdir="./.pytest-workshop-name-sync-$$"
rm -rf "$tmpdir"
mkdir -p "$tmpdir"
trap 'rm -rf "$tmpdir"' EXIT

WORKSHOP_CFG="${tmpdir}/workshop.cfg"
CONFIG_FILE="${tmpdir}/config.ini"

printf '123456 expansiongroup_testmod\n' > "$WORKSHOP_CFG"
printf 'workshop="@expansiongroup_testmod;@other mod"\nservermods="@expansiongroup_testmod"\n' > "$CONFIG_FILE"

fn_workshop_update_registered_mod_name 123456 'expansiongroup_testmod' 'expansion-group test mod'

grep -Fx '123456 expansion-group test mod' "$WORKSHOP_CFG"
grep -Fx 'workshop="@expansion-group test mod;@other mod"' "$CONFIG_FILE"
grep -Fx 'servermods="@expansion-group test mod"' "$CONFIG_FILE"
"""

    normalized_script = script.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")

    result = subprocess.run(
        ["bash", "-s"],
        cwd=REPO_ROOT,
        capture_output=True,
        input=normalized_script,
        check=False,
    )
    stdout = result.stdout.decode("utf-8", errors="replace")
    stderr = result.stderr.decode("utf-8", errors="replace")

    assert result.returncode == 0, stderr or stdout


def test_workshop_sync_treats_regex_like_mod_names_as_literal_strings():
    script = """
set -euo pipefail
export SCRIPT_DIR="$PWD"
export SCRIPT_PATH="$PWD/conanserver.sh"
export CURRENT_LANGUAGE="en"
source "./lib/i18n.sh"
source "./lib/core.sh"
source "./lib/config.sh"
source "./lib/workshop.sh"

tmpdir="./.pytest-workshop-literal-sync-$$"
rm -rf "$tmpdir"
mkdir -p "$tmpdir"
trap 'rm -rf "$tmpdir"' EXIT

WORKSHOP_CFG="${tmpdir}/workshop.cfg"
CONFIG_FILE="${tmpdir}/config.ini"

printf '123456 expansion+group(test)[v1]\n' > "$WORKSHOP_CFG"
printf 'workshop="@expansion+group(test)[v1];@other mod"\nservermods="@expansion+group(test)[v1]"\n' > "$CONFIG_FILE"

fn_workshop_update_registered_mod_name 123456 'expansion+group(test)[v1]' 'expansion+group(test)[v2]'

grep -Fx '123456 expansion+group(test)[v2]' "$WORKSHOP_CFG"
grep -Fx 'workshop="@expansion+group(test)[v2];@other mod"' "$CONFIG_FILE"
grep -Fx 'servermods="@expansion+group(test)[v2]"' "$CONFIG_FILE"
"""

    normalized_script = script.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")

    result = subprocess.run(
        ["bash", "-s"],
        cwd=REPO_ROOT,
        capture_output=True,
        input=normalized_script,
        check=False,
    )
    stdout = result.stdout.decode("utf-8", errors="replace")
    stderr = result.stderr.decode("utf-8", errors="replace")

    assert result.returncode == 0, stderr or stdout


def test_mod_remove_deletes_workshop_files_but_keeps_keys():
    script = """
set -euo pipefail
export SCRIPT_DIR="$PWD"
export SCRIPT_PATH="$PWD/conanserver.sh"
export CURRENT_LANGUAGE="en"
source "./lib/i18n.sh"
source "./lib/core.sh"
source "./lib/config.sh"
source "./lib/panel.sh"

tmpdir="./.pytest-panel-remove-$$"
rm -rf "$tmpdir"
mkdir -p "$tmpdir"
trap 'rm -rf "$tmpdir"' EXIT

mkdir -p "${tmpdir}/workshop/111" "${tmpdir}/serverfiles/keys"
printf '111 moda\n' > "${tmpdir}/workshop.cfg"
printf 'workshop="@moda"\nservermods="@moda"\n' > "${tmpdir}/config.ini"
printf '{"111":123,"222":456}\n' > "${tmpdir}/mod_timestamps.json"
printf 'key' > "${tmpdir}/serverfiles/keys/shared.bikey"

if ! ln -s "${tmpdir}/workshop/111" "${tmpdir}/serverfiles/@moda" 2>/dev/null; then
  printf '__SKIP_SYMLINK__\n'
  exit 0
fi

CONFIG_FILE="${tmpdir}/config.ini"
WORKSHOP_CFG="${tmpdir}/workshop.cfg"
WORKSHOPFOLDER="${tmpdir}/workshop"
SERVERFILES="${tmpdir}/serverfiles"
TIMESTAMP_FILE="${tmpdir}/mod_timestamps.json"
SERVER_DIR="${tmpdir}"

fn_panel_bridge_mods_remove 111 || { printf 'bridge_remove_failed\n'; exit 1; }

[ ! -e "$WORKSHOPFOLDER/111" ] || { printf 'workshop_dir_still_exists\n'; exit 1; }
[ ! -e "$SERVERFILES/@moda" ] || { printf 'mod_symlink_still_exists\n'; exit 1; }
[ -f "$SERVERFILES/keys/shared.bikey" ] || { printf 'shared_key_missing\n'; exit 1; }
! grep -q '@moda' "$CONFIG_FILE" || { printf 'config_still_references_mod\n'; exit 1; }
! grep -q . "$WORKSHOP_CFG" || { printf 'workshop_cfg_still_contains_entries\n'; exit 1; }
python3 - "$TIMESTAMP_FILE" <<'PY' || { printf 'timestamps_not_cleaned\n'; exit 1; }
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as handle:
    data = json.load(handle)
if "111" in data or data.get("222") != 456:
    raise SystemExit(1)
PY
"""

    normalized_script = script.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")

    result = subprocess.run(
        ["bash", "-s"],
        cwd=REPO_ROOT,
        capture_output=True,
        input=normalized_script,
        check=False,
    )
    stdout = result.stdout.decode("utf-8", errors="replace")
    stderr = result.stderr.decode("utf-8", errors="replace")

    if "__SKIP_SYMLINK__" in stdout:
        pytest.skip("symlink creation not supported in this environment")
    assert result.returncode == 0, stderr or stdout


def test_mod_remove_rejects_unsafe_workshopfolder():
    script = """
set -euo pipefail
export SCRIPT_DIR="$PWD"
export SCRIPT_PATH="$PWD/conanserver.sh"
export CURRENT_LANGUAGE="en"
source "./lib/i18n.sh"
source "./lib/core.sh"
source "./lib/config.sh"
source "./lib/panel.sh"

tmpdir="./.pytest-panel-unsafe-remove-$$"
rm -rf "$tmpdir"
mkdir -p "$tmpdir"
trap 'rm -rf "$tmpdir"' EXIT

printf '111 moda\n' > "${tmpdir}/workshop.cfg"
printf 'workshop="@moda"\nservermods="@moda"\n' > "${tmpdir}/config.ini"

CONFIG_FILE="${tmpdir}/config.ini"
WORKSHOP_CFG="${tmpdir}/workshop.cfg"
WORKSHOPFOLDER="/"
SERVERFILES="${tmpdir}/serverfiles"
TIMESTAMP_FILE="${tmpdir}/mod_timestamps.json"
SERVER_DIR="${tmpdir}"

if fn_panel_bridge_mods_remove 111; then
  printf 'expected_failure\n'
  exit 1
fi
"""

    normalized_script = script.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")

    result = subprocess.run(
        ["bash", "-s"],
        cwd=REPO_ROOT,
        capture_output=True,
        input=normalized_script,
        check=False,
    )
    stdout = result.stdout.decode("utf-8", errors="replace")
    stderr = result.stderr.decode("utf-8", errors="replace")

    assert result.returncode == 0, stderr or stdout
    assert "expected_failure" not in stdout
    assert "unsafe WORKSHOPFOLDER" in stdout or "unsafe mod path" in stdout, stderr or stdout


def test_mod_remove_rejects_unknown_mod_id_without_touching_files():
    script = """
set -euo pipefail
export SCRIPT_DIR="$PWD"
export SCRIPT_PATH="$PWD/conanserver.sh"
export CURRENT_LANGUAGE="en"
source "./lib/i18n.sh"
source "./lib/core.sh"
source "./lib/config.sh"
source "./lib/panel.sh"

tmpdir="./.pytest-panel-missing-mod-$$"
rm -rf "$tmpdir"
mkdir -p "${tmpdir}/workshop/999"
trap 'rm -rf "$tmpdir"' EXIT

printf '111 moda\n' > "${tmpdir}/workshop.cfg"
printf '{"999":123}\n' > "${tmpdir}/mod_timestamps.json"

WORKSHOP_CFG="${tmpdir}/workshop.cfg"
WORKSHOPFOLDER="${tmpdir}/workshop"
TIMESTAMP_FILE="${tmpdir}/mod_timestamps.json"
SERVERFILES="${tmpdir}/serverfiles"
CONFIG_FILE="${tmpdir}/config.ini"
SERVER_DIR="${tmpdir}"

if fn_panel_bridge_mods_remove 999; then
  printf 'expected_failure\n'
  exit 1
fi

[ -d "$WORKSHOPFOLDER/999" ] || { printf 'workshop_dir_deleted_unexpectedly\n'; exit 1; }
grep -q '"999":123' "$TIMESTAMP_FILE" || { printf 'timestamp_removed_unexpectedly\n'; exit 1; }
"""

    normalized_script = script.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")

    result = subprocess.run(
        ["bash", "-s"],
        cwd=REPO_ROOT,
        capture_output=True,
        input=normalized_script,
        check=False,
    )
    stdout = result.stdout.decode("utf-8", errors="replace")
    stderr = result.stderr.decode("utf-8", errors="replace")

    assert result.returncode == 0, stderr or stdout
    assert "expected_failure" not in stdout
    assert "mod not found in workshop.cfg" in stdout, stderr or stdout
