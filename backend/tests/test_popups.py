from datetime import datetime, timezone, timedelta
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import User, PanelPopup, UserPopupState
from services.ai_action_errors import AiActionValidationError
from services.ai_proposal_service import _popup_set_payload, _ausfuehren_popup_set, _AusfuehrungsRahmen


def _rahmen(payload: dict, user: User) -> _AusfuehrungsRahmen:
    return _AusfuehrungsRahmen(
        payload=payload,
        server_id=None,
        active_user=user,
        correlation_id="corr_test",
        expected_revision=None,
        row_id="prop_test",
        guardian=None,
        tool_name="propose_popup_set",
    )


def test_popup_lifecycle(client: TestClient, owner_user: User, owner_cookies: dict, csrf_token: str, db: Session):
    headers = {"X-CSRF-Token": csrf_token}

    # 1. Keine Popups initial
    resp = client.get("/api/popups/active", cookies=owner_cookies)
    assert resp.status_code == 200
    assert resp.json() is None

    # 2. Admin legt Popup an
    create_payload = {
        "title": "Wartungsarbeiten am Wochenende",
        "content_markdown": "### Wichtige Information\nAm Samstag finden Wartungsarbeiten statt.",
        "is_active": True,
        "button_text": "Statusseite",
        "button_url": "https://status.example.com",
    }
    resp = client.post("/api/popups/admin", json=create_payload, cookies=owner_cookies, headers=headers)
    assert resp.status_code == 201
    popup_data = resp.json()
    popup_id = popup_data["id"]
    assert popup_data["title"] == "Wartungsarbeiten am Wochenende"

    # 3. Aktives Popup abrufen
    resp = client.get("/api/popups/active", cookies=owner_cookies)
    assert resp.status_code == 200
    active = resp.json()
    assert active is not None
    assert active["id"] == popup_id
    assert active["title"] == "Wartungsarbeiten am Wochenende"

    # 4. Snooze (24h)
    resp = client.post(f"/api/popups/{popup_id}/dismiss", json={"mode": "snooze"}, cookies=owner_cookies, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["mode"] == "snooze"

    # Jetzt sollte kein aktives Popup mehr geliefert werden (innerhalb 24h)
    resp = client.get("/api/popups/active", cookies=owner_cookies)
    assert resp.status_code == 200
    assert resp.json() is None

    # 5. Zeitreise: Dismissal vor 25 Stunden setzen
    state = db.query(UserPopupState).filter_by(user_id=owner_user.id, popup_id=popup_id).first()
    assert state is not None
    state.last_dismissed_at = datetime.now(timezone.utc) - timedelta(hours=25)
    db.commit()

    # Jetzt muss es wieder auftauchen!
    resp = client.get("/api/popups/active", cookies=owner_cookies)
    assert resp.status_code == 200
    assert resp.json()["id"] == popup_id

    # 6. Permanent dismiss
    resp = client.post(f"/api/popups/{popup_id}/dismiss", json={"mode": "permanent"}, cookies=owner_cookies, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["mode"] == "permanent"

    # Auch nach 48 Stunden darf es nie wieder auftauchen
    state = db.query(UserPopupState).filter_by(user_id=owner_user.id, popup_id=popup_id).first()
    state.last_dismissed_at = datetime.now(timezone.utc) - timedelta(hours=48)
    db.commit()

    resp = client.get("/api/popups/active", cookies=owner_cookies)
    assert resp.status_code == 200
    assert resp.json() is None

    # 7. Admin Update & Delete
    resp = client.put(f"/api/popups/admin/{popup_id}", json={"title": "Aktualisierter Titel"}, cookies=owner_cookies, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["title"] == "Aktualisierter Titel"

    resp = client.delete(f"/api/popups/admin/{popup_id}", cookies=owner_cookies, headers=headers)
    assert resp.status_code == 204

    resp = client.get("/api/popups/admin/list", cookies=owner_cookies)
    assert resp.status_code == 200
    assert len(resp.json()) == 0


def test_ai_popup_proposal_execution(db: Session, owner_user: User):
    # Testet Payload-Validierung und Ausführung des KI-Tools
    payload, preview = _popup_set_payload(
        db,
        owner_user,
        {
            "title": "Neues Feature: Discord RPC",
            "content_markdown": "Ab sofort unterstützen wir Discord Rich Presence.",
            "is_active": True,
        },
    )
    assert payload["title"] == "Neues Feature: Discord RPC"
    assert payload["popup_id"] is None
    assert preview["operation"] == "popup_create"

    res = _ausfuehren_popup_set(db, _rahmen(payload, owner_user))
    assert res.result["created"] is True
    popup_id = res.result["popup_id"]

    db_popup = db.query(PanelPopup).filter_by(id=popup_id).first()
    assert db_popup is not None
    assert db_popup.title == "Neues Feature: Discord RPC"


def test_ai_popup_aendert_nur_das_genannte_feld(db: Session, owner_user: User):
    """Der Fall, an dem die KI vorher scheiterte: ein Satz raus, Rest bleibt.

    Ohne `popups_read` und ohne Änderungsweg antwortete sie wörtlich, sie könne
    „den bestehenden Pop-up nicht bearbeiten, weil dafür kein Popup-Lese- oder
    Aktualisierungswerkzeug verfügbar ist".

    Geprüft wird hier vor allem die **Abwesenheit** von Wirkung: ein Aufruf, der
    nur `content_markdown` nennt, darf Titel, Button und Zeitfenster nicht
    anfassen. Ein Payload-Bau, der fehlende Felder als `None` durchreicht, würde
    genau das tun — und der Betreiber fände das Pop-up hinterher ohne Button.
    """
    bestehend = PanelPopup(
        title="Desktop-App verfügbar",
        content_markdown="Die App gibt es für Windows, Mac und Linux.",
        is_active=True,
        button_text="Herunterladen",
        button_url="https://example.com/download",
        end_at=datetime(2026, 12, 24, tzinfo=timezone.utc),
        created_by_user_id=owner_user.id,
    )
    db.add(bestehend)
    db.commit()
    db.refresh(bestehend)

    payload, preview = _popup_set_payload(
        db,
        owner_user,
        {
            "popup_id": bestehend.id,
            "content_markdown": "Die App gibt es für Windows.",
        },
    )
    assert preview["operation"] == "popup_update"
    # Der bisherige Titel steht auf der Karte, obwohl das Modell keinen nannte.
    assert preview["title"] == "Desktop-App verfügbar"
    assert "title" not in payload
    assert "button_text" not in payload

    res = _ausfuehren_popup_set(db, _rahmen(payload, owner_user))
    assert res.result["updated"] is True
    assert res.result["popup_id"] == bestehend.id

    db.refresh(bestehend)
    assert bestehend.content_markdown == "Die App gibt es für Windows."
    assert bestehend.title == "Desktop-App verfügbar"
    assert bestehend.button_text == "Herunterladen"
    assert bestehend.button_url == "https://example.com/download"
    assert bestehend.end_at is not None


def test_ai_popup_null_raeumt_das_genannte_feld(db: Session, owner_user: User):
    """`null` ist etwas anderes als Weglassen — sonst wäre kein Feld je zu leeren."""
    bestehend = PanelPopup(
        title="Mit Button",
        content_markdown="Text",
        is_active=True,
        button_text="Mehr erfahren",
        button_url="https://example.com",
        created_by_user_id=owner_user.id,
    )
    db.add(bestehend)
    db.commit()
    db.refresh(bestehend)

    payload, _ = _popup_set_payload(
        db,
        owner_user,
        {"popup_id": bestehend.id, "button_text": None, "button_url": None},
    )
    assert payload["button_text"] is None
    _ausfuehren_popup_set(db, _rahmen(payload, owner_user))

    db.refresh(bestehend)
    assert bestehend.button_text is None
    assert bestehend.button_url is None
    assert bestehend.title == "Mit Button"


def test_ai_popup_verlangt_eine_echte_kennung(db: Session, owner_user: User):
    """Eine geratene Kennung soll gar nicht erst zu einer Karte werden.

    Und sie darf vor allem nicht als **neues** Pop-up durchrutschen: das wäre
    der Fehler, der aussieht wie Erfolg — der Benutzer bestätigt eine Änderung
    und bekommt eine zweite Ankündigung.
    """
    with pytest.raises(AiActionValidationError):
        _popup_set_payload(
            db, owner_user, {"popup_id": 999_999, "content_markdown": "Neu"}
        )

    # Anlegen ohne Titel ist ebenso wenig ein Vorschlag.
    with pytest.raises(AiActionValidationError):
        _popup_set_payload(db, owner_user, {"content_markdown": "Nur Text"})

    # Und eine Änderung, die nichts nennt, ist keine.
    popup = PanelPopup(
        title="Da", content_markdown="Text", is_active=True,
        created_by_user_id=owner_user.id,
    )
    db.add(popup)
    db.commit()
    db.refresh(popup)
    with pytest.raises(AiActionValidationError):
        _popup_set_payload(db, owner_user, {"popup_id": popup.id})


def test_ai_popup_laesst_pflichtfelder_nicht_leeren(db: Session, owner_user: User):
    """Leer genannt ist nicht weggelassen.

    `title` und `content_markdown` sind in der Datenbank `nullable=False` und im
    Panel-Schema `min_length=1`. Ein `title: ""` wuerde beides unterlaufen und
    ein Pop-up ohne Ueberschrift im Panel stehen lassen.
    """
    popup = PanelPopup(
        title="Hat einen Titel", content_markdown="Hat Inhalt", is_active=True,
        created_by_user_id=owner_user.id,
    )
    db.add(popup)
    db.commit()
    db.refresh(popup)

    with pytest.raises(AiActionValidationError):
        _popup_set_payload(db, owner_user, {"popup_id": popup.id, "title": ""})
    with pytest.raises(AiActionValidationError):
        _popup_set_payload(
            db, owner_user, {"popup_id": popup.id, "content_markdown": "   "}
        )

    db.refresh(popup)
    assert popup.title == "Hat einen Titel"
    assert popup.content_markdown == "Hat Inhalt"


def test_ai_popup_prueft_das_datum_vor_der_karte(db: Session, owner_user: User):
    """Eine Karte, die erst im Bestätigungsmoment am Datumsformat scheitert,
    ist eine Zusage, die nicht hält — geprüft wird deshalb beim Bauen."""
    with pytest.raises(AiActionValidationError):
        _popup_set_payload(
            db,
            owner_user,
            {
                "title": "Wartung",
                "content_markdown": "Text",
                "end_at": "naechsten Freitag",
            },
        )


def test_ai_popup_geloescht_zwischen_vorschlag_und_klick(db: Session, owner_user: User):
    """Zwischen Karte und Bestätigung liegt ein Zeitfenster ohne Obergrenze.

    Wird das Pop-up in ihm gelöscht, muss die Ausführung scheitern — und nicht
    still ein neues anlegen.
    """
    popup = PanelPopup(
        title="Kurzlebig", content_markdown="Text", is_active=True,
        created_by_user_id=owner_user.id,
    )
    db.add(popup)
    db.commit()
    db.refresh(popup)

    payload, _ = _popup_set_payload(
        db, owner_user, {"popup_id": popup.id, "title": "Neuer Titel"}
    )
    db.delete(popup)
    db.commit()

    vorher = db.query(PanelPopup).count()
    with pytest.raises(AiActionValidationError):
        _ausfuehren_popup_set(db, _rahmen(payload, owner_user))
    assert db.query(PanelPopup).count() == vorher


def test_popups_read_liefert_die_kennung_und_meldet_kuerzung(
    db: Session, owner_user: User
):
    """Ohne Kennung kein Ändern — und ohne Marke keine ehrliche Kürzung.

    `propose_popup_set` ersetzt den Inhalt vollständig. Wer einen gekürzten Text
    zurückschreibt, löscht den Rest; deshalb sagt die Leseantwort, dass sie
    gekürzt hat.
    """
    from services.ai_tools.base import MAX_POPUP_INHALT_CHARS
    from services.ai_action_service import execute_read_tool

    kurz = PanelPopup(
        title="Kurz", content_markdown="Wenig Text", is_active=True,
        created_by_user_id=owner_user.id,
    )
    lang = PanelPopup(
        title="Lang",
        content_markdown="x" * (MAX_POPUP_INHALT_CHARS + 10),
        is_active=False,
        created_by_user_id=owner_user.id,
    )
    db.add_all([kurz, lang])
    db.commit()

    alle = execute_read_tool(
        db, user=owner_user, tool_name="popups_read", arguments={}, herkunft="panel"
    )
    nach_kennung = {p["popup_id"]: p for p in alle["popups"]}
    assert nach_kennung[kurz.id]["content_truncated"] is False
    assert nach_kennung[kurz.id]["content_markdown"] == "Wenig Text"
    assert nach_kennung[lang.id]["content_truncated"] is True
    assert len(nach_kennung[lang.id]["content_markdown"]) == MAX_POPUP_INHALT_CHARS

    nur_aktive = execute_read_tool(
        db,
        user=owner_user,
        tool_name="popups_read",
        arguments={"only_active": True},
        herkunft="panel",
    )
    kennungen = {p["popup_id"] for p in nur_aktive["popups"]}
    assert kurz.id in kennungen
    assert lang.id not in kennungen


def test_ki_aendert_ein_pop_up_ueber_den_echten_vorschlagsweg(
    db: Session, regular_user: User
):
    """Der Weg des Betreibers, von der Bitte bis zum geänderten Text.

    Die Tests darüber prüfen Payload-Bau und Ausführung einzeln. Dieser prüft,
    dass beide über `create_proposal` / `execute_proposal` auch **zusammen**
    erreichbar sind — inklusive Rechteprüfung und Bestätigungskarte. Genau
    diese Kette fehlte: es gab kein Lesewerkzeug, mit dem die KI an die
    `popup_id` gekommen wäre, und kein Werkzeug, das sie hätte verwenden können.
    """
    from uuid import uuid4

    from models import AiConversation, Role, RolePermission
    from services import ai_proposal_service
    from services.ai_action_service import execute_read_tool
    from services.role_service import set_user_roles

    rolle = Role(name=f"popup-{uuid4().hex[:6]}", description=None, is_system=False)
    db.add(rolle)
    db.flush()
    for schluessel in ("ai.chat.use", "ai.popups.manage"):
        db.add(RolePermission(role_id=rolle.id, permission_key=schluessel))
    db.commit()
    set_user_roles(db, regular_user, [rolle.id])

    unterhaltung = AiConversation(
        id=str(uuid4()), user_id=regular_user.id, server_id=None, title="Pop-up"
    )
    db.add(unterhaltung)
    bestehend = PanelPopup(
        title="Desktop-App",
        content_markdown="Verfügbar für Windows, Mac und Linux.",
        is_active=True,
        created_by_user_id=regular_user.id,
    )
    db.add(bestehend)
    db.commit()
    db.refresh(bestehend)

    # 1. Die KI sieht nach, was es gibt — vorher war genau das unmöglich.
    gelesen = execute_read_tool(
        db,
        user=regular_user,
        tool_name="popups_read",
        arguments={},
        herkunft="panel",
    )
    treffer = next(p for p in gelesen["popups"] if p["popup_id"] == bestehend.id)
    assert treffer["content_markdown"] == "Verfügbar für Windows, Mac und Linux."

    # 2. Sie schlägt die Änderung vor.
    vorschlag = ai_proposal_service.create_proposal(
        db,
        user=regular_user,
        conversation=unterhaltung,
        tool_name="propose_popup_set",
        arguments={
            "popup_id": treffer["popup_id"],
            "content_markdown": "Verfügbar für Windows.",
            "reason": "Der Hinweis auf Mac und Linux soll raus",
            "expected_effect": "Das Pop-up nennt nur noch Windows",
        },
        correlation_id=str(uuid4()),
    )
    assert vorschlag.status == "proposed"
    assert vorschlag.requires_confirmation is True

    # 3. Der Betreiber bestätigt, und erst dann ändert sich etwas.
    db.refresh(bestehend)
    assert bestehend.content_markdown == "Verfügbar für Windows, Mac und Linux."

    vorschlag, token = ai_proposal_service.confirm_proposal(
        db, proposal_id=vorschlag.id, user=regular_user
    )
    ausgefuehrt, ergebnis = ai_proposal_service.execute_proposal(
        db, proposal_id=vorschlag.id, user=regular_user, confirmation_token=token
    )
    assert ausgefuehrt.status == "succeeded"
    assert ergebnis["updated"] is True

    db.refresh(bestehend)
    assert bestehend.content_markdown == "Verfügbar für Windows."
    assert bestehend.title == "Desktop-App"


def test_ohne_das_recht_bleiben_pop_ups_unsichtbar(db: Session, regular_user: User):
    """`ai.popups.manage` deckt Lesen und Ändern — und das Fehlen deckt beides.

    Ein Lesewerkzeug prüft sein Recht im eigenen Handler (die Registry trägt für
    `popups_read` nur ein `angebot`). Ohne diesen Test stünde die Prüfung an
    einer Stelle, an der ein Wegfall niemandem auffällt.
    """
    from services.ai_action_service import execute_read_tool

    db.add(PanelPopup(
        title="Geheim für Fremde", content_markdown="Text", is_active=True,
        created_by_user_id=regular_user.id,
    ))
    db.commit()

    with pytest.raises(AiActionValidationError):
        execute_read_tool(
            db,
            user=regular_user,
            tool_name="popups_read",
            arguments={},
            herkunft="panel",
        )
