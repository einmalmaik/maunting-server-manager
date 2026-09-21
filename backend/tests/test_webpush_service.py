"""Die Zustellung an einen Empfänger, der die Anwendung geschlossen hat.

Wer entscheiden darf, steht in `test_notification_dispatcher_and_privacy.py`.
Hier geht es nur darum, ob das Entschiedene auch ankommt — und ob auf dem Weg
dorthin nichts passiert, was nicht passieren darf.

Die beiden Prüfungen, an denen am meisten hängt:

* **Der Vektor aus RFC 8291.** Die Verschlüsselung ist hier von Hand
  zusammengesetzt statt aus einer Bibliothek (Begründung im Modulkopf von
  `services/webpush_service.py`). Ein Tippfehler in einer `info`-Zeichenkette
  fiele sonst nirgends auf: der Push-Dienst nimmt jeden Körper an, und erst der
  Browser des Empfängers verwirft ihn stumm. Der Vektor macht daraus einen
  Fehlschlag im Testlauf.
* **Eine Störung löscht kein Abonnement.** Ein Abo wegen einer 500 des
  Push-Dienstes zu entfernen wäre der teuerste denkbare Fehler — der Benutzer
  bekäme nie wieder etwas und sähe nirgends, warum.
"""

from __future__ import annotations

import base64
import json
import logging

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from jose import jwt
from sqlalchemy.orm import Session

from models import PushSubscription, User
from services import webpush_service
from services.notification_service import NotificationService
from services.webpush_service import _b64d, _b64e


# Ein formal gültiges Abonnement, mit dem nicht wirklich gesendet wird.
# Der Punkt stammt aus dem Testvektor des RFC, ist also ein echter P-256-Punkt.
GUELTIGER_PUNKT = "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"
GUELTIGES_AUTH = "BTBZMqHH6r4Tts7J_aSIgg"
ENDPUNKT = "https://fcm.googleapis.com/fcm/send/beispiel-eins"


def _umschlag(nummer: int) -> str:
    """Ein wohlgeformter `sv-e2ee-dr-v1:`-Umschlag.

    Vier Felder — Absenderkonto, Absendergeraet, Empfaengergeraet, Rumpf.
    `validate_e2ee_envelope_format` prueft das, und ein Relais mit einer
    Attrappe wuerde schon dort scheitern statt beim Versand.
    """
    rumpf = base64.b64encode(
        b'sv-dr-msg-v1:{"header":{"v":"sv-dr-msg-v1","dh":"AA","pn":0,"n":%d}}' % nummer
    ).decode("ascii")
    return f"sv-e2ee-dr-v1:7.{'a' * 32}.{'b' * 32}.{rumpf}"


def _abo_anlegen(db: Session, user: User, endpunkt: str = ENDPUNKT) -> PushSubscription:
    return webpush_service.eintragen(
        db, user, endpoint=endpunkt, p256dh=GUELTIGER_PUNKT, auth=GUELTIGES_AUTH
    )


# ── Verschlüsselung ─────────────────────────────────────────────────────────


def test_verschluesselung_trifft_den_vektor_aus_rfc_8291():
    """Byte für Byte der Körper aus RFC 8291 §5.

    Salz und Serverschlüssel kommen hier aus dem RFC statt aus dem
    Zufallsgenerator — nur deshalb ist das Ergebnis überhaupt vergleichbar. Im
    Betrieb zieht `_verschluesseln` beides selbst; wer das ändert, bricht
    AES-GCM.
    """
    ergebnis = webpush_service._verschluesseln(
        b"When I grow up, I want to be a watermelon",
        GUELTIGER_PUNKT,
        GUELTIGES_AUTH,
        salz=_b64d("DGv6ra1nlYgDCS1FRnbzlw"),
        server_privat=ec.derive_private_key(
            int.from_bytes(_b64d("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"), "big"),
            ec.SECP256R1(),
        ),
    )

    assert _b64e(ergebnis) == (
        "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLoc"
        "InmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLV"
        "WGNWQexSgSxsj_Qulcy4a-fN"
    )


def test_jeder_versand_bekommt_ein_eigenes_salz():
    """Zweimal dasselbe verschlüsseln darf nie dasselbe ergeben.

    Ein wiederverwendetes Salz-Schlüssel-Paar bricht AES-GCM. Der Test hält
    fest, dass die Vorgaben aus dem Vektor-Test oben wirklich nur dort gelten.
    """
    erste = webpush_service._verschluesseln(b"hallo", GUELTIGER_PUNKT, GUELTIGES_AUTH)
    zweite = webpush_service._verschluesseln(b"hallo", GUELTIGER_PUNKT, GUELTIGES_AUTH)
    assert erste != zweite
    assert erste[:16] != zweite[:16]  # das Salz steht vorn im Kopf


def test_zu_grosse_nutzlast_wird_abgelehnt():
    with pytest.raises(ValueError):
        webpush_service._verschluesseln(
            b"x" * (webpush_service.MAX_NUTZLAST_BYTES + 1), GUELTIGER_PUNKT, GUELTIGES_AUTH
        )


# ── VAPID ───────────────────────────────────────────────────────────────────


def test_vapid_token_gilt_nur_fuer_diesen_push_dienst():
    """`aud` ist der Ursprung des Endpunkts — anderswo ist das Token wertlos."""
    pem, oeffentlich = webpush_service._neues_paar()
    kopf = webpush_service._vapid_kopf(
        "https://updates.push.services.mozilla.com/wpush/v2/abc", pem, oeffentlich
    )

    token = kopf["Authorization"].split("t=")[1].split(",")[0]
    assert jwt.get_unverified_header(token)["alg"] == "ES256"
    assert jwt.get_unverified_claims(token)["aud"] == "https://updates.push.services.mozilla.com"
    assert f"k={oeffentlich}" in kopf["Authorization"]


def test_der_oeffentliche_schluessel_bleibt_derselbe(db: Session):
    """Ein Wechsel entwertet **jedes** bestehende Abonnement.

    Der Browser bindet sein Abo an den Schlüssel, mit dem er abonniert hat.
    Erzeugt das Panel bei jedem Aufruf ein neues Paar, antwortet der
    Push-Dienst danach auf jede Zustellung mit 403 — stillschweigend, für alle
    Benutzer auf einmal.
    """
    erster = webpush_service.oeffentlicher_schluessel(db)
    assert erster
    assert len(_b64d(erster)) == 65  # unkomprimierter P-256-Punkt

    assert webpush_service.oeffentlicher_schluessel(db) == erster
    assert webpush_service.oeffentlicher_schluessel(db) == erster


def test_der_private_schluessel_liegt_nicht_im_klartext(db: Session):
    """Die Zeile in `panel_settings` darf kein lesbares PEM sein."""
    from models import PanelSetting

    webpush_service.oeffentlicher_schluessel(db)
    zeile = db.query(PanelSetting).filter_by(key=webpush_service.SCHLUESSEL_PRIVAT).first()

    assert zeile is not None
    assert "BEGIN PRIVATE KEY" not in zeile.value


# ── SSRF ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "adresse",
    [
        "http://fcm.googleapis.com/fcm/send/x",  # kein TLS
        "https://127.0.0.1:8000/api/admin",
        "https://localhost/api/admin",
        "https://10.0.0.5/",
        "https://192.168.1.1/",
        "https://169.254.169.254/latest/meta-data/",  # Cloud-Metadaten
        "https://[::1]/",
        "ftp://fcm.googleapis.com/x",
        "",
    ],
)
def test_interne_und_unsichere_ziele_werden_abgelehnt(adresse: str):
    """Die Adresse kommt aus dem Browser. Ohne diese Schranke wäre das
    Eintragen eines Abonnements ein Weg, das Panel gegen sein eigenes Netz
    POSTen zu lassen."""
    assert webpush_service._ziel_ist_erlaubt(adresse) is False


def test_echte_push_dienste_kommen_durch():
    """Die Schranke darf den eigentlichen Zweck nicht mit erschlagen."""
    assert webpush_service._ziel_ist_erlaubt(ENDPUNKT) is True
    assert (
        webpush_service._ziel_ist_erlaubt(
            "https://updates.push.services.mozilla.com/wpush/v2/abc"
        )
        is True
    )


def test_eintragen_lehnt_eine_interne_adresse_ab(db: Session, owner_user: User):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as fehler:
        _abo_anlegen(db, owner_user, "https://127.0.0.1:8000/api/admin")
    assert fehler.value.status_code == 400
    assert db.query(PushSubscription).count() == 0


def test_eintragen_lehnt_unbrauchbare_schluessel_ab(db: Session, owner_user: User):
    """Ein kaputter Punkt fällt beim Eintragen auf und nicht später im
    Hintergrundfaden, wo ihn niemand sieht."""
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as fehler:
        webpush_service.eintragen(
            db, owner_user, endpoint=ENDPUNKT, p256dh="nicht-base64-und-kein-punkt", auth=GUELTIGES_AUTH
        )
    assert fehler.value.status_code == 400

    with pytest.raises(HTTPException):
        webpush_service.eintragen(
            db, owner_user, endpoint=ENDPUNKT, p256dh=GUELTIGER_PUNKT, auth=_b64e(b"zu kurz")
        )
    assert db.query(PushSubscription).count() == 0


# ── Eintragen und austragen ─────────────────────────────────────────────────


def test_dieselbe_adresse_wechselt_das_konto_statt_sich_zu_verdoppeln(
    db: Session, owner_user: User, regular_user: User
):
    """Der geteilte Browser.

    Eine Endpunkt-Adresse gehört zu einer Browserinstallation. Meldet sich dort
    ein anderes Konto an, muss die Zeile mitwandern — bliebe sie beim alten
    Konto, bekäme der neue Benutzer die Benachrichtigungen des vorherigen auf
    seinen Bildschirm.
    """
    _abo_anlegen(db, owner_user)
    assert db.query(PushSubscription).count() == 1

    _abo_anlegen(db, regular_user)

    abos = db.query(PushSubscription).all()
    assert len(abos) == 1
    assert abos[0].user_id == regular_user.id


def test_ein_konto_darf_mehrere_geraete_haben(db: Session, owner_user: User):
    """Zugestellt wird an Geräte, nicht an Konten — derselbe Gedanke wie bei
    `user_e2ee_devices`."""
    _abo_anlegen(db, owner_user, "https://fcm.googleapis.com/fcm/send/telefon")
    _abo_anlegen(db, owner_user, "https://fcm.googleapis.com/fcm/send/rechner")

    assert db.query(PushSubscription).filter_by(user_id=owner_user.id).count() == 2


def test_austragen_trifft_nur_die_eigene_adresse(
    db: Session, owner_user: User, regular_user: User
):
    _abo_anlegen(db, owner_user)

    assert webpush_service.austragen(db, regular_user, ENDPUNKT) is False
    assert db.query(PushSubscription).count() == 1

    assert webpush_service.austragen(db, owner_user, ENDPUNKT) is True
    assert db.query(PushSubscription).count() == 0


# ── Versand ─────────────────────────────────────────────────────────────────


@pytest.fixture
def abgefangener_versand(monkeypatch):
    """Hält fest, was an den Hintergrundfaden übergeben wird, statt es zu senden."""
    uebergeben: list[tuple] = []

    def _merken(funktion, *args, **kwargs):
        uebergeben.append(args)

    monkeypatch.setattr(webpush_service._versender, "submit", _merken)
    return uebergeben


def test_ohne_abonnement_geht_nichts_hinaus(db: Session, owner_user: User, abgefangener_versand):
    assert webpush_service.sende_an_konto(db, owner_user.id, {"title": "Neue Nachricht"}) == 0
    assert abgefangener_versand == []


def test_abgeschaltete_benachrichtigungen_senden_nichts(
    db: Session, owner_user: User, abgefangener_versand
):
    """`device_notifications` gilt bei geschlossener Anwendung genauso wie im
    Vordergrund. Ein Schalter, der nur die eine Hälfte trifft, ist keiner."""
    _abo_anlegen(db, owner_user)
    owner_user.device_notifications = False
    db.commit()

    assert webpush_service.sende_an_konto(db, owner_user.id, {"title": "Neue Nachricht"}) == 0
    assert abgefangener_versand == []


def test_ein_inaktives_konto_bekommt_nichts(db: Session, owner_user: User, abgefangener_versand):
    _abo_anlegen(db, owner_user)
    owner_user.is_active = False
    db.commit()

    assert webpush_service.sende_an_konto(db, owner_user.id, {"title": "Neue Nachricht"}) == 0


def test_versand_geht_an_jedes_geraet(db: Session, owner_user: User, abgefangener_versand):
    _abo_anlegen(db, owner_user, "https://fcm.googleapis.com/fcm/send/telefon")
    _abo_anlegen(db, owner_user, "https://fcm.googleapis.com/fcm/send/rechner")

    assert webpush_service.sende_an_konto(db, owner_user.id, {"title": "Neue Nachricht"}) == 2

    ziele, koerper, _pem, _oeff = abgefangener_versand[0]
    assert len(ziele) == 2
    assert json.loads(koerper)["title"] == "Neue Nachricht"


def test_was_hinausgeht_traegt_keinen_inhalt(db: Session, owner_user: User, abgefangener_versand):
    """Der ganze Weg von der Entscheidung bis zum Körper, an einem Stück.

    Zwischen `prepare_push_dispatch` und dem Versand darf kein Klartext wieder
    auftauchen — auch nicht über `extra_data`, das ein Aufrufer frei füllt.
    """
    _abo_anlegen(db, owner_user)

    nutzlast = NotificationService.prepare_push_dispatch(
        target_user_id=owner_user.id,
        sender_user_id=999,
        title="Neue Nachricht",
        body="Treffen wir uns um 18 Uhr am Bahnhof?",
        is_e2ee=True,
        extra_data={
            "text": "Treffen wir uns um 18 Uhr am Bahnhof?",
            "ciphertext_envelope": "sv-e2ee-dr-v1:AAAA",
            "key": "geheim",
        },
    )
    assert nutzlast is not None
    webpush_service.sende_an_konto(db, owner_user.id, nutzlast)

    _ziele, koerper, _pem, _oeff = abgefangener_versand[0]
    text = koerper.decode("utf-8")
    assert "Bahnhof" not in text
    assert "sv-e2ee-dr-v1" not in text
    assert "geheim" not in text
    assert json.loads(text)["body"] == "Du hast eine neue verschlüsselte Nachricht erhalten."


# ── Zustellung und tote Adressen ────────────────────────────────────────────


class _Antwort:
    def __init__(self, status_code: int):
        self.status_code = status_code


def _client_der(status: int | Exception, gesehen: list | None = None):
    class _Client:
        def post(self, url, content=None, headers=None):
            if gesehen is not None:
                gesehen.append((url, content, headers))
            if isinstance(status, Exception):
                raise status
            return _Antwort(status)

    return _Client()


@pytest.mark.parametrize("status", [404, 410])
def test_ein_verschwundener_browser_wird_ausgetragen(status: int):
    """404 und 410 sind die einzigen Antworten, die etwas über das Abonnement
    sagen: diesen Browser gibt es nicht mehr."""
    pem, oeff = webpush_service._neues_paar()
    lebt = webpush_service._zustellen(
        _client_der(status), ENDPUNKT, GUELTIGER_PUNKT, GUELTIGES_AUTH, b'{"title":"x"}', pem, oeff
    )
    assert lebt is False


@pytest.mark.parametrize(
    "stoerung", [500, 502, 429, 403, httpx.ConnectError("kein Netz"), httpx.ReadTimeout("zu lang")]
)
def test_eine_stoerung_entfernt_kein_abonnement(stoerung):
    """Der teuerste denkbare Fehler.

    Eine 500 des Push-Dienstes oder ein Netzwerkaussetzer sagen nichts über das
    Abonnement. Würde es dabei gelöscht, bekäme der Benutzer nie wieder etwas
    und sähe nirgends, warum.
    """
    pem, oeff = webpush_service._neues_paar()
    lebt = webpush_service._zustellen(
        _client_der(stoerung), ENDPUNKT, GUELTIGER_PUNKT, GUELTIGES_AUTH, b'{"title":"x"}', pem, oeff
    )
    assert lebt is True


def test_die_zustellung_traegt_die_richtigen_koepfe():
    gesehen: list = []
    pem, oeff = webpush_service._neues_paar()
    webpush_service._zustellen(
        _client_der(201, gesehen), ENDPUNKT, GUELTIGER_PUNKT, GUELTIGES_AUTH, b'{"title":"x"}', pem, oeff
    )

    url, inhalt, koepfe = gesehen[0]
    assert url == ENDPUNKT
    assert koepfe["Content-Encoding"] == "aes128gcm"
    assert koepfe["Content-Type"] == "application/octet-stream"
    assert koepfe["TTL"] == str(webpush_service.TTL_SEKUNDEN)
    assert koepfe["Authorization"].startswith("vapid t=")
    # Der Körper ist Chiffrat: der Push-Dienst sieht den Titel nicht.
    assert b"title" not in inhalt
    assert len(inhalt) > 16 + 4 + 1 + 65


def test_die_zustelladresse_landet_nicht_im_log(caplog):
    """Eine Endpunkt-Adresse ist ein Personenbezug: sie identifiziert genau
    einen Browser. Sie gehört deshalb in keine Logzeile — auch nicht in eine
    Fehlermeldung."""
    pem, oeff = webpush_service._neues_paar()
    with caplog.at_level(logging.WARNING):
        webpush_service._zustellen(
            _client_der(500), ENDPUNKT, GUELTIGER_PUNKT, GUELTIGES_AUTH, b'{"title":"x"}', pem, oeff
        )

    assert caplog.text
    assert ENDPUNKT not in caplog.text
    assert "beispiel-eins" not in caplog.text


def test_ein_totes_abo_wird_aus_der_tabelle_entfernt(
    db: Session, owner_user: User, monkeypatch
):
    """Die Zeile stehen zu lassen hieße, bei jeder Nachricht erneut dagegen zu
    laufen."""
    abo = _abo_anlegen(db, owner_user)
    pem, oeff = webpush_service._neues_paar()

    monkeypatch.setattr(webpush_service.httpx, "Client", lambda **_: _AlsKontext(_client_der(410)))
    webpush_service._zustellen_alle(
        [(abo.id, abo.endpoint, abo.p256dh, abo.auth)], b'{"title":"x"}', pem, oeff
    )

    db.expire_all()
    assert db.query(PushSubscription).count() == 0


def test_eine_stoerung_laesst_die_tabelle_in_ruhe(db: Session, owner_user: User, monkeypatch):
    abo = _abo_anlegen(db, owner_user)
    pem, oeff = webpush_service._neues_paar()

    monkeypatch.setattr(webpush_service.httpx, "Client", lambda **_: _AlsKontext(_client_der(503)))
    webpush_service._zustellen_alle(
        [(abo.id, abo.endpoint, abo.p256dh, abo.auth)], b'{"title":"x"}', pem, oeff
    )

    db.expire_all()
    assert db.query(PushSubscription).count() == 1


class _AlsKontext:
    """`httpx.Client` wird als `with`-Block benutzt."""

    def __init__(self, inner):
        self._inner = inner

    def __enter__(self):
        return self._inner

    def __exit__(self, *_):
        return False


# ── Der Weg durch die echten Routen ─────────────────────────────────────────


def test_die_routen_tragen_ein_und_wieder_aus(
    client, owner_cookies: dict, csrf_token: str, db: Session, owner_user: User
):
    """Einmal durch HTTP, mit Anmeldung und CSRF.

    Die Dienstfunktionen sind oben einzeln geprüft; hier geht es um das, was
    nur an der Route sichtbar wird — dass sie hinter Anmeldung und CSRF liegt
    und dass das Konto aus der Sitzung kommt.
    """
    koepfe = {"X-CSRF-Token": csrf_token}

    schluessel = client.get("/api/social/push/public-key", cookies=owner_cookies)
    assert schluessel.status_code == 200
    assert len(_b64d(schluessel.json()["key"])) == 65

    angelegt = client.post(
        "/api/social/push/subscribe",
        cookies=owner_cookies,
        headers=koepfe,
        json={"endpoint": ENDPUNKT, "p256dh": GUELTIGER_PUNKT, "auth": GUELTIGES_AUTH},
    )
    assert angelegt.status_code == 200

    db.expire_all()
    abo = db.query(PushSubscription).filter_by(endpoint=ENDPUNKT).first()
    assert abo is not None
    assert abo.user_id == owner_user.id

    weg = client.request(
        "DELETE",
        f"/api/social/push/subscribe?endpoint={ENDPUNKT}",
        cookies=owner_cookies,
        headers=koepfe,
    )
    assert weg.status_code == 200
    db.expire_all()
    assert db.query(PushSubscription).count() == 0


def test_ohne_anmeldung_geht_an_den_routen_nichts(client):
    """Eine Zustelladresse ist ein Personenbezug. Sie darf nicht anonym
    eintragbar sein, und der Schlüssel des Panels nicht anonym abrufbar."""
    assert client.get("/api/social/push/public-key").status_code in (401, 403)
    assert (
        client.post(
            "/api/social/push/subscribe",
            json={"endpoint": ENDPUNKT, "p256dh": GUELTIGER_PUNKT, "auth": GUELTIGES_AUTH},
        ).status_code
        in (401, 403)
    )


def test_ohne_csrf_wird_nichts_eingetragen(client, owner_cookies: dict):
    antwort = client.post(
        "/api/social/push/subscribe",
        cookies=owner_cookies,
        json={"endpoint": ENDPUNKT, "p256dh": GUELTIGER_PUNKT, "auth": GUELTIGES_AUTH},
    )
    assert antwort.status_code == 403


def test_eine_nachricht_loest_den_versand_aus(
    db: Session, owner_user: User, regular_user: User, abgefangener_versand
):
    """Der eigentliche Zweck, am echten Relais.

    Vorher endete `prepare_push_dispatch` in `relay_blind_envelope` im Nichts —
    der Rückgabewert wurde weggeworfen, und wer den Messenger geschlossen hatte,
    erfuhr von einer neuen Nachricht nichts. Dieser Test hält fest, dass die
    Strecke zusammenhängt.
    """
    from services.social_service import SocialService
    from services.sync_event_service import SyncEventService

    SyncEventService.clear_all_for_testing()
    anfrage = SocialService.send_friend_request(db, owner_user.id, regular_user.username)
    SocialService.accept_friend_request(db, regular_user.id, anfrage["id"])
    chat = SocialService.ensure_direct_chat(db, owner_user.id, regular_user.id)
    _abo_anlegen(db, regular_user)

    SocialService.relay_blind_envelope(
        db,
        blind_mailbox_id=chat.blind_mailbox_id,
        ciphertext_envelope=_umschlag(1),
        sender_user_id=owner_user.id,
        recipient_id=regular_user.id,
        client_uuid="uuid-eins",
    )

    assert len(abgefangener_versand) == 1
    ziele, koerper, _pem, _oeff = abgefangener_versand[0]
    assert [z[1] for z in ziele] == [ENDPUNKT]
    assert json.loads(koerper)["title"] == "Neue Nachricht"


def test_der_absender_bekommt_nichts_fuer_die_eigene_nachricht(
    db: Session, owner_user: User, regular_user: User, abgefangener_versand
):
    """Outgoing Echo Prevention, bis zum Versender durchgezogen.

    Beide Seiten haben hier eine Zustelladresse. Ginge etwas an den Absender
    hinaus, meldete sein eigenes zweites Gerät ihm seine eigene Nachricht.
    """
    from services.social_service import SocialService
    from services.sync_event_service import SyncEventService

    SyncEventService.clear_all_for_testing()
    anfrage = SocialService.send_friend_request(db, owner_user.id, regular_user.username)
    SocialService.accept_friend_request(db, regular_user.id, anfrage["id"])
    chat = SocialService.ensure_direct_chat(db, owner_user.id, regular_user.id)
    _abo_anlegen(db, owner_user, "https://fcm.googleapis.com/fcm/send/absender")
    _abo_anlegen(db, regular_user, "https://fcm.googleapis.com/fcm/send/empfaenger")

    SocialService.relay_blind_envelope(
        db,
        blind_mailbox_id=chat.blind_mailbox_id,
        ciphertext_envelope=_umschlag(2),
        sender_user_id=owner_user.id,
        recipient_id=regular_user.id,
        client_uuid="uuid-zwei",
    )

    alle_ziele = [z[1] for uebergabe in abgefangener_versand for z in uebergabe[0]]
    assert alle_ziele == ["https://fcm.googleapis.com/fcm/send/empfaenger"]


def test_eine_lesequittung_loest_keinen_versand_aus(
    db: Session, owner_user: User, regular_user: User, abgefangener_versand
):
    """Steuersignale sind keine Nachrichten. Ein Push je Lesequittung wäre eine
    Meldung für etwas, das der Benutzer selbst ausgelöst hat."""
    from services.social_service import SocialService
    from services.sync_event_service import SyncEventService

    SyncEventService.clear_all_for_testing()
    anfrage = SocialService.send_friend_request(db, owner_user.id, regular_user.username)
    SocialService.accept_friend_request(db, regular_user.id, anfrage["id"])
    chat = SocialService.ensure_direct_chat(db, owner_user.id, regular_user.id)
    _abo_anlegen(db, regular_user)

    SocialService.relay_blind_envelope(
        db,
        blind_mailbox_id=chat.blind_mailbox_id,
        ciphertext_envelope=_umschlag(3),
        sender_user_id=owner_user.id,
        recipient_id=regular_user.id,
        client_uuid="uuid-drei",
        is_control=True,
        control_type="read_receipt",
    )

    assert abgefangener_versand == []
