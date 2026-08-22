"""Die Leseendpunkte der Worker-Fenster und das Tipp-Signal.

docs/agentic-framework.md (Frontend-Zeile in §12): Die Worker-Liste im Chat ist
einsehbar, nicht beschreibbar und räumt sich auf. Hier steht die Server-Hälfte
dieser Zusage unter Test:

1. `GET /conversation/workers` zeigt nur **lebende** Aufträge — Endzustände
   fallen heraus (aufräumen), die Unterhaltung selbst bleibt lesbar (Audit).
2. `GET /conversation/worker/{id}` liest über die Kennung, denn ``kind=worker``
   ist mehrdeutig — und **nur eigene** Worker-Fenster: fremde und andersartige
   sind dasselbe 404 wie unbekannte.
3. `GET /conversation/run?conversation_id=…` und
   `GET /conversation/actions?conversation_id=…` sind derselbe Kennungs-Weg
   für Lauf und Vorschlagskarten der Worker-Ansicht.
4. `POST /conversation/typing` überträgt genau einen Zeitstempel — die
   Meldestelle hält danach die Zustellung zurück (Ruhe-Regel, §4).

Einen Schreibweg gibt es nicht — das ist keine ausgelassene Route, sondern die
Zusage: gesteuert wird im Gespräch, das Gehirn ruft `worker_cancel` (§6).
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import AiActionProposal, AiConversation, AiRun, Role, RolePermission, User
from services import ai_meldestelle
from services.role_service import set_user_roles


@pytest.fixture(autouse=True)
def _mit_chatrecht(db: Session, regular_user: User) -> None:
    """Jeder Test hier spricht als Benutzer mit ``ai.chat.use`` — mehr nicht.

    Bewusst ohne ``ai.background.use``: Die Leseendpunkte gehoeren zum Chat.
    Ob jemand Worker **starten** darf, entscheidet das Werkzeugangebot — wer
    das Recht verliert, soll seine laufenden Auftraege weiterhin sehen.
    """
    role = Role(name=f"worker-leser-{regular_user.id}", description=None, is_system=False)
    db.add(role)
    db.flush()
    db.add(RolePermission(role_id=role.id, permission_key="ai.chat.use"))
    db.commit()
    set_user_roles(db, regular_user, [role.id])
    db.commit()


def _worker_fenster(
    db: Session, user: User, *, titel: str = "Backups prüfen", status: str = "running"
) -> tuple[AiConversation, AiRun]:
    fenster = AiConversation(
        id=str(uuid4()), user_id=user.id, kind="worker", title=titel
    )
    db.add(fenster)
    db.flush()
    lauf = AiRun(
        id=str(uuid4()),
        conversation_id=fenster.id,
        user_id=user.id,
        status=status,
    )
    db.add(lauf)
    db.commit()
    return fenster, lauf


def test_die_liste_zeigt_lebende_und_verschweigt_beendete(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict
) -> None:
    """„Räumt sich auf" heisst: Endzustände fallen heraus, nichts wird gelöscht."""
    lebend, _ = _worker_fenster(db, regular_user, titel="Backups prüfen")
    beendet, _ = _worker_fenster(
        db, regular_user, titel="Kalender", status="completed"
    )

    antwort = client.get("/api/ai/conversation/workers", cookies=user_cookies)

    assert antwort.status_code == 200
    eintraege = antwort.json()
    assert [e["conversation_id"] for e in eintraege] == [lebend.id]
    assert eintraege[0]["title"] == "Backups prüfen"
    assert eintraege[0]["status"] == "running"
    # Der beendete ist aus der Liste, aber nicht aus der Welt: seine
    # Unterhaltung bleibt über die Kennung lesbar.
    nachlese = client.get(
        f"/api/ai/conversation/worker/{beendet.id}", cookies=user_cookies
    )
    assert nachlese.status_code == 200


def test_die_liste_zeigt_nur_eigene_auftraege(
    client: TestClient,
    db: Session,
    owner_user: User,
    regular_user: User,
    user_cookies: dict,
) -> None:
    _worker_fenster(db, owner_user, titel="Fremder Auftrag")

    antwort = client.get("/api/ai/conversation/workers", cookies=user_cookies)

    assert antwort.status_code == 200
    assert antwort.json() == []


def test_ein_fremdes_oder_andersartiges_fenster_ist_ein_404(
    client: TestClient,
    db: Session,
    owner_user: User,
    regular_user: User,
    user_cookies: dict,
) -> None:
    """Fremd, primary oder unbekannt — von aussen ununterscheidbar dasselbe 404."""
    fremd, _ = _worker_fenster(db, owner_user)
    eigenes_primary = AiConversation(
        id=str(uuid4()), user_id=regular_user.id, kind="primary", title="Chat"
    )
    db.add(eigenes_primary)
    db.commit()

    for kennung in (fremd.id, eigenes_primary.id, str(uuid4())):
        antwort = client.get(
            f"/api/ai/conversation/worker/{kennung}", cookies=user_cookies
        )
        assert antwort.status_code == 404


def test_lauf_und_vorschlaege_kommen_ueber_die_kennung(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict
) -> None:
    """Der Kennungs-Weg der Worker-Ansicht: Lauf und Karten desselben Fensters."""
    fenster, lauf = _worker_fenster(db, regular_user, status="waiting_confirmation")
    db.add(AiActionProposal(
        id=str(uuid4()),
        conversation_id=fenster.id,
        user_id=regular_user.id,
        server_id=None,
        tool_name="propose_file_delete",
        payload_encrypted="test-enc-v1::7b7d",
        preview_json='{"operation":"file_delete","path":"broken.cfg"}',
        requires_confirmation=True,
        status="proposed",
        correlation_id=str(uuid4()),
        run_id=lauf.id,
        reason="Die Datei ist kaputt.",
        expected_effect="Der Auftrag kann weiterarbeiten.",
    ))
    db.commit()

    laufantwort = client.get(
        "/api/ai/conversation/run",
        params={"conversation_id": fenster.id},
        cookies=user_cookies,
    )
    assert laufantwort.status_code == 200
    assert laufantwort.json()["id"] == lauf.id
    assert laufantwort.json()["kind"] == "worker"

    karten = client.get(
        "/api/ai/conversation/actions",
        params={"conversation_id": fenster.id},
        cookies=user_cookies,
    )
    assert karten.status_code == 200
    assert [k["run_id"] for k in karten.json()] == [lauf.id]


def test_ein_fremder_lauf_bleibt_ueber_die_kennung_unsichtbar(
    client: TestClient,
    db: Session,
    owner_user: User,
    regular_user: User,
    user_cookies: dict,
) -> None:
    fremd, _ = _worker_fenster(db, owner_user)

    laufantwort = client.get(
        "/api/ai/conversation/run",
        params={"conversation_id": fremd.id},
        cookies=user_cookies,
    )
    assert laufantwort.status_code == 200
    assert laufantwort.json() is None

    karten = client.get(
        "/api/ai/conversation/actions",
        params={"conversation_id": fremd.id},
        cookies=user_cookies,
    )
    assert karten.status_code == 404


def test_das_tippsignal_haelt_die_zustellung_zurueck(
    client: TestClient,
    db: Session,
    regular_user: User,
    user_cookies: dict,
    user_csrf_token: str | None,
) -> None:
    """Ein 204 ohne Körper — und die Ruhe ist danach messbar vorbei.

    Übertragen wird nur der Zeitpunkt; dass kein Text mitkommt, erzwingt
    schon die Route (kein Request-Body). Geprüft wird die Wirkung: die
    Meldestelle sieht den Benutzer als beschäftigt.
    """
    ai_meldestelle.zuruecksetzen_fuer_tests()
    try:
        assert ai_meldestelle.ruhe(db, user=regular_user) is True

        antwort = client.post(
            "/api/ai/conversation/typing",
            cookies=user_cookies,
            headers={"X-CSRF-Token": user_csrf_token},
        )

        assert antwort.status_code == 204
        assert ai_meldestelle.ruhe(db, user=regular_user) is False
    finally:
        ai_meldestelle.zuruecksetzen_fuer_tests()


def test_das_tippsignal_verlangt_den_csrf_beleg(
    client: TestClient, regular_user: User, user_cookies: dict
) -> None:
    """POST ohne Header: der Double-Submit-Schutz gilt auch für Harmloses."""
    cookies = {k: v for k, v in user_cookies.items() if "csrf" not in k}
    antwort = client.post("/api/ai/conversation/typing", cookies=cookies)
    assert antwort.status_code == 403


def _wartende_karte(
    db: Session, user: User, fenster: AiConversation, lauf: AiRun,
    *, status: str = "proposed",
) -> AiActionProposal:
    karte = AiActionProposal(
        id=str(uuid4()),
        conversation_id=fenster.id,
        user_id=user.id,
        server_id=None,
        tool_name="propose_server_lifecycle",
        payload_encrypted="test-enc-v1::7b7d",
        preview_json='{"operation":"restart"}',
        requires_confirmation=True,
        status=status,
        correlation_id=str(uuid4()),
        run_id=lauf.id,
        reason="Der Benutzer hat den Neustart verlangt.",
        expected_effect="Der Server startet neu.",
    )
    db.add(karte)
    db.commit()
    return karte


def test_der_dauerchat_zeigt_die_offenen_karten_seiner_auftraege(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict
) -> None:
    """Der Knopf gehoert dorthin, wo der Mensch gefragt hat.

    Ein Worker arbeitet in einem eigenen, unsichtbaren Fenster. Seine
    Bestaetigungskarte lag damit an einer Stelle, die der Mensch nur ueber
    zwei Klicks in der Worker-Ansicht erreicht — im Chat stand stattdessen
    ein Satz darueber, dass etwas zu bestaetigen waere (22.08.2026: „Soll
    MauntARK trotzdem jetzt neu gestartet werden?").

    Umgehaengt wird nichts: die Karte bleibt bei ihrem Auftrag und wird im
    Dauerchat nur mitgeliefert.
    """
    fenster, lauf = _worker_fenster(db, regular_user, status="waiting_confirmation")
    karte = _wartende_karte(db, regular_user, fenster, lauf)

    antwort = client.get("/api/ai/conversation/actions", cookies=user_cookies)

    assert antwort.status_code == 200
    assert karte.id in [k["id"] for k in antwort.json()]

    # Und sie bleibt zugleich im Auftragsfenster sichtbar.
    im_fenster = client.get(
        "/api/ai/conversation/actions",
        params={"conversation_id": fenster.id},
        cookies=user_cookies,
    )
    assert [k["id"] for k in im_fenster.json()] == [karte.id]


def test_der_dauerchat_zeigt_nur_die_offenen_karten(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict
) -> None:
    """Erledigtes gehoert in den Auftragsverlauf, nicht in den Chat.

    Sonst waechst der Dauerchat mit jeder je ausgefuehrten Worker-Aktion —
    und der Mensch sucht den einen Knopf zwischen zwanzig grauen Karten.
    """
    fenster, lauf = _worker_fenster(db, regular_user)
    erledigt = _wartende_karte(db, regular_user, fenster, lauf, status="succeeded")

    antwort = client.get("/api/ai/conversation/actions", cookies=user_cookies)

    assert antwort.status_code == 200
    assert erledigt.id not in [k["id"] for k in antwort.json()]


def test_die_karte_eines_abgebrochenen_auftrags_verlaesst_den_dauerchat(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict
) -> None:
    """Mitgeliefert wird nur, worauf gerade jemand wartet.

    Ein Abbruch (`worker_cancel`) und ein Abloesen setzen den **Lauf** auf
    einen Endzustand, die Karte aber bleibt auf 'proposed' stehen. Ohne die
    Bindung an einen lebenden Lauf stuende sie von da an dauerhaft im
    Dauerchat — und ein Klick Wochen spaeter fuehrte aus, was der Benutzer
    ausdruecklich abgebrochen hat.

    In seinem eigenen Fenster bleibt der Vorschlag sichtbar: der Auftrag ist
    Beleg, und wer die Auftragsansicht oeffnet, hat ihn gesucht.
    """
    fenster, lauf = _worker_fenster(db, regular_user, status="waiting_confirmation")
    karte = _wartende_karte(db, regular_user, fenster, lauf)
    lauf.status = "cancelled"
    db.commit()

    antwort = client.get("/api/ai/conversation/actions", cookies=user_cookies)

    assert antwort.status_code == 200
    assert karte.id not in [k["id"] for k in antwort.json()]

    im_fenster = client.get(
        "/api/ai/conversation/actions",
        params={"conversation_id": fenster.id},
        cookies=user_cookies,
    )
    assert [k["id"] for k in im_fenster.json()] == [karte.id]


def test_fremde_auftragskarten_bleiben_unsichtbar(
    client: TestClient, db: Session, owner_user: User, regular_user: User,
    user_cookies: dict,
) -> None:
    """Die Erweiterung darf die Eigentumsgrenze nicht aufweichen."""
    fremd, fremder_lauf = _worker_fenster(db, owner_user)
    fremde_karte = _wartende_karte(db, owner_user, fremd, fremder_lauf)

    antwort = client.get("/api/ai/conversation/actions", cookies=user_cookies)

    assert antwort.status_code == 200
    assert fremde_karte.id not in [k["id"] for k in antwort.json()]
