import aiosmtplib
from email.message import EmailMessage

from config import settings


class EmailService:
    @staticmethod
    def is_configured() -> bool:
        return bool(settings.smtp_host and settings.smtp_user)

    @staticmethod
    async def send_email(to: str, subject: str, body: str) -> bool:
        if not EmailService.is_configured():
            return False

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
