"""Gemeinsame Pruefung fuer hochgeladene Bilder (Profil- und Gruppenbilder).

Die Regeln standen bis hierher nur in `routers/auth.py`. Mit dem Gruppenlogo
gab es einen zweiten Aufrufer, und zwei Kopien derselben Magic-Byte-Tabelle sind
genau die Art Duplikat, bei der eine Seite spaeter eine Luecke bekommt, die die
andere laengst geschlossen hat.

Der Content-Type eines Uploads ist eine Behauptung des Browsers. Deshalb wird
zusaetzlich der Dateianfang geprueft: ohne diesen Schritt koennte eine als
`image/png` deklarierte Datei alles Moegliche enthalten.
"""

from __future__ import annotations

import os
import re
import uuid

from config import settings

MAX_BILD_BYTES = 5 * 1024 * 1024  # 5 MB
ERLAUBTE_BILDTYPEN = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}

# Dateinamen, die wir selbst vergeben. Alles andere wird beim Ausliefern und
# beim Loeschen abgewiesen — ein Pfad aus einem gespeicherten Feld darf nie
# ungeprueft an `os.path.join` gehen.
DATEINAME_MUSTER = re.compile(r"^[a-zA-Z0-9_\-\.]+$")


def bilder_verzeichnis() -> str:
    basis = settings.panel_config_dir if settings.panel_config_dir else "."
    verzeichnis = os.path.join(basis, "data", "avatars")
    os.makedirs(verzeichnis, exist_ok=True)
    return verzeichnis


def ist_gueltiges_bild(inhalt: bytes, mime_type: str) -> bool:
    """Prueft Groesse und Dateianfang gegen den behaupteten Typ."""
    if len(inhalt) > MAX_BILD_BYTES or len(inhalt) < 8:
        return False
    if mime_type == "image/jpeg" and inhalt.startswith(b"\xff\xd8\xff"):
        return True
    if mime_type == "image/png" and inhalt.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    if mime_type == "image/gif" and (inhalt.startswith(b"GIF87a") or inhalt.startswith(b"GIF89a")):
        return True
    if mime_type == "image/webp" and inhalt.startswith(b"RIFF") and b"WEBP" in inhalt[8:16]:
        return True
    return False


def speichere_bild(inhalt: bytes, mime_type: str, praefix: str, kennung: int) -> str:
    """Legt das Bild ab und gibt den Dateinamen zurueck."""
    endung = ERLAUBTE_BILDTYPEN[mime_type]
    dateiname = f"{praefix}_{kennung}_{uuid.uuid4().hex[:12]}{endung}"
    with open(os.path.join(bilder_verzeichnis(), dateiname), "wb") as datei:
        datei.write(inhalt)
    return dateiname


def loesche_bild(url: str | None) -> None:
    """Entfernt eine zuvor abgelegte Datei. Stille Nachsicht bei Fehlern."""
    if not url:
        return
    dateiname = url.split("/")[-1]
    if not dateiname or not DATEINAME_MUSTER.match(dateiname):
        return
    pfad = os.path.join(bilder_verzeichnis(), dateiname)
    if os.path.isfile(pfad):
        try:
            os.remove(pfad)
        except OSError:
            pass
