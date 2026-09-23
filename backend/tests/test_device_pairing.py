"""Die Kopplung: der einzige Weg, wie das Smart System hereinkommt.

Warum es sie gibt: bei aktivem Captcha ist ``/api/auth/login`` fuer die
Desktop-App verschlossen — ein Turnstile-Widget in einem Tauri-WebView
scheitert daran, dass Cloudflare-Schluessel an Domains haengen. Statt die
Anmeldestrecke im Desktop-Fenster nachzubauen, laedt der bereits angemeldete
Mensch sein Geraet ein.

Damit wird der Code zu einem Ausweis, und die Invarianten hier sind die, die
einen Ausweis erst brauchbar machen:

1. In der Datenbank steht nur sein Hash. Wer die Tabelle liest, kann sich damit
   nicht anmelden.
2. Genau einmal einloesbar, zehn Minuten lang.
3. Unbekannt, abgelaufen und verbraucht sehen von aussen gleich aus — wer raet,
   soll nicht lernen, ob er nah dran war.
4. Was daraus entsteht, ist eine **Desktop**-Sitzung, und sie bleibt es ueber
   die Rotation hinweg. Daran haengt, ob die KI die Werkzeuge fuer den Rechner
   ueberhaupt angeboten bekommt.
5. Entziehen trifft genau ein Geraet — nie ein fremdes, nie alle.
"""

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import DevicePairing, RefreshToken, Role, RolePermission, User
from services import device_pairing_service
from services.auth_service import AuthService
from services.role_service import set_user_roles


def _mit_chatrecht(db: Session, user: User) -> None:
    role = Role(name=f"chat-{user.id}", description=None, is_system=False)
    db.add(role)
    db.flush()
    db.add(RolePermission(role_id=role.id, permission_key="ai.chat.use"))
    db.commit()
    set_user_roles(db, user, [role.id])


def _kopf(cookies: dict) -> dict:
    marke = cookies.get("__Secure-csrf_token")
    return {"X-CSRF-Token": marke} if marke else {}


def _code_erzeugen(client: TestClient, cookies: dict, label: str = "Arbeitsrechner") -> dict:
    antwort = client.post(
        "/api/auth/devices/pairing",
        json={"label": label},
        cookies=cookies,
        headers=_kopf(cookies),
    )
    assert antwort.status_code == 200, antwort.text
    return antwort.json()


class TestCodeErzeugen:
    def test_der_code_steht_nur_in_der_antwort_nie_in_der_datenbank(
        self, client: TestClient, db: Session, regular_user: User, user_cookies: dict
    ):
        _mit_chatrecht(db, regular_user)
        daten = _code_erzeugen(client, user_cookies)

        einladung = db.query(DevicePairing).one()
        assert einladung.code_hash != daten["code"]
        # Der Klartext darf in keiner Spalte auftauchen — auch nicht in der
        # Bezeichnung, wenn jemand sie einmal aus dem Code ableiten wollte.
        for spalte in (einladung.code_hash, einladung.label):
            assert daten["code"] not in spalte

    def test_die_form_ist_abtippbar(
        self, client: TestClient, db: Session, regular_user: User, user_cookies: dict
    ):
        _mit_chatrecht(db, regular_user)
        daten = _code_erzeugen(client, user_cookies)
        code = daten["code"]
        assert len(code) == 14  # 12 Zeichen, zwei Striche
        # Kein I, O, 0 oder 1: das sind die Verwechslungen beim Abtippen.
        assert not set("IO01") & set(code.replace("-", ""))
        # QR-Code wird als lokaler SVG Data-URI mitgeliefert
        assert daten.get("qr_data_uri", "").startswith("data:image/svg+xml")
        assert "%3Csvg" in daten["qr_data_uri"] or "<svg" in daten["qr_data_uri"]

    def test_ohne_chatrecht_kein_code(
        self, client: TestClient, db: Session, regular_user: User, user_cookies: dict
    ):
        antwort = client.post(
            "/api/auth/devices/pairing",
            json={"label": "x"},
            cookies=user_cookies,
            headers=_kopf(user_cookies),
        )
        assert antwort.status_code == 403


class TestEinloesen:
    def test_ein_code_wird_zur_desktop_sitzung(
        self, client: TestClient, db: Session, regular_user: User, user_cookies: dict
    ):
        _mit_chatrecht(db, regular_user)
        code = _code_erzeugen(client, user_cookies)["code"]

        antwort = client.post("/api/auth/devices/redeem", json={"code": code, "label": "Laptop"})
        assert antwort.status_code == 200, antwort.text
        tokens = antwort.json()
        assert tokens["access_token"] and tokens["refresh_token"]

        # Der Anspruch im Token ist die eigentliche Zusage: nur damit bekommt
        # die KI die Werkzeuge fuer den Rechner angeboten.
        assert AuthService.decode_token(tokens["access_token"]).get("geraet") == "desktop"
        rt = AuthService.validate_refresh_token(db, tokens["refresh_token"])
        assert rt is not None and rt.geraet == "desktop"

    def test_die_herkunft_ueberlebt_die_rotation(
        self, client: TestClient, db: Session, regular_user: User, user_cookies: dict
    ):
        _mit_chatrecht(db, regular_user)
        code = _code_erzeugen(client, user_cookies)["code"]
        erste = client.post("/api/auth/devices/redeem", json={"code": code}).json()

        # Ohne diesen Durchlauf waere die App nach 15 Minuten eine gewoehnliche
        # Panel-Sitzung — und haette lautlos ihre Werkzeuge verloren.
        zweite = client.post(
            "/api/auth/refresh", json={"refresh_token": erste["refresh_token"]}
        )
        assert zweite.status_code == 200
        assert AuthService.decode_token(zweite.json()["access_token"]).get("geraet") == "desktop"

    def test_zweimal_einloesen_geht_nicht(
        self, client: TestClient, db: Session, regular_user: User, user_cookies: dict
    ):
        _mit_chatrecht(db, regular_user)
        code = _code_erzeugen(client, user_cookies)["code"]
        assert client.post("/api/auth/devices/redeem", json={"code": code}).status_code == 200
        assert client.post("/api/auth/devices/redeem", json={"code": code}).status_code == 400

    def test_abgelaufen_und_unbekannt_klingen_gleich(
        self, client: TestClient, db: Session, regular_user: User, user_cookies: dict
    ):
        _mit_chatrecht(db, regular_user)
        code = _code_erzeugen(client, user_cookies)["code"]
        einladung = db.query(DevicePairing).one()
        einladung.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()

        abgelaufen = client.post("/api/auth/devices/redeem", json={"code": code})
        unbekannt = client.post("/api/auth/devices/redeem", json={"code": "ZZZZ-ZZZZ-ZZZZ"})
        assert abgelaufen.status_code == unbekannt.status_code == 400
        assert abgelaufen.json()["detail"] == unbekannt.json()["detail"]

    def test_der_code_wird_nachsichtig_gelesen(
        self, client: TestClient, db: Session, regular_user: User, user_cookies: dict
    ):
        _mit_chatrecht(db, regular_user)
        code = _code_erzeugen(client, user_cookies)["code"]
        # Kleingeschrieben, ohne Striche, mit Leerzeichen — so tippt ein Mensch.
        entstellt = f" {code.replace('-', '').lower()} "
        assert client.post("/api/auth/devices/redeem", json={"code": entstellt}).status_code == 200

    def test_ein_gesperrter_benutzer_koppelt_nicht(
        self, client: TestClient, db: Session, regular_user: User, user_cookies: dict
    ):
        _mit_chatrecht(db, regular_user)
        code = _code_erzeugen(client, user_cookies)["code"]
        regular_user.is_active = False
        db.commit()
        assert client.post("/api/auth/devices/redeem", json={"code": code}).status_code == 400


class TestGeraeteliste:
    def test_gekoppeltes_geraet_steht_in_der_liste(
        self, client: TestClient, db: Session, regular_user: User, user_cookies: dict
    ):
        _mit_chatrecht(db, regular_user)
        code = _code_erzeugen(client, user_cookies, label="Arbeitsrechner")["code"]
        client.post("/api/auth/devices/redeem", json={"code": code})

        liste = client.get("/api/auth/devices", cookies=user_cookies).json()
        assert len(liste) == 1
        assert liste[0]["label"] == "Arbeitsrechner"
        assert liste[0]["family"]
        # Kein Token, kein Code — die Liste ist eine Anzeige, kein Tresor.
        assert set(liste[0]) == {"family", "label", "paired_at", "is_active", "last_active_at"}
        assert liste[0]["is_active"] is True
        assert liste[0]["last_active_at"] is not None

    def test_inaktives_geraet_wird_als_inaktiv_erkannt(
        self, client: TestClient, db: Session, regular_user: User, user_cookies: dict
    ):
        _mit_chatrecht(db, regular_user)
        code = _code_erzeugen(client, user_cookies, label="AltesGeraet")["code"]
        client.post("/api/auth/devices/redeem", json={"code": code})

        # Token revoken
        from models import RefreshToken
        db.query(RefreshToken).filter(RefreshToken.user_id == regular_user.id).update(
            {"revoked_at": datetime.now(timezone.utc)}
        )
        db.commit()

        liste = client.get("/api/auth/devices", cookies=user_cookies).json()
        assert len(liste) == 1
        assert liste[0]["is_active"] is False

    def test_device_heartbeat_aktualisiert_aktivitaet(
        self, client: TestClient, db: Session, regular_user: User, user_cookies: dict
    ):
        _mit_chatrecht(db, regular_user)
        code = _code_erzeugen(client, user_cookies, label="HeartbeatGeraet")["code"]
        redeem_res = client.post("/api/auth/devices/redeem", json={"code": code}).json()
        token = redeem_res["access_token"]

        hb = client.post("/api/auth/devices/heartbeat", headers={"Authorization": f"Bearer {token}"})
        assert hb.status_code == 200
        assert hb.json() == {"status": "ok"}

        liste = client.get("/api/auth/devices", cookies=user_cookies).json()
        assert len(liste) == 1
        assert liste[0]["is_active"] is True

    def test_refresh_grace_period_schuetzt_vor_rotationsabbruch(
        self, client: TestClient, db: Session, regular_user: User, user_cookies: dict
    ):
        """Wenn ein Client direkt nach einer Rotation das alte Token erneut sendet (z. B. Verbindungsabbruch), greift die Grace Period."""
        _mit_chatrecht(db, regular_user)
        code = _code_erzeugen(client, user_cookies, label="GracePeriodGeraet")["code"]
        redeem_res = client.post("/api/auth/devices/redeem", json={"code": code}).json()
        initial_refresh = redeem_res["refresh_token"]

        # Erste reguläre Rotation
        rot1 = client.post("/api/auth/refresh", json={"refresh_token": initial_refresh})
        assert rot1.status_code == 200
        rot1_data = rot1.json()
        assert rot1_data["access_token"]
        assert rot1_data["refresh_token"]

        # Erneute Anfrage mit initial_refresh innerhalb der 30s Grace Period muss gelingen
        rot_retry = client.post("/api/auth/refresh", json={"refresh_token": initial_refresh})
        assert rot_retry.status_code == 200
        assert rot_retry.json()["access_token"]
        # Keine Verzweigung (Bifurkation): Erneute Anfragen innerhalb der Grace Period erhalten exakt dasselbe Token
        assert rot_retry.json()["refresh_token"] == rot1_data["refresh_token"]

    def test_gekoppeltes_geraet_erhaelt_dauerhaftes_refresh_token(
        self, client: TestClient, db: Session, regular_user: User, user_cookies: dict
    ):
        """Refresh-Tokens für gekoppelte Geräte verfallen nicht nach 30 Tagen, sondern sind dauerhaft (10 Jahre) gültig."""
        _mit_chatrecht(db, regular_user)
        code = _code_erzeugen(client, user_cookies, label="DauerhaftesGeraet")["code"]
        redeem_res = client.post("/api/auth/devices/redeem", json={"code": code}).json()
        initial_refresh = redeem_res["refresh_token"]

        token_hash = AuthService._hash_token(initial_refresh)
        rt_db = db.query(RefreshToken).filter(RefreshToken.token_hash == token_hash).first()
        assert rt_db is not None
        expires_at = rt_db.expires_at.replace(tzinfo=timezone.utc) if rt_db.expires_at.tzinfo is None else rt_db.expires_at
        rest_tage = (expires_at - datetime.now(timezone.utc)).days
        assert rest_tage >= 3600

        # Auch nach Rotation muss das neue Refresh-Token dauerhafte Gültigkeit haben
        rot1 = client.post("/api/auth/refresh", json={"refresh_token": initial_refresh})
        assert rot1.status_code == 200
        rot1_refresh = rot1.json()["refresh_token"]

        rot_hash = AuthService._hash_token(rot1_refresh)
        rt_rot = db.query(RefreshToken).filter(RefreshToken.token_hash == rot_hash).first()
        assert rt_rot is not None
        expires_at_rot = rt_rot.expires_at.replace(tzinfo=timezone.utc) if rt_rot.expires_at.tzinfo is None else rt_rot.expires_at
        rest_tage_rot = (expires_at_rot - datetime.now(timezone.utc)).days
        assert rest_tage_rot >= 3600

    def test_wiederverwendung_ausserhalb_grace_period_revoziert_familie(
        self, client: TestClient, db: Session, regular_user: User, user_cookies: dict
    ):
        """Replay-Schutz (RFC 6749 BCP): Wird ein altes Token nach Ablauf der Grace Period wiederverwendet, wird die Familie revoziert."""
        _mit_chatrecht(db, regular_user)
        code = _code_erzeugen(client, user_cookies, label="ReplayGeraet")["code"]
        redeem_res = client.post("/api/auth/devices/redeem", json={"code": code}).json()
        initial_refresh = redeem_res["refresh_token"]

        # Erste reguläre Rotation
        rot1 = client.post("/api/auth/refresh", json={"refresh_token": initial_refresh})
        assert rot1.status_code == 200
        rot1_refresh = rot1.json()["refresh_token"]

        # Künstlich used_at auf 60 Sekunden in die Vergangenheit setzen (außerhalb der 30s Grace Period)
        token_hash = AuthService._hash_token(initial_refresh)
        rt_db = db.query(RefreshToken).filter(RefreshToken.token_hash == token_hash).first()
        assert rt_db is not None
        rt_db.used_at = datetime.now(timezone.utc) - timedelta(seconds=60)
        db.commit()

        # Replay-Versuch mit initial_refresh außerhalb der Grace Period
        replay = client.post("/api/auth/refresh", json={"refresh_token": initial_refresh})
        assert replay.status_code == 401

        # Nun muss die GESAMTE Familie revoziert sein — auch das legitime rot1_refresh ist jetzt ungültig!
        rot2 = client.post("/api/auth/refresh", json={"refresh_token": rot1_refresh})
        assert rot2.status_code == 401

    def test_logout_mit_abgelaufenem_access_token_und_refresh_token(
        self, client: TestClient, db: Session, regular_user: User, user_cookies: dict
    ):
        """Logout widerruft Familie auch wenn das Access-Token fehlt oder abgelaufen ist."""
        _mit_chatrecht(db, regular_user)
        code = _code_erzeugen(client, user_cookies, label="LogoutGeraet")["code"]
        redeem_res = client.post("/api/auth/devices/redeem", json={"code": code}).json()
        refresh_tok = redeem_res["refresh_token"]

        # Logout nur mit Refresh-Token (kein Access-Token im Authorization-Header)
        res = client.post("/api/auth/logout", json={"refresh_token": refresh_tok})
        assert res.status_code == 200

        # Familie muss revoziert sein
        assert client.post("/api/auth/refresh", json={"refresh_token": refresh_tok}).status_code == 401



    def test_ein_nicht_eingeloester_code_taucht_nicht_auf(
        self, client: TestClient, db: Session, regular_user: User, user_cookies: dict
    ):
        _mit_chatrecht(db, regular_user)
        _code_erzeugen(client, user_cookies)
        assert client.get("/api/auth/devices", cookies=user_cookies).json() == []

    def test_entziehen_sperrt_genau_dieses_geraet_aus(
        self, client: TestClient, db: Session, regular_user: User, user_cookies: dict
    ):
        _mit_chatrecht(db, regular_user)
        erster = _code_erzeugen(client, user_cookies, label="Eins")["code"]
        zweiter = _code_erzeugen(client, user_cookies, label="Zwei")["code"]
        eins = client.post("/api/auth/devices/redeem", json={"code": erster}).json()
        zwei = client.post("/api/auth/devices/redeem", json={"code": zweiter}).json()

        liste = client.get("/api/auth/devices", cookies=user_cookies).json()
        familie_eins = next(g["family"] for g in liste if g["label"] == "Eins")

        antwort = client.delete(
            f"/api/auth/devices/{familie_eins}", cookies=user_cookies, headers=_kopf(user_cookies)
        )
        assert antwort.status_code == 200

        # Das entzogene Geraet bekommt keine neue Sitzung mehr …
        assert client.post(
            "/api/auth/refresh", json={"refresh_token": eins["refresh_token"]}
        ).status_code == 401
        # … das andere schon. Ein Widerruf, der beide traefe, waere schlimmer
        # als keiner: niemand entzieht dann noch etwas.
        assert client.post(
            "/api/auth/refresh", json={"refresh_token": zwei["refresh_token"]}
        ).status_code == 200

        uebrig = client.get("/api/auth/devices", cookies=user_cookies).json()
        assert [g["label"] for g in uebrig] == ["Zwei"]

    def test_eine_fremde_familie_ist_nicht_zu_treffen(
        self, client: TestClient, db: Session, regular_user: User, owner_user: User,
        user_cookies: dict,
    ):
        _mit_chatrecht(db, regular_user)
        code = _code_erzeugen(client, user_cookies)["code"]
        eigene = client.post("/api/auth/devices/redeem", json={"code": code}).json()
        familie = db.query(DevicePairing).one().family

        # Der Owner kennt die Kennung — treffen darf er sie trotzdem nicht.
        fremd = client.post("/api/auth/login", json={
            "username": "owner", "password": "OwnerPass123!", "otp_code": None,
        })
        fremde_kekse = dict(fremd.cookies)
        antwort = client.delete(
            f"/api/auth/devices/{familie}", cookies=fremde_kekse, headers=_kopf(fremde_kekse)
        )
        assert antwort.status_code == 404
        assert client.post(
            "/api/auth/refresh", json={"refresh_token": eigene["refresh_token"]}
        ).status_code == 200

    def test_browser_logout_laesst_gekoppelte_geraete_aktiv(
        self, client: TestClient, db: Session, regular_user: User, user_cookies: dict
    ):
        _mit_chatrecht(db, regular_user)
        code = _code_erzeugen(client, user_cookies)["code"]
        geraet = client.post("/api/auth/devices/redeem", json={"code": code}).json()

        # Browser meldet sich ab
        logout_res = client.post(
            "/api/auth/logout", cookies=user_cookies, headers=_kopf(user_cookies)
        )
        assert logout_res.status_code == 200

        # Gekoppeltes Companion-Gerät kann sein Refresh-Token weiterhin erneuern
        refresh_res = client.post(
            "/api/auth/refresh", json={"refresh_token": geraet["refresh_token"]}
        )
        assert refresh_res.status_code == 200
        assert "access_token" in refresh_res.json()


class TestAufraeumen:
    def test_abgelaufene_einladungen_verschwinden(self, db: Session, regular_user: User):
        einladung, _ = device_pairing_service.anlegen(db, regular_user, "alt")
        einladung.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        db.commit()

        device_pairing_service.aufraeumen(db)
        assert db.query(DevicePairing).count() == 0

    def test_eingeloeste_bleiben_stehen(self, db: Session, regular_user: User):
        # An ihnen haengt der Name des Geraets. Wer sie mit aufraeumt, nimmt
        # dem Benutzer die Moeglichkeit, das richtige zu entziehen.
        einladung, code = device_pairing_service.anlegen(db, regular_user, "Laptop")
        device_pairing_service.einloesen(db, code)
        device_pairing_service.familie_vermerken(db, einladung, "fam-1")
        einladung.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        db.commit()

        device_pairing_service.aufraeumen(db)
        assert db.query(DevicePairing).count() == 1


class TestHerkunftAmToken:
    def test_eine_browser_sitzung_ist_panel(
        self, client: TestClient, db: Session, regular_user: User, user_cookies: dict
    ):
        marke = user_cookies["__Secure-access_token"]
        assert "geraet" not in AuthService.decode_token(marke)

    def test_der_client_kann_die_herkunft_nicht_mehr_behaupten(self):
        # Das Feld ist aus dem Schema verschwunden, nicht nur ignoriert:
        # solange es dastand, konnte jeder mit einer gueltigen Sitzung sich als
        # App ausgeben und nach Maus und Tastatur greifen.
        from schemas.ai_chat import AiChatRequest

        assert "herkunft" not in AiChatRequest.model_fields

    def test_die_abhaengigkeit_liest_den_anspruch(self, db: Session, regular_user: User):
        from dependencies import session_herkunft

        from starlette.datastructures import Headers

        class _Anfrage:
            def __init__(self, marke: str | None):
                # Echte Header-Struktur, nicht ein dict: Starlette liest
                # gross-/kleinschreibungsunabhaengig, ein dict nicht — und ein
                # Test, der deswegen "panel" misst, prueft gar nichts.
                self.headers = Headers({"authorization": f"Bearer {marke}"} if marke else {})
                self.cookies = {}

        panel = AuthService.create_access_token(
            {"sub": regular_user.username, "user_id": regular_user.id, "jti": "a"}
        )
        desktop = AuthService.create_access_token(
            {"sub": regular_user.username, "user_id": regular_user.id, "jti": "b",
             "geraet": "desktop"}
        )
        assert session_herkunft(_Anfrage(panel)) == "panel"
        assert session_herkunft(_Anfrage(desktop)) == "desktop"
        # Alles Unklare faellt auf die engere Seite.
        assert session_herkunft(_Anfrage(None)) == "panel"
        assert session_herkunft(_Anfrage("kaputt")) == "panel"


class TestPairingStatus:
    def test_status_vor_und_nach_dem_einloesen(
        self, client: TestClient, db: Session, regular_user: User, user_cookies: dict
    ):
        _mit_chatrecht(db, regular_user)
        daten = _code_erzeugen(client, user_cookies)
        code = daten["code"]

        # 1. Vor dem Einlösen: exists=True, redeemed=False
        st1 = client.get(
            f"/api/auth/devices/pairing/{code}/status",
            cookies=user_cookies,
            headers=_kopf(user_cookies),
        )
        assert st1.status_code == 200
        assert st1.json()["exists"] is True
        assert st1.json()["redeemed"] is False
        assert st1.json()["expired"] is False

        # 2. Einlösen vom Gerät
        redeem_res = client.post("/api/auth/devices/redeem", json={"code": code})
        assert redeem_res.status_code == 200

        # 3. Nach dem Einlösen: exists=True, redeemed=True
        st2 = client.get(
            f"/api/auth/devices/pairing/{code}/status",
            cookies=user_cookies,
            headers=_kopf(user_cookies),
        )
        assert st2.status_code == 200
        assert st2.json()["exists"] is True
        assert st2.json()["redeemed"] is True



def _valid_rsa_jwk(marker: str = "A") -> str:
    """Ein oeffentlicher Schluessel, der `validate_rsa_public_key_jwk` besteht."""
    import json

    return json.dumps(
        {"kty": "RSA", "n": marker * 350, "e": "AQAB", "alg": "RSA-OAEP", "use": "enc"}
    )


def _geraet_melden(client: TestClient, token: str, kennung: str, marker: str = "A") -> None:
    """Das frisch gekoppelte Geraet veroeffentlicht seinen E2EE-Schluessel."""
    antwort = client.put(
        "/api/social/e2ee/devices/self",
        json={"device_id": kennung, "public_key": _valid_rsa_jwk(marker), "label": "Neu"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert antwort.status_code == 200, antwort.text


class TestVerlaufsErstabgleich:
    """Der Verlauf zieht auf das frisch gekoppelte Geraet um.

    Ein neues Geraet hat keine Ratchet-Sitzungen und liest nichts
    Rueckwirkendes aus der Mailbox. Ohne diesen Weg staende es vor einem leeren
    Gespraech, obwohl daneben ein Geraet desselben Kontos alles hat. Der Server
    reicht dabei einen versiegelten Textblock durch — er kann ihn nicht oeffnen
    und soll ihn nicht aufbewahren.
    """

    def test_der_verlauf_geht_einmal_hinueber_und_ist_danach_weg(
        self, client: TestClient, db: Session, regular_user: User, user_cookies: dict
    ):
        _mit_chatrecht(db, regular_user)
        code = _code_erzeugen(client, user_cookies)["code"]
        tokens = client.post("/api/auth/devices/redeem", json={"code": code}).json()
        _geraet_melden(client, tokens["access_token"], "a1b2c3d4e5f60718")

        # Das Panel sieht jetzt, fuer wen es versiegeln muss.
        status = client.get(
            f"/api/auth/devices/pairing/{code}/status",
            cookies=user_cookies,
            headers=_kopf(user_cookies),
        ).json()
        assert status["redeemed"] is True
        assert [g["device_id"] for g in status["neue_geraete"]] == ["a1b2c3d4e5f60718"]
        assert status["verlauf_abgelegt"] is False
        # Name und Zeitpunkt gehen mit: das Panel zeigt sie neben der
        # Sicherheitsnummer, bevor es den Verlauf hergibt. Ohne sie hiesse die
        # Rueckfrage nur „ein Geraet" — und welches, wuesste niemand.
        neu = status["neue_geraete"][0]
        assert neu["label"] == "Neu"
        assert neu["created_at"]

        ablegen = client.put(
            f"/api/auth/devices/pairing/{code}/verlauf",
            json={"blob": "sv-e2ee-hybrid-v1:versiegelt"},
            cookies=user_cookies,
            headers=_kopf(user_cookies),
        )
        assert ablegen.status_code == 200, ablegen.text

        # Das Geraet holt ab — mit seiner eigenen Sitzung.
        kopf = {"Authorization": f"Bearer {tokens['access_token']}"}
        erst = client.get(f"/api/auth/devices/pairing/{code}/verlauf", headers=kopf)
        assert erst.status_code == 200
        assert erst.json()["blob"] == "sv-e2ee-hybrid-v1:versiegelt"

        # Und danach liegt hier nichts mehr. Liegenzubleiben waere der einzige
        # Weg, wie der Blob doch noch in ein Backup geraet.
        zweit = client.get(f"/api/auth/devices/pairing/{code}/verlauf", headers=kopf)
        assert zweit.status_code == 200
        assert zweit.json()["blob"] is None
        einladung = db.query(DevicePairing).filter_by(user_id=regular_user.id).first()
        db.refresh(einladung)
        assert einladung.verlauf_blob is None

    def test_vor_dem_einloesen_wird_nichts_abgelegt(
        self, client: TestClient, db: Session, regular_user: User, user_cookies: dict
    ):
        # Vorher gibt es kein Geraet, fuer das versiegelt werden koennte. Ein
        # Blob an einem offenen Code waere eine Ablage ohne Abnehmer.
        _mit_chatrecht(db, regular_user)
        code = _code_erzeugen(client, user_cookies)["code"]

        antwort = client.put(
            f"/api/auth/devices/pairing/{code}/verlauf",
            json={"blob": "sv-e2ee-hybrid-v1:zu-frueh"},
            cookies=user_cookies,
            headers=_kopf(user_cookies),
        )
        assert antwort.status_code == 400

    def test_nur_geraete_von_nach_dem_einloesen_zaehlen(
        self, client: TestClient, db: Session, regular_user: User, user_cookies: dict
    ):
        """Ein Geraet, das schon vorher da war, ist nicht das neue.

        Es braucht den Erstabgleich auch nicht — sein Verlauf liegt noch bei
        ihm. Waere es hier gelistet, versiegelte das Panel gegen das falsche
        Geraet und das neue bekaeme nichts.
        """
        _mit_chatrecht(db, regular_user)
        from models import UserE2eeDevice

        db.add(
            UserE2eeDevice(
                user_id=regular_user.id,
                device_id="altesgeraet000000",
                public_key_jwk=_valid_rsa_jwk("Z"),
                label="Alt",
                created_at=datetime.now(timezone.utc) - timedelta(days=3),
            )
        )
        db.commit()

        code = _code_erzeugen(client, user_cookies)["code"]
        tokens = client.post("/api/auth/devices/redeem", json={"code": code}).json()
        _geraet_melden(client, tokens["access_token"], "neuesgeraet000000", "B")

        status = client.get(
            f"/api/auth/devices/pairing/{code}/status",
            cookies=user_cookies,
            headers=_kopf(user_cookies),
        ).json()
        assert [g["device_id"] for g in status["neue_geraete"]] == ["neuesgeraet000000"]

    def test_ein_fremdes_konto_kommt_nicht_an_den_blob(
        self,
        client: TestClient,
        db: Session,
        regular_user: User,
        user_cookies: dict,
        owner_user: User,
        owner_cookies: dict,
    ):
        _mit_chatrecht(db, regular_user)
        _mit_chatrecht(db, owner_user)
        code = _code_erzeugen(client, user_cookies)["code"]
        tokens = client.post("/api/auth/devices/redeem", json={"code": code}).json()
        _geraet_melden(client, tokens["access_token"], "a1b2c3d4e5f60718")
        client.put(
            f"/api/auth/devices/pairing/{code}/verlauf",
            json={"blob": "sv-e2ee-hybrid-v1:versiegelt"},
            cookies=user_cookies,
            headers=_kopf(user_cookies),
        )

        # Der Code gehoert einem anderen Konto: die Einladung wird gar nicht
        # erst gefunden.
        fremd = client.get(
            f"/api/auth/devices/pairing/{code}/verlauf",
            cookies=owner_cookies,
            headers=_kopf(owner_cookies),
        )
        assert fremd.status_code == 200
        assert fremd.json()["blob"] is None

        fremd_ablegen = client.put(
            f"/api/auth/devices/pairing/{code}/verlauf",
            json={"blob": "sv-e2ee-hybrid-v1:untergeschoben"},
            cookies=owner_cookies,
            headers=_kopf(owner_cookies),
        )
        assert fremd_ablegen.status_code == 400

        # Und der echte Blob liegt unveraendert fuer sein Geraet bereit.
        eigen = client.get(
            f"/api/auth/devices/pairing/{code}/verlauf",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        assert eigen.json()["blob"] == "sv-e2ee-hybrid-v1:versiegelt"

    def test_zu_gross_wird_abgewiesen(
        self, client: TestClient, db: Session, regular_user: User, user_cookies: dict
    ):
        _mit_chatrecht(db, regular_user)
        code = _code_erzeugen(client, user_cookies)["code"]
        tokens = client.post("/api/auth/devices/redeem", json={"code": code}).json()
        _geraet_melden(client, tokens["access_token"], "a1b2c3d4e5f60718")

        antwort = client.put(
            f"/api/auth/devices/pairing/{code}/verlauf",
            json={"blob": "x" * (device_pairing_service.MAX_VERLAUF_BYTES + 1)},
            cookies=user_cookies,
            headers=_kopf(user_cookies),
        )
        assert antwort.status_code == 422

    def test_der_blob_stirbt_mit_der_uebergabe(
        self, client: TestClient, db: Session, regular_user: User, user_cookies: dict
    ):
        """Eingeloeste Zeilen bleiben liegen — ihr Verlaufsblob darf das nicht.

        Hat das neue Geraet ihn bis zum Ende der Uebergabe nicht geholt, ist
        der Erstabgleich gescheitert und der Blob hat keinen Zweck mehr.
        """
        _mit_chatrecht(db, regular_user)
        code = _code_erzeugen(client, user_cookies)["code"]
        tokens = client.post("/api/auth/devices/redeem", json={"code": code}).json()
        _geraet_melden(client, tokens["access_token"], "a1b2c3d4e5f60718")
        client.put(
            f"/api/auth/devices/pairing/{code}/verlauf",
            json={"blob": "sv-e2ee-hybrid-v1:versiegelt"},
            cookies=user_cookies,
            headers=_kopf(user_cookies),
        )

        einladung = db.query(DevicePairing).filter_by(user_id=regular_user.id).first()
        jetzt = datetime.now(timezone.utc)
        einladung.expires_at = jetzt - timedelta(minutes=5)
        einladung.redeemed_at = jetzt - timedelta(
            minutes=device_pairing_service.FRIST_MINUTEN, seconds=1
        )
        db.commit()

        device_pairing_service.aufraeumen(db)

        db.refresh(einladung)
        # Die Zeile bleibt — an ihr haengt die Geraetefamilie.
        assert einladung.redeemed_at is not None
        assert einladung.verlauf_blob is None
        assert einladung.verlauf_abgelegt_am is None

    def _eingeloest_und_gemeldet(
        self, client: TestClient, db: Session, user: User, cookies: dict
    ) -> tuple[str, dict, DevicePairing]:
        _mit_chatrecht(db, user)
        code = _code_erzeugen(client, cookies)["code"]
        tokens = client.post("/api/auth/devices/redeem", json={"code": code}).json()
        _geraet_melden(client, tokens["access_token"], "a1b2c3d4e5f60718")
        einladung = db.query(DevicePairing).filter_by(user_id=user.id).first()
        return code, {"Authorization": f"Bearer {tokens['access_token']}"}, einladung

    def test_die_uebergabe_laeuft_ab_dem_einloesen(
        self, client: TestClient, db: Session, regular_user: User, user_cookies: dict
    ):
        """Zwischen Einloesen und Uebergabe liegt eine Rueckfrage.

        Im Panel vergleicht ein Mensch die Sicherheitsnummer, bevor der Verlauf
        hinuebergeht — das dauert Minuten. Galt dafuer die Frist des Codes,
        scheiterte eine Kopplung, die kurz vor Ablauf eingeloest wurde, am
        Ablegen: Code um 12:00, eingeloest um 12:08, bestaetigt um 12:10:30.
        Das Geraet wartete danach bis 12:18 auf einen Verlauf, der nie kam.
        """
        code, bearer, einladung = self._eingeloest_und_gemeldet(
            client, db, regular_user, user_cookies
        )
        jetzt = datetime.now(timezone.utc)
        einladung.expires_at = jetzt - timedelta(seconds=30)
        einladung.redeemed_at = jetzt - timedelta(minutes=2, seconds=30)
        db.commit()

        ablegen = client.put(
            f"/api/auth/devices/pairing/{code}/verlauf",
            json={"blob": "sv-e2ee-hybrid-v1:versiegelt"},
            cookies=user_cookies,
            headers=_kopf(user_cookies),
        )
        assert ablegen.status_code == 200, ablegen.text

        # Das Aufraeumen richtet sich nach derselben Frist.
        device_pairing_service.aufraeumen(db)

        abholen = client.get(f"/api/auth/devices/pairing/{code}/verlauf", headers=bearer)
        assert abholen.status_code == 200
        assert abholen.json()["blob"] == "sv-e2ee-hybrid-v1:versiegelt"

    def test_die_uebergabe_endet_nach_der_frist_ab_dem_einloesen(
        self, client: TestClient, db: Session, regular_user: User, user_cookies: dict
    ):
        # Ein eingeloester Code ist kein Dauerauftrag: nach der Frist nimmt der
        # Server nichts mehr an und gibt nichts mehr heraus.
        code, bearer, einladung = self._eingeloest_und_gemeldet(
            client, db, regular_user, user_cookies
        )
        client.put(
            f"/api/auth/devices/pairing/{code}/verlauf",
            json={"blob": "sv-e2ee-hybrid-v1:versiegelt"},
            cookies=user_cookies,
            headers=_kopf(user_cookies),
        )
        jetzt = datetime.now(timezone.utc)
        einladung.redeemed_at = jetzt - timedelta(
            minutes=device_pairing_service.FRIST_MINUTEN, seconds=1
        )
        db.commit()

        abholen = client.get(f"/api/auth/devices/pairing/{code}/verlauf", headers=bearer)
        assert abholen.json()["blob"] is None
        ablegen = client.put(
            f"/api/auth/devices/pairing/{code}/verlauf",
            json={"blob": "sv-e2ee-hybrid-v1:zu-spaet"},
            cookies=user_cookies,
            headers=_kopf(user_cookies),
        )
        assert ablegen.status_code == 400
