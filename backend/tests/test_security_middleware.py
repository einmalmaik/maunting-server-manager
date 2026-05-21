"""Tests for rate limiting, CSP, CORS, and security headers."""
from fastapi.testclient import TestClient


class TestRateLimiting:
    def test_auth_endpoint_rate_limit(self, client: TestClient):
        """Auth endpoints should be rate-limited to 10 req/min."""
        for i in range(12):
            response = client.post("/api/auth/login", json={
                "username": "nonexistent",
                "password": "wrong",
                "otp_code": None,
            })
            if response.status_code == 429:
                assert "Zu viele Anfragen" in response.json()["detail"]
                assert "Retry-After" in response.headers
                return
        # Should have been rate-limited before 12 requests
        assert False, "Rate limiting did not trigger"

    def test_general_endpoint_rate_limit(self, client: TestClient):
        """General endpoints allow 100 req/min."""
        for i in range(105):
            response = client.get("/api/health")
            if response.status_code == 429:
                return
        assert False, "Rate limiting did not trigger for general endpoints"


class TestSecurityHeaders:
    def test_csp_header_present(self, client: TestClient):
        response = client.get("/api/health")
        assert "Content-Security-Policy" in response.headers
        csp = response.headers["Content-Security-Policy"]
        assert "default-src 'self'" in csp
        assert "script-src 'self'" in csp
        assert "frame-ancestors 'none'" in csp

    def test_x_content_type_options(self, client: TestClient):
        response = client.get("/api/health")
        assert response.headers.get("X-Content-Type-Options") == "nosniff"

    def test_x_frame_options(self, client: TestClient):
        response = client.get("/api/health")
        assert response.headers.get("X-Frame-Options") == "DENY"

    def test_referrer_policy(self, client: TestClient):
        response = client.get("/api/health")
        assert response.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"


class TestCors:
    def test_cors_preflight_allowed(self, client: TestClient):
        response = client.options("/api/health", headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
        })
        assert response.status_code == 200
        assert "access-control-allow-origin" in response.headers

    def test_cors_origin_reflected(self, client: TestClient):
        response = client.get("/api/health", headers={
            "Origin": "http://localhost:3000",
        })
        assert "access-control-allow-origin" in response.headers
