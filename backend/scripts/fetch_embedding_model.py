"""Laedt das lokale Embeddingmodell fuer das KI-Gedaechtnis herunter.

Wird einmalig von `install.sh` und `update.sh` aufgerufen. Das Panel laedt zur
Laufzeit **nie** Gewichte nach: ein Server-Manager, der im Betrieb Dateien aus
dem Internet holt und ausfuehrt, waere eine Supply-Chain-Flaeche, die wir nicht
wollen. Deshalb passiert das genau hier, unter Kontrolle des Betreibers.

Der Download ist rund 507 MB. Ist das Modell bereits vollstaendig vorhanden,
passiert nichts.

**Ein Fehlschlag ist kein Installationsfehler.** Ohne Modell laeuft die
Gedaechtnissuche ohne Vektoren weiter — schlechter, aber vollstaendig
funktionsfaehig. Ein Betreiber ohne Internetzugang oder hinter einem Proxy soll
nicht an dieser Stelle stehenbleiben. Das Skript endet deshalb immer mit 0 und
meldet den Zustand im Klartext.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


REPO = "minishlab/potion-multilingual-128M"
# Nur was zum Rechnen gebraucht wird. Das Repository enthaelt zusaetzlich eine
# ONNX-Fassung derselben Gewichte (~512 MB), die MSM nicht verwendet.
PATTERNS = [
    "model.safetensors",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "config.json",
    "README.md",
]
REQUIRED = ("config.json", "model.safetensors", "tokenizer.json")


def target_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "ml-models" / "potion-multilingual-128M"


def vollstaendig(destination: Path) -> bool:
    """Ist das Modell da **und** brauchbar?

    "Datei existiert" reichte hier einmal, und das war zu wenig: eine volle
    Platte hat `tokenizer.json` mitten im Text beschaedigt (die Datei war
    weiterhin 18 MB gross und endete korrekt). Der Ladeversuch scheiterte
    danach bei jedem Panelstart mit "invalid number at line 1 column
    12226784", die Gedaechtnissuche lief monatelang ohne Vektoren — und
    **jedes Update meldete "bereits vorhanden" und ruehrte es nicht an**. Ein
    Reparaturweg, der die Reparatur ueberspringt, ist keiner.

    Geprueft wird der Tokenizer, weil er der einzige grosse **Text** im Paket
    ist und das ganze Laden an ihm haengt. Die Gewichte pruefen wir hier
    bewusst nicht: sie sind binaer, ein Vollstaendigkeitstest hiesse, 507 MB
    zu lesen, und `safetensors` meldet einen Schaden beim Laden selbst.
    """
    if not all((destination / name).is_file() for name in REQUIRED):
        return False
    try:
        json.loads((destination / "tokenizer.json").read_bytes())
    except Exception:  # noqa: BLE001 - jeder Lesefehler heisst: neu holen
        print(
            "[ai] tokenizer.json ist beschaedigt — das Modell wird neu geladen.",
            file=sys.stderr,
        )
        return False
    return True


def main() -> int:
    destination = target_dir()
    if vollstaendig(destination):
        print(f"[ai] Embeddingmodell bereits vorhanden: {destination}")
        return 0

    # Lag schon etwas da, war es kaputt — dann ist der Zwischenspeicher
    # verdaechtig. `snapshot_download` verlinkt sonst genau dieselbe
    # beschaedigte Datei aus `.cache` zurueck und meldet Erfolg in
    # Sekundenbruchteilen. Nur in diesem Fall neu ziehen: bei einer frischen
    # Installation gibt es nichts zu erzwingen.
    beschaedigt = any((destination / name).is_file() for name in REQUIRED)

    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print(
            "[ai] huggingface_hub fehlt — Embeddingmodell wird uebersprungen. "
            "Die KI-Gedaechtnissuche laeuft ohne Vektoren.",
            file=sys.stderr,
        )
        return 0

    print(f"[ai] Lade Embeddingmodell {REPO} (~507 MB) nach {destination} ...")
    try:
        destination.mkdir(parents=True, exist_ok=True)
        snapshot_download(
            REPO,
            local_dir=str(destination),
            allow_patterns=PATTERNS,
            force_download=beschaedigt,
        )
    except Exception as exc:
        print(
            f"[ai] Download fehlgeschlagen ({type(exc).__name__}). "
            "Die KI-Gedaechtnissuche laeuft ohne Vektoren weiter; "
            "das Update ist davon nicht betroffen.",
            file=sys.stderr,
        )
        return 0

    if not vollstaendig(destination):
        print(
            "[ai] Download unvollstaendig oder beschaedigt. "
            "Die KI-Gedaechtnissuche laeuft ohne Vektoren weiter.",
            file=sys.stderr,
        )
        return 0

    print("[ai] Embeddingmodell bereit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
