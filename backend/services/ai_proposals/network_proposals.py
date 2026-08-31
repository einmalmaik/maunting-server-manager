from __future__ import annotations

import logging
from sqlalchemy.orm import Session
from fastapi import HTTPException
from services.ai_action_errors import AiActionValidationError
from services.ai_proposals.base import _AusfuehrungsRahmen, _Ausgefuehrt

logger = logging.getLogger(__name__)

def _cloudflare_dns_payload(rest: dict) -> tuple[dict, dict]:
    allowed = {"zone_id", "name", "rtype", "content", "proxied"}
    if set(rest) - allowed:
        raise AiActionValidationError("Cloudflare DNS hat ungueltige Argumente")
    zone_id = str(rest.get("zone_id", "")).strip()
    name = str(rest.get("name", "")).strip()[:253]
    rtype = str(rest.get("rtype", "")).strip()
    content = str(rest.get("content", "")).strip()[:253]
    proxied = bool(rest.get("proxied", False))
    if not zone_id:
        from services.panel_settings_service import PanelSettingsService
        zone_id = PanelSettingsService.get("cloudflare_default_zone", "")
    if not zone_id or len(zone_id) > 128:
        raise AiActionValidationError("zone_id fehlt oder ungueltig")
    if not name or rtype not in ("A", "CNAME"):
        raise AiActionValidationError("DNS Record braucht name und rtype A/CNAME")
    if not content:
        raise AiActionValidationError("content fehlt")
    if any(c in name for c in ("\n", "\r", "\0")) or any(c in content for c in ("\n", "\r", "\0")):
        raise AiActionValidationError("Ungueltige Zeichen in DNS Record")
    import re as _re

    if not _re.fullmatch(r"[A-Za-z0-9._\-]+", name):
        raise AiActionValidationError("DNS Name ungueltig")
    payload = {"zone_id": zone_id, "name": name, "rtype": rtype, "content": content, "proxied": proxied}
    preview = {"operation": "cloudflare_dns_create", "zone_id": zone_id, "name": name, "rtype": rtype, "content": content}
    return payload, preview

def _cloudflare_dns_delete_payload(rest: dict) -> tuple[dict, dict]:
    allowed = {"zone_id", "record_id", "record_name", "name"}
    if set(rest) - allowed:
        raise AiActionValidationError("Cloudflare DNS Delete hat ungueltige Argumente")
    zone_id = str(rest.get("zone_id", "")).strip()
    record_id = str(rest.get("record_id", "") or rest.get("record_name", "") or rest.get("name", "")).strip()
    if not record_id:
        raise AiActionValidationError("record_id oder Record-Name erforderlich")
    if not zone_id:
        from services.panel_settings_service import PanelSettingsService
        zone_id = PanelSettingsService.get("cloudflare_default_zone", "")
    payload = {"zone_id": zone_id, "record_id": record_id}
    preview = {"operation": "cloudflare_dns_delete", "zone_id": zone_id, "record_id": record_id}
    return payload, preview

def _ausfuehren_cloudflare_dns(db: Session, rahmen: _AusfuehrungsRahmen) -> _Ausgefuehrt:
    import asyncio as _aio, concurrent.futures

    p = rahmen.payload

    def _run():
        from services.cloudflare_service import create_dns_record

        async def _inner():
            return await create_dns_record(p["zone_id"], p["name"], p["rtype"], p["content"], bool(p.get("proxied", False)))

        try:
            return _aio.run(_inner())
        except RuntimeError:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                return ex.submit(lambda: _aio.run(_inner())).result(timeout=15)

    res = _run()
    return _Ausgefuehrt(result=res if isinstance(res, dict) else {"result": res})

def _ausfuehren_cloudflare_dns_delete(db: Session, rahmen: _AusfuehrungsRahmen) -> _Ausgefuehrt:
    import asyncio as _aio, concurrent.futures

    p = rahmen.payload

    def _run():
        from services.cloudflare_service import delete_dns_record

        async def _inner():
            return await delete_dns_record(p.get("zone_id"), p["record_id"])

        try:
            return _aio.run(_inner())
        except RuntimeError:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                return ex.submit(lambda: _aio.run(_inner())).result(timeout=15)

    res = _run()
    return _Ausgefuehrt(result={"deleted": bool(res), "record_id": p["record_id"]})
