import httpx
import aiosmtplib
from email.message import EmailMessage

from config import settings


class EmailService:
    """Email-Service mit SMTP und Resend-Unterstützung.

    Provider-Priorität:
      1. Resend (falls MSM_RESEND_API_KEY gesetzt)
      2. SMTP (falls MSM_SMTP_HOST gesetzt)
    """

    @staticmethod
    def is_configured() -> bool:
        if settings.resend_api_key:
            return True
        return bool(settings.smtp_host and settings.smtp_user)

    @staticmethod
    def _get_provider() -> str:
        if settings.resend_api_key:
            return "resend"
        if settings.smtp_host and settings.smtp_user:
            return "smtp"
        return "none"

    @staticmethod
    async def send_email(to: str, subject: str, body: str) -> bool:
        provider = EmailService._get_provider()
        if provider == "none":
            return False
        if provider == "resend":
            return await EmailService._send_resend(to, subject, body)
        return await EmailService._send_smtp(to, subject, body)

    @staticmethod
    async def _send_smtp(to: str, subject: str, body: str) -> bool:
        msg = EmailMessage()
        msg["From"] = settings.smtp_from
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)

        try:
            await aiosmtplib.send(
                msg,
                hostname=settings.smtp_host,
                port=settings.smtp_port,
                username=settings.smtp_user,
                password=settings.smtp_password,
                start_tls=settings.smtp_tls,
            )
            return True
        except Exception:
            return False

    @staticmethod
    async def _send_resend(to: str, subject: str, body: str) -> bool:
        """Sendet via Resend API (resend.com) — kein SMTP nötig."""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    "https://api.resend.com/emails",
                    headers={
                        "Authorization": f"Bearer {settings.resend_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "from": settings.smtp_from,
                        "to": [to],
                        "subject": subject,
                        "text": body,
                    },
                )
                return response.status_code in (200, 202)
        except Exception:
            return False

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
        return await EmailService.send_email(to, subject, body)

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
        return await EmailService.send_email(to, subject, body)

    @staticmethod
    async def send_verification_code_email(to: str, username: str, code: str) -> bool:
        subject = "Maunting Server Manager — Verifizierungscode"
        body = f"""Hallo {username},

Dein Verifizierungscode lautet:

{code}

Gültig für 10 Minuten.

Maunting Server Manager
"""
        return await EmailService.send_email(to, subject, body)
