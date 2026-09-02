"""Ownership-, Secret- und Opt-out-Invarianten fuer AI-Memory."""

from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import AiConversation, AiMemoryEntry, Role, RolePermission, User
from services.ai_context_service import build_provider_messages
from services.ai_limit_service import LIMIT_FIELDS, set_role_limit
from services.role_service import set_user_roles


def _enable_memory(db: Session, user: User) -> None:
    role = Role(name=f"memory-{user.id}", is_system=False)
    db.add(role)
    db.flush()
    db.add(RolePermission(role_id=role.id, permission_key="ai.memory.use"))
    set_role_limit(db, role.id, {field: None for field in LIMIT_FIELDS})
    db.commit()
    set_user_roles(db, user, [role.id])


def _csrf(cookies: dict) -> dict[str, str]:
    return {"X-CSRF-Token": cookies.get("__Secure-csrf_token", "")}


def test_memory_api_stores_ciphertext_and_returns_owned_plaintext(
    client: TestClient,
    db: Session,
    regular_user: User,
    user_cookies: dict,
) -> None:
    _enable_memory(db, regular_user)
    value = "Antwortsprache ist Deutsch"

    saved = client.put(
        "/api/ai/memory",
        json={"scope": "user", "key": "language", "value": value},
        cookies=user_cookies,
        headers=_csrf(user_cookies),
    )
    listed = client.get(
        "/api/ai/memory?scope=user", cookies=user_cookies
    )

    assert saved.status_code == 200
    assert listed.status_code == 200
    assert listed.json()[0]["value"] == value
    row = db.query(AiMemoryEntry).one()
    assert row.value_encrypted != value
    assert value not in row.value_encrypted


def test_memory_rejects_secret_like_content_without_persistence(
    client: TestClient,
    db: Session,
    regular_user: User,
    user_cookies: dict,
) -> None:
    _enable_memory(db, regular_user)

    response = client.put(
        "/api/ai/memory",
        json={"scope": "user", "key": "bad", "value": "api_key=do-not-store-this"},
        cookies=user_cookies,
        headers=_csrf(user_cookies),
    )

    assert response.status_code == 422
    assert db.query(AiMemoryEntry).count() == 0


def test_panel_memory_write_requires_settings_permission_but_is_visible(
    client: TestClient,
    db: Session,
    regular_user: User,
    user_cookies: dict,
    owner_cookies: dict,
) -> None:
    _enable_memory(db, regular_user)
    denied = client.put(
        "/api/ai/memory",
        json={"scope": "panel", "key": "maintenance", "value": "Sonntag 03:00 UTC"},
        cookies=user_cookies,
        headers=_csrf(user_cookies),
    )
    created = client.put(
        "/api/ai/memory",
        json={"scope": "panel", "key": "maintenance", "value": "Sonntag 03:00 UTC"},
        cookies=owner_cookies,
        headers=_csrf(owner_cookies),
    )
    visible = client.get("/api/ai/memory?scope=panel", cookies=user_cookies)

    assert denied.status_code == 403
    assert created.status_code == 200
    assert visible.status_code == 200
    assert visible.json()[0]["key"] == "maintenance"


def test_disabled_memory_is_not_added_to_provider_context(
    client: TestClient,
    db: Session,
    regular_user: User,
    user_cookies: dict,
) -> None:
    _enable_memory(db, regular_user)
    # `_enable_memory` vergibt das Recht. Eingeschaltet ist das Gedaechtnis
    # damit noch nicht — das ist seit dem Einwilligungsschritt die Entscheidung
    # des Benutzers, und der Test nimmt sie hier ausdruecklich vorweg.
    assert client.put(
        "/api/ai/memory/preference",
        json={"enabled": True},
        cookies=user_cookies,
        headers=_csrf(user_cookies),
    ).status_code == 200
    assert client.put(
        "/api/ai/memory",
        json={"scope": "user", "key": "language", "value": "Deutsch bevorzugt"},
        cookies=user_cookies,
        headers=_csrf(user_cookies),
    ).status_code == 200
    conversation = AiConversation(
        id=str(uuid4()), user_id=regular_user.id, server_id=None, title="Memory"
    )
    db.add(conversation)
    db.commit()
    enabled_context = str(build_provider_messages(db, conversation))
    assert "Deutsch bevorzugt" in enabled_context

    disabled = client.put(
        "/api/ai/memory/preference",
        json={"enabled": False},
        cookies=user_cookies,
        headers=_csrf(user_cookies),
    )
    assert disabled.status_code == 200
    assert "Deutsch bevorzugt" not in str(build_provider_messages(db, conversation))


def test_memory_upsert_maps_unique_violation_to_conflict(
    db: Session,
    regular_user: User,
    monkeypatch,
) -> None:
    """Ein paralleler Schreibvorgang endet als 409, nicht als 500.

    Der Upsert liest erst und schreibt dann. Gewinnt dazwischen ein anderer
    Vorgang, weist uq_ai_memory_scope_key den Verlierer ab. Simuliert wird
    genau dieser Commit-Fehler, weil das Zeitfenster sonst nicht deterministisch
    zu treffen ist.
    """
    from sqlalchemy.exc import IntegrityError

    from services import ai_memory_service

    _enable_memory(db, regular_user)

    def _raise_conflict() -> None:
        raise IntegrityError("INSERT", {}, Exception("uq_ai_memory_scope_key"))

    monkeypatch.setattr(db, "commit", _raise_conflict)
    rolled_back: list[bool] = []
    monkeypatch.setattr(db, "rollback", lambda: rolled_back.append(True))

    try:
        ai_memory_service.upsert_entry(
            db, user=regular_user, scope="user", server_id=None,
            key="language", value="Deutsch",
        )
    except Exception as exc:  # noqa: BLE001 - Statuscode ist die Zusicherung
        assert getattr(exc, "status_code", None) == 409
    else:
        raise AssertionError("Unique-Verletzung wurde nicht als Konflikt gemeldet")
    assert rolled_back == [True]


def _fuellen(db: Session, user: User, anzahl: int) -> None:
    from services import ai_memory_service

    for nummer in range(anzahl):
        ai_memory_service.upsert_entry(
            db, user=user, scope="user", server_id=None,
            key=f"notiz-{nummer:04d}", value=f"Wert {nummer}",
        )
    db.commit()


def test_persoenliche_ansicht_blaettert_statt_still_zu_deckeln(
    client: TestClient,
    db: Session,
    regular_user: User,
    user_cookies: dict,
    monkeypatch,
) -> None:
    """Der Vorrat waechst auf 5.000 — die Profilseite darf daran nicht ersticken.

    Vorher entschluesselte `GET /api/ai/memory/personal` jede einzelne Zeile auf
    einen Klick. Jede davon ist ein eigener HTTP-POST an den DIS-Sidecar;
    gemessen am 19.08.2026 sind das bei 5.000 Zeilen 10,3 s, und der
    Multiplikator je sichtbarem Server kommt obendrauf. Ein stiller Deckel waere
    hier die falsche Antwort — wer aufraeumen will, darf nicht 300 von 5.000 zu
    sehen bekommen. Also: eine Seite, und die Gesamtzahl daneben.

    Die Seitengroesse ist fuer den Test kleingesetzt. Nicht aus Bequemlichkeit:
    die Zusage lautet "es wird nicht mehr entschluesselt als gezeigt", und die
    gilt fuer jede Groesse. Mit den echten 200 muesste der Test 205 Eintraege
    anlegen und dafuer das Rollenlimit anheben — er pruefte dann zwei Dinge
    gleichzeitig und keins davon besser.

    Gezaehlt werden die Sidecar-Aufrufe, nicht nur die Zeilen in der Antwort.
    Eine kurze Liste bewiese sonst nur, dass wenig ankam — nicht, dass wenig
    gerechnet wurde.
    """
    from services import ai_memory_service
    from services.dis_client import DisClient

    _enable_memory(db, regular_user)
    monkeypatch.setattr(ai_memory_service, "PERSONAL_PAGE_SIZE", 3)
    _fuellen(db, regular_user, 5)

    echt = DisClient.decrypt
    aufrufe: list[str] = []

    def zaehlend(payload, *, aad):
        aufrufe.append(aad)
        return echt(payload, aad=aad)

    monkeypatch.setattr(DisClient, "decrypt", staticmethod(zaehlend))

    erste = client.get("/api/ai/memory/personal", cookies=user_cookies)
    aufrufe_erste = len(aufrufe)
    zweite = client.get("/api/ai/memory/personal?offset=3", cookies=user_cookies)

    assert erste.status_code == 200
    seite = erste.json()
    assert len(seite["entries"]) == 3
    assert seite["total"] == 5
    assert seite["limit"] == 3
    # Genau eine Entschluesselung je gezeigter Zeile — nicht je vorhandener.
    assert aufrufe_erste == 3

    assert zweite.status_code == 200
    assert len(zweite.json()["entries"]) == 2
    # Und die zweite Seite zeigt andere Zeilen als die erste, ohne Ueberlappung
    # und ohne Luecke: zusammen sind es genau die fuenf.
    erste_ids = [row["id"] for row in seite["entries"]]
    zweite_ids = [row["id"] for row in zweite.json()["entries"]]
    assert len(set(erste_ids + zweite_ids)) == 5


def test_persoenliche_seite_zaehlt_fuer_alles_loeschen_die_richtige_zahl(
    client: TestClient,
    db: Session,
    regular_user: User,
    user_cookies: dict,
) -> None:
    """`clearable` ist nicht `total`, und das ist der ganze Punkt.

    "Alle loeschen" im Profil raeumt ueber die Kennung `user:{id}` ab. Die
    eigenen Notizen zu einzelnen Servern liegen unter `server:{sid}:user:{uid}`
    und bleiben stehen — sie stehen aber in derselben Liste. Die
    Bestaetigungsfrage muss deshalb die Zahl nennen, die sie danach wirklich
    trifft; mit der Gesamtzahl fragte sie nach zwei und loeschte einen.

    Ohne die Seitenweise konnte die Oberflaeche das selbst ausrechnen, weil ihr
    alles vorlag. Seit sie 200 von 5.000 sieht, kann sie es nicht mehr — die
    Zahl muss vom Server kommen.
    """
    from models import Server, ServerPermission
    from services import ai_memory_service

    _enable_memory(db, regular_user)
    server = Server(
        name="Notizserver", game_type="dayz",
        install_dir="/tmp/notizserver", status="stopped",
    )
    db.add(server)
    db.commit()
    db.add(ServerPermission(
        user_id=regular_user.id, server_id=server.id, permission_key="server.view",
    ))
    db.commit()
    ai_memory_service.upsert_entry(
        db, user=regular_user, scope="user", server_id=None,
        key="ram", value="Ich nehme immer 8 GB",
    )
    ai_memory_service.upsert_entry(
        db, user=regular_user, scope="server", server_id=server.id,
        key="startzeit", value="Startet nur mit erhoehtem Timeout",
    )
    db.commit()

    seite = client.get("/api/ai/memory/personal", cookies=user_cookies).json()

    assert seite["total"] == 2
    assert seite["clearable"] == 1


def test_persoenliche_seite_laesst_sich_keine_groessere_bestellen(
    client: TestClient,
    db: Session,
    regular_user: User,
    user_cookies: dict,
    monkeypatch,
) -> None:
    """Die Seitengroesse gehoert dem Server, nicht dem Aufrufer.

    Sie wird in Sidecar-Roundtrips bezahlt. Ein `limit` in der Anfrage waere die
    Einladung, sich 5.000 Entschluesselungen auf einmal zu bestellen — genau die
    10,3 s, gegen die die Seitenweise gebaut ist. FastAPI nimmt unbekannte
    Parameter still hin, deshalb steht die Zusage hier und nicht im Vertrauen
    darauf, dass niemand es versucht.
    """
    from services import ai_memory_service

    _enable_memory(db, regular_user)
    monkeypatch.setattr(ai_memory_service, "PERSONAL_PAGE_SIZE", 3)
    _fuellen(db, regular_user, 5)

    versuch = client.get("/api/ai/memory/personal?limit=99", cookies=user_cookies)
    negativ = client.get("/api/ai/memory/personal?offset=-1", cookies=user_cookies)

    assert versuch.status_code == 200
    assert len(versuch.json()["entries"]) == 3
    # Ein negativer Offset ist kein Ruecklauf ins Nichts, sondern eine Abweisung.
    assert negativ.status_code == 422
