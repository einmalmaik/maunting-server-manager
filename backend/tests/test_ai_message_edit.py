"""Eine eigene Nachricht bearbeiten heisst: sie zuruecknehmen.

Der Verlauf wird ab der bearbeiteten Nachricht abgeschnitten — sie selbst
eingeschlossen. Alles andere waere widerspruechlich: die verworfene Fassung und
die darauf gegebene Antwort stuenden weiter im Kontext, und das Modell wuerde
eine Frage beruecksichtigen, die der Benutzer gerade zurueckgenommen hat.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from models import (
    AiActionProposal,
    AiConversation,
    AiMessage,
    AiToolResult,
    Role,
    RolePermission,
    Server,
    User,
)
from services import ai_chat_service
from services.role_service import set_user_roles


def _allow(db: Session, user: User) -> None:
    role = Role(name=f"chat-{user.id}", description=None, is_system=False)
    db.add(role)
    db.flush()
    db.add(RolePermission(role_id=role.id, permission_key="ai.chat.use"))
    db.commit()
    set_user_roles(db, user, [role.id])
    db.commit()


def _conversation(db: Session, user: User) -> AiConversation:
    conversation = ai_chat_service.get_or_create_primary_conversation(db, user)
    db.commit()
    return conversation


def _message(
    db: Session, conversation: AiConversation, role: str, content: str, minute: int
) -> AiMessage:
    row = AiMessage(
        id=str(uuid4()), conversation_id=conversation.id, role=role, content=content,
        status="complete",
        created_at=datetime(2026, 8, 9, 12, minute, tzinfo=timezone.utc),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _csrf(cookies: dict) -> dict[str, str]:
    return {"X-CSRF-Token": cookies.get("__Secure-csrf_token", "")}


# ── Der Schnitt ───────────────────────────────────────────────────────


def test_editing_removes_the_message_and_everything_after(
    db: Session, regular_user: User
) -> None:
    """Nicht nur die eine Antwort — alles Spaetere.

    Bearbeitet jemand die dritte von sechs Nachrichten, beruhen die
    Nachrichten vier bis sechs auf einer Praemisse, die es nicht mehr gibt.
    """
    _allow(db, regular_user)
    conversation = _conversation(db, regular_user)
    _message(db, conversation, "user", "erste Frage", 1)
    _message(db, conversation, "assistant", "erste Antwort", 2)
    ziel = _message(db, conversation, "user", "zweite Frage", 3)
    _message(db, conversation, "assistant", "zweite Antwort", 4)
    _message(db, conversation, "user", "dritte Frage", 5)
    _message(db, conversation, "assistant", "dritte Antwort", 6)

    removed = ai_chat_service.truncate_from(db, conversation, ziel)
    db.commit()

    rest = [
        row.content for row in
        db.query(AiMessage)
        .filter(AiMessage.conversation_id == conversation.id)
        .order_by(AiMessage.created_at)
        .all()
    ]
    assert rest == ["erste Frage", "erste Antwort"]
    assert removed == 4


def test_tool_results_of_the_removed_turns_go_too(
    db: Session, regular_user: User
) -> None:
    """Sonst floessen gelesene Daten in einen Kontext, in dem niemand danach fragte."""
    _allow(db, regular_user)
    conversation = _conversation(db, regular_user)
    _message(db, conversation, "user", "alte Frage", 1)
    db.add(AiToolResult(
        id=str(uuid4()), conversation_id=conversation.id, tool_name="read_server_logs",
        result_json='{"alt": true}',
        created_at=datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc),
    ))
    ziel = _message(db, conversation, "user", "neue Frage", 3)
    db.add(AiToolResult(
        id=str(uuid4()), conversation_id=conversation.id, tool_name="read_server_status",
        result_json='{"neu": true}',
        created_at=datetime(2026, 8, 9, 12, 4, tzinfo=timezone.utc),
    ))
    db.commit()

    ai_chat_service.truncate_from(db, conversation, ziel)
    db.commit()

    verbleibend = [
        row.tool_name for row in
        db.query(AiToolResult).filter(AiToolResult.conversation_id == conversation.id).all()
    ]
    assert verbleibend == ["read_server_logs"]


def test_an_executed_action_survives_the_edit(db: Session, regular_user: User) -> None:
    """Ein gestoppter Server bleibt gestoppt.

    Den Verlauf umzuschreiben aendert nichts an der Welt. Eine ausgefuehrte
    Aktion aus dem Chat verschwinden zu lassen waere deshalb eine Luege — sie
    bleibt, und im Audit-Log steht sie ohnehin unveraendert.

    Ein noch offener Vorschlag geht dagegen mit: eine Rueckfrage zu einer
    zurueckgenommenen Bitte ist gegenstandslos.
    """
    _allow(db, regular_user)
    conversation = _conversation(db, regular_user)
    server = Server(
        name="edit-test", game_type="dayz", install_dir="/tmp/edit",
        status="stopped", container_name="msm-edit",
    )
    db.add(server)
    db.commit()
    db.refresh(server)

    ziel = _message(db, conversation, "user", "stoppe den Server", 3)
    spaeter = datetime(2026, 8, 9, 12, 4, tzinfo=timezone.utc)
    db.add(AiActionProposal(
        id=str(uuid4()), conversation_id=conversation.id, user_id=regular_user.id,
        server_id=server.id, tool_name="propose_server_lifecycle",
        payload_encrypted="x", preview_json="{}", status="succeeded",
        correlation_id=str(uuid4()), created_at=spaeter,
    ))
    db.add(AiActionProposal(
        id=str(uuid4()), conversation_id=conversation.id, user_id=regular_user.id,
        server_id=server.id, tool_name="propose_backup",
        payload_encrypted="x", preview_json="{}", status="proposed",
        correlation_id=str(uuid4()), created_at=spaeter,
    ))
    db.commit()

    ai_chat_service.truncate_from(db, conversation, ziel)
    db.commit()

    uebrig = [
        row.status for row in
        db.query(AiActionProposal)
        .filter(AiActionProposal.conversation_id == conversation.id)
        .all()
    ]
    assert uebrig == ["succeeded"]


# ── Was nicht bearbeitet werden darf ──────────────────────────────────


def test_an_assistant_message_cannot_be_edited(db: Session, regular_user: User) -> None:
    """Eine Modellantwort umzuschreiben waere keine Korrektur, sondern Faelschung.

    Sie ist Teil des Kontexts, aus dem spaetere Antworten entstehen — wer sie
    aendert, aendert rueckwirkend die Grundlage von allem Folgenden.
    """
    _allow(db, regular_user)
    conversation = _conversation(db, regular_user)
    antwort = _message(db, conversation, "assistant", "meine Antwort", 2)

    with pytest.raises(Exception) as exc:
        ai_chat_service.owned_message(db, conversation, antwort.id)
    assert getattr(exc.value, "status_code", None) == 409


def test_a_foreign_message_is_not_found(db: Session, regular_user: User) -> None:
    """Die Nachricht eines anderen Benutzers gibt es fuer diesen Chat nicht."""
    from services.auth_service import AuthService

    other = AuthService.create_user(db, "fremder", "fremder@test.de", "EditPass123!")
    other.email_verified = True
    db.commit()
    db.refresh(other)
    _allow(db, regular_user)

    fremde_unterhaltung = ai_chat_service.get_or_create_primary_conversation(db, other)
    db.commit()
    fremde_nachricht = _message(db, fremde_unterhaltung, "user", "geheim", 1)

    meine = _conversation(db, regular_user)
    with pytest.raises(Exception) as exc:
        ai_chat_service.owned_message(db, meine, fremde_nachricht.id)
    assert getattr(exc.value, "status_code", None) == 404


# ── Ueber die API ─────────────────────────────────────────────────────


def test_the_endpoint_reports_how_much_it_removed(
    client, db: Session, regular_user: User, user_cookies: dict
) -> None:
    _allow(db, regular_user)
    conversation = _conversation(db, regular_user)
    _message(db, conversation, "user", "behalten", 1)
    ziel = _message(db, conversation, "user", "zu bearbeiten", 3)
    _message(db, conversation, "assistant", "darauf geantwortet", 4)

    response = client.put(
        f"/api/ai/conversation/messages/{ziel.id}",
        json={"content": "so war es gemeint"},
        cookies=user_cookies,
        headers=_csrf(user_cookies),
    )

    assert response.status_code == 200
    assert response.json() == {"removed": 2}
    assert db.query(AiMessage).filter(
        AiMessage.conversation_id == conversation.id
    ).count() == 1


def test_an_empty_edit_changes_nothing(
    client, db: Session, regular_user: User, user_cookies: dict
) -> None:
    """Erst pruefen, dann abschneiden.

    Eine Bearbeitung abzulehnen, *nachdem* der halbe Verlauf weg ist, waere die
    schlechtere Reihenfolge — der Benutzer haette dann beides verloren.
    """
    _allow(db, regular_user)
    conversation = _conversation(db, regular_user)
    ziel = _message(db, conversation, "user", "bleibt stehen", 1)

    response = client.put(
        f"/api/ai/conversation/messages/{ziel.id}",
        json={"content": "   "},
        cookies=user_cookies,
        headers=_csrf(user_cookies),
    )

    assert response.status_code == 422
    assert db.query(AiMessage).filter(
        AiMessage.conversation_id == conversation.id
    ).count() == 1
