"""Fremdtext in einer Mail, die aussieht, als kaeme sie vom Panel.

`_notification_email_html` setzt seine Bausteine als rohes HTML zusammen. Das
ist gewollt — mehrere Aufrufer bauen dort `<strong>` um einen Statuswert ein.
Ungewollt war, dass durch dieselbe Stelle auch Servernamen, Benutzernamen,
Vorfallbeschreibungen und jetzt von einem Modell geschriebene Berichte flossen.

Ein Servername kommt aus einem Formular oder aus einer Shop-Bestellung. Eine
Vorfallbeschreibung kommt aus einer Logzeile auf einem Server, auf dem Fremde
spielen. Der Modellbericht kommt aus beidem. `<a href="...">Hier klicken</a>`
in einer Mail mit MSM-Kopfzeile ist ein brauchbarer Phishing-Traeger, und die
Mail geht an denjenigen, der das Panel betreibt.

Diese Tests pruefen nicht, ob irgendwo maskiert wird, sondern dass die
gefaehrlichen Zeichen im **fertigen HTML** nicht mehr als Auszeichnung stehen —
und zugleich, dass die absichtliche Auszeichnung der Vorlage erhalten bleibt.
Eine pauschale Maskierung des ganzen Dokuments waere die naheliegende und
falsche Loesung.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from services.email_service import EmailService


BOESE = '<script>alert("x")</script><a href="https://phish.example">klick</a>'


def _versand():
    """Faengt die letzte Mail ab und liefert `(subject, body, html)`."""
    gesendet = {}

    async def _fake(to, subject, body, html=None):
        gesendet["to"] = to
        gesendet["subject"] = subject
        gesendet["body"] = body
        gesendet["html"] = html or ""
        return True

    return gesendet, _fake


class TestHtmlText:
    def test_it_escapes_and_keeps_the_line_breaks(self):
        """Beides zugleich — sonst waere ein mehrzeiliger Bericht eine Zeile."""
        ergebnis = EmailService.html_text('<b>a</b>\nzweite "Zeile"')

        assert "<b>" not in ergebnis
        assert "&lt;b&gt;" in ergebnis
        assert "&quot;" in ergebnis
        assert "<br>" in ergebnis

    def test_none_becomes_empty(self):
        """`backup_name` ist optional; `None` darf nicht als "None" in der Mail stehen."""
        assert EmailService.html_text(None) == ""


class TestHeilungsbericht:
    def test_model_text_arrives_escaped(self):
        """Der Kern: Modelltext ist unvertrauenswuerdige Eingabe.

        Unabhaengig davon, was im Systemprompt steht — ein Modell, das ueber
        eine praeparierte Logzeile ueberredet wurde, schreibt sonst einen Link
        in eine Mail des Panels.
        """
        gesendet, fake = _versand()
        with patch.object(EmailService, "send_email", AsyncMock(side_effect=fake)):
            asyncio.run(
                EmailService.send_ai_healing_report(
                    "b@test.de",
                    "betreiber",
                    server_name="Testserver",
                    incident_type="process_not_running",
                    geheilt=True,
                    bericht=BOESE,
                )
            )

        html = gesendet["html"]
        assert "<script>" not in html
        assert 'href="https://phish.example"' not in html
        assert "&lt;script&gt;" in html
        # Die Vorlage selbst bleibt HTML — sonst haette man den Fehler nur
        # gegen einen anderen getauscht.
        assert "<strong>" in html

    def test_server_name_and_incident_type_are_escaped_too(self):
        """Auch die Felder, die nicht vom Modell stammen.

        Ein Servername kommt aus einem Formular, der Vorfalltyp aus dem
        Agenten. Beides ist Fremdtext, auch wenn es harmlos aussieht.
        """
        gesendet, fake = _versand()
        with patch.object(EmailService, "send_email", AsyncMock(side_effect=fake)):
            asyncio.run(
                EmailService.send_ai_healing_report(
                    "b@test.de",
                    "<i>betreiber</i>",
                    server_name='<img src=x onerror="alert(1)">',
                    incident_type="<b>typ</b>",
                    geheilt=False,
                    bericht="ok",
                    backup_name="<u>Guardian-Heilung</u>",
                )
            )

        html = gesendet["html"]
        # Die Zeichenfolge `onerror=` steht weiterhin im Dokument — als Text.
        # Das ist der Punkt: entscheidend ist nicht, ob das Wort vorkommt,
        # sondern ob es in einem Tag steht. Geprueft wird deshalb auf die
        # eingeschleuste Nutzlast in Roh- und in maskierter Form. (Ein
        # pauschales `"<img" not in html` traefe das Logo der Vorlage.)
        assert "<img src=x" not in html
        assert 'onerror="' not in html
        assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in html
        assert "<i>betreiber</i>" not in html
        assert "<b>typ</b>" not in html
        assert "<u>Guardian-Heilung</u>" not in html

    def test_the_headline_comes_from_the_panel_not_from_the_model(self):
        """`geheilt` ist eine Tatsache des Panels, kein Satz des Modells.

        Ein Modell, das sich irrt oder beschoenigt, soll nicht die Betreffzeile
        bestimmen. Deshalb wird hier gegen einen Bericht geprueft, der das
        Gegenteil behauptet.
        """
        gesendet, fake = _versand()
        with patch.object(EmailService, "send_email", AsyncMock(side_effect=fake)):
            asyncio.run(
                EmailService.send_ai_healing_report(
                    "b@test.de",
                    "betreiber",
                    server_name="Testserver",
                    incident_type="process_not_running",
                    geheilt=False,
                    bericht="Alles wieder in bester Ordnung, der Server laeuft.",
                )
            )

        assert "nicht behoben" in gesendet["subject"]
        assert "nicht behoben" in gesendet["body"]


class TestSicherheitsmeldungen:
    def test_the_notification_template_escapes_username_and_title(self):
        """Die beiden Felder, die die Vorlage selbst maskiert.

        `message` und `detail` bleiben bewusst roh — dort baut jeder Aufrufer
        seine eigene Auszeichnung und maskiert seine Fremdanteile mit
        `html_text`. Fuer `username` und `title` gilt das nicht: kein Aufrufer
        gibt dort Auszeichnung mit, und der Benutzername ist frei waehlbar.
        """
        html = EmailService._notification_email_html(
            "<script>a</script>", "<script>b</script>", "<strong>ok</strong>", ""
        )

        assert "<script>" not in html
        assert "&lt;script&gt;a&lt;/script&gt;" in html
        assert "<strong>ok</strong>" in html
