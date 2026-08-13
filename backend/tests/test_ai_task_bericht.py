"""Der Bericht nach einem faelligen Auftrag — und wann er ausbleibt.

Das ist die Stelle, an der der Betreiber ueberhaupt erfaehrt, dass etwas
passiert ist. Er sass nicht davor; ohne diese Mail steht der ganze Vorgang nur
in einem Chat, den er vielleicht tagelang nicht oeffnet. Deshalb sind die
Zusagen hier haerter als beim gewoehnlichen Chatlauf:

* Verschickt wird bei **jedem** Endzustand. "Nicht geschafft" ist die
  wichtigere Nachricht von beiden.
* **Genau einmal.** Zwei Berichte ueber einen Vorgang sind schlimmer als eine
  ausgebliebene Wiederholung: der Betreiber kann nicht unterscheiden, ob es
  zwei Laeufe waren.
* Fremdtext geht durch die Maskierung. Der Abschlusstext stammt vom Modell und
  kann Serverlogs enthalten — also Text, den ein Spieler geschrieben hat.

Abgefangen wird bei `_zustellen` und nicht bei `send_ai_task_report`: der echte
Weg startet einen Daemon-Thread mit eigener Ereignisschleife, und ein Test, der
darauf wartet, misst die Laufzeit des Rechners.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from models import AiConversation, AiMessage, AiRun, AiTask, Role, RolePermission, User
from services import ai_stream_service, ai_task_report, ai_task_service
from services.auth_service import AuthService
from services.role_service import set_user_roles


KI_RECHTE = ("ai.chat.use", "ai.tasks.manage")


def _benutzer(db: Session, name: str, *, mails: bool = True) -> User:
    user = AuthService.create_user(db, name, f"{name}@test.de", "UserPass123!")
    user.email_verified = True
    user.email_notifications = mails
    db.commit()
    rolle = Role(name=f"bericht-{name}", description=None, is_system=False)
    db.add(rolle)
    db.flush()
    for key in KI_RECHTE:
        db.add(RolePermission(role_id=rolle.id, permission_key=key))
    db.commit()
    set_user_roles(db, user, [rolle.id])
    db.refresh(user)
    return user


def _aufgabe(db: Session, user: User, **felder) -> AiTask:
    vorgabe = {
        "title": "Serverbericht",
        "instruction": "Sieh nach den Servern.",
        "kind": "report",
        "plan_kind": "daily",
        "time_of_day": "08:00",
        "timezone": "Europe/Berlin",
        "channel": "email",
    }
    vorgabe.update(felder)
    aufgabe = ai_task_service.anlegen(db, user=user, felder=vorgabe)
    db.commit()
    db.refresh(aufgabe)
    return aufgabe


def _lauf(db: Session, user: User, aufgabe: AiTask, *, status: str = "completed",
          antwort: str | None = "Alle vier Server laufen.") -> AiRun:
    conversation = AiConversation(id=f"conv-{user.id}", user_id=user.id, title="KI")
    db.add(conversation)
    db.flush()
    run = AiRun(
        id=f"run-{aufgabe.id}",
        user_id=user.id,
        conversation_id=conversation.id,
        status=status,
    )
    db.add(run)
    if antwort is not None:
        db.add(AiMessage(
            id=f"msg-{aufgabe.id}",
            conversation_id=conversation.id,
            role="assistant",
            content=antwort,
            status="complete",
        ))
    db.commit()
    db.refresh(run)
    return run


def _zustand(aufgabe: AiTask, **felder) -> dict:
    rahmen = {
        "task_id": aufgabe.id,
        "kind": aufgabe.kind,
        "channel": aufgabe.channel,
        "title": aufgabe.title,
    }
    rahmen.update(felder)
    return {"aufgabe": rahmen}


class Mailfach:
    """Faengt den Bericht ab, statt ihn zu verschicken."""

    def __init__(self):
        self.briefe: list[dict] = []

    def einbauen(self, monkeypatch) -> "Mailfach":
        from services import email_service

        monkeypatch.setattr(ai_task_report, "_zustellen", lambda **f: self.briefe.append(f))
        monkeypatch.setattr(
            email_service.EmailService, "is_configured", staticmethod(lambda: True)
        )
        return self


# ── Verschickt wird bei jedem Endzustand ──────────────────────────────────


@pytest.mark.parametrize("status,geschafft", [
    ("completed", True),
    ("failed", False),
    ("cancelled", False),
])
def test_jeder_endzustand_erreicht_den_betreiber(
    db: Session, monkeypatch, status, geschafft
) -> None:
    """Ein Auftrag, der still scheitert, ist schlimmer als gar keiner.

    Der Betreiber verlaesst sich dann auf ein Backup, das es seit Wochen nicht
    mehr gibt — und erfaehrt es erst, wenn er es braucht.
    """
    fach = Mailfach().einbauen(monkeypatch)
    user = _benutzer(db, f"zustand{status}")
    aufgabe = _aufgabe(db, user)
    run = _lauf(db, user, aufgabe, status=status)

    ai_task_report.bericht_versenden(db, run=run, zustand=_zustand(aufgabe))

    assert len(fach.briefe) == 1
    assert fach.briefe[0]["geschafft"] is geschafft


def test_der_bericht_nennt_plan_und_namen_der_aufgabe(db: Session, monkeypatch) -> None:
    """Beides aus **einer** Quelle: `plan_text`.

    Sonst stuende im Betreff eine andere Beschreibung desselben Plans als in der
    Auflistung im Chat — und der Betreiber haette zwei Aussagen ueber eine
    Aufgabe, die sich widersprechen koennen.
    """
    fach = Mailfach().einbauen(monkeypatch)
    user = _benutzer(db, "planimtext")
    aufgabe = _aufgabe(db, user, title="Morgendlicher Serverbericht")
    run = _lauf(db, user, aufgabe)

    ai_task_report.bericht_versenden(db, run=run, zustand=_zustand(aufgabe))

    brief = fach.briefe[0]
    assert brief["task_title"] == "Morgendlicher Serverbericht"
    assert brief["plan_text"] == ai_task_service.plan_text(aufgabe)
    assert "Europe/Berlin" in brief["plan_text"]


def test_der_abschlusstext_des_modells_geht_mit(db: Session, monkeypatch) -> None:
    fach = Mailfach().einbauen(monkeypatch)
    user = _benutzer(db, "abschluss")
    aufgabe = _aufgabe(db, user)
    run = _lauf(db, user, aufgabe, antwort="Server 3 war aus, ich habe ihn gestartet.")

    ai_task_report.bericht_versenden(db, run=run, zustand=_zustand(aufgabe))

    assert "Server 3 war aus" in fach.briefe[0]["bericht"]


def test_ohne_abschlusstext_steht_trotzdem_etwas_in_der_mail(
    db: Session, monkeypatch
) -> None:
    """Eine leere Mail waere schlimmer als keine — sie sieht nach Erfolg aus."""
    fach = Mailfach().einbauen(monkeypatch)
    user = _benutzer(db, "stumm")
    aufgabe = _aufgabe(db, user)
    run = _lauf(db, user, aufgabe, antwort=None)

    ai_task_report.bericht_versenden(db, run=run, zustand=_zustand(aufgabe))

    assert "KI-Chat" in fach.briefe[0]["bericht"]


def test_der_fremdtext_ist_geschwaerzt(db: Session, monkeypatch) -> None:
    """Der Abschlusstext kann Serverlogs enthalten — also fremde Adressen."""
    fach = Mailfach().einbauen(monkeypatch)
    user = _benutzer(db, "geschwaerzt")
    aufgabe = _aufgabe(db, user)
    run = _lauf(
        db, user, aufgabe,
        antwort="Im Log stand die Adresse spieler@example.com.",
    )

    ai_task_report.bericht_versenden(db, run=run, zustand=_zustand(aufgabe))

    assert "spieler@example.com" not in fach.briefe[0]["bericht"]


# ── Wann ausdruecklich nichts hinausgeht ──────────────────────────────────


def test_kanal_chat_verschickt_nichts(db: Session, monkeypatch) -> None:
    """Der Benutzer hat ausdruecklich keine Mail gewollt."""
    fach = Mailfach().einbauen(monkeypatch)
    user = _benutzer(db, "nurchat")
    aufgabe = _aufgabe(db, user, channel="chat")
    run = _lauf(db, user, aufgabe)

    ai_task_report.bericht_versenden(db, run=run, zustand=_zustand(aufgabe))

    assert fach.briefe == []


def test_kanal_email_heisst_zusaetzlich_und_nicht_ausschliesslich(
    db: Session, monkeypatch
) -> None:
    """Der Verlauf steht in **jedem** Fall im Chat.

    `channel` entscheidet nur ueber die Benachrichtigung. Der Betreiber wollte
    beides — den Verlauf im Chat, damit er im Kontext bleibt, und die Mail,
    damit er es mitbekommt.
    """
    fach = Mailfach().einbauen(monkeypatch)
    user = _benutzer(db, "beides")
    aufgabe = _aufgabe(db, user, channel="both")
    run = _lauf(db, user, aufgabe)

    ai_task_report.bericht_versenden(db, run=run, zustand=_zustand(aufgabe))

    assert len(fach.briefe) == 1
    nachrichten = (
        db.query(AiMessage)
        .filter(AiMessage.conversation_id == run.conversation_id)
        .count()
    )
    assert nachrichten >= 1


def test_ohne_benachrichtigungswunsch_kommt_keine_mail(db: Session, monkeypatch) -> None:
    """Derselbe Schalter wie beim Guardian-Bericht. Kein dritter."""
    fach = Mailfach().einbauen(monkeypatch)
    user = _benutzer(db, "keinemails", mails=False)
    aufgabe = _aufgabe(db, user)
    run = _lauf(db, user, aufgabe)

    ai_task_report.bericht_versenden(db, run=run, zustand=_zustand(aufgabe))

    assert fach.briefe == []


def test_ohne_eingerichteten_versand_kommt_keine_mail(db: Session, monkeypatch) -> None:
    from services import email_service

    fach = Mailfach().einbauen(monkeypatch)
    monkeypatch.setattr(
        email_service.EmailService, "is_configured", staticmethod(lambda: False)
    )
    user = _benutzer(db, "keinsmtp")
    aufgabe = _aufgabe(db, user)
    run = _lauf(db, user, aufgabe)

    ai_task_report.bericht_versenden(db, run=run, zustand=_zustand(aufgabe))

    assert fach.briefe == []


def test_ohne_rahmen_passiert_gar_nichts(db: Session, monkeypatch) -> None:
    """Ein gewoehnlicher Chatlauf schickt keine Mail."""
    fach = Mailfach().einbauen(monkeypatch)
    user = _benutzer(db, "chatlauf")
    aufgabe = _aufgabe(db, user)
    run = _lauf(db, user, aufgabe)

    ai_task_report.bericht_versenden(db, run=run, zustand={})

    assert fach.briefe == []


def test_eine_inzwischen_geloeschte_aufgabe_berichtet_trotzdem(
    db: Session, monkeypatch
) -> None:
    """Zwischen Start und Ende koennen Minuten liegen.

    Loescht der Betreiber die Aufgabe in dieser Zeit im Chat, ist der Lauf
    trotzdem gelaufen — und was tatsaechlich passiert ist, gehoert gesagt. Der
    Name kommt dann aus dem Rahmen, der den Lauf ueberlebt hat.
    """
    fach = Mailfach().einbauen(monkeypatch)
    user = _benutzer(db, "geloescht")
    aufgabe = _aufgabe(db, user, title="Nachtbackup")
    run = _lauf(db, user, aufgabe)
    zustand = _zustand(aufgabe)
    db.delete(db.get(AiTask, aufgabe.id))
    db.commit()

    ai_task_report.bericht_versenden(db, run=run, zustand=zustand)

    assert len(fach.briefe) == 1
    assert fach.briefe[0]["task_title"] == "Nachtbackup"


# ── Genau einmal ──────────────────────────────────────────────────────────


def test_der_abschluss_berichtet_genau_einmal(db: Session, monkeypatch) -> None:
    """`_lauf_nachbereiten` wird aus zwei Richtungen gerufen.

    Vom regulaeren Abschluss und vom Waechter fuer den bereits beendeten Lauf.
    Beide koennen denselben Lauf treffen. Die Marke im Zustand entscheidet, und
    sie wird **vor** dem Versand committet.
    """
    fach = Mailfach().einbauen(monkeypatch)
    user = _benutzer(db, "einmal")
    aufgabe = _aufgabe(db, user)
    run = _lauf(db, user, aufgabe)
    zustand = _zustand(aufgabe)

    ai_stream_service._lauf_nachbereiten(db, run, zustand)
    ai_stream_service._lauf_nachbereiten(db, run, zustand)

    assert len(fach.briefe) == 1


def test_die_marke_ueberlebt_im_laufzustand(db: Session, monkeypatch) -> None:
    """Nicht nur im Woerterbuch: der zweite Weg liest sie frisch aus der Zeile."""
    from services import ai_run_service

    fach = Mailfach().einbauen(monkeypatch)
    user = _benutzer(db, "marke")
    aufgabe = _aufgabe(db, user)
    run = _lauf(db, user, aufgabe)

    ai_stream_service._lauf_nachbereiten(db, run, _zustand(aufgabe))
    db.refresh(run)
    assert ai_run_service.zustand_lesen(run).get("aufgabe_berichtet") is True

    # Der zweite Weg uebergibt `None` und liest den Zustand selbst.
    ai_stream_service._lauf_nachbereiten(db, run, None)
    assert len(fach.briefe) == 1


def test_ein_fehlgeschlagener_versand_kippt_den_lauf_nicht(
    db: Session, monkeypatch
) -> None:
    """Der Lauf ist zu diesem Zeitpunkt fertig und committet."""
    Mailfach().einbauen(monkeypatch)
    user = _benutzer(db, "versandfehler")
    aufgabe = _aufgabe(db, user)
    run = _lauf(db, user, aufgabe)

    def kaputt(**felder):
        raise RuntimeError("SMTP weg")

    monkeypatch.setattr(ai_task_report, "_zustellen", kaputt)

    ai_stream_service._lauf_nachbereiten(db, run, _zustand(aufgabe))

    db.refresh(run)
    assert run.status == "completed"


def test_guardian_und_aufgabe_stoeren_sich_nicht(db: Session, monkeypatch) -> None:
    """Beide Rahmen haengen an derselben Nachbereitung, mit je eigener Marke.

    Sie kommen nie zusammen vor — aber die Funktion darf das nicht voraussetzen,
    sonst verschluckt der eine Rahmen den Bericht des anderen.
    """
    from services import ai_guardian_report

    fach = Mailfach().einbauen(monkeypatch)
    guardian_briefe: list[dict] = []
    monkeypatch.setattr(
        ai_guardian_report, "_zustellen", lambda **f: guardian_briefe.append(f)
    )
    user = _benutzer(db, "beiderahmen")
    aufgabe = _aufgabe(db, user)
    run = _lauf(db, user, aufgabe)
    zustand = _zustand(aufgabe)
    # Ein Guardian-Rahmen ohne gueltige Kennungen: `bericht_versenden` steigt
    # dort sauber aus, die Aufgabenseite muss trotzdem laufen.
    zustand["guardian"] = {"server_id": 0, "incident_id": 0}

    ai_stream_service._lauf_nachbereiten(db, run, zustand)

    assert len(fach.briefe) == 1
    assert guardian_briefe == []
