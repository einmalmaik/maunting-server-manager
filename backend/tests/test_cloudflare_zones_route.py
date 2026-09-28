"""GET /api/settings/cloudflare-zones: bekannte Fehler sind keine 500er.

Ohne Token antwortete die Route mit 500 „Zonen konnten nicht geladen werden",
also einem Panelfehler, obwohl nur eine Einstellung fehlte.
"""

from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from services import cloudflare_service
from services.cloudflare_service import CloudflareApiUnavailable

PFAD = "/api/settings/cloudflare-zones"


def test_ohne_token_meldet_die_route_die_fehlende_einstellung(client: TestClient, owner_cookies: dict):
    with patch("services.cloudflare_api_key_service.resolve_key", return_value=None):
        res = client.get(PFAD, cookies=owner_cookies)
    assert res.status_code == 400
    assert res.json()["detail"] == "Kein Cloudflare-API-Token hinterlegt."


def test_abgelehnter_token_ist_eine_einstellungsfrage(client: TestClient, owner_cookies: dict):
    async def abgelehnt():
        raise CloudflareApiUnavailable("cloudflare_api_token_invalid")

    with patch.object(cloudflare_service, "list_zones", abgelehnt):
        res = client.get(PFAD, cookies=owner_cookies)
    assert res.status_code == 400
    assert res.json()["detail"] == "Cloudflare hat den API-Token abgelehnt."


def test_unerreichbares_cloudflare_ist_ein_fehler_dahinter(client: TestClient, owner_cookies: dict):
    async def stumm():
        raise httpx.ConnectError("keine Verbindung")

    with patch.object(cloudflare_service, "list_zones", stumm):
        res = client.get(PFAD, cookies=owner_cookies)
    assert res.status_code == 502
    assert res.json()["detail"] == "Cloudflare ist gerade nicht erreichbar."


def test_fehler_im_panel_wird_nicht_verschluckt(client: TestClient, owner_cookies: dict):
    async def kaputt():
        raise KeyError("result")

    with patch.object(cloudflare_service, "list_zones", kaputt), pytest.raises(KeyError):
        client.get(PFAD, cookies=owner_cookies)


def test_zonen_kommen_unveraendert_durch(client: TestClient, owner_cookies: dict):
    async def zonen():
        return [{"id": "z1", "name": "example.com", "status": "active", "plan": {"name": "Free"}}]

    with patch.object(cloudflare_service, "list_zones", zonen):
        res = client.get(PFAD, cookies=owner_cookies)
    assert res.status_code == 200
    assert res.json() == {"zones": [{"id": "z1", "name": "example.com", "status": "active"}]}
