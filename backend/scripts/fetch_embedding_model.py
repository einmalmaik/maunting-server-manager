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


def main() -> int:
    destination = target_dir()
    if all((destination / name).is_file() for name in REQUIRED):
        print(f"[ai] Embeddingmodell bereits vorhanden: {destination}")
        return 0

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
        )
    except Exception as exc:
        print(
            f"[ai] Download fehlgeschlagen ({type(exc).__name__}). "
            "Die KI-Gedaechtnissuche laeuft ohne Vektoren weiter; "
            "das Update ist davon nicht betroffen.",
            file=sys.stderr,
        )
        return 0

    missing = [name for name in REQUIRED if not (destination / name).is_file()]
    if missing:
        print(
            f"[ai] Unvollstaendiger Download, es fehlen: {', '.join(missing)}. "
            "Die KI-Gedaechtnissuche laeuft ohne Vektoren weiter.",
            file=sys.stderr,
        )
        return 0

    print("[ai] Embeddingmodell bereit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
