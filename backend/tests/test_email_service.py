"""Tests for EmailService: template logo embedding and email helper validation."""
import pytest
from unittest.mock import AsyncMock, patch

from services.email_service import EmailService


class TestEmailServiceTemplates:
    def test_logo_html_contains_base64_logo(self):
        logo_html = EmailService._logo_html()
        assert "data:image/png;base64," in logo_html
        assert EmailService.LOGO_BASE64 in logo_html
        assert "width=\"120\"" in logo_html
        assert "<img" in logo_html

    def test_base_template_contains_logo(self):
        template = EmailService._base_template("Test Title", "<p>Test Content</p>")
        assert "data:image/png;base64," in template
        assert EmailService.LOGO_BASE64 in template
        assert "MauntingStudios" in template
        assert "INFRASTRUCTURE CONTROL" in template
        assert "#0f172a" in template  # Header background color

    def test_verification_email_html_contains_logo(self):
        html = EmailService._verification_email_html("testuser", "http://test-url/verify")
        assert "data:image/png;base64," in html
        assert EmailService.LOGO_BASE64 in html
        assert "http://test-url/verify" in html

    def test_password_reset_email_html_contains_logo(self):
        html = EmailService._password_reset_email_html("testuser", "http://test-url/reset")
        assert "data:image/png;base64," in html
        assert EmailService.LOGO_BASE64 in html
        assert "http://test-url/reset" in html

    def test_verification_code_email_html_contains_logo(self):
        html = EmailService._verification_code_email_html("testuser", "123456")
        assert "data:image/png;base64," in html
        assert EmailService.LOGO_BASE64 in html
        assert "123456" in html

    def test_notification_email_html_contains_logo(self):
        html = EmailService._notification_email_html("testuser", "Notice", "Test message")
        assert "data:image/png;base64," in html
        assert EmailService.LOGO_BASE64 in html
        assert "Notice" in html
        assert "Test message" in html


class TestEmailSendingHelpers:
    @pytest.mark.anyio
    @patch("services.email_service.EmailService.send_email", new_callable=AsyncMock)
    async def test_send_verification_email_passes_logo(self, mock_send_email):
        mock_send_email.return_value = True
        success = await EmailService.send_verification_email("user@test.de", "testuser", "test_token")
        
        assert success is True
        mock_send_email.assert_called_once()
        args, kwargs = mock_send_email.call_args
        
        # Check that recipient is correct
        assert args[0] == "user@test.de"
        # Check that subject has the prefix
        assert "E-Mail verifizieren" in args[1]
        # Check that HTML body has the base64 logo
        html_body = args[3] if len(args) > 3 else kwargs.get("html")
        assert html_body is not None
        assert "data:image/png;base64," in html_body
        assert EmailService.LOGO_BASE64 in html_body
        assert "test_token" in html_body

    @pytest.mark.anyio
    @patch("services.email_service.EmailService.send_email", new_callable=AsyncMock)
    async def test_send_password_reset_email_passes_logo(self, mock_send_email):
        mock_send_email.return_value = True
        success = await EmailService.send_password_reset_email("user@test.de", "testuser", "reset_token")
        
        assert success is True
        mock_send_email.assert_called_once()
        args, kwargs = mock_send_email.call_args
        
        assert args[0] == "user@test.de"
        assert "Passwort zurücksetzen" in args[1]
        html_body = args[3] if len(args) > 3 else kwargs.get("html")
        assert html_body is not None
        assert "data:image/png;base64," in html_body
        assert EmailService.LOGO_BASE64 in html_body
        assert "reset_token" in html_body

    @pytest.mark.anyio
    @patch("services.email_service.EmailService.send_email", new_callable=AsyncMock)
    async def test_send_verification_code_email_passes_logo(self, mock_send_email):
        mock_send_email.return_value = True
        success = await EmailService.send_verification_code_email("user@test.de", "testuser", "987654")
        
        assert success is True
        mock_send_email.assert_called_once()
        args, kwargs = mock_send_email.call_args
        
        assert args[0] == "user@test.de"
        assert "Verifizierungscode" in args[1]
        html_body = args[3] if len(args) > 3 else kwargs.get("html")
        assert html_body is not None
        assert "data:image/png;base64," in html_body
        assert EmailService.LOGO_BASE64 in html_body
        assert "987654" in html_body
