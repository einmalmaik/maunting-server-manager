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
import threading
import time
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from models import AiConversation, User
from services import ai_stream_service, node_client
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
    def _schlafen(
        _user_id: int,
        call: ProviderToolCall,
        _herkunft: str = "panel",
        _familie: str | None = None,
    ):
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


@pytest.mark.asyncio
async def test_ein_haengender_aufruf_haelt_die_runde_nicht_fest(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Einer antwortet nie — die anderen drei kommen trotzdem an.

    Ohne `WERKZEUG_ZEITGRENZE` gab es gegen diesen Fall nichts: der Aufruf
    hing, `gather` wartete, und die Antwort stand bis `MAX_STREAM_SECONDS`
    (300 s) im Adapter. Gemessen wurde das nie — im Benchmark ist jedes
    Werkzeug in Millisekunden fertig, weil dort weder Node noch Docker
    antworten müssen. Es ist der Ausreisser, und Ausreisser sieht ein Median
    nicht.

    Geprüft wird gleich dreierlei, weil es dieselbe Sache ist: die Runde endet,
    jeder Aufruf bekommt seine Antwort, und der abgelaufene bekommt die
    **richtige**.
    """
    monkeypatch.setattr(ai_stream_service, "WERKZEUG_ZEITGRENZE", 0.2)
    monkeypatch.setattr(
        ai_stream_service, "_werkzeug_nebenlaeufigkeit", lambda: AUFRUFE
    )
    conversation = _unterhaltung(db, regular_user)

    # Ein Ereignis statt eines langen Schlafs: der Thread lässt sich nicht
    # abbrechen — das ist ja der Punkt —, also muss der Test ihn selbst
    # freilassen. Sonst hängt am Ende der Suite ein Threadpool, den
    # `concurrent.futures` beim Beenden brav abwartet.
    freigabe = threading.Event()

    def _einer_haengt(
        _user_id: int,
        call: ProviderToolCall,
        _herkunft: str = "panel",
        _familie: str | None = None,
    ):
        if call.id == "c0":
            freigabe.wait(timeout=5)
        return {"servers": [], "tool": call.name}, None

    monkeypatch.setattr(ai_stream_service, "_werkzeug_ausfuehren", _einer_haengt)

    try:
        beginn = time.perf_counter()
        nachrichten, benutzt, _ = await ai_stream_service._tool_followup_messages(
            user_id=regular_user.id,
            conversation_id=conversation.id,
            tool_calls=_aufrufe(AUFRUFE),
        )
        dauer = time.perf_counter() - beginn
    finally:
        freigabe.set()

    assert dauer < 1.0, (
        f"Die Runde brauchte {dauer:.2f}s — ein einzelner haengender Aufruf "
        "haelt weiterhin die ganze Antwort fest"
    )
    # Zu jeder `tool_call_id` genau eine Antwort. Fehlt eine, weist der Anbieter
    # die naechste Anfrage ab — der abgelaufene Aufruf darf also nicht einfach
    # verschwinden.
    assert len(nachrichten) == AUFRUFE + 1
    assert len(benutzt) == AUFRUFE
    assert benutzt[0].get("failed") is True

    abgelaufen = nachrichten[1]["content"]
    # **Der Wortlaut ist die Zusage, nicht die Verzierung.** `wait_for` bricht
    # das Warten ab, nicht den Thread: `remember` darf danach noch committen.
    # Ein Modell, dem hier "fehlgeschlagen" gesagt wird, wiederholt den Aufruf
    # und legt den Eintrag ein zweites Mal an. Es gibt gegen diese Doppelung
    # keine Sperre — nur diesen Satz.
    assert "nicht weiter abgewartet" in abgelaufen
    assert "wiederhole ihn nicht blind" in abgelaufen
    # Und die uebrigen drei haben ihre Daten.
    for nachricht in nachrichten[2:]:
        assert "list_my_servers" in nachricht["content"]


def test_die_zeitgrenze_liegt_ueber_den_eigenen_fristen() -> None:
    """Ein Rückhalt darf nie vor der zuständigen Stelle greifen.

    Der `node_client` wartet 30 s auf eine Anlage und meldet sich dann selbst —
    mit einer Fehlermeldung, die sagt, *was* nicht ging. `WERKZEUG_ZEITGRENZE`
    weiss das nicht; sie kann nur sagen "hat zu lange gedauert". Läge sie
    darunter, bekäme das Modell bei jedem langsamen Node die schlechtere von
    zwei Auskünften, und die genauere käme nie zum Zug.

    Der Test steht hier, weil die Zahl verlockend klein aussieht: wer die
    Antwortzeit drücken will, dreht zuerst an ihr. Sie ist aber kein
    Latenzhebel — die gesamte Werkzeugzeit liegt im Benchmark bei
    0,00–0,10 s je Lauf.
    """
    assert ai_stream_service.WERKZEUG_ZEITGRENZE > node_client._DEFAULT_TIMEOUT


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
