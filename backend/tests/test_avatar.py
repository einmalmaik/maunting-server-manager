import io
import pytest
from fastapi.testclient import TestClient
from models import User


def test_avatar_upload_and_delete(client: TestClient, owner_user: User, owner_cookies: dict, csrf_token: str):
    # Valid PNG 1x1 image bytes
    png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"

    files = {"file": ("test.png", io.BytesIO(png_bytes), "image/png")}
    headers = {"X-CSRF-Token": csrf_token}
    res = client.post("/api/auth/me/avatar", files=files, headers=headers, cookies=owner_cookies)
    assert res.status_code == 200
    data = res.json()
    assert data["avatar_url"] is not None
    assert "/api/auth/avatar/avatar_" in data["avatar_url"]

    # Get avatar image
    avatar_path = data["avatar_url"]
    get_res = client.get(avatar_path)
    assert get_res.status_code == 200
    assert get_res.content == png_bytes

    # Delete avatar
    del_res = client.delete("/api/auth/me/avatar", headers=headers, cookies=owner_cookies)
    assert del_res.status_code == 200
    assert del_res.json()["avatar_url"] is None

    # Get deleted avatar returns 404
    get_res_after = client.get(avatar_path)
    assert get_res_after.status_code == 404


def test_avatar_invalid_format(client: TestClient, owner_user: User, owner_cookies: dict, csrf_token: str):
    files = {"file": ("bad.exe", io.BytesIO(b"MZ\x90\x00notanimage"), "application/octet-stream")}
    headers = {"X-CSRF-Token": csrf_token}
    res = client.post("/api/auth/me/avatar", files=files, headers=headers, cookies=owner_cookies)
    assert res.status_code == 400


def test_avatar_corrupted_png(client: TestClient, owner_user: User, owner_cookies: dict, csrf_token: str):
    files = {"file": ("corrupt.png", io.BytesIO(b"fake png data"), "image/png")}
    headers = {"X-CSRF-Token": csrf_token}
    res = client.post("/api/auth/me/avatar", files=files, headers=headers, cookies=owner_cookies)
    assert res.status_code == 400
