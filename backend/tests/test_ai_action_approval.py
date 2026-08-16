"""Freigabe per E-Mail — was der Link darf und was er nicht darf.

Der Anlass steht in `services/ai_approval_service`: ein unbeaufsichtigter Lauf
endete an dem Punkt, an dem einer seiner Vorschlaege eine Bestaetigung brauchte.
Der Server blieb kaputt, und die Mail sagte "konnte nicht behoben werden" —
obwohl der Betreiber ein Telefon in der Tasche hat.

Was hier geprueft wird, ist die andere Haelfte davon: dass aus diesem Link kein
zweiter, schwaecherer Weg ins Panel wird. Fuenf Zusagen, jede mit einem
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
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

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
        assert ausfuehren.called
        # Bestaetigt wurde ueber den regulaeren Weg: der Vorschlag traegt
        # danach einen Token-Hash.
        db.refresh(vorschlag)
        assert vorschlag.confirmation_token_hash

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
