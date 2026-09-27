"""Spaltentyp fuer Text, der in der Datenbank nur verschluesselt steht.

Im Code ist ein ``DisText`` gewoehnlicher Text. Beim Schreiben verschluesselt
ihn ``DisClient.encrypt``, beim Lesen entschluesselt ihn ``DisClient.decrypt``.
Wer die Tabelle direkt ansieht (PostgreSQL-Studio, Dump, Backup), sieht nur
``msm-dis-v1:...``.

Warum ein Spaltentyp und nicht Aufrufe an jeder Stelle: Chatnachrichten allein
werden an rund hundert Stellen gelesen und geschrieben. Wer an jeder davon
selbst ver- und entschluesseln muss, vergisst irgendwann eine, und dann steht
dort wieder Klartext. Der Typ laesst keine Stelle aus.

Der Preis ist die AAD. Ein Spaltentyp kennt seine Zeile nicht, die AAD bindet
den Wert deshalb nur an Tabelle und Spalte (``msm:ai:ai_messages.content``),
nicht an Zeile oder Besitzer. Gegen Lesen reicht das: ohne den Sidecar ist
der Wert unlesbar. Wer schreiben darf, kann ein Chiffrat innerhalb derselben
Spalte in eine andere Zeile kopieren. Er kann aber auch gleich ein
Passwort-Hash austauschen, der Schutz davor liegt nicht in dieser Spalte.

**Altbestand.** Zeilen von vor der Umstellung stehen noch im Klartext. Sie
werden unveraendert gelesen und von ``services.dis_altbestand`` nachgezogen.
Erkannt wird der Unterschied am Praefix, nicht an einem Entschluesselungsversuch:
ein Versuch je Klartextzeile kostete einen Sidecar-Aufruf, und ein Fehlschlag
waere von einem manipulierten Chiffrat nicht zu unterscheiden.

**Vergleiche.** ``coerce_compared_value`` gibt schlichtes ``Text`` zurueck.
Sonst verschluesselte SQLAlchemy auch den Vergleichswert, etwa das Muster
in ``spalte.like("msm-dis-v1:%")``, und kein Filter traefe je.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import Select, type_coerce
from sqlalchemy.orm import Query, Session
from sqlalchemy.types import Text, TypeDecorator

#: Klartexte, die ``vorab_entschluesselt`` fuer den laufenden Block schon
#: geholt hat, je (Chiffrat, AAD). Ausserhalb eines Blocks ``None``.
_vorab: ContextVar[dict[tuple[str, str], str] | None] = ContextVar("dis_vorab", default=None)

#: Unter der 8-MiB-Grenze des Sidecars (``MAX_JSON_BODY``), mit Luft fuer das
#: JSON drumherum, und unter seinen 10.000 Werten je Anfrage (``MAX_BATCH``).
_STAPEL_ZEICHEN = 6 * 1024 * 1024
_STAPEL_WERTE = 5_000


class DisText(TypeDecorator):
    impl = Text
    cache_ok = True

    def __init__(self, aad: str) -> None:
        super().__init__()
        self.aad = aad

    def process_bind_param(self, value: str | None, dialect) -> str | None:
        if value is None:
            return None
        # Erst hier importiert: `services/__init__` laedt `models`, ein
        # Import auf Modulebene waere ein Zyklus (wie in `models/user.py`).
        from services.dis_client import DisClient

        return DisClient.encrypt(value, aad=self.aad)

    def process_result_value(self, value: str | None, dialect) -> str | None:
        from services.dis_client import DisClient

        if value is None or not DisClient.ist_verschluesselt(value):
            return value
        vorab = _vorab.get()
        if vorab is not None:
            klartext = vorab.get((value, self.aad))
            if klartext is not None:
                return klartext
        return DisClient.decrypt(value, aad=self.aad)

    def coerce_compared_value(self, op, value):
        return Text()


def ai_text(tabelle_spalte: str) -> DisText:
    """``DisText`` mit der AAD ``msm:ai:<tabelle>.<spalte>``."""
    return DisText(aad=f"msm:ai:{tabelle_spalte}")


def _stapel(paare: list[tuple[str, str]]) -> Iterator[list[tuple[str, str]]]:
    stapel: list[tuple[str, str]] = []
    zeichen = 0
    for paar in paare:
        laenge = len(paar[0]) + len(paar[1])
        if stapel and (zeichen + laenge > _STAPEL_ZEICHEN or len(stapel) >= _STAPEL_WERTE):
            yield stapel
            stapel, zeichen = [], 0
        stapel.append(paar)
        zeichen += laenge
    if stapel:
        yield stapel


def gebuendelt_entschluesseln(paare: list[tuple[str, str]]) -> dict[tuple[str, str], str]:
    """Entschluesselt Paare aus Chiffrat und AAD in wenigen Aufrufen.

    Was nicht klappt (Sidecar weg, unlesbarer Wert), fehlt im Ergebnis. Der
    Aufrufer entschluesselt es dann einzeln, mit denselben Fehlern wie immer.
    """
    from services.dis_client import DisClient, DisSidecarError

    klartexte: dict[tuple[str, str], str] = {}
    for stapel in _stapel(list(dict.fromkeys(paare))):
        try:
            ergebnis = DisClient.decrypt_many(stapel)
        except DisSidecarError:
            continue
        klartexte.update({paar: text for paar, text in zip(stapel, ergebnis) if text is not None})
    return klartexte


@contextmanager
def vorab_entschluesselt(db: Session, abfrage: Query | Select, *spalten) -> Iterator[None]:
    """Entschluesselt die ``DisText``-Spalten einer Abfrage gebuendelt vorab.

    Ohne das kostet jeder verschluesselte Wert einen eigenen Aufruf beim
    Sidecar, nacheinander: eine Chatseite mit 200 Nachrichten und vier
    verschluesselten Spalten waren bis 27.09.2026 bis zu 800 Aufrufe.

    Der Block liest dieselben Zeilen einmal roh (``type_coerce`` auf ``Text``,
    am Typ vorbei), holt alle Klartexte mit ``decrypt_many`` und legt sie fuer
    die Dauer des Blocks bereit. Laedt ``abfrage`` darin ihre Objekte, nimmt
    ``DisText`` den Klartext von dort. ``abfrage`` darf keine Ladeoptionen
    tragen, die Rohabfrage waehlt nur die Spalten.

    Was vorab nicht klappt (Sidecar weg, ein unlesbarer Wert, neueres
    Format), faellt still auf den Einzelweg zurueck. Dort scheitert es mit
    denselben Fehlern wie ohne diesen Block, keiner geht verloren.
    Die Klartexte leben nur im Block, danach gibt es sie hier nicht mehr.
    """
    from services.dis_client import DisClient

    typen = [spalte.type for spalte in spalten]
    for typ in typen:
        if not isinstance(typ, DisText):
            raise TypeError("vorab_entschluesselt nimmt nur DisText-Spalten")
    rohspalten = [type_coerce(spalte, Text) for spalte in spalten]
    if isinstance(abfrage, Query):
        zeilen = abfrage.with_entities(*rohspalten).all()
    else:
        zeilen = db.execute(abfrage.with_only_columns(*rohspalten)).all()

    paare = list(
        dict.fromkeys(
            (wert, typ.aad)
            for zeile in zeilen
            for wert, typ in zip(zeile, typen)
            if wert and DisClient.ist_verschluesselt(wert)
        )
    )
    klartexte: dict[tuple[str, str], str] = dict(_vorab.get() or {})
    klartexte.update(gebuendelt_entschluesseln(paare))

    marke = _vorab.set(klartexte)
    try:
        yield
    finally:
        _vorab.reset(marke)
