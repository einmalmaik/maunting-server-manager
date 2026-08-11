"""Das Kontextbudget darf die gerade gestellte Frage nicht verdraengen.

Zwei Zusagen, die vorher beide gebrochen waren: ein grosser Anhang kostet die
Historie nicht ihren Platz, und ein einzelner grosser Werkzeugauszug kostet die
kleineren nicht ihren Rueckfluss.

Dazu die Grenze des Rueckflusses selbst: er endet am Lauf. Ein Chat laeuft in
MSM dauerhaft und wechselt dabei das Thema — ohne diese Grenze stand der
gelesene Log von Server A noch vor dem Modell, wenn laengst nach Server B
gefragt wurde.
"""

import os
import struct
import zlib
from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import (
    AiConversation, AiMessage, AiRun, AiToolResult, Role, RolePermission, User,
)
from services.ai_context_service import (
    MAX_TOOL_RESULT_CONTEXT_CHARS,
    _recent_tool_results,
    build_provider_messages,
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


# ── Der Rueckfluss endet an der Themengrenze ──────────────────────────


def _lauf(db: Session, conversation: AiConversation, user: User) -> AiRun:
    row = AiRun(
        id=str(uuid4()), conversation_id=conversation.id, user_id=user.id, status="completed"
    )
    db.add(row)
    db.commit()
    return row


def _ergebnis(
    db: Session, conversation: AiConversation, *, lauf: AiRun | None,
    tool: str, wert: str, sekunde: int,
) -> None:
    """Ein Werkzeugergebnis mit ausdruecklicher Uhrzeit.

    Die Reihenfolge entscheidet hier ueber das Ergebnis, und mehrere Zeilen im
    selben Commit teilen sich sonst denselben Zeitstempel — der Test waere dann
    von der Einfuegereihenfolge der Datenbank abhaengig statt von der Zeit.
    """
    db.add(AiToolResult(
        id=str(uuid4()), conversation_id=conversation.id,
        run_id=lauf.id if lauf is not None else None,
        tool_name=tool, result_json=wert,
        created_at=datetime(2026, 8, 11, 12, 0, sekunde, tzinfo=timezone.utc),
    ))
    db.commit()


def test_raw_data_of_an_earlier_topic_does_not_come_back(
    db: Session,
    regular_user: User,
) -> None:
    """Der Log von Server A gehoert nicht in die Frage nach Server B.

    Die Unterhaltung laeuft in MSM dauerhaft und behandelt nacheinander
    unabhaengige Themen; ein Lauf ist die Spanne, in der ein Thema gilt. Vorher
    nahm der Rueckfluss die letzten sechs Ergebnisse der **gesamten**
    Unterhaltung — Rohdaten, die zur Frage nicht gehoeren, sind schlimmer als
    keine, weil das Modell sie fuer aktuell haelt.
    """
    conversation = _conversation(db, regular_user)
    alt, neu = _lauf(db, conversation, regular_user), _lauf(db, conversation, regular_user)
    _ergebnis(db, conversation, lauf=alt, tool="read_server_logs",
              wert="Server A: Exit code 137", sekunde=1)
    _ergebnis(db, conversation, lauf=neu, tool="read_config",
              wert="Server B: LootAbundance=100", sekunde=2)

    block = _recent_tool_results(db, conversation.id)

    assert block is not None
    assert "LootAbundance=100" in block
    assert "Exit code 137" not in block


def test_a_continuation_of_the_same_run_keeps_its_data(
    db: Session,
    regular_user: User,
) -> None:
    """Die Grenze liegt am Lauf, nicht an der Nachricht.

    Eine Rueckfrage nach einer Bestaetigung setzt denselben Lauf fort und muss
    dieselben Daten sehen — sonst faengt die KI mitten im Vorgang von vorn an zu
    lesen. Genau deshalb ist der Lauf die Grenze und nicht die einzelne Runde.
    """
    conversation = _conversation(db, regular_user)
    lauf = _lauf(db, conversation, regular_user)
    _ergebnis(db, conversation, lauf=lauf, tool="read_server_logs",
              wert="erste Runde", sekunde=1)
    _ergebnis(db, conversation, lauf=lauf, tool="read_config",
              wert="zweite Runde", sekunde=2)

    block = _recent_tool_results(db, conversation.id)

    assert block is not None
    assert "erste Runde" in block and "zweite Runde" in block


def test_a_read_skill_result_never_flows_back(db: Session, regular_user: User) -> None:
    """Ein Skilltext ist eine Anleitung, keine Messung.

    Er wiederholte sich sonst Zug um Zug und drueckte mit bis zu 12.000 Zeichen
    alles andere aus dem Budget. Genau das war der Motor dafuer, dass ein einmal
    gegriffener Skill jede folgende Antwort faerbte — auch die zu einem voellig
    anderen Thema.
    """
    conversation = _conversation(db, regular_user)
    lauf = _lauf(db, conversation, regular_user)
    _ergebnis(db, conversation, lauf=lauf, tool="read_skill",
              wert="Anleitung zum Startfehler", sekunde=1)
    _ergebnis(db, conversation, lauf=lauf, tool="read_config",
              wert="LootRespawnDays=2", sekunde=2)

    block = _recent_tool_results(db, conversation.id)

    assert block is not None
    assert "LootRespawnDays=2" in block
    assert "Anleitung zum Startfehler" not in block
    assert "read_skill" not in block


def test_rows_from_before_the_column_still_flow_back(
    db: Session,
    regular_user: User,
) -> None:
    """Bestandszeilen tragen `NULL` und bilden einen gemeinsamen Topf.

    Fuer sie bleibt es beim frueheren Verhalten; der Topf laeuft von selbst aus.
    Ohne diese Zusage haette das Update jeden laufenden Chat um seinen
    Werkzeugkontext gebracht.
    """
    conversation = _conversation(db, regular_user)
    _ergebnis(db, conversation, lauf=None, tool="read_config", wert="alt-eins", sekunde=1)
    _ergebnis(db, conversation, lauf=None, tool="read_server_status", wert="alt-zwei", sekunde=2)

    block = _recent_tool_results(db, conversation.id)

    assert block is not None
    assert "alt-eins" in block and "alt-zwei" in block
