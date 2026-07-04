"""Tests for EmailService: template logo embedding and email helper validation."""
import pytest
from unittest.mock import AsyncMock, patch

from services.email_service import EmailService
from config import settings


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
        assert EmailService.HEADER_COLOR in template

    def test_logo_url_falls_back_to_png(self):
        previous = settings.logo_url
        settings.logo_url = ""
        try:
            logo_url = EmailService._get_logo_url()
            assert logo_url.endswith("/logo.png")
        finally:
            settings.logo_url = previous

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
        assert "testuser" not in html

    def test_notification_email_html_contains_logo(self):
        html = EmailService._notification_email_html("testuser", "Notice", "Test message")
        assert "data:image/png;base64," in html
        assert EmailService.LOGO_BASE64 in html
        assert "Notice" in html
        assert "Test message" in html


class TestEmailSendingHelpers:
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
        assert "testuser" not in html_body

    @pytest.mark.anyio
    @patch("services.email_service.EmailService.send_email", new_callable=AsyncMock)
    async def test_send_oauth_linked_notification(self, mock_send_email):
        mock_send_email.return_value = True
        success = await EmailService.send_oauth_linked_notification("user@test.de", "testuser", "Google")
        assert success is True
        mock_send_email.assert_called_once()
        args, _ = mock_send_email.call_args
        assert args[0] == "user@test.de"
        assert "Google verknüpft" in args[1]
        html = args[3] if len(args) > 3 else None
        assert html is not None
        assert "Google" in html
        assert "data:image/png;base64," in html

    @pytest.mark.anyio
    @patch("services.email_service.EmailService.send_email", new_callable=AsyncMock)
    async def test_send_oauth_unlinked_notification(self, mock_send_email):
        mock_send_email.return_value = True
        success = await EmailService.send_oauth_unlinked_notification("user@test.de", "testuser", "Discord")
        assert success is True
        args, _ = mock_send_email.call_args
        assert "Discord Verknüpfung aufgehoben" in args[1]

    @pytest.mark.anyio
    @patch("services.email_service.EmailService.send_email", new_callable=AsyncMock)
    async def test_send_account_registered_notification(self, mock_send_email):
        mock_send_email.return_value = True
        success = await EmailService.send_account_registered_notification("user@test.de", "newuser")
        assert success is True
        args, _ = mock_send_email.call_args
        assert "Konto erfolgreich erstellt" in args[1]
        html = args[3] if len(args) > 3 else None
        assert "Konto erfolgreich erstellt" in (html or "")

    def test_smtp_resend_decryption(self):
        from services.auth_service import AuthService
        from services.panel_settings_service import PanelSettingsService
        
        # Test SMTP Password
        enc_smtp = AuthService.encrypt_secret("secret-smtp-pass", aad="msm:settings:smtp_password")
        PanelSettingsService.set("smtp_password_encrypted", enc_smtp)
        assert EmailService._get_setting("smtp_password") == "secret-smtp-pass"
        
        # Test Resend API Key
        enc_resend = AuthService.encrypt_secret("secret-resend-key", aad="msm:settings:resend_api_key")
        PanelSettingsService.set("resend_api_key_encrypted", enc_resend)
        assert EmailService._get_setting("resend_api_key") == "secret-resend-key"
