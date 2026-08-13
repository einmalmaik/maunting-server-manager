"""Was der Katalog anbietet, muss der Vorschlagspfad auch annehmen.

Der Katalog fuehrt das Modell, der Vorschlagspfad entscheidet. Laufen beide
auseinander, entsteht die haesslichere von zwei Lagen: entweder bietet der
Katalog etwas an, das abgewiesen wird — das Modell versucht es dann wieder und
wieder und verbrennt Tokens —, oder er verschweigt etwas, das durchginge.

Dazu die Menge, die ein faellig gewordener Auftrag aufrufen darf. Sie ist
ausgeschrieben und keine Ableitung; ein Tippfehler darin waere eine stille
Luecke, die niemandem auffiele, weil bei einem faelligen Lauf niemand zusieht.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy.orm import Session

from models import AiTask, Role, RolePermission, User
from models.ai_task import ARTEN, KANAELE, PLANARTEN
from services import ai_action_service, ai_proposal_service, ai_task_service
from services.ai_action_errors import AiActionValidationError
from services.ai_tool_registry import (
    ALWAYS_CONFIRM_TOOLS,
    AUFGABEN_HANDELN,
    AUFGABEN_LESEN,
    WERKZEUGE,
    aufgaben_tools,
)
from services.auth_service import AuthService
from services.role_service import set_user_roles


def _katalog(name: str) -> dict:
    for eintrag in ai_action_service.provider_tool_definitions():
        if eintrag["function"]["name"] == name:
            return eintrag["function"]
    raise AssertionError(f"{name} steht nicht im Werkzeugkatalog")


def _benutzer(db: Session, name: str, *rechte: str) -> User:
    user = AuthService.create_user(db, name, f"{name}@test.de", "UserPass123!")
    user.email_verified = True
    user.email_notifications = True
    db.commit()
    rolle = Role(name=f"werkzeug-{name}", description=None, is_system=False)
    db.add(rolle)
    db.flush()
    for key in rechte:
        db.add(RolePermission(role_id=rolle.id, permission_key=key))
    db.commit()
    set_user_roles(db, user, [rolle.id])
    db.refresh(user)
    return user


#: Ohne `reason`/`expected_effect`. Die beiden trennt `create_proposal` ab,
#: bevor es den Payload-Bau ruft (`rest` statt `arguments`) — genau damit die
#: Schluesselmengenpruefungen darin ihre exakte Form behalten. Ein Test, der sie
#: mitgibt, prueft einen Aufruf, den es im Betrieb nicht gibt.
ANLEGEN = {
    "title": "Serverbericht",
    "instruction": "Sieh nach den Servern.",
    "kind": "report",
    "plan_kind": "daily",
    "time_of_day": "08:00",
    "timezone": "Europe/Berlin",
    "channel": "email",
}


# ── Katalog und Pruefung tragen dieselben Werte ───────────────────────────


@pytest.mark.parametrize(
    ("feld", "erwartet"),
    [("kind", ARTEN), ("plan_kind", PLANARTEN), ("channel", KANAELE)],
)
def test_die_aufzaehlungen_im_katalog_sind_die_der_datenbank(
    feld: str, erwartet: tuple[str, ...]
) -> None:
    """Ein `enum`, das der Vorschlagspfad nicht kennt, ist eine Einladung zum
    Endlosversuch — und eines, das er kennt und der Katalog verschweigt, ist
    eine Faehigkeit, die niemand findet."""
    schema = _katalog("propose_task_set")["parameters"]["properties"][feld]
    assert schema["enum"] == list(erwartet)


def test_kein_argument_klingt_nach_befehlsausfuehrung() -> None:
    """Der allgemeine Vertragstest prueft das ohnehin — hier steht der Grund.

    `run` faellt dort als ganzer Namensteil auf, und die Spalte in der Datenbank
    heisst `next_run_at`. Wer das Schema aus den Spaltennamen ableitet statt es
    zu schreiben, reisst den Test, ohne zu verstehen warum.
    """
    namen = set(_katalog("propose_task_set")["parameters"]["properties"])
    assert not {name for name in namen if "run" in name.split("_")}


def test_die_testmail_nimmt_keinen_empfaenger_entgegen() -> None:
    """Die eigentliche Sicherheitsaussage dieses Werkzeugs.

    Gaebe es ein `to`, waere MSM ueber die KI mit einem Satz im Chat zu einem
    Mailversender fuer Fremde geworden. Der Empfaenger steht deshalb nicht im
    Schema, sondern im Handler — er ist der Fragende, immer.
    """
    assert _katalog("send_test_email")["parameters"]["properties"] == {}
    quelle = ai_action_service._execute_send_test_email.__code__.co_varnames
    assert "to" not in quelle


# ── Die Werkzeugmenge eines faelligen Laufs ───────────────────────────────


def test_die_aufgabenmengen_enthalten_nur_werkzeuge_die_es_gibt() -> None:
    """Ein Tippfehler waere eine stille Luecke: das Werkzeug faehlt schlicht,
    und bei einem faelligen Lauf sitzt niemand davor, dem es auffiele."""
    unbekannt = (AUFGABEN_LESEN | AUFGABEN_HANDELN) - set(WERKZEUGE)
    assert unbekannt == set()


def test_eine_faellige_aufgabe_kann_niemanden_fragen() -> None:
    """`ask_user` fehlt, weil niemand davorsitzt.

    Das ist keine Sparmassnahme. Eine unbeantwortbare Rueckfrage haette den Lauf
    geparkt, bis er ablaeuft — die Aufgabe waere still ausgefallen, und der
    Benutzer haette am naechsten Morgen weder Mail noch Fehlermeldung.
    """
    assert "ask_user" not in aufgaben_tools("report")
    assert "ask_user" not in aufgaben_tools("act")


def test_ein_reiner_bericht_bekommt_keine_schreibwerkzeuge() -> None:
    from services.ai_tool_registry import WRITE_TOOLS

    assert aufgaben_tools("report") & WRITE_TOOLS == set()
    assert aufgaben_tools("act") & WRITE_TOOLS == set(AUFGABEN_HANDELN)


def test_unumkehrbares_bleibt_auch_einer_handelnden_aufgabe_verwehrt() -> None:
    """Loeschen und Einspielen stehen in `ALWAYS_CONFIRM_TOOLS` und wuerden
    ohnehin abgewiesen — sie hier trotzdem fernzuhalten spart eine Runde, in der
    das Modell etwas versucht, das nie klappen kann."""
    assert AUFGABEN_HANDELN & ALWAYS_CONFIRM_TOOLS == set()


def test_eine_aufgabe_legt_keine_aufgaben_an() -> None:
    """Sonst waere ein Auftrag denkbar, der sich selbst vermehrt."""
    for name in ("propose_task_set", "propose_task_delete"):
        assert name not in aufgaben_tools("act")


def test_aus_einem_lauf_ohne_zeugen_wird_nichts_gelernt() -> None:
    """Was das Modell dabei liest, kann ein Spieler in ein Log geschrieben
    haben. Der Gedaechtnisblock im Kontext kommt ohnehin von selbst mit — es
    fehlt also nichts, was die Aufgabe braeuchte."""
    from services.ai_tool_registry import MEMORY_TOOLS, SKILL_TOOLS

    assert aufgaben_tools("act") & (MEMORY_TOOLS | SKILL_TOOLS) == set()


def test_die_websuche_ist_dabei() -> None:
    """Anders als beim Guardian, und mit Absicht: dort fehlt sie, weil niemand
    gefragt hat. Hier hat jemand gefragt — "sag mir taeglich, wie das Wetter
    wird" war das Beispiel des Betreibers."""
    assert "web_search" in aufgaben_tools("report")


# ── Der Vorschlagspfad ────────────────────────────────────────────────────


def test_ohne_zeitzone_entsteht_kein_vorschlag(db: Session) -> None:
    """Die mechanische Seite der Zusage, dass die KI vorher gefragt hat.

    Der Systemprompt sagt es dem Modell; ein Prompt ist aber keine Schranke.
    Hier laeuft der Versuch auf, **bevor** eine Karte im Chat steht — und der
    Fehlertext nennt `ask_user`, damit der naechste Versuch der richtige ist.
    """
    user = _benutzer(db, "ohnezone", "ai.tasks.manage")
    felder = {name: wert for name, wert in ANLEGEN.items() if name != "timezone"}
    with pytest.raises(AiActionValidationError, match="ask_user"):
        ai_proposal_service._task_set_payload(db, user, felder)


def test_die_vorschau_speichert_nichts(db: Session) -> None:
    """Ein Vorschlag, der schon passiert ist, bevor jemand ihn bestaetigt hat,
    waere kein Vorschlag."""
    user = _benutzer(db, "vorschau", "ai.tasks.manage")
    payload, preview = ai_proposal_service._task_set_payload(db, user, dict(ANLEGEN))
    db.flush()

    assert db.query(AiTask).count() == 0
    assert payload["task_id"] is None
    assert preview["operation"] == "task_create"
    assert preview["plan"] == "taeglich um 08:00 (Europe/Berlin)"
    assert preview["next_run"] is not None


def test_die_vorschau_beim_aendern_laesst_die_zeile_unberuehrt(db: Session) -> None:
    user = _benutzer(db, "aendernvorschau", "ai.tasks.manage")
    aufgabe = ai_task_service.anlegen(db, user=user, felder=dict(ANLEGEN))
    db.commit()

    _, preview = ai_proposal_service._task_set_payload(db, user, {
        "task_id": aufgabe.id,
        "enabled": False,
    })
    db.refresh(aufgabe)

    assert preview["operation"] == "task_update"
    assert preview["enabled"] is False
    # Die echte Zeile steht unveraendert da, bis jemand bestaetigt.
    assert aufgabe.enabled is True


def test_eine_leere_kennung_legt_an_statt_abzuweisen(db: Session) -> None:
    """**Der haeufigste Fehlschlag im Betrieb.**

    Das Schema sagt "task_id weglassen legt neu an". Ein Modell kann ein Feld
    aber schlecht weglassen, das im Schema danebensteht — es schickt `""`. Die
    Abweisung kostete dabei nicht das Feld, sondern die ganze Antwort: eine
    Formmeldung aus dem Vorschlagspfad beendet den Lauf, und im Chat stand
    statt der neuen Aufgabe "Die KI hat einen Werkzeugaufruf gestellt, den das
    Panel nicht annehmen konnte".

    "Nicht genannt" und "leer genannt" zu unterscheiden trug hier nichts. Was
    eine Kennung sein *soll* und keine ist — eine Zahl, ein Objekt — faellt
    weiterhin durch.
    """
    user = _benutzer(db, "leerekennung", "ai.tasks.manage")

    payload, preview = ai_proposal_service._task_set_payload(
        db, user, {**ANLEGEN, "task_id": ""}
    )

    assert payload["task_id"] is None
    assert preview["operation"] == "task_create"
    assert db.query(AiTask).count() == 0


def test_eine_kennung_die_keine_ist_faellt_weiterhin_durch(db: Session) -> None:
    user = _benutzer(db, "falschekennung", "ai.tasks.manage")
    for wert in (7, ["a"], {"id": "a"}):
        with pytest.raises(AiActionValidationError, match="Kennung"):
            ai_proposal_service._task_set_payload(
                db, user, {**ANLEGEN, "task_id": wert}
            )


def test_eine_anders_geschriebene_uhrzeit_geht_durch(db: Session) -> None:
    """Zusammen mit `test_dieselbe_uhrzeit_anders_geschrieben_wird_angenommen`.

    Dort steht die Regel, hier steht, dass sie auch am Werkzeug ankommt — der
    Weg dazwischen fuehrt durch `vorschau`, und der ist es, der im Betrieb den
    Lauf abbrach.
    """
    user = _benutzer(db, "uhrzeitform", "ai.tasks.manage")

    _, preview = ai_proposal_service._task_set_payload(
        db, user, {**ANLEGEN, "time_of_day": "8:00"}
    )

    assert preview["plan"] == "taeglich um 08:00 (Europe/Berlin)"


def test_nur_die_uhrzeit_zu_verschieben_genuegt(db: Session) -> None:
    """Die kleinste denkbare Aenderung — und sie ging an Wochentagen kaputt.

    `_planfelder_ergaenzen` reicht den Bestand aus der Datenbank herein, und
    dort stehen die Tage als ``"1,3,5"``. Die Pruefung nahm nur Listen an, also
    scheiterte ausgerechnet "verschieb das auf neun Uhr" an einer Aufgabe, die
    gar nicht angefasst werden sollte.
    """
    user = _benutzer(db, "verschieben", "ai.tasks.manage")
    aufgabe = ai_task_service.anlegen(
        db, user=user, felder={**ANLEGEN, "weekdays": [1, 3, 5]}
    )
    db.commit()

    _, preview = ai_proposal_service._task_set_payload(
        db, user, {"task_id": aufgabe.id, "time_of_day": "09:00"}
    )

    assert preview["operation"] == "task_update"
    # Die Tage stehen unveraendert daneben — sie waren nie Gegenstand.
    assert preview["plan"] == "Mo, Mi, Fr um 09:00 (Europe/Berlin)"


def test_ein_aenderungsvorschlag_ohne_aenderung_wird_abgewiesen(db: Session) -> None:
    user = _benutzer(db, "leeraendern", "ai.tasks.manage")
    aufgabe = ai_task_service.anlegen(db, user=user, felder=dict(ANLEGEN))
    db.commit()

    with pytest.raises(AiActionValidationError, match="nichts"):
        ai_proposal_service._task_set_payload(db, user, {
            "task_id": aufgabe.id,
        })


def test_ein_erfundenes_argument_wird_abgewiesen(db: Session) -> None:
    user = _benutzer(db, "erfunden", "ai.tasks.manage")
    with pytest.raises(AiActionValidationError, match="ungueltige Argumente"):
        ai_proposal_service._task_set_payload(
            db, user, {**ANLEGEN, "command": "rm -rf /"}
        )


def test_die_loeschvorschau_nennt_namen_und_plan(db: Session) -> None:
    """"Aufgabe a3f2c1…-… loeschen?" ist keine Frage, die jemand beantworten
    kann."""
    user = _benutzer(db, "loeschvorschau", "ai.tasks.manage")
    aufgabe = ai_task_service.anlegen(db, user=user, felder=dict(ANLEGEN))
    db.commit()

    payload, preview = ai_proposal_service._task_delete_payload(
        db, user, {"task_id": aufgabe.id}
    )

    assert payload["task_id"] == aufgabe.id
    assert preview["title"] == "Serverbericht"
    assert preview["plan"] == "taeglich um 08:00 (Europe/Berlin)"
    assert db.query(AiTask).count() == 1


def test_in_der_vorschau_steht_nichts_geheimes(db: Session) -> None:
    """Sie geht unverschluesselt in die Datenbank, in die SSE-Nutzlast und in
    den wiederangehaengten Chat."""
    user = _benutzer(db, "geheim", "ai.tasks.manage")
    _, preview = ai_proposal_service._task_set_payload(db, user, {
        **ANLEGEN,
        "instruction": "Melde dich an mit password=Sommer2026! und sieh nach.",
    })
    assert "Sommer2026" not in json.dumps(preview, ensure_ascii=False)


# ── Die Testmail ──────────────────────────────────────────────────────────


def test_die_testmail_geht_an_die_eigene_adresse(db: Session, monkeypatch) -> None:
    """Die Testmail nimmt denselben Korbweg wie jeder Bericht.

    Frueher stand hier ein Thread mit einer Koroutine darin; abgefangen wurde
    `ai_mail.zustellen` als Ganzes. Seit die Mail in den Ausgangskorb geht,
    braucht es keine Attrappe mehr — die Zeile steht in der Datenbank und laesst
    sich lesen. Genau das ist der Gewinn des Umbaus: der Versand ist nicht mehr
    etwas, das gleich passiert, sondern etwas, das aufgeschrieben ist.
    """
    from models import AiMailOutbox
    from services.email_service import EmailService

    user = _benutzer(db, "testmail", "ai.chat.use")
    monkeypatch.setattr(
        EmailService, "is_configured", staticmethod(lambda: True)
    )
    monkeypatch.setattr(EmailService, "_get_provider", staticmethod(lambda: "smtp"))
    ai_action_service._TESTMAILS.clear()

    ergebnis = ai_action_service._execute_send_test_email(db, user=user)

    assert ergebnis["sent"] is True
    assert ergebnis["transport"] == "smtp"
    # Maskiert: der erste Buchstabe und die Domain, nie die ganze Adresse.
    assert ergebnis["recipient"] == "t***@test.de"
    assert "testmail@test.de" not in json.dumps(ergebnis, ensure_ascii=False)

    zeilen = (
        db.query(AiMailOutbox).filter(AiMailOutbox.user_id == user.id).all()
    )
    assert len(zeilen) == 1
    zeile = zeilen[0]
    assert zeile.anlass == "ai-test-email"
    assert zeile.status == "offen"
    # Der Rueckfall steht vollstaendig darin — die Testmail ist das Messgeraet
    # fuer den Versandweg und darf nicht am Modell haengen.
    assert "Versandweg" in zeile.text_body
    # Und die Angaben fuer den Verfassungsschritt liegen daneben, ohne Adresse.
    assert zeile.fakten and "Testmail" in zeile.fakten
    assert "testmail@test.de" not in (zeile.fakten + (zeile.rahmen_json or ""))


def test_die_testmail_wird_gedrosselt(db: Session, monkeypatch) -> None:
    """Nicht gegen den Menschen gerichtet, sondern gegen ein Modell in einer
    Schleife: "kommt sie an?" – "ich schicke nochmal" – "und nochmal"."""
    from services import ai_mail
    from services.email_service import EmailService

    user = _benutzer(db, "drossel", "ai.chat.use")
    monkeypatch.setattr(EmailService, "is_configured", staticmethod(lambda: True))
    monkeypatch.setattr(EmailService, "_get_provider", staticmethod(lambda: "resend"))
    monkeypatch.setattr(ai_mail, "zustellen", lambda **felder: None)
    ai_action_service._TESTMAILS.clear()

    for _ in range(ai_action_service.MAX_TESTMAILS_JE_STUNDE):
        assert ai_action_service._execute_send_test_email(db, user=user)["sent"] is True

    gebremst = ai_action_service._execute_send_test_email(db, user=user)
    assert gebremst["sent"] is False
    assert gebremst["reason"] == "rate_limited"
    assert "Spam" in gebremst["detail"]


def test_ohne_versandweg_sagt_die_testmail_warum(db: Session, monkeypatch) -> None:
    """Drei Moeglichkeiten, und der Benutzer soll alle drei hoeren — sonst sucht
    er am falschen Ende."""
    from services.email_service import EmailService

    user = _benutzer(db, "keinweg", "ai.chat.use")
    monkeypatch.setattr(EmailService, "is_configured", staticmethod(lambda: False))
    ai_action_service._TESTMAILS.clear()

    ergebnis = ai_action_service._execute_send_test_email(db, user=user)

    assert ergebnis["sent"] is False
    assert ergebnis["reason"] == "not_deliverable"
    assert "Benachrichtigungen" in ergebnis["detail"]
