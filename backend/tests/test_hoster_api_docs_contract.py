"""Die Hoster-API-Doku darf nicht stillschweigend vom Code abweichen.

Ein Shop-Entwickler baut seine Anbindung gegen `docs/hoster-api.md`. Ein neuer
Endpunkt, ein neuer Servicestatus oder ein neues Antwortfeld, das nur im Code
existiert, ist fuer ihn unsichtbar — und ein entfernter Endpunkt, der in der Doku
stehen bleibt, ist schlimmer als gar keine Doku.

Deshalb prueft dieser Test die drei Dinge, die Teil des oeffentlichen Vertrags
sind: Pfade, Statusvokabular und Antwortfelder.
"""

from pathlib import Path

from routers import hoster_admin, hoster_api
from schemas.hoster import HosterServiceResponse
from services.hoster_service_lifecycle import DESIRED_STATES, SERVICE_STATUSES


DOCS = Path(__file__).resolve().parent.parent.parent / "docs" / "hoster-api.md"


def _doc_text() -> str:
    assert DOCS.exists(), f"Die Hoster-API-Referenz fehlt: {DOCS}"
    return DOCS.read_text(encoding="utf-8")


def _routes(router) -> set[tuple[str, str]]:
    """Sammelt (Methode, Pfad) eines Routers ohne die automatischen HEAD/OPTIONS."""
    found: set[tuple[str, str]] = set()
    for route in router.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if not path or not methods:
            continue
        for method in methods:
            if method in {"HEAD", "OPTIONS"}:
                continue
            found.add((method, path))
    return found


def test_every_external_endpoint_is_documented():
    text = _doc_text()
    routes = _routes(hoster_api.router) | _routes(hoster_api.redeem_router)
    assert routes, "Die externe Hoster-API hat keine Routen — Heuristik pruefen."
    missing = sorted(
        f"{method} {path}" for method, path in routes if path not in text
    )
    assert not missing, (
        "Diese externen Endpunkte fehlen in docs/hoster-api.md: " + ", ".join(missing)
    )


def test_every_admin_endpoint_is_documented():
    """Auch die Panel-Endpunkte gehoeren dokumentiert.

    Sie sind kein Teil der Shop-Integration, aber der Betreiber automatisiert
    seine Einrichtung darueber. Ohne Referenz bleibt ihm nur der Quelltext.
    """
    text = _doc_text()
    routes = _routes(hoster_admin.router)
    assert routes, "Der Hoster-Admin-Router hat keine Routen — Heuristik pruefen."
    # Der Doku-Abschnitt listet die Pfade relativ zum gemeinsamen Praefix.
    prefix = hoster_admin.router.prefix
    missing = sorted(
        f"{method} {path}"
        for method, path in routes
        if path not in text and path[len(prefix):] not in text
    )
    assert not missing, (
        "Diese Admin-Endpunkte fehlen in docs/hoster-api.md: " + ", ".join(missing)
    )


def test_every_service_status_is_documented():
    text = _doc_text()
    missing = [status for status in SERVICE_STATUSES if f"service.{status}" not in text]
    assert not missing, (
        "Diese Servicestatus erzeugen einen Webhook, sind aber nicht dokumentiert: "
        + ", ".join(missing)
    )


def test_every_desired_state_is_documented():
    text = _doc_text()
    missing = [state for state in sorted(DESIRED_STATES) if f"`{state}`" not in text]
    assert not missing, (
        "Diese Zielzustaende sind nicht dokumentiert: " + ", ".join(missing)
    )


def test_every_response_field_is_documented():
    text = _doc_text()
    missing = [
        name for name in HosterServiceResponse.model_fields if f"`{name}`" not in text
    ]
    assert not missing, (
        "Diese Antwortfelder der externen API sind nicht dokumentiert: "
        + ", ".join(missing)
    )


def test_signature_example_is_reproducible():
    """Das Rechenbeispiel in der Doku muss mit der echten Implementierung uebereinstimmen.

    Ein Beispiel, das nicht nachrechenbar ist, kostet einen Shop-Entwickler
    Stunden — er sucht den Fehler bei sich, obwohl er in der Doku liegt.
    """
    from services.hoster_webhook_service import sign_payload

    text = _doc_text()
    secret = "whsec_msm_beispiel_nicht_verwenden"
    timestamp = "1786120930"
    body = (
        '{"event":"service.ready","external_service_id":"SVC-4711",'
        '"desired_state":"active","status":"ready","status_code":null,'
        '"server_id":42,"correlation_id":"6f6d9d1e-6b1e-4a51-9f0c-2b7a5d3e8c14",'
        '"terminate_after":null,"updated_at":"2026-08-08T09:22:10+00:00"}'
    )
    expected = sign_payload(secret, timestamp, body)
    assert expected in text, (
        "Das HMAC-Beispiel in docs/hoster-api.md stimmt nicht mehr mit "
        f"sign_payload() ueberein. Erwartet: {expected}"
    )


def test_payload_operational_limits_are_documented():
    """Werte, die das Verhalten beim Empfaenger bestimmen, muessen genannt sein."""
    from services import hoster_webhook_service as webhook

    text = _doc_text()
    assert str(webhook.MAX_ATTEMPTS) in text
    assert str(int(webhook.REQUEST_TIMEOUT_SECONDS)) in text
    assert str(webhook.DELIVERY_RETENTION_DAYS) in text
    for seconds in webhook.RETRY_BACKOFF_SECONDS:
        assert str(seconds) in text, f"Backoff-Abstand {seconds}s ist nicht dokumentiert"
    # Die stille Verwerfung zu grosser Payloads ist fuer den Empfaenger
    # unsichtbar und deshalb der wichtigste Doku-Punkt ueberhaupt.
    assert "16 KiB" in text or "16 KB" in text
