"""Der QR-Code zur 2FA-Einrichtung, im Panel erzeugt statt beim Fremden bestellt.

Hier stand einmal nichts: die Oberflaeche liess das Bild von api.qrserver.com
holen und schickte dabei die vollstaendige `otpauth://`-URI als Query-Parameter
mit. Darin stehen das Base32-TOTP-Geheimnis und die Kennung des Benutzers, und
ein Query-Parameter landet im Zugriffslog des Empfaengers. Wer dieses Log liest,
erzeugt ab sofort gueltige Codes — `AuthService.verify_totp` prueft reines
RFC 6238 ohne serverseitigen Zusatzfaktor, das Geheimnis allein genuegt also.
Der zweite Faktor waere damit keiner mehr.

Deshalb wird der Code jetzt hier erzeugt. Das Ergebnis ist eine `data:`-URI,
und `data:` steht in der img-src-Liste der eigenen CSP (main.py) — die Anzeige
traegt damit in *jeder* Aufstellung, auch dort, wo FastAPI das SPA-Dokument
selbst ausliefert und seine CSP folglich auf dem Dokument liegt.

Die Kapselung ist Absicht und folgt docs/agent-rules/dependencies.md: `segno`
wird ausschliesslich hier benutzt. Faellt die Bibliothek weg, ersetzt man diese
eine Funktion; `qr_datenuri` liefert dann None, und die Oberflaeche zeigt
weiterhin Geheimnis und Link zum Abschreiben.
"""

from __future__ import annotations

import logging

import segno

logger = logging.getLogger(__name__)

# Fehlerkorrektur M ist die uebliche Wahl fuer Authenticator-Codes. Ein Bild am
# Schirm nimmt keinen Schaden, hoehere Stufen wuerden den Code also nur dichter
# machen und damit schlechter scannbar.
FEHLERKORREKTUR = "m"

# scale=8 kostet nichts: die Pfadangaben werden laenger, nicht zahlreicher (fuer
# eine lange URI gemessen 7179 statt 7127 Zeichen). Dafuer ist die Eigengroesse
# des Bildes bereits groesser als die Anzeigeflaeche, und kein Browser muss ein
# zu kleines SVG hochrechnen.
SKALIERUNG = 8

# Der Ruhebereich gehoert zum Standard; ohne ihn finden viele Leser den Code
# nicht. Vier Module sind das vorgeschriebene Mindestmass.
RUHEBEREICH = 4


def qr_datenuri(otpauth_uri: str) -> str | None:
    """Erzeugt den QR-Code zur `otpauth://`-URI als `data:`-URI.

    Gibt None zurueck, wenn sich kein Code erzeugen laesst. Die Einrichtung von
    2FA darf an der Bildbeigabe nicht scheitern — Geheimnis und Link stehen
    ohnehin daneben und reichen allein aus.

    `light="white"` ist nicht kosmetisch: das Panel ist dunkel, und ein Code
    ohne hellen Untergrund verliert den Kontrast, den ein Leser braucht.
    """
    if not otpauth_uri:
        return None
    try:
        code = segno.make(otpauth_uri, error=FEHLERKORREKTUR)
        return code.svg_data_uri(
            scale=SKALIERUNG,
            border=RUHEBEREICH,
            dark="black",
            light="white",
        )
    except Exception:
        # Bewusst breit: was hier auch schiefgeht, es darf den Benutzer nicht
        # von seinem zweiten Faktor abschneiden. Die URI selbst ist unversehrt.
        logger.warning("QR-Code fuer die 2FA-Einrichtung nicht erzeugbar", exc_info=True)
        return None
