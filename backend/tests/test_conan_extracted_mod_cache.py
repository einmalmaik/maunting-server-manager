"""Conan ExtractedMods-Cache nach Workshop-Pak-Update."""
from pathlib import Path

from games.blueprint_plugin import _purge_conan_extracted_mod_cache


def test_purge_conan_extracted_mod_cache(tmp_path):
    extracted = tmp_path / "ConanSandbox" / "Saved" / "ExtractedMods"
    extracted.mkdir(parents=True)
    for name in (
        "Foo-LinuxServer.pak",
        "Foo-LinuxServer.utoc",
        "Foo-LinuxServer.ucas",
        "Bar-LinuxServer.pak",
    ):
        (extracted / name).write_bytes(b"x")

    removed = _purge_conan_extracted_mod_cache(tmp_path, "Foo.pak")

    assert len(removed) == 3
    assert not (extracted / "Foo-LinuxServer.pak").exists()
    assert (extracted / "Bar-LinuxServer.pak").exists()