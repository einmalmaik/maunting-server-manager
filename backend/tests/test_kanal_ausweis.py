"""Der dauerhafte Ausweis für den Hintergrundkanal eines Geräts.

Was hier festgehalten wird:

* **Der Ausweis steht nirgends.** Gespeichert ist nur sein SHA-256; die Zeile
  verrät keinen gültigen Wert.
* **Er öffnet nur den Strom.** Er ist kein JWT, `get_current_user` kennt ihn
  nicht — Zuhören ja, Konto nein.
* **Widerruf trifft die offene Verbindung** (Punkt 20), nicht nur die Zeile.
* **Abmelden trifft die eigene Sitzung**, nicht die Kanäle der anderen Geräte.
* **Der Deckel verdängt den ältesten**, nicht den neuen.
* **Ohne frischen Nachweis gibt es keinen Ausweis** (Punkt 21).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import NotificationChannel, User
from services import notification_channel_service, passkey_service
from services.auth_service import AuthService
from services.sync_event_service import SyncEventService

PASSWORT = "OwnerPass123!"


@pytest.fixture(autouse=True)
def _leere_abonnenten():
    SyncEventService.clear_all_for_testing()
    yield
    SyncEventService.clear_all_for_testing()


def _kanal(db: Session, user: User, familie: str | None = None, label: str = "Telefon"):
    return notification_channel_service.anlegen(db, user, familie=familie, label=label)


def test_ausweis_wird_nur_als_hash_gespeichert(db: Session, owner_user: User) -> None:
    kanal, ausweis = _kanal(db, owner_user)

    assert len(ausweis) == notification_channel_service.AUSWEIS_LAENGE
    assert kanal.token_hash != ausweis
    assert ausweis not in kanal.token_hash
    # Der Ausweis ist kein JWT — jede andere Route lehnt ihn ab, weil
    # `get_current_user` nur signierte Access-Tokens kennt.
    assert AuthService.decode_token(ausweis) is None


def test_ausweis_oeffnet_den_strom(db: Session, owner_user: User, client: TestClient) -> None:
    kanal, ausweis = _kanal(db, owner_user, familie="fam-a")
    conn_id, _queue = SyncEventService.subscribe(
        user_id=owner_user.id, team_ids=[], is_admin=False, familie=f"kanal:{kanal.id}"
    )

    antwort = client.post(
        "/api/events/presence",
        json={"conn_id": conn_id, "zustand": "hintergrund"},
        headers={"Authorization": f"Bearer {ausweis}"},
    )

    assert antwort.status_code == 200
    assert antwort.json() == {"ok": True}
    # Und der Zusteller ist jetzt der Dienst dieses Geräts.
    assert SyncEventService.zustelllage(user_id=owner_user.id)[0] == conn_id


def test_ein_erfundener_ausweis_oeffnet_nichts(db: Session, owner_user: User, client: TestClient) -> None:
    antwort = client.post(
        "/api/events/presence",
        json={"conn_id": "conn-1", "zustand": "hintergrund"},
        headers={"Authorization": f"Bearer {'a' * 64}"},
    )

    # Kein Zugang — und zwar nicht schon an der CSRF-Pruefung vorbei: ein
    # **erfundener** 64-stelliger Header befreit dort ausdruecklich nicht.
    assert antwort.status_code in (401, 403)


def test_widerruf_schliesst_die_offene_verbindung(db: Session, owner_user: User) -> None:
    kanal, _ausweis = _kanal(db, owner_user, familie="fam-a")
    _conn, queue = SyncEventService.subscribe(
        user_id=owner_user.id, team_ids=[], is_admin=False, familie=f"kanal:{kanal.id}"
    )

    notification_channel_service.widerrufen(db, kanal)

    signal = queue.get_nowait()
    assert signal["type"] == "shutdown"
    assert signal["reason"] == "session_revoked"
    assert not SyncEventService.has_active_subscribers(owner_user.id)


def test_abmelden_trifft_nur_die_eigene_familie(db: Session, owner_user: User) -> None:
    eigen, _ = _kanal(db, owner_user, familie="fam-a", label="Telefon")
    fremd, _ = _kanal(db, owner_user, familie="fam-b", label="Rechner")

    anzahl = notification_channel_service.widerrufen_familie(db, owner_user.id, "fam-a")

    assert anzahl == 1
    offen = {k.id for k in notification_channel_service.liste(db, owner_user.id)}
    assert offen == {fremd.id}
    assert eigen.id not in offen


def test_abmelden_ohne_familie_raeumt_alle(db: Session, owner_user: User) -> None:
    _kanal(db, owner_user, familie="fam-a")
    _kanal(db, owner_user, familie="fam-b")

    assert notification_channel_service.widerrufen_familie(db, owner_user.id, None) == 2
    assert notification_channel_service.liste(db, owner_user.id) == []


def test_deckel_verdraengt_den_aeltesten(db: Session, owner_user: User) -> None:
    erste: list[NotificationChannel] = []
    for _ in range(notification_channel_service.MAX_KANAELE):
        kanal, _ausweis = _kanal(db, owner_user)
        erste.append(kanal)

    neu, _ = _kanal(db, owner_user)

    offen = {k.id for k in notification_channel_service.liste(db, owner_user.id)}
    assert len(offen) == notification_channel_service.MAX_KANAELE
    assert neu.id in offen
    assert erste[0].id not in offen


def test_ohne_frischen_nachweis_gibt_es_keinen_ausweis(
    client: TestClient, owner_cookies: dict, csrf_token: str | None, owner_user: User
) -> None:
    ohne = client.post(
        "/api/notifications/channels",
        json={"label": "Telefon"},
        cookies=owner_cookies,
        headers={"X-CSRF-Token": csrf_token or ""},
    )

    assert ohne.status_code == 403
    assert "Passwort" in ohne.json()["detail"]


def test_mit_passwort_entsteht_genau_ein_ausweis(
    client: TestClient, owner_cookies: dict, csrf_token: str | None, db: Session, owner_user: User
) -> None:
    antwort = client.post(
        "/api/notifications/channels",
        json={"label": "Telefon", "password": PASSWORT},
        cookies=owner_cookies,
        headers={"X-CSRF-Token": csrf_token or ""},
    )

    assert antwort.status_code == 200
    daten = antwort.json()
    assert len(daten["token"]) == notification_channel_service.AUSWEIS_LAENGE
    assert daten["label"] == "Telefon"
    # Kein zweiter Weg zum Ausweis: die Liste nennt ihn nicht.
    liste = client.get("/api/notifications/channels", cookies=owner_cookies).json()
    assert all("token" not in eintrag for eintrag in liste)
    assert [e["id"] for e in liste] == [daten["id"]]


def test_widerruf_ueber_die_route(
    client: TestClient, owner_cookies: dict, csrf_token: str | None, db: Session, owner_user: User
) -> None:
    kanal, _ausweis = _kanal(db, owner_user, familie="fam-a")

    antwort = client.delete(
        f"/api/notifications/channels/{kanal.id}",
        cookies=owner_cookies,
        headers={"X-CSRF-Token": csrf_token or ""},
    )

    assert antwort.status_code == 204
    assert notification_channel_service.liste(db, owner_user.id) == []


def test_ein_fremder_kanal_ist_nicht_auffindbar(
    client: TestClient, user_cookies: dict, user_csrf_token: str | None, db: Session, owner_user: User
) -> None:
    kanal, _ausweis = _kanal(db, owner_user, familie="fam-a")

    antwort = client.delete(
        f"/api/notifications/channels/{kanal.id}",
        cookies=user_cookies,
        headers={"X-CSRF-Token": user_csrf_token or ""},
    )

    assert antwort.status_code == 404
    # Und er lebt weiter.
    assert [k.id for k in notification_channel_service.liste(db, owner_user.id)] == [kanal.id]


def test_der_zweck_ist_fuer_passkeys_angemeldet() -> None:
    # Ohne diesen Eintrag scheitert die Bestätigung im Browser, bevor sie
    # beginnt (`POST /auth/passkey/browser`).
    assert "notification_channel" in passkey_service.ZWECKE_ANGEMELDET
