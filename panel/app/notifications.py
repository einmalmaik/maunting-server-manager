from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage

import httpx

logger = logging.getLogger(__name__)


def _provider() -> str:
    return os.getenv("EMAIL_PROVIDER", "off").strip().lower()


def _sender() -> str:
    return os.getenv("EMAIL_FROM", "").strip()


def _enabled() -> bool:
    return _provider() in {"smtp", "resend"} and bool(_sender())


def _send_smtp(to_email: str, subject: str, body: str) -> None:
    host = os.getenv("SMTP_HOST", "").strip()
    port = int(os.getenv("SMTP_PORT", "587").strip() or "587")
    username = os.getenv("SMTP_USERNAME", "").strip()
    password = os.getenv("SMTP_PASSWORD", "")
    use_tls = os.getenv("SMTP_STARTTLS", "true").strip().lower() != "false"
    if not host:
        raise RuntimeError("SMTP_HOST is not configured.")

    message = EmailMessage()
    message["From"] = _sender()
    message["To"] = to_email
    message["Subject"] = subject
    message.set_content(body)

    with smtplib.SMTP(host, port, timeout=15) as smtp:
        if use_tls:
            smtp.starttls()
        if username:
            smtp.login(username, password)
        smtp.send_message(message)


def _send_resend(to_email: str, subject: str, body: str) -> None:
    api_key = os.getenv("RESEND_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("RESEND_API_KEY is not configured.")

    response = httpx.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "from": _sender(),
            "to": [to_email],
            "subject": subject,
            "text": body,
        },
        timeout=15.0,
    )
    response.raise_for_status()


def send_email(to_email: str | None, subject: str, body: str) -> bool:
    if not to_email or not _enabled():
        return False
    try:
        if _provider() == "smtp":
            _send_smtp(to_email, subject, body)
        elif _provider() == "resend":
            _send_resend(to_email, subject, body)
        else:
            return False
    except Exception:
        logger.warning("Email notification failed for recipient=%s", to_email, exc_info=True)
        return False
    return True


def notify_account_created(to_email: str | None, username: str) -> None:
    send_email(
        to_email,
        "Maunting Server Manager account created",
        (
            f"Hello {username},\n\n"
            "An account was created for you in the Maunting Server Manager.\n"
            "Use the password provided by your administrator and enable 2FA after first login.\n"
        ),
    )


def notify_password_reset(to_email: str | None, username: str) -> None:
    send_email(
        to_email,
        "Maunting Server Manager password changed",
        (
            f"Hello {username},\n\n"
            "Your Maunting Server Manager password was changed by an administrator.\n"
            "If you did not expect this, contact the panel owner immediately.\n"
        ),
    )
