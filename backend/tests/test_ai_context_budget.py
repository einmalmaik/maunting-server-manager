"""Das Kontextbudget darf die gerade gestellte Frage nicht verdraengen.

Zwei Zusagen, die vorher beide gebrochen waren: ein grosser Anhang kostet die
Historie nicht ihren Platz, und ein einzelner grosser Werkzeugauszug kostet die
kleineren nicht ihren Rueckfluss.

Dazu die Grenze des Rueckflusses selbst: er endet am Lauf. Ein Chat laeuft in
MSM dauerhaft und wechselt dabei das Thema — ohne diese Grenze stand der
gelesene Log von Server A noch vor dem Modell, wenn laengst nach Server B
gefragt wurde.
"""

import json
import os
import struct
import zlib
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import (
    AiConversation, AiMessage, AiRun, AiToolResult, Role, RolePermission, User,
)
from services import ai_memory_service
from services.ai_context_service import (
    MAX_TOOL_RESULT_CONTEXT_CHARS,
    TOOL_RESULT_TRUNCATION_MARK,
    WERKZEUG_KONTEXT_KOPF,
    _juengste_gespraechszeile,
    _recent_tool_results,
    auf_budget_kuerzen,
    build_provider_messages,
    message_character_count,
    teilbudgets,
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


def _enable_memory(db: Session, user: User) -> None:
    """Recht und Einwilligung — beides braucht es, damit das Memory mitgeht."""
    role = Role(name=f"budget-memory-{user.id}", is_system=False)
    db.add(role)
    db.flush()
    db.add_all([
        RolePermission(role_id=role.id, permission_key="ai.chat.use"),
        RolePermission(role_id=role.id, permission_key="ai.memory.use"),
    ])
    db.commit()
    set_user_roles(db, user, [role.id])
    ai_memory_service.set_preference(db, user, True)


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

    `read_server_logs` liefert bis zu 24.000 Zeichen, doppelt so viel wie das
    Rueckflussbudget dieses Pfades (ohne Katalogwissen 12.000). Vorher lief die
    Schleife vom aeltesten Eintrag her und brach beim ersten zu grossen `break`
    ab — der alte Log nahm damit alle juengeren, winzigen Ergebnisse mit ins
    Nichts.
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


def test_der_rückfluss_wächst_nicht_mit_dem_kontextfenster(
    db: Session,
    regular_user: User,
) -> None:
    """Ein Millionenfenster gibt dem Log der vorigen Runde keinen Zoll mehr.

    Vorher wuchs das Rückflussbudget mit dem Fenster und lag bei luna
    (3.319.200 Zeichen) bei 200.000. Ein einmal gelesener Log ging damit bei
    jeder Folgefrage vollständig neu mit — er steht vor der Frage, wird also
    nicht zwischengespeichert. Nachgerechnet an acht Fragen mit je einem
    24.480-Zeichen-Log: Präfix 70,5 % statt 77,9 %, je Folgefrage 25,0 % mehr
    bezahlte Zeichen.

    Verglichen werden zwei Fenster, in denen der Deckel wirklich bindet. Der
    Rückfall (`teilbudgets(None)`) taugt dafür seit dem 16.000er Deckel nicht
    mehr: bei 24.000 Zeichen bindet dort `gesamt // 2` = 12.000, der Block wäre
    also kleiner statt gleich groß.
    """
    conversation = _conversation(db, regular_user)
    db.add(AiToolResult(
        id=str(uuid4()), conversation_id=conversation.id,
        tool_name="read_server_logs", result_json="L" * 24_480,
    ))
    db.commit()

    mittel = _recent_tool_results(db, conversation.id, teilbudgets(64_000))
    weit = _recent_tool_results(db, conversation.id, teilbudgets(3_319_200))

    assert mittel is not None
    assert weit == mittel, "Das große Fenster hat den Rückfluss wachsen lassen"
    assert len(weit) <= len(WERKZEUG_KONTEXT_KOPF) + MAX_TOOL_RESULT_CONTEXT_CHARS
    # Und das Modell sieht, dass es nur einen Ausschnitt vor sich hat. Hier
    # steht die Marke am Blockende, weil es genau eine Zeile gibt; bei mehreren
    # trägt sie die gekürzte Zeile, und die ist nach `reversed()` die älteste.
    assert weit.endswith(TOOL_RESULT_TRUNCATION_MARK)


def test_das_gedächtnis_wächst_mit_dem_kontextfenster() -> None:
    """Der einzige Block, der stehenblieb, obwohl nichts dagegen sprach.

    Werkzeugdaten, Zusammenfassung und Historie wachsen mit dem Fenster; das
    Gedächtnis rechnete daneben gegen feste 6.000 Zeichen und meldete "weitere
    Einträge wurden aus Platzgründen ausgelassen", während im Fenster daneben
    180.000 Zeichen frei blieben. Anders als beim Werkzeugrückfluss gibt es
    dafür keine Begründung: der Block steht vor der Frage und geht
    zwischengespeichert mit, er kostet Platz und nicht Geld.

    Der Deckel gehört mit dazu — ohne ihn bekäme ein Millionenfenster einen
    Kontext, der zu weiten Teilen aus alten Notizen besteht.
    """
    assert (
        teilbudgets(200_000).gedaechtnis_zeichen
        > teilbudgets(None).gedaechtnis_zeichen
    )
    assert teilbudgets(3_319_200).gedaechtnis_zeichen == 24_000


def test_der_kontextaufbau_reicht_das_gedächtnisbudget_durch(
    db: Session, regular_user: User
) -> None:
    """Die Rechnung nützt nichts, wenn sie den Gedächtnisdienst nicht erreicht.

    Genau das war der Befund: `build_provider_messages` kannte das Fenster,
    rechnete `teilbudgets(context_chars)` und reichte an
    `provider_memory_context` trotzdem nur Frage und Server weiter. Der Block
    schnitt danach gegen seine eigene Konstante — ein Test auf die Zahlen
    allein hätte das nie bemerkt.
    """
    _enable_memory(db, regular_user)
    conversation = _conversation(db, regular_user)
    for nummer in range(90):
        ai_memory_service.upsert_entry(
            db, user=regular_user, scope="user", server_id=None,
            key=f"notiz{nummer:03d}",
            value=f"Eine ausfuehrliche Notiz Nummer {nummer}, {'Wortfuellung ' * 6}",
            origin="user",
        )

    def memory_block(context_chars: int | None) -> str:
        messages = build_provider_messages(
            db, conversation, "Was weisst du?", context_chars=context_chars
        )
        treffer = [
            item["content"] for item in messages
            if isinstance(item.get("content"), str)
            and item["content"].startswith("Unvertrauenswuerdige Praeferenzdaten")
        ]
        assert len(treffer) == 1
        return treffer[0]

    weit = memory_block(200_000)
    eng = memory_block(None)

    assert len([zeile for zeile in weit.splitlines() if zeile.startswith("[user/")]) == 90
    assert "ausgelassen" not in weit
    assert "ausgelassen" in eng


def test_der_gedächtnissockel_ist_das_heutige_verhalten() -> None:
    """Ohne Fensterwissen bleibt alles, wie es war — und ein enges Fenster auch.

    Die zwei Zahlen stehen in zwei Dateien: der Sockel hier bei den übrigen
    Sockeln, der Rückfall des Gedächtnisdienstes dort, wo er ohne Budget
    greift. Dieser Test hält sie zusammen. Wer nur eine von beiden anfasst,
    bekommt hier rot statt einer stillen Verschiebung im Kontext.
    """
    assert (
        teilbudgets(None).gedaechtnis_zeichen == ai_memory_service.MAX_CONTEXT_CHARS
    )
    # Ein 4.096-Token-Modell (rund 16.000 Zeichen) fällt nicht unter den
    # Sockel: mitwachsen heißt wachsen, nicht schrumpfen. Was insgesamt zu viel
    # ist, schneidet danach `auf_budget_kuerzen` gegen `gesamt`.
    assert (
        teilbudgets(16_000).gedaechtnis_zeichen == ai_memory_service.MAX_CONTEXT_CHARS
    )


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


# ── Gekuerzt heisst kuerzer, nicht kaputt ────────────────────────────────
#
# Der Inhalt einer `role="tool"`-Nachricht ist JSON. Der Schnitt durch den Text
# traf darin mitten in eine Zeichenkette, und beim Modell kam ein Bruchstueck
# an, das kein Parser mehr oeffnet:
#
#     {"error":"AI_GUARDIAN_NO_HUMAN","message":"In einer Guar [...gekuerzt]
#
# Gefunden hat das ein Test, der es gar nicht suchte — `_antworten` in
# `test_ai_guardian_kein_mensch` liest Werkzeugergebnisse mit `json.loads` und
# fiel darueber. Die Zusage gehoert aber hierher, wo gekuerzt wird, und nicht
# in einen Test ueber Rueckfragen in der Guardian-Heilung.


def _werkzeugnachricht(nutzlast: dict) -> dict:
    """Ein Werkzeugergebnis so, wie `ai_stream_service` es schreibt.

    Dieselben Schalter an `json.dumps` — ohne sie misst der Test eine andere
    Laenge als die, die im Lauf entsteht.
    """
    return {
        "role": "tool",
        "tool_call_id": "c1",
        "content": json.dumps(nutzlast, ensure_ascii=True, separators=(",", ":")),
    }


def _mit_werkzeugergebnis(nutzlast: dict) -> list[dict]:
    return [
        {"role": "system", "content": "S" * 8_000},
        {"role": "user", "content": "Warum stuerzt er ab?"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1"}]},
        _werkzeugnachricht(nutzlast),
        {"role": "user", "content": "Und jetzt?"},
    ]


def test_ein_gekuerztes_werkzeugergebnis_bleibt_lesbares_json() -> None:
    """Gekuerzt wird die Nutzlast, nicht der Text darum herum.

    Ein Ergebnis, das das Modell nicht lesen kann, ist so gut wie keines — und
    schlechter als eines, das ehrlich sagt, dass es nur ein Ausschnitt ist. Die
    Marke steht deshalb **im** gekuerzten Feld und nicht hinter der
    schliessenden Klammer.
    """
    messages = _mit_werkzeugergebnis(
        {"untrusted": True, "tool": "read_server_logs", "data": "L" * 30_000}
    )

    gekuerzt = auf_budget_kuerzen(messages, 10_000)

    nutzlast = json.loads(gekuerzt[3]["content"])
    assert nutzlast["tool"] == "read_server_logs"
    assert nutzlast["untrusted"] is True
    assert nutzlast["data"].endswith(TOOL_RESULT_TRUNCATION_MARK)
    assert len(nutzlast["data"]) < 30_000
    assert message_character_count(gekuerzt) <= 10_000


def test_der_fehlercode_ueberlebt_die_kuerzung() -> None:
    """Zuerst die kurzen Felder, dann das lange — und darum bleibt `error` stehen.

    Ein Werkzeugergebnis ohne `error` sieht aus wie ein gelungener Aufruf. Diese
    Verwechslung ist teurer als jedes Budget: das Modell meldet dem Betreiber
    eine Heilung, die nie stattfand. Der Fehlercode ist zwanzig Zeichen lang und
    passt unter jedes Budget — er darf sie nicht an das Meldungsfeld verlieren,
    nur weil das zufaellig vor ihm dran waere.
    """
    messages = _mit_werkzeugergebnis(
        {"error": "AI_GUARDIAN_NO_HUMAN", "message": "In einer Heilung " * 2_000}
    )

    gekuerzt = auf_budget_kuerzen(messages, 10_000)

    nutzlast = json.loads(gekuerzt[3]["content"])
    assert nutzlast["error"] == "AI_GUARDIAN_NO_HUMAN"
    assert nutzlast["message"].endswith(TOOL_RESULT_TRUNCATION_MARK)


def test_ein_umlaut_zaehlt_mit_seiner_json_laenge_und_nicht_mit_einer() -> None:
    """Unter ``ensure_ascii`` ist ein ``ö`` sechs Zeichen lang, kein eines.

    Wer den Schnitt aus der Zeichenzahl der Nutzlast rechnet, kuerzt einen
    deutschen Serverlog auf das Sechsfache seines Budgets — die Kuerzung
    laeuft, meldet Vollzug und die Anfrage reisst das Fenster trotzdem. Der
    Schnitt wird deshalb an der **serialisierten** Laenge gesucht.
    """
    messages = _mit_werkzeugergebnis({
        "untrusted": True,
        "tool": "read_server_logs",
        "data": "Fehler beim Öffnen der Datei — Zugriff verweigert. " * 600,
    })

    gekuerzt = auf_budget_kuerzen(messages, 10_000)

    json.loads(gekuerzt[3]["content"])
    assert message_character_count(gekuerzt) <= 10_000


def test_eine_ergebnisliste_bleibt_eine_liste() -> None:
    """Form heisst Form: aus einer Liste wird keine Zeichenkette.

    Sonst muesste das Modell raten, ob es ein Ergebnis liest oder eine Meldung
    darueber. Gekuerzt wird von hinten, weil eine Ergebnisliste ihre
    Reihenfolge meint — der erste Server, die erste Zeile, der erste Vorgang.
    """
    messages = _mit_werkzeugergebnis({
        "tool": "list_servers",
        "outcomes": [{"id": n, "name": f"server-{n}"} for n in range(2_000)],
    })

    gekuerzt = auf_budget_kuerzen(messages, 10_000)

    nutzlast = json.loads(gekuerzt[3]["content"])
    assert isinstance(nutzlast["outcomes"], list)
    assert nutzlast["outcomes"][0] == {"id": 0, "name": "server-0"}
    assert nutzlast["outcomes"][-1] == TOOL_RESULT_TRUNCATION_MARK
    assert len(nutzlast["outcomes"]) < 2_000


def test_ein_werkzeugergebnis_ohne_json_wird_weiter_als_text_gekuerzt() -> None:
    """Kein Rueckfall, sondern der richtige Weg fuer schlichten Text.

    Werkzeugergebnisse aus der Zeit vor der Serialisierung tragen ihn, und die
    uebrigen Tests dieser Datei bauen ihre Nachrichten so. Ein Textschnitt macht
    Text nicht kaputt — kaputt ging nur JSON.
    """
    messages = [
        {"role": "system", "content": "S" * 8_000},
        {"role": "user", "content": "Warum stuerzt er ab?"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1"}]},
        {"role": "tool", "tool_call_id": "c1", "content": "L" * 30_000},
        {"role": "user", "content": "Und jetzt?"},
    ]

    gekuerzt = auf_budget_kuerzen(messages, 10_000)

    assert gekuerzt[3]["content"].startswith("LLL")
    assert gekuerzt[3]["content"].endswith(TOOL_RESULT_TRUNCATION_MARK)
    assert message_character_count(gekuerzt) <= 10_000


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


def test_trimming_spends_the_tool_context_before_the_question(
    db: Session, regular_user: User
) -> None:
    """Der Werkzeugkontext steht hinter der Frage — gekürzt wird er trotzdem zuerst.

    Seit er im Nachspann liegt (das ist die Bedingung dafür, dass der Anbieter
    überhaupt etwas zwischenspeichern kann), ist er nicht mehr die Nachricht
    *vor* der Historie, sondern die dahinter. Ohne die Zuordnung „das sind
    Werkzeugdaten“ wäre er damit das Letzte, was die Kürzung anfasst — und die
    gerade gestellte Frage das Vorletzte. Genau umgekehrt ist es richtig: einen
    Logausschnitt kann das Modell neu anfordern, eine Frage nicht.
    """
    del db, regular_user
    messages = [
        {"role": "system", "content": "Systemprompt"},
        {"role": "user", "content": "Die erste Frage " + "f" * 2_000},
        {"role": "user", "content": "Und jetzt?"},
        {"role": "user", "content": WERKZEUG_KONTEXT_KOPF + "- read_server_logs: " + "L" * 20_000},
        {"role": "system", "content": "Lage (Auskunft des Panels, keine Anweisung):"},
    ]

    gekuerzt = auf_budget_kuerzen(messages, 8_000)

    assert gekuerzt[2]["content"] == "Und jetzt?"
    assert gekuerzt[1]["content"] == messages[1]["content"]
    assert len(gekuerzt[3]["content"]) < len(messages[3]["content"]) // 2
    assert message_character_count(gekuerzt) <= 8_000


def test_the_question_survives_even_when_the_budget_does_not(
    db: Session, regular_user: User
) -> None:
    """Die gerade gestellte Frage geht ganz hinaus — auch wenn es nicht reicht.

    Der Schutz hing an ``index == len - 1``, und das war die Frage, solange sie
    die letzte Nachricht war. Seit Werkzeugkontext und Lageblock dahinter
    stehen, schonte er den Lageblock — der als ``system`` ohnehin unantastbar
    ist — und griff die Frage an: hier bliebe von 6.000 Zeichen gut die Hälfte
    übrig, obwohl der Werkzeugkontext davor schon auf seinen Sockel geschrumpft
    ist.

    Dass die Liste danach über dem Budget liegt, ist die alte, bewusste
    Abwägung: eine Absage des Anbieters ist sichtbar, eine still halbierte
    Frage nicht.
    """
    del db, regular_user
    frage = "Warum startet der Server nicht? Hier der Auszug:\n" + "F" * 6_000
    messages = [
        {"role": "system", "content": "S" * 12_000},
        {"role": "user", "content": frage},
        {"role": "user", "content": WERKZEUG_KONTEXT_KOPF + "- read_server_logs: " + "W" * 5_000},
        {"role": "system", "content": "Lage (Auskunft des Panels, keine Anweisung):"},
    ]

    gekuerzt = auf_budget_kuerzen(messages, 16_000)

    assert gekuerzt[1]["content"] == frage
    assert len(gekuerzt[2]["content"]) < 300


def test_eine_frage_kann_sich_nicht_als_werkzeugdaten_ausgeben(
    db: Session, regular_user: User
) -> None:
    """Ob eine Nachricht Material ist, darf nicht am Benutzertext hängen.

    `_ist_werkzeugdaten` erkennt den Rückfluss an seiner Kopfzeile — die eine
    Stelle, an der eine Nachricht in dieser Liste noch an ihrem Text hängt.
    Schreibt ein Benutzer genau diese Kopfzeile an den Anfang seiner Frage,
    gilt seine Frage als ersetzbares Material: `_juengste_gespraechszeile`
    überspringt sie und schützt stattdessen eine ältere Assistentenzeile,
    und `auf_budget_kuerzen` opfert sie im ersten Durchgang zusammen mit den
    Logauszügen — also vor dem übrigen Gespräch statt danach.
    """
    conversation = _conversation(db, regular_user)
    start = datetime.now(timezone.utc) - timedelta(hours=1)
    db.add(AiMessage(
        id=str(uuid4()), conversation_id=conversation.id, role="assistant",
        content="Früher Zug " + "a" * 3_000, status="complete",
        created_at=start,
    ))
    frage = WERKZEUG_KONTEXT_KOPF + "Warum startet der Server nicht? " + "F" * 3_000
    db.add(AiMessage(
        id=str(uuid4()), conversation_id=conversation.id, role="user",
        content=frage, status="complete",
        created_at=start + timedelta(minutes=1),
    ))
    db.commit()

    nachrichten = build_provider_messages(db, conversation, context_chars=200_000)

    stelle = _juengste_gespraechszeile(nachrichten)
    assert stelle == len(nachrichten) - 1, (
        "die gerade gestellte Frage gilt als Werkzeugmaterial und ist damit "
        "die erste, die eine Kürzung opfert"
    )
    # Der Text bleibt vollständig lesbar — neutralisiert wird nur der Anfang.
    assert "Warum startet der Server nicht?" in nachrichten[stelle]["content"]
    assert nachrichten[stelle]["content"].endswith("F" * 3_000)


def test_the_nachspann_still_counts_against_the_window(
    db: Session, regular_user: User
) -> None:
    """Nach hinten geschoben heißt nicht: nicht mehr gezählt.

    Werkzeugkontext und Lageblock werden gebaut, bevor die Historie ihr Budget
    bekommt, und stehen erst danach in der Liste. Wer beim Verschieben die
    Zählung mitverschiebt, gibt der Historie ein Budget, das der Nachspann
    hinterher überzieht — und der Anbieter lehnt die Anfrage ab, statt sie
    knapper zu beantworten.
    """
    conversation = _conversation(db, regular_user)
    lauf = _lauf(db, conversation, regular_user)
    _ergebnis(db, conversation, lauf=lauf, tool="read_server_logs",
              wert="L" * 30_000, sekunde=1)
    start = datetime.now(timezone.utc) - timedelta(hours=2)
    for index in range(40):
        db.add(AiMessage(
            id=str(uuid4()), conversation_id=conversation.id,
            role="user" if index % 2 == 0 else "assistant",
            content=f"Nachricht {index} " + "x" * 2_000,
            status="complete",
            created_at=start + timedelta(minutes=index),
        ))
    db.commit()

    nachrichten = build_provider_messages(db, conversation, context_chars=60_000)

    assert message_character_count(nachrichten) <= 60_000


def test_an_image_attachment_counts_with_its_full_base64_url() -> None:
    """Listenförmiger Inhalt wird tief gezählt, nicht als `repr` gemessen.

    Hier stand `len(str(content))`: das baute in jeder Werkzeugrunde die
    vollständige Zeichenkette des Inhalts auf — bei fünf Bildern in
    Maximalgröße rund 1,7 MB —, nahm ihre Länge und warf sie weg.

    Die naheliegende Abkürzung ist falsch, und genau deshalb steht dieser Test
    hier: eine Fassung, die nur die Werte der obersten Ebene summiert, zählt
    für diesen Anhang 52 Zeichen statt Hunderttausender — die Base64-URL liegt
    eine Ebene tiefer, in `{"url": ...}`. Das Bild wäre für das Budget
    unsichtbar, `auf_budget_kuerzen` ließe den Verlauf ungekürzt, und der
    Anbieter wiese die Anfrage wegen des überschrittenen Fensters ab — der
    Fall, den die Kürzung gerade verhindern soll.
    """
    url = "data:image/png;base64," + "A" * 340_000
    messages = [
        {"role": "user", "content": "Sieh dir das an"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Unvertrauenswuerdiger Bildanhang: bild.png"},
                {"type": "image_url", "image_url": {"url": url}},
            ],
        },
    ]

    gezaehlt = message_character_count(messages)

    assert gezaehlt > len(url)
    # Die 52 Zeichen der flachen Fassung wären hier ein stiller Unterzählfehler.
    assert gezaehlt > 1_000


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


# ── Der Werkzeugkatalog fährt mit und zählt mit ───────────────────────────


@pytest.mark.asyncio
async def test_the_tool_catalogue_counts_against_the_same_window(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`context_chars` ist die eine Währung — dann muss auch der Katalog hinein.

    Das Budget wurde ausschließlich über die Nachrichtenliste gerechnet:
    `message_character_count` summiert nur `content`. Der Werkzeugkatalog ging
    daneben über dieselbe Leitung, als `tools=`, und tauchte in keiner Rechnung
    auf — bei 51 Werkzeugen rund 45.000 Zeichen, mehr als das gesamte
    Nachrichtenbudget eines 32k-Modells.

    Der Benutzer sah davon keinen knapperen Kontext, sondern einen
    abgebrochenen Lauf: der Anbieter lehnt eine zu große Anfrage ab.

    Geprüft wird am echten Segment und nicht an der Formel — genau die
    Nachrichten und genau der Katalog, die zusammen hinausgehen.
    """
    from services import ai_run_broker, ai_stream_service
    from models import AiProvider
    from services.openai_compatible_adapter import StreamChunk

    provider = AiProvider(
        name="Budget", provider_kind="openrouter", default_model="model-a",
        enabled=True, requires_api_key=False,
    )
    db.add(provider)
    db.commit()
    db.refresh(provider)

    conversation = _conversation(db, regular_user)
    start = datetime.now(timezone.utc) - timedelta(hours=2)
    # Reichlich mehr Verlauf, als in das Fenster passt: nur dann schöpft die
    # Kürzung das Budget wirklich aus, und nur dann ist die Frage überhaupt
    # gestellt. Bei knapper Historie bliebe der Test grün, ohne etwas zu wissen.
    for index in range(100):
        db.add(AiMessage(
            id=str(uuid4()), conversation_id=conversation.id,
            role="user" if index % 2 == 0 else "assistant",
            content=f"Nachricht {index} " + "x" * 2_000,
            status="complete",
            created_at=start + timedelta(minutes=index),
        ))
    db.commit()

    # Ein bekannt kleines Fenster. Groß genug, dass der Katalog hineinpasst,
    # klein genug, dass die Historie ohne die Behebung darüber hinausläuft.
    fenster = 120_000
    gesehen: dict = {}

    async def fake(_client, *, provider, api_key, messages, usage, tools=None,
                   tool_choice=None, reasoning=False, reasoning_effort=None,
                   cache_marke=False, model=None):
        del provider, api_key, tool_choice, reasoning, reasoning_effort, cache_marke
        gesehen["messages"] = [dict(item) for item in messages]
        gesehen["tools"] = tools
        usage.total_tokens = 10
        yield StreamChunk("content", "ok")

    monkeypatch.setattr(ai_stream_service, "stream_chat_completion", fake)

    run, fehler = ai_stream_service.lauf_beginnen(
        db, user=regular_user, conversation=conversation, provider=provider,
        request_id=uuid4(), content="Und jetzt?", reasoning=False,
        context_chars=fenster,
    )
    assert run is not None, f"Lauf konnte nicht beginnen: {fehler}"
    ai_run_broker.eroeffnen(run.id)
    await ai_stream_service.segment_ausfuehren(run.id, client=object())

    assert gesehen.get("tools"), "Es ging kein Werkzeugkatalog hinaus"
    katalog = len(json.dumps(gesehen["tools"], ensure_ascii=False))
    nachrichten = message_character_count(gesehen["messages"])

    assert katalog > 0
    assert nachrichten + katalog <= fenster, (
        f"Nachrichten ({nachrichten}) und Werkzeugkatalog ({katalog}) sind zusammen "
        f"{nachrichten + katalog} Zeichen und sprengen das Fenster von {fenster} — "
        "der Anbieter lehnt die Anfrage ab"
    )
    assert gesehen.get("messages"), "Es gingen keine Nachrichten hinaus"


def test_message_content_includes_static_timestamp_prefix(db: Session, regular_user: User) -> None:
    """Nachrichten im Verlauf tragen einen statischen Zeitstempel-Praefix."""
    conversation = AiConversation(
        id=str(uuid4()), user_id=regular_user.id, kind="primary", title="Chat"
    )
    db.add(conversation)
    db.commit()

    dt = datetime(2026, 8, 20, 14, 30, 0, tzinfo=timezone.utc)
    msg = AiMessage(
        id=str(uuid4()),
        conversation_id=conversation.id,
        role="user",
        content="Mein ARK Server laeuft nicht.",
        status="complete",
        created_at=dt,
    )
    db.add(msg)
    db.commit()

    nachrichten = build_provider_messages(db, conversation)
    user_msgs = [m for m in nachrichten if m.get("role") == "user" and "Mein ARK Server" in m.get("content", "")]
    assert len(user_msgs) == 1
    assert user_msgs[0]["content"].startswith("[20.08. 14:30] ")
    assert "Mein ARK Server laeuft nicht." in user_msgs[0]["content"]


def test_interne_zeilen_tragen_keinen_zeitstempel(db: Session, regular_user: User) -> None:
    """Maschinerie-Zeilen (`intern=True`) gehen ohne Zeitstempel-Praefix hinaus.

    Der Praefix begruendet sich mit der Oberflaeche — und die zeigt interne
    Zeilen nie an. Schlimmer: der Lieferauftrag der Meldestelle ist die
    **letzte** Nachricht vor der Antwort, und ein Zeitstempel unmittelbar an
    der Melde-Anweisung ist genau das Material, das das Modell beim Liefern
    nachplappert („am 22.08. um 14:30 fertig geworden"). Die Uhr steht im
    Lageblock; hier hat sie nichts verloren.
    """
    conversation = AiConversation(
        id=str(uuid4()), user_id=regular_user.id, kind="primary", title="Chat"
    )
    db.add(conversation)
    db.commit()

    dt = datetime(2026, 8, 22, 14, 30, 0, tzinfo=timezone.utc)
    msg = AiMessage(
        id=str(uuid4()),
        conversation_id=conversation.id,
        role="user",
        content="Meldung des Panels: der Auftrag ist fertig. Liefere die Ergebnisse.",
        status="complete",
        intern=True,
        created_at=dt,
    )
    db.add(msg)
    db.commit()

    nachrichten = build_provider_messages(db, conversation)
    treffer = [m for m in nachrichten if "Meldung des Panels" in m.get("content", "")]
    assert len(treffer) == 1
    assert not treffer[0]["content"].startswith("[")


def test_eigene_antworten_tragen_keinen_zeitstempel(
    db: Session, regular_user: User
) -> None:
    """Das Modell darf seine eigene Vorlage nicht vor sich sehen.

    Der Anlass ist der dritte Anlauf gegen dasselbe Verhalten: der Betreiber
    las am 22.08.2026 „[22.08. 20:30] Auf deinen Windows-Rechner kann ich
    nicht zugreifen." — das Modell hatte den Praefix selbst geschrieben. Die
    Anlaeufe davor waren Sprache gegen Mechanik (eine Regel im Prompt, dann
    der Verzicht bei internen Zeilen); die **Vorlage** stand weiterhin an
    jeder einzelnen seiner eigenen Verlaufszeilen. Was ein Prompt einmal
    verbietet, demonstriert der Verlauf zwanzigmal.

    Die Benutzerzeile daneben behaelt ihren Praefix — sie ist der Grund, aus
    dem es ihn gibt (Zeitabstand zwischen den Nachrichten), und das Modell
    schreibt keine Benutzerzeilen.
    """
    conversation = AiConversation(
        id=str(uuid4()), user_id=regular_user.id, kind="primary", title="Chat"
    )
    db.add(conversation)
    db.commit()

    dt = datetime(2026, 8, 22, 20, 30, 0, tzinfo=timezone.utc)
    db.add(AiMessage(
        id=str(uuid4()),
        conversation_id=conversation.id,
        role="user",
        content="Wie voll ist meine C-Platte?",
        status="complete",
        created_at=dt,
    ))
    db.add(AiMessage(
        id=str(uuid4()),
        conversation_id=conversation.id,
        role="assistant",
        content="Die Platte ist zu 82 Prozent belegt.",
        status="complete",
        created_at=dt,
    ))
    db.commit()

    nachrichten = build_provider_messages(db, conversation)
    antwort = [
        m for m in nachrichten
        if m.get("role") == "assistant" and "82 Prozent" in (m.get("content") or "")
    ]
    frage = [
        m for m in nachrichten
        if m.get("role") == "user" and "C-Platte" in (m.get("content") or "")
    ]
    assert len(antwort) == 1 and len(frage) == 1
    assert not antwort[0]["content"].startswith("[")
    assert frage[0]["content"].startswith("[22.08. 20:30] ")


def test_ein_selbst_geschriebener_zeitstempel_wird_abgestreift(
    db: Session, regular_user: User
) -> None:
    """Der Bestand ist schon verseucht — nur nicht mehr hinzufuegen genuegt nicht.

    `_finalize_stream` speichert die Antwort ungefiltert. Was das Modell
    bisher nachgeahmt hat, steht damit **im Text** der Zeile und bliebe als
    Vorlage stehen, bis sie aus dem Kontextfenster faellt. Ein laufendes
    Gespraech wuerde den Fehler also weiter demonstrieren, obwohl er behoben
    ist.
    """
    conversation = AiConversation(
        id=str(uuid4()), user_id=regular_user.id, kind="primary", title="Chat"
    )
    db.add(conversation)
    db.commit()

    db.add(AiMessage(
        id=str(uuid4()),
        conversation_id=conversation.id,
        role="assistant",
        content="[22.08. 20:30] Auf deinen Windows-Rechner kann ich nicht zugreifen.",
        status="complete",
        created_at=datetime(2026, 8, 22, 20, 30, 0, tzinfo=timezone.utc),
    ))
    db.commit()

    nachrichten = build_provider_messages(db, conversation)
    treffer = [
        m for m in nachrichten
        if m.get("role") == "assistant" and "Windows-Rechner" in (m.get("content") or "")
    ]
    assert len(treffer) == 1
    assert treffer[0]["content"] == (
        "Auf deinen Windows-Rechner kann ich nicht zugreifen."
    )

