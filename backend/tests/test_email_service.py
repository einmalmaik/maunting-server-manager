"""Tests for EmailService: template logo embedding and email helper validation."""
import pytest
from unittest.mock import AsyncMock, patch

from services.email_service import EmailService
from config import settings


LOGO_VERWEIS = f'src="cid:{EmailService.LOGO_CID}"'


class TestEmailServiceTemplates:
    def test_logo_html_points_to_the_inline_attachment(self):
        """Kein ``data:``-Bild mehr: Gmail und Outlook zeigen es nicht an."""
        logo_html = EmailService._logo_html()
        assert LOGO_VERWEIS in logo_html
        assert "data:image" not in logo_html
        assert "<img" in logo_html

    def test_base_template_carries_logo_and_msm_brand(self):
        template = EmailService._base_template("Test Title", "<p>Test Content</p>")
        assert LOGO_VERWEIS in template
        assert ">MSM<" in template
        assert "MauntingStudios" not in template
        assert "INFRASTRUCTURE CONTROL" not in template
        assert "Diese Nachricht wurde automatisch versendet." in template

    def test_base_template_loads_the_website_fonts_from_the_panel(self):
        template = EmailService._base_template("T", "<p>x</p>")
        basis = settings.panel_url.rstrip("/")
        assert f"{basis}/fonts/mail/inter-latin-400-normal.woff2" in template
        assert f"{basis}/fonts/mail/manrope-latin-800-normal.woff2" in template
        assert "fonts.googleapis" not in template

    def test_every_referenced_font_file_exists(self):
        """Die Vorlage verweist auf Dateien im Frontend — sie muessen dort liegen."""
        import re
        from pathlib import Path

        ordner = Path(__file__).resolve().parents[2] / "frontend" / "public" / "fonts" / "mail"
        dateien = re.findall(r"/fonts/mail/([\w.-]+\.woff2)", EmailService._schriften_css())
        assert dateien
        for datei in dateien:
            assert (ordner / datei).is_file(), datei

    def test_logo_file_is_a_small_png(self):
        from services.email_service import _logo_bytes

        daten = _logo_bytes()
        assert daten is not None and daten.startswith(b"\x89PNG")
        assert len(daten) < 40_000

    def test_password_reset_email_html_contains_logo(self):
        html = EmailService._password_reset_email_html("testuser", "http://test-url/reset")
        assert LOGO_VERWEIS in html
        assert "http://test-url/reset" in html

    def test_verification_code_email_html_contains_logo(self):
        html = EmailService._verification_code_email_html("testuser", "123456")
        assert LOGO_VERWEIS in html
        assert "123456" in html
        assert "testuser" not in html

    def test_notification_email_html_contains_logo(self):
        html = EmailService._notification_email_html("testuser", "Notice", "Test message")
        assert LOGO_VERWEIS in html
        assert "Notice" in html
        assert "Test message" in html

    def test_preview_line_is_the_message_not_the_brand(self):
        html = EmailService._notification_email_html(
            "Mike", "2FA aktiviert", "Die 2FA wurde <strong>aktiviert</strong>."
        )
        assert "mso-hide:all" in html
        assert "Die 2FA wurde aktiviert." in html


class TestLogoAnhang:
    """Das Logo reist als Inline-Anhang mit Content-ID — auf beiden Versandwegen."""

    @pytest.mark.anyio
    async def test_smtp_puts_the_logo_next_to_the_html_part(self):
        gesendet = {}

        async def fake_send(msg, **_kwargs):
            gesendet["msg"] = msg

        html = EmailService._notification_email_html("Mike", "Titel", "Text")
        with patch("services.email_service.aiosmtplib.send", side_effect=fake_send), \
                patch.object(EmailService, "_get_setting", return_value=""):
            assert await EmailService._send_smtp("a@test.de", "Betreff", "Text", html) is True

        msg = gesendet["msg"]
        bilder = [teil for teil in msg.walk() if teil.get_content_type() == "image/png"]
        assert len(bilder) == 1
        assert bilder[0]["Content-ID"] == f"<{EmailService.LOGO_CID}>"
        assert bilder[0].get_content_disposition() == "inline"
        # Das Bild haengt unter multipart/related, zusammen mit der HTML-Fassung.
        related = next(t for t in msg.walk() if t.get_content_type() == "multipart/related")
        typen = [t.get_content_type() for t in related.iter_parts()]
        assert typen == ["text/html", "image/png"]
        # Die Textfassung bleibt die erste Alternative.
        assert msg.get_body(preferencelist=("plain",)).get_content().startswith("Text")

    @pytest.mark.anyio
    async def test_smtp_without_logo_reference_attaches_nothing(self):
        """Alte KI-Mails aus dem Postausgang tragen noch das ``data:``-Bild."""
        gesendet = {}

        async def fake_send(msg, **_kwargs):
            gesendet["msg"] = msg

        with patch("services.email_service.aiosmtplib.send", side_effect=fake_send), \
                patch.object(EmailService, "_get_setting", return_value=""):
            await EmailService._send_smtp("a@test.de", "B", "T", "<p>alt</p>")

        typen = [t.get_content_type() for t in gesendet["msg"].walk()]
        assert "image/png" not in typen

    @pytest.mark.anyio
    async def test_resend_sends_the_logo_as_inline_attachment(self):
        import base64
        from services.email_service import _logo_bytes

        antwort = type("Antwort", (), {"status_code": 200})()
        post = AsyncMock(return_value=antwort)
        html = EmailService._notification_email_html("Mike", "Titel", "Text")
        with patch("services.email_service.httpx.AsyncClient.post", post), \
                patch.object(EmailService, "_get_setting", return_value="k"):
            assert await EmailService._send_resend("a@test.de", "B", "T", html) is True

        anhaenge = post.call_args.kwargs["json"]["attachments"]
        assert anhaenge == [{
            "content": base64.b64encode(_logo_bytes()).decode("ascii"),
            "filename": "msm-logo.png",
            "content_type": "image/png",
            "content_id": EmailService.LOGO_CID,
        }]


class TestSicherheitshinweisNurBeiSicherheit:
    @pytest.mark.anyio
    @patch("services.email_service.EmailService.send_email", new_callable=AsyncMock)
    async def test_2fa_mail_carries_the_hint_in_html_and_text(self, mock_send_email):
        mock_send_email.return_value = True
        await EmailService.send_2fa_status_notification("a@test.de", "Mike", True)
        _to, subject, body, html = mock_send_email.call_args[0]
        assert "2FA aktiviert" in subject
        assert "Hallo Mike," in body and "Hallo Mike," in html
        assert "Zwei-Faktor-Authentifizierung für dein Konto wurde aktiviert." in body
        assert EmailService.SICHERHEITSHINWEIS in body
        assert EmailService.SICHERHEITSHINWEIS in html
        assert "<strong>" not in body

    @pytest.mark.anyio
    @patch("services.email_service.EmailService.send_email", new_callable=AsyncMock)
    async def test_server_and_calendar_mails_do_not(self, mock_send_email):
        mock_send_email.return_value = True
        await EmailService.send_server_installed_notification("a@test.de", "Mike", "Palworld")
        await EmailService.send_calendar_reminder_notification(
            "a@test.de", "Mike", "Wartung", "Mo, 28.09. 18:00"
        )
        for aufruf in mock_send_email.call_args_list:
            assert "ändere sofort dein Passwort" not in aufruf[0][3]

    @pytest.mark.anyio
    @patch("services.email_service.EmailService.send_email", new_callable=AsyncMock)
    async def test_new_device_mail_escapes_the_user_agent(self, mock_send_email):
        """Die Browserkennung schickt der Client selbst — sie ist Fremdtext."""
        mock_send_email.return_value = True
        await EmailService.send_new_device_login_notification(
            "a@test.de", "Mike", "203.0.113.7", '<a href="https://phish.example">x</a>'
        )
        html = mock_send_email.call_args[0][3]
        assert 'href="https://phish.example"' not in html
        assert "&lt;a href=&quot;https://phish.example&quot;&gt;" in html
        assert "203.0.113.7" in html


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
        assert LOGO_VERWEIS in html_body
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
        assert LOGO_VERWEIS in html_body
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
        assert LOGO_VERWEIS in html

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
        msg["Subject"] = "Maunting Service Manager — KI-Aufgabe erledigt: Prüfung"
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

