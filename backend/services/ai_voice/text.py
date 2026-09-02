"""Reine Textregeln des Sprachmodus."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MAX_BELEG_ZEILEN = 40
MAX_BELEG_ZEICHEN = 2_000
_ZAUN = re.compile(r"^\s*```")
_UNVOLLSTAENDIGE_SATZZEICHEN = (",", "...", "…", "-", "—", ":", ";")
_UNVOLLSTAENDIGE_WOERTER = {
    "und", "oder", "aber", "weil", "dass", "denn", "den", "die", "das", "dem", "des",
    "ein", "eine", "einen", "einem", "einer", "eines", "bitte", "um", "wie", "mit", "für",
    "fuer", "in", "an", "auf", "aus", "bei", "also", "bzw", "bzw.", "resp", "resp.", "äh",
    "ähm", "aeh", "aehm", "and", "or", "but", "because", "that", "the", "a", "an", "to",
    "with", "for",
}


def _ist_gedanke_abgeschlossen(text: str) -> bool:
    sauber = (text or "").strip().lower()
    if not sauber or any(sauber.endswith(zeichen) for zeichen in _UNVOLLSTAENDIGE_SATZZEICHEN):
        return False
    if sauber.endswith((".", "!", "?", "。", "！", "？")):
        return not sauber.endswith(("...", "…", "..!", "..?", ".."))
    woerter = sauber.split()
    return bool(woerter) and woerter[-1] not in _UNVOLLSTAENDIGE_WOERTER and len(woerter) >= 4


@dataclass
class Belegfilter:
    """Trennt Codebelege vom vorlesbaren Antworttext."""

    _puffer: str = ""
    _im_block: bool = False
    _block: list[str] = field(default_factory=list)
    _quelle: str = ""

    def fuettern(self, text: str) -> tuple[str, list[dict]]:
        self._puffer += text
        gesprochen: list[str] = []
        belege: list[dict] = []
        while "\n" in self._puffer:
            zeile, self._puffer = self._puffer.split("\n", 1)
            beleg = self._zeile(zeile, gesprochen)
            if beleg is not None:
                belege.append(beleg)
        if not self._im_block and not self._puffer.lstrip().startswith("`"):
            treffer = re.search(r"([.!?…:;])\s+", self._puffer)
            if treffer is not None and treffer.end() >= 10:
                satz = self._puffer[:treffer.end()]
                self._puffer = self._puffer[treffer.end():]
                gesprochen.append(satz)
        return "".join(gesprochen), belege

    def ausklingen(self) -> tuple[str, list[dict]]:
        gesprochen: list[str] = []
        belege: list[dict] = []
        if self._puffer:
            beleg = self._zeile(self._puffer, gesprochen)
            self._puffer = ""
            if beleg is not None:
                belege.append(beleg)
        if self._im_block:
            self._im_block = False
            beleg = self._beleg_bauen()
            if beleg is not None:
                belege.append(beleg)
        return "".join(gesprochen), belege

    def _zeile(self, zeile: str, gesprochen: list[str]) -> dict | None:
        if _ZAUN.match(zeile):
            if self._im_block:
                self._im_block = False
                return self._beleg_bauen()
            self._im_block = True
            self._block = []
            self._quelle = zeile.strip().lstrip("`").strip()
            return None
        if self._im_block:
            if len(self._block) < MAX_BELEG_ZEILEN:
                self._block.append(zeile[:MAX_BELEG_ZEICHEN])
            return None
        gesprochen.append(zeile + "\n")
        return None

    def _beleg_bauen(self) -> dict | None:
        zeilen = [zeile for zeile in self._block if zeile.strip()]
        self._block = []
        quelle, self._quelle = self._quelle, ""
        return {"art": "beleg", "quelle": quelle, "zeilen": zeilen} if zeilen else None


_ZUSTIMMUNG = frozenset({
    "ja", "jo", "jep", "jawohl", "jaja", "ja bitte", "ja gerne", "ja genau", "ja mach das",
    "ja mach", "ja tu das", "ja bestaetigt", "ja klar", "mach das", "mach es", "mach", "tu das",
    "los", "leg los", "mach weiter", "bestaetigt", "bestaetige", "bestaetigen", "einverstanden",
    "in ordnung", "passt", "okay", "ok", "okay mach das", "gerne", "genau", "korrekt", "yes",
    "yep", "yeah", "go", "do it", "confirm", "confirmed",
})
_ABLEHNUNG = frozenset({
    "nein", "ne", "nee", "noe", "nein danke", "lass es", "lass das", "nicht", "abbrechen",
    "abbruch", "stopp", "stop", "halt", "warte", "lieber nicht", "doch nicht", "vergiss es",
    "nein lass", "no", "nope", "cancel", "abort", "dont", "do not",
})
_UMSCHRIFT = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def _normalisieren(text: str) -> str:
    ohne = "".join(zeichen for zeichen in text.lower().translate(_UMSCHRIFT) if zeichen.isalnum() or zeichen.isspace())
    return " ".join(ohne.split())


def ist_zustimmung(text: str) -> bool:
    return _normalisieren(text) in _ZUSTIMMUNG


def ist_ablehnung(text: str) -> bool:
    return _normalisieren(text) in _ABLEHNUNG
