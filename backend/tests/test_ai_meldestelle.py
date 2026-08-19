"""Die Meldestelle: schwaerzen, warten, buendeln, genau einmal zustellen.

docs/agentic-framework.md (Abschnitt 4). Die vier Zusagen des Moduls, jede
als eigener Test — und die Schema-Zusage der neuen Tabelle dazu, nach der
Hausregel: was die Datenbank durchsetzen soll, wird an der Datenbank geprueft.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import models  # noqa: F401
from config import settings
from database import Base
from models import (
    AiConversation,
    AiMailOutbox,
    AiMeldung,
    AiMessage,
    AiRun,
    Role,
    RolePermission,
    User,
)
from services import ai_meldestelle, ai_provider_service, ai_run_service
from services.role_service import set_user_roles


@pytest.fixture(autouse=True)
def _tippsignal_leeren():
    ai_meldestelle.zuruecksetzen_fuer_tests()
    yield
    ai_meldestelle.zuruecksetzen_fuer_tests()


def _benutzer(db: Session, name: str, *, mit_chat: bool = True) -> User:
    user = User(
        username=name,
        email_encrypted="x",
        email_hash=name,
        password_hash="x",
        is_active=True,
    )
    db.add(user)
    db.flush()
    if mit_chat:
        role = Role(name=f"melde-{name}", description=None, is_system=False)
        db.add(role)
        db.flush()
        db.add(RolePermission(role_id=role.id, permission_key="ai.chat.use"))
        db.commit()
        set_user_roles(db, user, [role.id])
    db.commit()
    return user


# ── Schwaerzung: der Choke-Point ──────────────────────────────────────────


def test_melden_schwaerzt_bevor_gespeichert_wird(db: Session) -> None:
    """Kein Klartext-Geheimnis erreicht je die Tabelle.

    Der Text stammt aus einem Worker-Lauf, der Logdateien fremder Server
    gelesen haben kann — die Schwaerzung sitzt deshalb im `melden()` selbst
    und nicht bei den Lesern.
    """
    user = _benutzer(db, "schwaerzer", mit_chat=False)

    meldung = ai_meldestelle.melden(
        db, user=user, text="Backup ok. Gefunden: password=hunter2 im Log."
    )

    assert "hunter2" not in meldung.text
    assert "[REDACTED]" in meldung.text
    zeile = db.query(AiMeldung).one()
    assert "hunter2" not in zeile.text


def test_eine_frage_wird_geschwaerzt_gespeichert(db: Session) -> None:
    user = _benutzer(db, "frager", mit_chat=False)

    meldung = ai_meldestelle.melden(
        db,
        user=user,
        text="Ich brauche eine Entscheidung.",
        art="frage",
        worker_id=None,
        question={"question": "Welt token=abc123FF loeschen?", "options": [
            {"label": "Ja"}, {"label": "Nein"},
        ]},
    )

    assert meldung.art == "frage"
    assert meldung.question_json is not None
    assert "abc123FF" not in meldung.question_json


# ── Der email-Kanal geht sofort, der Chat wartet ──────────────────────────


def test_der_mail_kanal_landet_sofort_im_ausgangskorb(db: Session) -> None:
    user = _benutzer(db, "mailer", mit_chat=False)

    ai_meldestelle.melden(
        db, user=user, text="Fertig.", kanal="both", worker_titel="Backups"
    )

    korb = db.query(AiMailOutbox).all()
    assert len(korb) == 1
    assert korb[0].anlass == "ai-worker-meldung"
    # Der Chat-Kanal wartet weiter auf Ruhe: die Marke ist nicht gesetzt.
    assert db.query(AiMeldung).one().zugestellt_at is None


def test_der_chat_kanal_erzeugt_keine_mail(db: Session) -> None:
    user = _benutzer(db, "nurchat", mit_chat=False)

    ai_meldestelle.melden(db, user=user, text="Fertig.", kanal="chat")

    assert db.query(AiMailOutbox).count() == 0


# ── Die Karenz: gelesen, nie gesetzt ──────────────────────────────────────


class TestKarenz:
    """Der einzige Weg, den Wert zu ändern, ist die Panel-Einstellung.

    Hier stand einmal ein `set_karenz_sekunden()`, das niemand rief — kein
    Router, kein Frontend, kein Test. Es ist gelöscht; geblieben ist der Leser
    samt Bereichswächter. Diese Tests halten fest, was er zusagt: eine Zahl
    innerhalb der Grenzen wird übernommen, alles andere fällt auf die Vorgabe
    zurück statt eine unsinnige Karenz in Betrieb zu nehmen.
    """

    @pytest.fixture(autouse=True)
    def _einstellung_leeren(self):
        from services.panel_settings_service import PanelSettingsService

        PanelSettingsService.invalidate_cache()
        yield
        PanelSettingsService.invalidate_cache()

    def test_ohne_eintrag_gilt_die_vorgabe(self, db: Session) -> None:
        assert ai_meldestelle.karenz_sekunden() == ai_meldestelle.STANDARD_KARENZ_S

    def test_ein_gepflegter_wert_wird_uebernommen(self, db: Session) -> None:
        from services.panel_settings_service import PanelSettingsService

        PanelSettingsService.set(ai_meldestelle.KARENZ_KEY, "7")

        assert ai_meldestelle.karenz_sekunden() == 7

    @pytest.mark.parametrize("roh", ["0", "999", "abc", "", "  "])
    def test_ein_unsinniger_wert_faellt_auf_die_vorgabe_zurueck(
        self, db: Session, roh: str
    ) -> None:
        """Notbremse gegen einen von Hand verdrehten Eintrag.

        Null Sekunden hiesse „grätsche sofort ins Gespräch", 999 hiesse
        „schweige eine Viertelstunde" — beides ist keine Karenz mehr. Weil es
        keine Maske gibt, die den Wert prüft, prüft ihn der Leser.
        """
        from services.panel_settings_service import PanelSettingsService

        PanelSettingsService.set(ai_meldestelle.KARENZ_KEY, roh)

        assert ai_meldestelle.karenz_sekunden() == ai_meldestelle.STANDARD_KARENZ_S

    def test_die_karenz_wird_nirgends_geschrieben(self) -> None:
        """Der tote Setter bleibt tot — und die Doku sagt dasselbe.

        Ein Setter ohne Aufrufer ist eine Zusage an den nächsten Leser, die
        niemand einlöst. Wer ihn wieder einführt, soll ihn im selben Zug auch
        verdrahten (Endpunkt, Recht, Maske) — dieser Test erinnert daran.
        """
        assert not hasattr(ai_meldestelle, "set_karenz_sekunden")


# ── Die Ruhe-Regel ────────────────────────────────────────────────────────


class TestRuhe:
    def test_ohne_bewegung_ist_ruhe(self, db: Session) -> None:
        user = _benutzer(db, "ruhig", mit_chat=False)
        assert ai_meldestelle.ruhe(db, user=user) is True

    def test_ein_aktiver_lauf_im_dauerchat_ist_keine_ruhe(self, db: Session) -> None:
        user = _benutzer(db, "beschaeftigt", mit_chat=False)
        fenster = AiConversation(
            id="ruhe-c1", user_id=user.id, kind="primary", title="Chat",
            updated_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        db.add(fenster)
        db.flush()
        db.add(AiRun(id="ruhe-r1", conversation_id=fenster.id, user_id=user.id, status="running"))
        db.commit()

        assert ai_meldestelle.ruhe(db, user=user) is False

    def test_ein_geparkter_worker_ist_keine_beschaeftigung(self, db: Session) -> None:
        """Die Ruhe fragt nach dem Gespraech, nicht nach den Auftraegen."""
        user = _benutzer(db, "wartend", mit_chat=False)
        fenster = AiConversation(
            id="ruhe-w1", user_id=user.id, kind="worker", title="Auftrag",
        )
        db.add(fenster)
        db.flush()
        db.add(AiRun(
            id="ruhe-r2", conversation_id=fenster.id, user_id=user.id,
            status="waiting_wake",
        ))
        db.commit()

        assert ai_meldestelle.ruhe(db, user=user) is True

    def test_tippen_haelt_die_karenz_offen(self, db: Session) -> None:
        user = _benutzer(db, "tipper", mit_chat=False)

        ai_meldestelle.tippen_melden(user.id)
        assert ai_meldestelle.ruhe(db, user=user) is False

    def test_eine_frische_nachricht_ist_keine_ruhe(self, db: Session) -> None:
        user = _benutzer(db, "leser", mit_chat=False)
        db.add(AiConversation(
            id="ruhe-c2", user_id=user.id, kind="primary", title="Chat",
            updated_at=datetime.now(timezone.utc),
        ))
        db.commit()

        assert ai_meldestelle.ruhe(db, user=user) is False


# ── Buendelung, Marke, Lieferlauf ─────────────────────────────────────────


def _vorflug_faelschen(db: Session):
    """Ein Vorflug ohne Netz: echter Provider, unbekanntes Fenster."""
    from services.ai_context_window import unbekannt

    anbieter = ai_provider_service.create_provider(
        db,
        name="Zustell-Zugang",
        provider_kind="openrouter",
        default_model="test-modell",
        enabled=True,
        requires_api_key=True,
        operator_api_key="sk-or-v1-test",
    )
    db.commit()

    flug = ai_run_service.Vorflug(
        anbieter=anbieter, denken=False, stufe=None, fenster=unbekannt()
    )

    async def _vorflug(client, db_, user_):
        return flug, anbieter

    return _vorflug


def test_zustellung_buendelt_und_markiert_vor_dem_lauf(db: Session) -> None:
    user = _benutzer(db, "empfaenger")
    ai_meldestelle.melden(db, user=user, text="Backups geprueft: alles gut.")
    ai_meldestelle.melden(
        db, user=user, text="Kalender aufgeraeumt.", art="ergebnis", worker_id=None
    )

    with (
        patch.object(ai_run_service, "http_client", lambda: object()),
        patch.object(ai_run_service, "vorflug", _vorflug_faelschen(db)),
        patch.object(ai_run_service, "anlauf", lambda db_, run: True),
    ):
        run = asyncio.run(ai_meldestelle.zustellung_anstossen(db, user=user))

    assert run is not None
    # Ein Lauf, nicht zwei: die Buendelung ist die Zusage.
    laeufe = (
        db.query(AiRun)
        .join(AiConversation, AiConversation.id == AiRun.conversation_id)
        .filter(AiConversation.kind == "primary", AiRun.user_id == user.id)
        .all()
    )
    assert len(laeufe) == 1
    # Beide Meldungen markiert — die Marke fiel vor dem Lauf.
    assert all(m.zugestellt_at is not None for m in db.query(AiMeldung).all())
    # Der Lieferauftrag traegt beide Texte und den Panel-Absender.
    nachricht = (
        db.query(AiMessage)
        .filter(AiMessage.conversation_id == run.conversation_id, AiMessage.role == "user")
        .one()
    )
    assert "Meldung des Panels" in nachricht.content
    assert "Backups geprueft" in nachricht.content
    assert "Kalender aufgeraeumt" in nachricht.content
    # **Und sie ist Maschinerie, kein Gespraech.** Der Text traegt eine
    # JSON-Nutzlast und eine Anweisung an das Gehirn; der Betreiber las das im
    # eigenen Chat, an sich selbst adressiert. Ein Worker arbeitet im
    # Hintergrund — seine Zettel gehoeren nicht in den Verlauf.
    assert nachricht.intern is True
    # Der Rahmen macht den Lauf als Lieferlauf kenntlich.
    ids = ai_run_service.zustand_lesen(run).get("meldung", {}).get("ids")
    assert sorted(ids) == sorted(m.id for m in db.query(AiMeldung).all())


def test_der_lieferauftrag_verlaesst_den_sichtbaren_verlauf(db: Session) -> None:
    """Sichtbar für das Modell, unsichtbar für den Menschen — beides zugleich.

    Das ist die eigentliche Zusage der Spalte, und sie hat zwei Hälften, die
    leicht auseinanderfallen:

    * Der **Verlauf** (`routers/ai_chat._verlauf_seite`) filtert sie heraus.
      Ohne das las der Betreiber im eigenen Chat eine JSON-Nutzlast und eine
      Anweisung an die KI, adressiert an ihn selbst.
    * Der **Kontext** (`ai_context_service.build_provider_messages`) behält
      sie. Nähme man sie auch dort weg, bekäme das Gehirn den Auftrag zu
      liefern und wüsste eine Runde später nicht mehr, warum es geliefert hat
      — es würde seine eigene Antwort für unbegründet halten.

    Deshalb prüft dieser Test beide Wege in einem Durchgang: die Hälften
    getrennt zu prüfen hieße, genau den Fehler zuzulassen, bei dem eine von
    beiden mitwandert.
    """
    from routers.ai_chat import _verlauf_seite
    from services import ai_context_service

    user = _benutzer(db, "leser")
    ai_meldestelle.melden(db, user=user, text="Backups geprueft: alles gut.")

    with (
        patch.object(ai_run_service, "http_client", lambda: object()),
        patch.object(ai_run_service, "vorflug", _vorflug_faelschen(db)),
        patch.object(ai_run_service, "anlauf", lambda db_, run: True),
    ):
        run = asyncio.run(ai_meldestelle.zustellung_anstossen(db, user=user))

    assert run is not None
    conversation = db.get(AiConversation, run.conversation_id)

    # 1) Der Browser sieht die Zeile nicht.
    seite = _verlauf_seite(db, conversation, None)
    sichtbar = [m.content for m in seite.messages]
    assert not any("Meldung des Panels" in text for text in sichtbar), (
        "die Maschinerie steht wieder im Verlauf"
    )
    assert not any("worker_meldungen" in text for text in sichtbar)

    # 2) Das Modell sieht sie sehr wohl.
    provider_messages = ai_context_service.build_provider_messages(
        db, conversation, query="Was ist mit den Backups?", rolle="gehirn",
    )
    im_kontext = "\n".join(
        str(nachricht.get("content") or "") for nachricht in provider_messages
    )
    assert "Backups geprueft" in im_kontext, (
        "ohne den Lieferauftrag im Kontext weiss das Gehirn nicht, warum es "
        "gerade berichtet"
    )


def test_eine_echte_nutzernachricht_bleibt_sichtbar(db: Session) -> None:
    """Die Gegenprobe: ``intern`` ist die Ausnahme, nicht die Regel.

    Ohne diesen Fall bestünde der Filter oben auch dann, wenn er versehentlich
    den ganzen Verlauf verschluckte.
    """
    from routers.ai_chat import _verlauf_seite
    from services import ai_chat_service

    user = _benutzer(db, "tipper")
    conversation = ai_chat_service.get_or_create_primary_conversation(db, user)
    db.add(AiMessage(
        id="msg-echt",
        conversation_id=conversation.id,
        role="user",
        content="Wie geht es meinen Servern?",
        status="complete",
    ))
    db.commit()

    seite = _verlauf_seite(db, conversation, None)

    assert [m.content for m in seite.messages] == ["Wie geht es meinen Servern?"]


def test_ohne_ruhe_wird_nichts_zugestellt(db: Session) -> None:
    user = _benutzer(db, "gestoert")
    ai_meldestelle.melden(db, user=user, text="Fertig.")
    ai_meldestelle.tippen_melden(user.id)

    with patch.object(ai_run_service, "http_client", lambda: object()):
        run = asyncio.run(ai_meldestelle.zustellung_anstossen(db, user=user))

    assert run is None
    assert db.query(AiMeldung).one().zugestellt_at is None


def test_der_sprachmodus_darf_die_chat_ruhe_ueberstimmen(db: Session) -> None:
    """``ruhe_noetig=False``: die Sprachbruecke bringt ihr eigenes Ruhe-Praedikat mit.

    Im Sprachmodus ersetzt der VAD-Zustand "bereit" die Chat-Ruhe — die
    Chat-Regel wuerde eine offene Sprachsitzung sogar faelschlich blockieren,
    weil jede Aeusserung ``conversation.updated_at`` bewegt und die Karenz nie
    ablaeuft. Das Tipp-Signal hier steht stellvertretend fuer diese falsche
    Blockade: mit ``ruhe_noetig=True`` liefert derselbe Aufbau nichts
    (`test_ohne_ruhe_wird_nichts_zugestellt`), ohne liefert er.
    """
    user = _benutzer(db, "sprecher")
    ai_meldestelle.melden(db, user=user, text="Fertig.")
    ai_meldestelle.tippen_melden(user.id)

    with (
        patch.object(ai_run_service, "http_client", lambda: object()),
        patch.object(ai_run_service, "vorflug", _vorflug_faelschen(db)),
        patch.object(ai_run_service, "anlauf", lambda db_, run: True),
    ):
        run = asyncio.run(
            ai_meldestelle.zustellung_anstossen(db, user=user, ruhe_noetig=False)
        )

    assert run is not None
    assert db.query(AiMeldung).one().zugestellt_at is not None
    # Auch die gesprochene Lieferung ist ein Gehirn-Zug — dieselbe Stimme,
    # derselbe Katalog wie im Chat, nicht die volle Werkzeugkiste.
    assert ai_run_service.zustand_lesen(run).get("rolle") == "gehirn"


def test_ein_kontrolliertes_scheitern_nimmt_die_marke_zurueck(db: Session) -> None:
    """Kontingent erschoepft ist eine Antwort, kein Absturz — die Meldung bleibt."""
    from services import ai_stream_service

    user = _benutzer(db, "kontingent")
    ai_meldestelle.melden(db, user=user, text="Fertig.")

    with (
        patch.object(ai_run_service, "http_client", lambda: object()),
        patch.object(ai_run_service, "vorflug", _vorflug_faelschen(db)),
        patch.object(
            ai_stream_service, "lauf_beginnen",
            lambda *a, **k: (None, ("kontingent",)),
        ),
    ):
        run = asyncio.run(ai_meldestelle.zustellung_anstossen(db, user=user))

    assert run is None
    assert db.query(AiMeldung).one().zugestellt_at is None


def test_ohne_chatrecht_bleibt_die_meldung_stehen(db: Session) -> None:
    user = _benutzer(db, "ohnechat", mit_chat=False)
    ai_meldestelle.melden(db, user=user, text="Fertig.")

    with patch.object(ai_run_service, "http_client", lambda: object()):
        run = asyncio.run(ai_meldestelle.zustellung_anstossen(db, user=user))

    assert run is None
    assert db.query(AiMeldung).one().zugestellt_at is None


# ── Der Abschlusshaken: lauf_beendet ──────────────────────────────────────


def _beendeter_worker(
    db: Session, user: User, *, status: str = "completed",
    stop_reason: str | None = "done", kanal: str = "chat",
) -> AiRun:
    from uuid import uuid4

    fenster = AiConversation(
        id=f"lb-{uuid4().hex[:8]}", user_id=user.id, kind="worker", title="Backups"
    )
    db.add(fenster)
    db.flush()
    run = AiRun(
        id=f"lbr-{uuid4().hex[:8]}", conversation_id=fenster.id,
        user_id=user.id, status=status, stop_reason=stop_reason,
    )
    db.add(run)
    db.commit()
    return run


def test_ein_beendeter_worker_reicht_sein_ergebnis_ein(db: Session) -> None:
    """Gemeldet wird bei jedem Endzustand, der ein Ergebnis ist — auch failed.

    Der Text ist der Abschlusstext des Modells (KI-erzeugt); die Zeile
    "Stand laut Panel" darunter ist Auskunft fuer das Gehirn, keine Phrase an
    den Menschen.
    """
    user = _benutzer(db, "haken", mit_chat=False)
    run = _beendeter_worker(db, user, kanal="email")
    db.add(AiMessage(
        id="lb-m1", conversation_id=run.conversation_id, role="assistant",
        content="Alle Backups sind aktuell, keine Luecken gefunden.",
        status="complete",
    ))
    db.commit()
    zustand = {
        "worker": {
            "conversation_id": run.conversation_id,
            "titel": "Backups",
            "kanal": "email",
        },
        "rounds": 3,
    }

    ai_meldestelle.lauf_beendet(db, run=run, zustand=zustand)

    meldung = db.query(AiMeldung).one()
    assert meldung.worker_id == run.conversation_id
    assert meldung.kanal == "email"
    assert "Backups sind aktuell" in meldung.text
    assert "erledigt" in meldung.text
    # `email` heisst zusaetzlich: die Mail liegt im Ausgangskorb.
    assert db.query(AiMailOutbox).count() == 1


def test_ein_gescheiterter_worker_meldet_ehrlich(db: Session) -> None:
    user = _benutzer(db, "ehrlich", mit_chat=False)
    run = _beendeter_worker(db, user, status="failed", stop_reason="AI_STREAM_FAILED")

    ai_meldestelle.lauf_beendet(
        db, run=run,
        zustand={"worker": {"conversation_id": run.conversation_id,
                            "titel": "Backups", "kanal": "chat"}},
    )

    meldung = db.query(AiMeldung).one()
    assert "nicht abgeschlossen" in meldung.text
    assert "AI_STREAM_FAILED" in meldung.text


def test_abgeloeste_und_eingefangene_laeufe_melden_nichts(db: Session) -> None:
    """`answered`, `superseded`, `worker_cancel`, `berechtigung_entzogen`,
    `process_restart`: die Auskunft gibt jeweils ein anderer — der Nachfolger,
    der Abbrechende oder der Wiederanlauf."""
    user = _benutzer(db, "still", mit_chat=False)
    for grund in ai_meldestelle.OHNE_MELDUNG:
        run = _beendeter_worker(db, user, status="cancelled", stop_reason=grund)
        ai_meldestelle.lauf_beendet(
            db, run=run,
            zustand={"worker": {"conversation_id": run.conversation_id,
                                "titel": "X", "kanal": "chat"}},
        )

    assert db.query(AiMeldung).count() == 0


def test_eine_angehaltene_runde_meldet_keinen_abbruch(db: Session) -> None:
    """Der gemeldete Fall: nachgereichte Werte, sauberer Lauf, Abbruchmeldung.

    Der Betreiber reichte einem laufenden Auftrag Werte nach. Alles lief
    durch — und trotzdem sagte ihm die KI:

        "Der Auftrag zur ASA-Konfiguration wurde abgebrochen und hat keine
        Zusammenfassung hinterlassen."

    Der Bestand zeigte, warum. Beim Abloesen markiert `aufgabe_abbrechen` die
    Zeile als `superseded` und haelt gleich darauf die asyncio-Aufgabe an; der
    Abschluss im Stream ueberschreibt den Grund dann mit `cancelled`. Und
    `cancelled` stand nicht in der stillen Liste:

        cancelled/cancelled  MELDET   <- die Falschmeldung
        completed/done       MELDET   <- das echte Ergebnis

    Das Gehirn hat beide Meldungen korrekt wiedergegeben. Es bekam nur eine
    zu viel.
    """
    user = _benutzer(db, "angehalten", mit_chat=False)
    run = _beendeter_worker(db, user, status="cancelled", stop_reason="cancelled")

    ai_meldestelle.lauf_beendet(
        db, run=run,
        zustand={"worker": {"conversation_id": run.conversation_id,
                            "titel": "ASA-XP-und-Ernte konfigurieren",
                            "kanal": "chat"}},
    )

    assert db.query(AiMeldung).count() == 0, (
        "die angehaltene Runde hat gemeldet — genau daraus entstand die "
        "erfundene Abbruchmeldung an den Betreiber"
    )


def test_ein_echtes_ergebnis_meldet_weiterhin(db: Session) -> None:
    """Die Gegenprobe: der Nachfolger berichtet ganz normal.

    Ohne sie koennte `OHNE_MELDUNG` unbemerkt so weit wachsen, dass gar nichts
    mehr beim Benutzer ankommt.
    """
    user = _benutzer(db, "berichtet", mit_chat=False)
    run = _beendeter_worker(db, user, status="completed", stop_reason="done")

    ai_meldestelle.lauf_beendet(
        db, run=run,
        zustand={"worker": {"conversation_id": run.conversation_id,
                            "titel": "ASA-XP-und-Ernte konfigurieren",
                            "kanal": "chat"}},
    )

    assert db.query(AiMeldung).count() == 1


# ── Schema-Zusagen der neuen Tabelle ──────────────────────────────────────


def test_die_datenbank_kennt_nur_die_zwei_meldungsarten(db: Session) -> None:
    from models.ai_meldung import MELDUNGSARTEN

    assert MELDUNGSARTEN == ("ergebnis", "frage")
    user = _benutzer(db, "artpruefung", mit_chat=False)

    with pytest.raises(IntegrityError):
        db.execute(
            text(
                "INSERT INTO ai_meldungen (id, user_id, art, kanal, text, created_at) "
                "VALUES ('m-x', :uid, 'prozess', 'chat', 'x', '2026-08-18')"
            ),
            {"uid": user.id},
        )
    db.rollback()


def test_eine_meldung_ueberlebt_ihr_worker_fenster(db: Session) -> None:
    """SET NULL, nicht CASCADE: die Meldung ist ein Beleg an den Menschen."""
    user = _benutzer(db, "beleg", mit_chat=False)
    fenster = AiConversation(id="beleg-w1", user_id=user.id, kind="worker", title="A")
    db.add(fenster)
    db.commit()
    meldung = ai_meldestelle.melden(
        db, user=user, text="Fertig.", worker_id=fenster.id
    )

    db.delete(fenster)
    db.commit()

    db.refresh(meldung)
    assert meldung.worker_id is None
    assert meldung.text == "Fertig."


def _frisch(engine):
    engine.dispose()
    return inspect(engine)


def test_die_migration_traegt_die_meldungstabelle(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'meldungen.db'}"
    vorher = settings.database_url
    settings.database_url = db_url
    backend_dir = Path(__file__).resolve().parent.parent
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "migrations"))
    engine = create_engine(db_url)
    try:
        Base.metadata.create_all(engine)
        command.stamp(config, "head")

        command.downgrade(config, "20260818_02")
        assert "ai_meldungen" not in _frisch(engine).get_table_names()

        command.upgrade(config, "head")
        inspector = _frisch(engine)
        assert "ai_meldungen" in inspector.get_table_names()
        indizes = {i["name"] for i in inspector.get_indexes("ai_meldungen")}
        assert "ix_ai_meldungen_user_zugestellt" in indizes
    finally:
        engine.dispose()
        settings.database_url = vorher
