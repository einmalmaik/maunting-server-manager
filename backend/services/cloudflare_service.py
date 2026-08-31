from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

API_BASE = "https://api.cloudflare.com/client/v4"


class CloudflareApiUnavailable(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _raise_if_auth(resp: httpx.Response) -> None:
    if resp.status_code in (401, 403):
        raise CloudflareApiUnavailable("cloudflare_api_token_invalid")


def is_configured() -> bool:
    from services.cloudflare_api_key_service import resolve_key
    from services.panel_settings_service import PanelSettingsService

    enabled = PanelSettingsService.get("cloudflare_enabled", "true") != "false"
    return enabled and bool(resolve_key())


async def test_connection() -> dict[str, Any]:
    from services.cloudflare_api_key_service import resolve_key

    key = resolve_key()
    if not key:
        return {"ok": False, "error": "cloudflare_api_token_missing"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{API_BASE}/zones",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                params={"per_page": 1},
            )
            if resp.status_code in (401, 403):
                return {"ok": False, "error": "cloudflare_api_token_invalid"}
            resp.raise_for_status()
            return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def list_zones() -> list[dict[str, Any]]:
    from services.cloudflare_api_key_service import resolve_key

    key = resolve_key()
    if not key:
        raise CloudflareApiUnavailable("cloudflare_api_token_missing")
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(f"{API_BASE}/zones", headers=_headers(key), params={"per_page": 50})
        _raise_if_auth(resp)
        resp.raise_for_status()
        data = resp.json()
        return data.get("result") or []


async def list_dns_records(zone_id: str) -> list[dict[str, Any]]:
    from services.cloudflare_api_key_service import resolve_key

    key = resolve_key()
    if not key:
        raise CloudflareApiUnavailable("cloudflare_api_token_missing")
    zone_id = str(zone_id).strip().replace("\n", "").replace("\r", "")
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(f"{API_BASE}/zones/{zone_id}/dns_records", headers=_headers(key), params={"per_page": 100})
        _raise_if_auth(resp)
        resp.raise_for_status()
        data = resp.json()
        return data.get("result") or []


async def create_dns_record(zone_id: str, name: str, rtype: str, content: str, proxied: bool = False, ttl: int = 1) -> dict[str, Any]:
    from services.cloudflare_api_key_service import resolve_key

    key = resolve_key()
    if not key:
        raise CloudflareApiUnavailable("cloudflare_api_token_missing")
    zone_id = str(zone_id).strip().replace("\n", "").replace("\r", "")
    payload: dict[str, Any] = {"type": rtype, "name": name, "content": content, "ttl": ttl, "proxied": proxied}
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(f"{API_BASE}/zones/{zone_id}/dns_records", headers=_headers(key), json=payload)
        _raise_if_auth(resp)
        if resp.status_code >= 400:
            try:
                detail = resp.json()
            except Exception:
                detail = resp.text
            raise CloudflareApiUnavailable(f"cloudflare_create_failed: {detail}")
        resp.raise_for_status()
        data = resp.json()
        return data.get("result") or {}


async def delete_dns_record(zone_id: str, record_id: str) -> bool:
    from services.cloudflare_api_key_service import resolve_key

    key = resolve_key()
    if not key:
        raise CloudflareApiUnavailable("cloudflare_api_token_missing")
    zone_id = str(zone_id).strip().replace("\n", "").replace("\r", "")
    record_id = str(record_id).strip().replace("\n", "").replace("\r", "")
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.delete(f"{API_BASE}/zones/{zone_id}/dns_records/{record_id}", headers=_headers(key))
        _raise_if_auth(resp)
        resp.raise_for_status()
        return True
