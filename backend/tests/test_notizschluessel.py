"""Ein Notizschluessel je Konto: der Eintrag auf dem Server und das Neuverschluesseln.

Der Server kennt vom Kontoschluessel nur den Abdruck. Setzen darf ihn nur, wer
die Unterschrift eines freigegebenen eigenen Geraets vorlegt — eine Sitzung
allein genuegt nicht (Durchsicht 30.09.2026). Jeder Eintrag hat einen Stand,
der nur waechst; die Unterschrift gilt fuer genau einen. Das Neuverschluesseln
tauscht nur Chiffrate, die sich seit dem Lesen nicht geaendert haben, und
laesst ``updated_at`` stehen. Was die Geraete mit dem Eintrag machen, steht in
`notizschluessel.fix.beweis.test.ts`.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from dependencies import get_current_user, get_db, verify_csrf
from main import app
from models import User
from models.calendar_event import CalendarEvent
from models.note import Note
from services import e2ee_device_service
from services.calendar_service import CalendarService
from services.notes_service import NotesService
from services.sync_event_service import SyncEventService

WEB = "vA7+K8W+Ef77uJSwVVquOW"
MSS = "1R4SxN8dMRCKdGxayCGcIO"


def _rsa_jwk(marker: str) -> str:
    return json.dumps({"kty": "RSA", "n": marker * 350, "e": "AQAB", "alg": "RSA-OAEP-256"})


def _ecdsa_paar():
    priv = ec.generate_private_key(ec.SECP256R1())
    zahlen = priv.public_key().public_numbers()

    def b64url(b: bytes) -> str:
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")

    jwk = json.dumps({
        "kty": "EC",
        "crv": "P-256",
        "x": b64url(zahlen.x.to_bytes(32, "big")),
        "y": b64url(zahlen.y.to_bytes(32, "big")),
    })
    return priv, jwk


def _unterschreibe(priv, daten: str) -> str:
    der = priv.sign(daten.encode("utf-8"), ec.ECDSA(hashes.SHA256()))
    r, s = utils.decode_dss_signature(der)
    return base64.b64encode(r.to_bytes(32, "big") + s.to_bytes(32, "big")).decode("ascii")


@pytest.fixture
def konto(db: Session, clean_db):
    user = User(username="schluessel", email="schluessel@msm.local", password_hash="pw", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    web_priv, web_ecdsa = _ecdsa_paar()
    neu_priv, neu_ecdsa = _ecdsa_paar()
    # Das erste Geraet ist freigegeben, das zweite wartet noch.
    e2ee_device_service.veroeffentlichen(db, user, "web-00000001", _rsa_jwk("A"), "Web", web_ecdsa, familie="f-web")
    e2ee_device_service.veroeffentlichen(db, user, "neu-00000002", _rsa_jwk("B"), "Neu", neu_ecdsa, familie="f-neu")

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[verify_csrf] = lambda: None
    with TestClient(app) as client:
        yield client, user, web_priv, neu_priv
    app.dependency_overrides.clear()


def _setzen(client, user, priv, geraet, abdruck, stand):
    sig = _unterschreibe(priv, NotesService.kontoschluessel_daten(user.id, abdruck, stand, geraet))
    return client.put(
        "/api/notes/kontoschluessel",
        json={"abdruck": abdruck, "stand": stand, "geraet": geraet, "signatur": sig},
    )


def test_setzen_nur_mit_unterschrift_eines_freigegebenen_geraets(konto, db: Session):
    client, user, web_priv, neu_priv = konto

    # Nicht als Notiz-Kennung gelesen, und noch keiner gesetzt.
    assert client.get("/api/notes/kontoschluessel").json()["abdruck"] is None

    # Eine Sitzung ohne Geraet, eine falsche Unterschrift, ein wartendes Geraet: nichts.
    unsinn = base64.b64encode(b"0" * 64).decode()
    assert client.put(
        "/api/notes/kontoschluessel",
        json={"abdruck": WEB, "stand": 1, "geraet": "web-00000001", "signatur": unsinn},
    ).status_code == 403
    assert _setzen(client, user, neu_priv, "neu-00000002", WEB, 1).status_code == 403
    assert _setzen(client, user, web_priv, "neu-00000002", WEB, 1).status_code == 403
    assert client.get("/api/notes/kontoschluessel").json()["abdruck"] is None

    ok = _setzen(client, user, web_priv, "web-00000001", WEB, 1)
    assert ok.status_code == 200
    stand = client.get("/api/notes/kontoschluessel").json()
    assert stand["abdruck"] == WEB and stand["geraet"] == "web-00000001" and stand["stand"] == 1
    daten = NotesService.kontoschluessel_daten(user.id, WEB, 1, "web-00000001")
    assert e2ee_device_service.unterschrift_gilt(db, user, "web-00000001", daten, stand["signatur"])


def test_geraet_eines_anderen_kontos_setzt_nichts(konto, db: Session):
    client, user, _, _ = konto
    fremd = User(username="fremd2", email="fremd2@msm.local", password_hash="pw", is_active=True)
    db.add(fremd)
    db.commit()
    db.refresh(fremd)
    fremd_priv, fremd_ecdsa = _ecdsa_paar()
    e2ee_device_service.veroeffentlichen(
        db, fremd, "fremd-0000003", _rsa_jwk("C"), "Fremd", fremd_ecdsa, familie="f-fremd"
    )
    # Freigegeben ist es — aber bei einem anderen Konto.
    assert _setzen(client, user, fremd_priv, "fremd-0000003", WEB, 1).status_code == 403
    assert client.get("/api/notes/kontoschluessel").json()["abdruck"] is None


def test_zweites_setzen_auf_demselben_stand_ist_ein_konflikt(konto):
    client, user, web_priv, _ = konto
    assert _setzen(client, user, web_priv, "web-00000001", WEB, 1).status_code == 200

    zweiter = _setzen(client, user, web_priv, "web-00000001", MSS, 1)
    assert zweiter.status_code == 409
    assert zweiter.json()["detail"]["abdruck"] == WEB

    # Ein Stand zu weit ist ebenso nichts.
    assert _setzen(client, user, web_priv, "web-00000001", MSS, 3).status_code == 409

    # Wechseln nur auf den naechsten Stand — und unterschrieben.
    assert _setzen(client, user, web_priv, "web-00000001", MSS, 2).status_code == 200
    stand = client.get("/api/notes/kontoschluessel").json()
    assert (stand["abdruck"], stand["stand"]) == (MSS, 2)


def test_alte_unterschrift_laesst_sich_nie_wieder_vorlegen(konto):
    """Auch nicht, wenn der Eintrag im Kreis zum selben Abdruck zurueckkehrte."""
    client, user, web_priv, _ = konto
    assert _setzen(client, user, web_priv, "web-00000001", WEB, 1).status_code == 200
    erste = client.get("/api/notes/kontoschluessel").json()["signatur"]
    assert _setzen(client, user, web_priv, "web-00000001", MSS, 2).status_code == 200
    zweite = client.get("/api/notes/kontoschluessel").json()["signatur"]

    # Die Unterschrift von Stand 1 auf irgendeinem Stand vorgelegt: nichts.
    for stand in (1, 2, 3):
        wiederholt = client.put(
            "/api/notes/kontoschluessel",
            json={"abdruck": WEB, "stand": stand, "geraet": "web-00000001", "signatur": erste},
        )
        assert wiederholt.status_code in (403, 409), stand
    # Und die von Stand 2 auf dem naechsten ebenso wenig.
    assert client.put(
        "/api/notes/kontoschluessel",
        json={"abdruck": MSS, "stand": 3, "geraet": "web-00000001", "signatur": zweite},
    ).status_code == 403
    assert client.get("/api/notes/kontoschluessel").json()["abdruck"] == MSS


def test_nimmt_nur_abdruecke_und_staende_ab_eins(konto):
    client, user, web_priv, _ = konto
    schluessel = "AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE="
    assert _setzen(client, user, web_priv, "web-00000001", schluessel, 1).status_code == 422
    assert _setzen(client, user, web_priv, "web-00000001", "<script>", 1).status_code == 422
    assert _setzen(client, user, web_priv, "web-00000001", WEB, 0).status_code == 422
    assert _setzen(client, user, web_priv, "web-00000001", WEB, -1).status_code == 422


def test_alle_geraete_zuruecksetzen_leert_den_eintrag_der_stand_bleibt(konto, db: Session):
    client, user, web_priv, _ = konto
    assert _setzen(client, user, web_priv, "web-00000001", WEB, 1).status_code == 200
    alt = client.get("/api/notes/kontoschluessel").json()["signatur"]
    e2ee_device_service.zuruecksetzen(db, user, None)
    assert client.get("/api/notes/kontoschluessel").json() == {
        "abdruck": None, "stand": 1, "geraet": None, "signatur": None,
    }
    # Die Unterschrift von vor dem Neubeginn setzt ihn nicht wieder.
    assert client.put(
        "/api/notes/kontoschluessel",
        json={"abdruck": WEB, "stand": 2, "geraet": "web-00000001", "signatur": alt},
    ).status_code == 403


def _alte_notiz(db: Session, user: User, uid: str, titel: str, inhalt: str, art: str = "personal") -> Note:
    frueher = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    note = Note(
        user_id=user.id, note_uid=uid, title=titel, content=inhalt, category="personal",
        color="primary", is_pinned=False, is_archived=False, note_type=art,
        created_at=frueher, updated_at=frueher,
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    return note


def _notizen(client, *eintraege):
    return client.post("/api/notes/neu-verschluesseln", json={"eintraege": list(eintraege)})


def test_notiz_neu_verschluesseln_nur_beim_unveraenderten_stand(konto, db: Session):
    client, user, _, _ = konto
    note = _alte_notiz(db, user, "n-1", "sv-note-v1:alt-titel", "sv-note-v1:alt-inhalt")
    frueher = note.updated_at

    ok = _notizen(client, {
        "note_uid": "n-1",
        "title": {"alt": "sv-note-v1:alt-titel", "neu": "sv-note-v1:neu-titel"},
        "content": {"alt": "sv-note-v1:alt-inhalt", "neu": "sv-note-v1:neu-inhalt"},
    })
    assert ok.status_code == 200
    assert ok.json() == {"geschrieben": ["n-1"], "uebersprungen": []}
    db.expire_all()
    note = db.get(Note, note.id)
    assert (note.title, note.content) == ("sv-note-v1:neu-titel", "sv-note-v1:neu-inhalt")
    # Derselbe Inhalt, nur ein anderer Schluessel: kein neuer Bearbeitungszeitpunkt.
    assert note.updated_at == frueher

    # Inzwischen auf einem anderen Geraet bearbeitet: nichts ueberschreiben.
    veraltet = _notizen(client, {
        "note_uid": "n-1", "title": {"alt": "sv-note-v1:alt-titel", "neu": "sv-note-v1:zurueck"},
    })
    assert veraltet.status_code == 200
    assert veraltet.json() == {"geschrieben": [], "uebersprungen": ["n-1"]}
    db.expire_all()
    assert db.get(Note, note.id).title == "sv-note-v1:neu-titel"


def test_sammelauftrag_ein_ereignis_und_fremdes_bleibt_liegen(konto, db: Session, monkeypatch):
    client, user, _, _ = konto
    gemeldet: list[dict] = []
    monkeypatch.setattr(SyncEventService, "publish", lambda ereignis, **_: gemeldet.append(ereignis))
    for i in range(3):
        _alte_notiz(db, user, f"s-{i}", f"sv-note-v1:t{i}", "")
    _alte_notiz(db, user, "s-team", "sv-note-v1:team", "", art="team")
    fremd = User(username="fremd3", email="fremd3@msm.local", password_hash="pw", is_active=True)
    db.add(fremd)
    db.commit()
    _alte_notiz(db, fremd, "s-fremd", "sv-note-v1:fremd", "")

    antwort = _notizen(
        client,
        *({"note_uid": f"s-{i}", "title": {"alt": f"sv-note-v1:t{i}", "neu": f"sv-note-v1:n{i}"}} for i in range(3)),
        {"note_uid": "s-team", "title": {"alt": "sv-note-v1:team", "neu": "sv-note-v1:x"}},
        {"note_uid": "s-fremd", "title": {"alt": "sv-note-v1:fremd", "neu": "sv-note-v1:x"}},
        {"note_uid": "gibt-es-nicht", "title": {"alt": "sv-note-v1:a", "neu": "sv-note-v1:b"}},
    )
    assert antwort.status_code == 200
    assert antwort.json() == {
        "geschrieben": ["s-0", "s-1", "s-2"],
        "uebersprungen": ["s-team", "s-fremd", "gibt-es-nicht"],
    }
    # Drei Notizen, ein Ereignis — und keines an das fremde Konto.
    assert gemeldet == [{"entity": "notes", "action": "updated", "user_id": user.id}]
    db.expire_all()
    titel = {n.note_uid: n.title for n in db.query(Note).all()}
    assert titel["s-team"] == "sv-note-v1:team" and titel["s-fremd"] == "sv-note-v1:fremd"


def test_notiz_neu_verschluesseln_nie_zu_und_nie_von_klartext(konto, db: Session):
    client, user, _, _ = konto
    _alte_notiz(db, user, "n-2", "sv-note-v1:a", "")
    _alte_notiz(db, user, "n-3", "Klartext alt", "")
    assert _notizen(
        client, {"note_uid": "n-3", "title": {"alt": "Klartext alt", "neu": "sv-note-v1:b"}},
    ).status_code == 400
    # Ein Klartext-Feld verwirft den ganzen Auftrag — auch die gute Notiz daneben.
    assert _notizen(
        client,
        {"note_uid": "n-2", "title": {"alt": "sv-note-v1:a", "neu": "sv-note-v1:b"}},
        {"note_uid": "n-2", "content": {"alt": "sv-note-v1:a", "neu": "Klartext"}},
    ).status_code == 400
    db.expire_all()
    assert {n.note_uid: n.title for n in db.query(Note).all()} == {"n-2": "sv-note-v1:a", "n-3": "Klartext alt"}


def test_sammelauftrag_hat_grenzen(konto):
    client, _, _, _ = konto
    assert _notizen(client).status_code == 422
    zu_viele = [{"note_uid": f"x-{i}", "title": {"alt": "sv-note-v1:a", "neu": "sv-note-v1:b"}} for i in range(201)]
    assert _notizen(client, *zu_viele).status_code == 422
    riesig = "sv-note-v1:" + "A" * 1_000_000
    assert _notizen(client, {"note_uid": "x", "title": {"alt": "sv-note-v1:a", "neu": riesig}}).status_code == 422
    assert client.post("/api/calendar/events/neu-verschluesseln", json={"eintraege": [
        {"event_id": "t", "title": {"alt": "sv-cal-v1:a", "neu": "sv-cal-v1:" + "A" * 1_000_000}},
    ]}).status_code == 422


def test_termin_neu_verschluesseln_nur_beim_unveraenderten_stand(konto, db: Session):
    client, user, _, _ = konto
    frueher = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    nativ = CalendarService.get_or_create_native_calendar(db, user)
    ev = CalendarEvent(
        user_id=user.id, calendar_id=nativ.id, event_uid="t-1", event_type="personal",
        title="sv-cal-v1:alt", description=None, location=None, recurrence="sv-cal-v1:serie",
        start_time=frueher, end_time=frueher + timedelta(hours=1), all_day=False,
        created_at=frueher, updated_at=frueher,
    )
    db.add(ev)
    db.commit()

    def termine(*eintraege):
        return client.post("/api/calendar/events/neu-verschluesseln", json={"eintraege": list(eintraege)})

    ok = termine({
        "event_id": "t-1",
        "title": {"alt": "sv-cal-v1:alt", "neu": "sv-cal-v1:neu"},
        "recurrence": {"alt": "sv-cal-v1:serie", "neu": "sv-cal-v1:serie-neu"},
    })
    assert ok.status_code == 200 and ok.json()["geschrieben"] == ["t-1"]
    db.expire_all()
    ev = db.get(CalendarEvent, ev.id)
    assert (ev.title, ev.recurrence) == ("sv-cal-v1:neu", "sv-cal-v1:serie-neu")
    assert ev.updated_at == frueher

    assert termine({
        "event_id": "t-1", "title": {"alt": "sv-cal-v1:alt", "neu": "sv-cal-v1:x"},
    }).json() == {"geschrieben": [], "uebersprungen": ["t-1"]}
    assert termine({
        "event_id": "t-1", "title": {"alt": "sv-cal-v1:neu", "neu": "Klartext"},
    }).status_code == 400
    db.expire_all()
    assert db.get(CalendarEvent, ev.id).title == "sv-cal-v1:neu"


def test_migration_laeuft_hin_und_zurueck(pg_wegwerf) -> None:
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect

    from config import settings
    from database import Base

    db_url = pg_wegwerf("notizschluessel")
    vorher = settings.database_url
    settings.database_url = db_url
    backend_dir = Path(__file__).resolve().parent.parent
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "migrations"))
    engine = create_engine(db_url)
    neu = {"notes_key_abdruck", "notes_key_stand", "notes_key_geraet", "notes_key_signatur"}

    def spalten() -> set[str]:
        return {s["name"] for s in inspect(engine).get_columns("users")}

    try:
        Base.metadata.create_all(engine)
        command.stamp(config, "head")
        assert neu <= spalten()
        command.downgrade(config, "20260929_02")
        assert not (neu & spalten())
        with engine.begin() as con:
            con.execute(text(
                "INSERT INTO users (id, username, password_hash, is_owner, is_active, email_verified, "
                "two_factor_enabled, email_notifications, ai_notifications, device_notifications, "
                "location_sharing_enabled, ai_desktop_systembereich, created_at) VALUES "
                "(1, 'alt', 'x', false, true, true, false, true, true, true, false, 'lesen', '2026-09-01')"
            ))
        command.upgrade(config, "head")
        assert neu <= spalten()
        with engine.connect() as con:
            zeile = con.execute(text("SELECT notes_key_abdruck, notes_key_stand FROM users WHERE id = 1")).one()
            assert tuple(zeile) == (None, None)
    finally:
        engine.dispose()
        settings.database_url = vorher
