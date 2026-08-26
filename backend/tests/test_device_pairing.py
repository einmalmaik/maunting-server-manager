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
        assert set(liste[0]) == {"family", "label", "paired_at"}

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
