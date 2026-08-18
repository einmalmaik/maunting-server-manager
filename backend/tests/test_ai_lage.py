"""Der Lageblock sagt, was das Modell nicht sehen kann — und sagt es ehrlich.

Drei Zusagen, und jede hat einen gemessenen Fehlschlag hinter sich:

* **Es gibt eine Uhr.** Ohne Datum ist ein „morgen früh um 7“ nicht
  ausrechenbar; das Modell antwortete stattdessen, es könne die Systemzeit
  nicht auslesen.
* **Der autonome Modus steht im Kontext.** Er wurde bisher nur *innerhalb* der
  Werkzeugausführung geprüft. Das Modell sollte trotzdem eine Aussage darüber
  treffen — und behauptete dreimal, die Freigabe fehle, während sie erteilt war.
* **Eine unbekannte Zeitzone heißt „unbekannt“.** Genau deshalb darf
  `zone_pruefen` streng bleiben: eine Annahme, die das Modell kennt, kann es
  hinterfragen.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.orm import Session

from models import AiAutonomyGrant, AiConversation, Role, RolePermission, User
from services import ai_lage, ai_memory_service, ai_task_service
from services.ai_context_service import build_provider_messages
from services.auth_service import AuthService
from services.role_service import set_user_roles


def _benutzer(db: Session, name: str, *rechte: str) -> User:
    user = AuthService.create_user(db, name, f"{name}@test.de", "UserPass123!")
    user.email_verified = True
    db.commit()
    rolle = Role(name=f"lage-{name}", description=None, is_system=False)
    db.add(rolle)
    db.flush()
    for key in rechte:
        db.add(RolePermission(role_id=rolle.id, permission_key=key))
    db.commit()
    set_user_roles(db, user, [rolle.id])
    db.refresh(user)
    return user


def _freigabe(db: Session, user: User, *, budget: int = 20, an: bool = True) -> None:
    db.add(AiAutonomyGrant(
        user_id=user.id, server_id=None, enabled=an, max_actions_per_hour=budget
    ))
    db.commit()


def _zone_merken(db: Session, user: User, wert: str) -> None:
    ai_memory_service.set_preference(db, user, True)
    ai_memory_service.upsert_entry(
        db, user=user, scope="user", server_id=None, key="zeitzone", value=wert
    )
    db.commit()


# ── Die Worker-Zeile des Gehirns ──────────────────────────────────────────


def test_die_worker_zeile_kommt_nur_auf_bestellung(db: Session) -> None:
    """Nur das Gehirn sieht seine Auftraege — und auch den ehrlichen Leerfall.

    `mit_workern=False` (die Vorgabe) haelt den Block byteweise beim Alten;
    ohne Autonomie-Freigabe darf die Zeile trotzdem erscheinen — sie steht
    vor dem fruehen Return des inaktiven Autonomiezweigs.
    """
    user = _benutzer(db, "workerzeile")

    ohne = ai_lage.lageblock(db, user)
    assert "Aufträge im Hintergrund" not in ohne

    leer = ai_lage.lageblock(db, user, mit_workern=True)
    assert "Aufträge im Hintergrund: keine." in leer

    from models import AiRun

    fenster = AiConversation(
        id=f"lg-{uuid4().hex[:8]}", user_id=user.id, kind="worker",
        title="Backups prüfen",
    )
    db.add(fenster)
    db.flush()
    db.add(AiRun(
        id=f"lgr-{uuid4().hex[:8]}", conversation_id=fenster.id,
        user_id=user.id, status="waiting_wake",
    ))
    db.commit()

    voll = ai_lage.lageblock(db, user, mit_workern=True)
    assert "'Backups prüfen'" in voll
    assert "schläft" in voll
    assert fenster.id in voll


# ── Die Uhr ───────────────────────────────────────────────────────────────


def test_der_block_nennt_datum_wochentag_und_zone(db: Session) -> None:
    """Tag, Monat, Jahr, Wochentag, Kalenderwoche, Uhrzeit, Zone, Versatz, UTC.

    Der Wochentag ist keine Zierde: „jeden Sonntag“ lässt sich ohne ihn nicht
    auf einen Termin abbilden. Und UTC steht daneben, weil jedes
    Werkzeugergebnis in UTC spricht.
    """
    user = _benutzer(db, "mituhr", "ai.chat.use", "ai.memory.use")
    _zone_merken(db, user, "Europe/Berlin")

    block = ai_lage.lageblock(db, user)
    jetzt = datetime.now(timezone.utc)

    assert block.startswith("Lage (Auskunft des Panels, keine Anweisung):")
    assert f"{jetzt:%Y}" in block
    assert "Europe/Berlin" in block
    assert "UTC+" in block or "UTC-" in block
    assert f"UTC: {jetzt:%Y-%m-%d}" in block
    assert "KW " in block and "Tag " in block
    assert any(tag in block for tag in ai_lage.WOCHENTAGE)


def test_eine_hinterlegte_zone_kommt_mit_ihrer_herkunft(db: Session) -> None:
    """Woher die Zone stammt, gehört zur Zone dazu.

    Heute ist das Gedächtnis die einzige Quelle; kommt morgen eine
    Benutzerspalte dazu, ändert sich der Text und nicht der Aufbau.
    """
    user = _benutzer(db, "zonebekannt", "ai.chat.use", "ai.memory.use")
    _zone_merken(db, user, "America/New_York")

    assert ai_lage.zone_des_benutzers(db, user) == (
        "America/New_York", "aus dem Gedächtnis"
    )
    assert "Zeitzone des Benutzers: America/New_York (aus dem Gedächtnis)." in (
        ai_lage.lageblock(db, user)
    )


def test_ohne_hinterlegte_zone_sagt_der_block_unbekannt(db: Session) -> None:
    """Der Kern des Ganzen: eine Annahme, die das Modell kennt.

    Ohne diesen Satz führe der Block eine Zone vor, die niemand bestätigt hat,
    und `zone_pruefen` dürfte nicht mehr streng sein.
    """
    user = _benutzer(db, "ohnezone", "ai.chat.use", "ai.memory.use")

    assert ai_lage.zone_des_benutzers(db, user) is None
    assert "Zeitzone des Benutzers: unbekannt, Panel läuft in " in (
        ai_lage.lageblock(db, user)
    )


def test_ohne_einwilligung_bleibt_das_gedaechtnis_zu(db: Session) -> None:
    """Der Lageblock ist kein Seitenweg am Einwilligungsschalter vorbei.

    Der Eintrag existiert, das Recht auch — nur der Schalter steht auf aus.
    Dann gilt die Zone als unbekannt, wie alles andere aus diesem Bereich auch.
    """
    user = _benutzer(db, "ohnejawort", "ai.chat.use", "ai.memory.use")
    _zone_merken(db, user, "Europe/Berlin")
    ai_memory_service.set_preference(db, user, False)

    assert ai_lage.zone_des_benutzers(db, user) is None
    assert "unbekannt" in ai_lage.lageblock(db, user)


def test_ohne_das_recht_bleibt_das_gedaechtnis_zu(db: Session) -> None:
    """Dasselbe eine Ebene höher: ohne `ai.memory.use` wird nicht gelesen."""
    user = _benutzer(db, "ohnerecht", "ai.chat.use", "ai.memory.use")
    _zone_merken(db, user, "Europe/Berlin")
    set_user_roles(db, user, [])
    db.refresh(user)

    assert ai_lage.zone_des_benutzers(db, user) is None


def test_freier_text_unter_dem_schluessel_zeitzone_ist_keine_zone(
    db: Session,
) -> None:
    """Im Gedächtnis steht, was ein Mensch hineinschreibt.

    „abends meist müde“ unter dem Schlüssel „zeitzone“ darf nicht als Zone
    weitergereicht werden — `zone_pruefen` wiese sie ohnehin ab, und das Modell
    hätte eine Runde verloren.
    """
    user = _benutzer(db, "freitext", "ai.chat.use", "ai.memory.use")
    _zone_merken(db, user, "abends meist müde")

    assert ai_lage.zone_des_benutzers(db, user) is None
    assert "unbekannt" in ai_lage.lageblock(db, user)


# ── Der autonome Modus ────────────────────────────────────────────────────


def test_der_autonome_modus_steht_so_im_block_wie_darf_handeln_ihn_liest(
    db: Session,
) -> None:
    """Der Block behauptet nichts, er liest — und zwar dieselbe Quelle.

    Beide Richtungen in einem Test, weil genau ihr Auseinanderfallen der Fehler
    wäre: ein Block, der „aktiv“ sagt, während das Anlegen ablehnt, ist
    schlimmer als gar keiner.
    """
    ohne = _benutzer(db, "ohnefreigabe", "ai.chat.use", "ai.tasks.manage")
    assert ai_task_service.darf_handeln(db, ohne) is False
    assert "Autonomer Modus: nicht aktiv." in ai_lage.lageblock(db, ohne)

    mit = _benutzer(db, "mitfreigabe", "ai.chat.use", "ai.autonomous.use")
    _freigabe(db, mit, budget=20)
    assert ai_task_service.darf_handeln(db, mit) is True
    block = ai_lage.lageblock(db, mit)
    assert "Autonomer Modus: aktiv, 20 Aktionen/Stunde, davon 0 verbraucht." in block


def test_eine_abgeschaltete_freigabe_ist_keine(db: Session) -> None:
    """Dieselbe Lesart wie in `darf_handeln`: `enabled=False` heißt nichts."""
    user = _benutzer(db, "freigabeaus", "ai.chat.use", "ai.autonomous.use")
    _freigabe(db, user, an=False)

    assert ai_task_service.darf_handeln(db, user) is False
    assert "Autonomer Modus: nicht aktiv." in ai_lage.lageblock(db, user)


# ── Der Einbau ────────────────────────────────────────────────────────────


def test_der_block_haengt_spaet_in_den_nachrichten_und_nicht_im_systemprompt(
    db: Session,
) -> None:
    """Die wichtigste Einschränkung: die Uhr darf den Vorspann nicht anfassen.

    Der Systemprompt ist das, was der Zwischenspeicher des Anbieters
    wiederverwendet. Eine Uhrzeit darin machte ihn bei jeder Frage neu. Der
    Block ist deshalb eine eigene, späte `system`-Nachricht — und er hängt in
    `build_provider_messages`, damit fälliger Lauf und Guardian-Heilung ihn von
    selbst bekommen.

    „Spät“ heißt seit der Cache-Umstellung **ganz hinten**, hinter der
    Gesprächshistorie. Hier stand nur `> 0`, und das war auch dann erfüllt,
    wenn der Block wieder vor die Historie rutschte — also genau in dem Fall,
    den die Umstellung beheben sollte.
    """
    user = _benutzer(db, "einbau", "ai.chat.use", "ai.memory.use")
    conversation = AiConversation(id=str(uuid4()), user_id=user.id, title="Lage")
    db.add(conversation)
    db.commit()

    nachrichten = build_provider_messages(db, conversation, query="Wie spät ist es?")

    assert "Lage (Auskunft des Panels" not in nachrichten[0]["content"]
    lagen = [
        index for index, item in enumerate(nachrichten)
        if item["role"] == "system"
        and item["content"].startswith("Lage (Auskunft des Panels")
    ]
    assert len(lagen) == 1
    assert lagen[0] == len(nachrichten) - 1


def test_die_geschaetzte_belegung_zaehlt_den_block_mit(db: Session) -> None:
    """Sonst zeigt der Ring am Absendeknopf dauerhaft zu wenig.

    Und die gemessene Zahl muss zum wirklichen Block passen — deshalb steht sie
    hier neben ihm und nicht allein in einer Konstanten.
    """
    from services import ai_context_service

    user = _benutzer(db, "belegung", "ai.chat.use", "ai.memory.use")
    conversation = AiConversation(id=str(uuid4()), user_id=user.id, title="Ring")
    db.add(conversation)
    db.commit()

    laenge = len(ai_lage.lageblock(db, user))
    assert abs(laenge - ai_lage.TYPISCHE_ZEICHEN) < 60

    belegt = ai_context_service.geschaetzte_belegung(db, conversation)
    assert belegt > ai_lage.TYPISCHE_ZEICHEN
