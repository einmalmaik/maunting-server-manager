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

    @pytest.mark.anyio
    @patch("services.email_service.EmailService.send_email", new_callable=AsyncMock)
    async def test_send_guardian_incident_notification(self, mock_send_email):
        mock_send_email.return_value = True
        success = await EmailService.send_guardian_incident_notification(
            "admin@test.de", "admin", "Palworld Server", "CrashLoop", "quarantined", "Process crashed 3 times"
        )
        assert success is True
        args, _ = mock_send_email.call_args
        assert args[0] == "admin@test.de"
        assert "Guardian Alert: Palworld Server" in args[1]
        body = args[2]
        assert "CrashLoop" in body
        assert "quarantined" in body
        assert "Process crashed 3 times" in body


class TestUmlauteInKiMails:
    """Die KI-Mails schreiben Deutsch, nicht Ersatzdeutsch.

    Aus dem Betrieb: in einer einzigen Berichtsmail standen "faellig" und
    "durchgeführt" nebeneinander. Der Grund war banal — die KI-Texte waren neu
    und in Ersatzschreibung getippt, die Vorlage darunter war aelter und
    richtig. Es sah aus wie ein Notbehelf gegen eine kaputte Kodierung, war aber
    keiner.

    Diese Tests halten beides fest: dass der Text Umlaute hat, und dass der
    Versandweg sie traegt. Das Zweite ist das wichtigere — ohne es waere das
    Erste eine Verschlimmbesserung.
    """

    @pytest.mark.anyio
    @patch("services.email_service.EmailService.send_email", new_callable=AsyncMock)
    async def test_the_task_report_is_written_in_german(self, mock_send_email):
        mock_send_email.return_value = True
        await EmailService.send_ai_task_report(
            "admin@test.de", "admin",
            task_title="Serverstatus", plan_text="täglich um 18:00",
            geschafft=True, bericht="Alles läuft.",
        )
        _to, _subject, body, html = mock_send_email.call_args[0]
        for text in (body, html):
            assert "fällig" in text
            assert "vollständigen" in text
            assert "faellig" not in text
            assert "vollstaendigen" not in text

    @pytest.mark.anyio
    @patch("services.email_service.EmailService.send_email", new_callable=AsyncMock)
    async def test_the_guardian_report_is_written_in_german(self, mock_send_email):
        mock_send_email.return_value = True
        await EmailService.send_ai_healing_report(
            "admin@test.de", "admin",
            server_name="Palworld", incident_type="CrashLoop",
            geheilt=True, bericht="Neu gestartet.", backup_name=None,
        )
        _to, _subject, body, html = mock_send_email.call_args[0]
        for text in (body, html):
            assert "Störung" in text
            assert "eigenständig" in text
            assert "Stoerung" not in text

    def test_the_transport_carries_umlauts_in_body_and_subject(self):
        """Der eigentliche Beweis: eine fertige Nachricht, so wie sie hinausgeht.

        Gebaut wie in ``_send_smtp``, nur ohne Versand. Der Betreff mit Umlaut
        muss nach RFC 2047 kodiert herauskommen (sonst zerlegt ihn der
        Empfaenger), der Text muss den Umlaut nach dem Dekodieren wieder
        hergeben.
        """
        from email.message import EmailMessage

        msg = EmailMessage()
        msg["From"] = "panel@example.com"
        msg["To"] = "admin@test.de"
        msg["Subject"] = "Maunting Server Manager — KI-Aufgabe erledigt: Prüfung"
        msg.set_content("Deine KI-Aufgabe war fällig. Alles läuft.")

        roh = msg.as_bytes()
        # Der Betreff steht RFC-2047-kodiert im Kopf und nicht als rohes UTF-8.
        # Das ist der Teil, der ohne Kodierung wirklich kaputtgeht: Kopfzeilen
        # sind ASCII, ein Umlaut darin kommt beim Empfaenger zerlegt an.
        betreffzeile = next(
            zeile for zeile in roh.splitlines() if zeile.startswith(b"Subject:")
        )
        assert betreffzeile.isascii()
        assert b"=?utf-8?" in betreffzeile.lower()

        # Der Rumpf darf dagegen echtes UTF-8 fuehren -- ``EmailMessage`` setzt
        # dafuer charset und Transferkodierung passend. Genau deshalb ist die
        # Ersatzschreibung im Text ueberfluessig.
        assert 'charset="utf-8"' in str(msg)

        # Und beim Lesen kommt der Umlaut zurueck — in Betreff wie in Text.
        from email import message_from_bytes
        from email.policy import default as standard_policy

        gelesen = message_from_bytes(roh, policy=standard_policy)
        assert "Prüfung" in gelesen["Subject"]
        assert "fällig" in gelesen.get_content()

