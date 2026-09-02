"""Das OpenAPI-Schema gehoert hinter einen Login, und /docs gehoert der SPA.

Zwei Befunde in einem:

- FastAPI registriert seine Doku-Routen im Konstruktor, der SPA-Mount auf "/"
  kommt erst am Ende von `main.py`. Starlette matcht in Registrierungsreihenfolge,
  also lieferte `/docs` im Single-Host-Betrieb die Swagger-UI statt der
  Doku-Seite des Panels.
- `/openapi.json` hatte keinerlei Auth-Dependency und gab damit anonym das
  vollstaendige Schema heraus, inklusive aller Hoster-Verwaltungsendpunkte und
  des Namens des API-Key-Headers.
"""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import Role, RolePermission, User
from services.role_service import set_user_roles


def test_default_openapi_paths_are_gone(client: TestClient) -> None:
    """Weder /openapi.json noch /docs oder /redoc duerfen weiter existieren.

    Sonst waere die Route weiterhin vor dem SPA-Mount registriert und die
    Verschiebung nach /api/* haette nichts geaendert.
    """
    for path in ("/openapi.json", "/docs", "/redoc"):
        response = client.get(path)
        assert response.status_code == 404, f"{path} antwortet weiterhin mit Swagger"


def test_openapi_schema_requires_a_session(client: TestClient) -> None:
    response = client.get("/api/openapi.json")

    assert response.status_code in (401, 403)


def test_openapi_schema_requires_the_settings_permission(
    client: TestClient,
    db: Session,
    regular_user: User,
    user_cookies: dict,
) -> None:
    """Angemeldet allein reicht nicht — sonst saehe jeder Kunde das Schema."""
    role = Role(name="no-settings", description=None, is_system=False)
    db.add(role)
    db.flush()
    db.add(RolePermission(role_id=role.id, permission_key="ai.chat.use"))
    db.commit()
    set_user_roles(db, regular_user, [role.id])

    response = client.get("/api/openapi.json", cookies=user_cookies)

    assert response.status_code == 403


def test_openapi_schema_is_served_with_the_permission(
    client: TestClient,
    owner_cookies: dict,
) -> None:
    response = client.get("/api/openapi.json", cookies=owner_cookies)

    assert response.status_code == 200
    schema = response.json()
    assert schema["openapi"].startswith("3.")
    # Stichprobe: die externen und die verwaltenden Hoster-Pfade sind enthalten.
    assert "/api/hoster/v1/services/{external_service_id}" in schema["paths"]
    assert "/api/hoster/integrations" in schema["paths"]


def test_swagger_ui_is_gated_and_gets_its_own_csp(
    client: TestClient,
    owner_cookies: dict,
) -> None:
    """Ohne die CSP-Ausnahme waere die Seite leer statt nutzbar."""
    anonymous = client.get("/api/docs")
    assert anonymous.status_code in (401, 403)

    response = client.get("/api/docs", cookies=owner_cookies)
    assert response.status_code == 200
    assert "swagger-ui" in response.text
    csp = response.headers["Content-Security-Policy"]
    assert "https://cdn.jsdelivr.net" in csp


def test_other_responses_keep_the_strict_csp(client: TestClient) -> None:
    """Die CDN-Freigabe gilt ausschliesslich fuer die beiden Doku-Pfade."""
    response = client.get("/api/health")

    assert response.status_code == 200
    assert "https://cdn.jsdelivr.net" not in response.headers["Content-Security-Policy"]
