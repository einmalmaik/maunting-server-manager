"""Der Laufbeginn laeuft **neben** der Ereignisschleife, nicht auf ihr.

Dieselbe Zusage wie in `test_ai_werkzeug_nebenlaeufigkeit.py`, eine Ebene
frueher. Gemessen war (backend/logs/ai-benchmark/*-vorher-anlauf-parallel.json,
Stufe 200): `lauf_beginnen` kostet 13 ms je Lauf, bei 200 gleichzeitigen
Nachrichten zusammen 2,84 s — und die lagen **auf** der Ereignisschleife, weil
der Endpunkt eine `async def` ist und eine blockierende Funktion geradewegs
aufrief. Die Schleife stand dabei 3,45 s am Stueck. In dieser Zeit bekam kein
Benutzer eine Antwort auf irgendeine Anfrage; auch keiner, der nur seine
Serverliste aufrufen wollte.

Die zweite Zusage hier ist die heiklere und die, die der Benchmark nie sehen
wird: **je Unterhaltung laeuft immer nur ein Anlauf.** Solange alles auf der
Schleife lag, war das geschenkt. Jetzt haengt daran die Reihenfolge
"Vorgaenger abloesen, dann anlegen" — und ein Benutzer hat genau eine
Unterhaltung, zwei schnell abgeschickte Nachrichten reichen also aus, um sie zu
brechen.
"""

from __future__ import annotations

import asyncio
import time
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from models import AiConversation, AiProvider, User
from services import ai_stream_service


#: Lang genug, dass der Unterschied zwischen "auf der Schleife" und "daneben"
#: nicht im Rauschen verschwindet, kurz genug, dass die Suite nicht wartet.
ANLAUFDAUER = 0.2
ANLAEUFE = 4


def _unterhaltung(db: Session, user: User) -> AiConversation:
    conversation = AiConversation(
        id=str(uuid4()), user_id=user.id, server_id=None, title="Anlauf"
    )
    db.add(conversation)
    db.commit()
    return conversation


def _anbieter(db: Session) -> AiProvider:
    provider = AiProvider(
        name="Anlauf",
        provider_kind="openrouter",
        default_model="model-a",
        enabled=True,
        requires_api_key=False,
    )
    db.add(provider)
    db.commit()
    db.refresh(provider)
    return provider


def _langsamer_anlauf(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Ersetzt `lauf_beginnen` durch einen synchronen Schlaf.

    Ersetzt wird `lauf_beginnen` und nicht eine seiner Datenbankschichten: die
    Funktion besitzt die Sitzung, und genau die soll hier nicht mitspielen.
    Gemessen wird die **Ablaufsteuerung** — ob der Anlauf in einen Thread
    gegeben und je Unterhaltung gereiht wird. Ob eine eigene Sitzung je Anlauf
    richtig ist, beantworten die uebrigen KI-Tests, die alle durch diesen Pfad
    gehen.

    `time.sleep` und nicht `asyncio.sleep`, weil das der Punkt ist: ein
    Datenbankschreibvorgang blockiert seinen Thread. Ein `asyncio.sleep` waere
    hoeflich und wuerde die Frage nicht stellen.

    Der Rueckgabewert protokolliert, wieviele Anlaeufe je Unterhaltung
    gleichzeitig drin waren — das ist die zweite Zusage dieser Datei.
    """
    protokoll: dict = {"drin": 0, "hoechstens": 0}
    schloss = __import__("threading").Lock()

    def _schlafen(db, **kwargs):
        del db
        with schloss:
            protokoll["drin"] += 1
            protokoll["hoechstens"] = max(protokoll["hoechstens"], protokoll["drin"])
        try:
            time.sleep(ANLAUFDAUER)
        finally:
            with schloss:
                protokoll["drin"] -= 1

        class _Lauf:
            id = str(uuid4())

        return _Lauf(), None

    monkeypatch.setattr(ai_stream_service, "lauf_beginnen", _schlafen)
    return protokoll


@pytest.mark.asyncio
async def test_der_laufbeginn_blockiert_die_ereignisschleife_nicht(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Das Panel darf nicht stehen, waehrend eine Nachricht anlaeuft.

    Der Waechter zaehlt, wie oft eine gewoehnliche Koroutine waehrenddessen
    drankommt. Lag der Anlauf auf der Schleife, war die Antwort null — und genau
    null war sie, solange `stream_message` das synchrone `lauf_beginnen` direkt
    rief.
    """
    _langsamer_anlauf(monkeypatch)
    # Eins, damit die Anlaeufe **nacheinander** laufen: der schlechteste Fall
    # fuer diese Frage. Selbst dann muss die Schleife frei bleiben, denn genau
    # das ist der Unterschied zwischen `to_thread` und einem direkten Aufruf.
    monkeypatch.setattr(ai_stream_service, "_anlauf_nebenlaeufigkeit", lambda: 1)
    conversation = _unterhaltung(db, regular_user)
    provider = _anbieter(db)

    ticks = 0

    async def _waechter() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.02)
            ticks += 1

    waechter = asyncio.create_task(_waechter())
    try:
        ergebnisse = await asyncio.gather(*(
            ai_stream_service.lauf_beginnen_nebenher(
                user_id=regular_user.id,
                conversation_id=conversation.id,
                provider_id=provider.id,
                request_id=uuid4(),
                content="Sieh nach meinen Servern",
                reasoning=False,
            )
            for _ in range(ANLAEUFE)
        ))
    finally:
        waechter.cancel()

    # Jeder Anlauf hat trotzdem seine Kennung bekommen — der Endpunkt braucht
    # sie sofort, mit ihr haengt sich der Browser an den Ereignisstrom.
    assert all(run_id for run_id, _ in ergebnisse)

    # Vier Anlaeufe zu 0,2 s sind 0,8 s Arbeit; bei 20-ms-Takt waeren das rund
    # vierzig Gelegenheiten. Zehn ist weit genug unter dem Erwartungswert, um
    # auf einem ausgelasteten Rechner nicht falsch anzuschlagen, und weit genug
    # ueber null, um eine blockierte Schleife zu erkennen.
    assert ticks >= 10, (
        f"Die Ereignisschleife kam waehrend der Anlaeufe nur {ticks}-mal dran — "
        "das Panel steht in dieser Zeit"
    )


@pytest.mark.asyncio
async def test_zwei_anlaeufe_derselben_unterhaltung_ueberlappen_nie(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die Reihenfolge "abloesen, dann anlegen" ueberlebt die Nebenlaeufigkeit.

    `lauf_beginnen` beendet zuerst die offenen Laeufe der Unterhaltung und legt
    **danach** den neuen an. Duerften zwei Anlaeufe derselben Unterhaltung
    gleichzeitig hineinlaufen, koennte jeder abloesen, bevor der andere angelegt
    hat — und am Ende schrieben zwei Laeufe in denselben Chat.

    Die Breite steht hier auf vier, damit die Schranke die Aussage nicht
    versehentlich mitbeweist: gereiht wird durch das Schloss der Unterhaltung,
    nicht durch die Obergrenze.
    """
    protokoll = _langsamer_anlauf(monkeypatch)
    monkeypatch.setattr(ai_stream_service, "_anlauf_nebenlaeufigkeit", lambda: ANLAEUFE)
    conversation = _unterhaltung(db, regular_user)
    provider = _anbieter(db)

    await asyncio.gather(*(
        ai_stream_service.lauf_beginnen_nebenher(
            user_id=regular_user.id,
            conversation_id=conversation.id,
            provider_id=provider.id,
            request_id=uuid4(),
            content="Und jetzt starte ihn neu",
            reasoning=False,
        )
        for _ in range(ANLAEUFE)
    ))

    assert protokoll["hoechstens"] == 1, (
        f"{protokoll['hoechstens']} Anlaeufe derselben Unterhaltung waren "
        "gleichzeitig drin — zwei Laeufe koennen denselben Chat beschreiben"
    )


@pytest.mark.asyncio
async def test_zwei_unterhaltungen_stehen_sich_nicht_im_weg(
    db: Session, regular_user: User, owner_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Das Schloss haengt an der Unterhaltung, nicht am Prozess.

    Sonst waere die Reihung von oben ein globaler Flaschenhals: bei 200
    gleichzeitigen Nachrichten von 200 Benutzern liefe der Anlauf wieder
    streng nacheinander, nur woanders.
    """
    protokoll = _langsamer_anlauf(monkeypatch)
    monkeypatch.setattr(ai_stream_service, "_anlauf_nebenlaeufigkeit", lambda: 2)
    eine = _unterhaltung(db, regular_user)
    andere = _unterhaltung(db, owner_user)
    provider = _anbieter(db)

    beginn = time.perf_counter()
    await asyncio.gather(
        ai_stream_service.lauf_beginnen_nebenher(
            user_id=regular_user.id, conversation_id=eine.id,
            provider_id=provider.id, request_id=uuid4(),
            content="Frage A", reasoning=False,
        ),
        ai_stream_service.lauf_beginnen_nebenher(
            user_id=owner_user.id, conversation_id=andere.id,
            provider_id=provider.id, request_id=uuid4(),
            content="Frage B", reasoning=False,
        ),
    )
    dauer = time.perf_counter() - beginn

    assert protokoll["hoechstens"] == 2, (
        "Zwei verschiedene Unterhaltungen liefen nacheinander statt nebeneinander"
    )
    assert dauer < ANLAUFDAUER * 2, (
        f"Zwei Anlaeufe zu {ANLAUFDAUER}s brauchten {dauer:.2f}s"
    )


@pytest.mark.asyncio
async def test_das_schloss_verschwindet_wieder(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die Ablage der Schloesser darf nicht mit jeder Unterhaltung wachsen.

    Ein Schloss je Unterhaltung, das nie aufgeraeumt wird, ist ein Leck, das
    erst nach Monaten Betrieb auffaellt — und dann als "das Panel braucht
    langsam viel Speicher", also als etwas, das niemand mehr hierher
    zurueckverfolgt.
    """
    _langsamer_anlauf(monkeypatch)
    conversation = _unterhaltung(db, regular_user)
    provider = _anbieter(db)

    await ai_stream_service.lauf_beginnen_nebenher(
        user_id=regular_user.id, conversation_id=conversation.id,
        provider_id=provider.id, request_id=uuid4(),
        content="Einmal", reasoning=False,
    )

    assert conversation.id not in ai_stream_service._ANLAUF_SCHLOESSER
    assert conversation.id not in ai_stream_service._ANLAUF_WARTENDE


def test_auf_sqlite_laeuft_genau_ein_anlauf() -> None:
    """Die Breite haengt an der Datenbank, und das ist kein Detail.

    Auf SQLite teilen sich alle Sitzungen eine Verbindung. Zwei Transaktionen
    gleichzeitig darauf sind keine Nebenlaeufigkeit, sondern ein Datenfehler:
    der Commit der einen schliesst die offene Arbeit der anderen mit ab.

    Der Test steht hier, damit niemand die Zahl spaeter "vereinheitlicht". Die
    Testsuite selbst laeuft auf SQLite — eine Aenderung an dieser Stelle waere
    also gerade dort gefaehrlich, wo sie am wenigsten auffiele.
    """
    assert ai_stream_service._anlauf_nebenlaeufigkeit() == 1
