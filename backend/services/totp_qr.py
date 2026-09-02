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

try:
    import segno
except ImportError:  # pragma: no cover - exercised on systems before deps install
    # Weich und nicht hart, und das ist keine Bequemlichkeit: `routers/auth.py`
    # importiert dieses Modul beim Start, `routers/__init__.py` importiert
    # `auth`, und `main.py` importiert `routers`. Ein harter Import macht aus
    # einer fehlenden Zeichenbibliothek einen Totalausfall des Panels — kein
    # Login, keine Server, nichts, in einer Neustartschleife von systemd.
    # Genau so ist es am 11.08.2026 im Betrieb passiert, weil der Code ohne
    # `update.sh` (und damit ohne `pip install -r requirements.txt`) auf den
    # Server kam.
    #
    # Der Docstring oben verspricht ohnehin, dass ein Wegfall der Bibliothek
    # nur das Bild kostet. Der harte Import hat dieses Versprechen gebrochen.
    # Dieselbe Form benutzt `services/docker_service.py` seit jeher.
    segno = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

if segno is None:
    # Beim Start und nicht erst beim ersten Einrichten: der Betreiber schaut
    # nach einem Update ins journalctl, nicht in die 2FA-Maske.
    logger.warning(
        "segno fehlt — die 2FA-Einrichtung zeigt keinen QR-Code. "
        "Behebung: venv/bin/pip install -r requirements.txt"
    )

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


def qr_datenuri(otpauth_uri: str, error: str = FEHLERKORREKTUR, border: int = RUHEBEREICH) -> str | None:
    """Erzeugt den QR-Code zur `otpauth://`-URI als `data:`-URI.

    Gibt None zurueck, wenn sich kein Code erzeugen laesst. Die Einrichtung von
    2FA darf an der Bildbeigabe nicht scheitern — Geheimnis und Link stehen
    ohnehin daneben und reichen allein aus.

    `light="white"` ist nicht kosmetisch: das Panel ist dunkel, und ein Code
    ohne hellen Untergrund verliert den Kontrast, den ein Leser braucht.
    """
    if segno is None or not otpauth_uri:
        return None
    try:
        code = segno.make(otpauth_uri, error=error)
        return code.svg_data_uri(
            scale=SKALIERUNG,
            border=border,
            dark="black",
            light="white",
        )
    except Exception:
        # Bewusst breit: was hier auch schiefgeht, es darf den Benutzer nicht
        # von seinem zweiten Faktor abschneiden. Die URI selbst ist unversehrt.
        logger.warning("QR-Code fuer die 2FA-Einrichtung nicht erzeugbar", exc_info=True)
        return None
