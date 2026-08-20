"""Der Takt: wann eine Aufgabe faellig ist — und wann ausdruecklich nicht.

Der Takt ist die Stelle, an der ein stehender Auftrag zu einem Lauf wird. Er
laeuft jede Minute, unbeaufsichtigt, und jede seiner Entscheidungen wirkt auf
etwas, das der Betreiber erst Stunden spaeter sieht. Drei davon sind es wert,
einzeln zugesichert zu werden:

* **Vertagen** heisst: der Termin bleibt stehen. Wer den Anspruch schon genommen
  haette, haette den Termin verbrannt, nur weil der Mensch gerade tippte.
* **Anspruch nehmen** heisst: der Termin ist weitergeschaltet, *bevor* der Lauf
  beginnt. Ohne das findet ein Absturz mitten im Lauf beim naechsten Durchlauf
  denselben faelligen Termin wieder — und wieder, und wieder.
* **Ueberspringen** heisst: ein zu alter Termin wird nicht nachgeholt. Ein um
  elf Uhr nachgeholtes Nachtbackup ist schlechter als gar keines.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from models import AiProvider, AiRun, AiTask, Role, RolePermission, User
from services import ai_run_service, ai_task_service
from services.ai_limit_service import LIMIT_FIELDS, set_role_limit
from services.auth_service import AuthService
from services.role_service import set_user_roles


_KEIN_CLIENT = object()

KI_RECHTE = ("ai.chat.use", "ai.tasks.manage")


def _benutzer(db: Session, name: str) -> User:
    user = AuthService.create_user(db, name, f"{name}@test.de", "UserPass123!")
    user.email_verified = True
    db.commit()
    rolle = Role(name=f"takt-{name}", description=None, is_system=False)
    db.add(rolle)
    db.flush()
    for key in KI_RECHTE:
        db.add(RolePermission(role_id=rolle.id, permission_key=key))
    set_role_limit(db, rolle.id, {field: None for field in LIMIT_FIELDS})
    db.commit()
    set_user_roles(db, user, [rolle.id])
    db.refresh(user)
    return user


def _anbieter(db: Session, name: str = "Takt-Anbieter") -> AiProvider:
    provider = AiProvider(
        name=name,
        provider_kind="openrouter",
        default_model="model-a",
        enabled=True,
        requires_api_key=False,
    )
    db.add(provider)
    db.commit()
    db.refresh(provider)
    return provider


def _aufgabe(db: Session, user: User, **felder) -> AiTask:
    vorgabe = {
        "title": "Serverbericht",
        "instruction": "Sieh nach den Servern.",
        "kind": "report",
        "plan_kind": "daily",
        "time_of_day": "08:00",
        "timezone": "Europe/Berlin",
        "channel": "chat",
    }
    vorgabe.update(felder)
    aufgabe = ai_task_service.anlegen(db, user=user, felder=vorgabe)
    db.commit()
    db.refresh(aufgabe)
    return aufgabe


def _faellig_machen(db: Session, aufgabe: AiTask, *, vor_minuten: float = 0) -> datetime:
    """Setzt den Termin in die Vergangenheit — das tut sonst die Zeit.

    Ueber die Datenbank und nicht ueber `naechste_faelligkeit`: ein Test, der die
    Uhr vorstellt, prueft die Uhr. Hier geht es um das, was der Takt mit einem
    faelligen Termin macht.
    """
    termin = datetime.now(timezone.utc) - timedelta(minutes=vor_minuten)
    aufgabe.next_run_at = termin
    if aufgabe.plan_kind == "once":
        # Bei einem einmaligen Auftrag **ist** `once_at` der Termin. Nur
        # `next_run_at` vorzustellen erzeugte einen Zustand, den die Zeit nie
        # herstellt — und der Test haette dann diesen Zustand gemessen statt
        # den Takt.
        aufgabe.once_at = termin
    db.commit()
    db.refresh(aufgabe)
    return aufgabe.next_run_at


def _laufzeit_faelschen(monkeypatch, *, client=_KEIN_CLIENT):
    monkeypatch.setattr(ai_run_service, "http_client", lambda: client)


class Starts:
    """Faengt `aufgabenlauf_starten` ab und merkt sich, wer drankam.

    Der Start selbst hat eine eigene Datei (`test_ai_task_lauf.py`). Hier steht
    nur die Auswahl unter Beobachtung — welche Aufgabe drankommt, welche
    vertagt, welche uebersprungen wird.
    """

    def __init__(self, *, erfolg: bool = True):
        self.erfolg = erfolg
        self.gestartet: list[str] = []

    def einbauen(self, monkeypatch) -> "Starts":
        async def fake(db, *, aufgabe):
            self.gestartet.append(aufgabe.id)
            return AiRun(id=f"run-{aufgabe.id}") if self.erfolg else None

        monkeypatch.setattr(ai_task_service, "aufgabenlauf_starten", fake)
        return self


# ── Auswahl ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_eine_faellige_aufgabe_wird_gestartet(db: Session, monkeypatch) -> None:
    user = _benutzer(db, "faellig")
    _anbieter(db)
    _laufzeit_faelschen(monkeypatch)
    starts = Starts().einbauen(monkeypatch)
    aufgabe = _aufgabe(db, user)
    _faellig_machen(db, aufgabe)

    assert await ai_task_service.faellige_aufgaben_bearbeiten(db) == 1
    assert starts.gestartet == [aufgabe.id]


@pytest.mark.asyncio
async def test_eine_aufgabe_in_der_zukunft_bleibt_liegen(db: Session, monkeypatch) -> None:
    user = _benutzer(db, "spaeter")
    _anbieter(db)
    _laufzeit_faelschen(monkeypatch)
    starts = Starts().einbauen(monkeypatch)
    _aufgabe(db, user)

    assert await ai_task_service.faellige_aufgaben_bearbeiten(db) == 0
    assert starts.gestartet == []


@pytest.mark.asyncio
async def test_eine_pausierte_aufgabe_wird_nicht_faellig(db: Session, monkeypatch) -> None:
    """Das ist der Unterschied zwischen abschalten und loeschen.

    Der Betreiber hat sich ausdruecklich beides gewuenscht: eine Aufgabe, die
    nicht mehr laeuft und trotzdem noch da ist.
    """
    user = _benutzer(db, "pausiert")
    _anbieter(db)
    _laufzeit_faelschen(monkeypatch)
    starts = Starts().einbauen(monkeypatch)
    aufgabe = _aufgabe(db, user)
    _faellig_machen(db, aufgabe)
    aufgabe.enabled = False
    db.commit()

    assert await ai_task_service.faellige_aufgaben_bearbeiten(db) == 0
    assert starts.gestartet == []


@pytest.mark.asyncio
async def test_ohne_laufzeit_sieht_der_takt_gar_nicht_erst_nach(
    db: Session, monkeypatch
) -> None:
    """Jeder einzelne Start wuerde daran scheitern — und dabei Termine verbrennen."""
    user = _benutzer(db, "keinelaufzeit")
    _anbieter(db)
    _laufzeit_faelschen(monkeypatch, client=None)
    starts = Starts().einbauen(monkeypatch)
    aufgabe = _aufgabe(db, user)
    vorher = _faellig_machen(db, aufgabe)

    assert await ai_task_service.faellige_aufgaben_bearbeiten(db) == 0
    assert starts.gestartet == []
    db.refresh(aufgabe)
    assert aufgabe.next_run_at == vorher


# ── Kein Vertagen mehr ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ein_chattender_mensch_haelt_den_takt_nicht_mehr_auf(
    db: Session, monkeypatch
) -> None:
    """Hier stand bis zum 20.08.2026 das Vertagen: solange im Dauerchat ein
    Lauf aktiv war, blieb der Termin stehen. Seit die Aufgaben in einem
    eigenen Hintergrundfenster laufen (Betreiber-Vorgabe: das Gespraech wird
    nie unterbrochen, im Dauerchat steht nur, was der Mensch schreibt), ist
    ein aktiver Chat-Lauf kein Grund mehr — die Aufgabe startet, der Termin
    schaltet weiter. Dass der Lauf dem Gespraech nicht ins Wort faellt,
    sichert test_ai_task_lauf ueber das eigene Fenster zu.
    """
    user = _benutzer(db, "chattet")
    _anbieter(db)
    _laufzeit_faelschen(monkeypatch)
    starts = Starts().einbauen(monkeypatch)
    aufgabe = _aufgabe(db, user)
    vorher = _faellig_machen(db, aufgabe)

    def _laeuft(db, *, user_id, kind=None):
        return AiRun(id="laeuft")

    monkeypatch.setattr(ai_run_service, "aktiver_lauf", _laeuft)

    assert await ai_task_service.faellige_aufgaben_bearbeiten(db) == 1
    assert starts.gestartet == [aufgabe.id]
    db.refresh(aufgabe)
    # Der Termin ist weitergeschaltet — nicht verbrannt und nicht stehen
    # geblieben.
    assert ai_task_service.utc(aufgabe.next_run_at) > ai_task_service.utc(vorher)


# ── Anspruch nehmen ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_der_termin_ist_weitergeschaltet_bevor_der_lauf_beginnt(
    db: Session, monkeypatch
) -> None:
    """Die Schranke gegen die heisse Schleife.

    Faellt der Prozess mitten im Lauf, findet der naechste Durchlauf einen
    Termin in der Zukunft — und nicht denselben faelligen Termin ein weiteres
    Mal, und noch eines, jede Minute, mit einem Anbieteraufruf je Runde.
    """
    user = _benutzer(db, "anspruch")
    _anbieter(db)
    _laufzeit_faelschen(monkeypatch)
    aufgabe = _aufgabe(db, user)
    gesehen: list[datetime | None] = []

    async def fake(db, *, aufgabe):
        db.refresh(aufgabe)
        gesehen.append(aufgabe.next_run_at)
        return AiRun(id="run-1")

    monkeypatch.setattr(ai_task_service, "aufgabenlauf_starten", fake)
    vorher = _faellig_machen(db, aufgabe)

    await ai_task_service.faellige_aufgaben_bearbeiten(db)

    assert gesehen and gesehen[0] is not None
    assert ai_task_service.utc(gesehen[0]) > ai_task_service.utc(vorher)


@pytest.mark.asyncio
async def test_zwei_gleichzeitige_durchlaeufe_starten_einen_lauf(
    db: Session, monkeypatch
) -> None:
    """Nicht ueber eine Sperre, sondern ueber die Bedingung im UPDATE.

    Wer genau den Termin vorfindet, den er gelesen hat, hat ihn. Der zweite
    findet ihn nicht mehr vor und geht leer aus.
    """
    user = _benutzer(db, "gleichzeitig")
    _anbieter(db)
    _laufzeit_faelschen(monkeypatch)
    aufgabe = _aufgabe(db, user)
    gelesen = _faellig_machen(db, aufgabe)
    neu = ai_task_service.naechste_faelligkeit(aufgabe, ab=ai_task_service._jetzt())

    erster = ai_task_service._anspruch_nehmen(db, aufgabe, gelesen=gelesen, neu=neu)
    zweiter = ai_task_service._anspruch_nehmen(db, aufgabe, gelesen=gelesen, neu=neu)

    assert erster is True
    assert zweiter is False


# ── Ueberspringen ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ein_zu_alter_termin_wird_uebersprungen_statt_nachgeholt(
    db: Session, monkeypatch
) -> None:
    """Das Panel war aus. Ein Backup von heute Nacht laeuft nicht um elf.

    Der Termin wird trotzdem weitergeschaltet — sonst stuende die Aufgabe bis in
    alle Ewigkeit auf demselben verpassten Morgen.
    """
    user = _benutzer(db, "verspaetet")
    _anbieter(db)
    _laufzeit_faelschen(monkeypatch)
    starts = Starts().einbauen(monkeypatch)
    aufgabe = _aufgabe(db, user)
    alt = _faellig_machen(db, aufgabe, vor_minuten=ai_task_service.MAX_VERZUG_MINUTEN + 5)

    assert await ai_task_service.faellige_aufgaben_bearbeiten(db) == 0
    assert starts.gestartet == []
    db.refresh(aufgabe)
    assert ai_task_service.utc(aufgabe.next_run_at) > ai_task_service.utc(alt)
    assert aufgabe.enabled is True


@pytest.mark.asyncio
async def test_ein_knapp_verspaeteter_termin_laeuft_noch(db: Session, monkeypatch) -> None:
    """Die Grenze liegt bei einer Stunde und nicht bei einer Minute.

    Ein Takt von 60 Sekunden, ein Lauf des Vorgaengers, ein kurzer Neustart —
    kleine Verspaetungen sind der Normalfall und kein Grund, den Auftrag des
    Betreibers ausfallen zu lassen.
    """
    user = _benutzer(db, "knapp")
    _anbieter(db)
    _laufzeit_faelschen(monkeypatch)
    starts = Starts().einbauen(monkeypatch)
    aufgabe = _aufgabe(db, user)
    _faellig_machen(db, aufgabe, vor_minuten=ai_task_service.MAX_VERZUG_MINUTEN - 5)

    assert await ai_task_service.faellige_aufgaben_bearbeiten(db) == 1
    assert starts.gestartet == [aufgabe.id]


# ── Der einmalige Termin ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ein_einmaliger_auftrag_ist_nach_dem_lauf_vorbei(
    db: Session, monkeypatch
) -> None:
    user = _benutzer(db, "einmal")
    _anbieter(db)
    _laufzeit_faelschen(monkeypatch)
    Starts().einbauen(monkeypatch)
    morgen = datetime.now(timezone.utc) + timedelta(days=1)
    aufgabe = _aufgabe(
        db, user, plan_kind="once", time_of_day=None, once_at=morgen.isoformat()
    )
    _faellig_machen(db, aufgabe)

    assert await ai_task_service.faellige_aufgaben_bearbeiten(db) == 1
    db.refresh(aufgabe)
    assert aufgabe.next_run_at is None
    # Die Zeile bleibt stehen: der Betreiber soll sehen, dass sie gelaufen ist.
    assert db.query(AiTask).filter(AiTask.id == aufgabe.id).count() == 1


@pytest.mark.asyncio
async def test_ein_einmaliger_auftrag_verfaellt_nicht_still_wenn_nichts_lief(
    db: Session, monkeypatch
) -> None:
    """Kein Anbieter, kein Kontingent — und der Termin waere weg gewesen.

    Bei einem taeglichen Plan kostet das eine Ausgabe. Bei "erinnere mich morgen
    um drei" kostet es genau die Sache, um die gebeten wurde.
    """
    user = _benutzer(db, "einmalgescheitert")
    _anbieter(db)
    _laufzeit_faelschen(monkeypatch)
    Starts(erfolg=False).einbauen(monkeypatch)
    morgen = datetime.now(timezone.utc) + timedelta(days=1)
    aufgabe = _aufgabe(
        db, user, plan_kind="once", time_of_day=None, once_at=morgen.isoformat()
    )
    vorher = _faellig_machen(db, aufgabe)

    assert await ai_task_service.faellige_aufgaben_bearbeiten(db) == 0
    db.refresh(aufgabe)
    assert aufgabe.next_run_at == vorher


@pytest.mark.asyncio
async def test_ein_stillgelegter_einmaliger_auftrag_wird_nicht_wiederbelebt(
    db: Session, monkeypatch
) -> None:
    """`aufgabenlauf_starten` gibt auch dann ``None``, wenn es abschaltet.

    Beides zu verwechseln haette die Aufgabe genau in dem Fall wieder faellig
    gemacht, in dem ihre Voraussetzung weggefallen ist.
    """
    user = _benutzer(db, "einmalstill")
    _anbieter(db)
    _laufzeit_faelschen(monkeypatch)
    morgen = datetime.now(timezone.utc) + timedelta(days=1)
    aufgabe = _aufgabe(
        db, user, plan_kind="once", time_of_day=None, once_at=morgen.isoformat()
    )
    _faellig_machen(db, aufgabe)

    async def fake(db, *, aufgabe):
        ai_task_service._stilllegen(db, aufgabe, grund="test")
        return None

    monkeypatch.setattr(ai_task_service, "aufgabenlauf_starten", fake)

    assert await ai_task_service.faellige_aufgaben_bearbeiten(db) == 0
    db.refresh(aufgabe)
    assert aufgabe.enabled is False
    assert aufgabe.next_run_at is None


# ── Grenzen und Robustheit ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ein_durchlauf_beginnt_hoechstens_die_erlaubte_zahl(
    db: Session, monkeypatch
) -> None:
    """Jeder Lauf ist ein Anbieteraufruf, und der Takt schlaegt jede Minute zu."""
    _anbieter(db)
    _laufzeit_faelschen(monkeypatch)
    starts = Starts().einbauen(monkeypatch)
    grenze = ai_task_service.MAX_AUFGABEN_JE_DURCHLAUF
    for nummer in range(grenze + 3):
        user = _benutzer(db, f"viele{nummer}")
        aufgabe = _aufgabe(db, user, title=f"Auftrag {nummer}")
        _faellig_machen(db, aufgabe, vor_minuten=nummer)

    assert await ai_task_service.faellige_aufgaben_bearbeiten(db) == grenze
    assert len(starts.gestartet) == grenze


@pytest.mark.asyncio
async def test_die_aelteste_faelligkeit_kommt_zuerst(db: Session, monkeypatch) -> None:
    _anbieter(db)
    _laufzeit_faelschen(monkeypatch)
    starts = Starts().einbauen(monkeypatch)
    erst = _aufgabe(db, _benutzer(db, "aelter"), title="Aelter")
    dann = _aufgabe(db, _benutzer(db, "juenger"), title="Juenger")
    _faellig_machen(db, dann, vor_minuten=1)
    _faellig_machen(db, erst, vor_minuten=30)

    await ai_task_service.faellige_aufgaben_bearbeiten(db)

    assert starts.gestartet == [erst.id, dann.id]


@pytest.mark.asyncio
async def test_eine_kaputte_aufgabe_nimmt_die_uebrigen_nicht_mit(
    db: Session, monkeypatch
) -> None:
    """Der Auftrag im Scheduler darf nie durchschlagen — und der Takt auch nicht."""
    _anbieter(db)
    _laufzeit_faelschen(monkeypatch)
    kaputt = _aufgabe(db, _benutzer(db, "kaputt"), title="Kaputt")
    heil = _aufgabe(db, _benutzer(db, "heil"), title="Heil")
    _faellig_machen(db, kaputt, vor_minuten=10)
    _faellig_machen(db, heil, vor_minuten=1)
    gestartet: list[str] = []

    async def fake(db, *, aufgabe):
        if aufgabe.id == kaputt.id:
            raise RuntimeError("etwas ging schief")
        gestartet.append(aufgabe.id)
        return AiRun(id="run-heil")

    monkeypatch.setattr(ai_task_service, "aufgabenlauf_starten", fake)

    assert await ai_task_service.faellige_aufgaben_bearbeiten(db) == 1
    assert gestartet == [heil.id]


@pytest.mark.asyncio
async def test_der_auftrag_im_scheduler_haelt_alles_zurueck(monkeypatch) -> None:
    """Ein Fehler im Ausloeser darf den Scheduler nicht aus dem Takt bringen."""
    from services import scheduler_service

    async def fake(db):
        raise RuntimeError("Datenbank weg")

    monkeypatch.setattr(ai_task_service, "faellige_aufgaben_bearbeiten", fake)

    # Kein `pytest.raises`: die Zusage ist, dass gar nichts herauskommt.
    await scheduler_service._ai_tasks_task()
