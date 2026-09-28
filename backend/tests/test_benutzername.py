"""Benutzername selbst waehlen und aendern, dazu die Praefixe in ``users``.

Die Invarianten:

1. Kein Name entsteht aus der E-Mail. Social Login und Hoster-Shop vergeben
   einen vorlaeufigen Namen mit ``username_gewaehlt=False``; der Mensch
   waehlt einmal selbst.
2. Umbenennen haelt die Sitzung: Tokens finden das Konto ueber ``user_id``.
3. Namen sind eindeutig ohne Ruecksicht auf Gross/klein.
4. ``password_hash`` beginnt immer mit ``msm-pw-v1:``, ``email_encrypted``
   mit ``msm-email-v1:``. Alte passlib-Hashes werden umhuellt und beim
   naechsten Login frisch erzeugt.
"""

import pytest
from fastapi.testclient import TestClient
from passlib.context import CryptContext
from pydantic import ValidationError
from sqlalchemy.orm import Session

from models import User
from models.audit_log import AuditLog
from models.team import Team
from models.user import EMAIL_PRAEFIX, email_chiffrat_fuer_dis
from schemas.user import UserCreate, UsernameUpdateRequest
from services import oauth_service, passwort_altbestand
from services.auth_service import AuthService
from services.dis_client import DisClient, DisSidecarError


def _patch(client: TestClient, cookies: dict, csrf: str | None, name: str):
    return client.patch(
        "/api/auth/me/username",
        json={"username": name},
        headers={"X-CSRF-Token": csrf or ""},
        cookies=cookies,
    )


class TestRegeln:
    @pytest.mark.parametrize("name", ["max", "Max_Mustermann", "a.b-c", "_x_", "x" * 32])
    def test_erlaubt(self, name):
        assert UsernameUpdateRequest(username=name).username == name

    @pytest.mark.parametrize(
        "name",
        ["ab", "x" * 33, "max@web.de", "mit leer", ".max", "max-", "Ümlaut", "alle", "Everyone"],
    )
    def test_abgelehnt(self, name):
        with pytest.raises(ValidationError):
            UsernameUpdateRequest(username=name)

    def test_registrierung_prueft_dieselbe_regel(self):
        with pytest.raises(ValidationError):
            UserCreate(username="max@web.de", email="max@web.de", password="Passwort123!")

    def test_belegt_ohne_gross_klein(self, db: Session, regular_user: User):
        assert AuthService.benutzername_belegt(db, "USER1")
        assert not AuthService.benutzername_belegt(db, "USER1", ausser_id=regular_user.id)


class TestUmbenennen:
    def test_sitzung_bleibt_und_audit_steht(
        self, client: TestClient, db: Session, regular_user: User, user_cookies: dict, user_csrf_token: str
    ):
        resp = _patch(client, user_cookies, user_csrf_token, "neuer_name")
        assert resp.status_code == 200, resp.text
        assert resp.json()["username"] == "neuer_name"
        assert resp.json()["username_gewaehlt"] is True

        # Dasselbe Access-Token, dessen `sub` noch "user1" sagt.
        me = client.get("/api/auth/me", cookies=user_cookies)
        assert me.status_code == 200
        assert me.json()["username"] == "neuer_name"

        eintrag = db.query(AuditLog).filter(AuditLog.action == "auth.username.change").one()
        assert eintrag.user_id == regular_user.id
        assert "user1" in eintrag.details and "neuer_name" in eintrag.details

    def test_alter_name_ist_nicht_mehr_das_konto(
        self, client: TestClient, db: Session, regular_user: User, user_cookies: dict, user_csrf_token: str
    ):
        assert _patch(client, user_cookies, user_csrf_token, "neuer_name").status_code == 200
        # Wer den alten Namen uebernimmt, erbt das laufende Token nicht.
        fremd = AuthService.create_user(db, "user1", "fremd@test.de", "Fremd12345!")
        me = client.get("/api/auth/me", cookies=user_cookies)
        assert me.json()["id"] == regular_user.id != fremd.id

    def test_belegt_auch_in_anderer_schreibweise(
        self, client: TestClient, owner_user: User, regular_user: User, user_cookies: dict, user_csrf_token: str
    ):
        resp = _patch(client, user_cookies, user_csrf_token, "OWNER")
        assert resp.status_code == 409

    def test_eigene_schreibweise_aendern(
        self, client: TestClient, regular_user: User, user_cookies: dict, user_csrf_token: str
    ):
        resp = _patch(client, user_cookies, user_csrf_token, "User1")
        assert resp.status_code == 200
        assert resp.json()["username"] == "User1"

    def test_ungueltiger_name_422(
        self, client: TestClient, regular_user: User, user_cookies: dict, user_csrf_token: str
    ):
        assert _patch(client, user_cookies, user_csrf_token, "mail@web.de").status_code == 422

    def test_persoenliches_team_zieht_mit(
        self, client: TestClient, db: Session, regular_user: User, user_cookies: dict, user_csrf_token: str
    ):
        from services.team_service import personal_team

        team = personal_team(db, regular_user)
        db.commit()
        assert _patch(client, user_cookies, user_csrf_token, "neuer_name").status_code == 200
        db.expire_all()
        assert db.get(Team, team.id).name == "neuer_name"

    def test_ohne_csrf_abgelehnt(self, client: TestClient, regular_user: User, user_cookies: dict):
        assert _patch(client, user_cookies, None, "neuer_name").status_code == 403


class TestVorlaeufigerName:
    def _profil(self, username: str | None, email: str):
        return oauth_service.NormalizedProfile(
            subject="s-1", email=email, email_verified=True,
            username=username, name=None, avatar=None, raw={},
        )

    def test_google_ohne_namen_bekommt_keinen_aus_der_mail(self, db: Session):
        user = oauth_service.register_user_from_oauth(db, self._profil(None, "mauntingstudios@gmail.com"))
        assert user.username_gewaehlt is False
        assert "gmail" not in user.username and "maunting" not in user.username
        assert user.username.startswith("user_")

    def test_mail_als_anbietername_wird_verworfen(self, db: Session):
        user = oauth_service.register_user_from_oauth(db, self._profil("max@firma.de", "max@firma.de"))
        assert user.username.startswith("user_")

    def test_brauchbarer_anbietername_wird_vorschlag(self, db: Session, regular_user: User):
        user = oauth_service.register_user_from_oauth(db, self._profil("Alice.GH", "alice@x.de"))
        assert user.username == "Alice.GH"
        assert user.username_gewaehlt is False
        zweiter = oauth_service.register_user_from_oauth(db, self._profil("USER1", "b@x.de"))
        assert zweiter.username == "USER1_1"

    def test_waehlen_setzt_die_marke(self, client: TestClient, db: Session):
        user = oauth_service.register_user_from_oauth(db, self._profil(None, "neu@x.de"))
        user.password_hash = AuthService.hash_password("Passwort123!")
        db.commit()
        login = client.post("/api/auth/login", json={"username": user.username, "password": "Passwort123!"})
        assert login.status_code == 200
        cookies = dict(login.cookies)
        assert client.get("/api/auth/me", cookies=cookies).json()["username_gewaehlt"] is False
        resp = _patch(client, cookies, cookies.get("__Secure-csrf_token"), "selbst_gewaehlt")
        assert resp.json()["username_gewaehlt"] is True


class TestEmailPraefix:
    def test_setter_schreibt_msm_email_v1(self, regular_user: User):
        assert regular_user.email_encrypted.startswith(EMAIL_PRAEFIX)
        assert DisClient.PRAEFIX not in regular_user.email_encrypted
        assert regular_user.email == "user1@test.de"

    def test_altbestand_mit_dis_praefix_bleibt_lesbar(self, db: Session, regular_user: User):
        regular_user.email_encrypted = DisClient.encrypt("alt@test.de", aad="msm:user:email")
        db.commit()
        assert regular_user.email == "alt@test.de"

    def test_unbekannte_fassung_meldet_sich(self):
        with pytest.raises(DisSidecarError):
            email_chiffrat_fuer_dis("msm-email-v2:abc")


class TestPasswortAltbestand:
    PASSWORT = "Alt-Passwort 1!"

    def _altkonto(self, db: Session) -> User:
        user = AuthService.create_user(db, "altkonto", "alt@test.de", "egal12345")
        user.password_hash = CryptContext(schemes=["argon2"]).hash(self.PASSWORT)
        user.email_verified = True
        db.commit()
        return user

    def test_umhuellen_laesst_kein_argon2_stehen(self, db: Session, regular_user: User):
        alt = self._altkonto(db)
        vorher_regular = regular_user.password_hash
        assert passwort_altbestand.umhuellen(db) == 1
        db.expire_all()
        assert db.get(User, alt.id).password_hash.startswith("msm-pw-v1:")
        assert db.get(User, regular_user.id).password_hash == vorher_regular
        assert passwort_altbestand.umhuellen(db) == 0

    def test_unbekannte_form_bleibt_stehen(self, db: Session):
        alt = self._altkonto(db)
        alt.password_hash = "$2b$12$bcrypt-irgendwas"
        db.commit()
        assert passwort_altbestand.umhuellen(db) == 0
        db.expire_all()
        assert db.get(User, alt.id).password_hash == "$2b$12$bcrypt-irgendwas"

    def test_login_macht_aus_dem_umhuellten_einen_frischen_hash(self, client: TestClient, db: Session):
        alt = self._altkonto(db)
        passwort_altbestand.umhuellen(db)
        resp = client.post("/api/auth/login", json={"username": "altkonto", "password": self.PASSWORT})
        assert resp.status_code == 200, resp.text
        db.expire_all()
        frisch = db.get(User, alt.id).password_hash
        assert frisch.startswith("msm-pw-v1:") and frisch.endswith(":v2")
        assert not DisClient.braucht_neuen_hash(frisch)

    def test_falsches_passwort_bleibt_falsch(self, client: TestClient, db: Session):
        self._altkonto(db)
        passwort_altbestand.umhuellen(db)
        resp = client.post("/api/auth/login", json={"username": "altkonto", "password": "falsch123"})
        assert resp.status_code == 401
