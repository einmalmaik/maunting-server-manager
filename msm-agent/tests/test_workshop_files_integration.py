"""Deep on-node integration tests for Blueprint Workshop file lifecycle."""

from pathlib import Path

import pytest


def _payload(mode: str) -> dict:
    return {
        "workshop_app_id": "67890",
        "workshop_id": "12345",
        "mode": mode,
        "actions": [
            {
                "operation": "copy",
                "source": "steamapps/workshop/content/67890/12345/*.pak",
                "target": "Runtime/Mods/{BASENAME}",
                "required": True,
            }
        ],
    }


def test_workshop_apply_inspect_cleanup_roundtrip(client, auth_headers, servers_dir: Path) -> None:
    source = servers_dir / "77" / "steamapps" / "workshop" / "content" / "67890" / "12345"
    source.mkdir(parents=True)
    (source / "synthetic-mod.pak").write_bytes(b"synthetic-workshop-content")

    applied = client.post(
        "/files/workshop?server_id=77", json=_payload("apply"), headers=auth_headers
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["target_basenames"] == ["synthetic-mod.pak"]
    target = servers_dir / "77" / "Runtime" / "Mods" / "synthetic-mod.pak"
    assert target.read_bytes() == b"synthetic-workshop-content"

    inspected = client.post(
        "/files/workshop?server_id=77", json=_payload("inspect"), headers=auth_headers
    )
    assert inspected.status_code == 200
    assert inspected.json()["ready"] is True

    cleaned = client.post(
        "/files/workshop?server_id=77", json=_payload("cleanup"), headers=auth_headers
    )
    assert cleaned.status_code == 200
    assert not target.exists()
    assert not source.exists()


def test_workshop_rejects_path_traversal(client, auth_headers) -> None:
    payload = _payload("apply")
    payload["actions"][0]["target"] = "../outside/{BASENAME}"
    response = client.post(
        "/files/workshop?server_id=77", json=payload, headers=auth_headers
    )
    assert response.status_code == 400


def test_workshop_copy_does_not_follow_existing_target_symlink(
    client, auth_headers, servers_dir: Path
) -> None:
    source = servers_dir / "77" / "steamapps" / "workshop" / "content" / "67890" / "12345"
    source.mkdir(parents=True)
    (source / "synthetic-mod.pak").write_bytes(b"new-content")
    outside = servers_dir / "outside.pak"
    outside.write_bytes(b"must-not-change")
    target = servers_dir / "77" / "Runtime" / "Mods" / "synthetic-mod.pak"
    target.parent.mkdir(parents=True)
    try:
        target.symlink_to(outside)
    except OSError:
        pytest.skip("Symlinks are unavailable on this platform")

    response = client.post(
        "/files/workshop?server_id=77", json=_payload("apply"), headers=auth_headers
    )

    assert response.status_code == 200, response.text
    assert not target.is_symlink()
    assert target.read_bytes() == b"new-content"
    assert outside.read_bytes() == b"must-not-change"


def test_workshop_copy_does_not_overwrite_external_hardlink(
    client, auth_headers, servers_dir: Path
) -> None:
    source = servers_dir / "77" / "steamapps" / "workshop" / "content" / "67890" / "12345"
    source.mkdir(parents=True)
    (source / "synthetic-mod.pak").write_bytes(b"new-content")
    outside = servers_dir / "outside.pak"
    outside.write_bytes(b"must-not-change")
    target = servers_dir / "77" / "Runtime" / "Mods" / "synthetic-mod.pak"
    target.parent.mkdir(parents=True)
    try:
        target.hardlink_to(outside)
    except OSError:
        pytest.skip("Hardlinks are unavailable on this platform")

    response = client.post(
        "/files/workshop?server_id=77", json=_payload("apply"), headers=auth_headers
    )

    assert response.status_code == 200, response.text
    assert target.read_bytes() == b"new-content"
    assert outside.read_bytes() == b"must-not-change"
