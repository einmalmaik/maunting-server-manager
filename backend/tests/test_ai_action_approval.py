"""Freigabe per E-Mail — was der Link darf und was er nicht darf.

Der Anlass steht in `services/ai_approval_service`: ein unbeaufsichtigter Lauf
endete an dem Punkt, an dem einer seiner Vorschlaege eine Bestaetigung brauchte.
Der Server blieb kaputt, und die Mail sagte "konnte nicht behoben werden" —
obwohl der Betreiber ein Telefon in der Tasche hat.

Was hier geprueft wird, ist die andere Haelfte davon: dass aus diesem Link kein
zweiter, schwaecherer Weg ins Panel wird. Sechs Zusagen, jede mit einem
eigenen Test:

* **Einmalverbrauch**, atomar — zwei Klicks sind nicht zwei Ausfuehrungen.
* **Eine einzige Fehlermeldung** fuer unbekannt, abgelaufen und verbraucht.
  Drei verschiedene Antworten sagten einem Fremden, welche Token es gibt.
* **Kein GET, das ausfuehrt.** Mailscanner klicken Links.
* **Wecken bei Zustimmung *und* bei Ablehnung.** Ein abgelehnter Vorschlag ist
  fuer die KI genauso eine Auskunft wie ein ausgefuehrter.
* **Der Rueckfall bleibt.** Ohne Adresse wird zurueckgenommen und beendet, genau
  wie vorher — ein Lauf, der auf eine Antwort wartet, die nie kommen kann, waere
  schlimmer als einer, der ehrlich aufhoert.
* **Die Kette bricht nicht ab.** Traegt ein Lauf mehrere Vorschlaege, folgt auf
  jede Entscheidung die naechste Frage — eine zur Zeit, geweckt wird nach der
  letzten.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Query, Session

from models import (
    AiActionApproval,
    AiActionProposal,
    AiConversation,
    AiRun,
    Role,
    RolePermission,
    Server,
    ServerPermission,
    User,
)
from models.ai_action_approval import hash_approval_token
from services import ai_approval_service
from services.ai_action_errors import AiActionStateError
from services.role_service import set_user_roles


def _jetzt() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture
def lage(db: Session, regular_user: User):
    """Ein Benutzer mit Chatrecht, ein Server, eine Unterhaltung, ein Lauf."""
    rolle = Role(name="freigabe-test", description=None, is_system=False)
    db.add(rolle)
    db.flush()
    db.add(RolePermission(role_id=rolle.id, permission_key="ai.chat.use"))
    db.commit()
    set_user_roles(db, regular_user, [rolle.id])

    server = Server(
        name="Freigabe-Server",
        game_type="dayz",
        install_dir="/tmp/freigabe",
        container_name="msm-freigabe",
        status="running",
    )
    db.add(server)
    db.commit()
    db.refresh(server)
    for key in ("server.view", "server.restart", "server.files.write"):
        db.add(
            ServerPermission(
                user_id=regular_user.id, server_id=server.id, permission_key=key
            )
        )
    conversation = AiConversation(
        id=str(uuid4()), user_id=regular_user.id, server_id=None, title="Guardian"
    )
    db.add(conversation)
    db.commit()

    run = AiRun(
        id=str(uuid4()),
        conversation_id=conversation.id,
        user_id=regular_user.id,
        status="waiting_confirmation",
    )
    db.add(run)
    db.commit()
    return regular_user, server, conversation, run


def _vorschlag(db: Session, lage, *, status: str = "proposed") -> AiActionProposal:
    user, server, conversation, run = lage
    zeile = AiActionProposal(
        id=str(uuid4()),
        conversation_id=conversation.id,
        user_id=user.id,
        server_id=server.id,
        tool_name="propose_file_delete",
        payload_encrypted="test-enc-v1::7b7d",
        preview_json='{"operation":"file_delete","path":"broken.cfg"}',
        requires_confirmation=True,
        status=status,
        correlation_id=str(uuid4()),
        run_id=run.id,
        reason="Die Datei ist kaputt.",
        expected_effect="Der Server startet wieder.",
    )
    db.add(zeile)
    db.commit()
    return zeile


def _freigabe(
    db: Session, lage, vorschlag: AiActionProposal, *, stunden: int = 24
) -> tuple[AiActionApproval, str]:
    user, _, _, run = lage
    token = "t-" + uuid4().hex
    zeile = AiActionApproval(
        id=str(uuid4()),
        token_hash=hash_approval_token(token),
        proposal_id=vorschlag.id,
        run_id=run.id,
        user_id=user.id,
        expires_at=_jetzt() + timedelta(hours=stunden),
    )
    db.add(zeile)
    db.commit()
    return zeile, token


class TestEinmalverbrauch:
    def test_der_zweite_klick_findet_nichts_mehr(self, db: Session, lage) -> None:
        """Zwei Klicks auf denselben Link sind nicht zwei Ausfuehrungen.

        Verbraucht wird per bedingtem UPDATE auf ``consumed_at IS NULL`` — auf
        SQLite gibt es kein ``SELECT ... FOR UPDATE``, und die Zeilenzahl der
        Antwort ist die Sperre.
        """
        vorschlag = _vorschlag(db, lage)
        _, token = _freigabe(db, lage, vorschlag)

        erste = ai_approval_service.entscheiden(
            db, token=token, entscheidung="rejected"
        )
        assert erste["decision"] == "rejected"

        with pytest.raises(AiActionStateError) as fehler:
            ai_approval_service.entscheiden(db, token=token, entscheidung="approved")
        assert str(fehler.value) == "AI_APPROVAL_INVALID"

    def test_abgelaufen_und_unbekannt_melden_dasselbe(self, db: Session, lage) -> None:
        """Drei verschiedene Antworten sagten einem Fremden, welche Token es gibt."""
        vorschlag = _vorschlag(db, lage)
        zeile, token = _freigabe(db, lage, vorschlag)
        zeile.expires_at = _jetzt() - timedelta(minutes=1)
        db.commit()

        with pytest.raises(AiActionStateError) as abgelaufen:
            ai_approval_service.entscheiden(db, token=token, entscheidung="approved")
        with pytest.raises(AiActionStateError) as unbekannt:
            ai_approval_service.entscheiden(
                db, token="gibt-es-nicht", entscheidung="approved"
            )
        assert str(abgelaufen.value) == str(unbekannt.value) == "AI_APPROVAL_INVALID"

    def test_lesen_verbraucht_nicht(self, db: Session, lage) -> None:
        """`freigabe_lesen` ist der GET-Pfad — er darf nichts anfassen."""
        vorschlag = _vorschlag(db, lage)
        zeile, token = _freigabe(db, lage, vorschlag)

        assert ai_approval_service.freigabe_lesen(db, token) is not None
        assert ai_approval_service.freigabe_lesen(db, token) is not None
        db.refresh(zeile)
        assert zeile.consumed_at is None
        assert zeile.decision is None


class TestEntscheidung:
    def test_ablehnen_setzt_den_vorschlag_ab_und_weckt_den_lauf(
        self, db: Session, lage
    ) -> None:
        """Ablehnen ist eine Auskunft, kein Schweigen.

        Ohne das Wecken stuende der Lauf bis zu seiner Frist auf
        ``waiting_confirmation``, und die KI erfuehre nie, dass sie einen
        anderen Weg suchen soll.
        """
        vorschlag = _vorschlag(db, lage)
        _, token = _freigabe(db, lage, vorschlag)

        with patch("services.ai_run_service.lauf_fortsetzen") as wecken:
            ai_approval_service.entscheiden(db, token=token, entscheidung="rejected")

        db.refresh(vorschlag)
        assert vorschlag.status == "expired"
        assert vorschlag.error_code == "AI_APPROVAL_REJECTED"
        assert wecken.called

    def test_freigeben_geht_durch_bestaetigen_und_ausfuehren(
        self, db: Session, lage
    ) -> None:
        """Die Mail ersetzt den Klick, nicht die Pruefung.

        `confirm_proposal` und `execute_proposal` werden ganz normal gerufen —
        mit ihnen laufen die drei Rechtepruefungen, die Backup-Schranke, der
        Server-Mutex und der atomare Verbrauch des Bestaetigungstokens
        unveraendert weiter.

        Der Vorschlag entsteht hier ueber `create_proposal` und nicht von Hand:
        `confirm_proposal` entschluesselt die Nutzlast mit einem AAD, das an der
        Zeile haengt. Ein handgeschriebener Chiffretext kaeme gar nicht bis zu
        der Zusage, um die es geht.
        """
        from services import ai_proposal_service

        user, server, conversation, run = lage
        vorschlag = ai_proposal_service.create_proposal(
            db,
            user=user,
            conversation=conversation,
            tool_name="propose_server_lifecycle",
            arguments={
                "server_id": server.id,
                "operation": "restart",
                "reason": "Der Server hängt.",
                "expected_effect": "Er läuft wieder.",
            },
            correlation_id=str(uuid4()),
        )
        vorschlag.run_id = run.id
        db.commit()
        _, token = _freigabe(db, lage, vorschlag)

        with patch("services.ai_run_service.lauf_fortsetzen"), patch(
            "services.ai_proposal_service.execute_proposal"
        ) as ausfuehren:
            ausfuehren.return_value = (vorschlag, {"ok": True})
            ergebnis = ai_approval_service.entscheiden(
                db, token=token, entscheidung="approved"
            )

        assert ergebnis["decision"] == "approved"
        aufruf = ausfuehren.call_args.kwargs
        assert aufruf["proposal_id"] == vorschlag.id
        # **Der Akteur ist der Mensch aus der Freigabezeile.** Nicht der
        # Dienstbenutzer eines Laufs und nicht der Betreiber — wer per Mail
        # zustimmt, handelt unter seinen eigenen Rechten.
        assert aufruf["user"].id == user.id
        # Weitergereicht wird genau der Token, den `confirm_proposal` eben
        # erzeugt hat. Der Beweis geht ohne zweiten Mock: der Hash steht an der
        # Vorschlagszeile, und der Mock löscht ihn nicht.
        db.refresh(vorschlag)
        assert vorschlag.confirmation_token_hash == hashlib.sha256(
            aufruf["confirmation_token"].encode()
        ).hexdigest()

    def test_die_freigabe_fuehrt_wirklich_aus_und_entwertet_den_token(
        self, db: Session, lage
    ) -> None:
        """Derselbe Weg, aber ohne `execute_proposal` wegzumocken.

        Gemockt wird allein die letzte Naht zum Server
        (`request_lifecycle_operation`). Rechteprüfung, Server-Mutex und der
        atomare Einmalverbrauch des Bestätigungstokens laufen damit echt —
        erst so ist der Anspruch dieses Moduls belegt und nicht nur behauptet.
        """
        from services import ai_proposal_service

        user, server, conversation, run = lage
        vorschlag = ai_proposal_service.create_proposal(
            db,
            user=user,
            conversation=conversation,
            tool_name="propose_server_lifecycle",
            arguments={
                "server_id": server.id,
                "operation": "restart",
                "reason": "Der Server hängt.",
                "expected_effect": "Er läuft wieder.",
            },
            correlation_id=str(uuid4()),
        )
        vorschlag.run_id = run.id
        db.commit()
        _, token = _freigabe(db, lage, vorschlag)

        gesehen: dict = {}

        def _einreihen(db_, *, server_id, operation, actor, idempotency_key):
            gesehen["server_id"] = server_id
            gesehen["operation"] = operation
            gesehen["actor_user_id"] = actor.user.id
            gesehen["origin"] = actor.origin
            return {"status": "queued", "task_id": "t-1"}

        with patch("services.ai_run_service.lauf_fortsetzen"), patch(
            "services.server_action_service.request_lifecycle_operation",
            side_effect=_einreihen,
        ):
            ergebnis = ai_approval_service.entscheiden(
                db, token=token, entscheidung="approved"
            )

        assert ergebnis["decision"] == "approved"
        assert gesehen["server_id"] == server.id
        assert gesehen["operation"] == "restart"
        assert gesehen["actor_user_id"] == user.id
        assert gesehen["origin"] == "ai"
        # Der Bestätigungstoken ist beim Ausführen atomar verbraucht worden.
        db.refresh(vorschlag)
        assert vorschlag.confirmation_token_hash is None
        # Und der Freigabelink ebenso: ein zweiter Klick findet nichts mehr.
        with pytest.raises(AiActionStateError) as fehler:
            ai_approval_service.entscheiden(db, token=token, entscheidung="approved")
        assert str(fehler.value) == "AI_APPROVAL_INVALID"

    def test_ein_verbrannter_link_weckt_den_lauf_trotzdem(
        self, db: Session, lage
    ) -> None:
        """Ab dem Anspruch ist das Token weg — der wartende Lauf muss das erfahren.

        `_anspruch_nehmen` verbraucht die Zeile, bevor der Vorschlagszustand
        geprüft wird. Scheitert `confirm_proposal` danach, blieb der Lauf früher
        stumm auf ``waiting_confirmation`` stehen: der Link war verbrannt,
        niemand konnte mehr zustimmen, und weil die Zeile jetzt ``consumed_at``
        trägt, galt der Auftrag beim Fristende als aufgegeben statt als
        eskaliert. Der Betreiber erfuhr nicht einmal, dass eine Freigabe offen
        gewesen war.
        """
        _, _, _, run = lage
        # Im Panel wurde derselbe Vorschlag inzwischen bestätigt und
        # ausgeführt — der Link aus der Mail kommt zu spät.
        vorschlag = _vorschlag(db, lage, status="succeeded")
        zeile, token = _freigabe(db, lage, vorschlag)

        with patch("services.ai_run_service.lauf_fortsetzen") as wecken:
            with pytest.raises(AiActionStateError) as fehler:
                ai_approval_service.entscheiden(
                    db, token=token, entscheidung="approved"
                )

        assert str(fehler.value) == "AI_ACTION_NOT_PROPOSED"
        db.refresh(zeile)
        assert zeile.consumed_at is not None, "Das Token bleibt bewusst verbraucht"
        assert wecken.call_args.kwargs["run_id"] == run.id

    def test_ein_entzogenes_recht_kommt_auch_mit_link_nicht_durch(
        self, db: Session, lage
    ) -> None:
        """Wer das Recht verliert, verliert auch den Link.

        Der Fall ist nicht theoretisch: zwischen Mail und Antwort liegen
        Stunden, und in der Zeit kann ein Betreiber die Delegation
        zurueckziehen. Der Link darf sie nicht ueberdauern.
        """
        user, server, _, _ = lage
        vorschlag = _vorschlag(db, lage)
        _, token = _freigabe(db, lage, vorschlag)

        db.query(ServerPermission).filter(
            ServerPermission.user_id == user.id,
            ServerPermission.server_id == server.id,
        ).delete()
        db.commit()

        with patch("services.ai_run_service.lauf_fortsetzen"):
            with pytest.raises(AiActionStateError):
                ai_approval_service.entscheiden(
                    db, token=token, entscheidung="approved"
                )

    def test_ein_stillgelegtes_konto_entscheidet_nicht_mehr(
        self, db: Session, lage
    ) -> None:
        user, _, _, _ = lage
        vorschlag = _vorschlag(db, lage)
        _, token = _freigabe(db, lage, vorschlag)
        user.is_active = False
        db.commit()

        with pytest.raises(AiActionStateError) as fehler:
            ai_approval_service.entscheiden(db, token=token, entscheidung="approved")
        assert str(fehler.value) == "AI_APPROVAL_INVALID"


class TestEndpunkt:
    """Der Router: kein GET, das ausfuehrt, und die Kopfzeilen stimmen."""

    def test_get_zeigt_nur_und_veraendert_nichts(
        self, client, db: Session, lage
    ) -> None:
        vorschlag = _vorschlag(db, lage)
        zeile, token = _freigabe(db, lage, vorschlag)

        antwort = client.get(f"/api/ai/approvals/{token}")
        assert antwort.status_code == 200
        daten = antwort.json()
        assert daten["tool_name"] == "propose_file_delete"
        assert daten["server_name"] == "Freigabe-Server"
        assert daten["reason"] == "Die Datei ist kaputt."

        db.refresh(zeile)
        assert zeile.consumed_at is None
        db.refresh(vorschlag)
        assert vorschlag.status == "proposed"

    def test_der_link_bleibt_nirgends_haengen(self, client, db: Session, lage) -> None:
        """`no-store` und `no-referrer`.

        Der Link steht in einer Mail. Ohne die Kopfzeilen landete das Token im
        Zwischenspeicher eines geteilten Geraets und im ``Referer`` jedes
        nachgeladenen Bildes.
        """
        vorschlag = _vorschlag(db, lage)
        _, token = _freigabe(db, lage, vorschlag)

        antwort = client.get(f"/api/ai/approvals/{token}")
        assert antwort.headers["Cache-Control"] == "no-store"
        assert antwort.headers["Referrer-Policy"] == "no-referrer"

    def test_ein_unbekanntes_token_meldet_404_ohne_auskunft(self, client) -> None:
        antwort = client.get("/api/ai/approvals/gibt-es-nicht")
        assert antwort.status_code == 404
        assert antwort.json()["detail"] == "ai.errors.codes.AI_APPROVAL_INVALID"

    def test_der_post_braucht_kein_csrf_aber_ein_gueltiges_token(
        self, client, db: Session, lage
    ) -> None:
        """Kein CSRF-Schutz, und trotzdem kein CSRF-Loch.

        Der Endpunkt traegt keine Cookie-Authentifizierung; er wirkt
        ausschliesslich durch das Token im Pfad. Eine fremde Seite kann ihn
        nicht sinnvoll ausloesen, ohne das Token zu kennen — und wer es kennt,
        braucht keinen Umweg.
        """
        vorschlag = _vorschlag(db, lage)
        _, token = _freigabe(db, lage, vorschlag)

        with patch("services.ai_run_service.lauf_fortsetzen"):
            antwort = client.post(
                f"/api/ai/approvals/{token}/decide", json={"decision": "rejected"}
            )
        assert antwort.status_code == 200
        assert antwort.json()["decision"] == "rejected"

        falsch = client.post(
            "/api/ai/approvals/gibt-es-nicht/decide", json={"decision": "rejected"}
        )
        assert falsch.status_code == 404

    def test_eine_erfundene_entscheidung_wird_abgewiesen(
        self, client, db: Session, lage
    ) -> None:
        vorschlag = _vorschlag(db, lage)
        _, token = _freigabe(db, lage, vorschlag)
        antwort = client.post(
            f"/api/ai/approvals/{token}/decide", json={"decision": "vielleicht"}
        )
        assert antwort.status_code == 422


class TestAnfordern:
    """Wann eine Freigabemail entsteht — und wann ausdruecklich nicht."""

    def test_ohne_adresse_keine_freigabe(self, db: Session, lage) -> None:
        """Der Rueckfall muss bleiben.

        Ohne Zustellweg kann niemand antworten. Ein Lauf, der auf eine Antwort
        wartet, die nie kommen kann, haengt bis zu seiner Frist — das ist
        schlimmer als einer, der ehrlich aufhoert.
        """
        user, _, _, run = lage
        vorschlag = _vorschlag(db, lage)

        with patch("services.ai_mail.empfaenger", return_value=None):
            assert not ai_approval_service.freigabe_anfordern(
                db, proposal=vorschlag, user=user, run_id=run.id
            )
        assert db.query(AiActionApproval).count() == 0

    def test_eine_zweite_frage_wartet(self, db: Session, lage) -> None:
        """Nicht acht gleichlautende Mails.

        Ohne Deckel schriebe ein Lauf, der in jeder Runde denselben Vorschlag
        neu aufsetzt, eine Mail je Runde — und der Empfaenger wuesste bei keiner,
        ob sie noch gilt.
        """
        user, _, _, run = lage
        erster = _vorschlag(db, lage)
        zweiter = _vorschlag(db, lage)
        _freigabe(db, lage, erster)

        with patch("services.ai_mail.empfaenger", return_value="a@b.de"):
            assert not ai_approval_service.freigabe_anfordern(
                db, proposal=zweiter, user=user, run_id=run.id
            )

    def test_die_grenze_wird_unter_der_benutzersperre_geprueft(
        self, db: Session, lage, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Nachsehen und Anlegen gehören in eine Sperre.

        Ein Benutzer kann mehrere unbeaufsichtigte Läufe gleichzeitig haben —
        bis zu drei Worker plus die Guardian-Heilung —, und jeder fragt in einer
        eigenen Sitzung. Ohne Sperre sähen zwei davon beide "keine offene
        Freigabe" und schrieben beide eine Mail. Echte Nebenläufigkeit ist auf
        SQLite nicht herstellbar — ``FOR UPDATE`` ist dort eine leere Anweisung
        —, deshalb hält dieser Test die Reihenfolge statt des Rennens.
        """
        user, _, _, run = lage
        vorschlag = _vorschlag(db, lage)

        ablauf: list[str] = []
        echte_sperre = Query.with_for_update
        echtes_nachsehen = ai_approval_service.offene_freigabe

        def _sperre(self, *args, **kwargs):
            ablauf.append("sperre")
            return echte_sperre(self, *args, **kwargs)

        def _nachsehen(db_, *, user_id):
            ablauf.append("nachsehen")
            return echtes_nachsehen(db_, user_id=user_id)

        monkeypatch.setattr(Query, "with_for_update", _sperre)
        monkeypatch.setattr(ai_approval_service, "offene_freigabe", _nachsehen)

        with patch("services.ai_mail.empfaenger", return_value="a@b.de"), patch(
            "services.ai_mail.einreihen", return_value="outbox-1"
        ):
            assert ai_approval_service.freigabe_anfordern(
                db, proposal=vorschlag, user=user, run_id=run.id
            )

        assert ablauf == ["sperre", "nachsehen"]

    def test_die_grenze_steht_im_code_und_nicht_in_einer_konstanten(self) -> None:
        """Eine Konstante, die nichts durchsetzt, ist irreführender als keine.

        `MAX_OFFENE = 1` stand mit Begründung im Modul und wurde nirgends
        ausgewertet — die Grenze lebt allein in der Prüfung auf
        `offene_freigabe`. Der Test hält die Aufräumung fest; die Wirkung der
        Grenze deckt `test_eine_zweite_frage_wartet` weiterhin ab.
        """
        assert not hasattr(ai_approval_service, "MAX_OFFENE")

    def test_eine_nicht_eingereihte_mail_verwirft_das_token(
        self, db: Session, lage
    ) -> None:
        """Sonst blockierte eine Freigabe ohne Link jeden weiteren Versuch."""
        user, _, _, run = lage
        vorschlag = _vorschlag(db, lage)

        with patch("services.ai_mail.empfaenger", return_value="a@b.de"), patch(
            "services.ai_mail.einreihen", return_value=None
        ):
            assert not ai_approval_service.freigabe_anfordern(
                db, proposal=vorschlag, user=user, run_id=run.id
            )
        assert db.query(AiActionApproval).count() == 0

    def test_der_link_steht_im_rueckfalltext(self, db: Session, lage) -> None:
        """Ab dem zweiten Zustellversuch ruft der Postausgang das Modell nicht mehr.

        Steht der Link nur in der schoenen Fassung, ist die zweite Zustellung
        eine Frage ohne Antwortmoeglichkeit — und sie kommt genau dann, wenn die
        erste nicht angekommen ist.
        """
        user, _, _, run = lage
        vorschlag = _vorschlag(db, lage)
        gesehen: dict = {}

        def _merken(db_, **kwargs):
            gesehen.update(kwargs)
            return "outbox-1"

        with patch("services.ai_mail.empfaenger", return_value="a@b.de"), patch(
            "services.ai_mail.einreihen", side_effect=_merken
        ):
            assert ai_approval_service.freigabe_anfordern(
                db, proposal=vorschlag, user=user, run_id=run.id
            )

        zeile = db.query(AiActionApproval).one()
        assert zeile.proposal_id == vorschlag.id
        assert zeile.run_id == run.id
        # Der Klartext steht nur in der Mail — in der Datenbank nur sein Hash.
        assert len(zeile.token_hash) == 64
        assert "/ai/freigabe/" in gesehen["text"]
        assert "/ai/freigabe/" in (gesehen["html"] or "")
        # Kein `fakten`-Feld: diese Mail ist eine Frage mit einem Knopf, keine
        # Erzaehlung. Ein umformulierter Text koennte den Anlass verschieben.
        assert gesehen.get("fakten") is None


class TestKette:
    """Zwei offene Vorschläge, eine Frage nach der anderen.

    Eine Mail nennt genau einen Vorschlag. Ein Lauf legt aber durchaus zwei an
    — das Stundenkontingent ist benutzerweit, und `autonomy_allows` fällt bei
    Erschöpfung mitten im Vorgang auf Bestätigungspflicht zurück. Ohne die
    Kette entschied der Betreiber den gemailten, `darf_fortsetzen` sah den
    zweiten noch auf 'proposed' und weckte nicht, und eine zweite Mail forderte
    niemand an: der Lauf stand bis zu seiner Frist, ohne dass jemand davon
    erfuhr.
    """

    def test_nach_der_entscheidung_geht_die_naechste_frage_hinaus(
        self, db: Session, lage
    ) -> None:
        erster = _vorschlag(db, lage)
        zweiter = _vorschlag(db, lage)
        _, token = _freigabe(db, lage, erster)

        with patch("services.ai_mail.empfaenger", return_value="a@b.de"), patch(
            "services.ai_mail.einreihen", return_value="outbox-1"
        ), patch("services.ai_run_service.lauf_fortsetzen") as wecken:
            ai_approval_service.entscheiden(db, token=token, entscheidung="rejected")

        db.refresh(erster)
        assert erster.status == "expired"
        # Genau eine offene Frage, und sie gilt dem zweiten Vorschlag.
        offen = (
            db.query(AiActionApproval)
            .filter(AiActionApproval.consumed_at.is_(None))
            .all()
        )
        assert [zeile.proposal_id for zeile in offen] == [zweiter.id]
        # Geweckt wird noch nicht — der Lauf trägt weiterhin etwas Offenes.
        assert not wecken.called

    def test_der_lauf_wacht_erst_nach_der_letzten_frage_auf(
        self, db: Session, lage
    ) -> None:
        """Und der zweite Link aus der Kette funktioniert wirklich.

        Deshalb wird er aus dem Mailtext gefischt statt von Hand gebaut: eine
        Kette, deren zweite Mail keinen brauchbaren Link trägt, wäre genau
        derselbe hängende Lauf mit einem Postfacheintrag mehr.
        """
        _, _, _, run = lage
        erster = _vorschlag(db, lage)
        zweiter = _vorschlag(db, lage)
        _, token = _freigabe(db, lage, erster)
        gesehen: dict = {}

        def _merken(db_, **kwargs):
            gesehen.update(kwargs)
            return "outbox-1"

        with patch("services.ai_mail.empfaenger", return_value="a@b.de"), patch(
            "services.ai_mail.einreihen", side_effect=_merken
        ), patch("services.ai_run_service.lauf_fortsetzen") as wecken:
            ai_approval_service.entscheiden(db, token=token, entscheidung="rejected")
            zweites_token = str(gesehen["text"]).split("/ai/freigabe/", 1)[1].split()[0]
            ergebnis = ai_approval_service.entscheiden(
                db, token=zweites_token, entscheidung="rejected"
            )

        assert ergebnis["decision"] == "rejected"
        db.refresh(zweiter)
        assert zweiter.status == "expired"
        # Jetzt ist nichts mehr offen — und erst jetzt läuft der Lauf weiter.
        assert wecken.call_args.kwargs["run_id"] == run.id

    def test_ein_stillgelegtes_konto_bekommt_keine_zweite_frage(
        self, db: Session, lage
    ) -> None:
        """Die Kette fragt niemanden, der nicht mehr antworten darf.

        `entscheiden` weist ein stillgelegtes Konto ab, bevor es den Vorschlag
        überhaupt liest — die Kette läuft danach trotzdem an, und sie muss
        dieselbe Antwort geben.
        """
        user, _, _, _ = lage
        _vorschlag(db, lage)
        zweiter = _vorschlag(db, lage)
        _, token = _freigabe(db, lage, zweiter)
        user.is_active = False
        db.commit()

        with patch("services.ai_mail.empfaenger", return_value="a@b.de"), patch(
            "services.ai_mail.einreihen", return_value="outbox-1"
        ):
            with pytest.raises(AiActionStateError):
                ai_approval_service.entscheiden(
                    db, token=token, entscheidung="approved"
                )

        assert (
            db.query(AiActionApproval)
            .filter(AiActionApproval.consumed_at.is_(None))
            .count()
            == 0
        )


class TestMailrahmen:
    """Der Link kommt vom Panel, nie vom Modell."""

    def test_der_knopf_zeigt_auf_das_panel(self) -> None:
        from config import settings
        from services.email_service import EmailService

        rahmen = EmailService.ai_rahmen_freigabe(
            "maick",
            tool_name="propose_file_delete",
            server_name="Testserver",
            token="abc123",
            stunden=24,
        )
        assert rahmen["cta_url"].startswith(settings.panel_url.rstrip("/"))
        _, text, html = EmailService.ai_mail_rendern(
            rahmen, mailtext=None, rueckfall=str(rahmen["rueckfall"])
        )
        assert "abc123" in text
        assert "abc123" in html

    def test_ein_fremdes_ziel_wird_verworfen(self) -> None:
        """Die eine maskierungsfreie Stelle der Vorlage ist geschlossen.

        Der Rahmen geht als JSON durch die Datenbank und wird vom Postausgang
        Tage spaeter wieder eingelesen. Ohne diese Pruefung waere der Knopf der
        Traeger, auf den es jede Prompt-Injection abgesehen hat.
        """
        from services.email_service import EmailService

        rahmen = EmailService.ai_rahmen_freigabe(
            "maick",
            tool_name="propose_file_delete",
            server_name="Testserver",
            token="abc123",
            stunden=24,
        )
        rahmen["cta_url"] = "https://phishing.invalid/ai/freigabe/abc123"
        _, text, html = EmailService.ai_mail_rendern(
            rahmen, mailtext=None, rueckfall=str(rahmen["rueckfall"])
        )
        assert "phishing.invalid" not in text
        assert "phishing.invalid" not in html


class TestAufraeumen:
    def test_abgelaufene_und_verbrauchte_gehen_weg(self, db: Session, lage) -> None:
        """Die Tatsache der Zustimmung steht im Audit — hier stuende sie doppelt."""
        vorschlag = _vorschlag(db, lage)
        abgelaufen, _ = _freigabe(db, lage, vorschlag)
        abgelaufen.expires_at = _jetzt() - timedelta(hours=1)
        offen, _ = _freigabe(db, lage, _vorschlag(db, lage))
        db.commit()

        entfernt = ai_approval_service.abgelaufene_aufraeumen(db)
        assert entfernt == 1
        assert db.query(AiActionApproval).one().id == offen.id
