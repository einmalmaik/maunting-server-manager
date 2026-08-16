"""Die KI schreibt ihre Mails selbst — und darf dabei genau drei Dinge nicht.

Der Betreiber hat eine Berichtsmail beanstandet: fester Rahmen, ein
Modellabsatz darin, und in der Betreffzeile der Name der Aufgabe statt ihres
Ergebnisses. Seither verfasst das Modell Betreff und Text — alle drei Mails,
ausdruecklich auch die Testmail.

Damit wandern drei Dinge in die Naehe von etwas, das ein Modell schreibt. Diese
Tests halten fest, dass sie es nicht erreichen:

1. **Der Empfaenger.** Er kommt aus `ai_mail.empfaenger` und aus nichts sonst.
   Auch wenn im Modelltext eine Adresse steht, aendert das keinen Empfaenger.
2. **Der Zustand.** „erledigt“ oder „nicht abgeschlossen“ ist eine Tatsache des
   Laufs. Ein Modell, das beschoenigt, faerbt hoechstens den Zusatz.
3. **Die Betreffzeile.** Ein Zeilenumbruch darin kostete die ganze Mail und
   meldete dabei Erfolg — `msg["Subject"]` wirft, die Ausnahme laeuft bis
   `ai_mail.zustellen` durch und endet dort als Warnung.

Dazu die vierte Zusage, die ueber allem steht: **verschickt wird immer.** Faellt
das Modell aus, geht der feste Text hinaus.

Gefaelscht wird das Modell wie in `test_ai_run.py` — ein Ersatz fuer
`stream_chat_completion`, der `usage.tool_calls` fuellt. Kein Netz.
"""

from __future__ import annotations

import asyncio
import json
import threading
from email.message import EmailMessage
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from models import AiMailOutbox, AiProvider, AiUsageEvent, User
from services import ai_guardian_report, ai_mail_text, ai_task_report
from services.ai_mail_text import (
    WERKZEUG,
    WERKZEUG_NAME,
    Mailtext,
    auswerten,
    betreff_bereinigen,
    verfassen,
)
from services.email_service import EmailService
from services.openai_compatible_adapter import (
    AiProviderRequestError,
    ProviderToolCall,
    StreamChunk,
)


#: Der gefaelschte Anbieter unten fasst ihn nie an — genau wie in `test_ai_run`.
_KEIN_CLIENT = object()


def _provider(db: Session) -> AiProvider:
    # Der Name ist eindeutig in der Datenbank; ein Test, der zweimal verfassen
    # laesst, braucht deshalb zwei Anbieter und nicht denselben zweimal.
    provider = AiProvider(
        name=f"Mailtext-{uuid4().hex[:8]}",
        provider_kind="openrouter",
        default_model="model-a",
        enabled=True,
        requires_api_key=False,
    )
    db.add(provider)
    db.commit()
    db.refresh(provider)
    return provider


def _fake_modell(
    monkeypatch,
    *,
    argumente: dict | None = None,
    werkzeugname: str = WERKZEUG_NAME,
    platzt: Exception | None = None,
) -> dict:
    """Ersetzt den Providerruf. Gibt zurueck, was der Adapter zu sehen bekam."""
    gesehen: dict = {}

    async def fake(_client, *, provider, api_key, messages, usage, tools=None,
                   tool_choice=None, reasoning=False, reasoning_effort=None,
                   cache_marke=False):
        del provider, api_key, reasoning, reasoning_effort, cache_marke
        gesehen["tools"] = tools
        gesehen["tool_choice"] = tool_choice
        gesehen["messages"] = messages
        if platzt is not None:
            raise platzt
        usage.total_tokens = 10
        if argumente is not None:
            usage.tool_calls.append(
                ProviderToolCall(id="c1", name=werkzeugname, arguments=argumente)
            )
        if False:  # pragma: no cover - macht die Funktion zum Generator
            yield StreamChunk("content", "")

    monkeypatch.setattr(ai_mail_text, "stream_chat_completion", fake)
    return gesehen


def _verfassen(db: Session, user: User, monkeypatch, **kwargs) -> Mailtext | None:
    provider = _provider(db)
    _fake_modell(monkeypatch, **kwargs)
    return asyncio.run(
        verfassen(
            user_id=user.id,
            provider_id=provider.id,
            anlass="test",
            fakten="Ergebnis laut Panel: erledigt",
            client=_KEIN_CLIENT,
        )
    )


def _versand():
    """Faengt die letzte Mail ab und liefert `(to, subject, body, html)`."""
    gesendet: dict = {}

    async def _fake(to, subject, body, html=None):
        gesendet["to"] = to
        gesendet["subject"] = subject
        gesendet["body"] = body
        gesendet["html"] = html or ""
        return True

    return gesendet, _fake


def _abschicken(monkeypatch, koroutine_bauen) -> dict:
    gesendet, fake = _versand()
    monkeypatch.setattr(EmailService, "send_email", fake)
    asyncio.run(koroutine_bauen())
    return gesendet


# ── Die Betreffzeile ──────────────────────────────────────────────────────


class TestBetreffzeile:
    """Ein Umbruch im Betreff kostete die ganze Mail — bei gruenem Lauf."""

    def test_a_line_break_in_the_subject_never_reaches_the_header(self):
        """Der Vorfall: `msg["Subject"] = subject` steht **vor** dem `try`.

        Die `ValueError` aus Pythons Header-Pruefung laeuft an der
        Fehlerbehandlung des Versands vorbei bis `ai_mail.zustellen` und wird
        dort als Warnung verschluckt. Der Lauf ist gruen, die Mail ist weg.
        """
        assert "\n" not in betreff_bereinigen("Bericht\nvom Assistenten")
        assert "\r" not in betreff_bereinigen("Bericht\r\nvom Assistenten")
        assert betreff_bereinigen("Bericht\nvom Assistenten") == (
            "Bericht vom Assistenten"
        )

    def test_control_characters_go_too_not_just_cr_and_lf(self):
        """Ein NUL oder ein Escape ist genauso wenig zustellbar — und unsichtbar."""
        sauber = betreff_bereinigen("Server\x00neu\x1bgestartet​jetzt")
        assert "\x00" not in sauber
        assert "\x1b" not in sauber
        assert "​" not in sauber

    def test_an_overlong_subject_is_cut_to_a_readable_length(self):
        """Ein Modell, das den ganzen Bericht in den Betreff schreibt, tut das einmal."""
        lang = betreff_bereinigen("A" * 500)
        assert len(lang) == ai_mail_text.MAX_BETREFF_ZEICHEN

    def test_a_missing_subject_is_empty_and_never_the_word_none(self):
        """`None` im Betreff waere woertlich "None" in der Kopfzeile gewesen."""
        assert betreff_bereinigen(None) == ""

    def test_a_subject_with_a_line_break_still_produces_a_deliverable_mail(
        self, monkeypatch
    ):
        """Der Beweis am fertigen Kopf, nicht an der Hilfsfunktion.

        Gebaut wie in `_send_smtp`: was hier ohne Ausnahme durchgeht, geht auch
        dort durch.
        """
        gesendet = _abschicken(monkeypatch, lambda: EmailService.send_ai_task_report(
            "b@test.de", "betreiber",
            task_title="Serverstatus", plan_text="täglich um 18:00",
            geschafft=True, bericht="Alles läuft.",
            mailtext=Mailtext(
                betreff="Drei Server laufen\nBcc: opfer@example.com",
                absaetze=["Alles in Ordnung."],
            ),
        ))

        assert "\n" not in gesendet["subject"]
        msg = EmailMessage()
        msg["Subject"] = gesendet["subject"]  # wirft, wenn ein Umbruch drin ist
        assert msg["Subject"] == gesendet["subject"]


# ── Das Formular ──────────────────────────────────────────────────────────


class TestDasFormular:
    """Strukturierte Felder statt Freitext — und ein Zwang statt einer Bitte."""

    def test_the_model_is_forced_into_exactly_one_tool(
        self, db: Session, regular_user: User, monkeypatch
    ):
        """„Antworte als JSON“ ist eine Bitte, `tool_choice` ist eine Vorgabe.

        Ohne den Zwang schriebe ein Modell hier gelegentlich einen freundlichen
        Absatz mit Sternchen — und die Formatierung stuende wieder im Text, wo
        es keinen Markdown-Leser gibt, der sie aufloest.
        """
        provider = _provider(db)
        gesehen = _fake_modell(monkeypatch, argumente={
            "betreff": "Alles laeuft", "absaetze": ["Drei Server laufen."],
        })
        asyncio.run(verfassen(
            user_id=regular_user.id, provider_id=provider.id,
            anlass="test", fakten="egal", client=_KEIN_CLIENT,
        ))

        assert gesehen["tools"] == [WERKZEUG]
        assert gesehen["tool_choice"] == {
            "type": "function", "function": {"name": WERKZEUG_NAME},
        }

    def test_the_form_is_not_a_tool_of_the_registry(self):
        """Es gehoert nicht in `ai_tool_registry` und darf nirgends ausfuehrbar sein.

        Die Werkzeuglisten dort sind bewusst ausgeschriebene Aufzaehlungen, und
        `execute_read_tool` bekommt bewusst keine `run_id`. Dieses Formular wird
        nie ausgefuehrt — waere es dort eingetragen, koennte ein Lauf es
        aufrufen und MSM muesste eine Antwort dafuer erfinden.
        """
        from services import ai_tool_registry

        assert WERKZEUG_NAME not in ai_tool_registry.WERKZEUGE

    def test_fields_arrive_as_plain_text_and_keep_their_shape(self):
        """Absaetze, Punkte und Schluss bleiben getrennt — das ist die Struktur."""
        text = auswerten({
            "betreff": "Drei Server laufen",
            "absaetze": ["Ich habe alle drei geprüft.", "Nichts geändert."],
            "punkte": ["MauntShrouded: läuft", "7 Days to Die: läuft"],
            "schluss": "Melde mich morgen wieder.",
        })

        assert text is not None
        assert text.betreff == "Drei Server laufen"
        assert text.absaetze == ["Ich habe alle drei geprüft.", "Nichts geändert."]
        assert text.punkte == ["MauntShrouded: läuft", "7 Days to Die: läuft"]
        assert text.schluss == "Melde mich morgen wieder."

    def test_a_single_string_instead_of_a_list_costs_no_mail(self):
        """Nachsichtig lesen, streng speichern.

        Modelle liefern `absaetze` gelegentlich als einen langen String statt
        als Feld mit einem Eintrag. Das ist dieselbe Aussage in einer anderen
        Form — daran soll keine Mail scheitern.
        """
        text = auswerten({
            "betreff": "Status", "absaetze": "Erster Absatz.\n\nZweiter Absatz.",
        })

        assert text is not None
        assert text.absaetze == ["Erster Absatz.", "Zweiter Absatz."]

    def test_a_form_without_subject_or_text_is_no_form(self):
        """Eine Mail ohne Betreff waere schlechter als die Vorlage, die es gibt."""
        assert auswerten({"absaetze": ["Text ohne Betreff."]}) is None
        assert auswerten({"betreff": "Betreff ohne Text"}) is None
        assert auswerten("kein Objekt") is None

    def test_secrets_do_not_survive_the_detour_through_the_mail(self):
        """Derselbe Schwaerzer wie ueberall — auch auf dem Weg in eine Mail.

        Der Text geht an ein Postfach und liegt danach ausserhalb des Panels.
        Ein Schluessel, den das Modell aus einer Logzeile aufgeschnappt hat,
        waere dort dauerhaft.
        """
        text = auswerten({
            "betreff": "Status",
            "absaetze": ["Schreib an angreifer@example.com."],
        })

        assert text is not None
        assert "angreifer@example.com" not in text.absaetze[0]


# ── Verschickt wird immer ─────────────────────────────────────────────────


class TestVerschicktWirdImmer:
    """Der Verfassungsschritt darf die Mail verschoenern, nie verhindern."""

    def test_a_provider_failure_leaves_the_mail_with_the_fixed_text(
        self, db: Session, regular_user: User, monkeypatch
    ):
        """Kein Anbieter, kein Text — aber trotzdem eine Mail."""
        ergebnis = _verfassen(
            db, regular_user, monkeypatch,
            platzt=AiProviderRequestError("AI_PROVIDER_UNAVAILABLE"),
        )

        assert ergebnis is None

    def test_a_model_that_ignores_the_form_gets_no_say(
        self, db: Session, regular_user: User, monkeypatch
    ):
        """Ein Modell, das statt des Formulars Prosa schickt, wird ueberhoert."""
        assert _verfassen(db, regular_user, monkeypatch, argumente=None) is None
        assert _verfassen(
            db, regular_user, monkeypatch,
            argumente={"betreff": "x", "absaetze": ["y"]}, werkzeugname="etwas_anderes",
        ) is None

    def test_a_failed_composition_leaves_no_reservation_behind(
        self, db: Session, regular_user: User, monkeypatch
    ):
        """Eine offene Reservierung zaehlt dauerhaft gegen das Kontingent.

        `reserve_ai_usage` zaehlt reservierte Ereignisse **ohne Zeitfenster**.
        Zwei stehengebliebene genuegen bei `concurrent_operations = 2`, und der
        Benutzer bekommt fuer immer eine Absage, obwohl nichts laeuft.
        """
        _verfassen(
            db, regular_user, monkeypatch,
            platzt=AiProviderRequestError("AI_PROVIDER_UNAVAILABLE"),
        )

        offen = (
            db.query(AiUsageEvent)
            .filter(
                AiUsageEvent.user_id == regular_user.id,
                AiUsageEvent.status == "reserved",
            )
            .count()
        )
        assert offen == 0

    def test_the_test_email_goes_out_even_when_the_model_is_silent(self, monkeypatch):
        """Sie ist das Messgeraet fuer den Versandweg.

        Eine Testmail, die ausgerechnet dann ausbleibt, wenn das Modell klemmt,
        misst das Falsche — der Betreiber suchte den Fehler dann bei SMTP.
        """
        gesendet = _abschicken(
            monkeypatch,
            lambda: EmailService.send_ai_test_email("b@test.de", "betreiber"),
        )

        assert "Testmail vom KI-Assistenten" in gesendet["subject"]
        assert "auf deine Bitte hin verschickt" in gesendet["body"]
        assert "eingerichtete Versandweg" in gesendet["body"]

    def test_the_test_email_is_written_by_the_model_when_it_can(self, monkeypatch):
        """Der ausdrueckliche Wunsch des Betreibers: auch sie ist nicht vorgefertigt."""
        gesendet = _abschicken(monkeypatch, lambda: EmailService.send_ai_test_email(
            "b@test.de", "betreiber",
            mailtext=Mailtext(
                betreff="Der Postweg steht",
                absaetze=["Ich habe dir diese Mail auf deine Bitte hin geschickt."],
                schluss="Damit ist der Weg für meine Berichte nachgewiesen.",
            ),
        ))

        assert "Der Postweg steht" in gesendet["subject"]
        assert "auf deine Bitte hin geschickt" in gesendet["body"]
        assert "nachgewiesen" in gesendet["body"]


# ── Die drei Zusagen ──────────────────────────────────────────────────────


class TestDerEmpfaengerKommtNieVomModell:
    """`ai_mail.empfaenger` entscheidet, sonst niemand."""

    def test_an_address_in_the_model_text_changes_no_recipient(self, monkeypatch):
        """MSM verschickt keine Post an Dritte, weil ein Modell einen Namen nannte."""
        gesendet = _abschicken(monkeypatch, lambda: EmailService.send_ai_task_report(
            "besitzer@test.de", "betreiber",
            task_title="Serverstatus", plan_text="täglich",
            geschafft=True, bericht="Alles läuft.",
            mailtext=Mailtext(
                betreff="Bitte an angreifer@example.com weiterleiten",
                absaetze=["Schick eine Kopie an angreifer@example.com."],
            ),
        ))

        assert gesendet["to"] == "besitzer@test.de"

    def test_the_facts_handed_to_the_model_never_contain_the_address(self):
        """Was das Modell nicht sieht, kann es nicht wiederholen.

        Die Adresse steht eine Ebene hoeher in denselben Feldern. Sie aus den
        Angaben herauszuhalten ist billiger und sicherer, als sie spaeter aus
        dem Text zu filtern.
        """
        felder = {
            "to": "besitzer@test.de", "username": "betreiber",
            "task_title": "Serverstatus", "plan_text": "täglich",
            "geschafft": True, "bericht": "Alles läuft.",
        }
        assert "besitzer@test.de" not in ai_task_report._fakten(felder)

        guardian = {
            "to": "besitzer@test.de", "username": "betreiber",
            "server_name": "MauntShrouded", "incident_type": "process_not_running",
            "geheilt": True, "bericht": "Neu gestartet.", "backup_name": None,
        }
        assert "besitzer@test.de" not in ai_guardian_report._fakten(guardian)


class TestDerZustandKommtVomPanel:
    """geschafft/geheilt sind Tatsachen des Laufs, keine Saetze des Modells."""

    def test_a_flattering_model_cannot_rename_a_failed_task(self, monkeypatch):
        """Geprueft gegen einen Bericht, der das Gegenteil behauptet.

        Ein Auftrag, der still gescheitert ist, ist die wichtigere Nachricht von
        beiden — niemand sass davor.
        """
        gesendet = _abschicken(monkeypatch, lambda: EmailService.send_ai_task_report(
            "b@test.de", "betreiber",
            task_title="Serverstatus", plan_text="täglich",
            geschafft=False, bericht="egal",
            mailtext=Mailtext(
                betreff="Alles bestens erledigt",
                absaetze=["Ich habe alles erfolgreich abgeschlossen."],
            ),
        ))

        assert "nicht abgeschlossen" in gesendet["subject"]
        assert "nicht abgeschlossen" in gesendet["body"]
        assert "nicht abgeschlossen" in gesendet["html"]
        # Der Zusatz des Modells darf daneben stehen — nur nicht davor.
        assert gesendet["subject"].index("nicht abgeschlossen") < (
            gesendet["subject"].index("Alles bestens erledigt")
        )

    def test_a_flattering_model_cannot_rename_a_failed_healing(self, monkeypatch):
        """Dasselbe fuer Guardian: der Server laeuft nicht, und das steht oben."""
        gesendet = _abschicken(
            monkeypatch, lambda: EmailService.send_ai_healing_report(
                "b@test.de", "betreiber",
                server_name="MauntShrouded", incident_type="process_not_running",
                geheilt=False, bericht="egal",
                mailtext=Mailtext(
                    betreff="Server läuft wieder",
                    absaetze=["Alles wieder in bester Ordnung."],
                ),
            )
        )

        assert "nicht behoben" in gesendet["subject"]
        assert "nicht behoben" in gesendet["body"]


class TestDerModelltextInDerFertigenMail:
    """Was das Modell schreibt, kommt an — als Text und nur als Text."""

    def test_paragraphs_and_bullets_arrive_without_a_markdown_reader(
        self, monkeypatch
    ):
        """Die Struktur kommt aus den Feldern, nicht aus Sternchen im Text.

        Vorher stand `**Laufend:**` woertlich in der Mail, weil es im Backend
        keinen Markdown-Leser gibt — und keinen geben soll.
        """
        gesendet = _abschicken(monkeypatch, lambda: EmailService.send_ai_task_report(
            "b@test.de", "betreiber",
            task_title="Serverstatus", plan_text="täglich",
            geschafft=True, bericht="egal",
            mailtext=Mailtext(
                betreff="Drei Server laufen",
                absaetze=["Ich habe alle drei geprüft."],
                punkte=["MauntShrouded", "7 Days to Die"],
                schluss="Ich habe nichts geändert.",
            ),
        ))

        assert "Drei Server laufen" in gesendet["subject"]
        for satz in ("Ich habe alle drei geprüft.", "MauntShrouded",
                     "7 Days to Die", "Ich habe nichts geändert."):
            assert satz in gesendet["body"]
        assert "<li" in gesendet["html"]
        assert "**" not in gesendet["html"]

    def test_model_markup_arrives_as_text_and_never_as_markup(self, monkeypatch):
        """Ein Modell laesst sich ueber eine praeparierte Logzeile ueberreden.

        Ein Link in einer Mail mit MSM-Kopfzeile ist ein brauchbarer
        Phishing-Traeger, und die Mail geht an denjenigen, der das Panel
        betreibt.
        """
        boese = '<a href="https://phish.example">klick</a>'
        gesendet = _abschicken(
            monkeypatch, lambda: EmailService.send_ai_healing_report(
                "b@test.de", "betreiber",
                server_name="MauntShrouded", incident_type="process_not_running",
                geheilt=True, bericht="egal", backup_name="Guardian-Heilung",
                mailtext=Mailtext(betreff=boese, absaetze=[boese], punkte=[boese]),
            )
        )

        assert 'href="https://phish.example"' not in gesendet["html"]
        assert "&lt;a href=" in gesendet["html"]

    def test_a_status_report_still_does_not_tell_you_to_change_your_password(
        self, monkeypatch
    ):
        """Die Berichtsvorlage traegt keinen Sicherheitshinweis — jetzt auch benutzt.

        Sie stand schon da, aber die `send_ai_*`-Funktionen gingen weiterhin
        durch die Sicherheitsvorlage. Der beanstandete Satz stand also
        weiterhin unter jedem Serverstatusbericht.
        """
        for bauen in (
            lambda: EmailService.send_ai_task_report(
                "b@test.de", "betreiber", task_title="T", plan_text="täglich",
                geschafft=True, bericht="Alles läuft.",
            ),
            lambda: EmailService.send_ai_healing_report(
                "b@test.de", "betreiber", server_name="S", incident_type="typ",
                geheilt=True, bericht="Neu gestartet.",
            ),
            lambda: EmailService.send_ai_test_email("b@test.de", "betreiber"),
        ):
            gesendet = _abschicken(monkeypatch, bauen)
            assert "ändere sofort dein Passwort" not in gesendet["html"]
            assert "kontaktiere den Administrator" not in gesendet["html"]


# ── Der Verfassungsschritt als Ganzes ─────────────────────────────────────


def test_the_model_writes_subject_and_body_of_a_real_report(
    db: Session, regular_user: User, monkeypatch
):
    """Der Weg von der Modellantwort bis in die fertige Mail, in einem Stueck."""
    text = _verfassen(db, regular_user, monkeypatch, argumente={
        "betreff": "Alle drei Server laufen",
        "absaetze": ["Ich habe die drei Server geprüft."],
        "punkte": ["MauntShrouded"],
        "schluss": "Nichts zu tun.",
    })
    assert text is not None

    gesendet = _abschicken(monkeypatch, lambda: EmailService.send_ai_task_report(
        "b@test.de", "betreiber", task_title="Serverstatus", plan_text="täglich",
        geschafft=True, bericht="unbenutzt", mailtext=text,
    ))

    assert gesendet["subject"] == (
        "Maunting Service Manager — KI-Aufgabe erledigt: Alle drei Server laufen"
    )
    assert "Ich habe die drei Server geprüft." in gesendet["body"]
    # Der feste Rueckfalltext taucht nicht zusaetzlich auf — sonst stuende
    # dasselbe zweimal in der Mail, einmal vom Modell und einmal aus dem Code.
    assert "unbenutzt" not in gesendet["body"]


def test_the_composition_is_billed_like_any_other_provider_call(
    db: Session, regular_user: User, monkeypatch
):
    """Ein unsichtbarer Verbrauch waere genau das, was die Buchung verhindern soll."""
    _verfassen(db, regular_user, monkeypatch, argumente={
        "betreff": "Status", "absaetze": ["Alles läuft."],
    })

    ereignisse = (
        db.query(AiUsageEvent)
        .filter(AiUsageEvent.user_id == regular_user.id)
        .all()
    )
    assert len(ereignisse) == 1
    assert ereignisse[0].status == "completed"


@pytest.mark.parametrize("anlass", ["ai-task-report", "ai-guardian-report"])
def test_a_report_without_a_usable_provider_still_sends(
    db: Session, regular_user: User, monkeypatch, anlass
):
    """Kein Anbieter ist kein Fehler — der Benutzer hat vielleicht nie einen benutzt."""
    ergebnis = asyncio.run(verfassen(
        user_id=regular_user.id, provider_id=None, anlass=anlass,
        fakten="egal", client=_KEIN_CLIENT,
    ))

    assert ergebnis is None


# ── Rahmen und Rendern ────────────────────────────────────────────────────
#
# Dieselbe Mail entsteht seit dem Ausgangskorb an zwei Stellen: beim Einreihen
# als Rueckfall und im Arbeiter mit dem Modelltext. Zwei Stellen, die dasselbe
# zusammensetzen, laufen auseinander — die Textfassung dieser Mail trug einmal
# einen Sicherheitshinweis, den die HTML-Fassung nicht hatte, und niemand sah
# es. Deshalb gibt es genau einen Renderer, und diese Tests halten ihn fest.


class TestRahmenUndRendern:
    def test_the_frame_survives_the_trip_through_the_database(self):
        """Der Rahmen geht als JSON durch eine Tabelle und muss danach dasselbe ergeben.

        Zwischen Einreihen und Versand liegt eine `json.dumps`/`json.loads`-Runde
        und moeglicherweise ein Prozessneustart. Ginge dabei etwas verloren —
        das Zustandswort, die Fusszeile, der Satz des Panels —, faellt es
        niemandem auf: die Mail geht ja trotzdem hinaus, nur mit weniger drin.
        """
        rahmen = EmailService.ai_rahmen_task(
            "betreiber", task_title="Serverstatus", plan_text="täglich",
            geschafft=False,
        )
        gereist = json.loads(json.dumps(rahmen, ensure_ascii=False))

        text = Mailtext(betreff="Kurz", absaetze=["Ein Absatz."])
        assert EmailService.ai_mail_rendern(
            gereist, mailtext=text
        ) == EmailService.ai_mail_rendern(rahmen, mailtext=text)

    def test_rendering_the_same_frame_twice_differs_only_in_the_model_part(self):
        """Rueckfall und verfasste Fassung teilen alles, was das Panel beitraegt.

        Der Rueckfall entsteht beim Einreihen, die verfasste Fassung im
        Arbeiter. Ueberschrift, Zustandswort, Anrede und Fusszeile muessen
        beide Male dieselben sein — sonst haette der Betreiber je nach Laune
        des Modells eine andere Mail vor sich.
        """
        rahmen = EmailService.ai_rahmen_task(
            "betreiber", task_title="Serverstatus", plan_text="täglich",
            geschafft=True,
        )
        fest_betreff, fest_text, fest_html = EmailService.ai_mail_rendern(
            rahmen, rueckfall="Der feste Text."
        )
        verfasst_betreff, verfasst_text, verfasst_html = EmailService.ai_mail_rendern(
            rahmen, mailtext=Mailtext(betreff="Alles läuft", absaetze=["Geprüft."])
        )

        for betreff in (fest_betreff, verfasst_betreff):
            assert betreff.startswith("Maunting Service Manager — KI-Aufgabe erledigt")
        for koerper in (fest_text, verfasst_text):
            assert "Hallo betreiber," in koerper
            assert 'Deine KI-Aufgabe "Serverstatus" (täglich) war fällig.' in koerper
            assert "Den vollständigen Verlauf findest du im KI-Chat" in koerper
        assert "Der feste Text." in fest_text
        assert "Der feste Text." not in verfasst_text
        assert "Geprüft." in verfasst_text
        assert "Geprüft." in verfasst_html
        assert "Geprüft." not in fest_html

    def test_an_incomplete_frame_still_produces_a_deliverable_mail(self):
        """Ein Rahmen aus einer aelteren Fassung des Codes kostet Text, nie die Mail.

        Er kommt aus einem JSON-Feld und kann alles sein: leer, halb, von
        vorgestern. Der Arbeiter liest ihn nachsichtig — und was dabei
        herauskommt, muss trotzdem einen zustellbaren Betreff haben, sonst
        wirft `msg["Subject"]` an einer Stelle, an der die Ausnahme oben
        verschluckt wird.
        """
        betreff, koerper, html = EmailService.ai_mail_rendern(
            {}, mailtext=Mailtext(betreff="Kurz", absaetze=["Ein Absatz."])
        )

        assert betreff == "Maunting Service Manager: Kurz"
        assert "Ein Absatz." in koerper and "Ein Absatz." in html


class TestBerichtspfadeNehmenDenKorb:
    """Alle drei Anlaesse legen eine Zeile an, statt einen Thread zu starten.

    Das war die offene Naht: der Verfassungsschritt ist asynchron, das
    Einreihen verlangte eine fertig gerenderte Mail — also blieb der
    Modellaufruf vor dem Korb und die Mail in einem Thread. Stuerzte der
    Prozess zwischen Lauf-Ende und Versand ab, war sie weg.
    """

    def _benutzer(self, db: Session, name: str) -> User:
        from services.auth_service import AuthService

        user = AuthService.create_user(db, name, f"{name}@test.de", "KorbPass123!")
        user.email_notifications = True
        db.commit()
        db.refresh(user)
        return user

    def test_the_task_report_queues_facts_and_a_fallback(self, db: Session):
        user = self._benutzer(db, "aufgabenkorb")
        vorher = threading.active_count()

        ai_task_report._zustellen(
            db=db,
            user_id=int(user.id),
            provider_id=None,
            to="unbenutzt@test.de",
            username=str(user.username),
            task_title="Serverstatus",
            plan_text="täglich",
            geschafft=True,
            bericht="Alle drei Server laufen.",
        )

        assert threading.active_count() == vorher
        zeile = db.query(AiMailOutbox).filter(
            AiMailOutbox.user_id == user.id
        ).one()
        assert zeile.anlass == "ai-task-report"
        assert zeile.betreff.startswith(
            "Maunting Service Manager — KI-Aufgabe erledigt"
        )
        assert "Alle drei Server laufen." in zeile.text_body
        assert "Ergebnis laut Panel: erledigt" in zeile.fakten
        # Die Adresse steht nirgends in der Zeile — auch nicht im Rahmen, obwohl
        # der Aufrufer sie kennt.
        assert "unbenutzt@test.de" not in zeile.rahmen_json
        assert "aufgabenkorb@test.de" not in (zeile.rahmen_json + zeile.fakten)

    def test_the_guardian_report_queues_facts_and_a_fallback(self, db: Session):
        user = self._benutzer(db, "guardiankorb")
        vorher = threading.active_count()

        ai_guardian_report._zustellen(
            db=db,
            user_id=int(user.id),
            provider_id=None,
            to="unbenutzt@test.de",
            username=str(user.username),
            server_name="Testserver",
            incident_type="process_not_running",
            geheilt=False,
            bericht="Der Dienst liess sich nicht starten.",
            backup_name="vor-eingriff-2026",
        )

        assert threading.active_count() == vorher
        zeile = db.query(AiMailOutbox).filter(
            AiMailOutbox.user_id == user.id
        ).one()
        assert zeile.anlass == "ai-guardian-report"
        # Das Zustandswort steht vorn und kommt aus `geheilt`, nicht aus dem
        # Bericht — der behauptet hier nichts, aber ein Modell koennte es.
        assert zeile.betreff.startswith(
            "Maunting Service Manager — Guardian: Problem nicht behoben"
        )
        assert "vor-eingriff-2026" in zeile.text_body
        assert "Ergebnis laut Panel: nicht behoben" in zeile.fakten
