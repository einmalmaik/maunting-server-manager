"""Das Kontextbudget darf die gerade gestellte Frage nicht verdraengen.

Zwei Zusagen, die vorher beide gebrochen waren: ein grosser Anhang kostet die
Historie nicht ihren Platz, und ein einzelner grosser Werkzeugauszug kostet die
kleineren nicht ihren Rueckfluss.
"""

import os
import struct
import zlib
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import AiConversation, AiMessage, AiToolResult, Role, RolePermission, User
from services.ai_context_service import (
    MAX_TOOL_RESULT_CONTEXT_CHARS,
    _recent_tool_results,
    auf_budget_kuerzen,
    build_provider_messages,
    message_character_count,
)
from services.role_service import set_user_roles


def _enable_attachments(db: Session, user: User) -> None:
    role = Role(name=f"budget-{user.id}", is_system=False)
    db.add(role)
    db.flush()
    db.add_all([
        RolePermission(role_id=role.id, permission_key="ai.chat.use"),
        RolePermission(role_id=role.id, permission_key="ai.attachments.use"),
    ])
    db.commit()
    set_user_roles(db, user, [role.id])


def _conversation(db: Session, user: User) -> AiConversation:
    row = AiConversation(id=str(uuid4()), user_id=user.id, title="Budget")
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _png(width: int, height: int) -> bytes:
    """Ein gueltiges, absichtlich unkomprimierbares PNG.

    Zufallspixel, damit die Datei so gross wird wie ein echter Screenshot —
    genau daran haengt der Fehler, den dieser Test faengt.
    """
    roh = b"".join(b"\x00" + os.urandom(width * 3) for _ in range(height))

    def chunk(typ: bytes, daten: bytes) -> bytes:
        pruef = zlib.crc32(typ + daten) & 0xFFFFFFFF
        return struct.pack(">I", len(daten)) + typ + daten + struct.pack(">I", pruef)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(roh, 0))
        + chunk(b"IEND", b"")
    )


def test_an_image_attachment_does_not_evict_the_question_it_belongs_to(
    client: TestClient,
    db: Session,
    regular_user: User,
    user_cookies: dict,
) -> None:
    """Ein Screenshot darf die Frage nicht aus dem Kontext werfen.

    Vorher zaehlte ein 30-KB-PNG mit rund 40.000 Zeichen gegen dasselbe Budget
    wie Text (`MAX_CONTEXT_CHARS` = 24.000). Damit war `remaining` schon vor der
    ersten Zeile negativ, und weil die Historie absteigend sortiert ist, war die
    erste Zeile die soeben gestellte Frage: der Anbieter sah ein Bild ohne
    Frage und ohne einen Satz Verlauf.
    """
    from services.ai_attachment_service import bind_to_message

    _enable_attachments(db, regular_user)
    conversation = _conversation(db, regular_user)
    hochgeladen = client.post(
        "/api/ai/conversation/attachments",
        files={"file": ("screenshot.png", _png(100, 100), "image/png")},
        cookies=user_cookies,
        headers={"X-CSRF-Token": user_cookies.get("__Secure-csrf_token", "")},
    )
    assert hochgeladen.status_code == 201

    frage = AiMessage(
        id=str(uuid4()), conversation_id=conversation.id, role="user",
        content="Warum stuerzt der Server ab?", status="complete",
    )
    db.add(frage)
    db.flush()
    bind_to_message(
        db, conversation_id=conversation.id, user_id=regular_user.id, message_id=frage.id
    )
    db.commit()

    messages = build_provider_messages(db, conversation, "Warum stuerzt der Server ab?")

    assert any(
        isinstance(item.get("content"), str)
        and "Warum stuerzt der Server ab?" in item["content"]
        for item in messages
    ), "Die gerade gestellte Frage fehlt im Providerkontext"


def test_one_huge_tool_result_does_not_suppress_the_smaller_ones(
    db: Session,
    regular_user: User,
) -> None:
    """Ein gelesener Log verdraengt die uebrigen Werkzeugergebnisse nicht.

    `read_server_logs` liefert bis zu 24.000 Zeichen, dreimal so viel wie das
    Rueckflussbudget. Vorher lief die Schleife vom aeltesten Eintrag her und
    brach beim ersten zu grossen `break` ab — der alte Log nahm damit alle
    juengeren, winzigen Ergebnisse mit ins Nichts.
    """
    conversation = _conversation(db, regular_user)
    for name, wert in [
        ("read_server_logs", "L" * 20_000),
        ("read_config", "server-port=25565"),
        ("list_servers", "Survival, Creative"),
    ]:
        db.add(AiToolResult(
            id=str(uuid4()), conversation_id=conversation.id,
            tool_name=name, result_json=wert,
        ))
        db.commit()

    block = _recent_tool_results(db, conversation.id)

    assert block is not None
    assert "server-port=25565" in block
    assert "Survival, Creative" in block
    # Der grosse Auszug geht nicht verloren, er wird nur gekuerzt — das Modell
    # soll erkennen, dass da mehr war.
    assert "read_server_logs" in block
    assert len(block) <= MAX_TOOL_RESULT_CONTEXT_CHARS + 200


def test_a_large_window_lets_more_than_twenty_messages_through(
    db: Session, regular_user: User
) -> None:
    """Der feste Deckel von 20 Nachrichten war die eigentliche Fessel.

    Selbst mit einem Million-Token-Modell gingen nie mehr als zwanzig
    Nachrichten hinaus — das Zeichenbudget kam gar nicht erst zum Zug. Der Chat
    vergass also bei rund einem Prozent Auslastung.
    """
    conversation = _conversation(db, regular_user)
    start = datetime.now(timezone.utc) - timedelta(hours=2)
    for index in range(60):
        db.add(AiMessage(
            id=str(uuid4()), conversation_id=conversation.id,
            role="user" if index % 2 == 0 else "assistant",
            content=f"Nachricht {index} " + "x" * 300,
            status="complete",
            created_at=start + timedelta(minutes=index),
        ))
    db.commit()

    eng = build_provider_messages(db, conversation)
    weit = build_provider_messages(db, conversation, context_chars=400_000)

    def enthaelt(messages: list[dict], text: str) -> bool:
        return any(
            isinstance(item.get("content"), str) and text in item["content"]
            for item in messages
        )

    assert enthaelt(eng, "Nachricht 59 ")
    assert not enthaelt(eng, "Nachricht 0 ")
    assert enthaelt(weit, "Nachricht 0 ")


def test_a_grown_run_is_trimmed_without_orphaning_a_tool_call(
    db: Session, regular_user: User
) -> None:
    """Gekuerzt wird der Inhalt, nie die Nachricht.

    Die Werkzeugschleife haengt waehrend eines Laufs weiter an — ein Lauf, der
    ins Fenster passte, kann so mitten in der Arbeit darueber hinauswachsen.
    Eine geloeschte Werkzeugantwort liesse jedoch ihren `tool_call`
    unbeantwortet, und das lehnen OpenAI-kompatible Anbieter rundheraus ab.
    """
    del db, regular_user
    messages = [
        {"role": "system", "content": "Systemprompt"},
        {"role": "user", "content": "Warum stuerzt er ab?"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1"}]},
        {"role": "tool", "tool_call_id": "c1", "content": "L" * 30_000},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c2"}]},
        {"role": "tool", "tool_call_id": "c2", "content": "M" * 30_000},
        {"role": "user", "content": "Und jetzt?"},
    ]

    gekuerzt = auf_budget_kuerzen(messages, 20_000)

    assert len(gekuerzt) == len(messages)
    aufrufe = {"c1", "c2"}
    antworten = {item["tool_call_id"] for item in gekuerzt if item["role"] == "tool"}
    assert antworten == aufrufe
    assert message_character_count(gekuerzt) <= 20_000
    # Die letzte Nachricht ist die, auf die geantwortet werden soll — sie bleibt.
    assert gekuerzt[-1]["content"] == "Und jetzt?"
    # Und der Systemprompt wird nicht angetastet.
    assert gekuerzt[0]["content"] == "Systemprompt"


def test_trimming_spends_the_tool_output_before_the_conversation(
    db: Session, regular_user: User
) -> None:
    """Ein Logausschnitt ist ersetzbar, eine Frage nicht."""
    del db, regular_user
    messages = [
        {"role": "user", "content": "Die erste Frage " + "f" * 5_000},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1"}]},
        {"role": "tool", "tool_call_id": "c1", "content": "L" * 20_000},
        {"role": "user", "content": "Und jetzt?"},
    ]

    gekuerzt = auf_budget_kuerzen(messages, 8_000)

    # Das Werkzeugergebnis hat den ganzen Ueberhang getragen; die Frage steht
    # noch vollstaendig da.
    assert gekuerzt[0]["content"] == messages[0]["content"]
    assert len(gekuerzt[2]["content"]) < len(messages[2]["content"]) // 4
    assert message_character_count(gekuerzt) <= 8_000
