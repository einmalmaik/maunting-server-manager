"""Anrufraeume: wer darf hinein, wer bekommt ein Token, wer wird abgewiesen.

Loest `test_direct_call_signaling.py` ab. Die blinde Signalisierung ueber
WebSocket gibt es nicht mehr; ausgehandelt wird am Medienserver. Geblieben ist
die eigentliche Frage: ein LiveKit-Token bekommt nur, wer den Raumnamen kennt
**und** an diesem Raum berechtigt ist.
"""

from __future__ import annotations

import secrets

import pytest
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy.orm import Session

from models import User, UserFriend
from services import livekit_service
from services.auth_service import AuthService
from services.call_room_service import CallRoomService, GroupCallRoomRegistry, UserActiveCallRegistry
from services.sync_event_service import SyncEventService

API_KEY = "APItestkey01"
API_SECRET = "test-secret-fuer-anrufe"


@pytest.fixture(autouse=True)
def _raeume_leeren():
    CallRoomService.clear_all_for_testing()
    GroupCallRoomRegistry.clear_all_for_testing()
    yield
    CallRoomService.clear_all_for_testing()
    GroupCallRoomRegistry.clear_all_for_testing()


@pytest.fixture(autouse=True)
def _livekit_lokal(monkeypatch):
    """Ein eingerichteter lokaler Sidecar, ohne dass einer laufen muss.

    Ohne Konfiguration antworten die Anruf-Endpunkte mit 503; das ist richtig,
    macht aber jeden Berechtigungstest unsichtbar.
    """
    monkeypatch.setattr(livekit_service.settings, "livekit_api_key", API_KEY)
    monkeypatch.setattr(livekit_service.settings, "livekit_api_secret", API_SECRET)
    monkeypatch.setattr(livekit_service.settings, "livekit_url", "wss://panel.test/livekit")
    # Kein echter Sidecar: Raumabfragen sollen nicht ins Netz greifen.
    monkeypatch.setattr(livekit_service, "_twirp", lambda *a, **k: {"participants": []})


def _user(db: Session, username: str) -> User:
    user = User(
        username=username,
        email=f"{username}@test.local",
        password_hash=AuthService.hash_password("StrongTestPass123!"),
        is_active=True,
        email_verified=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _login(client: TestClient, username: str) -> dict[str, str]:
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": "StrongTestPass123!", "otp_code": None},
    )
    assert response.status_code == 200
    return dict(response.cookies)


def _csrf(cookies: dict[str, str]) -> dict[str, str]:
    return {"X-CSRF-Token": cookies.get("__Secure-csrf_token", "")}


def _befreunde(db: Session, a: User, b: User) -> None:
    db.add(UserFriend(user_id=a.id, friend_id=b.id, status="accepted"))
    db.commit()


# ── Der Dienst ──────────────────────────────────────────────────────────────


def test_raum_kennt_anrufer_und_angerufenen() -> None:
    raum = CallRoomService.issue(1, 2)
    assert CallRoomService.berechtigte(raum) == {1, 2}
    assert CallRoomService.authorize(raum, 1)
    assert CallRoomService.authorize(raum, 2)
    assert not CallRoomService.authorize(raum, 3)


def test_nur_wer_drin_ist_darf_jemanden_nachholen() -> None:
    raum = CallRoomService.issue(1, 2)
    assert not CallRoomService.add_participant(raum, 99, 3)
    assert CallRoomService.berechtigte(raum) == {1, 2}

    assert CallRoomService.add_participant(raum, 2, 3)
    assert CallRoomService.berechtigte(raum) == {1, 2, 3}
    assert CallRoomService.authorize(raum, 3)


def test_ablehnen_meldet_den_anrufer_und_schliesst_den_raum() -> None:
    raum = CallRoomService.issue(1, 2)
    assert CallRoomService.reject(raum, 2) == 1
    assert CallRoomService.is_consumed(raum)
    assert not CallRoomService.authorize(raum, 1)
    # Ein zweites Ablehnen findet nichts mehr vor.
    assert CallRoomService.reject(raum, 2) is None


def test_der_anrufer_lehnt_seinen_eigenen_anruf_nicht_ab() -> None:
    raum = CallRoomService.issue(1, 2)
    assert CallRoomService.reject(raum, 1) is None
    assert CallRoomService.authorize(raum, 2)


def test_abbrechen_darf_nur_der_ersteller() -> None:
    raum = CallRoomService.issue(1, 2)
    assert CallRoomService.cancel(raum, 2) is None
    assert CallRoomService.cancel(raum, 1) == 2
    assert CallRoomService.is_consumed(raum)


def test_abgelaufener_raum_gibt_niemandem_mehr_zutritt(monkeypatch) -> None:
    raum = CallRoomService.issue(1, 2)
    echt = __import__("time").time

    monkeypatch.setattr(
        "services.call_room_service.time.time",
        lambda: echt() + 10_000,
    )
    assert not CallRoomService.authorize(raum, 1)
    assert CallRoomService.berechtigte(raum) == set()


def test_ein_raum_wird_nicht_endlos_verlaengert(monkeypatch) -> None:
    """Jede Tokenausgabe schiebt das Ende nach hinten, aber nie ueber 2 Stunden."""
    raum = CallRoomService.issue(1, 2)
    echt = __import__("time").time
    versatz = {"wert": 0.0}
    monkeypatch.setattr(
        "services.call_room_service.time.time", lambda: echt() + versatz["wert"]
    )

    # Anruf annehmen, damit die Gespraechsfrist gilt
    assert CallRoomService.authorize(raum, 2) is True

    for schritt in range(1, 300):
        versatz["wert"] = schritt * 30.0
        if not CallRoomService.authorize(raum, 1):
            break
    else:  # pragma: no cover - waere ein Fehler, kein erwarteter Pfad
        pytest.fail("Der Raum lebte laenger als die Obergrenze erlaubt.")

    assert 7000.0 <= versatz["wert"] <= 7300.0


def test_angenommener_raum_bleibt_ueber_einladungsfrist_hinaus_gueltig(monkeypatch) -> None:
    """Ein angenommener Anrufraum stirbt nicht nach 60 Sekunden Einladungsfrist."""
    raum = CallRoomService.issue(1, 2)
    echt = __import__("time").time
    versatz = {"wert": 0.0}
    monkeypatch.setattr(
        "services.call_room_service.time.time", lambda: echt() + versatz["wert"]
    )

    assert CallRoomService.authorize(raum, 2) is True

    versatz["wert"] = 300.0
    assert CallRoomService.authorize(raum, 1) is True
    assert CallRoomService.berechtigte(raum) == {1, 2}


# ── Einladung ───────────────────────────────────────────────────────────────


def test_einladung_nur_unter_bestaetigten_freunden(db: Session, client: TestClient) -> None:
    anrufer = _user(db, f"anrufer_{secrets.token_hex(4)}")
    ziel = _user(db, f"ziel_{secrets.token_hex(4)}")
    kekse = _login(client, anrufer.username)

    abgelehnt = client.post(
        f"/api/social/calls/invite/{ziel.id}", cookies=kekse, headers=_csrf(kekse)
    )
    assert abgelehnt.status_code == 403

    _befreunde(db, anrufer, ziel)
    erlaubt = client.post(
        f"/api/social/calls/invite/{ziel.id}", cookies=kekse, headers=_csrf(kekse)
    )
    assert erlaubt.status_code == 200
    assert erlaubt.json()["recipient_id"] == ziel.id


def test_einladung_laesst_beim_ziel_klingeln(db: Session, client: TestClient) -> None:
    anrufer = _user(db, f"anrufer_{secrets.token_hex(4)}")
    ziel = _user(db, f"ziel_{secrets.token_hex(4)}")
    _befreunde(db, anrufer, ziel)
    kekse = _login(client, anrufer.username)

    conn_id, schlange = SyncEventService.subscribe(ziel.id)
    try:
        antwort = client.post(
            f"/api/social/calls/invite/{ziel.id}?mode=video",
            cookies=kekse,
            headers=_csrf(kekse),
        )
        assert antwort.status_code == 200
        raum = antwort.json()["signaling_token"]

        ereignis = schlange.get_nowait()
        assert ereignis["type"] == "direct_call_invitation"
        assert ereignis["mode"] == "video"
        assert ereignis["signaling_token"] == raum
        assert ereignis["caller_id"] == anrufer.id
    finally:
        SyncEventService.unsubscribe(conn_id)


def test_man_ruft_sich_nicht_selbst_an(db: Session, client: TestClient) -> None:
    allein = _user(db, f"allein_{secrets.token_hex(4)}")
    kekse = _login(client, allein.username)
    antwort = client.post(
        f"/api/social/calls/invite/{allein.id}", cookies=kekse, headers=_csrf(kekse)
    )
    assert antwort.status_code == 400


def test_ohne_eingerichteten_medienserver_gibt_es_keine_einladung(
    db: Session, client: TestClient, monkeypatch
) -> None:
    """Lieber ehrlich 503 als ein Token, das nirgends gilt."""
    anrufer = _user(db, f"anrufer_{secrets.token_hex(4)}")
    ziel = _user(db, f"ziel_{secrets.token_hex(4)}")
    _befreunde(db, anrufer, ziel)
    kekse = _login(client, anrufer.username)
    monkeypatch.setattr(livekit_service.settings, "livekit_api_key", "")

    antwort = client.post(
        f"/api/social/calls/invite/{ziel.id}", cookies=kekse, headers=_csrf(kekse)
    )
    assert antwort.status_code == 503


# ── Zugangstoken ────────────────────────────────────────────────────────────


def test_token_nur_fuer_den_eigenen_raum(db: Session, client: TestClient) -> None:
    anrufer = _user(db, f"anrufer_{secrets.token_hex(4)}")
    ziel = _user(db, f"ziel_{secrets.token_hex(4)}")
    fremder = _user(db, f"fremder_{secrets.token_hex(4)}")
    _befreunde(db, anrufer, ziel)
    anrufer_kekse = _login(client, anrufer.username)
    ziel_kekse = _login(client, ziel.username)
    fremd_kekse = _login(client, fremder.username)

    raum = client.post(
        f"/api/social/calls/invite/{ziel.id}",
        cookies=anrufer_kekse,
        headers=_csrf(anrufer_kekse),
    ).json()["signaling_token"]

    fuer_ziel = client.post(
        "/api/social/calls/token",
        json={"art": "direkt", "raum": raum},
        cookies=ziel_kekse,
        headers=_csrf(ziel_kekse),
    )
    assert fuer_ziel.status_code == 200
    daten = fuer_ziel.json()
    assert daten["identity"] == f"u{ziel.id}"
    assert daten["url"] == "wss://panel.test/livekit"

    anspruch = jwt.decode(daten["token"], API_SECRET, algorithms=["HS256"])
    assert anspruch["video"]["room"] == raum
    assert anspruch["sub"] == f"u{ziel.id}"

    # Wer den Raumnamen kennt, aber nicht eingeladen wurde, kommt nicht hinein.
    fuer_fremden = client.post(
        "/api/social/calls/token",
        json={"art": "direkt", "raum": raum},
        cookies=fremd_kekse,
        headers=_csrf(fremd_kekse),
    )
    assert fuer_fremden.status_code == 403


def test_kein_token_mehr_nach_ablehnen(db: Session, client: TestClient) -> None:
    anrufer = _user(db, f"anrufer_{secrets.token_hex(4)}")
    ziel = _user(db, f"ziel_{secrets.token_hex(4)}")
    _befreunde(db, anrufer, ziel)
    anrufer_kekse = _login(client, anrufer.username)
    ziel_kekse = _login(client, ziel.username)

    raum = client.post(
        f"/api/social/calls/invite/{ziel.id}",
        cookies=anrufer_kekse,
        headers=_csrf(anrufer_kekse),
    ).json()["signaling_token"]

    conn_id, schlange = SyncEventService.subscribe(anrufer.id)
    try:
        abgelehnt = client.post(
            f"/api/social/calls/{raum}/reject",
            cookies=ziel_kekse,
            headers=_csrf(ziel_kekse),
        )
        assert abgelehnt.status_code == 200
        ereignis = schlange.get_nowait()
        assert ereignis["type"] == "direct_call_rejected"
        # Der Anrufer muss den Raum daran wiedererkennen, sonst legt er nicht
        # auf und sitzt nach der Ablehnung allein im Gespraech weiter.
        assert ereignis["signaling_token"] == raum
        # `rejected_by`, nicht `recipient_id`: die Nachbarereignisse meinen mit
        # `recipient_id` den Empfaenger des Ereignisses, hier stand darunter der
        # Absender der Ablehnung. Der Client verglich es mit dem eigenen Konto
        # und legte deshalb nie auf.
        assert ereignis["rejected_by"] == ziel.id
        assert "recipient_id" not in ereignis
    finally:
        SyncEventService.unsubscribe(conn_id)

    nachtraeglich = client.post(
        "/api/social/calls/token",
        json={"art": "direkt", "raum": raum},
        cookies=ziel_kekse,
        headers=_csrf(ziel_kekse),
    )
    assert nachtraeglich.status_code == 410


def test_besetzt_ablehnen_laesst_den_laufenden_anruf_auf_den_anderen_geraeten(
    db: Session, client: TestClient
) -> None:
    """Wer im Gespraech ist und einen zweiten Anruf ablehnt, telefoniert weiter.

    Bis 26.09.2026 meldete die Ablehnung allen Geraeten des Ablehnenden „kein
    Anruf“, und die Anzeige „laeuft auf einem anderen Geraet“ verschwand.
    """
    anrufer = _user(db, f"anrufer_{secrets.token_hex(4)}")
    ziel = _user(db, f"ziel_{secrets.token_hex(4)}")
    _befreunde(db, anrufer, ziel)
    UserActiveCallRegistry.register(ziel.id, "laufender-raum", "direkt", partner_id=999)
    anrufer_kekse = _login(client, anrufer.username)
    ziel_kekse = _login(client, ziel.username)
    raum = client.post(
        f"/api/social/calls/invite/{ziel.id}",
        cookies=anrufer_kekse,
        headers=_csrf(anrufer_kekse),
    ).json()["signaling_token"]

    conn_id, schlange = SyncEventService.subscribe(ziel.id)
    try:
        assert client.post(
            f"/api/social/calls/{raum}/reject",
            cookies=ziel_kekse,
            headers=_csrf(ziel_kekse),
        ).status_code == 200
        ereignisse = []
        while not schlange.empty():
            ereignisse.append(schlange.get_nowait())
        zustand = [e for e in ereignisse if e.get("type") == "user_call_state_changed"]
        assert zustand, "Der Ablehnende bekommt seinen Anrufzustand"
        assert zustand[-1]["active_call"]["raum"] == "laufender-raum"
    finally:
        SyncEventService.unsubscribe(conn_id)
        UserActiveCallRegistry.remove_room("laufender-raum")


def _raum_zwischen(db: Session, client: TestClient, anrufer: User, ziel: User) -> str:
    """Zwei Freunde, ein offener Raum. Gibt den Raumnamen zurueck."""
    _befreunde(db, anrufer, ziel)
    kekse = _login(client, anrufer.username)
    antwort = client.post(
        f"/api/social/calls/invite/{ziel.id}",
        cookies=kekse,
        headers=_csrf(kekse),
    )
    assert antwort.status_code == 200
    return antwort.json()["signaling_token"]


def _token_anfordern(client: TestClient, raum: str, kekse: dict[str, str]):
    return client.post(
        "/api/social/calls/token",
        json={"art": "direkt", "raum": raum},
        cookies=kekse,
        headers=_csrf(kekse),
    )


def test_kein_token_mehr_nach_blockieren(db: Session, client: TestClient) -> None:
    """Blockieren schliesst die Tuer, auch wenn der Raum schon offen steht.

    Das laufende Gespraech bricht davon nicht ab — ein Token wird nur beim
    Verbinden gezogen. Was nicht mehr geht, ist Neuverbinden, Geraetewechsel und
    spaetes Annehmen. Ohne diese Pruefung waere die Freundschaft nur in der
    Sekunde des Waehlens eine Bedingung und danach zwei Stunden lang keine mehr.
    """
    anrufer = _user(db, f"anrufer_{secrets.token_hex(4)}")
    ziel = _user(db, f"ziel_{secrets.token_hex(4)}")
    raum = _raum_zwischen(db, client, anrufer, ziel)
    anrufer_kekse = _login(client, anrufer.username)
    ziel_kekse = _login(client, ziel.username)

    assert _token_anfordern(client, raum, ziel_kekse).status_code == 200

    blockiert = client.post(
        f"/api/social/friends/{anrufer.id}/block",
        cookies=ziel_kekse,
        headers=_csrf(ziel_kekse),
    )
    assert blockiert.status_code == 200

    # Beide Seiten: der Blockierte kommt nicht mehr herein, und wer blockiert
    # hat, betritt den gemeinsamen Raum ebenfalls nicht mehr.
    for kekse in (anrufer_kekse, ziel_kekse):
        abgewiesen = _token_anfordern(client, raum, kekse)
        assert abgewiesen.status_code == 403
        assert "Freunden" in abgewiesen.json()["detail"]


def test_kein_token_mehr_nach_entfreunden(db: Session, client: TestClient) -> None:
    anrufer = _user(db, f"anrufer_{secrets.token_hex(4)}")
    ziel = _user(db, f"ziel_{secrets.token_hex(4)}")
    raum = _raum_zwischen(db, client, anrufer, ziel)
    anrufer_kekse = _login(client, anrufer.username)
    ziel_kekse = _login(client, ziel.username)

    entfernt = client.delete(
        f"/api/social/friends/{ziel.id}",
        cookies=anrufer_kekse,
        headers=_csrf(anrufer_kekse),
    )
    assert entfernt.status_code == 200

    for kekse in (anrufer_kekse, ziel_kekse):
        assert _token_anfordern(client, raum, kekse).status_code == 403


def test_pending_schweigt_nach_blockieren(db: Session, client: TestClient) -> None:
    """Ein Blockierter klingelt nicht auf dem naechsten Geraet noch einmal."""
    anrufer = _user(db, f"anrufer_{secrets.token_hex(4)}")
    ziel = _user(db, f"ziel_{secrets.token_hex(4)}")
    _raum_zwischen(db, client, anrufer, ziel)
    ziel_kekse = _login(client, ziel.username)

    vorher = client.get("/api/social/calls/pending", cookies=ziel_kekse)
    assert vorher.status_code == 200
    assert vorher.json()["has_pending_call"] is True

    client.post(
        f"/api/social/friends/{anrufer.id}/block",
        cookies=ziel_kekse,
        headers=_csrf(ziel_kekse),
    )

    nachher = client.get("/api/social/calls/pending", cookies=ziel_kekse)
    assert nachher.status_code == 200
    assert nachher.json()["has_pending_call"] is False
    assert nachher.json()["call"] is None


def test_abbrechen_meldet_dem_ziel_und_entwertet_den_raum(
    db: Session, client: TestClient
) -> None:
    anrufer = _user(db, f"anrufer_{secrets.token_hex(4)}")
    ziel = _user(db, f"ziel_{secrets.token_hex(4)}")
    _befreunde(db, anrufer, ziel)
    anrufer_kekse = _login(client, anrufer.username)
    ziel_kekse = _login(client, ziel.username)

    raum = client.post(
        f"/api/social/calls/invite/{ziel.id}",
        cookies=anrufer_kekse,
        headers=_csrf(anrufer_kekse),
    ).json()["signaling_token"]

    conn_id, schlange = SyncEventService.subscribe(ziel.id)
    try:
        abgebrochen = client.post(
            f"/api/social/calls/{raum}/cancel",
            cookies=anrufer_kekse,
            headers=_csrf(anrufer_kekse),
        )
        assert abgebrochen.status_code == 200
        ereignis = schlange.get_nowait()
        assert ereignis["type"] == "direct_call_cancelled"
        assert ereignis["signaling_token"] == raum
    finally:
        SyncEventService.unsubscribe(conn_id)

    spaet = client.post(
        "/api/social/calls/token",
        json={"art": "direkt", "raum": raum},
        cookies=ziel_kekse,
        headers=_csrf(ziel_kekse),
    )
    assert spaet.status_code == 410


# ── Jemanden nachholen ──────────────────────────────────────────────────────


def test_dritten_in_ein_laufendes_gespraech_holen(db: Session, client: TestClient) -> None:
    anrufer = _user(db, f"anrufer_{secrets.token_hex(4)}")
    ziel = _user(db, f"ziel_{secrets.token_hex(4)}")
    dritter = _user(db, f"dritter_{secrets.token_hex(4)}")
    _befreunde(db, anrufer, ziel)
    _befreunde(db, anrufer, dritter)
    anrufer_kekse = _login(client, anrufer.username)
    dritter_kekse = _login(client, dritter.username)

    raum = client.post(
        f"/api/social/calls/invite/{ziel.id}",
        cookies=anrufer_kekse,
        headers=_csrf(anrufer_kekse),
    ).json()["signaling_token"]

    conn_id, schlange = SyncEventService.subscribe(dritter.id)
    try:
        nachgeholt = client.post(
            f"/api/social/calls/{raum}/invite/{dritter.id}?mode=audio",
            cookies=anrufer_kekse,
            headers=_csrf(anrufer_kekse),
        )
        assert nachgeholt.status_code == 200
        ereignis = schlange.get_nowait()
        assert ereignis["type"] == "direct_call_invitation"
        assert ereignis["signaling_token"] == raum
    finally:
        SyncEventService.unsubscribe(conn_id)

    # Der Dritte ist mit dem Angerufenen **nicht** befreundet, nur mit dem
    # Anrufer. Die Freundschaftspruefung an der Tokenausgabe fragt deshalb nach
    # einem Freund unter den Berechtigten, nicht nach allen — sonst waere das
    # Nachholen hier stillschweigend abgeschafft.
    zugang = client.post(
        "/api/social/calls/token",
        json={"art": "direkt", "raum": raum},
        cookies=dritter_kekse,
        headers=_csrf(dritter_kekse),
    )
    assert zugang.status_code == 200


def test_nachholen_nur_fuer_eigene_freunde(db: Session, client: TestClient) -> None:
    """Ein Raum ist kein Weg, Fremde an Dritte heranzufuehren."""
    anrufer = _user(db, f"anrufer_{secrets.token_hex(4)}")
    ziel = _user(db, f"ziel_{secrets.token_hex(4)}")
    fremder = _user(db, f"fremder_{secrets.token_hex(4)}")
    _befreunde(db, anrufer, ziel)
    anrufer_kekse = _login(client, anrufer.username)

    raum = client.post(
        f"/api/social/calls/invite/{ziel.id}",
        cookies=anrufer_kekse,
        headers=_csrf(anrufer_kekse),
    ).json()["signaling_token"]

    abgelehnt = client.post(
        f"/api/social/calls/{raum}/invite/{fremder.id}",
        cookies=anrufer_kekse,
        headers=_csrf(anrufer_kekse),
    )
    assert abgelehnt.status_code == 403
    assert fremder.id not in CallRoomService.berechtigte(raum)


def test_nachholen_nur_aus_dem_raum_heraus(db: Session, client: TestClient) -> None:
    anrufer = _user(db, f"anrufer_{secrets.token_hex(4)}")
    ziel = _user(db, f"ziel_{secrets.token_hex(4)}")
    aussen = _user(db, f"aussen_{secrets.token_hex(4)}")
    vierter = _user(db, f"vierter_{secrets.token_hex(4)}")
    _befreunde(db, anrufer, ziel)
    _befreunde(db, aussen, vierter)
    anrufer_kekse = _login(client, anrufer.username)
    aussen_kekse = _login(client, aussen.username)

    raum = client.post(
        f"/api/social/calls/invite/{ziel.id}",
        cookies=anrufer_kekse,
        headers=_csrf(anrufer_kekse),
    ).json()["signaling_token"]

    # `aussen` ist mit `vierter` befreundet, gehoert aber nicht zu diesem Raum.
    abgelehnt = client.post(
        f"/api/social/calls/{raum}/invite/{vierter.id}",
        cookies=aussen_kekse,
        headers=_csrf(aussen_kekse),
    )
    assert abgelehnt.status_code == 404
    assert vierter.id not in CallRoomService.berechtigte(raum)


# ── Raumschluessel ──────────────────────────────────────────────────────────


def test_raumschluessel_geht_nur_an_teilnehmer(db: Session, client: TestClient) -> None:
    anrufer = _user(db, f"anrufer_{secrets.token_hex(4)}")
    ziel = _user(db, f"ziel_{secrets.token_hex(4)}")
    fremder = _user(db, f"fremder_{secrets.token_hex(4)}")
    _befreunde(db, anrufer, ziel)
    anrufer_kekse = _login(client, anrufer.username)
    fremd_kekse = _login(client, fremder.username)

    raum = client.post(
        f"/api/social/calls/invite/{ziel.id}",
        cookies=anrufer_kekse,
        headers=_csrf(anrufer_kekse),
    ).json()["signaling_token"]

    umschlag = "sv-e2ee-hybrid-v1." + "A" * 64

    conn_id, schlange = SyncEventService.subscribe(ziel.id)
    try:
        zugestellt = client.post(
            f"/api/social/calls/{raum}/key",
            json={"target_user_id": ziel.id, "ciphertext": umschlag},
            cookies=anrufer_kekse,
            headers=_csrf(anrufer_kekse),
        )
        assert zugestellt.status_code == 200
        ereignis = schlange.get_nowait()
        assert ereignis["type"] == "call_key"
        assert ereignis["raum"] == raum
        assert ereignis["ciphertext"] == umschlag
        assert ereignis["from_user_id"] == anrufer.id
    finally:
        SyncEventService.unsubscribe(conn_id)

    # An einen Fremden geht nichts hinaus ...
    an_fremden = client.post(
        f"/api/social/calls/{raum}/key",
        json={"target_user_id": fremder.id, "ciphertext": umschlag},
        cookies=anrufer_kekse,
        headers=_csrf(anrufer_kekse),
    )
    assert an_fremden.status_code == 403

    # ... und ein Fremder kann darueber niemandem etwas zustellen.
    vom_fremden = client.post(
        f"/api/social/calls/{raum}/key",
        json={"target_user_id": ziel.id, "ciphertext": umschlag},
        cookies=fremd_kekse,
        headers=_csrf(fremd_kekse),
    )
    assert vom_fremden.status_code == 404


def test_teilnehmerzahl_nur_fuer_berechtigte(db: Session, client: TestClient) -> None:
    anrufer = _user(db, f"anrufer_{secrets.token_hex(4)}")
    ziel = _user(db, f"ziel_{secrets.token_hex(4)}")
    fremder = _user(db, f"fremder_{secrets.token_hex(4)}")
    _befreunde(db, anrufer, ziel)
    anrufer_kekse = _login(client, anrufer.username)
    fremd_kekse = _login(client, fremder.username)

    raum = client.post(
        f"/api/social/calls/invite/{ziel.id}",
        cookies=anrufer_kekse,
        headers=_csrf(anrufer_kekse),
    ).json()["signaling_token"]

    erlaubt = client.get(f"/api/social/calls/{raum}/teilnehmer", cookies=anrufer_kekse)
    assert erlaubt.status_code == 200
    assert erlaubt.json()["teilnehmer"] == 0

    abgelehnt = client.get(f"/api/social/calls/{raum}/teilnehmer", cookies=fremd_kekse)
    assert abgelehnt.status_code == 404
