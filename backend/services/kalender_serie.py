"""Serientermine: Regelformat, Pruefung und Ausbreitung.

Ein Serientermin ist **eine** Zeile in `calendar_events` plus eine Regel. Die
einzelnen Vorkommen existieren nirgends als Datensatz; sie werden beim Lesen
ausgerechnet. Das ist der Grund, warum ein Geburtstag die KI ueberlebt: die
Regel steht am Termin, nicht in einem Gedaechtnis, das jedes Jahr neu befragt
werden muesste.

Die Regel selbst wird **verschluesselt** gespeichert — wie Titel, Beschreibung
und Ort (`CalendarService`). Dieses Modul sieht nur Klartext und weiss nichts
von Schluesseln; es ist reine Rechnung ohne Datenbank und ohne Sidecar.

Spiegel in TypeScript: `frontend/src/services/kalenderSerie.ts`. Beide Seiten
pruefen gegen dieselben Faelle in `tests/fixtures/kalender_serie_vektoren.json`.
Zwei Implementierungen sind der Preis der Ende-zu-Ende-Verschluesselung: eine
Regel, die nur das Geraet lesen kann, kann auch nur das Geraet ausbreiten.

Unterstuetzte Teilmenge von RFC 5545 (Betreiberentscheid 22.09.2026):

    FREQ      DAILY | WEEKLY | MONTHLY | YEARLY
    INTERVAL  ganzzahlig >= 1
    BYDAY     nur bei WEEKLY, nur MO..SU ohne Zahlpraefix
    UNTIL     Datum — **oder** COUNT, nie beides

Alles andere wird abgewiesen statt stillschweigend ignoriert. Eine Verzweigung
ohne `raise` liefert sonst falsche Daten unter dem richtigen Namen: eine Regel
`FREQ=MONTHLY;BYSETPOS=-1` ("letzter Werktag") wuerde als schlichtes "monatlich
am Ersten" durchlaufen, und der Termin stuende jeden Monat am falschen Tag.
"""

from __future__ import annotations

import calendar as _kalender
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
import json
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

__all__ = [
    "SerienRegelFehler",
    "Serie",
    "Vorkommen",
    "LEERE_SERIE",
    "regel_lesen",
    "serie_lesen",
    "serie_schreiben",
    "serie_aus_werkzeug",
    "ausbreiten",
    "kurzform",
]


class SerienRegelFehler(ValueError):
    """Die Regel gehoert nicht zur unterstuetzten Teilmenge."""


FREQUENZEN = ("DAILY", "WEEKLY", "MONTHLY", "YEARLY")

# RFC-5545-Kuerzel. Montag ist 0, wie `date.weekday()`.
_WOCHENTAGE = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}

# Was die KI schreiben darf. Deutsche und englische Kuerzel nebeneinander:
# nachsichtig lesen, streng speichern. Ueberschneidungen (MO, FR, SA) meinen in
# beiden Sprachen denselben Tag, es gibt also nichts zu verwechseln.
_WERKZEUG_WOCHENTAGE = {
    "MO": "MO", "DI": "TU", "MI": "WE", "DO": "TH", "FR": "FR", "SA": "SA", "SO": "SU",
    "TU": "TU", "WE": "WE", "TH": "TH", "SU": "SU",
}

_WERKZEUG_TAKTE = {
    "taeglich": "DAILY",
    "täglich": "DAILY",
    "woechentlich": "WEEKLY",
    "wöchentlich": "WEEKLY",
    "monatlich": "MONTHLY",
    "jaehrlich": "YEARLY",
    "jährlich": "YEARLY",
    # Falls das Modell doch englisch antwortet.
    "daily": "DAILY",
    "weekly": "WEEKLY",
    "monthly": "MONTHLY",
    "yearly": "YEARLY",
}

# Obergrenze fuer die Kandidatenschleife. Greift nur, wenn eine Regel ohne Ende
# auf ein absurd weites Fenster trifft; im Normalfall beendet das Fenster die
# Schleife lange vorher. Ohne die Grenze koennte ein manipuliertes Dokument den
# Erinnerungslauf festhalten.
_MAX_SCHRITTE = 5000


@dataclass(frozen=True)
class Regel:
    """Die geprueften Bestandteile einer RRULE."""

    freq: str
    interval: int = 1
    byday: tuple[str, ...] = ()
    until: date | None = None
    count: int | None = None


@dataclass(frozen=True)
class Serie:
    """Was im verschluesselten Feld `recurrence` eines Termins steht.

    `rrule is None` heisst "kein Serientermin". Dieser Fall ist **nicht** leer,
    sondern wird genauso gespeichert wie jede echte Regel — sonst verriete die
    Datenbank durch "Feld gefuellt vs. Feld leer", welche Termine Serien sind
    (Betreiberentscheid 22.09.2026).
    """

    rrule: str | None = None
    ausnahmen: frozenset[str] = frozenset()
    abweichungen: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def ist_serie(self) -> bool:
        return bool(self.rrule)


@dataclass(frozen=True)
class Vorkommen:
    """Ein einzelnes Vorkommen einer Serie, in UTC."""

    schluessel: str
    """Lokales Datum (YYYY-MM-DD) des **urspruenglichen** Vorkommens.

    Bleibt auch bei einer Abweichung das urspruengliche Datum — sonst faende
    eine spaetere Aenderung ihren eigenen Eintrag nicht wieder.
    """

    start: datetime
    ende: datetime
    ist_abweichung: bool = False
    titel: str | None = None


LEERE_SERIE = Serie()


# ── Zeitzone ──────────────────────────────────────────────────────────────


def zeitzone_von(name: str | None) -> timezone | ZoneInfo:
    """Wie `calendar_service._user_timezone`, aber ohne Benutzerobjekt."""
    kennung = (name or "").strip()
    if kennung:
        try:
            return ZoneInfo(kennung)
        except (ZoneInfoNotFoundError, ValueError, ModuleNotFoundError):
            pass
    return timezone.utc


# ── Regel lesen und schreiben ─────────────────────────────────────────────


def _until_lesen(roh: str) -> date:
    wert = roh.strip().upper()
    # DATE-TIME (20260110T235959Z) und DATE (20260110). Bei DATE-TIME zaehlt
    # nur der Tag: UNTIL ist einschliesslich, und eine Uhrzeit darin haette in
    # der Teilmenge keine Bedeutung, die ein Mensch eingestellt haette.
    if "T" in wert:
        wert = wert.split("T", 1)[0]
    if len(wert) != 8 or not wert.isdigit():
        raise SerienRegelFehler(f"UNTIL ist kein Datum: {roh!r}")
    try:
        return date(int(wert[0:4]), int(wert[4:6]), int(wert[6:8]))
    except ValueError as e:
        raise SerienRegelFehler(f"UNTIL ist kein Datum: {roh!r}") from e


def regel_lesen(rrule: str) -> Regel:
    """Liest und **prueft** eine RRULE. Wirft bei allem ausserhalb der Teilmenge."""
    text = (rrule or "").strip()
    if text.upper().startswith("RRULE:"):
        text = text[6:].strip()
    if not text:
        raise SerienRegelFehler("Leere Wiederholungsregel.")

    teile: dict[str, str] = {}
    for stueck in text.split(";"):
        stueck = stueck.strip()
        if not stueck:
            continue
        if "=" not in stueck:
            raise SerienRegelFehler(f"Bestandteil ohne Wert: {stueck!r}")
        schluessel, _, wert = stueck.partition("=")
        schluessel = schluessel.strip().upper()
        if schluessel in teile:
            raise SerienRegelFehler(f"{schluessel} steht doppelt in der Regel.")
        teile[schluessel] = wert.strip()

    if not teile:
        raise SerienRegelFehler("Leere Wiederholungsregel.")

    erlaubt = {"FREQ", "INTERVAL", "BYDAY", "UNTIL", "COUNT"}
    unbekannt = sorted(set(teile) - erlaubt)
    if unbekannt:
        raise SerienRegelFehler(
            "Nicht unterstuetzt: " + ", ".join(unbekannt) + ". "
            "Erlaubt sind FREQ, INTERVAL, BYDAY, UNTIL, COUNT."
        )

    freq = teile.get("FREQ", "").upper()
    if not freq:
        raise SerienRegelFehler("FREQ fehlt.")
    if freq not in FREQUENZEN:
        raise SerienRegelFehler(
            f"FREQ={freq} gehoert nicht zur Teilmenge ({', '.join(FREQUENZEN)})."
        )

    interval = 1
    if "INTERVAL" in teile:
        try:
            interval = int(teile["INTERVAL"])
        except ValueError as e:
            raise SerienRegelFehler(f"INTERVAL ist keine Zahl: {teile['INTERVAL']!r}") from e
        if interval < 1:
            raise SerienRegelFehler("INTERVAL muss mindestens 1 sein.")

    byday: tuple[str, ...] = ()
    if "BYDAY" in teile:
        if freq != "WEEKLY":
            raise SerienRegelFehler("BYDAY wird nur bei FREQ=WEEKLY unterstuetzt.")
        roh = [t.strip().upper() for t in teile["BYDAY"].split(",") if t.strip()]
        if not roh:
            raise SerienRegelFehler("BYDAY ist leer.")
        for tag in roh:
            if tag not in _WOCHENTAGE:
                raise SerienRegelFehler(
                    f"BYDAY={tag} wird nicht unterstuetzt "
                    "(erlaubt: MO, TU, WE, TH, FR, SA, SU ohne Zahlpraefix)."
                )
        # Sortiert nach Wochentag, damit dieselbe Regel immer denselben Text
        # ergibt — sonst haengt der Ciphertext an der Reihenfolge der Eingabe.
        byday = tuple(sorted(set(roh), key=lambda t: _WOCHENTAGE[t]))

    if "UNTIL" in teile and "COUNT" in teile:
        raise SerienRegelFehler("UNTIL und COUNT schliessen einander aus.")

    until = _until_lesen(teile["UNTIL"]) if "UNTIL" in teile else None

    count = None
    if "COUNT" in teile:
        try:
            count = int(teile["COUNT"])
        except ValueError as e:
            raise SerienRegelFehler(f"COUNT ist keine Zahl: {teile['COUNT']!r}") from e
        if count < 1:
            raise SerienRegelFehler("COUNT muss mindestens 1 sein.")

    return Regel(freq=freq, interval=interval, byday=byday, until=until, count=count)


def regel_schreiben(regel: Regel) -> str:
    """Kanonische Textform — dieselbe Regel ergibt immer denselben String."""
    stuecke = [f"FREQ={regel.freq}"]
    if regel.interval != 1:
        stuecke.append(f"INTERVAL={regel.interval}")
    if regel.byday:
        stuecke.append("BYDAY=" + ",".join(regel.byday))
    if regel.until is not None:
        stuecke.append("UNTIL=" + regel.until.strftime("%Y%m%d"))
    if regel.count is not None:
        stuecke.append(f"COUNT={regel.count}")
    return ";".join(stuecke)


# ── Das Dokument im Feld `recurrence` ─────────────────────────────────────


def _datum_schluessel(roh: Any) -> str:
    """Normiert einen Vorkommen-Schluessel auf YYYY-MM-DD."""
    text = str(roh or "").strip()
    if "T" in text:
        text = text.split("T", 1)[0]
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as e:
        raise SerienRegelFehler(f"Kein gueltiges Datum: {roh!r}") from e


def serie_lesen(klartext: str | None) -> Serie:
    """Liest das entschluesselte Dokument.

    Nachsichtig: alte Zeilen ohne Dokument, leere Strings und unlesbares JSON
    gelten als "kein Serientermin". Ein Termin ist wichtiger als seine
    Wiederholung — wer hier wirft, laesst einen ganzen Kalender verschwinden,
    weil eine einzige Zeile verbogen ist.
    """
    text = (klartext or "").strip()
    if not text:
        return LEERE_SERIE
    try:
        roh = json.loads(text)
    except (ValueError, TypeError):
        return LEERE_SERIE
    if not isinstance(roh, dict):
        return LEERE_SERIE

    rrule = roh.get("rrule")
    if rrule is not None:
        rrule = str(rrule).strip() or None
    if rrule is not None:
        try:
            # Kanonisieren, damit eine von Hand geschriebene Regel dieselbe
            # Gestalt bekommt wie eine erzeugte.
            rrule = regel_schreiben(regel_lesen(rrule))
        except SerienRegelFehler:
            # Unlesbare Regel: der Termin bleibt, die Wiederholung faellt weg.
            return LEERE_SERIE

    ausnahmen: set[str] = set()
    for eintrag in roh.get("ausnahmen") or []:
        try:
            ausnahmen.add(_datum_schluessel(eintrag))
        except SerienRegelFehler:
            continue

    abweichungen: dict[str, dict[str, Any]] = {}
    roh_abw = roh.get("abweichungen")
    if isinstance(roh_abw, dict):
        for schluessel, wert in roh_abw.items():
            if not isinstance(wert, dict):
                continue
            try:
                abweichungen[_datum_schluessel(schluessel)] = wert
            except SerienRegelFehler:
                continue

    return Serie(rrule=rrule, ausnahmen=frozenset(ausnahmen), abweichungen=abweichungen)


def serie_schreiben(serie: Serie) -> str:
    """Kanonische Klartextform des Dokuments, bereit zum Verschluesseln."""
    dokument: dict[str, Any] = {"rrule": serie.rrule}
    if serie.ausnahmen:
        dokument["ausnahmen"] = sorted(serie.ausnahmen)
    if serie.abweichungen:
        dokument["abweichungen"] = {k: serie.abweichungen[k] for k in sorted(serie.abweichungen)}
    return json.dumps(dokument, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


LEERES_DOKUMENT = serie_schreiben(LEERE_SERIE)


# ── Aus dem Werkzeugargument der KI ───────────────────────────────────────


def serie_aus_werkzeug(roh: Any) -> Serie:
    """Baut eine Serie aus dem, was die KI geschrieben hat.

    Nachsichtig lesen, streng speichern: deutsche wie englische Kuerzel,
    Gross- und Kleinschreibung egal, `null` und `{}` heissen "keine
    Wiederholung". Was danach noch falsch ist, wirft — ein Formfehler kostet
    eine Runde, ein stillschweigend verworfener Takt kostet den Termin.
    """
    if roh is None:
        return LEERE_SERIE
    if not isinstance(roh, dict):
        raise SerienRegelFehler("recurrence muss ein Objekt sein.")
    if not roh:
        return LEERE_SERIE

    # `{"takt": null}` ist der ausdrueckliche Weg, eine bestehende Serie
    # aufzuloesen — und der einzige. Das Feld ganz wegzulassen heisst "nicht
    # anfassen"; ohne diesen Unterschied verloere ein Geburtstag seine
    # Wiederholung, sobald jemand nur den Titel korrigiert.
    #
    # Ein **fehlender** Takt neben anderen Feldern bleibt dagegen ein Fehler:
    # `{"intervall": 2}` ist keine Absicht, sondern eine halbe Angabe.
    genannt = [k for k in ("takt", "freq", "frequenz") if k in roh]
    if genannt and all(roh[k] is None for k in genannt):
        return LEERE_SERIE

    roh_takt = roh.get("takt") or roh.get("freq") or roh.get("frequenz")
    if roh_takt is None:
        raise SerienRegelFehler(
            "recurrence braucht einen takt (taeglich, woechentlich, monatlich, jaehrlich)."
        )
    takt = _WERKZEUG_TAKTE.get(str(roh_takt).strip().lower())
    if not takt:
        raise SerienRegelFehler(
            f"Unbekannter takt: {roh_takt!r}. "
            "Erlaubt sind taeglich, woechentlich, monatlich, jaehrlich."
        )

    stuecke = [f"FREQ={takt}"]

    roh_intervall = roh.get("intervall", roh.get("interval"))
    if roh_intervall is not None:
        try:
            intervall = int(roh_intervall)
        except (TypeError, ValueError) as e:
            raise SerienRegelFehler(f"intervall ist keine Zahl: {roh_intervall!r}") from e
        if intervall < 1:
            raise SerienRegelFehler("intervall muss mindestens 1 sein.")
        if intervall != 1:
            stuecke.append(f"INTERVAL={intervall}")

    roh_tage = roh.get("wochentage") or roh.get("byday")
    if roh_tage:
        if takt != "WEEKLY":
            raise SerienRegelFehler("wochentage gibt es nur bei takt=woechentlich.")
        if isinstance(roh_tage, str):
            roh_tage = [t for t in roh_tage.replace(";", ",").split(",")]
        tage: list[str] = []
        for eintrag in roh_tage:
            kuerzel = _WERKZEUG_WOCHENTAGE.get(str(eintrag).strip().upper())
            if not kuerzel:
                raise SerienRegelFehler(
                    f"Unbekannter Wochentag: {eintrag!r}. "
                    "Erlaubt sind MO, DI, MI, DO, FR, SA, SO."
                )
            tage.append(kuerzel)
        if tage:
            stuecke.append("BYDAY=" + ",".join(tage))

    roh_bis = roh.get("bis") or roh.get("until")
    roh_anzahl = roh.get("anzahl", roh.get("count"))
    if roh_bis and roh_anzahl is not None:
        raise SerienRegelFehler("bis und anzahl schliessen einander aus.")

    if roh_bis:
        text = str(roh_bis).strip()
        if "T" in text:
            text = text.split("T", 1)[0]
        text = text.replace("-", "").replace(".", "").replace("/", "")
        if len(text) != 8 or not text.isdigit():
            raise SerienRegelFehler(f"bis ist kein Datum (YYYY-MM-DD erwartet): {roh_bis!r}")
        stuecke.append(f"UNTIL={text}")

    if roh_anzahl is not None:
        try:
            anzahl = int(roh_anzahl)
        except (TypeError, ValueError) as e:
            raise SerienRegelFehler(f"anzahl ist keine Zahl: {roh_anzahl!r}") from e
        if anzahl < 1:
            raise SerienRegelFehler("anzahl muss mindestens 1 sein.")
        stuecke.append(f"COUNT={anzahl}")

    # Ein letztes Mal durch die strenge Pruefung, damit hier und dort dieselben
    # Regeln gelten.
    return Serie(rrule=regel_schreiben(regel_lesen(";".join(stuecke))))


# ── Ausbreitung ───────────────────────────────────────────────────────────


def _monat_versetzt(jahr: int, monat: int, schritte: int) -> tuple[int, int] | None:
    gesamt = (jahr * 12 + (monat - 1)) + schritte
    neues_jahr = gesamt // 12
    if not (date.min.year <= neues_jahr <= date.max.year):
        return None
    return neues_jahr, (gesamt % 12) + 1


def _lokale_kandidaten(regel: Regel, start_lokal: datetime, grenze_lokal: date):
    """Erzeugt lokale Startzeitpunkte in aufsteigender Reihenfolge.

    Liefert Wanduhrzeiten, keine Instanten: eine woechentliche Besprechung um
    10:00 ist um 10:00, diesseits und jenseits der Sommerzeit. Die Umrechnung
    nach UTC passiert erst beim Aufrufer.
    """
    uhrzeit: time = start_lokal.timetz().replace(tzinfo=None)
    tz = start_lokal.tzinfo
    erstes_datum = start_lokal.date()
    geliefert = 0

    def baue(d: date) -> datetime:
        return datetime.combine(d, uhrzeit, tzinfo=tz)

    def fertig(d: date) -> bool:
        if regel.until is not None and d > regel.until:
            return True
        if regel.count is not None and geliefert >= regel.count:
            return True
        # Das Fenster begrenzt nur, wenn kein COUNT mitzaehlt: bei COUNT muss
        # bis zum letzten Vorkommen gezaehlt werden, auch weit hinter dem
        # sichtbaren Bereich, sonst stimmt die Zahl nicht.
        if regel.count is None and d > grenze_lokal:
            return True
        return False

    if regel.freq in ("DAILY", "WEEKLY"):
        schritt_tage = regel.interval * (7 if regel.freq == "WEEKLY" else 1)

        if regel.freq == "WEEKLY" and regel.byday:
            gewuenscht = sorted(_WOCHENTAGE[t] for t in regel.byday)
            # Anker ist der Montag der Woche, in der DTSTART liegt (WKST=MO).
            wochenanfang = erstes_datum - timedelta(days=erstes_datum.weekday())
            for n in range(_MAX_SCHRITTE):
                try:
                    woche = wochenanfang + timedelta(days=n * 7 * regel.interval)
                except OverflowError:
                    return
                if regel.count is None and woche > grenze_lokal:
                    return
                for wtag in gewuenscht:
                    d = woche + timedelta(days=wtag)
                    if d < erstes_datum:
                        continue
                    if fertig(d):
                        return
                    geliefert += 1
                    yield d, baue(d)
            return

        for n in range(_MAX_SCHRITTE):
            try:
                d = erstes_datum + timedelta(days=n * schritt_tage)
            except OverflowError:
                return
            if fertig(d):
                return
            geliefert += 1
            yield d, baue(d)
        return

    if regel.freq == "MONTHLY":
        tag = erstes_datum.day
        for n in range(_MAX_SCHRITTE):
            versetzt = _monat_versetzt(erstes_datum.year, erstes_datum.month, n * regel.interval)
            if versetzt is None:
                # Hinter dem Jahr 9999 gibt es keinen Kalender mehr. Ohne diese
                # Kante wuerde `FREQ=MONTHLY;INTERVAL=9999` den Erinnerungslauf
                # mit einem ValueError abbrechen — und zwar fuer **alle**
                # Benutzer, nicht nur fuer den mit dem verbogenen Dokument.
                return
            jahr, monat = versetzt
            letzter = _kalender.monthrange(jahr, monat)[1]
            if tag > letzter:
                # Den 31. gibt es im Februar nicht. RFC 5545: ueberspringen,
                # nicht verschieben — sonst stuende die Miete im Februar am
                # 28. und im Maerz wieder am 31.
                if regel.count is None and date(jahr, monat, letzter) > grenze_lokal:
                    return
                continue
            d = date(jahr, monat, tag)
            if fertig(d):
                return
            geliefert += 1
            yield d, baue(d)
        return

    # YEARLY
    monat, tag = erstes_datum.month, erstes_datum.day
    for n in range(_MAX_SCHRITTE):
        jahr = erstes_datum.year + n * regel.interval
        if jahr > date.max.year:
            return
        letzter = _kalender.monthrange(jahr, monat)[1]
        if tag > letzter:
            # Der 29. Februar, ausserhalb der Schaltjahre.
            if regel.count is None and date(jahr, monat, letzter) > grenze_lokal:
                return
            continue
        d = date(jahr, monat, tag)
        if fertig(d):
            return
        geliefert += 1
        yield d, baue(d)


def _zeitpunkt_lesen(roh: Any) -> datetime | None:
    text = str(roh or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def ausbreiten(
    serie: Serie,
    start: datetime,
    ende: datetime,
    *,
    ganztaegig: bool = False,
    zeitzone: str | None = None,
    fenster_von: datetime | None = None,
    fenster_bis: datetime | None = None,
) -> list[Vorkommen]:
    """Rechnet die Vorkommen einer Serie im Fenster aus.

    `start` und `ende` sind der Termin selbst (UTC), `fenster_von`/`fenster_bis`
    der sichtbare Bereich. Ein Vorkommen gehoert dazu, wenn es den Bereich
    beruehrt — ein dreitaegiger Termin erscheint auch, wenn nur sein Mittelteil
    im Fenster liegt.
    """
    tz = zeitzone_von(zeitzone)
    start_utc = start if start.tzinfo else start.replace(tzinfo=timezone.utc)
    ende_utc = ende if ende.tzinfo else ende.replace(tzinfo=timezone.utc)
    start_utc = start_utc.astimezone(timezone.utc)
    ende_utc = ende_utc.astimezone(timezone.utc)

    dauer = ende_utc - start_utc
    if dauer < timedelta(0):
        dauer = timedelta(0)
    # Ganztaegige Termine rechnen in Tagen, nicht in Stunden: ueber einen
    # Zeitumstellungstag ist ein Tag 23 oder 25 Stunden lang, und der Termin
    # soll trotzdem um Mitternacht enden.
    ganze_tage = max(1, round(dauer.total_seconds() / 86400)) if ganztaegig else 0

    start_lokal = start_utc.astimezone(tz)

    def bis_utc(beginn_lokal: datetime) -> tuple[datetime, datetime]:
        a = beginn_lokal.astimezone(timezone.utc)
        if ganztaegig:
            b = (beginn_lokal + timedelta(days=ganze_tage)).astimezone(timezone.utc)
        else:
            b = a + dauer
        return a, b

    def im_fenster(a: datetime, b: datetime) -> bool:
        if fenster_bis is not None and a > fenster_bis:
            return False
        if fenster_von is not None and b < fenster_von:
            return False
        return True

    ergebnis: list[Vorkommen] = []

    def eintragen(schluessel: str, a: datetime, b: datetime) -> None:
        if schluessel in serie.ausnahmen:
            return
        abweichung = serie.abweichungen.get(schluessel)
        ist_abweichung = False
        titel = None
        if abweichung is not None:
            neu_a = _zeitpunkt_lesen(abweichung.get("start"))
            neu_b = _zeitpunkt_lesen(abweichung.get("ende"))
            if neu_a is not None:
                b = neu_b if neu_b is not None else neu_a + (b - a)
                a = neu_a
            elif neu_b is not None:
                b = neu_b
            roh_titel = abweichung.get("titel")
            titel = str(roh_titel) if roh_titel else None
            ist_abweichung = True
        if not im_fenster(a, b):
            return
        ergebnis.append(
            Vorkommen(schluessel=schluessel, start=a, ende=b, ist_abweichung=ist_abweichung, titel=titel)
        )

    if not serie.ist_serie:
        a, b = start_utc, ende_utc
        eintragen(start_lokal.date().isoformat(), a, b)
        return ergebnis

    try:
        regel = regel_lesen(serie.rrule or "")
    except SerienRegelFehler:
        # `serie_lesen` faengt das schon ab; hier steht es fuer den Fall, dass
        # jemand eine Serie von Hand zusammensetzt.
        a, b = start_utc, ende_utc
        eintragen(start_lokal.date().isoformat(), a, b)
        return ergebnis

    # Bis wohin ueberhaupt gerechnet wird. Ohne Fensterende gaebe es bei einer
    # Regel ohne Ende keinen Grund aufzuhoeren.
    if fenster_bis is not None:
        grenze_lokal = fenster_bis.astimezone(tz).date() + timedelta(days=1)
    else:
        grenze_lokal = date.max - timedelta(days=1)

    for _datum, beginn_lokal in _lokale_kandidaten(regel, start_lokal, grenze_lokal):
        a, b = bis_utc(beginn_lokal)
        eintragen(_datum.isoformat(), a, b)

    # Eine Abweichung kann ein Vorkommen nach hinten verschieben; sortieren,
    # damit die Ansicht nicht springt.
    ergebnis.sort(key=lambda v: v.start)
    return ergebnis


# ── Fuer Menschen ─────────────────────────────────────────────────────────

_KURZ_TAKT = {
    "DAILY": ("täglich", "alle {n} Tage"),
    "WEEKLY": ("wöchentlich", "alle {n} Wochen"),
    "MONTHLY": ("monatlich", "alle {n} Monate"),
    "YEARLY": ("jährlich", "alle {n} Jahre"),
}

_KURZ_TAGE = {
    "MO": "Mo", "TU": "Di", "WE": "Mi", "TH": "Do", "FR": "Fr", "SA": "Sa", "SU": "So",
}


def kurzform(serie: Serie) -> str:
    """Eine Zeile, die ein Mensch vor der Freigabe lesen kann.

    Steht bewusst im Klartext in der Vorschau eines Vorschlags: wer nicht sieht,
    dass er eine **jaehrliche** Wiederholung freigibt, gibt etwas anderes frei,
    als er denkt.
    """
    if not serie.ist_serie:
        return "einmalig"
    try:
        regel = regel_lesen(serie.rrule or "")
    except SerienRegelFehler:
        return "einmalig"

    einfach, mehrfach = _KURZ_TAKT[regel.freq]
    text = einfach if regel.interval == 1 else mehrfach.format(n=regel.interval)

    if regel.byday:
        text += " (" + ", ".join(_KURZ_TAGE[t] for t in regel.byday) + ")"

    if regel.until is not None:
        text += " bis " + regel.until.strftime("%d.%m.%Y")
    elif regel.count is not None:
        text += f", {regel.count} Termine"
    else:
        text += ", ohne Ende"

    if serie.ausnahmen:
        text += f", {len(serie.ausnahmen)} ausgenommen"
    if serie.abweichungen:
        text += f", {len(serie.abweichungen)} verschoben"
    return text
