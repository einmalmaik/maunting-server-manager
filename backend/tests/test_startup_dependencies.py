"""Eine fehlende Nebenabhaengigkeit darf das Panel nicht am Start hindern.

Am 11.08.2026 stand das Panel in einer Neustartschleife, weil `segno` fehlte —
eine Bibliothek, die ein Bild neben einem Geheimnis zeichnet, das ohnehin
danebensteht. Die Kette war `main` -> `routers` -> `auth` -> `totp_qr` ->
`import segno`, und ein harter Import im Modulkopf entschied damit ueber Login,
Serververwaltung und alles Uebrige. Dieselbe Falle lag in `ai_skill_service`
mit `yaml`.

Dieser Test schliesst nicht die zwei Faelle, sondern die Bauart. Er sperrt
*jede* Abhaengigkeit aus requirements.txt aus, die nicht ausdruecklich unten in
KERN steht, und verlangt, dass `main` trotzdem durchimportiert.

Damit kostet eine neue Abhaengigkeit eine bewusste Entscheidung: entweder sie
wird weich importiert (try/except ImportError, Rueckfall auf None — so wie
services/docker_service.py es seit jeher macht), oder sie kommt mit
Begruendung nach KERN. Was von beidem richtig ist, entscheidet eine Frage:
*Soll das Panel ohne dieses Paket ueberhaupt laufen?* Bei einem Datenbanktreiber
lautet die Antwort nein. Bei einem QR-Zeichner lautet sie ja.
"""

from __future__ import annotations

import re
import subprocess
import sys
from importlib.metadata import packages_distributions
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent

# Ohne diese laeuft das Panel nicht, und das ist richtig so: Webrahmen, ORM,
# Migrationen, Einstellungen, Kryptographie fuer Anmeldedaten, der
# Datenbanktreiber, die Ratenbegrenzung und der Zeitplaner. Sie fehlen zu lassen
# und weiterzumachen waere kein Dienst am Betreiber, sondern ein Panel, das
# vorgibt zu arbeiten.
#
# Die Liste ist bewusst von Hand gepflegt. Sie ist der Ort, an dem jemand
# begruendet, warum ein Paket unverzichtbar sein soll.
KERN = {
    "fastapi",
    "sqlalchemy",
    "pydantic",
    "pydantic-settings",
    "python-jose",
    "passlib",
    "python-multipart",
    "aiosmtplib",
    "email-validator",
    "httpx",
    "apscheduler",
    "psutil",
    "python-dotenv",
    "slowapi",
    "limits",
    "psycopg2-binary",
}


def _requirements() -> list[str]:
    zeilen = (BACKEND / "requirements.txt").read_text(encoding="utf-8").splitlines()
    namen = []
    for zeile in zeilen:
        zeile = zeile.strip()
        if not zeile or zeile.startswith("#"):
            continue
        # "uvicorn[standard]==0.30.0" -> "uvicorn"
        name = re.split(r"[=<>\[!~;]", zeile, maxsplit=1)[0].strip()
        if name:
            namen.append(name.lower().replace("_", "-"))
    return namen


def _module_je_verteilung() -> dict[str, set[str]]:
    """Paketname -> importierbare Modulnamen, aus der Installation gelesen.

    Von Hand gepflegt waere diese Zuordnung eine Fehlerquelle fuer sich:
    pyyaml heisst `yaml`, python-jose heisst `jose`, psycopg2-binary heisst
    `psycopg2`. Die Installation weiss es besser als eine Tabelle im Test.
    """
    zuordnung: dict[str, set[str]] = {}
    for modul, verteilungen in packages_distributions().items():
        for verteilung in verteilungen:
            schluessel = verteilung.lower().replace("_", "-")
            zuordnung.setdefault(schluessel, set()).add(modul)
    return zuordnung


# Sperrt die genannten Wurzelmodule und importiert dann die Anwendung.
_PROGRAMM = """
import sys
GESPERRT = set(%r)


class Sperre:
    def find_spec(self, name, path=None, target=None):
        if name.split('.')[0] in GESPERRT:
            raise ImportError("No module named %%r" %% name)
        return None


sys.meta_path.insert(0, Sperre())
import main
assert main.app is not None
print("OK")
"""


def test_the_panel_starts_without_every_optional_dependency() -> None:
    """Alle entbehrlichen Pakete gleichzeitig weg — das Panel muss hochkommen.

    Gleichzeitig und nicht einzeln: das ist die schaerfere Zusage, es ist ein
    Unterprozess statt sechsundzwanzig, und es entspricht dem, was im Betrieb
    passiert — ein venv ist selten nur um genau ein Paket im Rueckstand.
    """
    zuordnung = _module_je_verteilung()
    optional = [p for p in _requirements() if p not in KERN]
    assert optional, "requirements.txt gelesen, aber nichts Entbehrliches gefunden"

    zu_sperren: set[str] = set()
    nicht_installiert: list[str] = []
    for paket in optional:
        module = zuordnung.get(paket)
        if module:
            zu_sperren |= module
        else:
            nicht_installiert.append(paket)

    # Ein nicht installiertes Paket laesst sich nicht sperren — es waere still
    # aus der Pruefung gefallen. Lieber laut sein als eine Zusage vortaeuschen.
    assert not nicht_installiert, (
        "Nicht installiert, deshalb ungeprueft: "
        f"{nicht_installiert}. Erst `pip install -r requirements.txt`."
    )

    ergebnis = subprocess.run(
        [sys.executable, "-c", _PROGRAMM % (sorted(zu_sperren),)],
        cwd=BACKEND, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )

    if ergebnis.returncode != 0:
        letzte = [z for z in (ergebnis.stderr or "").splitlines() if z.strip()][-12:]
        pytest.fail(
            "Ohne die entbehrlichen Pakete startet das Panel nicht mehr.\n"
            "Entweder das neue Paket weich importieren (try/except ImportError,\n"
            "Rueckfall auf None — Vorbild services/docker_service.py), oder es\n"
            "mit Begruendung nach KERN in dieser Datei aufnehmen.\n"
            f"Gesperrt waren: {sorted(zu_sperren)}\n\n" + "\n".join(letzte)
        )


def test_every_requirement_is_decided_one_way_or_the_other() -> None:
    """KERN darf nichts nennen, was gar nicht mehr in requirements.txt steht.

    Sonst wuerde ein geloeschtes Paket seinen Freibrief behalten, und ein
    spaeter unter demselben Namen hinzugefuegtes bekaeme ihn stillschweigend
    geerbt — ohne dass jemand die Frage noch einmal gestellt haette.
    """
    verwaist = KERN - set(_requirements())
    assert not verwaist, f"KERN nennt Pakete ausserhalb der requirements.txt: {sorted(verwaist)}"
