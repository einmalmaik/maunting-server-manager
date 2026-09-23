"""Das Bild einer Regionsanalyse: welche Ebene die Analyse meldet und wie das
Panel das Bild holt.

Die Zusagen: Ohne Copernicus gibt es trotzdem ein Bild — das Kartenbild, als
Mosaik ohne Zeitpunkt benannt, nie als Szene. Mit Copernicus die neueste
Sentinel-2-Szene mit Zeitpunkt und Quelle. Und der Browser fragt nie ArcGIS
oder Copernicus selbst: `/api/ai/geo/image` holt das Bild, von einer Adresse,
die nie aus der Anfrage stammt.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import Role, RolePermission, User
from services import ai_geo_image_service, ai_geo_service, ai_satellite_service
from services.role_service import set_user_roles

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64

BERLIN = {
    "name": "Berlin, Deutschland",
    "latitude": 52.52,
    "longitude": 13.405,
    "bbox": [13.0883, 52.3382, 13.7611, 52.6755],
    "country": "Deutschland",
}


def _allow_satellite(db: Session, user: User) -> None:
    role = Role(name=f"geo-bild-{user.id}", description=None, is_system=False)
    db.add(role)
    db.flush()
    db.add(RolePermission(role_id=role.id, permission_key="ai.satellite.use"))
    db.commit()
    set_user_roles(db, user, [role.id])


@pytest.fixture(autouse=True)
def _frischer_zustand():
    ai_geo_image_service._cache.clear()
    ai_satellite_service._preview_hrefs.clear()
    yield
    ai_geo_image_service._cache.clear()
    ai_satellite_service._preview_hrefs.clear()
    ai_geo_image_service.shutdown_http_client()


@pytest.fixture
def anbieter(monkeypatch: pytest.MonkeyPatch):
    """Ein Anbieter ohne Netz. Schreibt mit, was das Panel bei ihm abruft."""
    abrufe: list[httpx.Request] = []
    antwort = {"status": 200, "type": "image/jpeg", "body": JPEG}

    def handler(request: httpx.Request) -> httpx.Response:
        abrufe.append(request)
        return httpx.Response(
            antwort["status"], headers={"content-type": antwort["type"]}, content=antwort["body"],
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    monkeypatch.setattr(ai_geo_image_service, "_external_http_client", lambda: client)
    return abrufe, antwort


@pytest.fixture
def stationen(monkeypatch: pytest.MonkeyPatch):
    """Anbieter ohne Netz, die weiterleiten: je Host eine Antwort
    (Status, Köpfe, Inhalt). Schreibt mit, was das Panel abruft."""
    abrufe: list[httpx.Request] = []
    antworten: dict[str, tuple[int, dict[str, str], bytes]] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        abrufe.append(request)
        status, koepfe, inhalt = antworten.get(request.url.host, (404, {}, b""))
        return httpx.Response(status, headers=koepfe, content=inhalt)

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    monkeypatch.setattr(ai_geo_image_service, "_external_http_client", lambda: client)
    return abrufe, antworten


#: So verweist der Katalog auf die Vorschau einer Szene, und dorthin leitet
#: CREODIAS weiter (abgerufen am 23.09.2026).
SZENE = "S2B_MSIL2A_20260922T102019_N0513_R065_T33UUU_20260922T155813"
KATALOG_HREF = "https://datahub.creodias.eu/odata/v1/Assets(10cf3feb-aa3a-418e-9572-f4450250a299)/$value"
DATEI_HREF = "https://zipper.creodias.eu/odata/v1/Assets(10cf3feb-aa3a-418e-9572-f4450250a299)/$value"


# ── Die Ebene der Analyse ──────────────────────────────────────────────────


def test_a_sentinel_scene_becomes_the_image_with_time_and_source() -> None:
    szene = {
        "id": "S2B_MSIL2A_20260912T101559",
        "mission": "Sentinel-2 L2A",
        "datetime": "2026-09-12T10:15:59Z",
        "cloud_cover_percent": 8.2,
        "preview_url": "https://datahub.creodias.eu/odata/v1/Assets(1)/$value",
    }

    ergebnis = ai_geo_service._region_result(BERLIN, None, [szene])

    ebene = ergebnis["satellite"]["layers"]["latest_imagery"]
    assert ebene["kind"] == "scene"
    assert ebene["scene_id"] == "S2B_MSIL2A_20260912T101559"
    assert ebene["captured_at"] == "2026-09-12T10:15:59Z"
    assert ebene["cloud_cover_percent"] == 8.2
    assert ebene["attribution"] == "Copernicus Sentinel data 2026"
    assert ergebnis["satellite"]["scenes"] == [szene]
    assert ergebnis["satellite"]["available"] is True
    # Das Kartenbild steht als zweite Ebene dabei: Ohne MapTiler zoomt die
    # Kamera darauf, eine Szenenvorschau hat nur ihren einen Ausschnitt.
    assert list(ergebnis["satellite"]["layers"]) == ["latest_imagery", "map_imagery"]
    kartenbild = ergebnis["satellite"]["layers"]["map_imagery"]
    assert kartenbild["id"] == "map_imagery"
    assert kartenbild["kind"] == "map"
    assert kartenbild["bbox"] == BERLIN["bbox"]
    assert kartenbild["captured_at"] is None


def test_a_scene_without_preview_is_no_image_and_no_scene() -> None:
    ohne_bild = {"id": "S2A_ohne", "mission": "Sentinel-2 L2A", "datetime": "2026-09-01T10:00:00Z", "preview_url": ""}

    ergebnis = ai_geo_service._region_result(BERLIN, None, [ohne_bild])

    assert list(ergebnis["satellite"]["layers"]) == ["latest_imagery"]
    assert ergebnis["satellite"]["layers"]["latest_imagery"]["kind"] == "map"
    assert ergebnis["satellite"]["layers"]["latest_imagery"]["id"] == "latest_imagery"
    assert ergebnis["satellite"]["scenes"] == []


# ── Der Ausschnitt ─────────────────────────────────────────────────────────


def test_the_image_shows_the_place_box_unchanged() -> None:
    assert ai_geo_image_service.image_bbox(BERLIN["bbox"], 52.52, 13.405) == (13.0883, 52.3382, 13.7611, 52.6755)


def test_a_box_across_the_date_line_becomes_a_frame_around_the_place() -> None:
    # Nominatim gibt Russland mit min_lon > max_lon zurück.
    assert ai_geo_image_service.image_bbox([19.6389, 41.185, -168.9978, 82.0586], 64.69, 97.75) == (
        92.75, 61.69, 102.75, 67.69,
    )


def test_a_huge_box_is_cut_to_a_continent_and_a_point_widened() -> None:
    gross = ai_geo_image_service.image_bbox([-170.0, -60.0, 170.0, 80.0], 10.0, 0.0)
    assert gross == (-30.0, -10.0, 30.0, 30.0)
    punkt = ai_geo_image_service.image_bbox([13.4, 52.5, 13.4, 52.5], 52.5, 13.4)
    assert punkt[2] - punkt[0] == pytest.approx(0.01)
    assert punkt[3] - punkt[1] == pytest.approx(0.01)


def test_the_frame_stays_inside_web_mercator() -> None:
    antarktis = ai_geo_image_service.image_bbox([-180.0, -90.0, 180.0, -60.0], -82.0, 0.0)
    assert antarktis[1] == -85.0
    assert antarktis[3] - antarktis[1] == pytest.approx(30.0)


# ── Die Route ──────────────────────────────────────────────────────────────


def test_the_image_route_needs_the_map_permission(client: TestClient, user_cookies: dict, anbieter) -> None:
    abrufe, _ = anbieter
    antwort = client.get(
        "/api/ai/geo/image", params={"kind": "map", "bbox": "13.0883,52.3382,13.7611,52.6755"}, cookies=user_cookies,
    )

    assert antwort.status_code == 403
    assert abrufe == []


def test_the_panel_fetches_the_map_image_itself_and_keeps_it(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict, anbieter,
) -> None:
    _allow_satellite(db, regular_user)
    abrufe, _ = anbieter
    parameter = {"kind": "map", "bbox": "13.0883,52.3382,13.7611,52.6755"}

    erste = client.get("/api/ai/geo/image", params=parameter, cookies=user_cookies)
    zweite = client.get("/api/ai/geo/image", params=parameter, cookies=user_cookies)

    assert erste.status_code == 200
    assert erste.headers["content-type"] == "image/jpeg"
    # Die Route setzt ihren Kopf selbst, und die Vorgabe für die API
    # (`no-store`) verdrängt ihn nicht: Der Browser darf das Bild eine
    # Viertelstunde behalten, nur er.
    assert erste.headers["cache-control"] == "private, max-age=900"
    assert erste.content == JPEG
    assert zweite.content == JPEG
    # Ein Abruf für beide Anfragen — und genau bei ArcGIS, in Web Mercator.
    assert len(abrufe) == 1
    ziel = abrufe[0].url
    assert ziel.host == "server.arcgisonline.com"
    assert ziel.params["imageSR"] == "3857"
    assert ziel.params["bbox"] == "13.08830,52.33820,13.76110,52.67550"
    assert ziel.params["size"] == "1280,720"


def test_each_image_shape_is_its_own_image(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict, anbieter,
) -> None:
    _allow_satellite(db, regular_user)
    abrufe, _ = anbieter
    ausschnitt = "13.0883,52.3382,13.7611,52.6755"

    for form in ("portrait", "square", "landscape", "portrait"):
        antwort = client.get(
            "/api/ai/geo/image", params={"kind": "map", "bbox": ausschnitt, "shape": form}, cookies=user_cookies,
        )
        assert antwort.status_code == 200
    unbekannt = client.get(
        "/api/ai/geo/image", params={"kind": "map", "bbox": ausschnitt, "shape": "panorama"}, cookies=user_cookies,
    )

    # Jede Form einmal geholt, die zweite Hochform aus dem Zwischenspeicher.
    assert [abruf.url.params["size"] for abruf in abrufe] == ["720,1280", "1024,1024", "1280,720"]
    assert unbekannt.status_code == 422


@pytest.mark.parametrize("bbox",["", "1,2,3", "a,b,c,d", "13.7,52.3,13.0,52.6", "13,95,14,96", "nan,1,2,3"])
def test_the_image_route_rejects_a_bad_frame(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict, anbieter, bbox: str,
) -> None:
    _allow_satellite(db, regular_user)
    abrufe, _ = anbieter

    antwort = client.get("/api/ai/geo/image", params={"kind": "map", "bbox": bbox}, cookies=user_cookies)

    assert antwort.status_code == 422
    assert abrufe == []


@pytest.mark.parametrize(
    ("typ", "inhalt", "code"),
    [
        ("text/html", b"<html>Fehler</html>", "AI_GEO_IMAGE_INVALID"),
        # ArcGIS meldet Fehler auch einmal als "Bild" mit einer Textseite darin.
        ("image/jpeg", b"<html>Fehler</html>", "AI_GEO_IMAGE_INVALID"),
        ("image/png", JPEG, "AI_GEO_IMAGE_INVALID"),
        # Ohne Bildtyp im Kopf entscheidet der Inhalt — und der ist keins.
        ("application/octet-stream", b"<html>Fehler</html>", "AI_GEO_IMAGE_INVALID"),
    ],
)
def test_only_a_real_image_passes(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict, anbieter,
    typ: str, inhalt: bytes, code: str,
) -> None:
    _allow_satellite(db, regular_user)
    _, antwort_vom_anbieter = anbieter
    antwort_vom_anbieter.update(type=typ, body=inhalt)

    antwort = client.get(
        "/api/ai/geo/image", params={"kind": "map", "bbox": "13.0883,52.3382,13.7611,52.6755"}, cookies=user_cookies,
    )

    assert antwort.status_code == 502
    assert antwort.json()["detail"] == code


def test_an_oversized_image_is_refused(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict, anbieter, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_satellite(db, regular_user)
    _, antwort_vom_anbieter = anbieter
    monkeypatch.setattr(ai_geo_image_service, "_MAX_BYTES", len(JPEG) - 1)

    antwort = client.get(
        "/api/ai/geo/image", params={"kind": "map", "bbox": "13.0883,52.3382,13.7611,52.6755"}, cookies=user_cookies,
    )

    assert antwort.status_code == 502
    assert antwort.json()["detail"] == "AI_GEO_IMAGE_TOO_LARGE"


def test_an_unknown_scene_is_not_fetched(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict, anbieter,
) -> None:
    _allow_satellite(db, regular_user)
    abrufe, _ = anbieter

    antwort = client.get("/api/ai/geo/image", params={"kind": "scene", "scene_id": "S2_unbekannt"}, cookies=user_cookies)

    assert antwort.status_code == 404
    assert abrufe == []


def test_a_scene_preview_on_a_foreign_host_is_never_fetched(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict, anbieter,
) -> None:
    # Die Adresse kommt aus der Copernicus-Antwort. Zeigt sie woandershin —
    # ins interne Netz, auf einen fremden Rechner —, holt das Panel nichts.
    _allow_satellite(db, regular_user)
    abrufe, _ = anbieter
    ai_satellite_service._remember_previews([
        {"id": "S2_intern", "preview_url": "https://169.254.169.254/latest/meta-data"},
        {"id": "S2_fremd", "preview_url": "https://dataspace.copernicus.eu.example.org/q.jpg"},
        {"id": "S2_http", "preview_url": "http://catalogue.dataspace.copernicus.eu/q.jpg"},
    ])

    for szene in ("S2_intern", "S2_fremd", "S2_http"):
        antwort = client.get("/api/ai/geo/image", params={"kind": "scene", "scene_id": szene}, cookies=user_cookies)
        assert antwort.status_code == 404

    assert abrufe == []


def test_the_copernicus_token_goes_to_copernicus_only(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict, anbieter, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_satellite(db, regular_user)
    abrufe, antwort_vom_anbieter = anbieter
    antwort_vom_anbieter.update(type="image/png", body=PNG)
    monkeypatch.setattr(ai_satellite_service, "access_token", lambda: "synthetisches-token")
    ai_satellite_service._remember_previews([
        {"id": "S2_katalog", "preview_url": "https://catalogue.dataspace.copernicus.eu/odata/v1/Assets(1)/$value"},
        {"id": "S2_creodias", "preview_url": "https://datahub.creodias.eu/odata/v1/Assets(2)/$value"},
    ])

    katalog = client.get("/api/ai/geo/image", params={"kind": "scene", "scene_id": "S2_katalog"}, cookies=user_cookies)
    creodias = client.get("/api/ai/geo/image", params={"kind": "scene", "scene_id": "S2_creodias"}, cookies=user_cookies)

    assert katalog.status_code == 200
    assert katalog.headers["content-type"] == "image/png"
    assert creodias.status_code == 200
    assert abrufe[0].headers["authorization"] == "Bearer synthetisches-token"
    assert "authorization" not in abrufe[1].headers


# ── Der Weg einer Szenenvorschau ───────────────────────────────────────────


def test_a_scene_preview_is_fetched_through_the_creodias_redirect(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict, stationen, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Die Adresse aus dem Katalog antwortet mit 301, erst das Ziel hat die
    # Datei — als Anhang ohne Bildtyp. Ohne beides kam nie ein Bild an.
    _allow_satellite(db, regular_user)
    abrufe, antworten = stationen
    monkeypatch.setattr(ai_satellite_service, "access_token", lambda: "synthetisches-token")
    antworten["datahub.creodias.eu"] = (301, {"location": DATEI_HREF, "content-type": "text/html"}, b"<html>Moved</html>")
    antworten["zipper.creodias.eu"] = (
        200,
        {"content-type": "application/octet-stream", "content-disposition": f"attachment; filename={SZENE}-ql.jpg"},
        JPEG,
    )
    ai_satellite_service._remember_previews([{"id": SZENE, "preview_url": KATALOG_HREF}])

    antwort = client.get("/api/ai/geo/image", params={"kind": "scene", "scene_id": SZENE}, cookies=user_cookies)

    assert antwort.status_code == 200
    # Hinaus geht der Typ, den der Inhalt belegt — `octet-stream` hielte der
    # Browser für einen Download.
    assert antwort.headers["content-type"] == "image/jpeg"
    assert antwort.content == JPEG
    assert [abruf.url.host for abruf in abrufe] == ["datahub.creodias.eu", "zipper.creodias.eu"]
    # Das Token gehört Copernicus; CREODIAS sieht es an keiner Station.
    assert all("authorization" not in abruf.headers for abruf in abrufe)


@pytest.mark.parametrize(
    "ziel",
    [
        "https://169.254.169.254/latest/meta-data",
        "http://zipper.creodias.eu/odata/v1/Assets(1)/$value",
        "https://zipper.creodias.eu.example.org/q.jpg",
        "https://zipper.creodias.eu:8443/q.jpg",
    ],
)
def test_a_redirect_off_the_list_is_not_followed(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict, stationen, ziel: str,
) -> None:
    # Das Ziel einer Weiterleitung nennt der Anbieter. Es muss dieselbe
    # Prüfung bestehen wie die Adresse aus dem Katalog.
    _allow_satellite(db, regular_user)
    abrufe, antworten = stationen
    antworten["datahub.creodias.eu"] = (302, {"location": ziel}, b"")
    ai_satellite_service._remember_previews([{"id": SZENE, "preview_url": KATALOG_HREF}])

    antwort = client.get("/api/ai/geo/image", params={"kind": "scene", "scene_id": SZENE}, cookies=user_cookies)

    assert antwort.status_code == 502
    assert [abruf.url.host for abruf in abrufe] == ["datahub.creodias.eu"]


def test_a_redirect_loop_ends(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict, stationen,
) -> None:
    _allow_satellite(db, regular_user)
    abrufe, antworten = stationen
    antworten["datahub.creodias.eu"] = (301, {"location": DATEI_HREF}, b"")
    antworten["zipper.creodias.eu"] = (301, {"location": KATALOG_HREF}, b"")
    ai_satellite_service._remember_previews([{"id": SZENE, "preview_url": KATALOG_HREF}])

    antwort = client.get("/api/ai/geo/image", params={"kind": "scene", "scene_id": SZENE}, cookies=user_cookies)

    assert antwort.status_code == 502
    assert len(abrufe) == ai_geo_image_service._MAX_REDIRECTS + 1


def test_the_token_stays_behind_when_copernicus_redirects_to_creodias(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict, stationen, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_satellite(db, regular_user)
    abrufe, antworten = stationen
    monkeypatch.setattr(ai_satellite_service, "access_token", lambda: "synthetisches-token")
    antworten["catalogue.dataspace.copernicus.eu"] = (301, {"location": DATEI_HREF}, b"")
    antworten["zipper.creodias.eu"] = (200, {"content-type": "application/octet-stream"}, PNG)
    ai_satellite_service._remember_previews([
        {"id": SZENE, "preview_url": "https://catalogue.dataspace.copernicus.eu/odata/v1/Assets(1)/$value"},
    ])

    antwort = client.get("/api/ai/geo/image", params={"kind": "scene", "scene_id": SZENE}, cookies=user_cookies)

    assert antwort.status_code == 200
    assert antwort.headers["content-type"] == "image/png"
    assert abrufe[0].headers["authorization"] == "Bearer synthetisches-token"
    assert "authorization" not in abrufe[1].headers


def test_the_map_image_follows_no_redirect(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict, stationen,
) -> None:
    # Die ArcGIS-Adresse baut das Panel selbst; eine Weiterleitung von dort
    # ist nicht vorgesehen und wird nicht verfolgt.
    _allow_satellite(db, regular_user)
    abrufe, antworten = stationen
    antworten["server.arcgisonline.com"] = (302, {"location": "https://server.arcgisonline.com/anderswo.jpg"}, b"")

    antwort = client.get(
        "/api/ai/geo/image", params={"kind": "map", "bbox": "13.0883,52.3382,13.7611,52.6755"}, cookies=user_cookies,
    )

    assert antwort.status_code == 502
    assert len(abrufe) == 1
