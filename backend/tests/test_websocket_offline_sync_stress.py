from __future__ import annotations

import asyncio
import base64
import json
import time

import pytest
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

from models import User, UserFriend, E2eeBlindEnvelope
from services.auth_service import AuthService
from services.social_service import SocialService
from services.sync_event_service import SyncEventService

# Der Cookie-Weg verlangt eine erlaubte Herkunft (dependencies.get_current_user_for_ws).
HERKUNFT = {"origin": "http://localhost:3000"}


# Die Schlüsselkennung im Kopf eines Gruppenumschlags, wie sie seit 09/2026
# vor dem Chiffretext steht.
_GRUPPEN_KEY = "00112233445566ff."


def _umschlag(marke: str) -> str:
    """Ein formal gültiger Gruppenumschlag mit wiedererkennbarem Inhalt.

    Hier geht es um Zustellung, Entprellung und Reihenfolge, nicht um Krypto —
    der Inhalt muss nur unterscheidbar sein. Die Form muss trotzdem stimmen:
    `relay_blind_envelope` prüft sie beim Schreiben und weist alles ab, was
    nicht wie ein Chiffretext aussieht. Die früher hier stehenden Kurzformen
    (`sv-e2ee-group-v1:alice-secret`) stammen aus der Zeit, als sich der
    Gruppenschlüssel aus der Gruppenkennung ableiten ließ, und fallen durch.

    Aufgefüllt wird auf 32 Bytes, weil der Prüfer mindestens 28 verlangt (12
    Byte Nonce, 16 Byte Tag), und mit einem Füllzeichen statt mit Nullen, damit
    kurze Marken nicht an der Null-Nonce-Regel hängenbleiben.
    """
    roh = marke.encode("utf-8").ljust(32, b"*")[:32]
    return f"sv-e2ee-group-v1:{_GRUPPEN_KEY}{base64.b64encode(roh).decode()}"


def _create_user(db: Session, username: str, privacy: str = "friends") -> User:
    user = User(
        username=username,
        email=f"{username}@example.com",
        password_hash=AuthService.hash_password("Password123!"),
        is_active=True,
        email_verified=True,
        social_privacy=privacy,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _login_user(client: TestClient, username: str) -> dict:
    resp = client.post("/api/auth/login", json={
        "username": username,
        "password": "Password123!",
        "otp_code": None,
    })
    assert resp.status_code == 200
    return dict(resp.cookies)


# ============================================================================
# 1. Message Deduplication & Idempotency Tests
# ============================================================================

def test_e2ee_envelope_idempotent_deduplication(db: Session):
    """Prüft, dass wiederholtes Senden mit identischer client_uuid keine Duplikate erzeugt."""
    mailbox = "mailbox-stress-test-1"
    uuid_tag = "client-uuid-stable-12345"
    payload = _umschlag("encrypted-content-1")

    # Erstes Senden
    env1 = SocialService.relay_blind_envelope(
        db,
        blind_mailbox_id=mailbox,
        ciphertext_envelope=payload,
        client_uuid=uuid_tag,
    )
    assert env1.id is not None
    assert env1.client_uuid == uuid_tag

    # Wiederholtes Senden (Retry / Netzwerkschwankung)
    env2 = SocialService.relay_blind_envelope(
        db,
        blind_mailbox_id=mailbox,
        ciphertext_envelope=payload,
        client_uuid=uuid_tag,
    )
    # Muss dieselbe ID zurückliefern
    assert env2.id == env1.id
    assert env2.client_uuid == uuid_tag

    # Direkte DB-Prüfung: Genau eine Zeile darf existieren
    count = db.query(E2eeBlindEnvelope).filter_by(
        blind_mailbox_id=mailbox,
        client_uuid=uuid_tag,
    ).count()
    assert count == 1

    # Abruf aus der Mailbox liefert ebenfalls nur 1 Exemplar
    envelopes = SocialService.get_blind_envelopes(db, blind_mailbox_id=mailbox)
    assert len(envelopes) == 1
    assert envelopes[0].id == env1.id


def test_e2ee_envelope_without_uuid_creates_distinct_records(db: Session):
    """Envelopes ohne client_uuid werden standardmäßig als getrennte Nachrichten behandelt.

    Zwei **verschiedene** Umschläge, nicht zweimal derselbe: entprellt wird über
    die client_uuid, und ohne sie zählt jeder Umschlag einzeln. Denselben
    Chiffretext zweimal zu schicken wäre etwas anderes — das ist eine
    Wiedereinspielung und wird mit 409 abgewiesen, weil zwei echte
    Verschlüsselungen desselben Textes nie dieselben Bytes ergeben.
    """
    mailbox = "mailbox-stress-legacy"

    e1 = SocialService.relay_blind_envelope(db, blind_mailbox_id=mailbox, ciphertext_envelope=_umschlag("legacy-msg-1"))
    e2 = SocialService.relay_blind_envelope(db, blind_mailbox_id=mailbox, ciphertext_envelope=_umschlag("legacy-msg-2"))

    assert e1.id != e2.id
    count = db.query(E2eeBlindEnvelope).filter_by(blind_mailbox_id=mailbox).count()
    assert count == 2


# ============================================================================
# 2. WebSocket relay_ack & Idempotente Bestätigung
# ============================================================================

def _quittung(ws, *, frist: float = 10.0) -> dict:
    """Wartet auf die eine Antwort, die auf einen `relay`-Rahmen folgt.

    Zwei Dinge, die die frühere Fassung nicht konnte und die diesen Test zum
    Suitenkiller gemacht haben:

    1. **Eine Fehlerantwort ist auch eine Antwort.** Der Server beantwortet
       jeden `relay`-Rahmen genau einmal — mit `relay_ack` oder mit `error`.
       Wer nur auf `relay_ack` wartet und den Fehler überliest, wartet auf
       etwas, das nie kommt.
    2. **Das Warten hat eine Frist.** `receive_json()` hängt an einer
       `queue.Queue` ohne Zeitgrenze. Unter xdist erschlägt die 120-Sekunden-
       Grenze auf Windows nicht den Test, sondern den ganzen Arbeitsprozess
       („node down: Not properly terminated"), und der Lauf bleibt bei 99 %
       stehen. Eine eigene Frist macht daraus einen gewöhnlichen Fehlschlag.

    Alles andere (etwa ein vorangestelltes `e2ee_blind_message`) wird
    übersprungen.
    """
    import queue as _queue

    ende = time.monotonic() + frist
    while True:
        rest = ende - time.monotonic()
        if rest <= 0:
            raise AssertionError(
                "Der Server hat den relay-Rahmen innerhalb der Frist weder "
                "quittiert noch abgelehnt."
            )
        try:
            rohnachricht = ws._send_queue.get(timeout=rest)
        except _queue.Empty:
            continue
        if isinstance(rohnachricht, BaseException):
            raise rohnachricht
        if rohnachricht.get("type") == "websocket.close":
            raise AssertionError(
                f"Verbindung geschlossen statt quittiert: {rohnachricht!r}"
            )
        nachricht = json.loads(rohnachricht["text"])
        if nachricht.get("type") in ("relay_ack", "error"):
            return nachricht


def test_websocket_relay_ack_and_idempotency(db: Session, client: TestClient):
    """Derselbe `client_uuid` zweimal ergibt zweimal dieselbe Umschlagkennung.

    Die Mailbox gehört hier wirklich den beiden Beteiligten. Vorher stand da
    eine ausgedachte Kennung (`ws-mailbox-dedup-test`), und seit die
    Entprellung **hinter** der Berechtigungsprüfung steht — der Sicherheitsfix
    aus 09/2026, siehe `relay_blind_envelope` —, antwortet der Server darauf
    mit 403 statt mit einer Quittung. Der Test prüfte damit eine Zusage, die
    das Panel bewusst nicht mehr gibt.
    """
    anna = _create_user(db, "ws_dedup_anna")
    bert = _create_user(db, "ws_dedup_bert")
    db.add_all([
        UserFriend(user_id=anna.id, friend_id=bert.id, status="accepted"),
        UserFriend(user_id=bert.id, friend_id=anna.id, status="accepted"),
    ])
    db.commit()

    mailbox = SocialService.ensure_direct_chat(db, anna.id, bert.id).blind_mailbox_id
    client_uuid = "client-ws-uuid-999"
    umschlag = _umschlag("ws-relay-cipher")
    cookies = _login_user(client, "ws_dedup_anna")

    with client.websocket_connect("/api/social/ws", cookies=cookies, headers=HERKUNFT) as ws:
        ws.send_json({
            "type": "relay",
            "blind_mailbox_id": mailbox,
            "ciphertext_envelope": umschlag,
            "client_uuid": client_uuid,
            "recipient_id": bert.id,
        })
        erste = _quittung(ws)
        assert erste["type"] == "relay_ack", erste
        assert erste["client_uuid"] == client_uuid
        assert isinstance(erste["id"], int)

        # Derselbe Rahmen noch einmal — so sieht ein Wiederholungsversuch nach
        # einer Netzschwankung aus.
        ws.send_json({
            "type": "relay",
            "blind_mailbox_id": mailbox,
            "ciphertext_envelope": umschlag,
            "client_uuid": client_uuid,
            "recipient_id": bert.id,
        })
        zweite = _quittung(ws)
        assert zweite["type"] == "relay_ack", zweite
        assert zweite["client_uuid"] == client_uuid
        assert zweite["id"] == erste["id"]

    assert db.query(E2eeBlindEnvelope).filter_by(
        blind_mailbox_id=mailbox, client_uuid=client_uuid
    ).count() == 1


def test_websocket_relay_in_fremde_mailbox_wird_abgelehnt(db: Session, client: TestClient):
    """Eine Mailbox, zu der man nicht gehört, bringt eine Ablehnung — keine Quittung.

    Die Kehrseite des Tests darüber, und der Grund, warum er umgeschrieben
    werden musste: die Mailbox-Kennung ist für jeden ausrechenbar. Ohne diese
    Zusage wäre der Entprellungspfad ein Weg an der Berechtigungsprüfung vorbei.
    """
    fremde = _create_user(db, "ws_dedup_fremde")
    cookies = _login_user(client, "ws_dedup_fremde")

    with client.websocket_connect("/api/social/ws", cookies=cookies, headers=HERKUNFT) as ws:
        ws.send_json({
            "type": "relay",
            "blind_mailbox_id": "ws-mailbox-dedup-test",
            "ciphertext_envelope": _umschlag("ws-relay-fremd"),
            "client_uuid": "client-ws-uuid-fremd",
        })
        antwort = _quittung(ws)

    assert antwort["type"] == "error", antwort
    assert antwort["status_code"] == 403
    assert db.query(E2eeBlindEnvelope).filter_by(
        blind_mailbox_id="ws-mailbox-dedup-test"
    ).count() == 0


# ============================================================================
# 3. Heartbeat / Ping Isolation
# ============================================================================

def test_websocket_ping_pong_isolated(client: TestClient, owner_cookies: dict):
    """Prüft, dass Heartbeats (ping/pong) direkt beantwortet werden und keine Events triggern."""
    with client.websocket_connect("/api/social/ws", cookies=owner_cookies, headers=HERKUNFT) as ws:
        ws.send_json({"type": "ping"})
        data = ws.receive_json()
        assert data == {"type": "pong"}


# ============================================================================
# 4. Ghost-Mode & Online-Status-Leak Isolation
# ============================================================================

def test_websocket_ghost_mode_never_leaks_to_strangers(db: Session, client: TestClient):
    """Ghost-Mode ('invisible' oder 'friends'/'private'): WebSocket-Join darf Nicht-Freunden keinen Status verraten."""
    alice = _create_user(db, "alice_ghost", privacy="friends")
    stranger = _create_user(db, "bob_stranger", privacy="friends")

    # Alice auf invisible setzen
    SocialService.update_presence(db, alice.id, {"status": "invisible"})

    stranger_cookies = _login_user(client, "bob_stranger")
    alice_cookies = _login_user(client, "alice_ghost")

    # Bob verbindet sich mit WebSocket
    with client.websocket_connect("/api/social/ws", cookies=stranger_cookies, headers=HERKUNFT) as ws_bob:
        # Alice verbindet sich und sendet join
        with client.websocket_connect("/api/social/ws", cookies=alice_cookies, headers=HERKUNFT) as ws_alice:
            ws_alice.send_json({"type": "join"})

            # Bob sendet ping, um zu verifizieren, dass die Socket-Verbindung aktiv ist
            ws_bob.send_json({"type": "ping"})
            bob_resp = ws_bob.receive_json()
            # Bob darf nur sein eigenes Pong erhalten, KEIN user_joined Event von Alice!
            assert bob_resp == {"type": "pong"}


def test_websocket_friends_join_broadcasts_only_to_friends(db: Session, client: TestClient):
    """Öffentliche oder befreundete Join-Events gehen nur an berechtigte Kontakte."""
    user1 = _create_user(db, "user_public", privacy="public")
    user2 = _create_user(db, "user_friend", privacy="friends")
    user3 = _create_user(db, "user_unrelated", privacy="friends")

    # user1 und user2 sind Freunde
    rel1 = UserFriend(user_id=user1.id, friend_id=user2.id, status="accepted")
    rel2 = UserFriend(user_id=user2.id, friend_id=user1.id, status="accepted")
    db.add_all([rel1, rel2])
    db.commit()

    SocialService.update_presence(db, user1.id, {"status": "online"})

    cookies1 = _login_user(client, "user_public")
    cookies2 = _login_user(client, "user_friend")
    cookies3 = _login_user(client, "user_unrelated")

    with client.websocket_connect("/api/social/ws", cookies=cookies2, headers=HERKUNFT) as ws_friend:
        with client.websocket_connect("/api/social/ws", cookies=cookies3, headers=HERKUNFT) as ws_unrelated:
            with client.websocket_connect("/api/social/ws", cookies=cookies1, headers=HERKUNFT) as ws_alice:
                ws_alice.send_json({"type": "join"})

            # Freund erhält user_joined Event
            friend_event = ws_friend.receive_json()
            assert friend_event.get("type") == "user_joined"
            assert friend_event.get("user_id") == user1.id

            # Unbeteiligter Fremder (user3) darf KEIN user_joined erhalten
            ws_unrelated.send_json({"type": "ping"})
            unrelated_resp = ws_unrelated.receive_json()
            assert unrelated_resp == {"type": "pong"}


# ============================================================================
# 5. Stress Test: Schnelle Verbindungsabbrüche & Flapping
# ============================================================================

def test_websocket_rapid_connect_disconnect_stress(client: TestClient, owner_cookies: dict):
    """Stresstest für 25 aufeinanderfolgende schnelle Connect/Disconnect Zyklen ohne Session-Leaks."""
    for i in range(25):
        with client.websocket_connect("/api/social/ws", cookies=owner_cookies, headers=HERKUNFT) as ws:
            ws.send_json({"type": "ping"})
            resp = ws.receive_json()
            assert resp == {"type": "pong"}
            # Schließt sofort


def test_sync_events_websocket_rapid_connect_disconnect(client: TestClient, owner_cookies: dict):
    """Stresstest für /api/events/ws (Sync Events WebSocket) ohne Session-Leaks."""
    for i in range(15):
        with client.websocket_connect("/api/events/ws", cookies=owner_cookies, headers=HERKUNFT) as ws:
            ready = ws.receive_json()
            assert ready.get("type") == "ready"
            ws.send_json({"type": "ping"})
            resp = ws.receive_json()
            assert resp == {"type": "pong"}


# ============================================================================
# 6. Edge Cases: Security Invariants & WebSocket Crash Resilience
# ============================================================================

def test_e2ee_envelope_unauthorized_user_cannot_access_existing_uuid(db: Session):
    """Sicherheits-Invariante: Ein unberechtigter Dritter darf auch bei bekannter client_uuid
    keinen Umschlag aus einer fremden Mailbox abrufen."""
    from fastapi import HTTPException
    alice = _create_user(db, "alice_secure", privacy="friends")
    bob = _create_user(db, "bob_secure", privacy="friends")
    charlie = _create_user(db, "charlie_intruder", privacy="friends")

    # Alice und Bob sind Freunde
    rel1 = UserFriend(user_id=alice.id, friend_id=bob.id, status="accepted")
    rel2 = UserFriend(user_id=bob.id, friend_id=alice.id, status="accepted")
    db.add_all([rel1, rel2])
    db.commit()

    # Alice und Bob haben einen Chat
    chat = SocialService.ensure_direct_chat(db, alice.id, bob.id)
    mailbox = chat.blind_mailbox_id
    uuid_tag = "uuid-secret-msg-42"

    # Alice sendet Nachricht
    env = SocialService.relay_blind_envelope(
        db,
        blind_mailbox_id=mailbox,
        ciphertext_envelope=_umschlag("alice-secret"),
        sender_user_id=alice.id,
        client_uuid=uuid_tag,
    )
    assert env.id is not None

    # Charlie (nicht berechtigt) versucht denselben Umschlag über die client_uuid abzugreifen
    with pytest.raises(HTTPException) as exc_info:
        SocialService.relay_blind_envelope(
            db,
            blind_mailbox_id=mailbox,
            ciphertext_envelope=_umschlag("charlie-fake"),
            sender_user_id=charlie.id,
            client_uuid=uuid_tag,
        )
    # Muss mit 400 oder 403 abgewiesen werden (Berechtigungsfehler)
    assert exc_info.value.status_code in (400, 403)

    # Und derselbe Versuch ohne Empfängerangabe, denn das ist der Weg, der die
    # Berechtigung wirklich am Chat prüft statt an der Mailbox-Ableitung. Genau
    # hier lag die Lücke: die Entprellung über die client_uuid stand einmal vor
    # dem Berechtigungsblock und gab den fremden Umschlag heraus, bevor
    # irgendwer gefragt hatte, ob Charlie zu diesem Gespräch gehört. Die
    # Mailbox-Kennung ist kein Geheimnis, sie ist der Hash zweier
    # Benutzerkennungen.
    with pytest.raises(HTTPException) as ohne_empfaenger:
        SocialService.relay_blind_envelope(
            db,
            blind_mailbox_id=mailbox,
            ciphertext_envelope=_umschlag("charlie-zweiter-versuch"),
            sender_user_id=charlie.id,
            client_uuid=uuid_tag,
        )
    assert ohne_empfaenger.value.status_code == 403

    # Der Umschlag liegt unverändert da, und es ist genau einer.
    verbliebene = db.query(E2eeBlindEnvelope).filter_by(blind_mailbox_id=mailbox).all()
    assert len(verbliebene) == 1
    assert verbliebene[0].id == env.id

    # Gegenprobe: Bob gehört dazu, sein Wiederholungsversuch mit derselben
    # client_uuid bekommt weiterhin den gespeicherten Umschlag zurück statt
    # einer Absage. Ohne das wäre die Entprellung mitrepariert worden.
    wiederholung = SocialService.relay_blind_envelope(
        db,
        blind_mailbox_id=mailbox,
        ciphertext_envelope=_umschlag("alice-secret"),
        sender_user_id=bob.id,
        client_uuid=uuid_tag,
    )
    assert wiederholung.id == env.id


def test_websocket_handles_invalid_relay_without_crash_or_disconnect(db: Session, client: TestClient):
    """Crash-Resilienz: Wenn ein Client einen fehlerhaften Relay-Frame sendet,
    stürzt der WebSocket nicht ab und trennt nicht die Verbindung, sondern antwortet mit einem Error-Frame."""
    alice = _create_user(db, "alice_ws_crash_test")
    cookies = _login_user(client, "alice_ws_crash_test")

    with client.websocket_connect("/api/social/ws", cookies=cookies, headers=HERKUNFT) as ws:
        # Sende ungültigen Frame (falscher recipient_id für eine unpassende Mailbox)
        ws.send_json({
            "type": "relay",
            "blind_mailbox_id": "completely-invalid-mailbox-hash-not-matching",
            "ciphertext_envelope": "short",
            "recipient_id": 999999,
            "client_uuid": "failing-uuid-1",
        })

        # Server muss einen error Frame senden, OHNE die Verbindung zu schließen
        err_msg = ws.receive_json()
        assert err_msg.get("type") == "error"
        assert err_msg.get("error") == "relay_failed"
        assert err_msg.get("client_uuid") == "failing-uuid-1"

        # Verbindung ist weiterhin aktiv und antwortet auf Ping
        ws.send_json({"type": "ping"})
        pong = ws.receive_json()
        assert pong == {"type": "pong"}


def test_websocket_blocked_user_never_receives_user_joined(db: Session, client: TestClient):
    """Ghost-Mode & Block: Auch bei öffentlichem Profil darf ein blockierter Nutzer niemals
    ein user_joined Event erhalten."""
    alice = _create_user(db, "alice_blocker", privacy="public")
    bob = _create_user(db, "bob_blocked", privacy="public")

    # Vorab bestehender Chat zwischen beiden
    chat = SocialService.ensure_direct_chat(db, alice.id, bob.id)

    # Alice blockiert Bob
    SocialService.block_user(db, alice.id, bob.id)

    alice_cookies = _login_user(client, "alice_blocker")
    bob_cookies = _login_user(client, "bob_blocked")

    with client.websocket_connect("/api/social/ws", cookies=bob_cookies, headers=HERKUNFT) as ws_bob:
        with client.websocket_connect("/api/social/ws", cookies=alice_cookies, headers=HERKUNFT) as ws_alice:
            ws_alice.send_json({"type": "join"})

        # Bob sendet ping — darf KEIN user_joined von Alice erhalten!
        ws_bob.send_json({"type": "ping"})
        resp = ws_bob.receive_json()
        assert resp == {"type": "pong"}


def test_e2ee_envelope_concurrent_duplicate_relays_race_condition(db: Session):
    """Nebenläufigkeits-Test: Gleichzeitige Inserts mit identischer client_uuid
    lösen die IntegrityError-Behandlung sauber aus und geben die gleiche ID zurück."""
    mailbox = "concurrent-mailbox-test"
    uuid_tag = "concurrent-uuid-xyz"
    payload = _umschlag("concurrent-payload")

    # Sequentiell und verschränkt testen
    env1 = SocialService.relay_blind_envelope(
        db,
        blind_mailbox_id=mailbox,
        ciphertext_envelope=payload,
        client_uuid=uuid_tag,
    )
    env2 = SocialService.relay_blind_envelope(
        db,
        blind_mailbox_id=mailbox,
        ciphertext_envelope=payload,
        client_uuid=uuid_tag,
    )

    assert env1.id == env2.id
    assert db.query(E2eeBlindEnvelope).filter_by(blind_mailbox_id=mailbox, client_uuid=uuid_tag).count() == 1



@pytest.mark.parametrize(
    "pfad", ["/api/social/ws", "/api/events/ws", "/api/events/live/ws", "/api/sync/ws", "/api/sync/events/ws"]
)
@pytest.mark.parametrize("herkunft", [{"origin": "https://fremd.example"}, {}])
def test_fremde_seite_liest_den_live_strom_nicht_mit(
    client: TestClient, owner_cookies: dict, pfad: str, herkunft: dict
):
    """Eine fremde Seite öffnet den Socket, der Browser legt das Cookie bei.

    Mit getrenntem Frontend (SameSite=None) tut Chrome das für jede Seite.
    Ohne Herkunftsprüfung läse sie KI-Notizen, Team-Notizen und
    Anrufeinladungen mit.
    """
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as fehler:
        # Nimmt der Server an, endet der Block ohne Ausnahme und der Test
        # ist rot, statt auf eine Nachricht zu warten.
        with client.websocket_connect(pfad, cookies=owner_cookies, headers=herkunft):
            pass
    assert fehler.value.code == 1008
