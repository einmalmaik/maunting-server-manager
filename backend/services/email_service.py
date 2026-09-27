import base64
import html
import logging
import re
from functools import lru_cache
from pathlib import Path

import httpx
import aiosmtplib
from email.message import EmailMessage

from config import settings
from services.panel_settings_service import PanelSettingsService

_log = logging.getLogger("msm.email")

#: Das Logo als eigene Datei und nicht mehr als Base64-Literal im Quelltext.
#: Rund freigestellt und auf 168 px verkleinert (angezeigt mit 56 px, also
#: scharf bis dreifacher Pixeldichte) — die `logo.png` des Frontends waere mit
#: 1,6 MB fuer eine Mail untragbar.
_LOGO_PFAD = Path(__file__).resolve().parent.parent / "assets" / "mail" / "msm-logo.png"


@lru_cache(maxsize=1)
def _logo_bytes() -> bytes | None:
    try:
        return _LOGO_PFAD.read_bytes()
    except OSError:
        _log.warning("Mail-Logo fehlt unter %s — Mails gehen ohne Logo hinaus", _LOGO_PFAD)
        return None


class EmailService:
    """Email-Service mit SMTP und Resend-Unterstützung.

    Provider-Priorität:
      1. Resend (falls MSM_RESEND_API_KEY gesetzt)
      2. SMTP (falls MSM_SMTP_HOST gesetzt)
    """

    # ---- Farben aus der Design-DNA (frontend/tailwind.config.ts) ----
    BG_COLOR = "#071013"          # surface / --dna-background
    SURFACE_COLOR = "#101b1f"     # surface-container (Karte)
    HEADER_COLOR = "#0b1518"      # surface-container-low (eingelassene Kaesten)
    BORDER_COLOR = "#284147"      # border
    DIVIDER_COLOR = "#1b2d33"
    PRIMARY_TEXT = "#e7f4f7"      # on-surface
    SECONDARY_TEXT = "#a9bdc3"    # on-surface-variant
    ACCENT_COLOR = "#b9f6ff"      # primary
    ACCENT_HOVER = "#67e8f9"
    CYAN_ACCENT = "#67e8f9"       # primary-fixed-dim / --dna-focus
    TEAL_ACCENT = "#5eead4"       # secondary
    ON_ACCENT = "#031316"         # on-primary
    MUTED_COLOR = "#78939b"
    WARNING_COLOR = "#f59e0b"     # --dna-warning
    WARNING_SURFACE = "#1f231d"

    # ---- Schriften wie auf der Webseite ----
    # Manrope fuer Ueberschriften, Inter fuer Text, JetBrains Mono fuer Codes.
    # Wer keine Webschriften laedt (Gmail, Outlook), bekommt die Systemschrift
    # dahinter — deshalb steht die Kette ueberall vollstaendig.
    FONT_TEXT = "Inter,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
    FONT_TITEL = "Manrope,Inter,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
    FONT_MONO = "'JetBrains Mono',Consolas,'Courier New',monospace"

    #: Content-ID des Logos. Die Vorlage verweist mit ``cid:`` darauf, und
    #: `send_email` haengt das Bild an, sobald eine Mail diesen Verweis traegt.
    LOGO_CID = "msm-logo"

    @classmethod
    def _logo_html(cls) -> str:
        """Das Logo als Verweis auf den Inline-Anhang.

        Frueher stand hier ein ``data:``-Bild. Gmail und Outlook zeigen solche
        Bilder nicht an — das Logo fehlte also genau in den Postfaechern, in
        denen die meisten Mails gelesen werden. Ein Inline-Anhang mit
        Content-ID zeigen alle gaengigen Programme.
        """
        return (
            f'<img src="cid:{cls.LOGO_CID}" alt="MSM" width="56" height="56" '
            f'style="display:block;width:56px;height:56px;border:0;outline:none;'
            f'text-decoration:none;border-radius:50%;" />'
        )

    @classmethod
    def _inline_logo(cls, html_body: str | None) -> bytes | None:
        """Die Logodaten, falls die Mail das Logo verwendet — sonst ``None``.

        Aeltere Mails aus dem KI-Postausgang wurden noch mit ``data:``-Bild
        gerendert und tragen keinen ``cid:``-Verweis; sie bekommen keinen
        Anhang, der dann ungenutzt als Bueroklammer im Postfach stuende.
        """
        if not html_body or f"cid:{cls.LOGO_CID}" not in html_body:
            return None
        return _logo_bytes()

    @staticmethod
    def _get_setting(key: str) -> str:
        """Liest Setting aus DB (Vorrang) oder Umgebungsvariable."""
        if key == "smtp_password":
            enc = PanelSettingsService.get("smtp_password_encrypted", "")
            if enc:
                try:
                    from services.auth_service import AuthService
                    return AuthService.decrypt_secret(enc, aad="msm:settings:smtp_password")
                except Exception:
                    pass
            db_val = PanelSettingsService.get("smtp_password", "")
            if db_val:
                return db_val
            return getattr(settings, "smtp_password", "")

        if key == "resend_api_key":
            enc = PanelSettingsService.get("resend_api_key_encrypted", "")
            if enc:
                try:
                    from services.auth_service import AuthService
                    return AuthService.decrypt_secret(enc, aad="msm:settings:resend_api_key")
                except Exception:
                    pass
            db_val = PanelSettingsService.get("resend_api_key", "")
            if db_val:
                return db_val
            return getattr(settings, "resend_api_key", "")

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
            ok = await EmailService._send_resend(to, subject, body, html)
        else:
            ok = await EmailService._send_smtp(to, subject, body, html)
        if not ok:
            _log.warning("Email send failed for %s (provider=%s, subject=%s) – check Resend/SMTP config or rate limits", to, provider, subject)
        return ok

    @staticmethod
    async def _send_smtp(to: str, subject: str, body: str, html: str | None = None) -> bool:
        """Der SMTP-Weg.

        **Umlaute sind hier unbedenklich, in Text wie in Betreff.** Das steht
        da, weil es einmal anders geglaubt wurde: die KI-Mails schrieben
        "faellig" und "vollstaendig" in Ersatzschreibung, waehrend die aelteren
        Vorlagen derselben Datei "durchgeführt" schrieben. In einer Mail standen
        beide Schreibweisen nebeneinander, und es sah aus wie ein Notbehelf
        gegen eine kaputte Kodierung.

        Ist es nicht. ``EmailMessage`` waehlt fuer ``set_content`` selbst den
        passenden Zeichensatz und die passende Transferkodierung, und beim
        Serialisieren kodiert es einen Betreff mit Nicht-ASCII nach RFC 2047.
        Der Resend-Weg daneben schickt ohnehin JSON ueber HTTPS, also UTF-8.

        Wer hier kuenftig Ersatzschreibungen einfuegt, macht die Mail nicht
        sicherer, sondern nur schlechter zu lesen. (Fuer Quelltextkommentare
        gilt weiter die Projektschreibweise — nur nicht fuer Text, den ein
        Mensch in seinem Postfach liest.)
        """
        msg = EmailMessage()
        msg["From"] = EmailService._get_setting("smtp_from") or settings.smtp_from
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        if html:
            msg.add_alternative(html, subtype="html")
            logo = EmailService._inline_logo(html)
            if logo:
                # multipart/related unter der HTML-Fassung: dort sucht das
                # Mailprogramm die Content-ID. ``inline`` statt des Standards
                # ``attachment``, sonst zeigt es das Logo als Anhang.
                msg.get_payload()[-1].add_related(
                    logo, "image", "png",
                    cid=f"<{EmailService.LOGO_CID}>",
                    filename="msm-logo.png",
                    disposition="inline",
                )

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
                logo = EmailService._inline_logo(html)
                if logo:
                    payload["attachments"] = [{
                        "content": base64.b64encode(logo).decode("ascii"),
                        "filename": "msm-logo.png",
                        "content_type": "image/png",
                        "content_id": EmailService.LOGO_CID,
                    }]
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
    #
    # Ein Geruest und eine Handvoll Bausteine; jede Mail dieser Datei setzt
    # sich daraus zusammen. Aufbau: Logo mit „MSM“ ueber der Karte, in der
    # Karte Kategorie, Ueberschrift, Anrede und Text, darunter die Fusszeile.
    # Alles inline gestylt und in Tabellen gesetzt — Mailprogramme werfen
    # `<style>` teils weg (Gmail in manchen Ansichten) und kennen kein Flexbox.

    @classmethod
    def _schriften_css(cls) -> str:
        """``@font-face`` fuer die Schriften der Webseite, vom eigenen Panel geladen.

        Keine Schriften von Dritten — wie im Frontend (siehe frontend/index.html).
        Die Dateien liegen unter ``frontend/public/fonts/mail/``. Apple Mail,
        iOS und Thunderbird laden sie; Gmail und Outlook ignorieren
        ``@font-face`` und nehmen die Systemschrift aus der Kette.
        """
        basis = f"{str(settings.panel_url or '').rstrip('/')}/fonts/mail"
        schnitte = (
            ("Inter", 400, "inter-latin-400-normal"),
            ("Inter", 600, "inter-latin-600-normal"),
            ("Manrope", 800, "manrope-latin-800-normal"),
            ("JetBrains Mono", 600, "jetbrains-mono-latin-600-normal"),
        )
        return "\n".join(
            f"    @font-face {{ font-family:'{familie}'; font-style:normal; "
            f"font-weight:{gewicht}; font-display:swap; "
            f"src:url('{basis}/{datei}.woff2') format('woff2'); }}"
            for familie, gewicht, datei in schnitte
        )

    @classmethod
    def _base_template(cls, title: str, content_html: str, vorschau: str = "") -> str:
        """Gibt das gemeinsame HTML-Email-Gerüst zurück.

        ``vorschau`` ist die Zeile, die das Postfach neben dem Betreff zeigt.
        Ohne sie greift es sich den ersten Text der Mail — und das war hier
        der Markenname im Kopf. Sie muss wie `title` bereits maskiert sein.
        """
        vorschau_html = ""
        if vorschau:
            # Der Fuellstoff dahinter haelt den Mailtext aus der Vorschauzeile.
            fueller = "&#8199;&#65279;&#847; " * 40
            vorschau_html = (
                f'<div style="display:none;max-height:0;max-width:0;overflow:hidden;'
                f'mso-hide:all;font-size:1px;line-height:1px;color:{cls.BG_COLOR};opacity:0;">'
                f'{vorschau}{fueller}</div>'
            )
        return f"""<!DOCTYPE html>
<html lang="de" xmlns="http://www.w3.org/1999/xhtml" xmlns:o="urn:schemas-microsoft-com:office:office">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="X-UA-Compatible" content="IE=edge">
  <meta name="color-scheme" content="dark">
  <meta name="supported-color-schemes" content="dark">
  <title>{title}</title>
  <!--[if mso]>
  <style>* {{ font-family:'Segoe UI',Arial,sans-serif !important; }}</style>
  <![endif]-->
  <!--[if !mso]><!-->
  <style>
{cls._schriften_css()}
  </style>
  <!--<![endif]-->
  <style>
    :root {{ color-scheme: dark; supported-color-schemes: dark; }}
    body {{ margin:0 !important; padding:0 !important; width:100% !important; }}
    /* Hervorhebungen im Fliesstext heller als der gedaempfte Absatz. */
    .inner strong {{ color:{cls.PRIMARY_TEXT}; font-weight:600; }}
    @media only screen and (max-width: 600px) {{
      .aussen {{ padding:24px 12px !important; }}
      .inner {{ padding:28px 22px !important; }}
      .headline {{ font-size:22px !important; }}
      .code {{ font-size:28px !important; letter-spacing:6px !important; }}
      .fakt-label {{ display:block !important; width:auto !important; padding:12px 18px 0 18px !important; }}
      .fakt-wert {{ display:block !important; border-top:0 !important; padding:2px 18px 12px 18px !important; }}
    }}
  </style>
</head>
<body style="margin:0;padding:0;background-color:{cls.BG_COLOR};font-family:{cls.FONT_TEXT};-webkit-font-smoothing:antialiased;-webkit-text-size-adjust:100%;">
  {vorschau_html}
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{cls.BG_COLOR}" style="background-color:{cls.BG_COLOR};">
    <tr>
      <td class="aussen" align="center" style="padding:44px 16px 40px 16px;">
        <table role="presentation" width="560" cellpadding="0" cellspacing="0" border="0" style="max-width:560px;width:100%;">
          <!-- Marke -->
          <tr>
            <td align="center" style="padding:0 0 26px 0;">
              <table role="presentation" cellpadding="0" cellspacing="0" border="0">
                <tr>
                  <td style="vertical-align:middle;">{cls._logo_html()}</td>
                  <td style="vertical-align:middle;padding-left:14px;font-family:{cls.FONT_TITEL};font-size:24px;font-weight:800;letter-spacing:0.5px;color:{cls.ACCENT_COLOR};line-height:1;">MSM</td>
                </tr>
              </table>
            </td>
          </tr>
          <!-- Karte -->
          <tr>
            <td bgcolor="{cls.SURFACE_COLOR}" style="background-color:{cls.SURFACE_COLOR};border:1px solid {cls.BORDER_COLOR};border-radius:16px;overflow:hidden;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
                <tr>
                  <td height="3" bgcolor="{cls.CYAN_ACCENT}" style="height:3px;line-height:3px;font-size:0;background-color:{cls.CYAN_ACCENT};background-image:linear-gradient(90deg,{cls.CYAN_ACCENT} 0%,{cls.TEAL_ACCENT} 55%,{cls.SURFACE_COLOR} 100%);border-radius:16px 16px 0 0;">&nbsp;</td>
                </tr>
                <tr>
                  <td class="inner" style="padding:36px 40px 40px 40px;font-family:{cls.FONT_TEXT};">
                    {content_html}
                  </td>
                </tr>
              </table>
            </td>
          </tr>
          <!-- Fusszeile -->
          <tr>
            <td align="center" style="padding:28px 24px 0 24px;font-family:{cls.FONT_TEXT};">
              <p style="margin:0 0 4px 0;font-size:12px;font-weight:600;color:{cls.SECONDARY_TEXT};line-height:1.5;">Maunting Service Manager</p>
              <p style="margin:0;font-size:12px;color:{cls.MUTED_COLOR};line-height:1.5;">Diese Nachricht wurde automatisch versendet.</p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""

    # ---- Bausteine ----
    # Alle nehmen fertiges HTML entgegen, ausser `_fakten`: dort maskiert der
    # Baustein selbst, weil seine Werte fast immer Fremdtext sind (IP,
    # Browserkennung, Servername, Logzeile).

    @classmethod
    def _kategorie(cls, text: str) -> str:
        if not text:
            return ""
        return (
            f'<p style="margin:0 0 10px 0;font-family:{cls.FONT_TEXT};font-size:11px;'
            f'font-weight:600;letter-spacing:1.6px;text-transform:uppercase;'
            f'color:{cls.CYAN_ACCENT};line-height:1.4;">{text}</p>'
        )

    @classmethod
    def _ueberschrift(cls, text: str) -> str:
        return (
            f'<h1 class="headline" style="margin:0 0 22px 0;font-family:{cls.FONT_TITEL};'
            f'font-size:26px;font-weight:800;letter-spacing:-0.2px;'
            f'color:{cls.PRIMARY_TEXT};line-height:1.25;">{text}</h1>'
        )

    @classmethod
    def _anrede(cls, username: str) -> str:
        """``username`` kommt bereits maskiert; leer heisst ohne Namen."""
        name = f" {username}" if username else ""
        return (
            f'<p style="margin:0 0 12px 0;font-size:15px;color:{cls.PRIMARY_TEXT};'
            f'line-height:1.65;">Hallo{name},</p>'
        )

    @classmethod
    def _absatz(cls, inhalt: str, *, abstand: int = 16) -> str:
        return (
            f'<p style="margin:0 0 {abstand}px 0;font-size:15px;color:{cls.SECONDARY_TEXT};'
            f'line-height:1.65;">{inhalt}</p>'
        )

    @classmethod
    def _kleingedruckt(cls, inhalt: str) -> str:
        return (
            f'<p style="margin:24px 0 0 0;font-size:13px;color:{cls.MUTED_COLOR};'
            f'line-height:1.6;">{inhalt}</p>'
        )

    @classmethod
    def _fakten(cls, paare: list[tuple[str, object]] | None) -> str:
        """Ein eingelassener Kasten mit Bezeichnung und Wert je Zeile."""
        zeilen = [
            (cls.html_text(label), cls.html_text(wert))
            for label, wert in (paare or [])
            if str(wert or "").strip()
        ]
        if not zeilen:
            return ""
        teile = []
        for i, (label, wert) in enumerate(zeilen):
            linie = "" if i == 0 else f"border-top:1px solid {cls.DIVIDER_COLOR};"
            teile.append(
                f'<tr>'
                f'<td class="fakt-label" width="118" style="{linie}padding:12px 12px 12px 18px;'
                f'vertical-align:top;font-size:12px;font-weight:600;color:{cls.MUTED_COLOR};'
                f'line-height:1.5;">{label}</td>'
                f'<td class="fakt-wert" style="{linie}padding:12px 18px 12px 0;vertical-align:top;'
                f'font-size:14px;color:{cls.PRIMARY_TEXT};line-height:1.5;word-break:break-word;">{wert}</td>'
                f'</tr>'
            )
        return (
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="margin:8px 0 8px 0;background-color:{cls.HEADER_COLOR};'
            f'border:1px solid {cls.BORDER_COLOR};border-radius:12px;border-collapse:separate;">'
            f'{"".join(teile)}</table>'
        )

    #: Der Satz unter Sicherheitsereignissen. Text- und HTML-Fassung nehmen
    #: ihn von hier, damit sie nicht wieder auseinanderlaufen.
    SICHERHEITSHINWEIS = (
        "Falls du diese Aktion nicht durchgeführt hast, ändere sofort dein "
        "Passwort und kontaktiere den Administrator."
    )

    @classmethod
    def _sicherheitshinweis(cls) -> str:
        return (
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="margin:24px 0 0 0;">'
            f'<tr><td style="background-color:{cls.WARNING_SURFACE};border-left:3px solid {cls.WARNING_COLOR};'
            f'border-radius:0 10px 10px 0;padding:14px 18px;font-size:13px;line-height:1.6;">'
            f'<strong style="color:{cls.WARNING_COLOR};">Warst du das nicht?</strong><br>'
            f'<span style="color:{cls.SECONDARY_TEXT};">{cls.SICHERHEITSHINWEIS}</span>'
            f'</td></tr></table>'
        )

    @classmethod
    def _cta_button(cls, url: str, label: str) -> str:
        return f"""<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:26px 0 4px 0;">
  <tr>
    <td bgcolor="{cls.ACCENT_COLOR}" style="border-radius:10px;background-color:{cls.ACCENT_COLOR};text-align:center;">
      <a href="{url}" style="display:inline-block;padding:14px 28px;font-family:{cls.FONT_TEXT};font-size:15px;font-weight:600;color:{cls.ON_ACCENT};text-decoration:none;border-radius:10px;">{label}</a>
    </td>
  </tr>
</table>"""

    @classmethod
    def _code_box(cls, code: str) -> str:
        return f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:8px 0 4px 0;">
  <tr>
    <td style="background-color:{cls.HEADER_COLOR};border:1px solid {cls.BORDER_COLOR};border-radius:12px;padding:24px 16px;text-align:center;">
      <span class="code" style="font-family:{cls.FONT_MONO};font-size:34px;font-weight:600;color:{cls.ACCENT_COLOR};letter-spacing:10px;line-height:1;">{code}</span>
    </td>
  </tr>
</table>"""

    @staticmethod
    def _vorschautext(inhalt: str) -> str:
        """Aus fertigem HTML eine Vorschauzeile: Auszeichnung weg, Entitaeten bleiben."""
        ohne = re.sub(r"<[^>]+>", "", re.sub(r"<br\s*/?>", " ", inhalt or "", flags=re.I))
        return re.sub(r"\s+", " ", ohne).strip()

    # ------------------------------------------------------------------
    # Specific templates
    # ------------------------------------------------------------------

    @classmethod
    def _password_reset_email_html(cls, username: str, url: str) -> str:
        url_sicher = html.escape(url, quote=True)
        content = "\n".join([
            cls._kategorie("Konto"),
            cls._ueberschrift("Passwort zurücksetzen"),
            cls._anrede(cls.html_text(username)),
            cls._absatz("du hast angefordert, dein Passwort zurückzusetzen. Über den Button legst du ein neues fest."),
            cls._cta_button(url_sicher, "Neues Passwort festlegen"),
            cls._kleingedruckt(
                f'Der Link ist <strong style="color:{cls.SECONDARY_TEXT};">1 Stunde</strong> gültig. '
                "Falls du das Zurücksetzen nicht beantragt hast, ignoriere diese E-Mail."
            ),
            cls._kleingedruckt(
                "Button funktioniert nicht? Kopiere diesen Link in deinen Browser:<br>"
                f'<a href="{url_sicher}" style="color:{cls.ACCENT_COLOR};text-decoration:none;word-break:break-all;">{url_sicher}</a>'
            ),
        ])
        return cls._base_template(
            "Passwort zurücksetzen", content, "Lege ein neues Passwort für dein MSM-Konto fest."
        )

    @classmethod
    def _verification_code_email_html(cls, username: str, code: str) -> str:
        content = "\n".join([
            cls._kategorie("Konto"),
            cls._ueberschrift("Dein Verifizierungscode"),
            cls._anrede(""),
            cls._absatz("gib diesen Code im Panel ein, um fortzufahren:", abstand=18),
            cls._code_box(cls.html_text(code)),
            cls._kleingedruckt(
                f'Der Code ist <strong style="color:{cls.SECONDARY_TEXT};">10 Minuten</strong> gültig. '
                "Falls du ihn nicht angefordert hast, ignoriere diese E-Mail."
            ),
        ])
        return cls._base_template(
            "Verifizierungscode", content, "Dein Code ist 10 Minuten gültig."
        )

    # ------------------------------------------------------------------
    # Public senders
    # ------------------------------------------------------------------

    @staticmethod
    async def send_password_reset_email(to: str, username: str, token: str) -> bool:
        url = f"{settings.panel_url}/reset-password?token={token}"
        subject = "Maunting Service Manager — Passwort zurücksetzen"
        body = f"""Hallo {username},

setze dein Passwort zurück:
{url}

Dieser Link ist 1 Stunde gültig.

Maunting Service Manager
"""
        html = EmailService._password_reset_email_html(username, url)
        return await EmailService.send_email(to, subject, body, html)

    @staticmethod
    async def send_verification_code_email(to: str, username: str, code: str) -> bool:
        subject = "Maunting Service Manager — Verifizierungscode"
        body = f"""Hallo,

Dein Verifizierungscode lautet:

{code}

Gültig für 10 Minuten.

Maunting Service Manager
"""
        html = EmailService._verification_code_email_html(username, code)
        return await EmailService.send_email(to, subject, body, html)

    # ------------------------------------------------------------------
    # Security notification emails
    # ------------------------------------------------------------------

    @staticmethod
    def html_text(value: object) -> str:
        """Fremdtext, der in eine Mail soll — maskiert, mit erhaltenen Umbruechen.

        Das Grundgeruest dieser Datei setzt seine Bausteine als **rohes HTML**
        zusammen, und mehrere Aufrufer nutzen das absichtlich (`<strong>` um
        einen Statuswert). Solange dort nur Literale aus dem Code stehen, ist
        das harmlos.

        Sobald aber ein Servername, ein Benutzername, eine Vorfallbeschreibung
        oder — neu — ein von einem Modell geschriebener Bericht hineinfliesst,
        ist es eine Injection: der Name eines Servers stammt aus einem Formular
        oder aus einer Shop-Bestellung, die Vorfallbeschreibung aus einer
        Logzeile auf einem Server, auf dem Fremde spielen, und der Modelltext
        aus beidem.

        `<a href="...">Hier klicken</a>` in einer Mail, die aussieht, als kaeme
        sie vom Panel, ist ein brauchbarer Phishing-Traeger. Deshalb geht
        Fremdtext durch diese Funktion, bevor er in eine Vorlage kommt — und
        nicht die Vorlage durch eine Maskierung, die die gewollten Auszeichnungen
        mit zerstoeren wuerde.

        Umbrueche bleiben erhalten: ein Bericht ueber mehrere Absaetze waere
        sonst eine einzige Zeile.
        """
        return html.escape(str(value or ""), quote=True).replace("\n", "<br>")

    @classmethod
    def _notification_email_html(
        cls,
        username: str,
        title: str,
        message: str,
        detail: str = "",
        *,
        kategorie: str = "",
        fakten: list[tuple[str, object]] | None = None,
        sicherheitshinweis: bool = False,
    ) -> str:
        # `username` und `title` werden **hier** maskiert: kein Aufrufer gibt
        # dort Auszeichnung mit, und der Benutzername ist frei waehlbar.
        # `message` und `detail` bleiben roh — mehrere Aufrufer bauen dort
        # bewusst `<strong>` ein und maskieren ihre Fremdanteile selbst mit
        # `html_text`. `fakten` maskiert `_fakten` selbst.
        #
        # `sicherheitshinweis` ist aus, bis ein Aufrufer ihn anfordert. Er hing
        # frueher unter jeder Mail dieser Vorlage — auch unter „Server
        # installiert“ und unter Terminerinnerungen, wo „Falls du diese Aktion
        # nicht durchgeführt hast“ keinen Sinn ergibt (derselbe Fehler, den die
        # KI-Berichte schon einmal hatten, siehe `_ai_report_email_html`).
        username = cls.html_text(username)
        title = cls.html_text(title)
        teile = [
            cls._kategorie(cls.html_text(kategorie)),
            cls._ueberschrift(title),
            cls._anrede(username),
            cls._absatz(message),
        ]
        if detail:
            teile.append(cls._absatz(detail))
        teile.append(cls._fakten(fakten))
        if sicherheitshinweis:
            teile.append(cls._sicherheitshinweis())
        return cls._base_template(
            title, "\n".join(t for t in teile if t), cls._vorschautext(message)
        )

    @classmethod
    def _ai_report_email_html(
        cls,
        username: str,
        titel: str,
        absaetze: list[str],
        punkte: list[str] | None = None,
        schluss: str | None = None,
        fusszeile: str | None = None,
        cta: tuple[str, str] | None = None,
    ) -> str:
        """Die Vorlage fuer Berichte der KI. Neben `_notification_email_html`, nicht darin.

        Zwei Unterschiede, und beide sind der Grund fuer die eigene Vorlage.

        **Kein Sicherheitshinweis.** `_notification_email_html` haengt
        unbedingt „Falls du diese Aktion nicht durchgeführt hast, ändere sofort
        dein Passwort“ an. Fuer einen neuen Login ist das richtig. Unter einem
        Serverstatusbericht ist es falsch: der Benutzer hat nichts durchgefuehrt,
        die KI hat — er hat sie ja darum gebeten. Der Betreiber hat den Satz
        genau dort vorgefunden und sich zu Recht gewundert. Nebenbei war es
        schon vorher widerspruechlich, denn die Textfassung derselben Mail
        enthielt ihn nie.

        **Alles wird hier maskiert.** Die andere Vorlage laesst `message` und
        `detail` roh durch, weil ihre Aufrufer dort absichtlich `<strong>`
        einbauen und ihre Fremdanteile selbst behandeln. Hier gilt das
        Gegenteil: **jedes** Feld stammt aus einem Modell, und ein Modell, das
        ueber einen praeparierten Servernamen oder eine Logzeile dazu gebracht
        wurde, `<a href="...">` zu schreiben, haette sonst einen
        Phishing-Traeger in einer Mail, die aussieht, als kaeme sie vom Panel.
        Deshalb liefert das Modell **nur Text und nie Auszeichnung**, und die
        Struktur — Absaetze, Aufzaehlung — kommt aus den Feldern statt aus
        Markdown. Das erspart zugleich einen Markdown-Leser, den es hier nicht
        gibt: `**Laufend:**` stand deshalb woertlich in der Mail.

        `titel` und `fusszeile` stammen ausdruecklich **nicht** vom Modell,
        sondern vom Panel — siehe `send_ai_task_report`.

        `cta` ist der einzige Weg, auf dem in eine KI-Mail ein Link kommt, und
        er geht durch `_pruefe_cta`: alles, was nicht mit der Paneladresse aus
        den Einstellungen beginnt, faellt weg. Ohne diese Pruefung waere die
        eine maskierungsfreie Stelle dieser Vorlage der Traeger, nach dem eine
        Prompt-Injection sucht.
        """
        username = cls.html_text(username)
        titel_sicher = cls.html_text(titel)

        teile = [
            cls._kategorie("KI-Assistent"),
            cls._ueberschrift(titel_sicher),
            cls._anrede(username),
        ]
        for absatz in absaetze:
            if not str(absatz or "").strip():
                continue
            teile.append(cls._absatz(cls.html_text(absatz), abstand=14))
        if punkte:
            zeilen = "".join(
                f'<li style="margin:0 0 6px 0;">{cls.html_text(punkt)}</li>'
                for punkt in punkte
                if str(punkt or "").strip()
            )
            if zeilen:
                teile.append(
                    f'<ul style="margin:0 0 16px 0;padding-left:20px;font-size:15px;'
                    f'color:{cls.SECONDARY_TEXT};line-height:1.65;">{zeilen}</ul>'
                )
        if schluss and str(schluss).strip():
            teile.append(cls._absatz(cls.html_text(schluss), abstand=8))
        geprueft = cls._pruefe_cta(cta)
        if geprueft is not None:
            url, label = geprueft
            teile.append(cls._cta_button(url, cls.html_text(label)))
        if fusszeile and str(fusszeile).strip():
            teile.append(cls._kleingedruckt(cls.html_text(fusszeile)))
        erster = next((str(a).strip() for a in absaetze if str(a or "").strip()), "")
        # Der **maskierte** Titel auch hier hinein: `_base_template` setzt ihn
        # roh in `<title>`, genau wie es `_notification_email_html` vormacht
        # (dort wird `title` einmal oben maskiert und danach nur noch das
        # Ergebnis weitergereicht). Mit dem Rohwert stuende die Auszeichnung im
        # Kopf des Dokuments statt im Text — dieselbe Luecke, nur woanders.
        return cls._base_template(
            titel_sicher, "\n".join(teile), cls.html_text(erster)
        )

    @classmethod
    def _pruefe_cta(cls, cta: tuple[str, str] | None) -> tuple[str, str] | None:
        """Laesst genau die Links durch, die auf das eigene Panel zeigen.

        Der Rahmen einer KI-Mail geht als JSON durch die Datenbank und wird vom
        Postausgang wieder eingelesen — moeglicherweise Tage spaeter, aus einer
        Zeile, die inzwischen jemand angefasst hat. Der Knopf ist die einzige
        Stelle dieser Vorlage, die nicht maskiert wird; ohne Pruefung waere er
        der Traeger, auf den es jede Prompt-Injection abgesehen hat.

        Geprueft wird gegen `settings.panel_url` und nicht gegen ein Muster wie
        "beginnt mit https": ein fremdes HTTPS-Ziel ist genau der Fall, den
        diese Zeile verhindern soll.
        """
        if not cta:
            return None
        url, label = cta
        url = str(url or "").strip()
        label = str(label or "").strip()
        if not url or not label:
            return None
        basis = str(settings.panel_url or "").rstrip("/")
        if not basis or not url.startswith(basis + "/"):
            _log.warning("CTA-Link verworfen: zeigt nicht auf das Panel")
            return None
        return url, label

    @staticmethod
    def _ai_report_email_text(
        username: str,
        absaetze: list[str],
        punkte: list[str] | None = None,
        schluss: str | None = None,
        fusszeile: str | None = None,
        cta: tuple[str, str] | None = None,
    ) -> str:
        """Dieselben Felder als reiner Text.

        Aus **derselben** Quelle wie die HTML-Fassung, und das ist der Punkt:
        vorher wurden Text- und HTML-Fassung getrennt zusammengesetzt und gingen
        auseinander — die eine trug den Sicherheitshinweis, die andere nicht.
        Wer beides aus einem Satz Felder baut, kann sie nicht mehr auseinander
        laufen lassen.
        """
        zeilen = [f"Hallo {username},", ""]
        for absatz in absaetze:
            if str(absatz or "").strip():
                zeilen.extend([str(absatz).strip(), ""])
        for punkt in punkte or []:
            if str(punkt or "").strip():
                zeilen.append(f"- {str(punkt).strip()}")
        if punkte:
            zeilen.append("")
        if schluss and str(schluss).strip():
            zeilen.extend([str(schluss).strip(), ""])
        # Der Link **auch** in der Textfassung, und als nackte Adresse. Ein
        # Knopf, den es nur im HTML gibt, fehlt genau denen, die ihr Postfach
        # auf Textdarstellung stehen haben — und die Mail waere dann eine Frage
        # ohne Antwortmoeglichkeit.
        geprueft = EmailService._pruefe_cta(cta)
        if geprueft is not None:
            url, label = geprueft
            zeilen.extend([f"{label}: {url}", ""])
        if fusszeile and str(fusszeile).strip():
            zeilen.extend([str(fusszeile).strip(), ""])
        zeilen.append("Maunting Service Manager")
        return "\n".join(zeilen) + "\n"

    @staticmethod
    def _fakten_als_text(fakten: list[tuple[str, object]] | None) -> str:
        """Dieselben Fakten fuer die Textfassung, eine Zeile je Paar."""
        return "\n".join(
            f"{label}: {wert}" for label, wert in (fakten or []) if str(wert or "").strip()
        )

    @staticmethod
    async def send_security_notification(
        to: str,
        username: str,
        title: str,
        message: str,
        detail: str = "",
        *,
        fakten: list[tuple[str, object]] | None = None,
    ) -> bool:
        """Zentrale Funktion für Security-/Login-bezogene Benachrichtigungen (neuer Login, OAuth-Link, Passwort-Änderung etc.).
        Nutzt _notification_email_html + send_email. Alle Security-Events gehen hier durch (KISS + zentrale Wartung).

        ``message`` und ``detail`` duerfen `<strong>` tragen; die Textfassung
        bekommt sie ohne Auszeichnung.
        """
        subject = f"Maunting Service Manager — {title}"
        absaetze = [html.unescape(EmailService._vorschautext(message))]
        if detail:
            absaetze.append(html.unescape(EmailService._vorschautext(detail)))
        fakten_text = EmailService._fakten_als_text(fakten)
        if fakten_text:
            absaetze.append(fakten_text)
        absaetze.append(EmailService.SICHERHEITSHINWEIS)
        body = f"Hallo {username},\n\n" + "\n\n".join(absaetze) + "\n\nMaunting Service Manager\n"
        html_body = EmailService._notification_email_html(
            username, title, message, detail,
            kategorie="Kontosicherheit", fakten=fakten, sicherheitshinweis=True,
        )
        return await EmailService.send_email(to, subject, body, html_body)

    @staticmethod
    async def send_password_changed_notification(to: str, username: str) -> bool:
        return await EmailService.send_security_notification(
            to, username, "Passwort geändert", "Dein Passwort wurde soeben geändert.",
        )

    @staticmethod
    async def send_new_device_login_notification(to: str, username: str, ip: str, user_agent: str) -> bool:
        """Nutzt die zentrale send_security_notification (KISS).

        IP und Browserkennung gehen als Fakten hinein und damit maskiert —
        die Kennung schickt der Client selbst und kann beliebigen Text tragen.
        """
        return await EmailService.send_security_notification(
            to, username, "Neuer Login erkannt",
            "Soeben hat sich jemand von einem unbekannten Gerät in dein Konto eingeloggt.",
            fakten=[("IP-Adresse", ip), ("Gerät", user_agent)],
        )

    @staticmethod
    async def send_2fa_status_notification(to: str, username: str, enabled: bool) -> bool:
        action = "aktiviert" if enabled else "deaktiviert"
        return await EmailService.send_security_notification(
            to, username, f"2FA {action}",
            f"Die Zwei-Faktor-Authentifizierung für dein Konto wurde <strong>{action}</strong>.",
        )

    @staticmethod
    async def send_server_status_notification(to: str, username: str, server_name: str, status: str) -> bool:
        subject = f"Maunting Service Manager — Server-Status: {server_name}"
        body = f"""Hallo {username},

Der Server "{server_name}" hat seinen Status geändert: {status}

Maunting Service Manager
"""
        html = EmailService._notification_email_html(
            username, "Server-Status geändert",
            f'Der Server <strong>{EmailService.html_text(server_name)}</strong> hat seinen '
            f'Status geändert.',
            kategorie="Server",
            fakten=[("Neuer Status", status)],
        )
        return await EmailService.send_email(to, subject, body, html)

    @staticmethod
    async def send_guardian_incident_notification(
        to: str, username: str, server_name: str, incident_type: str, status: str, details: str = ""
    ) -> bool:
        subject = f"Maunting Service Manager — Guardian Alert: {server_name}"
        body = f"""Hallo {username},

Die Guardian Engine hat ein Ereignis beim Server "{server_name}" registriert.

Vorfall: {incident_type}
Status: {status}
Details: {details}

Bitte überprüfe den Server im Dashboard.

Maunting Service Manager — Guardian Engine
"""
        # Alle vier Werte sind Fremdtext: der Servername kommt aus einem
        # Formular oder aus einer Shop-Bestellung, Art und Stand aus dem Agenten,
        # die Beschreibung aus einer Logzeile eines Servers, auf dem Fremde
        # spielen. Unmaskiert waren sie ein Phishing-Traeger in einer Mail, die
        # aussieht, als kaeme sie vom Panel. Der Name wird hier maskiert, die
        # drei anderen maskiert `_fakten`.
        html = EmailService._notification_email_html(
            username,
            "Guardian Engine Alert",
            f'Die Guardian Engine hat ein Ereignis beim Server '
            f'<strong>{EmailService.html_text(server_name)}</strong> registriert. '
            f'Bitte überprüfe den Server im Dashboard.',
            kategorie="Guardian",
            fakten=[("Vorfall", incident_type), ("Status", status), ("Details", details)],
        )
        return await EmailService.send_email(to, subject, body, html)


    @staticmethod
    def _ai_betreff_praefix(titel: str) -> str:
        """Der Anteil der Betreffzeile, der **nie** vom Modell kommt.

        Eigene Funktion, seit der Betreff zweimal entsteht: einmal beim
        Einreihen in den Ausgangskorb (mit dem festen Text) und einmal im
        Arbeiter (mit der verfassten Fassung). Der Praefix wandert dazwischen
        als Teil des Rahmens durch die Datenbank — es muss also einen Ort geben,
        an dem er gebildet wird, und genau einen.
        """
        return f"Maunting Service Manager — {titel}"

    @staticmethod
    def _ai_betreffzeile_aus_praefix(praefix: str, zusatz: str) -> str:
        """Setzt die Betreffzeile zusammen und macht sie zustellbar.

        Zwei Dinge in einer Funktion, weil sie zusammengehoeren.

        Die **Reihenfolge** haelt die Zusage fest: vorne die Kennung des Panels
        und das Zustandswort — beides Tatsachen aus dem Lauf und beide im
        Praefix —, dahinter erst das, was das Modell beisteuert. Ein Modell, das
        beschoenigt, kann den Betreff faerben, aber nie das Ergebnis darin
        umschreiben.

        Die **Bereinigung** verhindert einen Vorfall, der die ganze Mail kostete
        und dabei Erfolg meldete: `send_email` setzt ``msg["Subject"]`` vor dem
        ``try``. Ein Zeilenumbruch darin wirft `ValueError`, die an der
        Fehlerbehandlung des Versands vorbei bis zum Aufrufer durchlaeuft und
        dort als Warnung endet — gruener Lauf, keine Mail. Bereinigt wird die
        **fertige** Zeile und nicht nur der Modellteil: Servername und
        Aufgabentitel kommen aus Formularen und aus einem Chat und koennen
        denselben Umbruch tragen.

        Der Ersatz ``"Maunting Service Manager"`` fuer einen fehlenden Praefix
        ist kein Schoenheitsfehler: der Praefix kann aus einem Rahmen kommen,
        der eine Prozessgrenze und ein JSON-Feld hinter sich hat. Ein leerer
        Betreff waere dort keine Mail mehr.
        """
        from services.ai_mail_text import (
            MAX_BETREFFZEILE_ZEICHEN,
            betreff_bereinigen,
        )

        zeile = str(praefix or "Maunting Service Manager")
        if zusatz:
            zeile += f": {zusatz}"
        return betreff_bereinigen(zeile, grenze=MAX_BETREFFZEILE_ZEICHEN)

    @staticmethod
    def _ai_mailtext(mailtext, *, fakt: str, rueckfall: str) -> tuple[str, list[str], list[str], str | None]:
        """Betreff, Absaetze, Punkte und Schluss — vom Modell oder aus dem Code.

        ``mailtext`` ist ein `ai_mail_text.Mailtext` oder ``None``. ``None``
        heisst „das Modell konnte nicht“ und ist kein Fehler: dann steht der
        feste Text in der Mail, und verschickt wird trotzdem. Der Verfassungs-
        schritt darf den Versand verschoenern, aber niemals verhindern.

        ``fakt`` ist der Satz des Panels und steht **immer** an erster Stelle,
        auch wenn das Modell geschrieben hat. Er traegt das Ergebnis, und das
        Ergebnis stammt aus dem Endzustand des Laufs, nicht aus der
        Selbsteinschaetzung des Modells.
        """
        if mailtext is not None and getattr(mailtext, "absaetze", None):
            return (
                str(getattr(mailtext, "betreff", "") or ""),
                [fakt] + list(mailtext.absaetze),
                list(getattr(mailtext, "punkte", None) or []),
                getattr(mailtext, "schluss", None),
            )
        return "", [fakt, rueckfall], [], None

    # ── Rahmen und Rendern ────────────────────────────────────────────────
    #
    # Bis hierher rendern und senden die `send_ai_*`-Funktionen in einem Zug.
    # Das ging, solange der Verfassungsschritt unmittelbar davor lag. Seit der
    # Ausgangskorb die Angaben speichert und der Arbeiter daraus verfasst,
    # entsteht **dieselbe** Mail an zwei Stellen: einmal beim Einreihen als
    # Rueckfall und einmal im Arbeiter mit dem Modelltext. Zwei Stellen, die
    # dasselbe zusammensetzen, laufen auseinander — die Textfassung dieser Mail
    # trug einmal einen Sicherheitshinweis, den die HTML-Fassung nicht hatte,
    # und niemand sah es.
    #
    # Deshalb ist es hier zerlegt: `ai_rahmen_*` sammelt je Anlass das, was das
    # Panel beitraegt (und nur das — reine Funktionen, keine Datenbank, kein
    # Versand), `ai_mail_rendern` macht daraus mit oder ohne Modelltext das
    # fertige Tripel. Der Rahmen ist ein `dict`, weil er als JSON durch die
    # Datenbank geht.

    @staticmethod
    def ai_rahmen_task(
        username: str, *, task_title: str, plan_text: str, geschafft: bool
    ) -> dict:
        """Der Panelanteil des Aufgabenberichts.

        `geschafft` ist der Endzustand des Laufs und bestimmt Ueberschrift und
        Zustandswort. Ein Modell, das sich irrt oder beschoenigt, faerbt
        hoechstens den Zusatz dahinter.
        """
        titel = (
            "KI-Aufgabe erledigt" if geschafft else "KI-Aufgabe nicht abgeschlossen"
        )
        zustand = "erledigt" if geschafft else "nicht abgeschlossen"
        return {
            "username": str(username or ""),
            "titel": titel,
            "betreff_praefix": EmailService._ai_betreff_praefix(titel),
            # Wonach der Betreff greift, wenn das Modell nichts geliefert hat.
            "betreff_ersatz": str(task_title or ""),
            "fakt": (
                f'Deine KI-Aufgabe "{task_title}" ({plan_text}) war fällig. '
                f"Ergebnis: {zustand}."
            ),
            "fusszeile": "Den vollständigen Verlauf findest du im KI-Chat des Panels.",
        }

    @staticmethod
    def ai_rahmen_healing(
        username: str,
        *,
        server_name: str,
        incident_type: str,
        geheilt: bool,
        backup_name: str | None = None,
    ) -> dict:
        """Der Panelanteil des Heilungsberichts.

        `geheilt` ist die Und-Verknuepfung aus Laufzustand und Vorfallzustand
        (siehe `ai_guardian_report`) — eine Tatsache, keine Selbsteinschaetzung.
        """
        titel = (
            "Guardian: Problem behoben" if geheilt
            else "Guardian: Problem nicht behoben"
        )
        zustand = "behoben" if geheilt else "nicht behoben"
        return {
            "username": str(username or ""),
            "titel": titel,
            "betreff_praefix": EmailService._ai_betreff_praefix(titel),
            "betreff_ersatz": str(server_name or ""),
            "fakt": (
                f'Auf dem Server "{server_name}" gab es eine Störung '
                f"({incident_type}). Der KI-Assistent hat sie eigenständig "
                f"bearbeitet. Ergebnis: {zustand}."
            ),
            "fusszeile": (
                f"Vor dem Eingriff wurde ein Backup angelegt: {backup_name}"
                if backup_name else None
            ),
        }

    @staticmethod
    def ai_rahmen_freigabe(
        username: str,
        *,
        tool_name: str,
        server_name: str,
        token: str,
        stunden: int,
    ) -> dict:
        """Der Panelanteil der Freigabemail — samt Link.

        **Der Link wird hier gebaut, nicht vom Modell geschrieben.** Der
        Systemprompt verbietet dem Modell Links, und `_ai_report_email_html`
        maskiert jedes Modellfeld; ein Modell, das ueber eine praeparierte
        Logzeile dazu gebracht wurde, eine Adresse zu nennen, koennte sonst
        einen Phishing-Traeger in eine Mail setzen, die aussieht, als kaeme sie
        vom Panel. Deshalb steht die Adresse im Rahmen, wird ueber `_cta_button`
        gerendert und stammt aus `settings.panel_url`.

        **Und sie zeigt auf eine Seite, nicht auf eine Aktion.** ``GET`` zeigt,
        was ansteht; erst ein ``POST`` von dieser Seite entscheidet. Mailscanner
        und Vorschaudienste klicken Links — ein GET, das ausfuehrt, waere ein
        Servereingriff durch einen Virenscanner.

        Der Werkzeugname bleibt roh stehen (``propose_file_delete`` und nicht
        "Datei loeschen"): der Rahmen kennt keine Uebersetzungen, und ein hier
        erfundener deutscher Name koennte von dem abweichen, was auf der
        Freigabeseite steht. Die Seite loest ihn auf.
        """
        titel = "KI wartet auf deine Freigabe"
        ziel = f' auf "{server_name}"' if server_name else ""
        url = f"{settings.panel_url.rstrip('/')}/ai/freigabe/{token}"
        return {
            "username": str(username or ""),
            "titel": titel,
            "betreff_praefix": EmailService._ai_betreff_praefix(titel),
            "betreff_ersatz": str(server_name or ""),
            "fakt": (
                f"Der KI-Assistent bearbeitet gerade eine Störung{ziel} und "
                f'braucht dafür deine Zustimmung zu "{tool_name}". Der autonome '
                "Modus führt diesen Schritt nicht von selbst aus."
            ),
            # Der Rueckfalltext, der beim Einreihen gerendert wird. Ab dem
            # zweiten Zustellversuch ruft der Postausgang das Modell nicht mehr,
            # und eine Freigabemail ohne diesen Satz waere eine Frage ohne
            # Antwortmoeglichkeit.
            "rueckfall": (
                "Öffne den Link unten, dort siehst du den Vorgang und "
                "entscheidest. Tust du nichts, passiert nichts: der Vorschlag "
                f"verfällt nach {stunden} Stunden."
            ),
            "cta_url": url,
            "cta_label": "Vorgang ansehen",
            "fusszeile": (
                f"Dieser Link gilt {stunden} Stunden und lässt sich genau "
                "einmal verwenden. Gib ihn nicht weiter."
            ),
        }

    @staticmethod
    def ai_rahmen_worker(username: str, *, auftrag_titel: str, frage: bool) -> dict:
        """Der Panelanteil einer Worker-Meldung (docs/agentic-framework.md).

        Bewusst ohne Zustandswort im Titel: ob der Auftrag gelang, steht im
        geschwaerzten Meldungstext, den das Modell verfasst — die Meldestelle
        weiss es nicht, und ein geratenes "erledigt" im Betreff waere eine
        Behauptung des Panels ueber etwas, das nur der Bericht selbst sagt.
        """
        titel = (
            "Dein KI-Auftrag hat eine Frage" if frage
            else "Dein KI-Auftrag hat berichtet"
        )
        return {
            "username": str(username or ""),
            "titel": titel,
            "betreff_praefix": EmailService._ai_betreff_praefix(titel),
            "betreff_ersatz": str(auftrag_titel or ""),
            "fakt": (
                f'Dein Auftrag "{auftrag_titel}" an den KI-Assistenten hat '
                + ("eine Rückfrage gestellt." if frage else "ein Ergebnis gemeldet.")
            ),
            "fusszeile": "Den vollständigen Verlauf findest du im KI-Chat des Panels.",
        }

    #: Der feste Text der Testmail. Steht als Konstante da, seit ihn zwei
    #: Stellen brauchen: `send_ai_test_email` und der Werkzeughandler, der die
    #: Mail in den Ausgangskorb legt und dabei den Rueckfall gleich mitrendert.
    #: Bei genau dieser Mail ist der Rueckfall wichtiger als bei den anderen
    #: beiden — sie ist das Messgeraet fuer den Versandweg und darf nicht
    #: ausgerechnet dann ausbleiben, wenn das Modell klemmt.
    AI_TESTMAIL_RUECKFALL = (
        "Wenn du sie liest, funktioniert der im Panel eingerichtete "
        "Versandweg — und damit auch die Berichte, die dir die KI künftig "
        "zu deinen Aufgaben und zu behobenen Störungen schickt."
    )

    @staticmethod
    def ai_rahmen_test(username: str) -> dict:
        """Der Panelanteil der Testmail.

        Ohne Zustandswort und ohne Ersatzbetreff: hier gibt es kein Ergebnis zu
        melden, die Mail beweist sich selbst, indem sie ankommt.
        """
        titel = "Testmail vom KI-Assistenten"
        return {
            "username": str(username or ""),
            "titel": titel,
            "betreff_praefix": EmailService._ai_betreff_praefix(titel),
            "betreff_ersatz": "",
            "fakt": "Diese Mail hat der KI-Assistent auf deine Bitte hin verschickt.",
            "fusszeile": None,
        }

    @staticmethod
    def ai_mail_rendern(
        rahmen: dict, *, mailtext=None, rueckfall: str = ""
    ) -> tuple[str, str, str]:
        """Aus Rahmen und (optionalem) Modelltext die fertige Mail: Betreff, Text, HTML.

        Rein: kein Versand, keine Datenbank, kein Modellaufruf. Genau deshalb ist
        sie zweimal benutzbar — beim Einreihen in den Ausgangskorb, wo
        ``mailtext`` ``None`` ist und der feste ``rueckfall`` traegt, und im
        Arbeiter, wo der verfasste Text vorliegt und ``rueckfall`` nicht mehr
        gebraucht wird.

        ``rahmen`` kommt im zweiten Fall aus einem JSON-Feld der Datenbank und
        kann deshalb alt, unvollstaendig oder von einer aelteren Fassung des
        Codes sein. Jeder Zugriff ist entsprechend nachsichtig: ein fehlender
        Schluessel kostet einen Satz, nie die Mail.
        """
        betreff, absaetze, punkte, schluss = EmailService._ai_mailtext(
            mailtext, fakt=str(rahmen.get("fakt") or ""), rueckfall=rueckfall
        )
        zusatz = betreff or str(rahmen.get("betreff_ersatz") or "")
        subject = EmailService._ai_betreffzeile_aus_praefix(
            str(rahmen.get("betreff_praefix") or ""), zusatz
        )
        username = str(rahmen.get("username") or "")
        titel = str(rahmen.get("titel") or "")
        fusszeile = rahmen.get("fusszeile") or None
        cta = None
        if rahmen.get("cta_url"):
            cta = (str(rahmen.get("cta_url")), str(rahmen.get("cta_label") or "Öffnen"))
        body = EmailService._ai_report_email_text(
            username, absaetze, punkte, schluss, fusszeile, cta
        )
        html_body = EmailService._ai_report_email_html(
            username, titel, absaetze, punkte, schluss, fusszeile, cta
        )
        return subject, body, html_body

    @staticmethod
    async def send_ai_healing_report(
        to: str,
        username: str,
        *,
        server_name: str,
        incident_type: str,
        geheilt: bool,
        bericht: str,
        backup_name: str | None = None,
        mailtext=None,
    ) -> bool:
        """Der Bericht der KI ueber eine Heilung, die ohne den Benutzer lief.

        Anders als jede andere Mail dieser Datei steht der Fliesstext hier nicht
        im Code: er stammt vom Modell — seit `ai_mail_text` sogar der Betreff.
        Das hat zwei Folgen, und beide sind beruecksichtigt.

        Erstens die Maskierung. Modelltext ist unvertrauenswuerdige Eingabe,
        unabhaengig davon, was im Systemprompt steht — ein Modell, das ueber
        eine praeparierte Logzeile dazu gebracht wurde, `<a href="...">` zu
        schreiben, haette sonst einen Phishing-Traeger in einer Mail, die
        aussieht, als kaeme sie vom Panel. Deshalb geht diese Mail durch
        `_ai_report_email_html`, das **jedes** Feld maskiert, und nicht mehr
        durch die Sicherheitsvorlage, die Fliesstext roh durchlaesst.

        Zweitens die Ueberschrift. Sie kommt **nicht** vom Modell, sondern aus
        `geheilt` — einer Tatsache des Panels. Ein Modell, das sich irrt oder
        beschoenigt, soll nicht auch noch die Betreffzeile bestimmen; es darf
        sie nur ergaenzen.
        """
        rahmen = EmailService.ai_rahmen_healing(
            username,
            server_name=server_name,
            incident_type=incident_type,
            geheilt=geheilt,
            backup_name=backup_name,
        )
        subject, body, html_body = EmailService.ai_mail_rendern(
            rahmen, mailtext=mailtext, rueckfall=bericht
        )
        return await EmailService.send_email(to, subject, body, html_body)

    @staticmethod
    async def send_ai_task_report(
        to: str,
        username: str,
        *,
        task_title: str,
        plan_text: str,
        geschafft: bool,
        bericht: str,
        mailtext=None,
    ) -> bool:
        """Der Bericht ueber einen stehenden Auftrag, der faellig war.

        Dieselben zwei Regeln wie beim Heilungsbericht, aus denselben Gruenden:

        Jedes Feld ist unvertrauenswuerdige Eingabe und wird maskiert. Das gilt
        hier fuer **mehr** Felder als dort: auch der Name der Aufgabe und der
        Plantext gehen letztlich auf etwas zurueck, das ein Mensch in einen Chat
        getippt und ein Modell umformuliert hat.

        Und die Ueberschrift kommt aus `geschafft`, einer Tatsache des Panels
        (dem Endzustand des Laufs), nicht aus der Selbsteinschaetzung des
        Modells. Ein Auftrag, der still gescheitert ist, ist die wichtigere
        Nachricht von beiden — niemand sass davor.
        """
        rahmen = EmailService.ai_rahmen_task(
            username,
            task_title=task_title,
            plan_text=plan_text,
            geschafft=geschafft,
        )
        subject, body, html_body = EmailService.ai_mail_rendern(
            rahmen, mailtext=mailtext, rueckfall=bericht
        )
        return await EmailService.send_email(to, subject, body, html_body)

    @staticmethod
    async def send_ai_test_email(to: str, username: str, *, mailtext=None) -> bool:
        """Die Mail, mit der sich der eingerichtete Versandweg nachpruefen laesst.

        Auch sie schreibt die KI selbst — der Betreiber hat ausdruecklich
        verlangt, dass hier nichts Vorgefertigtes mehr steht. Der feste Text
        bleibt trotzdem im Code, und das ist bei dieser Mail wichtiger als bei
        den anderen beiden: sie ist das Messgeraet fuer den Versandweg. Eine
        Testmail, die ausgerechnet dann ausbleibt, wenn das Modell klemmt,
        misst das Falsche und laesst den Betreiber am Mailversand zweifeln.

        Sie geht ueber `send_email` und damit ueber genau den Weg, den auch ein
        Aufgaben- oder Heilungsbericht nimmt. Das ist der Zweck: getestet wird
        nicht irgendein Versand, sondern der, auf den es spaeter ankommt.
        """
        subject, body, html_body = EmailService.ai_mail_rendern(
            EmailService.ai_rahmen_test(username),
            mailtext=mailtext,
            rueckfall=EmailService.AI_TESTMAIL_RUECKFALL,
        )
        return await EmailService.send_email(to, subject, body, html_body)

    @staticmethod
    async def send_server_installed_notification(to: str, username: str, server_name: str) -> bool:
        subject = f"Maunting Service Manager — Server installiert: {server_name}"
        body = f"""Hallo {username},

Der Server "{server_name}" wurde erfolgreich installiert und ist bereit.

Maunting Service Manager
"""
        html = EmailService._notification_email_html(
            username, "Server installiert",
            f'Der Server <strong>{EmailService.html_text(server_name)}</strong> wurde '
            f'erfolgreich installiert und ist bereit.',
            kategorie="Server",
        )
        return await EmailService.send_email(to, subject, body, html)

    @staticmethod
    async def send_user_added_to_server_notification(to: str, username: str, server_name: str, added_by: str) -> bool:
        subject = f"Maunting Service Manager — Zu Server hinzugefügt: {server_name}"
        body = f"""Hallo {username},

Du wurdest von {added_by} zum Server "{server_name}" hinzugefügt.

Maunting Service Manager
"""
        html = EmailService._notification_email_html(
            username, "Zu Server hinzugefügt",
            f'Du wurdest von <strong>{EmailService.html_text(added_by)}</strong> '
            f'zum Server <strong>{EmailService.html_text(server_name)}</strong> hinzugefügt.',
            kategorie="Server",
        )
        return await EmailService.send_email(to, subject, body, html)

    # ------------------------------------------------------------------
    # OAuth Linking / Unlinking + Account Registration notifications
    # (nutzen das bestehende _notification_email_html Template + user.email_notifications Flag)
    # Deaktivierbar über die Glocke im Topbar (bestehende Einstellung).
    # ------------------------------------------------------------------

    @staticmethod
    async def send_oauth_linked_notification(to: str, username: str, provider_name: str) -> bool:
        """OAuth Link: nutzt die zentrale send_security_notification (wie neuer Login etc.)."""
        return await EmailService.send_security_notification(
            to, username,
            f"{provider_name} verknüpft",
            f"Dein <strong>{EmailService.html_text(provider_name)}</strong>-Account wurde erfolgreich mit deinem MSM-Konto verknüpft."
        )

    @staticmethod
    async def send_oauth_unlinked_notification(to: str, username: str, provider_name: str) -> bool:
        """OAuth Unlink: nutzt die zentrale send_security_notification (wie neuer Login etc.)."""
        return await EmailService.send_security_notification(
            to, username,
            f"{provider_name} Verknüpfung aufgehoben",
            f"Die Verknüpfung zu deinem <strong>{EmailService.html_text(provider_name)}</strong>-Account wurde aufgehoben."
        )

    @staticmethod
    async def send_account_registered_notification(to: str, username: str) -> bool:
        """Account Registration: nutzt die zentrale send_security_notification."""
        return await EmailService.send_security_notification(
            to, username,
            "Konto erfolgreich erstellt",
            "Dein Konto wurde erfolgreich erstellt. Willkommen!"
        )

    # ------------------------------------------------------------------
    # Update-Benachrichtigungen (für Hintergrund-Check-Job)
    # ------------------------------------------------------------------
    # Deutsche Templates mit englischem Fallback im Plain-Text-Body (sinnvoll
    # für internationale Nutzer / Text-Fallback in Mail-Clients).
    # Wiederverwendung von _notification_email_html + _base_template (exakt
    # wie bei allen anderen Status-/Install-Benachrichtigungen).
    #
    # Aufruf-Bedingung (durch Caller sicherzustellen, global):
    #   if EmailService.is_configured() and user.email_notifications:
    #     await EmailService.send_... (keine neuen DB-Felder nötig;
    #     Steuerung erfolgt über bestehendes User.email_notifications + Glocke
    #     im Topbar).
    #
    # KISS + AGENTS.md:
    # - Keine Secrets, Pfade, IPs, Workshop-IDs, Versionen oder Hashes in Mails.
    # - Nur Server-/Mod-Name (bereits in anderen Mails verwendet, nicht sensitiv).
    # - Keine neuen Abhängigkeiten, keine Komplexität.
    # - Deutsche Kommentare.
    # - Methoden sind rein informativ (werden bei passiven Checks gerufen).
    # ------------------------------------------------------------------

    @staticmethod
    async def send_server_update_available_notification(to: str, username: str, server_name: str) -> bool:
        """Sendet E-Mail-Benachrichtigung, wenn ein Server-Datei-Update verfügbar ist.

        Typischer Aufruf aus Hintergrund-Check-Job (Scheduler), wenn der
        passive Check einen Hinweis meldet. Für Steam blockiert dieser Hinweis
        keinen Start/Restart; SteamCMD-Validate läuft dort ohnehin.
        """
        subject = f"Maunting Service Manager — Server-Update verfügbar: {server_name}"
        body = f"""Hallo {username},

[DE] Ein Update für die Server-Dateien von "{server_name}" ist verfügbar.
     Das Update wird beim nächsten Neustart berücksichtigt.

[EN] A server file update is available for "{server_name}".
     The update will be considered on the next restart.

Maunting Service Manager
"""
        html = EmailService._notification_email_html(
            username,
            "Server-Update verfügbar",
            f'Ein Update für die Server-Dateien von <strong>{EmailService.html_text(server_name)}</strong> '
            f'ist verfügbar. Es wird beim nächsten Neustart berücksichtigt.',
            kategorie="Server",
        )
        return await EmailService.send_email(to, subject, body, html)

    @staticmethod
    async def send_mod_update_available_notification(to: str, username: str, server_name: str, mod_name: str) -> bool:
        """Sendet E-Mail-Benachrichtigung, wenn ein Workshop-Mod-Update verfügbar ist.

        Typischer Aufruf aus Hintergrund-Check-Job (Scheduler), nachdem
        check_for_mod_updates() relevante Einträge geliefert hat.
        """
        subject = f"Maunting Service Manager — Mod-Update verfügbar: {mod_name}"
        body = f"""Hallo {username},

[DE] Ein Update für den Mod "{mod_name}" auf Server "{server_name}" ist verfügbar.

[EN] An update for the mod "{mod_name}" on server "{server_name}" is available.

Maunting Service Manager
"""
        html = EmailService._notification_email_html(
            username,
            "Mod-Update verfügbar",
            f'Ein Update für den Mod <strong>{EmailService.html_text(mod_name)}</strong> auf dem Server '
            f'<strong>{EmailService.html_text(server_name)}</strong> ist verfügbar.',
            kategorie="Server",
        )
        return await EmailService.send_email(to, subject, body, html)

    @staticmethod
    async def send_calendar_reminder_notification(
        to: str,
        username: str,
        title: str,
        start_str: str,
        location_str: str = "",
        time_hint: str = "in 2 Tagen",
    ) -> bool:
        """Sendet E-Mail-Erinnerung für anstehende Kalendertermine (z. B. 2 Tage oder 1 Tag vorab)."""
        subject = f"Terminerinnerung: {title} ({time_hint})"
        loc_line = f"\nOrt: {location_str}" if location_str else ""
        body = f"""Hallo {username},

Erinnerung an deinen bevorstehenden Termin {time_hint}:

Termin: {title}
Zeitpunkt: {start_str}{loc_line}

Maunting Service Manager
"""
        email_html = EmailService._notification_email_html(
            username,
            f"Terminerinnerung ({time_hint})",
            f"Erinnerung an deinen bevorstehenden Termin <strong>{EmailService.html_text(time_hint)}</strong>:",
            kategorie="Kalender",
            fakten=[("Termin", title), ("Zeitpunkt", start_str), ("Ort", location_str)],
        )
        return await EmailService.send_email(to, subject, body, email_html)

