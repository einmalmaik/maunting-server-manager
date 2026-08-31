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


async def _resolve_zone_id(zone_or_name: str | None) -> str:
    target = (zone_or_name or "").strip().replace("\n", "").replace("\r", "").lower()
    zones = await list_zones()
    if not zones:
        raise CloudflareApiUnavailable("no_zones_found")

    # 1. Direkte Übereinstimmung mit Zonen-ID
    if target:
        for z in zones:
            if str(z.get("id", "")).lower() == target:
                return str(z["id"])

    # 2. Direkte Übereinstimmung mit Zonen-Name (z.B. "mauntingstudios.de")
    if target:
        for z in zones:
            if z.get("name", "").lower() == target:
                return str(z["id"])

        # 3. Subdomain- / Suffix-Matching (z.B. target="test.mauntingstudios.de" -> zone="mauntingstudios.de")
        for z in zones:
            z_name = z.get("name", "").lower()
            if z_name and (target.endswith("." + z_name) or z_name in target):
                return str(z["id"])

    # 4. Standard-Zone aus den Panel-Einstellungen
    from services.panel_settings_service import PanelSettingsService
    default_zone = PanelSettingsService.get("cloudflare_default_zone", "").strip().lower()
    if default_zone:
        for z in zones:
            if z.get("name", "").lower() == default_zone or str(z.get("id", "")).lower() == default_zone:
                return str(z["id"])

    # 5. Falls genau 1 Zone vorhanden ist, nimm diese
    if len(zones) == 1:
        return str(zones[0]["id"])

    # 6. Automatischer Fallback auf die erste verfügbare Zone
    if zones:
        return str(zones[0]["id"])

    raise CloudflareApiUnavailable("zone_id_missing")


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


async def list_dns_records(zone_id: str | None = None) -> list[dict[str, Any]]:
    from services.cloudflare_api_key_service import resolve_key

    key = resolve_key()
    if not key:
        raise CloudflareApiUnavailable("cloudflare_api_token_missing")
    resolved_id = await _resolve_zone_id(zone_id)
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(f"{API_BASE}/zones/{resolved_id}/dns_records", headers=_headers(key), params={"per_page": 100})
        _raise_if_auth(resp)
        resp.raise_for_status()
        data = resp.json()
        return data.get("result") or []


async def create_dns_record(zone_id: str | None, name: str, rtype: str, content: str, proxied: bool = False, ttl: int = 1) -> dict[str, Any]:
    from services.cloudflare_api_key_service import resolve_key

    key = resolve_key()
    if not key:
        raise CloudflareApiUnavailable("cloudflare_api_token_missing")
    resolved_id = await _resolve_zone_id(zone_id)
    payload: dict[str, Any] = {"type": rtype, "name": name, "content": content, "ttl": ttl, "proxied": proxied}
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(f"{API_BASE}/zones/{resolved_id}/dns_records", headers=_headers(key), json=payload)
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


async def delete_dns_record(zone_id: str | None, record_id: str) -> bool:
    from services.cloudflare_api_key_service import resolve_key

    key = resolve_key()
    if not key:
        raise CloudflareApiUnavailable("cloudflare_api_token_missing")
    resolved_id = await _resolve_zone_id(zone_id)
    record_id = str(record_id).strip().replace("\n", "").replace("\r", "")

    # Falls record_id keine 32-Zeichen-Hex-ID ist (sondern ein Name wie test.mauntingstudios.de oder test), auflösen
    if len(record_id) != 32 or "." in record_id or not all(c in "0123456789abcdefABCDEF" for c in record_id):
        existing = await list_dns_records(resolved_id)
        target_name = record_id.lower()
        matched_id = None
        for r in existing:
            r_name = str(r.get("name", "")).lower()
            if r_name == target_name or r_name.startswith(target_name + ".") or str(r.get("id", "")).lower() == target_name:
                matched_id = str(r.get("id"))
                break
        if matched_id:
            record_id = matched_id
        else:
            raise CloudflareApiUnavailable(f"dns_record_not_found: {record_id}")

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.delete(f"{API_BASE}/zones/{resolved_id}/dns_records/{record_id}", headers=_headers(key))
        _raise_if_auth(resp)
        resp.raise_for_status()
        return True

