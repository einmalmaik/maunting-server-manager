"""Wer spricht, leiht der KI seine Rechte — und keines mehr.

Der Betreiber hat es am 16.08.2026 so gesagt: *„auch im Realtime-Modus erbt die
KI nur das, was der User selber auch kann, sei es über seine Rolle oder direkt
der Benutzer für den Server."*

Das ist keine neue Anforderung, sondern die alte — aber der Umbau des
Sprachmodus hat den Weg dorthin ausgetauscht, und ein ausgetauschter Weg ist
genau der Ort, an dem eine Zusage lautlos verlorengeht. Vorher lief im
Sprachmodus ein **zweiter** Werkzeuglauf mit eigener Rechteprüfung; jetzt ist es
derselbe Lauf wie im getippten Chat. Das ist der sicherere Aufbau — und
niemandem sieht man von aussen an, ob er wirklich so verdrahtet ist.

Die Kette hat vier Glieder, und jedes prüft denselben Benutzer:

1. Der Lauf gehört dem Sprechenden (``lauf_beginnen_nebenher(user_id=…)``).
   Damit gilt für jedes Werkzeug dieselbe Prüfung wie beim Tippen.
2. Ein Vorschlag entsteht nur, wenn `_require_tool_permission` den Sprechenden
   für **diesen** Server durchlässt.
3. Eine gesprochene Zustimmung greift nur auf **eigene** Vorschläge
   (`owned_proposal`).
4. `confirm_proposal` und `execute_proposal` prüfen erneut, und zwar gegen
   denselben Benutzer — die Stimme ersetzt den Klick und keine Prüfung.

Geprüft wird hier das dritte und vierte Glied. Die ersten beiden liegen im
gemeinsamen Chatweg und sind dort geprüft; ihre Wiederholung hier wäre eine
zweite Zusage über fremden Code. Was **nur** hier existiert, ist die Naht: der
Sprachmodus als Auslöser einer Bestätigung.
"""

from __future__ import annotations

import pytest

from services import ai_voice_bridge


class _Attrappe(ai_voice_bridge.Sprachbruecke):
    """Eine Brücke ohne Browser, Mikrofon und Stimme.

    Geprüft wird eine einzelne Methode, und sie braucht nichts davon. Die
    Alternative wäre ein aufgebauter WebSocket samt Anbieterverbindung — für
    eine Frage, die sich an vier Zeilen entscheidet.
    """

    def __init__(self, benutzer_id: int) -> None:  # noqa: D107 - siehe Klassendoku
        super().__init__(
            browser=None,  # type: ignore[arg-type]
            user_id=benutzer_id,
            conversation_id="egal",
            chat_provider_id=1,
            stimm_kind="elevenlabs",
            stimm_adresse="wss://example.invalid/",
            stimm_schluessel="egal",
            http_client=None,  # type: ignore[arg-type]
        )


def test_eine_gesprochene_zustimmung_greift_nur_auf_eigene_vorschlaege(
    db, owner_user, monkeypatch
) -> None:
    """Fremde Vorschlagskennungen prallen ab, bevor irgendetwas passiert.

    Der Angriff, den das abwehrt, braucht keinen Einbruch: eine Kennung ist eine
    Zeichenkette, und wer eine fremde errät oder aus einem geteilten Protokoll
    abliest, hätte sie sonst per „Ja" ausgelöst — mit **seiner** Stimme, aber
    fremder Wirkung.

    `owned_proposal` gibt ``None`` zurück, und das darf nicht als „dann eben
    ohne Prüfung" durchgehen. Genau das prüft die zweite Zusicherung: es wurde
    weder bestätigt noch ausgeführt.
    """
    from services import ai_proposal_service

    gerufen: list[str] = []

    monkeypatch.setattr(
        ai_proposal_service, "owned_proposal", lambda db, kennung, user: None
    )
    monkeypatch.setattr(
        ai_proposal_service,
        "confirm_proposal",
        lambda *a, **k: gerufen.append("confirm"),
    )
    monkeypatch.setattr(
        ai_proposal_service,
        "execute_proposal",
        lambda *a, **k: gerufen.append("execute"),
    )

    bruecke = _Attrappe(owner_user.id)
    erfolg, fortgesetzt = bruecke._ausfuehren("fremde-kennung")

    assert erfolg is False
    assert fortgesetzt is None
    assert gerufen == []


def test_bestaetigen_und_ausfuehren_laufen_auf_den_sprechenden(
    db, owner_user, monkeypatch
) -> None:
    """Der Benutzer der Prüfung ist der, dessen Mikrofon offen ist.

    Die naheliegende Abkürzung wäre ein Dienstbenutzer: die Brücke hat keine
    Anfrage und keinen `Depends`, sie holt sich ihre Sitzung selbst, und ein
    „Systembenutzer" wäre an dieser Stelle bequem. Er wäre auch eine
    Rechteerweiterung — die KI könnte dann, was **er** darf, statt was der
    Sprechende darf.

    Deshalb wird hier nicht geprüft, *dass* geprüft wird (das tun
    `confirm_proposal` und `execute_proposal` selbst und sind dort geprüft),
    sondern **wogegen**: gegen den Benutzer der Sprachsitzung, in beiden
    Schritten derselbe.
    """
    from services import ai_proposal_service

    gesehen: dict[str, object] = {}

    class _Vorschlag:
        run_id = None

    monkeypatch.setattr(
        ai_proposal_service, "owned_proposal", lambda db, kennung, user: _Vorschlag()
    )

    def _confirm(db, *, proposal_id, user):
        gesehen["confirm"] = user.id
        return _Vorschlag(), "einmal-token"

    def _execute(db, *, proposal_id, user, confirmation_token):
        gesehen["execute"] = user.id
        gesehen["token"] = confirmation_token
        return _Vorschlag(), {}

    monkeypatch.setattr(ai_proposal_service, "confirm_proposal", _confirm)
    monkeypatch.setattr(ai_proposal_service, "execute_proposal", _execute)

    bruecke = _Attrappe(owner_user.id)
    erfolg, fortgesetzt = bruecke._ausfuehren("eigene-kennung")

    assert erfolg is True
    # Kein Lauf am Vorschlag — also auch nichts, dem sich die Bruecke
    # anschliessend anhaengen muesste.
    assert fortgesetzt is None
    assert gesehen["confirm"] == owner_user.id
    assert gesehen["execute"] == owner_user.id
    # Und der Token aus dem ersten Schritt geht in den zweiten. Ein neu
    # erfundener wäre die Umgehung der Einmal-Entwertung.
    assert gesehen["token"] == "einmal-token"


def test_ein_abgewiesener_vorschlag_reisst_die_sitzung_nicht_ab(
    db, owner_user, monkeypatch
) -> None:
    """Entzogene Rechte enden in einem „nein" und nicht in einem Abbruch.

    Der Fall aus dem Betrieb: zwischen dem Anlegen des Vorschlags und dem
    gesprochenen „Ja" liegen Sekunden, in denen dem Benutzer der Server entzogen
    worden sein kann. `confirm_proposal` wirft dann
    ``AI_ACTION_ACCESS_REVOKED`` — richtig so. Die Sprachsitzung darf daran
    nicht sterben: der Mensch sitzt davor und soll hören, dass es nicht ging.
    """
    from services import ai_action_errors, ai_proposal_service

    class _Vorschlag:
        run_id = None

    monkeypatch.setattr(
        ai_proposal_service, "owned_proposal", lambda db, kennung, user: _Vorschlag()
    )

    def _confirm(db, *, proposal_id, user):
        raise ai_action_errors.AiActionStateError("AI_ACTION_ACCESS_REVOKED")

    monkeypatch.setattr(ai_proposal_service, "confirm_proposal", _confirm)

    bruecke = _Attrappe(owner_user.id)
    erfolg, fortgesetzt = bruecke._ausfuehren("kennung")

    assert erfolg is False
    assert fortgesetzt is None


@pytest.mark.parametrize(
    "gesagt, erwartet",
    [
        ("Ja", True),
        ("ja bitte", True),
        ("Mach das.", True),
        ("Okay!", True),
        # Und jetzt die, die keine Zustimmung sind. Sie sind der eigentliche
        # Grund für diese Prüfung.
        ("Ja, aber schau vorher nochmal in die Logs", False),
        ("Ja genau das meinte ich vorhin mit dem anderen Server", False),
        ("Jaaa also ich weiss nicht", False),
        ("Nein", False),
        ("Warte", False),
        ("", False),
        ("Was macht der Server gerade?", False),
    ],
)
def test_nur_eine_blanke_zustimmung_zaehlt_als_zustimmung(
    gesagt: str, erwartet: bool
) -> None:
    """Eine Äusserung gilt nur dann als „Ja", wenn sie **nichts anderes** ist.

    Das ist die heikelste Zeile des ganzen Sprachmodus, denn hinter ihr steht
    `confirm_proposal` — und der Betreiber hat ausdrücklich verlangt, dass ein
    gesprochenes Ja auch einen Server löschen darf. Ein „Ja, aber schau vorher
    nochmal nach" als Zustimmung zu lesen hiesse dann, genau das Gegenteil des
    Gesagten zu tun.

    Geprüft wird deshalb auf **Gleichheit** gegen eine geschlossene Menge und
    ausdrücklich nicht auf ein enthaltenes Wort. Satzzeichen, Grossschreibung
    und Umlaut-Umschrift fallen vorher weg: ein Transkript schreibt „Ja!",
    „ja" und „Ja." für dieselbe Silbe.
    """
    assert ai_voice_bridge.ist_zustimmung(gesagt) is erwartet


@pytest.mark.parametrize(
    "gesagt, erwartet",
    [
        ("Nein", True),
        ("nee", True),
        ("Lass es.", True),
        ("Stopp!", True),
        ("Nein, aber starte stattdessen den anderen", False),
        ("Ja", False),
    ],
)
def test_nur_eine_blanke_ablehnung_zaehlt_als_ablehnung(
    gesagt: str, erwartet: bool
) -> None:
    """Dieselbe Enge in der Gegenrichtung — mit anderer Folge.

    Ein übersehenes Nein ist harmloser als ein erfundenes Ja: der Vorschlag
    bleibt dann stehen, wie eine Karte, die niemand anklickt. Trotzdem gilt
    dieselbe Regel, und zwar aus einem zweiten Grund: „Nein, aber starte
    stattdessen den anderen" ist ein **Auftrag**, und als blosse Ablehnung
    gelesen ginge seine zweite Hälfte verloren.
    """
    assert ai_voice_bridge.ist_ablehnung(gesagt) is erwartet
