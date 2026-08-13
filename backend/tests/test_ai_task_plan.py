"""Wann ist "jeden Tag um 8 Uhr" das naechste Mal?

Die Rechnung ist der Teil des Features, den niemand sieht und jeder merkt: eine
Mail, die im Winter eine Stunde zu frueh kommt, ist kein Absturz, sondern ein
Aergernis ohne Fehlermeldung. Deshalb steht die Sommerzeit hier ausdruecklich im
Test und nicht nur im Kommentar.

Gerechnet wird ohne Datenbank — `naechste_faelligkeit` nimmt ein Objekt und
einen Zeitpunkt und gibt einen zurueck. Das ist Absicht: die Zeitrechnung soll
sich pruefen lassen, ohne dass eine Aufgabe angelegt, ein Recht vergeben und
eine Sitzung aufgebaut werden muss.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from models import AiTask
from services import ai_task_service
from services.ai_action_errors import AiActionValidationError


def _aufgabe(**felder) -> AiTask:
    """Eine Aufgabe ohne Datenbank — nur die Felder, um die es hier geht."""
    grund = {
        "id": "t",
        "user_id": 1,
        "title": "Bericht",
        "instruction": "Sieh nach.",
        "kind": "report",
        "plan_kind": "daily",
        "time_of_day": "08:00",
        "time_zone": "Europe/Berlin",
        "channel": "chat",
        "enabled": True,
    }
    return AiTask(**{**grund, **felder})


def _utc(*teile: int) -> datetime:
    return datetime(*teile, tzinfo=timezone.utc)


# ── Taeglich ──────────────────────────────────────────────────────────────


def test_acht_uhr_ortszeit_ist_im_sommer_sechs_uhr_utc() -> None:
    """Der eigentliche Zweck der Zeitzone an der Aufgabe.

    Ohne sie stuende "08:00" fuer acht Uhr UTC — und der Betreiber in Berlin
    bekaeme seine Morgenmail um zehn. Die vorhandenen Zeitplaene des Panels
    (`restart_times_utc`) machen genau das und muessen es, weil dort niemand
    gesagt hat, wo er wohnt.
    """
    aufgabe = _aufgabe()
    assert ai_task_service.naechste_faelligkeit(
        aufgabe, ab=_utc(2026, 8, 13, 10, 0)
    ) == _utc(2026, 8, 14, 6, 0)


def test_dieselbe_uhrzeit_verschiebt_sich_mit_der_winterzeit() -> None:
    """Am 25.10.2026 wird in Europa zurueckgestellt.

    Davor ist 08:00 Berlin gleich 06:00 UTC, danach 07:00 UTC. Wer die
    Umrechnung einmal beim Anlegen macht und den UTC-Wert festschreibt, hat ab
    dem Umstellungssonntag einen Auftrag, der eine Stunde falsch laeuft — und
    zwar ein halbes Jahr lang.
    """
    aufgabe = _aufgabe()
    vor_umstellung = ai_task_service.naechste_faelligkeit(
        aufgabe, ab=_utc(2026, 10, 24, 12, 0)
    )
    nach_umstellung = ai_task_service.naechste_faelligkeit(
        aufgabe, ab=_utc(2026, 10, 26, 12, 0)
    )

    assert vor_umstellung == _utc(2026, 10, 25, 7, 0)
    assert nach_umstellung == _utc(2026, 10, 27, 7, 0)
    # Und die Ortszeit bleibt beidesmal acht.
    from zoneinfo import ZoneInfo

    berlin = ZoneInfo("Europe/Berlin")
    assert ai_task_service.naechste_faelligkeit(
        aufgabe, ab=_utc(2026, 8, 13, 10, 0)
    ).astimezone(berlin).hour == 8
    assert nach_umstellung.astimezone(berlin).hour == 8


def test_wochentage_ueberspringen_die_uebrigen_tage() -> None:
    """Montag und Mittwoch — der 13.08.2026 ist ein Donnerstag."""
    aufgabe = _aufgabe(weekdays="1,3")
    assert ai_task_service.naechste_faelligkeit(
        aufgabe, ab=_utc(2026, 8, 13, 10, 0)
    ) == _utc(2026, 8, 17, 6, 0)


def test_der_termin_von_heute_zaehlt_noch_wenn_er_erst_kommt() -> None:
    """Wer morgens um sechs anlegt, wartet nicht bis morgen."""
    aufgabe = _aufgabe()
    assert ai_task_service.naechste_faelligkeit(
        aufgabe, ab=_utc(2026, 8, 13, 3, 0)
    ) == _utc(2026, 8, 13, 6, 0)


# ── Intervall ─────────────────────────────────────────────────────────────


def test_das_intervall_rechnet_ab_dem_uebergebenen_zeitpunkt() -> None:
    """`ab` ist ein Parameter und nicht "jetzt" — genau deswegen.

    Der Takt schaltet vom **faellig gewordenen** Termin weiter, nicht vom
    Augenblick der Verarbeitung. Sonst wandert ein Acht-Stunden-Auftrag mit
    jedem Lauf um die Verarbeitungsdauer nach hinten; nach hundert Laeufen
    laeuft er zu einer anderen Tageszeit als am Anfang. Genau dieser Drift ist
    beim Auto-Neustart des Panels schon einmal aufgeschlagen.
    """
    aufgabe = _aufgabe(plan_kind="interval", time_of_day=None, interval_hours=8)
    assert ai_task_service.naechste_faelligkeit(
        aufgabe, ab=_utc(2026, 8, 13, 10, 0)
    ) == _utc(2026, 8, 13, 18, 0)


# ── Einmalig ──────────────────────────────────────────────────────────────


def test_ein_einmaliger_termin_kommt_kein_zweites_mal() -> None:
    """Nach dem Feuern gibt es keinen naechsten — und das muss `None` sein.

    Ein Rueckfall auf "gleich nochmal" waere hier der schlimmste Fehler: der
    Takt saehe die Aufgabe im naechsten Durchlauf erneut als faellig und starte
    sechzigmal in der Stunde einen KI-Lauf.
    """
    aufgabe = _aufgabe(
        plan_kind="once", time_of_day=None, once_at=_utc(2026, 8, 20, 6, 0)
    )
    assert ai_task_service.naechste_faelligkeit(
        aufgabe, ab=_utc(2026, 8, 13, 10, 0)
    ) == _utc(2026, 8, 20, 6, 0)
    assert ai_task_service.naechste_faelligkeit(
        aufgabe, ab=_utc(2026, 8, 20, 6, 0)
    ) is None


def test_ein_zeitloser_wert_aus_der_datenbank_wirft_nicht() -> None:
    """SQLite gibt zeitzonenlose Werte zurueck, PostgreSQL zeitzonenbehaftete.

    Der Vergleich zwischen beiden wirft `TypeError` — hier in der
    Faelligkeitspruefung, also an der Stelle, an der ein Fehler bedeutet, dass
    gar keine Aufgabe mehr laeuft. Der Prueftstand ist SQLite, der Betrieb
    PostgreSQL; ohne diesen Test faellt es erst beim Betreiber auf.
    """
    aufgabe = _aufgabe(
        plan_kind="once", time_of_day=None, once_at=datetime(2026, 8, 20, 6, 0)
    )
    assert ai_task_service.naechste_faelligkeit(
        aufgabe, ab=_utc(2026, 8, 13, 10, 0)
    ) == _utc(2026, 8, 20, 6, 0)


# ── Die Zeitzone ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("wert", [None, "", "   ", "MEZ", "GMT+2", "Berlin", 42])
def test_ohne_brauchbare_zeitzone_wird_nichts_angelegt(wert: object) -> None:
    """Kein stiller Rueckfall auf UTC.

    Der Systemprompt sagt dem Modell, es solle die Zeitzone erfragen oder aus
    dem Gedaechtnis nehmen — aber ein Prompt ist keine Schranke. Faellt die
    Pruefung hier auf UTC zurueck, legt ein Modell, das nicht gefragt hat, die
    Aufgabe trotzdem an, und der Betreiber liest nirgends ein Wort ueber
    Zeitzonen. Er merkt es an der Uhrzeit seiner Mail.
    """
    with pytest.raises(AiActionValidationError):
        ai_task_service.zone_pruefen(wert)


def test_eine_iana_zone_wird_angenommen() -> None:
    assert ai_task_service.zone_pruefen("  Europe/Berlin  ") == "Europe/Berlin"
    assert ai_task_service.zone_pruefen("America/New_York") == "America/New_York"
    assert ai_task_service.zone_pruefen("UTC") == "UTC"


# ── Der Plan in Worten ────────────────────────────────────────────────────


def test_der_plantext_nennt_immer_die_zeitzone() -> None:
    """Er geht an drei Leser: Modell, Vorschlagskarte und E-Mail.

    "taeglich 08:00" ohne Zone ist genau die Angabe, bei der sich der Betreiber
    spaeter fragt, warum die Mail um neun kam. Deshalb steht die Zone in jeder
    zeitgebundenen Formulierung, und es gibt nur diese eine Funktion, die
    Plaene in Worte fasst.
    """
    assert ai_task_service.plan_text(_aufgabe()) == (
        "taeglich um 08:00 (Europe/Berlin)"
    )
    assert ai_task_service.plan_text(_aufgabe(weekdays="1,3,5")) == (
        "Mo, Mi, Fr um 08:00 (Europe/Berlin)"
    )
    assert ai_task_service.plan_text(
        _aufgabe(plan_kind="interval", time_of_day=None, interval_hours=8)
    ) == "alle 8 Stunden"
    assert "Europe/Berlin" in ai_task_service.plan_text(
        _aufgabe(plan_kind="once", time_of_day=None, once_at=_utc(2026, 8, 20, 6, 0))
    )


# ── Eingaben, die aus einem Modell kommen ─────────────────────────────────


@pytest.mark.parametrize(
    "wert", ["8:00", "08.00", "0800", "25:00", "08:60", "", None, 800]
)
def test_unbrauchbare_uhrzeiten_werden_abgewiesen(wert: object) -> None:
    with pytest.raises(AiActionValidationError):
        ai_task_service.uhrzeit_pruefen(wert)


def test_die_ganze_woche_und_keine_angabe_sind_dasselbe() -> None:
    """Zwei Schreibweisen fuer denselben Plan waeren zwei Zeilen, die
    verschieden aussehen und dasselbe tun."""
    assert ai_task_service.wochentage_pruefen([1, 2, 3, 4, 5, 6, 7]) is None
    assert ai_task_service.wochentage_pruefen([]) is None
    assert ai_task_service.wochentage_pruefen(None) is None
    # Entdoppelt und sortiert, damit derselbe Wunsch immer gleich dasteht.
    assert ai_task_service.wochentage_pruefen([5, 1, 1, 3]) == "1,3,5"


@pytest.mark.parametrize("wert", [[0], [8], [-1], ["mo"], [True], "1,2", 3])
def test_unbrauchbare_wochentage_werden_abgewiesen(wert: object) -> None:
    with pytest.raises(AiActionValidationError):
        ai_task_service.wochentage_pruefen(wert)
