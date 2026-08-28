"""Service zur Verwaltung und Interaktion mit verknüpften Benutzer-Postfächern.

Unterstützt IMAP/SMTP (Passwort / App-Passwort) sowie OAuth (XOAUTH2 für Gmail / Microsoft).
Sicherheitsinvariante:
  - Credentials werden erst zur Laufzeit aus der Datenbank entschlüsselt und verbleiben nie im Speicher.
  - HTML-E-Mails werden bereinigt und Plaintext extrahiert, um Prompt-Injections und Script-Leaks zu verhindern.
"""

from __future__ import annotations

import email
from email.header import decode_header
from email.message import EmailMessage
import imaplib
import logging
import smtplib
import ssl
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.user import User
from models.user_mailbox import UserMailbox
from services.ai_latency_metrics import metrics

_log = logging.getLogger("msm.mailbox")


def _decode_mime_header(header_value: str | None) -> str:
    if not header_value:
        return ""
    parts = decode_header(header_value)
    decoded = []
    for text, charset in parts:
        if isinstance(text, bytes):
            try:
                decoded.append(text.decode(charset or "utf-8", errors="replace"))
            except Exception:
                decoded.append(text.decode("latin1", errors="replace"))
        else:
            decoded.append(str(text))
    return "".join(decoded)


def _extract_body(msg: email.message.Message) -> tuple[str, str]:
    """Extrahiert (plain_text, html) aus einer MIME-Message."""
    text_content = ""
    html_content = ""

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition") or "")
            if "attachment" in content_disposition:
                continue

            payload = part.get_payload(decode=True)
            if not payload:
                continue

            charset = part.get_content_charset() or "utf-8"
            try:
                decoded_str = payload.decode(charset, errors="replace")
            except Exception:
                decoded_str = payload.decode("latin1", errors="replace")

            if content_type == "text/plain" and not text_content:
                text_content = decoded_str
            elif content_type == "text/html" and not html_content:
                html_content = decoded_str
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            try:
                decoded_str = payload.decode(charset, errors="replace")
            except Exception:
                decoded_str = payload.decode("latin1", errors="replace")
            if msg.get_content_type() == "text/html":
                html_content = decoded_str
            else:
                text_content = decoded_str

    # Fallback: Wenn nur HTML da ist, Tags rudimentär filtern für die KI
    if not text_content and html_content:
        import re

        clean = re.sub(r"<[^>]+>", " ", html_content)
        text_content = re.sub(r"\s+", " ", clean).strip()

    return text_content.strip(), html_content.strip()


class MailboxService:
    @staticmethod
    def get_mailbox(db: Session, user: User, mailbox_id: int | None = None) -> UserMailbox | None:
        """Gibt das angeforderte oder das Standard-Postfach des Benutzers zurück."""
        if mailbox_id is not None:
            return db.scalar(
                select(UserMailbox).where(
                    UserMailbox.id == mailbox_id,
                    UserMailbox.user_id == user.id,
                )
            )
        # Standard-Postfach
        default_mb = db.scalar(
            select(UserMailbox).where(
                UserMailbox.user_id == user.id,
                UserMailbox.is_default == True,  # noqa: E712
            )
        )
        if default_mb:
            return default_mb
        # Erstes verfügbares Postfach
        return db.scalar(
            select(UserMailbox).where(UserMailbox.user_id == user.id).order_by(UserMailbox.id.asc())
        )

    @staticmethod
    def test_connection(mailbox: UserMailbox) -> tuple[bool, str]:
        """Testet die IMAP- und SMTP-Verbindung des Postfachs."""
        secret = mailbox.get_credentials()
        if not secret:
            return False, "Keine Zugangsdaten hinterlegt"

        # 1. IMAP Test
        if mailbox.imap_host:
            try:
                if mailbox.imap_use_ssl:
                    context = ssl.create_default_context()
                    client = imaplib.IMAP4_SSL(
                        mailbox.imap_host, mailbox.imap_port or 993, ssl_context=context, timeout=10
                    )
                else:
                    client = imaplib.IMAP4(mailbox.imap_host, mailbox.imap_port or 143, timeout=10)
                    if hasattr(client, "starttls"):
                        client.starttls()

                username = mailbox.imap_username or mailbox.email
                if mailbox.provider_type in ("oauth_google", "oauth_microsoft"):
                    auth_string = f"user={username}\x01auth=Bearer {secret}\x01\x01"
                    client.authenticate("XOAUTH2", lambda _: auth_string.encode("utf-8"))
                else:
                    client.login(username, secret)
                client.logout()
            except (TimeoutError, OSError) as e:
                _log.warning("IMAP Timeout für %s (%s): %s", mailbox.email, mailbox.imap_host, e)
                return False, f"IMAP-Verbindung zeitüberschritten (Host nicht erreichbar oder falscher Port): {e}"
            except Exception as e:
                _log.warning("IMAP Test fehlgeschlagen für %s: %s", mailbox.email, e)
                return False, f"IMAP-Verbindung fehlgeschlagen: {e}"

        # 2. SMTP Test
        if mailbox.smtp_host:
            try:
                if mailbox.smtp_use_tls and mailbox.smtp_port == 465:
                    context = ssl.create_default_context()
                    server = smtplib.SMTP_SSL(
                        mailbox.smtp_host, mailbox.smtp_port, context=context, timeout=10
                    )
                else:
                    server = smtplib.SMTP(
                        mailbox.smtp_host, mailbox.smtp_port or 587, timeout=10
                    )
                    if mailbox.smtp_use_tls:
                        context = ssl.create_default_context()
                        server.starttls(context=context)

                username = mailbox.smtp_username or mailbox.email
                if mailbox.provider_type in ("oauth_google", "oauth_microsoft"):
                    auth_string = f"user={username}\x01auth=Bearer {secret}\x01\x01"
                    server.auth("XOAUTH2", lambda _: auth_string.encode("utf-8"))
                else:
                    server.login(username, secret)
                server.quit()
            except (TimeoutError, OSError) as e:
                _log.warning("SMTP Timeout für %s (%s): %s", mailbox.email, mailbox.smtp_host, e)
                return False, f"SMTP-Verbindung zeitüberschritten (Host nicht erreichbar oder falscher Port): {e}"
            except Exception as e:
                _log.warning("SMTP Test fehlgeschlagen für %s: %s", mailbox.email, e)
                return False, f"SMTP-Verbindung fehlgeschlagen: {e}"

        return True, "Verbindung erfolgreich hergestellt"

    @classmethod
    def search_messages(
        cls,
        db: Session,
        user: User,
        mailbox_id: int | None = None,
        query: str | None = None,
        sender: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Sucht im Postfach nach passenden E-Mails."""
        mailbox = cls.get_mailbox(db, user, mailbox_id)
        if not mailbox or not mailbox.imap_host:
            return []

        secret = mailbox.get_credentials()
        if not secret:
            return []

        results: list[dict[str, Any]] = []
        try:
            if mailbox.imap_use_ssl:
                context = ssl.create_default_context()
                client = imaplib.IMAP4_SSL(
                    mailbox.imap_host, mailbox.imap_port or 993, ssl_context=context, timeout=15
                )
            else:
                client = imaplib.IMAP4(mailbox.imap_host, mailbox.imap_port or 143, timeout=15)

            username = mailbox.imap_username or mailbox.email
            if mailbox.provider_type in ("oauth_google", "oauth_microsoft"):
                auth_string = f"user={username}\x01auth=Bearer {secret}\x01\x01"
                client.authenticate("XOAUTH2", lambda _: auth_string.encode("utf-8"))
            else:
                client.login(username, secret)

            client.select("INBOX", readonly=True)

            search_criteria = []
            if sender:
                search_criteria.append(f'FROM "{sender}"')
            if query:
                search_criteria.append(f'TEXT "{query}"')
            if not search_criteria:
                search_criteria.append("ALL")

            criteria_str = " ".join(search_criteria)
            status, data = client.search(None, criteria_str)
            if status != "OK" or not data or not data[0]:
                client.logout()
                return []

            msg_ids = data[0].split()
            # Neueste Nachrichten zuerst
            msg_ids = msg_ids[::-1][: min(limit, 25)]

            # Ein Batch spart bei Fernpostfächern bis zu 25 IMAP-Rundreisen.
            # Die IDs kommen ausschließlich aus dem gerade autorisierten
            # Suchergebnis und werden nicht aus Nutzereingaben übernommen.
            started_at = time.perf_counter()
            f_status, f_data = client.fetch(
                b",".join(msg_ids), "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM TO DATE)])"
            )
            metrics.record(
                "mailbox", "imap_header_fetch", (time.perf_counter() - started_at) * 1000,
                "ok" if f_status == "OK" else "error",
            )
            if f_status != "OK" or not f_data:
                client.logout()
                return []
            for record in f_data:
                if not isinstance(record, tuple) or len(record) < 2 or not isinstance(record[1], bytes):
                    continue
                raw_header = record[1]
                msg = email.message_from_bytes(raw_header)
                results.append(
                    {
                        "message_id": mid.decode("ascii", errors="replace"),
                        "subject": _decode_mime_header(msg.get("Subject")),
                        "from": _decode_mime_header(msg.get("From")),
                        "to": _decode_mime_header(msg.get("To")),
                        "date": msg.get("Date", ""),
                        "mailbox_email": mailbox.email,
                    }
                )

            client.logout()
        except Exception as e:
            _log.warning("Fehler bei IMAP-Suche für %s: %s", mailbox.email, e)

        return results

    @classmethod
    def read_message(
        cls,
        db: Session,
        user: User,
        message_id: str,
        mailbox_id: int | None = None,
    ) -> dict[str, Any] | None:
        """Liest den Volltext einer bestimmten E-Mail."""
        mailbox = cls.get_mailbox(db, user, mailbox_id)
        if not mailbox or not mailbox.imap_host:
            return None

        secret = mailbox.get_credentials()
        if not secret:
            return None

        try:
            if mailbox.imap_use_ssl:
                context = ssl.create_default_context()
                client = imaplib.IMAP4_SSL(
                    mailbox.imap_host, mailbox.imap_port or 993, ssl_context=context, timeout=15
                )
            else:
                client = imaplib.IMAP4(mailbox.imap_host, mailbox.imap_port or 143, timeout=15)

            username = mailbox.imap_username or mailbox.email
            if mailbox.provider_type in ("oauth_google", "oauth_microsoft"):
                auth_string = f"user={username}\x01auth=Bearer {secret}\x01\x01"
                client.authenticate("XOAUTH2", lambda _: auth_string.encode("utf-8"))
            else:
                client.login(username, secret)

            client.select("INBOX", readonly=True)
            status, data = client.fetch(message_id.encode("ascii"), "(BODY.PEEK[])")
            if status != "OK" or not data or not data[0] or not isinstance(data[0], tuple):
                client.logout()
                return None

            raw_email = data[0][1]
            msg = email.message_from_bytes(raw_email)
            plain_text, html_text = _extract_body(msg)

            result = {
                "message_id": message_id,
                "subject": _decode_mime_header(msg.get("Subject")),
                "from": _decode_mime_header(msg.get("From")),
                "to": _decode_mime_header(msg.get("To")),
                "date": msg.get("Date", ""),
                "body": plain_text[:8000],  # Begrenzung für Token-Effizienz
                "mailbox_email": mailbox.email,
            }
            client.logout()
            return result
        except Exception as e:
            _log.warning("Fehler beim Lesen der Mail %s für %s: %s", message_id, mailbox.email, e)
            return None

    @classmethod
    def send_email(
        cls,
        db: Session,
        user: User,
        recipient: str,
        subject: str,
        body_text: str,
        mailbox_id: int | None = None,
        body_html: str | None = None,
    ) -> dict[str, Any]:
        """Versendet eine E-Mail über das Postfach (nur nach Proposal-Freigabe)."""
        mailbox = cls.get_mailbox(db, user, mailbox_id)
        if not mailbox or not mailbox.smtp_host:
            raise ValueError(f"Kein SMTP-Postfach für Benutzer {user.id} verfügbar")

        secret = mailbox.get_credentials()
        if not secret:
            raise ValueError(f"Keine SMTP-Zugangsdaten für Postfach {mailbox.email}")

        msg = EmailMessage()
        msg["From"] = mailbox.email
        msg["To"] = recipient
        msg["Subject"] = subject
        msg.set_content(body_text)

        if body_html:
            msg.add_alternative(body_html, subtype="html")

        if mailbox.smtp_use_tls and mailbox.smtp_port == 465:
            context = ssl.create_default_context()
            server = smtplib.SMTP_SSL(
                mailbox.smtp_host, mailbox.smtp_port, context=context, timeout=15
            )
        else:
            server = smtplib.SMTP(
                mailbox.smtp_host, mailbox.smtp_port or 587, timeout=15
            )
            if mailbox.smtp_use_tls:
                context = ssl.create_default_context()
                server.starttls(context=context)

        username = mailbox.smtp_username or mailbox.email
        if mailbox.provider_type in ("oauth_google", "oauth_microsoft"):
            auth_string = f"user={username}\x01auth=Bearer {secret}\x01\x01"
            server.auth("XOAUTH2", lambda _: auth_string.encode("utf-8"))
        else:
            server.login(username, secret)

        started_at = time.perf_counter()
        try:
            server.send_message(msg)
        finally:
            server.quit()
            metrics.record("mailbox", "smtp_send", (time.perf_counter() - started_at) * 1000)

        return {
            "status": "sent",
            "from": mailbox.email,
            "to": recipient,
            "subject": subject,
        }
