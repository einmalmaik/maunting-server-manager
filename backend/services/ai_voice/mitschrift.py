"""Die Mitschrift einer Sprachsitzung, für den Gedächtnisschreiber.

Seit Stufe 2 des Gedächtnisses (06.10.2026) merkt sich keine Stimme mehr selbst
etwas — ``remember`` gibt es nicht mehr. Was in einer Sitzung gesagt wurde,
liest nach ihrem Ende `ai_gedaechtnis_schreiber.aus_mitschrift`, so wie er ein
getipptes Gespräch liest. Die Mitschrift liegt dafür nur im Speicher der
Sitzung; gespeichert wird sie nirgends.

Mitgeschrieben wird nur, wenn der Benutzer das Gedächtnis benutzen darf und es
eingeschaltet hat (`realtime_session.mitschreiben`). Sonst nimmt dieses Objekt
nichts an, und die Aufrufstellen brauchen keine eigene Abfrage.

Die drei Wege liefern ihre Abschrift verschieden:

* GPT-Live schickt Stücke beider Seiten in zeitlicher Folge (`stueck`).
* OpenAI-Realtime liefert die Abschrift einer Äußerung, wenn sie fertig ist —
  das kann nach der Antwort darauf sein. Die Zeile wird beim Eingang der
  Äußerung vorgemerkt und später gefüllt (`vormerken`, `einsetzen`).
* Gemini-Live schickt Stücke beider Seiten ohne zugesicherte Reihenfolge
  („no guaranteed ordering“). Sie werden je Zug gesammelt und am Zugende als
  ganze Zeilen abgelegt, erst der Mensch, dann die KI (`zug`, `zug_ende`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

#: Mehr nimmt eine Sitzung nicht auf. Eine Stunde Gespräch sind etwa 50.000
#: Zeichen. Der Deckel schützt den Speicher, nicht das Budget: gelesen wird in
#: Ausschnitten (`ai_gedaechtnis_schreiber.MAX_FENSTER_ZEICHEN`).
MAX_ZEICHEN = 96_000
#: ``ich`` ist der Mensch, ``ki`` die Stimme — die Namen, die
#: `ai_gedaechtnis_schreiber.aus_mitschrift` erwartet.
SPRECHER = ("ich", "ki")


def _jetzt() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class _Zeile:
    sprecher: str
    text: str
    zeit: datetime


class Mitschrift:
    """Was in einer Sprachsitzung gesagt wurde, Zeile für Zeile."""

    def __init__(self, aktiv: bool) -> None:
        self.aktiv = bool(aktiv)
        self.beginn = _jetzt()
        self._zeilen: list[_Zeile] = []
        self._vorgemerkt: dict[str, _Zeile] = {}
        self._zug: dict[str, str] = {}
        self._zeichen = 0
        self._voll = False

    def _platz(self, text: str) -> bool:
        if not self.aktiv or self._voll or not text:
            return False
        if self._zeichen + len(text) > MAX_ZEICHEN:
            self._voll = True
            logger.info("Mitschrift voll: der Rest der Sitzung wird nicht mehr mitgeschrieben")
            return False
        self._zeichen += len(text)
        return True

    def stueck(self, sprecher: str, text: str) -> None:
        """Ein Stück Abschrift. Spricht derselbe weiter, wächst seine Zeile."""
        if not self._platz(text):
            return
        letzte = self._zeilen[-1] if self._zeilen else None
        if letzte is not None and letzte.sprecher == sprecher:
            letzte.text += text
        else:
            self._zeilen.append(_Zeile(sprecher, text, _jetzt()))

    def zeile(self, sprecher: str, text: str) -> None:
        """Eine ganze Zeile."""
        text = (text or "").strip()
        if self._platz(text):
            self._zeilen.append(_Zeile(sprecher, text, _jetzt()))

    def vormerken(self, schluessel: str, sprecher: str) -> None:
        """Hält den Platz einer Zeile frei, deren Text später kommt."""
        if not self.aktiv or self._voll or not schluessel or schluessel in self._vorgemerkt:
            return
        zeile = _Zeile(sprecher, "", _jetzt())
        self._vorgemerkt[schluessel] = zeile
        self._zeilen.append(zeile)

    def einsetzen(self, schluessel: str, sprecher: str, text: str) -> None:
        """Füllt eine vorgemerkte Zeile; ohne Vormerkung wird es eine neue.

        Die erste Fassung gilt. Realtime meldet die Abschrift einer Antwort
        zweimal (im eigenen Ereignis und in ``response.done``); eine zweite
        Zeile mit demselben Satz wäre für den Schreiber ein zweiter Beleg.
        """
        text = (text or "").strip()
        zeile = self._vorgemerkt.get(schluessel) if schluessel else None
        if zeile is None:
            self.zeile(sprecher, text)
        elif not zeile.text and self._platz(text):
            zeile.text = text

    def zug(self, sprecher: str, text: str) -> None:
        """Ein Stück eines Zugs, dessen Teile in beliebiger Folge kommen."""
        if self.aktiv and text and not self._voll:
            self._zug[sprecher] = (self._zug.get(sprecher, "") + text)[:MAX_ZEICHEN]

    def zug_ende(self) -> None:
        """Der Zug ist vorbei: erst die Zeile des Menschen, dann die der KI."""
        for sprecher in SPRECHER:
            self.zeile(sprecher, self._zug.pop(sprecher, ""))

    @property
    def zeilen(self) -> list[tuple[str, str, datetime]]:
        """(sprecher, text, zeit) — ohne Vormerkungen, die leer blieben."""
        return [(z.sprecher, z.text, z.zeit) for z in self._zeilen if z.text.strip()]

    def abgeben(self, *, user_id: int, provider_id: int | None) -> bool:
        """Gibt die Mitschrift an den Schreiber — einmal. Ob ein Durchgang beginnt.

        Nur im Ereignisloop: der Schreiber legt dort seine Aufgabe an. Ein
        Fehler hier darf das Ende der Sitzung nicht stören; er wird ohne
        Inhalt protokolliert.
        """
        self.zug_ende()
        zeilen = self.zeilen
        aktiv, self.aktiv = self.aktiv, False
        self._zeilen, self._vorgemerkt = [], {}
        if not aktiv or not zeilen:
            return False
        try:
            from services import ai_gedaechtnis_schreiber

            return ai_gedaechtnis_schreiber.aus_mitschrift(
                user_id=user_id,
                provider_id=provider_id,
                zeilen=zeilen,
                beginn=self.beginn,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Mitschrift nicht abgegeben error=%s", type(exc).__name__)
            return False
