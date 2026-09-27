"""Der Datenexport: alles vom eigenen Konto, nichts von fremden, keine Hashes.

Invarianten:

1. Ohne Nachweis (Passwort bzw. eingerichteter zweiter Faktor) kein Export.
2. Nur Zeilen des eigenen Kontos, auch nicht ueber ein gemeinsames Team.
3. Kein Chiffrat im Paket, das der Server oeffnen kann, und kein Hash oder
   Token, mit dem man sich anmelden koennte.
4. Selbst hinterlegte Zugangsdaten nur nach Nachweis; ein Social-Konto ohne
   Passwort und 2FA bekommt sie nicht.
5. Entschluesselt wird gebuendelt, nicht einzeln je Wert.
"""

import json

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import AiConversation, AiMessage, AuditLog, Team, User
from services import credential_service
from services.auth_service import AuthService
from services.dis_client import DisClient
from services.notes_service import NotesService


def _kopf(cookies: dict) -> dict:
    marke = cookies.get("__Secure-csrf_token")
    return {"X-CSRF-Token": marke} if marke else {}


def _export(client: TestClient, cookies: dict, **nachweis):
    return client.post(
        "/api/auth/data-export", json=nachweis, cookies=cookies, headers=_kopf(cookies)
    )


def _bestand(db: Session, user: User, fremder: User) -> None:
    NotesService.create_note(db, user, title="Einkauf", content="Milch und Brot")
    team = Team(name="Gemeinsam", owner_user_id=user.id)
    db.add(team)
    db.commit()
    NotesService.create_note(
        db, fremder, title="Fremde Notiz", content="geht dich nichts an",
        note_type="team", team_id=team.id,
    )
    unterhaltung = AiConversation(id="c-1", kind="primary", user_id=user.id, title="Planung")
    db.add(unterhaltung)
    db.flush()
    db.add(AiMessage(id="m-1", conversation_id="c-1", role="user", content="Wie spaet ist es?"))
    db.commit()
    credential_service.upsert_user_credential(
        db, user_id=user.id, kind="github_token", label="Privat", secret="ghp_geheim123",
    )


class TestNachweis:
    def test_ohne_passwort_kein_export(self, client: TestClient, user_cookies: dict):
        assert _export(client, user_cookies).status_code == 403
        assert _export(client, user_cookies, password="falsch").status_code == 403

    def test_mit_2fa_zaehlt_nur_der_code(
        self, client: TestClient, db: Session, regular_user: User, user_cookies: dict, monkeypatch
    ):
        regular_user.two_factor_enabled = True
        regular_user.two_factor_secret_encrypted = "totp-geheimnis"
        db.commit()
        monkeypatch.setattr(
            AuthService, "verify_current_2fa_code", staticmethod(lambda _u, code: code == "123456")
        )
        assert _export(client, user_cookies, password="UserPass123!").status_code == 403
        antwort = _export(client, user_cookies, otp_code="123456")
        assert antwort.status_code == 200, antwort.text

    def test_hoechstens_drei_je_stunde_und_jeder_im_audit(
        self, client: TestClient, db: Session, regular_user: User, user_cookies: dict
    ):
        for _ in range(3):
            assert _export(client, user_cookies, password="UserPass123!").status_code == 200
        assert _export(client, user_cookies, password="UserPass123!").status_code == 429
        eintraege = db.query(AuditLog).filter(
            AuditLog.user_id == regular_user.id, AuditLog.action == "auth.data_export"
        ).count()
        assert eintraege == 3


class TestInhalt:
    def test_eigenes_im_klartext_fremdes_nicht_keine_hashes(
        self, client: TestClient, db: Session, regular_user: User, owner_user: User, user_cookies: dict
    ):
        _bestand(db, regular_user, owner_user)
        antwort = _export(client, user_cookies, password="UserPass123!")
        assert antwort.status_code == 200, antwort.text
        assert antwort.headers["cache-control"] == "no-store"
        paket = antwort.json()
        roh = json.dumps(paket, ensure_ascii=False)

        titel = [n["title"] for n in paket["tabellen"]["notes"]]
        assert titel == ["Einkauf"]
        assert paket["tabellen"]["notes"][0]["content"] == "Milch und Brot"
        assert paket["tabellen"]["ai_messages"][0]["content"] == "Wie spaet ist es?"
        assert paket["tabellen"]["ai_conversations"][0]["title"] == "Planung"
        assert [u["id"] for u in paket["tabellen"]["users"]] == [regular_user.id]
        assert paket["tabellen"]["users"][0]["email_encrypted"] == "user1@test.de"

        assert "Fremde Notiz" not in roh
        assert "geht dich nichts an" not in roh
        assert DisClient.PRAEFIX not in roh
        assert "password_hash" not in paket["tabellen"]["users"][0]
        assert regular_user.password_hash not in roh
        assert "users.password_hash" in paket["manifest"]["nicht_enthalten"]["spalten"]

        zugang = paket["tabellen"]["user_credentials"][0]
        assert zugang["secret_encrypted"] == "ghp_geheim123"
        assert paket["manifest"]["zugangsdaten_enthalten"] is True

    def test_social_konto_ohne_faktor_bekommt_keine_zugangsdaten(
        self, client: TestClient, db: Session, regular_user: User, owner_user: User, user_cookies: dict
    ):
        _bestand(db, regular_user, owner_user)
        regular_user.has_password = False
        db.commit()
        antwort = _export(client, user_cookies)
        assert antwort.status_code == 200, antwort.text
        paket = antwort.json()
        assert paket["tabellen"]["user_credentials"][0]["secret_encrypted"] is None
        assert "ghp_geheim123" not in json.dumps(paket)
        assert paket["manifest"]["zugangsdaten_enthalten"] is False
        # Der Rest kommt trotzdem.
        assert paket["tabellen"]["notes"][0]["content"] == "Milch und Brot"

    def test_entschluesselt_gebuendelt(
        self, client: TestClient, db: Session, regular_user: User, owner_user: User,
        user_cookies: dict, monkeypatch
    ):
        _bestand(db, regular_user, owner_user)
        for nummer in range(5):
            NotesService.create_note(db, regular_user, title=f"Notiz {nummer}", content="x")
        einzeln: list[str] = []
        original = DisClient.decrypt

        def zaehlen(wert, aad=None):
            einzeln.append(aad)
            return original(wert, aad=aad)

        monkeypatch.setattr(DisClient, "decrypt", staticmethod(zaehlen))
        antwort = _export(client, user_cookies, password="UserPass123!")
        assert antwort.status_code == 200, antwort.text
        assert len(antwort.json()["tabellen"]["notes"]) == 6
        assert [aad for aad in einzeln if aad and aad.startswith("msm:note:")] == []
