"""Die gerätebezogene Zustellung: annehmen, abholen, quittieren.

Der Weg, den ein Umschlag nimmt, wenn das Zielgerät ihn unterstützt: eine
opake Inbox je Gerät, ein Zustellauftrag je Nachricht, ein Quittung, das den
Auftrag löscht. Vorher teilten sich zwei Geräte desselben Kontos eine blinde
Mailbox, und der Chiffretext lag 30 Tage dort, obwohl ihn niemand mehr
brauchte.

Diese Datei prüft die Schranken, nicht den Weg:

* Nur das Gerät selbst darf seine Inbox öffnen — die Unterschrift entscheidet.
* Ein Token eines anderen Geräts desselben Kontos genügt nicht.
* Wer in die Mailbox des Gesprächs nicht schreiben darf, kann nichts
  zustellen — und erfährt dabei nichts über bekannte Zustellkennungen.
* Ein wartendes Zielgerät bekommt nichts.
* Die Quittung löscht genau die Zustellungen dieser Inbox und keine andere;
  das Chiffretextobjekt fällt mit dem letzten Auftrag.
* Dieselbe Zustellkennung mit anderen Bytes ist ein Konflikt, nicht eine
  Wiederholung — auch nach der Quittung.
* Ein entferntes Gerät nimmt seine Inbox und seine offenen Aufträge mit;
  Abruf und Quittung scheitern danach, und ein erneutes Veröffentlichen unter
  derselben Kennung wartet auf Freigabe.
"""

from __future__ import annotations

import base64
import hashlib
import json
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import User
from services.social import zustellung


# ── Hilfen ──────────────────────────────────────────────────────────────────


def _paar() -> tuple[str, str, object]:
    """Ein ECDSA-Schlüsselpaar: JWK (oeffentlich), JWK (privat), Objekt."""
    from cryptography.hazmat.primitives.asymmetric import ec

    schluessel = ec.generate_private_key(ec.SECP256R1())
    zahlen = schluessel.public_key().public_numbers()

    def b64url(roh: bytes) -> str:
        return base64.urlsafe_b64encode(roh).decode("ascii").rstrip("=")

    x = b64url(zahlen.x.to_bytes(32, "big"))
    y = b64url(zahlen.y.to_bytes(32, "big"))
    d = b64url(schluessel.private_numbers().private_value.to_bytes(32, "big"))
    oeffentlich = json.dumps({"kty": "EC", "crv": "P-256", "x": x, "y": y})
    privat = json.dumps({"kty": "EC", "crv": "P-256", "x": x, "y": y, "d": d})
    return oeffentlich, privat, schluessel


def _signiere(schluessel, daten: str) -> str:
    """ECDSA P-256 ueber SHA-256, WebCrypto-Format (r||s, 64 Byte, Base64)."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec

    der = schluessel.sign(daten.encode("utf-8"), ec.ECDSA(hashes.SHA256()))
    r, s = _der_zu_r_s(der)
    return base64.b64encode(r + s).decode("ascii")


def _der_zu_r_s(der: bytes) -> tuple[bytes, bytes]:
    from cryptography.hazmat.primitives.asymmetric import utils

    r, s = utils.decode_dss_signature(der)
    return (
        r.to_bytes(32, "big"),
        s.to_bytes(32, "big"),
    )


def _hash(wert: str) -> str:
    return hashlib.sha256(wert.encode("utf-8")).hexdigest()


def _konto(db: Session, name: str) -> User:
    from services.auth_service import AuthService

    vorhanden = db.query(User).filter(User.username == name).first()
    if vorhanden:
        db.refresh(vorhanden)
        return vorhanden
    user = AuthService.create_user(db, name, f"{name}@test.de", "ZustellPass123!")
    user.email_verified = True
    db.commit()
    db.refresh(user)
    return user


def _geraet(db: Session, konto: User, name: str, inbox: str) -> dict:
    """Legt ein Geraet mit Signaturschluessel an und meldet es an."""
    from services import e2ee_device_service

    oeffentlich, privat, schluessel = _paar()
    eintrag = e2ee_device_service.veroeffentlichen(
        db,
        konto,
        device_id=name,
        public_key_jwk=_rsa_placeholder(),
        label=name,
        signing_public_key_jwk=oeffentlich,
        familie=None,
        inbox_hash=inbox,
    )
    return {
        "eintrag": eintrag,
        "geraet_id": name,
        "inbox": inbox,
        "oeffentlich": oeffentlich,
        "privat": privat,
        "schluessel": schluessel,
    }


def _rsa_placeholder() -> str:
    """Ein gueltiger oeffentlicher RSA-Schluessel — der verschluesselt, der zaehlt nicht."""
    import base64 as _b64
    import json as _json

    from cryptography.hazmat.primitives.asymmetric import rsa

    schluessel = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    zahlen = schluessel.public_key().public_numbers()
    n = _b64.urlsafe_b64encode(
        zahlen.n.to_bytes((zahlen.n.bit_length() + 7) // 8, "big")
    ).decode().rstrip("=")
    e = _b64.urlsafe_b64encode(zahlen.e.to_bytes(3, "big")).decode().rstrip("=")
    return _json.dumps({"kty": "RSA", "n": n, "e": e})


def _zustellung(
    absender: dict,
    ziel: dict,
    mailbox_id: str,
    chiffertext: str = "sv-e2ee-hybrid-v1:test",
    delivery_id: str | None = None,
) -> dict:
    """Baut einen Annahmestapel mit gueltigem Beleg.

    Der Beleg kommt vom **sendenden** Geraet, das Ziel ist die Zustelladresse
    des **empfangenden**. Beides zu verwechseln waere der Fehler, den der
    Test am Anfang selbst gemacht hat.
    """
    payload_id = str(uuid.uuid4())
    delivery_id = delivery_id or str(uuid.uuid4())
    eintraege = [
        {
            "delivery_id": delivery_id,
            "payload_id": payload_id,
            "inbox_hash": ziel["inbox"],
        }
    ]
    daten = zustellung.relay_beweis_daten(
        absender["geraet_id"],
        mailbox_id,
        [
            {
                "delivery_id": delivery_id,
                "payload_id": payload_id,
                "inbox_hash": ziel["inbox"],
                "ciphertext_sha256": _hash(chiffertext),
            }
        ],
    )
    return {
        "mailbox_id": mailbox_id,
        "geraet_id": absender["geraet_id"],
        "payloads": [
            {
                "id": payload_id,
                "ciphertext": chiffertext,
                "ciphertext_sha256": _hash(chiffertext),
            }
        ],
        "eintraege": eintraege,
        "signatur": _signiere(absender["schluessel"], daten),
        "_delivery_id": delivery_id,
        "_payload_id": payload_id,
    }


def _kekse(client: TestClient, name: str) -> dict:
    antwort = client.post(
        "/api/auth/login",
        json={"username": name, "password": "ZustellPass123!", "otp_code": None},
    )
    assert antwort.status_code == 200, antwort.text
    return dict(antwort.cookies)


def _sitzung(db: Session, konto: User) -> tuple[dict, str]:
    """Eine eigene Sitzung (Refresh-Familie) fuer ein Geraet.

    Die Familie ist die Spur, an der das Entfernen die Sitzung aussperrt
    (`AuthService.revoke_refresh_family`). Zwei Aufrufe ergeben zwei Familien —
    genau wie Telefon und Laptop im Betrieb.
    """
    from starlette.responses import Response

    from services.auth_service import AuthService
    from services.session_service import issue_session

    tokens = issue_session(Response(), db, konto)
    familie = AuthService.decode_token(tokens.access_token)["familie"]
    return {"Authorization": f"Bearer {tokens.access_token}"}, familie


def _freigabe(schluessel, user_id: int, device_id: str, rsa: str, ecdsa: str) -> str:
    from services import e2ee_device_service

    return _signiere(
        schluessel,
        e2ee_device_service.freigabe_daten(user_id, device_id, rsa, ecdsa),
    )


def _entfernen(schluessel, user_id: int, device_id: str, rsa: str) -> str:
    from services import e2ee_device_service

    return _signiere(
        schluessel,
        e2ee_device_service.entfernen_daten(user_id, device_id, rsa),
    )


def _kopf(kekse: dict) -> dict:
    return {"X-CSRF-Token": kekse.get("__Secure-csrf_token", "")}


def _dmu(db: Session, a: User, b: User) -> str:
    """Eine Direktchat-Mailbox zwischen zwei befreundeten Konten.

    Die Freundschaft ist nicht Kosmetik: ohne sie ist die abgeleitete Kennung
    fuer beide keine Teilnehmer-Mailbox, und der Absender darf dort nicht
    schreiben — genau wie im Betrieb.
    """
    from models.user_friend import UserFriend
    from services.social_service import SocialService

    vorhanden = (
        db.query(UserFriend)
        .filter(
            UserFriend.user_id == a.id,
            UserFriend.friend_id == b.id,
            UserFriend.status == "accepted",
        )
        .first()
    )
    if vorhanden is None:
        db.add(UserFriend(user_id=a.id, friend_id=b.id, status="accepted"))
        db.commit()
    return SocialService.derive_blind_mailbox_id(a.id, b.id)


# ── Die Pruefungen ──────────────────────────────────────────────────────────


def test_die_zustellung_wird_abgeholt_und_dann_geloescht(
    client: TestClient, db: Session
) -> None:
    alpha = _konto(db, "zustell-alpha")
    beta = _konto(db, "zustell-beta")
    ziel = _geraet(db, beta, "beta-geraet", "b" * 64)
    absender = _geraet(db, alpha, "alpha-geraet", "a" * 64)
    mailbox = _dmu(db, alpha, beta)

    alpha_kekse = _kekse(client, "zustell-alpha")
    beta_kekse = _kekse(client, "zustell-beta")

    stapel = _zustellung(absender, ziel, mailbox)
    angenommen = client.post(
        "/api/social/e2ee/deliveries",
        json={k: v for k, v in stapel.items() if not k.startswith("_")},
        cookies=alpha_kekse,
        headers=_kopf(alpha_kekse),
    )
    assert angenommen.status_code == 200, angenommen.text
    assert angenommen.json()["angenommen"][0]["ergebnis"] == "angenommen"

    # Das Zielgeraet holt die Zustellung mit seinem Beleg.
    lese_daten = zustellung.lese_beweis_daten(ziel["inbox"], 0)
    geholt = client.get(
        "/api/social/e2ee/deliveries",
        params={"inbox_hash": ziel["inbox"], "seit_seq": 0, "limit": 10},
        cookies=beta_kekse,
        headers={"X-Geraete-Beleg": _signiere(ziel["schluessel"], lese_daten)},
    )
    assert geholt.status_code == 200, geholt.text
    eintraege = geholt.json()["eintraege"]
    assert len(eintraege) == 1
    assert eintraege[0]["delivery_id"] == stapel["_delivery_id"]
    assert eintraege[0]["ciphertext"] == "sv-e2ee-hybrid-v1:test"

    # Die Quittung loescht den Auftrag — und das Chiffretextobjekt mit.
    ack_daten = zustellung.ack_beweis_daten(ziel["inbox"], [stapel["_delivery_id"]])
    quittiert = client.post(
        "/api/social/e2ee/deliveries/ack",
        json={
            "inbox_hash": ziel["inbox"],
            "geraet_id": ziel["geraet_id"],
            "delivery_ids": [stapel["_delivery_id"]],
            "signatur": _signiere(ziel["schluessel"], ack_daten),
        },
        cookies=beta_kekse,
        headers=_kopf(beta_kekse),
    )
    assert quittiert.status_code == 200, quittiert.text
    assert quittiert.json()["quittiert"] == 1

    # Mit dem letzten Auftrag faellt das Chiffretextobjekt — nicht erst mit
    # der 30-Tage-Frist.
    from models import E2eeDeliveryPayload

    assert (
        db.query(E2eeDeliveryPayload)
        .filter(E2eeDeliveryPayload.id == stapel["_payload_id"])
        .count()
        == 0
    )

    nochmal = client.get(
        "/api/social/e2ee/deliveries",
        params={"inbox_hash": ziel["inbox"], "seit_seq": 0, "limit": 10},
        cookies=beta_kekse,
        headers={
            "X-Geraete-Beleg": _signiere(
                ziel["schluessel"], zustellung.lese_beweis_daten(ziel["inbox"], 0)
            )
        },
    )
    assert nochmal.json()["eintraege"] == []


def test_ein_falscher_beleg_wird_abgelehnt(client: TestClient, db: Session) -> None:
    alpha = _konto(db, "zustell-alpha")
    beta = _konto(db, "zustell-beta")
    ziel = _geraet(db, beta, "beta-geraet", "b" * 64)
    beta_kekse = _kekse(client, "zustell-beta")

    # Der Beleg stammt von einem anderen Schluessel.
    _, _, fremder = _paar()
    lese_daten = zustellung.lese_beweis_daten(ziel["inbox"], 0)
    geholt = client.get(
        "/api/social/e2ee/deliveries",
        params={"inbox_hash": ziel["inbox"], "seit_seq": 0},
        cookies=beta_kekse,
        headers={"X-Geraete-Beleg": _signiere(fremder, lese_daten)},
    )
    assert geholt.status_code == 403
    assert alpha.id  # der Absender ist hier nur der Randfall


def test_ohne_beleg_gibt_es_nichts(client: TestClient, db: Session) -> None:
    beta = _konto(db, "zustell-beta")
    ziel = _geraet(db, beta, "beta-geraet", "b" * 64)
    beta_kekse = _kekse(client, "zustell-beta")
    geholt = client.get(
        "/api/social/e2ee/deliveries",
        params={"inbox_hash": ziel["inbox"], "seit_seq": 0},
        cookies=beta_kekse,
    )
    assert geholt.status_code == 403


def test_ohne_berechtigung_auf_die_mailbox_wird_nichts_angenommen(
    client: TestClient, db: Session
) -> None:
    alpha = _konto(db, "zustell-alpha")
    beta = _konto(db, "zustell-beta")
    fremder = _konto(db, "zustell-fremd")
    ziel = _geraet(db, beta, "beta-geraet", "b" * 64)
    absender = _geraet(db, fremder, "fremd-geraet", "c" * 64)
    mailbox = _dmu(db, alpha, beta)

    fremd_kekse = _kekse(client, "zustell-fremd")
    stapel = _zustellung(absender, ziel, mailbox)
    antwort = client.post(
        "/api/social/e2ee/deliveries",
        json={k: v for k, v in stapel.items() if not k.startswith("_")},
        cookies=fremd_kekse,
        headers=_kopf(fremd_kekse),
    )
    assert antwort.status_code == 403
    # Und die Berechtigung steht **vor** der Idempotenz: der Fremde erfährt
    # nichts darueber, ob die Kennung schon bekannt ist.
    assert "delivery_id" not in antwort.text


def test_ein_wartendes_zielgeraet_bekommt_nichts(client: TestClient, db: Session) -> None:
    alpha = _konto(db, "zustell-alpha")
    beta = _konto(db, "zustell-beta")
    ziel = _geraet(db, beta, "beta-geraet", "b" * 64)
    absender = _geraet(db, alpha, "alpha-geraet", "a" * 64)
    # Das Ziel wartet noch auf Freigabe.
    ziel["eintrag"].is_approved = False
    db.commit()

    mailbox = _dmu(db, alpha, beta)
    alpha_kekse = _kekse(client, "zustell-alpha")
    stapel = _zustellung(absender, ziel, mailbox)
    antwort = client.post(
        "/api/social/e2ee/deliveries",
        json={k: v for k, v in stapel.items() if not k.startswith("_")},
        cookies=alpha_kekse,
        headers=_kopf(alpha_kekse),
    )
    assert antwort.status_code == 200
    assert antwort.json()["angenommen"] == []
    assert antwort.json()["abgelehnt"][0]["grund"] == zustellung.ZielNichtErlaubtFehler.code


def test_dieselbe_zustellkennung_mit_anderen_bytes_ist_ein_konflikt(
    client: TestClient, db: Session
) -> None:
    alpha = _konto(db, "zustell-alpha")
    beta = _konto(db, "zustell-beta")
    ziel = _geraet(db, beta, "beta-geraet", "b" * 64)
    absender = _geraet(db, alpha, "alpha-geraet", "a" * 64)
    mailbox = _dmu(db, alpha, beta)
    alpha_kekse = _kekse(client, "zustell-alpha")

    stapel = _zustellung(absender, ziel, mailbox)
    erstes = client.post(
        "/api/social/e2ee/deliveries",
        json={k: v for k, v in stapel.items() if not k.startswith("_")},
        cookies=alpha_kekse,
        headers=_kopf(alpha_kekse),
    )
    assert erstes.status_code == 200

    # Derselbe Stapel noch einmal: gleiche Bytes, gleiche Kennung.
    wiederholung = client.post(
        "/api/social/e2ee/deliveries",
        json={k: v for k, v in stapel.items() if not k.startswith("_")},
        cookies=alpha_kekse,
        headers=_kopf(alpha_kekse),
    )
    assert wiederholung.status_code == 200
    assert wiederholung.json()["angenommen"][0]["ergebnis"] == "bereits"

    # Andere Bytes unter derselben Kennung: ein Konflikt.
    anders = _zustellung(
        absender,
        ziel,
        mailbox,
        chiffertext="sv-e2ee-hybrid-v1:anders",
        delivery_id=stapel["_delivery_id"],
    )
    konflikt = client.post(
        "/api/social/e2ee/deliveries",
        json={k: v for k, v in anders.items() if not k.startswith("_")},
        cookies=alpha_kekse,
        headers=_kopf(alpha_kekse),
    )
    assert konflikt.status_code == 409


def test_die_quittung_eines_anderen_geraets_aendert_nichts(
    client: TestClient, db: Session
) -> None:
    alpha = _konto(db, "zustell-alpha")
    beta = _konto(db, "zustell-beta")
    ziel = _geraet(db, beta, "beta-geraet", "b" * 64)
    zweites = _geraet(db, beta, "beta-zweit", "d" * 64)
    absender = _geraet(db, alpha, "alpha-geraet", "a" * 64)
    mailbox = _dmu(db, alpha, beta)
    alpha_kekse = _kekse(client, "zustell-alpha")
    beta_kekse = _kekse(client, "zustell-beta")

    stapel = _zustellung(absender, ziel, mailbox)
    client.post(
        "/api/social/e2ee/deliveries",
        json={k: v for k, v in stapel.items() if not k.startswith("_")},
        cookies=alpha_kekse,
        headers=_kopf(alpha_kekse),
    )

    # Das zweite Geraet desselben Kontos quittiert die Zustellung des ersten.
    # Es ist ein anderes Geraet: die Inbox gehoert dem ersten, und die
    # Quittung des zweiten darf dort nichts loeschen.
    ack_daten = zustellung.ack_beweis_daten(ziel["inbox"], [stapel["_delivery_id"]])
    quittiert = client.post(
        "/api/social/e2ee/deliveries/ack",
        json={
            "inbox_hash": ziel["inbox"],
            "geraet_id": zweites["geraet_id"],
            "delivery_ids": [stapel["_delivery_id"]],
            "signatur": _signiere(zweites["schluessel"], ack_daten),
        },
        cookies=beta_kekse,
        headers=_kopf(beta_kekse),
    )
    # Der Beleg passt nicht zum Geraet der Inbox — oder die Inbox gehoert ihm
    # nicht. Beides heisst: abgelehnt.
    assert quittiert.status_code == 403

    # Und die Zustellung ist noch da.
    geholt = client.get(
        "/api/social/e2ee/deliveries",
        params={"inbox_hash": ziel["inbox"], "seit_seq": 0},
        cookies=beta_kekse,
        headers={
            "X-Geraete-Beleg": _signiere(
                ziel["schluessel"], zustellung.lese_beweis_daten(ziel["inbox"], 0)
            )
        },
    )
    assert len(geholt.json()["eintraege"]) == 1


def test_der_idempotenzrest_antwortet_eine_wiederholte_quittung(
    client: TestClient, db: Session
) -> None:
    alpha = _konto(db, "zustell-alpha")
    beta = _konto(db, "zustell-beta")
    ziel = _geraet(db, beta, "beta-geraet", "b" * 64)
    absender = _geraet(db, alpha, "alpha-geraet", "a" * 64)
    mailbox = _dmu(db, alpha, beta)
    alpha_kekse = _kekse(client, "zustell-alpha")
    beta_kekse = _kekse(client, "zustell-beta")

    stapel = _zustellung(absender, ziel, mailbox)
    client.post(
        "/api/social/e2ee/deliveries",
        json={k: v for k, v in stapel.items() if not k.startswith("_")},
        cookies=alpha_kekse,
        headers=_kopf(alpha_kekse),
    )
    ack_daten = zustellung.ack_beweis_daten(ziel["inbox"], [stapel["_delivery_id"]])
    koerper = {
        "inbox_hash": ziel["inbox"],
        "geraet_id": ziel["geraet_id"],
        "delivery_ids": [stapel["_delivery_id"]],
        "signatur": _signiere(ziel["schluessel"], ack_daten),
    }
    erstes = client.post(
        "/api/social/e2ee/deliveries/ack",
        json=koerper,
        cookies=beta_kekse,
        headers=_kopf(beta_kekse),
    )
    assert erstes.json()["quittiert"] == 1
    # Die zweite Quittung ist erfolgreich und loescht nichts mehr.
    zweites = client.post(
        "/api/social/e2ee/deliveries/ack",
        json=koerper,
        cookies=beta_kekse,
        headers=_kopf(beta_kekse),
    )
    assert zweites.status_code == 200
    assert zweites.json()["quittiert"] == 0


def test_abgelaufene_zustellungen_werden_geraeumt(db: Session) -> None:
    from datetime import datetime, timezone

    from models import E2eeDelivery, E2eeDeliveryPayload, E2eeDeliveryReceipt

    beta = _konto(db, "zustell-beta")
    ziel = _geraet(db, beta, "beta-geraet", "b" * 64)

    # Ein Auftrag mit abgelaufener Frist — direkt gesetzt, weil die Annahme
    # immer eine lebende Frist vergibt.
    payload_id = str(uuid.uuid4())
    delivery_id = str(uuid.uuid4())
    rest_id = str(uuid.uuid4())
    db.add(
        E2eeDeliveryPayload(
            id=payload_id,
            ciphertext="sv-e2ee-hybrid-v1:veraltet",
            ciphertext_sha256=_hash("sv-e2ee-hybrid-v1:veraltet"),
            groesse=30,
            angenommen_am=datetime.now(timezone.utc) - timedelta(days=40),
            ablauf_am=datetime.now(timezone.utc) - timedelta(days=1),
        )
    )
    db.commit()
    db.add(
        E2eeDelivery(
            id=delivery_id,
            payload_id=payload_id,
            inbox_hash=ziel["inbox"],
            laufende_seq=1,
            angenommen_am=datetime.now(timezone.utc) - timedelta(days=40),
            ablauf_am=datetime.now(timezone.utc) - timedelta(days=1),
        )
    )
    db.flush()
    # Der Idempotenzrest hat keine Kaskade an der Inbox: er haengt an keiner
    # Geraetezeile und faellt deshalb nur ueber seine eigene Frist.
    db.add(
        E2eeDeliveryReceipt(
            inbox_hash=ziel["inbox"],
            delivery_id=rest_id,
            request_hash="r" * 64,
            ergebnis=1,
            angenommen_am=datetime.now(timezone.utc) - timedelta(days=40),
            ablauf_am=datetime.now(timezone.utc) - timedelta(days=1),
        )
    )
    db.commit()

    ergebnis = zustellung.abgelaufene_raeumen(db)
    assert ergebnis["auftraege"] >= 1
    assert ergebnis["nutzlasten"] >= 1
    assert ergebnis["reste"] >= 1
    assert db.query(E2eeDelivery).filter(E2eeDelivery.id == delivery_id).first() is None
    assert (
        db.query(E2eeDeliveryReceipt)
        .filter(E2eeDeliveryReceipt.delivery_id == rest_id)
        .first()
        is None
    )


def test_geraeteentzug_raeumt_inbox_und_offene_zustellungen(
    client: TestClient, db: Session
) -> None:
    """Ein entferntes Gerät nimmt Inbox und offene Aufträge mit — und kommt nicht zurück.

    Die Kaskade steht in der Datenbank: `e2ee_device_inboxes.device_id` hängt
    mit ON DELETE CASCADE an der Gerätezeile, `e2ee_deliveries.inbox_hash` an
    der Inbox. Das Chiffretextobjekt hängt nur an den Aufträgen; es bleibt
    deshalb bis zu seiner Frist (30 Tage) liegen und fällt dann mit
    `abgelaufene_raeumen`. Diese Frist wird hier gesetzt, nicht abgewartet.

    Geprüft wird über den echten Entfernungsweg: das Telefon signiert, das
    Konto bestätigt mit Passwort. Danach ist die Sitzungsfamilie des Laptops
    gesperrt (401), und mit einer gültigen Sitzung des Kontos kommt man an die
    alte Inbox trotzdem nicht mehr heran (403).
    """
    from datetime import datetime, timezone

    from models import (
        E2eeDelivery,
        E2eeDeliveryPayload,
        E2eeDeliveryReceipt,
        E2eeDeviceInbox,
        RefreshToken,
        UserE2eeDevice,
    )
    from services import e2ee_device_service

    alpha = _konto(db, "zustell-alpha")
    beta = _konto(db, "zustell-beta")
    alpha_kopf, _ = _sitzung(db, alpha)
    telefon_kopf, _ = _sitzung(db, beta)
    laptop_kopf, laptop_familie = _sitzung(db, beta)

    telefonschluessel, _, telefon_privat = _paar()
    laptopschluessel, _, laptop_privat = _paar()
    telefon_rsa = _rsa_placeholder()
    laptop_rsa = _rsa_placeholder()
    telefon_inbox = "e" * 64
    laptop_inbox = "b" * 64

    def veroeffentlichen(kopf: dict, kennung: str, rsa: str, ecdsa: str, inbox: str) -> dict:
        antwort = client.put(
            "/api/social/e2ee/devices/self",
            headers=kopf,
            json={
                "device_id": kennung,
                "public_key": rsa,
                "signing_public_key": ecdsa,
                "label": kennung,
                "inbox_hash": inbox,
            },
        )
        assert antwort.status_code == 200, antwort.text
        return antwort.json()

    # Das Telefon ist das erste Gerät und damit freigegeben; der Laptop wartet,
    # bis das Telefon ihn unterschreibt.
    assert veroeffentlichen(
        telefon_kopf, "beta-telefon", telefon_rsa, telefonschluessel, telefon_inbox
    )["is_approved"] is True
    assert veroeffentlichen(
        laptop_kopf, "beta-laptop", laptop_rsa, laptopschluessel, laptop_inbox
    )["is_approved"] is False
    freigabe = _freigabe(
        telefon_privat, beta.id, "beta-laptop", laptop_rsa, laptopschluessel
    )
    assert client.post(
        "/api/social/e2ee/devices/self/approve",
        headers=telefon_kopf,
        json={
            "device_id": "beta-laptop",
            "approver_device_id": "beta-telefon",
            "signature": freigabe,
        },
    ).status_code == 200

    absender = _geraet(db, alpha, "alpha-geraet", "a" * 64)
    mailbox = _dmu(db, alpha, beta)
    stapel = _zustellung(absender, {"inbox": laptop_inbox}, mailbox)
    angenommen = client.post(
        "/api/social/e2ee/deliveries",
        json={k: v for k, v in stapel.items() if not k.startswith("_")},
        headers=alpha_kopf,
    )
    assert angenommen.status_code == 200, angenommen.text
    assert angenommen.json()["angenommen"][0]["ergebnis"] == "angenommen"

    # Dieselbe Nachricht geht an das Telefon. Sie muss das Entfernen des
    # Laptops ueberstehen — die Kaskade trifft genau eine Inbox.
    telefon_stapel = _zustellung(absender, {"inbox": telefon_inbox}, mailbox)
    angenommen = client.post(
        "/api/social/e2ee/deliveries",
        json={k: v for k, v in telefon_stapel.items() if not k.startswith("_")},
        headers=alpha_kopf,
    )
    assert angenommen.status_code == 200, angenommen.text
    assert angenommen.json()["angenommen"][0]["ergebnis"] == "angenommen"

    db.expire_all()
    laptop = (
        db.query(UserE2eeDevice)
        .filter(UserE2eeDevice.user_id == beta.id, UserE2eeDevice.device_id == "beta-laptop")
        .one()
    )
    assert (
        db.query(E2eeDeviceInbox).filter(E2eeDeviceInbox.device_id == laptop.id).one().inbox_hash
        == laptop_inbox
    )
    assert db.query(E2eeDelivery).filter(E2eeDelivery.inbox_hash == laptop_inbox).count() == 1
    assert (
        db.query(E2eeDeliveryPayload)
        .filter(E2eeDeliveryPayload.id == stapel["_payload_id"])
        .count()
        == 1
    )

    # Entfernen: Unterschrift des Telefons plus Passwort — der echte Weg.
    entfernen = _entfernen(telefon_privat, beta.id, "beta-laptop", laptop_rsa)
    antwort = client.post(
        "/api/social/e2ee/devices/self/remove",
        headers=telefon_kopf,
        json={
            "device_id": "beta-laptop",
            "approver_device_id": "beta-telefon",
            "signature": entfernen,
            "password": "ZustellPass123!",
        },
    )
    assert antwort.status_code == 200, antwort.text

    db.expire_all()
    assert (
        db.query(UserE2eeDevice)
        .filter(UserE2eeDevice.user_id == beta.id, UserE2eeDevice.device_id == "beta-laptop")
        .first()
        is None
    )
    # Inbox und offene Aufträge sind mit der Gerätezeile gefallen.
    assert db.query(E2eeDeviceInbox).filter(E2eeDeviceInbox.inbox_hash == laptop_inbox).first() is None
    assert db.query(E2eeDelivery).filter(E2eeDelivery.inbox_hash == laptop_inbox).first() is None
    # Auf das Chiffretextobjekt zeigt kein Auftrag mehr; es bleibt bis zur
    # Frist liegen (siehe unten).
    assert (
        db.query(E2eeDelivery)
        .filter(E2eeDelivery.payload_id == stapel["_payload_id"])
        .count()
        == 0
    )
    # Das Telefon ist unberuehrt: seine Inbox, sein Auftrag und sein
    # Chiffretextobjekt stehen noch.
    assert db.query(E2eeDeviceInbox).filter(E2eeDeviceInbox.inbox_hash == telefon_inbox).count() == 1
    assert db.query(E2eeDelivery).filter(E2eeDelivery.inbox_hash == telefon_inbox).count() == 1
    assert (
        db.query(E2eeDeliveryPayload)
        .filter(E2eeDeliveryPayload.id == telefon_stapel["_payload_id"])
        .count()
        == 1
    )
    assert (
        db.query(E2eeDeliveryReceipt)
        .filter(E2eeDeliveryReceipt.inbox_hash == laptop_inbox)
        .count()
        == 0
    )
    # Die Sitzungsfamilie ist gesperrt — nicht erst nach 15 Minuten.
    assert db.query(RefreshToken).filter(RefreshToken.family == laptop_familie).count() > 0
    assert (
        db.query(RefreshToken)
        .filter(
            RefreshToken.family == laptop_familie,
            RefreshToken.revoked_at.is_(None),
        )
        .count()
        == 0
    )

    # Abruf und Quittung: mit der alten Sitzung 401, mit einer gültigen Sitzung
    # des Kontos 403 — die Inbox gehört zu keinem Gerät mehr.
    lesebeleg = _signiere(laptop_privat, zustellung.lese_beweis_daten(laptop_inbox, 0))
    geholt = client.get(
        "/api/social/e2ee/deliveries",
        params={"inbox_hash": laptop_inbox, "seit_seq": 0},
        headers={**laptop_kopf, "X-Geraete-Beleg": lesebeleg},
    )
    assert geholt.status_code == 401, geholt.text
    geholt = client.get(
        "/api/social/e2ee/deliveries",
        params={"inbox_hash": laptop_inbox, "seit_seq": 0},
        headers={**telefon_kopf, "X-Geraete-Beleg": lesebeleg},
    )
    assert geholt.status_code == 403, geholt.text

    ack = {
        "inbox_hash": laptop_inbox,
        "geraet_id": "beta-laptop",
        "delivery_ids": [stapel["_delivery_id"]],
        "signatur": _signiere(
            laptop_privat,
            zustellung.ack_beweis_daten(laptop_inbox, [stapel["_delivery_id"]]),
        ),
    }
    quittiert = client.post(
        "/api/social/e2ee/deliveries/ack", json=ack, headers=laptop_kopf
    )
    assert quittiert.status_code == 401, quittiert.text
    quittiert = client.post(
        "/api/social/e2ee/deliveries/ack", json=ack, headers=telefon_kopf
    )
    assert quittiert.status_code == 403, quittiert.text

    db.expire_all()
    assert db.query(E2eeDeviceInbox).filter(E2eeDeviceInbox.inbox_hash == laptop_inbox).first() is None
    assert db.query(E2eeDelivery).filter(E2eeDelivery.inbox_hash == laptop_inbox).first() is None
    assert (
        db.query(E2eeDeliveryReceipt)
        .filter(E2eeDeliveryReceipt.inbox_hash == laptop_inbox)
        .count()
        == 0
    )

    # Das Chiffretextobjekt bleibt bis zu seiner Frist liegen (es kann von
    # Aufträgen anderer Inboxen geteilt sein) und fällt mit dem Aufräumen.
    objekt = (
        db.query(E2eeDeliveryPayload)
        .filter(E2eeDeliveryPayload.id == stapel["_payload_id"])
        .one()
    )
    objekt.ablauf_am = datetime.now(timezone.utc) - timedelta(days=1)
    db.commit()
    geraeumt = zustellung.abgelaufene_raeumen(db)
    assert geraeumt["nutzlasten"] >= 1
    assert (
        db.query(E2eeDeliveryPayload)
        .filter(E2eeDeliveryPayload.id == stapel["_payload_id"])
        .first()
        is None
    )
    # Der Auftrag des Telefons haengt noch an seinem Objekt; das Aufraeumen
    # laesst es stehen.
    assert (
        db.query(E2eeDeliveryPayload)
        .filter(E2eeDeliveryPayload.id == telefon_stapel["_payload_id"])
        .count()
        == 1
    )

    # Ein erneutes Veröffentlichen unter derselben Kennung wird nicht still
    # freigegeben: das Telefon bleibt das einzige freigegebene Gerät, und an
    # die neue Inbox wird nichts mehr zugestellt.
    neu_kopf, _ = _sitzung(db, beta)
    wieder = veroeffentlichen(
        neu_kopf, "beta-laptop", laptop_rsa, laptopschluessel, laptop_inbox
    )
    assert wieder["is_approved"] is False
    # Die Adresse ist wieder eingetragen — aber ohne Freigabe bekommt das
    # Geraet darauf nichts.
    assert wieder["inbox_hash"] == laptop_inbox
    assert wieder["transport"] == "geraet-v1"
    freigegeben = {
        g["device_id"]
        for g in e2ee_device_service.geraete(db, beta.id, nur_bestaetigt=True)
    }
    assert freigegeben == {"beta-telefon"}

    zweiter_stapel = _zustellung(absender, {"inbox": laptop_inbox}, mailbox)
    abgelehnt = client.post(
        "/api/social/e2ee/deliveries",
        json={k: v for k, v in zweiter_stapel.items() if not k.startswith("_")},
        headers=alpha_kopf,
    )
    assert abgelehnt.status_code == 200, abgelehnt.text
    assert abgelehnt.json()["angenommen"] == []
    assert abgelehnt.json()["abgelehnt"][0]["grund"] == zustellung.ZielNichtErlaubtFehler.code
    assert (
        db.query(E2eeDelivery).filter(E2eeDelivery.inbox_hash == laptop_inbox).count() == 0
    )
    assert (
        db.query(E2eeDeliveryPayload)
        .filter(E2eeDeliveryPayload.id == zweiter_stapel["_payload_id"])
        .count()
        == 0
    )


def test_der_idempotenzrest_antwortet_eine_wiederholte_annahme(
    client: TestClient, db: Session
) -> None:
    """Nach der Quittung ist derselbe Stapel eine Wiederholung, kein Konflikt.

    Der Auftrag ist dann geloescht; nur der Idempotenzrest erinnert sich an die
    Anfrage. Verglich er einen anderen Hash als die Annahme, antwortete der
    Server auf eine verlorene HTTP-Antwort mit 409 — der Absender haette die
    Nachricht als Konflikt gemeldet, obwohl sie angekommen und quittiert ist.
    """
    from models import E2eeDelivery, E2eeDeliveryPayload

    alpha = _konto(db, "zustell-alpha")
    beta = _konto(db, "zustell-beta")
    ziel = _geraet(db, beta, "beta-geraet", "b" * 64)
    absender = _geraet(db, alpha, "alpha-geraet", "a" * 64)
    mailbox = _dmu(db, alpha, beta)
    alpha_kopf, _ = _sitzung(db, alpha)
    beta_kopf, _ = _sitzung(db, beta)

    stapel = _zustellung(absender, ziel, mailbox)
    koerper = {k: v for k, v in stapel.items() if not k.startswith("_")}
    erstes = client.post(
        "/api/social/e2ee/deliveries", json=koerper, headers=alpha_kopf
    )
    assert erstes.status_code == 200, erstes.text

    quittiert = client.post(
        "/api/social/e2ee/deliveries/ack",
        json={
            "inbox_hash": ziel["inbox"],
            "geraet_id": ziel["geraet_id"],
            "delivery_ids": [stapel["_delivery_id"]],
            "signatur": _signiere(
                ziel["schluessel"],
                zustellung.ack_beweis_daten(ziel["inbox"], [stapel["_delivery_id"]]),
            ),
        },
        headers=beta_kopf,
    )
    assert quittiert.status_code == 200, quittiert.text
    assert quittiert.json()["quittiert"] == 1

    # Dieselben Bytes unter derselben Kennung: die Wiederholung ist Erfolg.
    wiederholung = client.post(
        "/api/social/e2ee/deliveries", json=koerper, headers=alpha_kopf
    )
    assert wiederholung.status_code == 200, wiederholung.text
    assert wiederholung.json()["angenommen"][0]["ergebnis"] == "bereits"
    # Und sie legt nichts wieder an: der Auftrag bleibt quittiert, das
    # Chiffretextobjekt bleibt geloescht.
    assert (
        db.query(E2eeDelivery).filter(E2eeDelivery.id == stapel["_delivery_id"]).first()
        is None
    )
    assert (
        db.query(E2eeDeliveryPayload)
        .filter(E2eeDeliveryPayload.id == stapel["_payload_id"])
        .first()
        is None
    )

    # Andere Bytes unter derselben Kennung bleiben ein Konflikt — auch nach
    # der Quittung. Der Rest vergleicht die Bytes, nicht nur die Kennung.
    anders = _zustellung(
        absender,
        ziel,
        mailbox,
        chiffertext="sv-e2ee-hybrid-v1:anders",
        delivery_id=stapel["_delivery_id"],
    )
    konflikt = client.post(
        "/api/social/e2ee/deliveries",
        json={k: v for k, v in anders.items() if not k.startswith("_")},
        headers=alpha_kopf,
    )
    assert konflikt.status_code == 409, konflikt.text


def test_eine_quittung_laesst_das_geteilte_objekt_liegen(
    client: TestClient, db: Session
) -> None:
    """Ein Gruppenpaket traegt mehrere Auftraege; erst der letzte nimmt das Objekt mit.

    Die Quittung zaehlt nach dem Loeschen des Auftrags. Zaehlte sie ihn selbst
    noch mit, bliebe das Objekt bis zur Frist liegen; faellt die Zaehlung zu
    frueh aus, nimmt sie den Auftrag des anderen Geraets per Kaskade mit.
    """
    from models import E2eeDelivery, E2eeDeliveryPayload
    from services import e2ee_device_service

    alpha = _konto(db, "zustell-alpha")
    beta = _konto(db, "zustell-beta")
    telefon = _geraet(db, beta, "beta-telefon", "e" * 64)
    laptop = _geraet(db, beta, "beta-laptop", "b" * 64)
    freigabe = _freigabe(
        telefon["schluessel"],
        beta.id,
        "beta-laptop",
        laptop["eintrag"].public_key_jwk,
        laptop["oeffentlich"],
    )
    assert e2ee_device_service.bestaetigen(
        db, beta, "beta-laptop", "beta-telefon", freigabe
    )
    absender = _geraet(db, alpha, "alpha-geraet", "a" * 64)
    mailbox = _dmu(db, alpha, beta)
    alpha_kopf, _ = _sitzung(db, alpha)
    beta_kopf, _ = _sitzung(db, beta)

    nutzlast_id = str(uuid.uuid4())
    chiffertext = "sv-e2ee-hybrid-v1:gruppe"
    sha = _hash(chiffertext)
    auftraege = [
        {"delivery_id": str(uuid.uuid4()), "payload_id": nutzlast_id, "inbox_hash": telefon["inbox"]},
        {"delivery_id": str(uuid.uuid4()), "payload_id": nutzlast_id, "inbox_hash": laptop["inbox"]},
    ]
    beleg = zustellung.relay_beweis_daten(
        absender["geraet_id"],
        mailbox,
        [{**eintrag, "ciphertext_sha256": sha} for eintrag in auftraege],
    )
    angenommen = client.post(
        "/api/social/e2ee/deliveries",
        headers=alpha_kopf,
        json={
            "mailbox_id": mailbox,
            "geraet_id": absender["geraet_id"],
            "payloads": [
                {"id": nutzlast_id, "ciphertext": chiffertext, "ciphertext_sha256": sha}
            ],
            "eintraege": auftraege,
            "signatur": _signiere(absender["schluessel"], beleg),
        },
    )
    assert angenommen.status_code == 200, angenommen.text
    assert [a["ergebnis"] for a in angenommen.json()["angenommen"]] == [
        "angenommen",
        "angenommen",
    ]

    # Das Telefon quittiert zuerst: der Auftrag des Laptops haelt das Objekt.
    quittiert = client.post(
        "/api/social/e2ee/deliveries/ack",
        headers=beta_kopf,
        json={
            "inbox_hash": telefon["inbox"],
            "geraet_id": "beta-telefon",
            "delivery_ids": [auftraege[0]["delivery_id"]],
            "signatur": _signiere(
                telefon["schluessel"],
                zustellung.ack_beweis_daten(telefon["inbox"], [auftraege[0]["delivery_id"]]),
            ),
        },
    )
    assert quittiert.status_code == 200, quittiert.text
    assert quittiert.json()["quittiert"] == 1
    assert (
        db.query(E2eeDelivery)
        .filter(E2eeDelivery.id == auftraege[1]["delivery_id"])
        .count()
        == 1
    )
    assert (
        db.query(E2eeDeliveryPayload)
        .filter(E2eeDeliveryPayload.id == nutzlast_id)
        .count()
        == 1
    )

    # Erst die zweite Quittung nimmt das Objekt mit.
    quittiert = client.post(
        "/api/social/e2ee/deliveries/ack",
        headers=beta_kopf,
        json={
            "inbox_hash": laptop["inbox"],
            "geraet_id": "beta-laptop",
            "delivery_ids": [auftraege[1]["delivery_id"]],
            "signatur": _signiere(
                laptop["schluessel"],
                zustellung.ack_beweis_daten(laptop["inbox"], [auftraege[1]["delivery_id"]]),
            ),
        },
    )
    assert quittiert.status_code == 200, quittiert.text
    assert quittiert.json()["quittiert"] == 1
    assert (
        db.query(E2eeDeliveryPayload)
        .filter(E2eeDeliveryPayload.id == nutzlast_id)
        .count()
        == 0
    )


def test_eine_abgelaufene_zustellung_wird_nicht_mehr_ausgeliefert(
    client: TestClient, db: Session
) -> None:
    """Die Frist gilt beim Lesen, nicht erst beim naechsten Aufraeumlauf.

    Nach einer Wiederherstellung eines alten Backups kann ein Auftrag mit
    abgelaufener Frist in der Datenbank stehen, bevor der stuendliche Lauf ihn
    entfernt. Er darf trotzdem nie wieder beim Geraet ankommen.
    """
    from datetime import datetime, timezone

    from models import E2eeDelivery, E2eeDeliveryPayload

    alpha = _konto(db, "zustell-alpha")
    beta = _konto(db, "zustell-beta")
    ziel = _geraet(db, beta, "beta-geraet", "b" * 64)
    absender = _geraet(db, alpha, "alpha-geraet", "a" * 64)
    mailbox = _dmu(db, alpha, beta)
    beta_kekse = _kekse(client, "zustell-beta")
    alpha_kekse = _kekse(client, "zustell-alpha")

    stapel = _zustellung(absender, ziel, mailbox)
    angenommen = client.post(
        "/api/social/e2ee/deliveries",
        json={k: v for k, v in stapel.items() if not k.startswith("_")},
        cookies=alpha_kekse,
        headers=_kopf(alpha_kekse),
    )
    assert angenommen.status_code == 200, angenommen.text

    # Die Frist verstreicht, ohne dass der Aufraeumlauf schon lief.
    veraltet = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.query(E2eeDelivery).filter(
        E2eeDelivery.id == stapel["_delivery_id"]
    ).update({E2eeDelivery.ablauf_am: veraltet}, synchronize_session=False)
    db.query(E2eeDeliveryPayload).filter(
        E2eeDeliveryPayload.id == stapel["_payload_id"]
    ).update({E2eeDeliveryPayload.ablauf_am: veraltet}, synchronize_session=False)
    db.commit()

    geholt = client.get(
        "/api/social/e2ee/deliveries",
        params={"inbox_hash": ziel["inbox"], "seit_seq": 0},
        cookies=beta_kekse,
        headers={
            "X-Geraete-Beleg": _signiere(
                ziel["schluessel"], zustellung.lese_beweis_daten(ziel["inbox"], 0)
            )
        },
    )
    assert geholt.status_code == 200, geholt.text
    assert geholt.json()["eintraege"] == []
