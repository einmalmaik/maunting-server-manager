"""Kein Quelltext steht doppelt kodiert im Repo.

Wer UTF-8 als cp1252 liest und wieder als UTF-8 speichert, bekommt gültiges
UTF-8, das niemand bemerkt: aus „ä“ wird `\\u00c3\\u00a4`, aus „—“ wird
`\\u00e2\\u20ac\\u201d`. Am 21.09.2026 standen so 404 Zeilen in
`services/ai_tools/`, und bis 03.10.2026 weitere 176 in
`services/ai_proposals/`. Dort lagen auch Meldungen, die Nutzer und Modell
lesen, etwa „Termin-Änderung erfordert event_id“ mit kaputtem Ä.

Die Muster stehen hier als Escape, damit die Datei sich nicht selbst findet.
Wer über Mojibake schreibt, schreibt die Zeichen ebenso als Escape.
"""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Das erste Byte einer Zwei- oder Dreibytefolge (0xC2, 0xC3, 0xE2), gelesen als
# cp1252, und danach ein Zeichen aus 0x80 bis 0xBF oder eines der
# Sonderzeichen, die cp1252 dort statt der Steuerzeichen hat (Euro, Anführungen, Striche).
MOJIBAKE = re.compile(
    "[\u00c2\u00c3\u00e2]"
    "[\u0080-\u00bf\u0152\u0153\u0160\u0161\u0178\u017d\u017e\u0192\u02c6\u02dc\u2013-\u2122]"
)

ORDNER = (
    "backend/",
    "frontend/src/",
    "smart-system/src-tauri/src/",
    "smart-system/src-tauri/gen/android/app/src/",
    "scripts/",
    "docs/",
)
ENDUNGEN = (".py", ".ts", ".tsx", ".json", ".md", ".rs", ".kt", ".sh", ".css", ".html")


def _dateien() -> list[str]:
    alle = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=True
    ).stdout.splitlines()
    return [d for d in alle if d.startswith(ORDNER) and d.endswith(ENDUNGEN)]


def test_kein_quelltext_ist_doppelt_kodiert() -> None:
    funde = []
    for datei in _dateien():
        pfad = ROOT / datei
        if not pfad.is_file():
            continue
        try:
            text = pfad.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for nr, zeile in enumerate(text.splitlines(), 1):
            if MOJIBAKE.search(zeile):
                funde.append(f"{datei}:{nr}: {ascii(zeile.strip()[:80])}")
    assert not funde, "Doppelt kodiert (UTF-8 als cp1252 gelesen):\n" + "\n".join(funde[:40])


def test_das_muster_trifft_mojibake_und_nichts_anderes() -> None:
    gut = "Termin-Änderung — für später, „Zitat“, Größe 2 €, à la carte, Café™"
    assert not MOJIBAKE.search(gut)
    assert MOJIBAKE.search(gut.encode("utf-8").decode("cp1252", errors="replace"))
    for zeichen in "äöüÄÖÜß—–„“…€":
        assert MOJIBAKE.search(zeichen.encode("utf-8").decode("cp1252", errors="replace")), zeichen
