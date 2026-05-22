import httpx
import aiosmtplib
from email.message import EmailMessage

from config import settings
from services.panel_settings_service import PanelSettingsService


class EmailService:
    """Email-Service mit SMTP und Resend-Unterstützung.

    Provider-Priorität:
      1. Resend (falls MSM_RESEND_API_KEY gesetzt)
      2. SMTP (falls MSM_SMTP_HOST gesetzt)
    """

    # ---- Brand Colors (MauntingStudios dark theme) ----
    BG_COLOR = "#0d1117"
    SURFACE_COLOR = "#161b22"
    BORDER_COLOR = "#30363d"
    PRIMARY_TEXT = "#e6edf3"
    SECONDARY_TEXT = "#8b949e"
    ACCENT_COLOR = "#4ade80"       # green-400
    ACCENT_HOVER = "#22c55e"       # green-500
    CYAN_ACCENT = "#22d3ee"        # cyan-400
    MUTED_COLOR = "#6e7681"

    @classmethod
    def _get_logo_url(cls) -> str:
        """Absolute URL to the logo used in HTML emails."""
        return settings.logo_url or f"{settings.panel_url.rstrip('/')}/logo.svg"

    @classmethod
    def _logo_html(cls) -> str:
        """Renders the logo for email templates.

        Uses an <img> when a logo_url is configured; otherwise falls back
        to the green square placeholder with the letter 'M'.
        """
        url = cls._get_logo_url()
        if url:
            return f'<img src="{url}" alt="M" width="40" height="40" style="border-radius:8px;display:block;" />'
        # Fallback placeholder (same dimensions, green background)
        return (
            f'<div style="width:40px;height:40px;background-color:{cls.ACCENT_COLOR};'
            f'border-radius:8px;text-align:center;line-height:40px;font-size:22px;'
            f'font-weight:800;color:{cls.BG_COLOR};">M</div>'
        )

    @staticmethod
    def _get_setting(key: str) -> str:
        """Liest Setting aus DB (Vorrang) oder Umgebungsvariable."""
        db_val = PanelSettingsService.get(key, "")
        if db_val:
            return db_val
        return getattr(settings, key, "")

    @staticmethod
    def is_configured() -> bool:
        if EmailService._get_setting("resend_api_key"):
            return True
        return bool(EmailService._get_setting("smtp_host") and EmailService._get_setting("smtp_user"))

    @staticmethod
    def _get_provider() -> str:
        if EmailService._get_setting("resend_api_key"):
            return "resend"
        if EmailService._get_setting("smtp_host") and EmailService._get_setting("smtp_user"):
            return "smtp"
        return "none"

    @staticmethod
    async def send_email(to: str, subject: str, body: str, html: str | None = None) -> bool:
        provider = EmailService._get_provider()
        if provider == "none":
            return False
        if provider == "resend":
            return await EmailService._send_resend(to, subject, body, html)
        return await EmailService._send_smtp(to, subject, body, html)

    @staticmethod
    async def _send_smtp(to: str, subject: str, body: str, html: str | None = None) -> bool:
        msg = EmailMessage()
        msg["From"] = EmailService._get_setting("smtp_from") or settings.smtp_from
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        if html:
            msg.add_alternative(html, subtype="html")

        try:
            await aiosmtplib.send(
                msg,
                hostname=EmailService._get_setting("smtp_host") or settings.smtp_host,
                port=int(EmailService._get_setting("smtp_port") or settings.smtp_port or 587),
                username=EmailService._get_setting("smtp_user") or settings.smtp_user,
                password=EmailService._get_setting("smtp_password") or settings.smtp_password,
                start_tls=EmailService._get_setting("smtp_tls").lower() == "true" if EmailService._get_setting("smtp_tls") else settings.smtp_tls,
            )
            return True
        except Exception:
            return False

    @staticmethod
    async def _send_resend(to: str, subject: str, body: str, html: str | None = None) -> bool:
        """Sendet via Resend API (resend.com) — kein SMTP nötig."""
        try:
            payload = {
                "from": EmailService._get_setting("smtp_from") or settings.smtp_from,
                "to": [to],
                "subject": subject,
                "text": body,
            }
            if html:
                payload["html"] = html
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    "https://api.resend.com/emails",
                    headers={
                        "Authorization": f"Bearer {EmailService._get_setting('resend_api_key') or settings.resend_api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                return response.status_code in (200, 202)
        except Exception:
            return False

    # ------------------------------------------------------------------
    # HTML Template helpers
    # ------------------------------------------------------------------

    @classmethod
    def _base_template(cls, title: str, content_html: str) -> str:
        """Gibt das gemeinsame HTML-Email-Gerüst zurück."""
        return f"""<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
  <style>
    @media only screen and (max-width: 600px) {{
      .container {{ width: 100% !important; padding: 16px !important; }}
      .inner {{ padding: 24px !important; }}
      .headline {{ font-size: 20px !important; }}
      .code {{ font-size: 32px !important; letter-spacing: 8px !important; }}
    }}
  </style>
</head>
<body style="margin:0;padding:0;background-color:{cls.BG_COLOR};font-family:'Segoe UI',Roboto,Helvetica,Arial,sans-serif;-webkit-font-smoothing:antialiased;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:{cls.BG_COLOR};">
    <tr>
      <td align="center" style="padding:40px 16px;">
        <table role="presentation" class="container" width="600" cellpadding="0" cellspacing="0" border="0" style="max-width:600px;width:100%;background-color:{cls.SURFACE_COLOR};border:1px solid {cls.BORDER_COLOR};border-radius:12px;overflow:hidden;">
          <!-- Header / Brand -->
          <tr>
            <td style="padding:32px 32px 24px 32px;text-align:center;border-bottom:1px solid {cls.BORDER_COLOR};">
              <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:0 auto;">
                <tr>
                  <td style="width:40px;height:40px;vertical-align:middle;">
                    {cls._logo_html()}
                  </td>
                  <td style="padding-left:12px;vertical-align:middle;">
                    <span style="font-family:'Segoe UI',Roboto,Helvetica,Arial,sans-serif;font-size:18px;font-weight:700;color:{cls.PRIMARY_TEXT};line-height:1.2;">MauntingStudios</span><br>
                    <span style="font-family:'Courier New',monospace;font-size:11px;color:{cls.MUTED_COLOR};letter-spacing:0.5px;">INFRASTRUCTURE CONTROL</span>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
          <!-- Content -->
          <tr>
            <td class="inner" style="padding:32px;">
              {content_html}
            </td>
          </tr>
          <!-- Footer -->
          <tr>
            <td style="padding:24px 32px;text-align:center;border-top:1px solid {cls.BORDER_COLOR};">
              <p style="margin:0;font-family:'Courier New',monospace;font-size:11px;color:{cls.MUTED_COLOR};line-height:1.5;">
                Maunting Server Manager<br>
                Diese Nachricht wurde automatisch versendet.
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""

    @classmethod
    def _cta_button(cls, url: str, label: str) -> str:
        return f"""<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:28px auto 0 auto;">
  <tr>
    <td style="border-radius:8px;background-color:{cls.ACCENT_COLOR};text-align:center;">
      <a href="{url}" style="display:inline-block;padding:14px 32px;font-family:'Segoe UI',Roboto,Helvetica,Arial,sans-serif;font-size:15px;font-weight:600;color:{cls.BG_COLOR};text-decoration:none;border-radius:8px;">{label}</a>
    </td>
  </tr>
</table>"""

    @classmethod
    def _code_box(cls, code: str) -> str:
        return f"""<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:24px auto 0 auto;">
  <tr>
    <td style="background-color:{cls.BG_COLOR};border:1px solid {cls.BORDER_COLOR};border-radius:8px;padding:20px 32px;text-align:center;">
      <span class="code" style="font-family:'Courier New',monospace;font-size:40px;font-weight:700;color:{cls.CYAN_ACCENT};letter-spacing:12px;line-height:1;">{code}</span>
    </td>
  </tr>
</table>"""

    # ------------------------------------------------------------------
    # Specific templates
    # ------------------------------------------------------------------

    @classmethod
    def _verification_email_html(cls, username: str, url: str) -> str:
        content = f"""<h1 class="headline" style="margin:0 0 12px 0;font-family:'Segoe UI',Roboto,Helvetica,Arial,sans-serif;font-size:24px;font-weight:700;color:{cls.CYAN_ACCENT};line-height:1.3;">E-Mail verifizieren</h1>
<p style="margin:0 0 8px 0;font-size:15px;color:{cls.PRIMARY_TEXT};line-height:1.6;">Hallo <strong>{username}</strong>,</p>
<p style="margin:0 0 20px 0;font-size:15px;color:{cls.SECONDARY_TEXT};line-height:1.6;">bitte bestätige deine E-Mail-Adresse, um dein Konto zu aktivieren. Klicke dazu auf den Button:</p>
{cls._cta_button(url, 'E-Mail verifizieren')}
<p style="margin:28px 0 0 0;font-size:13px;color:{cls.MUTED_COLOR};line-height:1.5;text-align:center;">Falls der Button nicht funktioniert, kopiere diesen Link in deinen Browser:<br><a href="{url}" style="color:{cls.ACCENT_COLOR};text-decoration:none;word-break:break-all;">{url}</a></p>
<p style="margin:20px 0 0 0;font-size:13px;color:{cls.MUTED_COLOR};line-height:1.5;text-align:center;">Falls du dich nicht registriert hast, ignoriere diese E-Mail.</p>"""
        return cls._base_template("E-Mail verifizieren", content)

    @classmethod
    def _password_reset_email_html(cls, username: str, url: str) -> str:
        content = f"""<h1 class="headline" style="margin:0 0 12px 0;font-family:'Segoe UI',Roboto,Helvetica,Arial,sans-serif;font-size:24px;font-weight:700;color:{cls.CYAN_ACCENT};line-height:1.3;">Passwort zurücksetzen</h1>
<p style="margin:0 0 8px 0;font-size:15px;color:{cls.PRIMARY_TEXT};line-height:1.6;">Hallo <strong>{username}</strong>,</p>
<p style="margin:0 0 20px 0;font-size:15px;color:{cls.SECONDARY_TEXT};line-height:1.6;">du hast angefordert, dein Passwort zurückzusetzen. Klicke auf den Button, um fortzufahren:</p>
{cls._cta_button(url, 'Passwort zurücksetzen')}
<p style="margin:28px 0 0 0;font-size:13px;color:{cls.MUTED_COLOR};line-height:1.5;text-align:center;">Falls der Button nicht funktioniert, kopiere diesen Link in deinen Browser:<br><a href="{url}" style="color:{cls.ACCENT_COLOR};text-decoration:none;word-break:break-all;">{url}</a></p>
<p style="margin:20px 0 0 0;font-size:13px;color:{cls.MUTED_COLOR};line-height:1.5;text-align:center;">Dieser Link ist <strong>1 Stunde</strong> gültig. Falls du das Zurücksetzen nicht beantragt hast, ignoriere diese E-Mail.</p>"""
        return cls._base_template("Passwort zurücksetzen", content)

    @classmethod
    def _verification_code_email_html(cls, username: str, code: str) -> str:
        content = f"""<h1 class="headline" style="margin:0 0 12px 0;font-family:'Segoe UI',Roboto,Helvetica,Arial,sans-serif;font-size:24px;font-weight:700;color:{cls.CYAN_ACCENT};line-height:1.3;">Verifizierungscode</h1>
<p style="margin:0 0 8px 0;font-size:15px;color:{cls.PRIMARY_TEXT};line-height:1.6;">Hallo <strong>{username}</strong>,</p>
<p style="margin:0 0 20px 0;font-size:15px;color:{cls.SECONDARY_TEXT};line-height:1.6;">gib den folgenden Code ein, um deine E-Mail-Adresse zu bestätigen:</p>
{cls._code_box(code)}
<p style="margin:24px 0 0 0;font-size:13px;color:{cls.MUTED_COLOR};line-height:1.5;text-align:center;">Der Code ist <strong>10 Minuten</strong> gültig.</p>
<p style="margin:16px 0 0 0;font-size:13px;color:{cls.MUTED_COLOR};line-height:1.5;text-align:center;">Falls du diesen Code nicht angefordert hast, ignoriere diese E-Mail.</p>"""
        return cls._base_template("Verifizierungscode", content)

    # ------------------------------------------------------------------
    # Public senders
    # ------------------------------------------------------------------

    @staticmethod
    async def send_verification_email(to: str, username: str, token: str) -> bool:
        url = f"{settings.panel_url}/verify-email?token={token}"
        subject = "Maunting Server Manager — E-Mail verifizieren"
        body = f"""Hallo {username},

bitte verifiziere deine E-Mail-Adresse:
{url}

Falls du dich nicht registriert hast, ignoriere diese E-Mail.

Maunting Server Manager
"""
        html = EmailService._verification_email_html(username, url)
        return await EmailService.send_email(to, subject, body, html)

    @staticmethod
    async def send_password_reset_email(to: str, username: str, token: str) -> bool:
        url = f"{settings.panel_url}/reset-password?token={token}"
        subject = "Maunting Server Manager — Passwort zurücksetzen"
        body = f"""Hallo {username},

setze dein Passwort zurück:
{url}

Dieser Link ist 1 Stunde gültig.

Maunting Server Manager
"""
        html = EmailService._password_reset_email_html(username, url)
        return await EmailService.send_email(to, subject, body, html)

    @staticmethod
    async def send_verification_code_email(to: str, username: str, code: str) -> bool:
        subject = "Maunting Server Manager — Verifizierungscode"
        body = f"""Hallo {username},

Dein Verifizierungscode lautet:

{code}

Gültig für 10 Minuten.

Maunting Server Manager
"""
        html = EmailService._verification_code_email_html(username, code)
        return await EmailService.send_email(to, subject, body, html)

    # ------------------------------------------------------------------
    # Security notification emails
    # ------------------------------------------------------------------

    @classmethod
    def _notification_email_html(cls, username: str, title: str, message: str, detail: str = "") -> str:
        detail_html = f'<p style="margin:12px 0 0 0;font-size:13px;color:{cls.MUTED_COLOR};line-height:1.5;">{detail}</p>' if detail else ""
        content = f"""<h1 class="headline" style="margin:0 0 12px 0;font-size:24px;font-weight:700;color:{cls.CYAN_ACCENT};line-height:1.3;">{title}</h1>
<p style="margin:0 0 8px 0;font-size:15px;color:{cls.PRIMARY_TEXT};line-height:1.6;">Hallo <strong>{username}</strong>,</p>
<p style="margin:0 0 20px 0;font-size:15px;color:{cls.SECONDARY_TEXT};line-height:1.6;">{message}</p>
{detail_html}
<p style="margin:20px 0 0 0;font-size:13px;color:{cls.MUTED_COLOR};line-height:1.5;text-align:center;">Falls du diese Aktion nicht durchgeführt hast, ändere sofort dein Passwort und kontaktiere den Administrator.</p>"""
        return cls._base_template(title, content)

    @staticmethod
    async def send_password_changed_notification(to: str, username: str) -> bool:
        subject = "Maunting Server Manager — Passwort geändert"
        body = f"""Hallo {username},

Dein Passwort wurde soeben geändert.

Falls du diese Änderung nicht vorgenommen hast, ändere sofort dein Passwort und kontaktiere den Administrator.

Maunting Server Manager
"""
        html = EmailService._notification_email_html(username, "Passwort geändert", "Dein Passwort wurde soeben geändert.")
        return await EmailService.send_email(to, subject, body, html)

    @staticmethod
    async def send_new_device_login_notification(to: str, username: str, ip: str, user_agent: str) -> bool:
        subject = "Maunting Server Manager — Neuer Login"
        body = f"""Hallo {username},

Ein neuer Login wurde von einem unbekannten Gerät erkannt:

IP: {ip}
Gerät: {user_agent}

Falls du dich nicht eingeloggt hast, ändere sofort dein Passwort.

Maunting Server Manager
"""
        detail = f"IP: {ip}<br>Gerät: {user_agent}"
        html = EmailService._notification_email_html(username, "Neuer Login erkannt", "Ein neuer Login wurde von einem unbekannten Gerät erkannt.", detail)
        return await EmailService.send_email(to, subject, body, html)

    @staticmethod
    async def send_2fa_status_notification(to: str, username: str, enabled: bool) -> bool:
        action = "aktiviert" if enabled else "deaktiviert"
        subject = f"Maunting Server Manager — 2FA {action}"
        body = f"""Hallo {username},

Die Zwei-Faktor-Authentifizierung (2FA) wurde {action}.

Falls du diese Änderung nicht vorgenommen hast, ändere sofort dein Passwort und kontaktiere den Administrator.

Maunting Server Manager
"""
        html = EmailService._notification_email_html(username, f"2FA {action}", f"Die Zwei-Faktor-Authentifizierung wurde {action}.")
        return await EmailService.send_email(to, subject, body, html)

    @staticmethod
    async def send_server_status_notification(to: str, username: str, server_name: str, status: str) -> bool:
        subject = f"Maunting Server Manager — Server-Status: {server_name}"
        body = f"""Hallo {username},

Der Server "{server_name}" hat seinen Status geändert: {status}

Maunting Server Manager
"""
        html = EmailService._notification_email_html(username, "Server-Status geändert", f'Der Server "{server_name}" hat seinen Status geändert: <strong>{status}</strong>.')
        return await EmailService.send_email(to, subject, body, html)

    @staticmethod
    async def send_server_installed_notification(to: str, username: str, server_name: str) -> bool:
        subject = f"Maunting Server Manager — Server installiert: {server_name}"
        body = f"""Hallo {username},

Der Server "{server_name}" wurde erfolgreich installiert und ist bereit.

Maunting Server Manager
"""
        html = EmailService._notification_email_html(username, "Server installiert", f'Der Server "{server_name}" wurde erfolgreich installiert.')
        return await EmailService.send_email(to, subject, body, html)

    @staticmethod
    async def send_user_added_to_server_notification(to: str, username: str, server_name: str, added_by: str) -> bool:
        subject = f"Maunting Server Manager — Zu Server hinzugefügt: {server_name}"
        body = f"""Hallo {username},

Du wurdest von {added_by} zum Server "{server_name}" hinzugefügt.

Maunting Server Manager
"""
        html = EmailService._notification_email_html(username, "Zu Server hinzugefügt", f'Du wurdest von <strong>{added_by}</strong> zum Server "{server_name}" hinzugefügt.')
        return await EmailService.send_email(to, subject, body, html)
