"""Die Ausbreitung von Serienterminen — gegen die gemeinsamen Vektoren.

Dieselbe Datei liest `frontend/src/services/kalenderSerie.test.ts`. Weichen die
beiden Implementierungen voneinander ab, zeigt die Ansicht etwas anderes an,
als die Erinnerung verschickt — und niemand merkt es, weil beide Seiten fuer
sich genommen gruen sind.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from services.kalender_serie import (
    LEERE_SERIE,
    LEERES_DOKUMENT,
    Serie,
    SerienRegelFehler,
    ausbreiten,
    kurzform,
    regel_lesen,
    regel_schreiben,
    serie_aus_werkzeug,
    serie_lesen,
    serie_schreiben,
)


def _vektoren() -> dict:
    wurzel = Path(__file__).resolve().parents[2]
    pfad = wurzel / "tests" / "fixtures" / "kalender_serie_vektoren.json"
    assert pfad.is_file(), f"Vektoren nicht gefunden: {pfad}"
    with open(pfad, "r", encoding="utf-8") as f:
        return json.load(f)


VEKTOREN = _vektoren()


def _utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Die gemeinsamen Faelle ────────────────────────────────────────────────


@pytest.mark.parametrize("fall", VEKTOREN["gueltig"], ids=lambda f: f["name"])
def test_ausbreitung_folgt_den_gemeinsamen_vektoren(fall: dict) -> None:
    serie = Serie(
        rrule=fall.get("rrule"),
        ausnahmen=frozenset(fall.get("ausnahmen") or []),
        abweichungen=fall.get("abweichungen") or {},
    )

    vorkommen = ausbreiten(
        serie,
        _utc(fall["start"]),
        _utc(fall["ende"]),
        ganztaegig=fall.get("ganztaegig", False),
        zeitzone=fall.get("zeitzone"),
        fenster_von=_utc(fall["fenster_von"]),
        fenster_bis=_utc(fall["fenster_bis"]),
    )

    tatsaechlich = [
        {
            "schluessel": v.schluessel,
            "start": _iso(v.start),
            "ende": _iso(v.ende),
            **({"ist_abweichung": True} if v.ist_abweichung else {}),
            **({"titel": v.titel} if v.titel else {}),
        }
        for v in vorkommen
    ]
    assert tatsaechlich == fall["erwartet"]


@pytest.mark.parametrize("fall", VEKTOREN["ungueltig"], ids=lambda f: f["name"])
def test_ungueltige_regeln_werden_abgewiesen(fall: dict) -> None:
    """Abgewiesen, nicht stillschweigend vereinfacht.

    Eine Verzweigung ohne `raise` haette aus `FREQ=MONTHLY;BYSETPOS=-1`
    ("letzter Werktag") ein schlichtes "monatlich am Ersten" gemacht — falsche
    Daten unter dem richtigen Namen.
    """
    with pytest.raises(SerienRegelFehler):
        regel_lesen(fall["rrule"])


# ── Regeltext ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "eingabe,erwartet",
    [
        ("FREQ=YEARLY", "FREQ=YEARLY"),
        ("freq=yearly", "FREQ=YEARLY"),
        ("RRULE:FREQ=DAILY;INTERVAL=1", "FREQ=DAILY"),
        ("  FREQ=WEEKLY ; BYDAY=TH,MO ", "FREQ=WEEKLY;BYDAY=MO,TH"),
        ("FREQ=WEEKLY;BYDAY=MO,MO", "FREQ=WEEKLY;BYDAY=MO"),
        ("FREQ=DAILY;UNTIL=20260110T235959Z", "FREQ=DAILY;UNTIL=20260110"),
    ],
)
def test_regeltext_ist_kanonisch(eingabe: str, erwartet: str) -> None:
    """Dieselbe Regel ergibt immer denselben Text.

    Wichtig, weil der Text verschluesselt wird: haenge die Gestalt des
    Ciphertexts an der Reihenfolge der Eingabe, verriete schon ein Vergleich
    zweier Zeilen etwas ueber ihren Inhalt.
    """
    assert regel_schreiben(regel_lesen(eingabe)) == erwartet


def test_doppelter_schluessel_wird_abgewiesen() -> None:
    with pytest.raises(SerienRegelFehler):
        regel_lesen("FREQ=DAILY;FREQ=WEEKLY")


# ── Das Dokument ──────────────────────────────────────────────────────────


def test_leeres_dokument_sieht_aus_wie_ein_volles() -> None:
    """Der Kern der Metadaten-Entscheidung.

    Auch ein Termin ohne Wiederholung traegt ein Dokument. Waere das Feld bei
    Einzelterminen leer, verriete die Datenbank durch blosses Hinsehen, welche
    Zeilen Serien sind — ohne dass jemand etwas entschluesseln muesste.
    """
    assert LEERES_DOKUMENT
    assert LEERES_DOKUMENT.strip() != ""
    assert serie_lesen(LEERES_DOKUMENT) == LEERE_SERIE
    assert not serie_lesen(LEERES_DOKUMENT).ist_serie


def test_dokument_haelt_den_umlauf_aus() -> None:
    serie = Serie(
        rrule="FREQ=YEARLY",
        ausnahmen=frozenset({"2027-03-14"}),
        abweichungen={"2028-03-14": {"start": "2028-03-15T09:00:00Z", "titel": "Nachgefeiert"}},
    )
    wieder = serie_lesen(serie_schreiben(serie))
    assert wieder.rrule == "FREQ=YEARLY"
    assert wieder.ausnahmen == frozenset({"2027-03-14"})
    assert wieder.abweichungen["2028-03-14"]["titel"] == "Nachgefeiert"


@pytest.mark.parametrize(
    "muell",
    [
        "",
        "   ",
        "kein json",
        "[]",
        "null",
        '{"rrule": "FREQ=MONTHLY;BYSETPOS=-1"}',
        '{"rrule": 42}',
        '{"rrule": "FREQ=YEARLY", "ausnahmen": "keine Liste"}',
        '{"rrule": "FREQ=YEARLY", "abweichungen": ["keine Zuordnung"]}',
        '{"rrule": "FREQ=YEARLY", "ausnahmen": ["gestern", "2027-03-14"]}',
    ],
)
def test_verbogene_dokumente_kosten_nie_den_termin(muell: str) -> None:
    """Nachsichtig lesen: ein kaputtes Dokument nimmt die Wiederholung, nicht den Termin.

    Wer hier wirft, laesst einen ganzen Kalender verschwinden, weil eine
    einzige Zeile verbogen ist — und genau dieser Fall trat bei der Ablage des
    Messengers schon einmal ein.
    """
    serie = serie_lesen(muell)
    vorkommen = ausbreiten(
        serie,
        _utc("2026-05-04T10:00:00Z"),
        _utc("2026-05-04T11:00:00Z"),
        zeitzone="Europe/Berlin",
        fenster_von=_utc("2026-01-01T00:00:00Z"),
        fenster_bis=_utc("2027-01-01T00:00:00Z"),
    )
    assert len(vorkommen) >= 1
    assert vorkommen[0].start == _utc("2026-05-04T10:00:00Z")


# ── Missbrauch ────────────────────────────────────────────────────────────


def test_regel_ohne_ende_haelt_den_erinnerungslauf_nicht_fest() -> None:
    """Ein taeglicher Termin ohne Ende, Fenster ueber hundert Jahre.

    Ohne Obergrenze liefe die Schleife hier 36.500 Mal je Termin und je
    Erinnerungstakt. Die Grenze ist kein Komfort, sie ist die Bremse gegen ein
    Dokument, das jemand von Hand weit aufgezogen hat.
    """
    serie = Serie(rrule="FREQ=DAILY")
    vorkommen = ausbreiten(
        serie,
        _utc("2026-01-01T10:00:00Z"),
        _utc("2026-01-01T11:00:00Z"),
        zeitzone="Europe/Berlin",
        fenster_von=_utc("2026-01-01T00:00:00Z"),
        fenster_bis=_utc("2126-01-01T00:00:00Z"),
    )
    assert len(vorkommen) <= 5000


def test_grosse_anzahl_wird_gedeckelt() -> None:
    serie = Serie(rrule="FREQ=DAILY;COUNT=999999")
    vorkommen = ausbreiten(
        serie,
        _utc("2026-01-01T10:00:00Z"),
        _utc("2026-01-01T11:00:00Z"),
        zeitzone="Europe/Berlin",
        fenster_von=_utc("2026-01-01T00:00:00Z"),
        fenster_bis=_utc("2026-02-01T00:00:00Z"),
    )
    assert len(vorkommen) == 31


@pytest.mark.parametrize(
    "rrule",
    [
        "FREQ=YEARLY;INTERVAL=9999",
        "FREQ=MONTHLY;INTERVAL=9999",
        "FREQ=WEEKLY;INTERVAL=999999;BYDAY=MO",
        "FREQ=DAILY;INTERVAL=999999",
        "FREQ=DAILY;INTERVAL=999999;COUNT=1000",
    ],
)
def test_riesige_intervalle_laufen_nicht_aus_dem_kalender(rrule: str) -> None:
    """Hinter dem Jahr 9999 hoert der Kalender auf.

    Gefunden beim Durchgehen der Missbrauchsfaelle: `FREQ=YEARLY;INTERVAL=9999`
    lief in `date(jahr, ...)` in einen ValueError. Der Erinnerungslauf geht
    ueber **alle** Benutzer — ein einziges von Hand verbogenes Dokument haette
    damit die Erinnerungen aller anderen mitgerissen. Ohne Fensterende
    (`fenster_bis=None`) ist der Fall am schaerfsten, denn dann begrenzt nur
    noch die Schrittzahl.
    """
    serie = Serie(rrule=rrule)
    vorkommen = ausbreiten(
        serie,
        _utc("2026-01-01T10:00:00Z"),
        _utc("2026-01-01T11:00:00Z"),
        zeitzone="UTC",
    )
    assert isinstance(vorkommen, list)


def test_ende_vor_dem_anfang_erzeugt_keine_negative_dauer() -> None:
    serie = Serie(rrule="FREQ=DAILY;COUNT=2")
    vorkommen = ausbreiten(
        serie,
        _utc("2026-01-01T10:00:00Z"),
        _utc("2026-01-01T09:00:00Z"),
        zeitzone="Europe/Berlin",
        fenster_von=_utc("2026-01-01T00:00:00Z"),
        fenster_bis=_utc("2026-02-01T00:00:00Z"),
    )
    assert all(v.ende >= v.start for v in vorkommen)


def test_unbekannte_zeitzone_faellt_auf_utc_und_wirft_nicht() -> None:
    serie = Serie(rrule="FREQ=DAILY;COUNT=2")
    vorkommen = ausbreiten(
        serie,
        _utc("2026-01-01T10:00:00Z"),
        _utc("2026-01-01T11:00:00Z"),
        zeitzone="Mond/Krater",
        fenster_von=_utc("2026-01-01T00:00:00Z"),
        fenster_bis=_utc("2026-02-01T00:00:00Z"),
    )
    assert [_iso(v.start) for v in vorkommen] == [
        "2026-01-01T10:00:00Z",
        "2026-01-02T10:00:00Z",
    ]


def test_ausnahme_fuer_jedes_vorkommen_liefert_nichts_statt_zu_werfen() -> None:
    serie = Serie(
        rrule="FREQ=DAILY;COUNT=3",
        ausnahmen=frozenset({"2026-01-01", "2026-01-02", "2026-01-03"}),
    )
    vorkommen = ausbreiten(
        serie,
        _utc("2026-01-01T10:00:00Z"),
        _utc("2026-01-01T11:00:00Z"),
        zeitzone="UTC",
        fenster_von=_utc("2026-01-01T00:00:00Z"),
        fenster_bis=_utc("2026-02-01T00:00:00Z"),
    )
    assert vorkommen == []


def test_abweichung_ohne_zeitangabe_laesst_das_vorkommen_stehen() -> None:
    serie = Serie(
        rrule="FREQ=DAILY;COUNT=1",
        abweichungen={"2026-01-01": {"titel": "Nur umbenannt"}},
    )
    vorkommen = ausbreiten(
        serie,
        _utc("2026-01-01T10:00:00Z"),
        _utc("2026-01-01T11:00:00Z"),
        zeitzone="UTC",
        fenster_von=_utc("2026-01-01T00:00:00Z"),
        fenster_bis=_utc("2026-02-01T00:00:00Z"),
    )
    assert len(vorkommen) == 1
    assert vorkommen[0].titel == "Nur umbenannt"
    assert vorkommen[0].start == _utc("2026-01-01T10:00:00Z")


# ── Das Werkzeugargument der KI ───────────────────────────────────────────


@pytest.mark.parametrize(
    "eingabe,erwartet",
    [
        ({"takt": "jaehrlich"}, "FREQ=YEARLY"),
        ({"takt": "jährlich"}, "FREQ=YEARLY"),
        ({"takt": "YEARLY"}, "FREQ=YEARLY"),
        ({"takt": "woechentlich", "wochentage": ["MO", "DO"]}, "FREQ=WEEKLY;BYDAY=MO,TH"),
        ({"takt": "woechentlich", "wochentage": ["MO", "TH"]}, "FREQ=WEEKLY;BYDAY=MO,TH"),
        ({"takt": "woechentlich", "wochentage": "mo,do"}, "FREQ=WEEKLY;BYDAY=MO,TH"),
        ({"takt": "woechentlich", "intervall": 2}, "FREQ=WEEKLY;INTERVAL=2"),
        ({"takt": "monatlich", "anzahl": 12}, "FREQ=MONTHLY;COUNT=12"),
        ({"takt": "taeglich", "bis": "2026-01-10"}, "FREQ=DAILY;UNTIL=20260110"),
        ({"takt": "taeglich", "bis": "2026-01-10T00:00:00Z"}, "FREQ=DAILY;UNTIL=20260110"),
        ({"takt": "taeglich", "intervall": 1}, "FREQ=DAILY"),
    ],
)
def test_werkzeugargument_wird_nachsichtig_gelesen(eingabe: dict, erwartet: str) -> None:
    """Deutsch wie englisch, Gross wie klein.

    Ein Formfehler soll eine Runde kosten, nicht die Antwort — aber gespeichert
    wird nur die kanonische Form.
    """
    assert serie_aus_werkzeug(eingabe).rrule == erwartet


@pytest.mark.parametrize("leer", [None, {}, {"takt": None}, {"freq": None}])
def test_fehlende_wiederholung_heisst_einmalig(leer) -> None:
    """`{"takt": null}` ist der ausdrueckliche Weg, eine Serie aufzuloesen.

    Der Systemprompt nennt genau diesen Weg (`ai_prompt`, Regel 5a) — beim
    ersten Bau warf das Modul hier noch, und die Anweisung haette ins Leere
    gefuehrt.
    """
    assert serie_aus_werkzeug(leer) == LEERE_SERIE


@pytest.mark.parametrize(
    "eingabe",
    [
        {"takt": "alle drei Tage"},
        {"takt": "jaehrlich", "intervall": 0},
        {"takt": "jaehrlich", "intervall": "zwei"},
        {"takt": "monatlich", "wochentage": ["MO"]},
        {"takt": "woechentlich", "wochentage": ["Montag"]},
        {"takt": "taeglich", "bis": "2026-01-10", "anzahl": 3},
        {"takt": "taeglich", "bis": "irgendwann"},
        {"takt": "taeglich", "anzahl": 0},
        {"intervall": 2},
        "jaehrlich",
        ["jaehrlich"],
    ],
)
def test_unbrauchbares_werkzeugargument_wirft(eingabe) -> None:
    with pytest.raises(SerienRegelFehler):
        serie_aus_werkzeug(eingabe)


# ── Kurzform fuer die Vorschau ────────────────────────────────────────────


@pytest.mark.parametrize(
    "rrule,erwartet",
    [
        (None, "einmalig"),
        ("FREQ=YEARLY", "jährlich, ohne Ende"),
        ("FREQ=MONTHLY;COUNT=12", "monatlich, 12 Termine"),
        ("FREQ=WEEKLY;BYDAY=MO,TH", "wöchentlich (Mo, Do), ohne Ende"),
        ("FREQ=WEEKLY;INTERVAL=2", "alle 2 Wochen, ohne Ende"),
        ("FREQ=DAILY;UNTIL=20260110", "täglich bis 10.01.2026"),
    ],
)
def test_kurzform_ist_lesbar(rrule: str | None, erwartet: str) -> None:
    assert kurzform(Serie(rrule=rrule)) == erwartet


def test_kurzform_nennt_ausnahmen_und_abweichungen() -> None:
    serie = Serie(
        rrule="FREQ=YEARLY",
        ausnahmen=frozenset({"2027-03-14"}),
        abweichungen={"2028-03-14": {"start": "2028-03-15T09:00:00Z"}},
    )
    assert kurzform(serie) == "jährlich, ohne Ende, 1 ausgenommen, 1 verschoben"


def test_ausbreiten_count_beendet_sofort_nach_fenster_grenze() -> None:
    """Prüft, dass Serien mit COUNT sofort abbrechen, sobald fenster_bis überschritten wird."""
    start = _utc("2026-01-01T10:00:00Z")
    ende = _utc("2026-01-01T11:00:00Z")
    fenster_bis = _utc("2026-01-10T00:00:00Z")

    # Täglich mit großem COUNT
    serie_tag = Serie(rrule="FREQ=DAILY;COUNT=10000")
    vorkommen_tag = list(ausbreiten(serie_tag, start, ende, fenster_bis=fenster_bis))
    assert len(vorkommen_tag) == 9
    assert all(v.start < fenster_bis for v in vorkommen_tag)

    # Wöchentlich mit BYDAY und großem COUNT
    serie_woche = Serie(rrule="FREQ=WEEKLY;COUNT=10000;BYDAY=MO,WE,FR")
    vorkommen_woche = list(ausbreiten(serie_woche, start, ende, fenster_bis=fenster_bis))
    assert all(v.start < fenster_bis for v in vorkommen_woche)

    # Monatlich
    fenster_bis_monat = _utc("2026-06-01T00:00:00Z")
    serie_monat = Serie(rrule="FREQ=MONTHLY;COUNT=10000")
    vorkommen_monat = list(ausbreiten(serie_monat, start, ende, fenster_bis=fenster_bis_monat))
    assert len(vorkommen_monat) == 5
    assert all(v.start < fenster_bis_monat for v in vorkommen_monat)

    # Jährlich
    fenster_bis_jahr = _utc("2030-01-01T00:00:00Z")
    serie_jahr = Serie(rrule="FREQ=YEARLY;COUNT=10000")
    vorkommen_jahr = list(ausbreiten(serie_jahr, start, ende, fenster_bis=fenster_bis_jahr))
    assert len(vorkommen_jahr) == 4
    assert all(v.start < fenster_bis_jahr for v in vorkommen_jahr)

