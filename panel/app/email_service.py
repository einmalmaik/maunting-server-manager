from __future__ import annotations

import logging
import secrets
import smtplib
from datetime import UTC, datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from .config import get_settings

logger = logging.getLogger(__name__)


def _generate_token() -> str:
    return secrets.token_urlsafe(32)


def generate_verification_token() -> str:
    return _generate_token()


def generate_reset_token() -> str:
    return _generate_token()


def _send_smtp(
    to: str,
    subject: str,
    body_text: str,
    body_html: str | None = None,
) -> bool:
    settings = get_settings()
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{settings.email_from_name} <{settings.email_from or settings.smtp_from}>"
        msg["To"] = to
        msg.attach(MIMEText(body_text, "plain"))
        if body_html:
            msg.attach(MIMEText(body_html, "html"))

        if settings.smtp_tls:
            server = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=15)
        else:
            server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15)
            if settings.smtp_starttls:
                server.starttls()
        if settings.smtp_user:
            server.login(settings.smtp_user, settings.smtp_password)
        server.sendmail(
            settings.email_from or settings.smtp_from,
            [to],
            msg.as_string(),
        )
        server.quit()
        return True
    except Exception as exc:
        logger.error("SMTP send failed: %s", exc)
        return False


def _send_resend(
    to: str,
    subject: str,
    body_text: str,
    body_html: str | None = None,
) -> bool:
    settings = get_settings()
    try:
        import resend as _resend
    except ImportError:
        logger.error("resend package not installed; cannot send email via Resend.")
        return False

    _resend.api_key = settings.resend_api_key
    params: dict = {
        "from": f"{settings.email_from_name} <{settings.email_from}>",
        "to": [to],
        "subject": subject,
        "text": body_text,
    }
    if body_html:
        params["html"] = body_html
    try:
        _resend.Emails.send(params)
        return True
    except Exception as exc:
        logger.error("Resend send failed: %s", exc)
        return False


def _is_enabled() -> bool:
    settings = get_settings()
    return settings.email_provider in ("smtp", "resend")


def send_email(
    to: str,
    subject: str,
    body_text: str,
    body_html: str | None = None,
) -> bool:
    settings = get_settings()
    if settings.email_provider == "none":
        logger.debug("Email provider is none; skipping send.")
        return False
    if settings.email_provider == "smtp":
        return _send_smtp(to, subject, body_text, body_html)
    if settings.email_provider == "resend":
        return _send_resend(to, subject, body_text, body_html)
    logger.warning("Unknown email provider: %s", settings.email_provider)
    return False


def _token_expiry_hours(token_type: str) -> int:
    settings = get_settings()
    if token_type == "password_reset":
        return settings.password_reset_token_hours
    if token_type == "verification":
        return settings.verification_token_hours
    return 24


def compute_expires_at(token_type: str) -> datetime:
    hours = _token_expiry_hours(token_type)
    return datetime.now(UTC) + timedelta(hours=hours)


def send_verification_email(to: str, username: str, token: str, base_url: str) -> bool:
    settings = get_settings()
    hours = _token_expiry_hours("verification")
    verify_url = f"{base_url.rstrip('/')}/api/auth/verify-email?token={token}"
    subject = f"Verify your email - {settings.app_name}"
    text = f"""Hello {username},

Please verify your email address by clicking the link below:
{verify_url}

This link expires in {hours} hour(s).

If you did not create this account, please ignore this email.
"""
    html = f"""<p>Hello {username},</p>
<p>Please verify your email address by clicking the link below:</p>
<p><a href="{verify_url}">Verify Email</a></p>
<p>This link expires in {hours} hour(s).</p>
<p>If you did not create this account, please ignore this email.</p>
"""
    return send_email(to, subject, text, html)


def send_password_reset_email(to: str, username: str, token: str, base_url: str) -> bool:
    settings = get_settings()
    hours = _token_expiry_hours("password_reset")
    reset_url = f"{base_url.rstrip('/')}/reset-password?token={token}"
    subject = f"Password reset - {settings.app_name}"
    text = f"""Hello {username},

A password reset was requested for your account.

Click the link below to reset your password:
{reset_url}

This link expires in {hours} hour(s).

If you did not request this reset, please ignore this email.
"""
    html = f"""<p>Hello {username},</p>
<p>A password reset was requested for your account.</p>
<p><a href="{reset_url}">Reset Password</a></p>
<p>This link expires in {hours} hour(s).</p>
<p>If you did not request this reset, please ignore this email.</p>
"""
    return send_email(to, subject, text, html)


def send_welcome_email(to: str, username: str, base_url: str) -> bool:
    settings = get_settings()
    subject = f"Welcome to {settings.app_name}"
    text = f"""Hello {username},

Your account has been created.

You can log in at:
{base_url.rstrip('/')}

If you have any questions, please contact your administrator.
"""
    return send_email(to, subject, text)
