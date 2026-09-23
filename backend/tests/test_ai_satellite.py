"""Tests für das modulare Satelliten- und Regionsanalysesystem (Copernicus / Sentinel).

Prüft Rechte, Tool-Gating, sichere Speicherung, Geocoding und Fehlerbehandlung.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy.orm import Session

from models import Role, RolePermission, User
from services import (
    ai_action_errors,
    ai_action_service,
    ai_geo_service,
    ai_regional_connectors_service,
    ai_satellite_service,
)
from services.role_service import set_user_roles


def _allow_satellite(db: Session, user: User) -> None:
    role = Role(name=f"satellit-{user.id}", description=None, is_system=False)
    db.add(role)
    db.flush()
    db.add(RolePermission(role_id=role.id, permission_key="ai.satellite.use"))
    db.commit()
    set_user_roles(db, user, [role.id])


def test_satellite_without_permission_is_rejected(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ai_satellite_service, "is_configured", lambda: True)

    with pytest.raises(ai_action_errors.AiActionValidationError):
        ai_action_service.execute_read_tool(
            db,
            user=regular_user,
            tool_name="analyze_region",
            arguments={"location": "Berlin"},
        )


@pytest.mark.parametrize("configured", [False, True])
def test_region_tools_are_offered_with_and_without_copernicus(
    monkeypatch: pytest.MonkeyPatch, configured: bool,
) -> None:
    # Ohne Copernicus zeigt die Analyse das schlüsselfreie Kartenbild — früher
    # fehlten Karte, Globus und Wetter ganz, sobald kein Zugang hinterlegt war.
    monkeypatch.setattr(ai_satellite_service, "is_configured", lambda: configured)

    names = {item["function"]["name"] for item in ai_action_service.provider_tool_definitions()}
    assert "analyze_region" in names
    assert "control_region_camera" in names


def test_region_camera_control_is_permission_checked_and_does_not_fetch(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ai_action_errors.AiActionValidationError):
        ai_action_service.execute_read_tool(
            db,
            user=regular_user,
            tool_name="control_region_camera",
            arguments={"action": "zoom_in"},
        )

    _allow_satellite(db, regular_user)
    monkeypatch.setattr(
        ai_geo_service,
        "geocode_location",
        lambda _location: pytest.fail("Ein relativer Zoom darf keine Geodaten abrufen"),
    )
    result = ai_action_service.execute_read_tool(
        db,
        user=regular_user,
        tool_name="control_region_camera",
        arguments={"action": "zoom_in"},
    )

    assert result["action"] == "zoom_in"
    assert isinstance(result["command_id"], str)
    assert result["command_id"]


def test_region_camera_focus_geocodes_only_the_landmark(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_satellite(db, regular_user)
    requested: list[str] = []

    def geocode(location: str) -> dict:
        requested.append(location)
        return {
            "name": "Verbotene Stadt, Peking",
            "country": "China",
            "latitude": 39.9163,
            "longitude": 116.3972,
            "bbox": [116.3872, 39.9063, 116.4072, 39.9263],
        }

    monkeypatch.setattr(ai_geo_service, "geocode_location", geocode)
    result = ai_action_service.execute_read_tool(
        db,
        user=regular_user,
        tool_name="control_region_camera",
        arguments={"action": "focus_location", "location": "Verbotene Stadt, Peking"},
    )

    assert requested == ["Verbotene Stadt, Peking"]
    assert result["action"] == "focus_location"
    assert result["location"] == "Verbotene Stadt, Peking"
    assert result["coordinates"]["latitude"] == 39.9163


def test_region_camera_focus_validates_action_specific_arguments(
    db: Session, regular_user: User,
) -> None:
    _allow_satellite(db, regular_user)

    with pytest.raises(ai_action_errors.AiActionValidationError):
        ai_action_service.execute_read_tool(
            db,
            user=regular_user,
            tool_name="control_region_camera",
            arguments={"action": "focus_location"},
        )
    with pytest.raises(ai_action_errors.AiActionValidationError):
        ai_action_service.execute_read_tool(
            db,
            user=regular_user,
            tool_name="control_region_camera",
            arguments={"action": "zoom_in", "zoom_level": 3},
        )


@pytest.mark.parametrize("ort", ["Berlin", "", None])
def test_a_zoom_with_a_place_is_still_a_zoom(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch, ort: str | None,
) -> None:
    """Ein Ort zum Zoom wird übergangen, nicht abgewiesen: Abgewiesene
    Kameraaufrufe wiederholte GPT-Live, bis das Tokenlimit griff
    (Sprachprobe 23.09.2026)."""
    _allow_satellite(db, regular_user)
    monkeypatch.setattr(
        ai_geo_service,
        "geocode_location",
        lambda _location: pytest.fail("Ein relativer Zoom darf keine Geodaten abrufen"),
    )

    result = ai_action_service.execute_read_tool(
        db,
        user=regular_user,
        tool_name="control_region_camera",
        arguments={"action": "zoom_in", "location": ort},
    )

    assert result["action"] == "zoom_in"
    assert "location" not in result
    assert "coordinates" not in result


def test_store_and_retrieve_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_store = {}

    from services.panel_settings_service import PanelSettingsService
    from services.auth_service import AuthService

    monkeypatch.setattr(PanelSettingsService, "get", lambda k, default="": fake_store.get(k, default))
    monkeypatch.setattr(PanelSettingsService, "set", lambda k, v: fake_store.__setitem__(k, v))
    monkeypatch.setattr(AuthService, "encrypt_secret", lambda plain, aad=None: f"enc:{plain}")
    monkeypatch.setattr(AuthService, "decrypt_secret", lambda enc, aad=None: enc.replace("enc:", "", 1) if enc.startswith("enc:") else "")

    assert ai_satellite_service.is_configured() is False
    ai_satellite_service.store_credentials("client_123", "secret_abc")
    assert ai_satellite_service.is_configured() is True
    creds = ai_satellite_service.get_credentials()
    assert creds == {"client_id": "client_123", "client_secret": "secret_abc"}

    ai_satellite_service.store_credentials("", "")
    assert ai_satellite_service.is_configured() is False


def _copernicus_ohne_netz(monkeypatch: pytest.MonkeyPatch, client: object) -> None:
    ai_satellite_service.shutdown_http_client()
    ai_satellite_service._token_cache.clear()
    ai_satellite_service._search_cache.clear()
    ai_satellite_service._preview_hrefs.clear()
    monkeypatch.setattr(ai_satellite_service, "get_credentials", lambda: {"client_id": "client", "client_secret": "secret"})
    monkeypatch.setattr(ai_satellite_service, "_external_http_client", lambda: client)


def test_satellite_search_coalesces_token_and_short_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class FakeClient:
        def post(self, url, **_kwargs):
            calls.append(url)
            if url == ai_satellite_service._TOKEN_ENDPOINT:
                return httpx.Response(200, json={"access_token": "synthetic-token", "expires_in": 300})
            return httpx.Response(200, json={"features": []})

    _copernicus_ohne_netz(monkeypatch, FakeClient())

    assert ai_satellite_service.search_satellite_imagery(52.52, 13.405) == []
    assert ai_satellite_service.search_satellite_imagery(52.52, 13.405) == []

    assert calls == [ai_satellite_service._TOKEN_ENDPOINT, ai_satellite_service._STAC_ENDPOINT]


#: Die Antwort des Katalogs auf genau diese Suche (Berlin, abgerufen am
#: 23.09.2026). Gekürzt sind nur die Nachkommastellen der Kachelumrisse und
#: die Links.
_STAC_ANTWORT_BERLIN = {
    "type": "FeatureCollection",
    "numberReturned": 2,
    "features": [
        {
            "id": "S2B_MSIL2A_20260922T102019_N0513_R065_T33UUU_20260922T155813",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[12.0048, 53.212], [12.0716, 52.2262], [13.6785, 52.2552], [13.6483, 53.242], [12.0048, 53.212]]],
            },
            "properties": {"datetime": "2026-09-22T10:20:19.024000Z", "eo:cloud_cover": 7.38},
            "assets": {
                "thumbnail": {"href": "https://datahub.creodias.eu/odata/v1/Assets(10cf3feb-aa3a-418e-9572-f4450250a299)/$value"},
            },
            "links": [{
                "rel": "self", "type": "application/geo+json",
                "href": "https://stac.dataspace.copernicus.eu/v1/collections/sentinel-2-l2a/items/S2B_MSIL2A_20260922T102019_N0513_R065_T33UUU_20260922T155813",
            }],
        },
        {
            "id": "S2B_MSIL2A_20260922T102019_N0513_R065_T32UQD_20260922T155813",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[11.9946, 53.212], [11.9278, 52.2262], [13.531, 52.1755], [13.6342, 53.1594], [11.9946, 53.212]]],
            },
            "properties": {"datetime": "2026-09-22T10:20:19.024000Z", "eo:cloud_cover": 6.91},
            "assets": {
                "thumbnail": {"href": "https://datahub.creodias.eu/odata/v1/Assets(ee5995e3-ff61-43dc-850d-04c98a0252d0)/$value"},
            },
            "links": [],
        },
    ],
    "links": [{"rel": "next", "type": "application/geo+json", "method": "POST", "href": "https://stac.dataspace.copernicus.eu/v1/search"}],
}


def test_the_search_asks_the_current_catalogue_at_the_place(monkeypatch: pytest.MonkeyPatch) -> None:
    # Bis 09/2026 fragte der Dienst den abgelösten Katalog nach `SENTINEL-2`.
    # Den gibt es dort nicht mehr; jede Suche endete mit 400, und mit
    # Copernicus-Zugang erschien nie eine Szene.
    anfragen: list[tuple[str, dict, dict]] = []

    class FakeClient:
        def post(self, url, **kwargs):
            anfragen.append((url, kwargs.get("json") or {}, kwargs.get("headers") or {}))
            if url == ai_satellite_service._TOKEN_ENDPOINT:
                return httpx.Response(200, json={"access_token": "synthetisches-token", "expires_in": 600})
            return httpx.Response(200, json=_STAC_ANTWORT_BERLIN)

    _copernicus_ohne_netz(monkeypatch, FakeClient())

    szenen = ai_satellite_service.search_satellite_imagery(latitude=52.52, longitude=13.405, limit=2)

    url, suche, koepfe = anfragen[-1]
    assert url == "https://stac.dataspace.copernicus.eu/v1/search"
    assert suche["collections"] == ["sentinel-2-l2a"]
    # Am Ort, nicht in einer Box um ihn: Jede Kachel enthält dann den Ort.
    assert suche["intersects"] == {"type": "Point", "coordinates": [13.405, 52.52]}
    assert "bbox" not in suche
    assert suche["filter-lang"] == "cql2-json"
    assert suche["filter"] == {"op": "<=", "args": [{"property": "eo:cloud_cover"}, 30.0]}
    assert suche["sortby"] == [{"field": "properties.datetime", "direction": "desc"}]
    assert suche["limit"] == 2
    beginn, ende = suche["datetime"].split("/")
    assert ende == ".."
    alter = datetime.now(timezone.utc) - datetime.strptime(beginn, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    assert timedelta(days=89) < alter < timedelta(days=91)
    assert "assets.thumbnail.href" in suche["fields"]["include"]
    assert koepfe["Authorization"] == "Bearer synthetisches-token"

    assert [szene["id"] for szene in szenen] == [
        "S2B_MSIL2A_20260922T102019_N0513_R065_T33UUU_20260922T155813",
        "S2B_MSIL2A_20260922T102019_N0513_R065_T32UQD_20260922T155813",
    ]
    erste = szenen[0]
    assert erste["datetime"] == "2026-09-22T10:20:19.024000Z"
    assert erste["cloud_cover_percent"] == 7.4
    assert erste["preview_url"] == "https://datahub.creodias.eu/odata/v1/Assets(10cf3feb-aa3a-418e-9572-f4450250a299)/$value"
    assert erste["geometry"]["type"] == "Polygon"
    # Das Bild dazu holt später das Panel — von genau dieser Adresse.
    assert ai_satellite_service.preview_href(erste["id"]) == erste["preview_url"]


def test_only_the_thumbnail_becomes_the_preview(monkeypatch: pytest.MonkeyPatch) -> None:
    # Ignoriert ein Server die Feldauswahl, kommen alle 47 Assets mit. Eine
    # Vorschau ist nur `thumbnail`; fehlt sie, gibt es keine. Das
    # Echtfarbenband ist ein JPEG 2000 von 60 bis 135 MB, das Produkt ein
    # Archiv von über 1 GB.
    ohne_vorschau = {
        "type": "FeatureCollection",
        "features": [{
            "id": "S2A_MSIL2A_20260920T101031_N0513_R022_T33UUU_20260920T140000",
            "geometry": None,
            "properties": {"datetime": "2026-09-20T10:10:31.024000Z", "eo:cloud_cover": 3.1},
            "assets": {
                "Product": {"href": "https://download.dataspace.copernicus.eu/odata/v1/Products(1)/$value"},
                "TCI_10m": {"href": "s3://eodata/Sentinel-2/MSI/L2A/2026/09/20/TCI_10m.jp2", "type": "image/jp2"},
            },
        }],
    }

    class FakeClient:
        def post(self, url, **_kwargs):
            if url == ai_satellite_service._TOKEN_ENDPOINT:
                return httpx.Response(200, json={"access_token": "synthetisches-token", "expires_in": 600})
            return httpx.Response(200, json=ohne_vorschau)

    _copernicus_ohne_netz(monkeypatch, FakeClient())

    szenen = ai_satellite_service.search_satellite_imagery(latitude=52.52, longitude=13.405)

    assert szenen[0]["preview_url"] == ""
    assert ai_satellite_service.preview_href(szenen[0]["id"]) is None


def test_geo_service_analyze_region(db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    _allow_satellite(db, regular_user)
    monkeypatch.setattr(ai_satellite_service, "is_configured", lambda: False)

    result = ai_action_service.execute_read_tool(
        db,
        user=regular_user,
        tool_name="analyze_region",
        arguments={"location": "Berlin"},
    )

    assert result["status"] == "success"
    assert "Berlin" in result["location"]
    assert result["coordinates"]["latitude"] == 52.52
    assert result["coordinates"]["longitude"] == 13.405
    assert "weather" in result
    assert "satellite" in result
    assert result["satellite"]["available"] is True
    assert "layers" in result["satellite"]
    layers = result["satellite"]["layers"]
    assert list(layers) == ["latest_imagery"]
    latest_imagery = layers["latest_imagery"]
    # Ohne Copernicus das Kartenbild — ausdrücklich keine Szene: kein
    # Aufnahmezeitpunkt, und in `scenes` steht nichts, das nach Überflug aussieht.
    assert latest_imagery["kind"] == "map"
    assert latest_imagery["name"] == "Kartenbild"
    assert latest_imagery["captured_at"] is None
    assert latest_imagery["scene_id"] is None
    assert "Überflug" in latest_imagery["description"]
    assert latest_imagery["attribution"] == "Esri, Vantor, Earthstar Geographics, and the GIS User Community"
    assert result["satellite"]["scenes"] == []
    assert "arcgisonline" in latest_imagery["url"]
    # Web Mercator wie jede Webkarte; in Grad wäre Berlin in die Breite gezogen.
    assert "imageSR=3857" in latest_imagery["url"]
    assert latest_imagery["bbox"] == [13.0883, 52.3382, 13.7611, 52.6755]
    assert latest_imagery["mission"] == "ArcGIS World Imagery"
    assert latest_imagery["resolution"] == "anbieterabhängig"
    assert result["news"] == []
    assert result["news_status"] == "not_allowed"
    assert result["camera"]["mode"] == "focus"
    assert isinstance(result["camera"]["command_id"], str)


def test_chat_region_initial_skips_optional_connectors(db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    _allow_satellite(db, regular_user)
    monkeypatch.setattr(ai_satellite_service, "is_configured", lambda: False)
    monkeypatch.setattr(
        ai_regional_connectors_service, "traffic",
        lambda *_args, **_kwargs: pytest.fail("traffic must not block the first chat result"),
    )
    monkeypatch.setattr(
        ai_regional_connectors_service, "public_posts",
        lambda *_args, **_kwargs: pytest.fail("public posts must not block the first chat result"),
    )

    result = ai_action_service.execute_read_tool(
        db,
        user=regular_user,
        tool_name="analyze_region",
        arguments={"location": "Berlin"},
        fast_region=True,
    )

    assert result["status"] == "success"
    assert "traffic" not in result
    assert result["news_status"] == "pending"
