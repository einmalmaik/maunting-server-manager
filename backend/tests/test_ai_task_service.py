"""Anlegen, aendern, loeschen — und die Grenzen, die dabei gelten.

Der Schwerpunkt liegt auf den Grenzen und nicht auf dem Glueckspfad. Ein
stehender Auftrag ist die einzige Sache, die die KI ohne anwesenden Menschen und
ohne Stoerung in Gang setzt; jede Bedingung, die beim Anlegen greift, ist eine,
die nachts um drei nicht mehr jemand pruefen kann.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from models import AiAutonomyGrant, AiTask, Role, RolePermission, User
from services import ai_task_service
from services.ai_action_errors import AiActionValidationError
from services.auth_service import AuthService
from services.role_service import set_user_roles


def _benutzer(db: Session, name: str, *rechte: str) -> User:
    user = AuthService.create_user(db, name, f"{name}@test.de", "UserPass123!")
    user.email_verified = True
    db.commit()
    rolle = Role(name=f"aufgaben-{name}", description=None, is_system=False)
    db.add(rolle)
    db.flush()
    for key in rechte:
        db.add(RolePermission(role_id=rolle.id, permission_key=key))
    db.commit()
    set_user_roles(db, user, [rolle.id])
    db.refresh(user)
    return user


def _freigabe(db: Session, user: User, *, budget: int = 10, an: bool = True) -> None:
    db.add(AiAutonomyGrant(
        user_id=user.id, server_id=None, enabled=an, max_actions_per_hour=budget
    ))
    db.commit()


TAEGLICH = {
    "title": "Serverbericht",
    "instruction": "Sieh nach den Servern und fasse zusammen.",
    "kind": "report",
    "plan_kind": "daily",
    "time_of_day": "08:00",
    "timezone": "Europe/Berlin",
    "channel": "email",
}


# ── Rechte ────────────────────────────────────────────────────────────────


def test_ohne_das_recht_entsteht_keine_aufgabe(db: Session) -> None:
    user = _benutzer(db, "ohnerecht", "ai.chat.use")
    with pytest.raises(AiActionValidationError, match="ai.tasks.manage"):
        ai_task_service.anlegen(db, user=user, felder=dict(TAEGLICH))
    assert db.query(AiTask).count() == 0


def test_eine_handelnde_aufgabe_verlangt_die_autonome_freigabe(db: Session) -> None:
    """Der ausdrueckliche Wunsch des Betreibers: handeln nur mit Autonomie.

    Und der Grund, warum es beim **Anlegen** geprueft wird und nicht erst beim
    Lauf: ohne Freigabe erzeugte ein faelliger Lauf um drei Uhr nachts nur einen
    Vorschlag, auf dessen Bestaetigung niemand wartet. Der Betreiber erfuehre
    erst Wochen spaeter, dass sein taegliches Backup nie gelaufen ist.
    """
    user = _benutzer(db, "nurchat", "ai.tasks.manage", "ai.chat.use")
    with pytest.raises(AiActionValidationError, match="autonome"):
        ai_task_service.anlegen(db, user=user, felder={**TAEGLICH, "kind": "act"})

    # Das Recht allein genuegt nicht — es braucht auch die erteilte Freigabe.
    user2 = _benutzer(db, "rechtohnegrant", "ai.tasks.manage", "ai.autonomous.use")
    with pytest.raises(AiActionValidationError, match="autonome"):
        ai_task_service.anlegen(db, user=user2, felder={**TAEGLICH, "kind": "act"})

    # Und eine abgeschaltete Freigabe ist keine.
    user3 = _benutzer(db, "grantaus", "ai.tasks.manage", "ai.autonomous.use")
    _freigabe(db, user3, an=False)
    with pytest.raises(AiActionValidationError, match="autonome"):
        ai_task_service.anlegen(db, user=user3, felder={**TAEGLICH, "kind": "act"})

    # Mit beidem geht es.
    user4 = _benutzer(db, "darfhandeln", "ai.tasks.manage", "ai.autonomous.use")
    _freigabe(db, user4)
    aufgabe = ai_task_service.anlegen(
        db, user=user4, felder={**TAEGLICH, "kind": "act"}
    )
    assert aufgabe.kind == "act"


def test_ein_budget_von_null_ist_keine_freigabe(db: Session) -> None:
    """`max_actions_per_hour = 0` heisst: nichts. Dieselbe Lesart wie in
    `zustaendiger_freigeber` beim Guardian."""
    user = _benutzer(db, "budgetnull", "ai.tasks.manage", "ai.autonomous.use")
    _freigabe(db, user, budget=0)
    assert ai_task_service.darf_handeln(db, user) is False


def test_ein_reiner_bericht_braucht_keine_autonomie(db: Session) -> None:
    """Die Trennung der beiden Arten hat genau hier ihren Nutzen: "sag mir
    morgens wie es steht" ist harmlos und soll ohne Sonderfreigabe gehen."""
    user = _benutzer(db, "nurbericht", "ai.tasks.manage", "ai.chat.use")
    aufgabe = ai_task_service.anlegen(db, user=user, felder=dict(TAEGLICH))
    assert aufgabe.kind == "report"
    assert aufgabe.next_run_at is not None


# ── Anlegen ───────────────────────────────────────────────────────────────


def test_beim_anlegen_steht_der_naechste_termin_schon_fest(db: Session) -> None:
    """`next_run_at` ist die Angabe, nicht eine Zwischenablage.

    Der Takt fragt ausschliesslich danach. Bliebe sie beim Anlegen leer und
    wuerde erst beim ersten Durchlauf gefuellt, waere die Aufgabe bis dahin
    unsichtbar — und niemand koennte dem Benutzer sagen, wann sie zuerst laeuft.
    """
    user = _benutzer(db, "termin", "ai.tasks.manage")
    aufgabe = ai_task_service.anlegen(db, user=user, felder=dict(TAEGLICH))
    db.commit()

    assert aufgabe.next_run_at is not None
    assert aufgabe.enabled is True
    assert ai_task_service.plan_text(aufgabe) == "taeglich um 08:00 (Europe/Berlin)"


def test_der_auftragstext_wird_beim_anlegen_geschwaerzt(db: Session) -> None:
    """Einmal beim Anlegen, nicht bei jedem Lauf.

    Der Text wird bei jeder Faelligkeit zur Benutzernachricht des Laufs — also
    an die Stelle mit dem meisten Gewicht. Was dort dauerhaft steht, soll
    einmal durch die Schwaerzung gegangen sein und danach unveraendert das
    bleiben, was ein Mensch bestaetigt hat.
    """
    user = _benutzer(db, "schwaerzung", "ai.tasks.manage")
    aufgabe = ai_task_service.anlegen(db, user=user, felder={
        **TAEGLICH,
        "instruction": "Melde dich bei api_key=sk-geheim1234567890 und sieh nach.",
    })
    assert "sk-geheim1234567890" not in aufgabe.instruction


@pytest.mark.parametrize(
    "aenderung",
    [
        {"title": ""},
        {"instruction": "   "},
        {"kind": "beides"},
        {"channel": "sms"},
        {"plan_kind": "cron"},
        {"timezone": "MEZ"},
    ],
)
def test_unbrauchbare_angaben_werden_abgewiesen(db: Session, aenderung: dict) -> None:
    user = _benutzer(db, f"abweisung{abs(hash(str(aenderung))) % 10000}", "ai.tasks.manage")
    with pytest.raises(AiActionValidationError):
        ai_task_service.anlegen(db, user=user, felder={**TAEGLICH, **aenderung})
    assert db.query(AiTask).count() == 0


def test_ein_zu_kurzes_intervall_wird_abgewiesen(db: Session) -> None:
    """Die Grenze ist eine Kostengrenze, keine technische.

    "Alle fuenf Minuten" waeren in einer Nacht knapp 300 Anbieteraufrufe aus dem
    Kontingent eines Menschen, der schlaeft. Wer wirklich engmaschig ueberwacht
    sein will, hat dafuer Guardian.
    """
    user = _benutzer(db, "zukurz", "ai.tasks.manage")
    with pytest.raises(AiActionValidationError, match="Intervall"):
        ai_task_service.anlegen(db, user=user, felder={
            **TAEGLICH, "plan_kind": "interval", "interval_hours": 0,
        })
    with pytest.raises(AiActionValidationError, match="Intervall"):
        ai_task_service.anlegen(db, user=user, felder={
            **TAEGLICH, "plan_kind": "interval", "interval_hours": 24 * 400,
        })


def test_ein_einmaliger_termin_in_der_vergangenheit_wird_abgewiesen(db: Session) -> None:
    user = _benutzer(db, "vergangen", "ai.tasks.manage")
    with pytest.raises(AiActionValidationError, match="Vergangenheit"):
        ai_task_service.anlegen(db, user=user, felder={
            **TAEGLICH, "plan_kind": "once", "once_at": "2020-01-01T08:00",
        })


def test_ein_einmaliger_termin_ohne_zone_meint_die_der_aufgabe(db: Session) -> None:
    """"Am 20. um acht" meint seine acht, nicht die von UTC.

    Der Benutzer hat seine Zeitzone genannt; sie dann ausgerechnet bei der
    einzigen Planart zu ignorieren, die einen vollstaendigen Zeitpunkt nennt,
    waere die unerwartetste Stelle fuer eine Ausnahme.
    """
    from datetime import timezone as tz

    user = _benutzer(db, "zonelos", "ai.tasks.manage")
    aufgabe = ai_task_service.anlegen(db, user=user, felder={
        **TAEGLICH, "plan_kind": "once", "once_at": "2026-12-20T08:00",
    })
    # Dezember: Berlin ist UTC+1.
    assert ai_task_service.utc(aufgabe.once_at) == __import__("datetime").datetime(
        2026, 12, 20, 7, 0, tzinfo=tz.utc
    )


def test_mehr_als_zwanzig_aufgaben_gibt_es_nicht(db: Session) -> None:
    user = _benutzer(db, "vielzuviel", "ai.tasks.manage")
    for nummer in range(ai_task_service.MAX_AUFGABEN_JE_BENUTZER):
        ai_task_service.anlegen(
            db, user=user, felder={**TAEGLICH, "title": f"Bericht {nummer}"}
        )
    db.commit()

    with pytest.raises(AiActionValidationError, match="Aufgaben"):
        ai_task_service.anlegen(db, user=user, felder=dict(TAEGLICH))


# ── Aendern und loeschen ──────────────────────────────────────────────────


def test_pausieren_braucht_keine_planangabe(db: Session) -> None:
    """"Pausier das mal" ist der haeufigste Fall und darf nicht verlangen, dass
    das Modell den ganzen Plan erneut aufschreibt — was es dabei falsch
    abschriebe, waere danach der Plan."""
    user = _benutzer(db, "pausieren", "ai.tasks.manage")
    aufgabe = ai_task_service.anlegen(
        db, user=user, felder={**TAEGLICH, "weekdays": [1, 3]}
    )
    db.commit()

    ai_task_service.aendern(
        db, user=user, task_id=aufgabe.id, felder={"enabled": False}
    )
    db.commit()
    db.refresh(aufgabe)

    assert aufgabe.enabled is False
    # Pausiert heisst faellig-nie: der Takt fragt nur `next_run_at` ab, und ein
    # stehengebliebener Termin in der Vergangenheit waere beim Einschalten
    # sofort ueberfaellig.
    assert aufgabe.next_run_at is None
    # Und der Plan steht unveraendert da.
    assert aufgabe.weekdays == "1,3"
    assert aufgabe.time_of_day == "08:00"


def test_wieder_einschalten_rechnet_den_termin_neu(db: Session) -> None:
    user = _benutzer(db, "einschalten", "ai.tasks.manage")
    aufgabe = ai_task_service.anlegen(db, user=user, felder=dict(TAEGLICH))
    ai_task_service.aendern(db, user=user, task_id=aufgabe.id, felder={"enabled": False})
    ai_task_service.aendern(db, user=user, task_id=aufgabe.id, felder={"enabled": True})
    db.commit()
    db.refresh(aufgabe)

    assert aufgabe.enabled is True
    assert ai_task_service.utc(aufgabe.next_run_at) > __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc
    )


def test_ein_planwechsel_raeumt_die_felder_der_alten_planart_weg(db: Session) -> None:
    """Sonst zeigt die Auflistung eine Uhrzeit an, nach der sich nichts richtet
    — und beim Wechsel zurueck gilt plotzlich eine Zeit, die der Benutzer nie
    erneut genannt hat."""
    user = _benutzer(db, "planwechsel", "ai.tasks.manage")
    aufgabe = ai_task_service.anlegen(
        db, user=user, felder={**TAEGLICH, "weekdays": [1, 3]}
    )
    ai_task_service.aendern(db, user=user, task_id=aufgabe.id, felder={
        "plan_kind": "interval", "interval_hours": 12,
    })
    db.commit()
    db.refresh(aufgabe)

    assert aufgabe.interval_hours == 12
    assert aufgabe.time_of_day is None
    assert aufgabe.weekdays is None
    assert ai_task_service.plan_text(aufgabe) == "alle 12 Stunden"


def test_eine_fremde_aufgabe_ist_nicht_erreichbar(db: Session) -> None:
    """Die Besitzpruefung steht in der Abfrage, nicht als `if` danach — eine
    fremde Aufgabe soll gar nicht erst in einer Variablen landen, aus der sie
    versehentlich in eine Vorschau geraet."""
    einer = _benutzer(db, "besitzer", "ai.tasks.manage")
    anderer = _benutzer(db, "fremder", "ai.tasks.manage")
    aufgabe = ai_task_service.anlegen(db, user=einer, felder=dict(TAEGLICH))
    db.commit()

    with pytest.raises(AiActionValidationError):
        ai_task_service.eigene_aufgabe(db, user=anderer, task_id=aufgabe.id)
    with pytest.raises(AiActionValidationError):
        ai_task_service.loeschen(db, user=anderer, task_id=aufgabe.id)
    with pytest.raises(AiActionValidationError):
        ai_task_service.aendern(
            db, user=anderer, task_id=aufgabe.id, felder={"enabled": False}
        )

    db.refresh(aufgabe)
    assert aufgabe.enabled is True


def test_loeschen_gibt_den_namen_zurueck(db: Session) -> None:
    """Damit die KI im Chat sagen kann, *was* sie geloescht hat — nach dem
    Loeschen ist die Zeile weg und der Name nicht mehr nachschlagbar."""
    user = _benutzer(db, "loeschen", "ai.tasks.manage")
    aufgabe = ai_task_service.anlegen(db, user=user, felder=dict(TAEGLICH))
    db.commit()

    assert ai_task_service.loeschen(db, user=user, task_id=aufgabe.id) == "Serverbericht"
    db.commit()
    assert db.query(AiTask).count() == 0


def test_eine_geratene_nummer_fuehrt_ins_leere(db: Session) -> None:
    """Und der Fehlertext sagt dem Modell, was es stattdessen tun soll — er
    landet als Werkzeugergebnis bei ihm und bestimmt den naechsten Versuch."""
    user = _benutzer(db, "geraten", "ai.tasks.manage")
    with pytest.raises(AiActionValidationError, match="list_tasks"):
        ai_task_service.eigene_aufgabe(db, user=user, task_id="gibtesnicht")


# ── Auflisten ─────────────────────────────────────────────────────────────


def test_die_auflistung_liefert_alles_in_einem_aufruf(db: Session) -> None:
    """Ergebnisse von Lesewerkzeugen fliessen nur aus dem juengsten Lauf in den
    Folgekontext. Ein Modell, das blaettern muesste, haette beim zweiten Aufruf
    den ersten schon vergessen."""
    user = _benutzer(db, "auflisten", "ai.tasks.manage")
    fremder = _benutzer(db, "nichtmeins", "ai.tasks.manage")
    ai_task_service.anlegen(db, user=user, felder={**TAEGLICH, "title": "Erste"})
    ai_task_service.anlegen(db, user=user, felder={
        **TAEGLICH, "title": "Zweite", "plan_kind": "interval", "interval_hours": 8,
    })
    ai_task_service.anlegen(db, user=fremder, felder={**TAEGLICH, "title": "Fremde"})
    db.commit()

    zeilen = ai_task_service.auflisten(db, user=user)

    assert [zeile["title"] for zeile in zeilen] == ["Erste", "Zweite"]
    assert zeilen[0]["plan"] == "taeglich um 08:00 (Europe/Berlin)"
    assert zeilen[1]["plan"] == "alle 8 Stunden"
    assert all(zeile["next_run"] is not None for zeile in zeilen)
    assert zeilen[0]["timezone"] == "Europe/Berlin"
