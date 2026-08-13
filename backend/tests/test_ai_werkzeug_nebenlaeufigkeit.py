"""Werkzeuge laufen nebeneinander — und **neben** der Ereignisschleife.

Diese Datei haelt zwei Zusagen fest, die der Benchmark nicht pruefen kann.

Die erste ist die wichtigere: waehrend Werkzeuge laufen, bleibt der Prozess
ansprechbar. Gemessen wurde das Gegenteil — neun Aufrufe zu drei Sekunden
legten das Panel siebenundzwanzig Sekunden lahm, und in dieser Zeit bekam
**kein** Benutzer eine Antwort auf **irgendeine** Anfrage. Der Benchmark sieht
das inzwischen (`MSM_BENCH_TOOL_DELAY`), aber er kostet Tokens und Netz; die
Zusage gehoert in die normale Suite.

Die zweite kann der Benchmark grundsaetzlich nicht sehen. Die Nebenlaeufigkeit
haengt an der Datenbank: auf PostgreSQL — der einzigen unterstuetzten
Betriebsdatenbank — hat jeder Aufruf seine eigene Verbindung und es laufen acht
gleichzeitig; auf SQLite teilen sich alle Sitzungen **eine** Verbindung, und
dort laeuft einer nach dem anderen. Die Testsuite ist SQLite. Ohne diese Datei
waere die Gleichzeitigkeit also eine Behauptung, die nirgends nachgesehen wird —
und die erste Aenderung, die `gather` gegen eine Schleife tauscht, faellt
niemandem auf.
"""

from __future__ import annotations

import asyncio
import time
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from models import AiConversation, User
from services import ai_stream_service
from services.openai_compatible_adapter import ProviderToolCall


#: Lang genug, dass der Unterschied zwischen "nacheinander" und "nebeneinander"
#: nicht im Rauschen verschwindet, kurz genug, dass die Suite nicht wartet.
WERKZEUGDAUER = 0.2
AUFRUFE = 4


def _unterhaltung(db: Session, user: User) -> AiConversation:
    conversation = AiConversation(
        id=str(uuid4()), user_id=user.id, server_id=None, title="Nebenlaeufig"
    )
    db.add(conversation)
    db.commit()
    return conversation


def _aufrufe(anzahl: int) -> list[ProviderToolCall]:
    return [
        ProviderToolCall(id=f"c{nummer}", name="list_my_servers", arguments={})
        for nummer in range(anzahl)
    ]


def _langsames_werkzeug(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ersetzt die Ausfuehrung durch einen synchronen Schlaf.

    Ersetzt wird `_werkzeug_ausfuehren` und nicht `execute_read_tool`: die
    Funktion besitzt die Datenbanksitzung, und genau die soll hier nicht
    mitspielen. Gemessen wird die **Ablaufsteuerung** — ob die Aufrufe
    nebeneinander geplant und in Threads gegeben werden. Ob eine Sitzung je
    Aufruf richtig ist, beantworten die uebrigen 1400 KI-Tests, die alle durch
    diesen Pfad gehen.

    `time.sleep` und nicht `asyncio.sleep`, weil das der Punkt ist: ein echter
    Aufruf an einen Node blockiert seinen Thread. Ein `asyncio.sleep` waere
    hoeflich und wuerde die Frage nicht stellen.
    """
    def _schlafen(_user_id: int, call: ProviderToolCall):
        time.sleep(WERKZEUGDAUER)
        return {"servers": [], "tool": call.name}, None

    monkeypatch.setattr(ai_stream_service, "_werkzeug_ausfuehren", _schlafen)


@pytest.mark.asyncio
async def test_die_ereignisschleife_bleibt_waehrend_der_werkzeuge_frei(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Das Panel darf nicht stehen, waehrend die KI arbeitet.

    Der gemeldete Vorfall in einem Satz: "ich habe von jedem Server ein Backup
    erstellen lassen, danach hat die Seite nicht mehr geladen, erst nachdem der
    Auftrag fertig war."

    Der Waechter zaehlt, wie oft eine gewoehnliche Koroutine waehrenddessen
    drankommt. Blockierte die Schleife, waere die Antwort null — und genau null
    war sie, solange `_tool_followup_messages` synchron aus dem Lauf gerufen
    wurde.
    """
    _langsames_werkzeug(monkeypatch)
    # Eins, damit die Aufrufe **nacheinander** laufen: der schlechteste Fall
    # fuer diese Frage. Selbst dann muss die Schleife frei bleiben, denn genau
    # das ist der Unterschied zwischen `to_thread` und einem direkten Aufruf.
    monkeypatch.setattr(ai_stream_service, "_werkzeug_nebenlaeufigkeit", lambda: 1)
    conversation = _unterhaltung(db, regular_user)

    ticks = 0

    async def _waechter() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.02)
            ticks += 1

    waechter = asyncio.create_task(_waechter())
    try:
        await ai_stream_service._tool_followup_messages(
            user_id=regular_user.id,
            conversation_id=conversation.id,
            tool_calls=_aufrufe(AUFRUFE),
        )
    finally:
        waechter.cancel()

    # Vier Aufrufe zu 0,2 s sind 0,8 s Arbeit; bei 20-ms-Takt waeren das rund
    # vierzig Gelegenheiten. Zehn ist weit genug unter dem Erwartungswert, um
    # auf einem ausgelasteten Rechner nicht falsch anzuschlagen, und weit genug
    # ueber null, um eine blockierte Schleife zu erkennen.
    assert ticks >= 10, (
        f"Die Ereignisschleife kam waehrend der Werkzeuge nur {ticks}-mal dran — "
        "das Panel steht in dieser Zeit"
    )


@pytest.mark.asyncio
async def test_werkzeuge_einer_runde_laufen_nebeneinander(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Vier Aufrufe zu 0,2 s dauern zusammen 0,2 s, nicht 0,8 s.

    Das ist die Zusage, die auf SQLite absichtlich **nicht** gilt und die der
    Benchmark deshalb nie zu sehen bekommt. Hier wird die Breite gesetzt, statt
    sie aus der Datenbank zu lesen — geprueft wird die Ablaufsteuerung, nicht
    die Entscheidung darueber.
    """
    _langsames_werkzeug(monkeypatch)
    monkeypatch.setattr(
        ai_stream_service, "_werkzeug_nebenlaeufigkeit", lambda: AUFRUFE
    )
    conversation = _unterhaltung(db, regular_user)

    beginn = time.perf_counter()
    nachrichten, benutzt, _ = await ai_stream_service._tool_followup_messages(
        user_id=regular_user.id,
        conversation_id=conversation.id,
        tool_calls=_aufrufe(AUFRUFE),
    )
    dauer = time.perf_counter() - beginn

    # Jeder Aufruf hat trotzdem sein eigenes Ergebnis bekommen — Gleichzeitigkeit
    # darf keinen verschlucken. Eine Assistentenzeile plus vier Werkzeugantworten.
    assert len(nachrichten) == AUFRUFE + 1
    assert len(benutzt) == AUFRUFE
    # Die Haelfte der sequenziellen Zeit als Grenze. Nebeneinander sind es rund
    # 0,2 s, nacheinander 0,8 s — dazwischen liegt genug Luft, dass ein langsamer
    # Rechner die Aussage nicht kippt.
    assert dauer < (WERKZEUGDAUER * AUFRUFE) / 2, (
        f"Vier Aufrufe zu {WERKZEUGDAUER}s brauchten {dauer:.2f}s — sie laufen "
        "nacheinander"
    )


def test_auf_sqlite_laeuft_genau_einer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Die Breite haengt an der Datenbank, und das ist kein Detail.

    Auf SQLite teilen sich alle Sitzungen eine Verbindung (`StaticPool` in der
    Testsuite). Zwei Transaktionen gleichzeitig darauf sind keine
    Nebenlaeufigkeit, sondern ein Datenfehler: der Commit der einen schliesst die
    offene Arbeit der anderen mit ab.

    Der Test steht hier, damit niemand die Zahl spaeter "vereinheitlicht". Die
    Testsuite selbst laeuft auf SQLite — eine Aenderung an dieser Stelle waere
    also gerade dort gefaehrlich, wo sie am wenigsten auffiele.
    """
    assert ai_stream_service._werkzeug_nebenlaeufigkeit() == 1
