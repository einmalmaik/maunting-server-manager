"""Ein Chat, der nie endet, braucht ein Gedaechtnis fuer sich selbst.

Der Assistent hat genau eine Unterhaltung. Ohne Kompression hiess "lang"
schlicht "abgeschnitten": `build_provider_messages` nahm die letzten 20
Nachrichten bzw. 24.000 Zeichen und liess den Rest weg — ohne Hinweis, ohne
Ersatz. Die KI wusste nach ein paar Dutzend Nachrichten nicht mehr, worum es am
Anfang ging, tat aber so, als kenne sie den Verlauf.

Die Tests halten drei Zusagen fest: es wird nur gefaltet wenn noetig, der
Verbrauch wird verbucht, und ein Fehlschlag darf niemals Nachrichten
verschwinden lassen.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from models import (
    AiConversation,
    AiMessage,
    AiProvider,
    AiUsageEvent,
    Role,
    RolePermission,
    User,
)
from services import ai_compaction_service, ai_context_window, ai_usage_service
from services.ai_context_service import build_provider_messages
from services.ai_limit_service import LIMIT_FIELDS, set_role_limit
from services.ai_model_catalog import Modell
from services.panel_settings_service import PanelSettingsService
from services.openai_compatible_adapter import AiProviderRequestError, StreamChunk
from services.role_service import set_user_roles


@pytest.fixture(autouse=True)
def _standardmarke():
    """Die Faltmarke ist panelweit und bleibt sonst zwischen den Tests stehen."""
    PanelSettingsService.invalidate_cache()
    yield
    PanelSettingsService.invalidate_cache()


def _provider(db: Session) -> AiProvider:
    provider = AiProvider(
        name="Kompression", provider_kind="openrouter",
        default_model="model-a", enabled=True, requires_api_key=False,
    )
    db.add(provider)
    db.commit()
    db.refresh(provider)
    return provider


def _enable(db: Session, user: User) -> None:
    role = Role(name=f"compact-{user.id}", description=None, is_system=False)
    db.add(role)
    db.flush()
    db.add(RolePermission(role_id=role.id, permission_key="ai.chat.use"))
    set_role_limit(db, role.id, {field: None for field in LIMIT_FIELDS})
    db.commit()
    set_user_roles(db, user, [role.id])


def _conversation(db: Session, user: User, *, messages: int, chars: int) -> AiConversation:
    conversation = AiConversation(
        id=str(uuid4()), user_id=user.id, server_id=None, title="Lang"
    )
    db.add(conversation)
    db.flush()
    start = datetime.now(timezone.utc) - timedelta(hours=messages)
    for index in range(messages):
        db.add(AiMessage(
            id=str(uuid4()), conversation_id=conversation.id,
            role="user" if index % 2 == 0 else "assistant",
            content=f"Nachricht {index} " + "x" * chars,
            status="complete",
            created_at=start + timedelta(minutes=index),
        ))
    db.commit()
    return conversation


def _fake_summary(monkeypatch: pytest.MonkeyPatch, text: str) -> dict:
    seen: dict = {}

    async def fake(_client, *, provider, api_key, messages, usage, **_kwargs):
        del provider, api_key
        seen["messages"] = messages
        usage.total_tokens = 30
        yield StreamChunk("content", text)

    monkeypatch.setattr(ai_compaction_service, "stream_chat_completion", fake)
    return seen


def test_a_short_conversation_is_never_compacted(db: Session, regular_user: User) -> None:
    """Unter der Schwelle passt alles in den Kontext — es gaebe nichts zu sparen."""
    conversation = _conversation(db, regular_user, messages=6, chars=100)

    assert ai_compaction_service.needs_compaction(db, conversation) is False


def test_a_long_conversation_is_recognized_as_foldable(
    db: Session, regular_user: User
) -> None:
    conversation = _conversation(db, regular_user, messages=40, chars=2_000)

    assert ai_compaction_service.needs_compaction(db, conversation) is True


@pytest.mark.asyncio
async def test_compaction_keeps_the_recent_messages_verbatim(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der juengste Teil traegt den aktuellen Faden und bleibt woertlich stehen."""
    _enable(db, regular_user)
    provider = _provider(db)
    conversation = _conversation(db, regular_user, messages=40, chars=2_000)
    _fake_summary(monkeypatch, "Es ging um einen Minecraft-Server und fehlende Ports.")

    done = await ai_compaction_service.compact_conversation(
        client=None, user_id=regular_user.id,
        conversation_id=conversation.id, provider_id=provider.id,
    )

    assert done is True
    db.expire_all()
    conversation = db.get(AiConversation, conversation.id)
    assert conversation.summary.startswith("Es ging um einen Minecraft-Server")
    assert conversation.summarized_until is not None

    # Die Historie enthaelt jetzt die Zusammenfassung plus den juengsten Rest —
    # nicht mehr die gefalteten Nachrichten einzeln.
    messages = build_provider_messages(db, conversation)
    serialized = " ".join(str(item.get("content")) for item in messages)
    assert "Es ging um einen Minecraft-Server" in serialized
    assert "Nachricht 0 " not in serialized
    assert "Nachricht 39 " in serialized


@pytest.mark.asyncio
async def test_compaction_books_its_own_token_usage(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die Zusammenfassung ist ein echter Providerruf und kostet Tokens.

    Ein unsichtbarer Verbrauch waere genau das, was Zielpunkt 6 verhindern
    soll: der Betreiber wuerde Kosten sehen, die in keiner Zeile stehen.
    """
    _enable(db, regular_user)
    provider = _provider(db)
    conversation = _conversation(db, regular_user, messages=40, chars=2_000)
    _fake_summary(monkeypatch, "Zusammenfassung.")

    await ai_compaction_service.compact_conversation(
        client=None, user_id=regular_user.id,
        conversation_id=conversation.id, provider_id=provider.id,
    )

    db.expire_all()
    events = db.query(AiUsageEvent).all()
    assert len(events) == 1
    assert events[0].status == "completed"
    assert events[0].accounted_tokens == 30


@pytest.mark.asyncio
async def test_a_failed_compaction_loses_no_messages(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scheitert die Zusammenfassung, bleibt der Chat unveraendert.

    Der gefaehrliche Fehler waere, `summarized_until` zu setzen und danach
    keine Zusammenfassung zu haben: dann waeren die Nachrichten aus dem Kontext
    verschwunden und nichts an ihrer Stelle.
    """
    _enable(db, regular_user)
    provider = _provider(db)
    conversation = _conversation(db, regular_user, messages=40, chars=2_000)

    async def failing(_client, **_kwargs):
        raise AiProviderRequestError("AI_PROVIDER_UNAVAILABLE")
        yield  # pragma: no cover - macht die Funktion zum Generator

    monkeypatch.setattr(ai_compaction_service, "stream_chat_completion", failing)

    done = await ai_compaction_service.compact_conversation(
        client=None, user_id=regular_user.id,
        conversation_id=conversation.id, provider_id=provider.id,
    )

    assert done is False
    db.expire_all()
    conversation = db.get(AiConversation, conversation.id)
    assert conversation.summary is None
    assert conversation.summarized_until is None
    # Die Reservierung darf keinen Nebenlaeufigkeitsplatz blockieren.
    assert db.query(AiUsageEvent).filter(AiUsageEvent.status == "reserved").count() == 0


@pytest.mark.asyncio
async def test_an_empty_summary_is_treated_as_a_failure(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Eine leere Antwort ist keine Zusammenfassung."""
    _enable(db, regular_user)
    provider = _provider(db)
    conversation = _conversation(db, regular_user, messages=40, chars=2_000)
    _fake_summary(monkeypatch, "   ")

    done = await ai_compaction_service.compact_conversation(
        client=None, user_id=regular_user.id,
        conversation_id=conversation.id, provider_id=provider.id,
    )

    assert done is False
    db.expire_all()
    assert db.get(AiConversation, conversation.id).summarized_until is None


@pytest.mark.asyncio
async def test_the_summary_prompt_never_carries_credentials(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Verlauf geht redigiert an den Anbieter — wie jeder andere Kontext."""
    _enable(db, regular_user)
    provider = _provider(db)
    conversation = _conversation(db, regular_user, messages=40, chars=2_000)
    # Der Zeitpunkt ist der Kern des Tests: `_conversation` legt die vierzig
    # Nachrichten ab `now - 40h` im Minutentakt an. Eine Zugangsdatennachricht
    # auf `now - 1 Tag` waere die juengste der Unterhaltung und bliebe damit im
    # woertlich erhaltenen Rest — sie wuerde nie gefaltet, und der Test bliebe
    # auch dann gruen, wenn die Redaktion des Zusammenfassungsprompts ganz
    # fehlte. Also setzen wir sie an den Anfang, wo tatsaechlich gefaltet wird.
    frueh = datetime.now(timezone.utc) - timedelta(hours=40) + timedelta(seconds=30)
    db.add(AiMessage(
        id=str(uuid4()), conversation_id=conversation.id, role="user",
        content="mein key ist api_key=sk-abcdefghijklmnopqrstuvwxyz012345",
        status="complete",
        created_at=frueh,
    ))
    db.commit()
    seen = _fake_summary(monkeypatch, "Zusammenfassung.")

    await ai_compaction_service.compact_conversation(
        client=None, user_id=regular_user.id,
        conversation_id=conversation.id, provider_id=provider.id,
    )

    serialized = " ".join(str(item["content"]) for item in seen["messages"])
    # Erst der Nachweis, dass die Nachricht ueberhaupt im Prompt steht: sonst
    # wuerde die zweite Zusicherung schon dadurch halten, dass die Nachricht
    # gar nicht mitgeschickt wurde.
    assert "mein key ist" in serialized
    assert "sk-abcdefghijklmnopqrstuvwxyz012345" not in serialized


@pytest.mark.asyncio
async def test_a_folded_question_keeps_its_text(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Text einer Rueckfrage steht in `question_json`, nicht in `content`.

    Wer beim Falten nur `content` liest, uebergibt dem Modell ein leeres
    "Assistent:" — und "den zweiten" ist danach nicht mehr aufloesbar, weil die
    Originalnachrichten hinter `summarized_until` liegen und nie wiederkommen.
    """
    _enable(db, regular_user)
    provider = _provider(db)
    conversation = _conversation(db, regular_user, messages=40, chars=2_000)
    # Bewusst ganz an den Anfang: nur so liegt das Paar im faltbaren Bereich und
    # nicht in den zwoelf woertlich erhaltenen Nachrichten am Ende.
    frueh = datetime.now(timezone.utc) - timedelta(hours=40)
    db.add(AiMessage(
        id=str(uuid4()), conversation_id=conversation.id, role="assistant",
        content="", status="complete",
        question_json=json.dumps({
            "question": "Welchen Server meinst du?",
            "options": [{"label": "Survival"}, {"label": "Creative"}],
        }),
        created_at=frueh + timedelta(seconds=30),
    ))
    db.add(AiMessage(
        id=str(uuid4()), conversation_id=conversation.id, role="user",
        content="den zweiten", status="complete",
        created_at=frueh + timedelta(seconds=40),
    ))
    db.commit()
    seen = _fake_summary(monkeypatch, "Zusammenfassung.")

    await ai_compaction_service.compact_conversation(
        client=None, user_id=regular_user.id,
        conversation_id=conversation.id, provider_id=provider.id,
    )

    serialized = " ".join(str(item["content"]) for item in seen["messages"])
    assert "Welchen Server meinst du?" in serialized
    # Welche Auswahl zur Debatte stand, gehoert zum Verstaendnis der Antwort.
    assert "Creative" in serialized
    assert "den zweiten" in serialized


@pytest.mark.asyncio
async def test_nothing_falls_between_the_summary_and_the_boundary(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Was `summarized_until` verdeckt, muss in der Zusammenfassung stecken.

    Bei 60 Nachrichten zu je rund 2.000 Zeichen sind ~97.000 Zeichen faltbar,
    also deutlich mehr als MAX_SOURCE_CHARS. Frueher ging trotzdem nur der
    juengste 60.000-Zeichen-Ausschnitt an den Anbieter, waehrend die Grenze auf
    die *letzte* faltbare Nachricht sprang: der Anfang war danach weder in
    `summary` noch in der Historie. Genau diese Luecke prueft der Test.
    """
    _enable(db, regular_user)
    provider = _provider(db)
    conversation = _conversation(db, regular_user, messages=60, chars=2_000)
    seen = _fake_summary(monkeypatch, "Zusammenfassung.")

    done = await ai_compaction_service.compact_conversation(
        client=None, user_id=regular_user.id,
        conversation_id=conversation.id, provider_id=provider.id,
    )

    assert done is True
    transcript = str(seen["messages"][1]["content"])
    # Gefaltet wird von vorne: die aelteste Nachricht ist die, die am ehesten
    # verlorenging.
    assert "Nachricht 0 " in transcript

    db.expire_all()
    conversation = db.get(AiConversation, conversation.id)
    verdeckt = (
        db.query(AiMessage)
        .filter(
            AiMessage.conversation_id == conversation.id,
            AiMessage.created_at <= conversation.summarized_until,
        )
        .all()
    )
    assert verdeckt
    for row in verdeckt:
        assert row.content[:16] in transcript
    # Der Ueberhang ist nicht verschwunden, sondern liegt als noch offene
    # Nachricht hinter der Grenze und kommt beim naechsten Durchlauf dran. Ob
    # `needs_compaction` dafuer schon wieder anschlaegt, haengt allein an
    # COMPACTION_THRESHOLD_CHARS und ist hier nicht die Zusage — die Zusage ist,
    # dass keine Nachricht zwischen Zusammenfassung und Grenze faellt.
    offen = ai_compaction_service._pending_messages(db, conversation)
    assert offen[0].content.startswith("Nachricht 29 ")


def test_a_large_window_postpones_the_fold(db: Session, regular_user: User) -> None:
    """Dieselbe Unterhaltung, zwei Modelle, zwei Antworten.

    Rund 80.000 faltbare Zeichen: ueber der alten Konstante von 40.000, aber
    weit unter drei Vierteln eines 128k-Fensters. Vorher wurde hier gefaltet,
    obwohl das Modell den ganzen Verlauf muehelos getragen haette — genau der
    Fall, den diese Aenderung abschafft.
    """
    conversation = _conversation(db, regular_user, messages=40, chars=3_000)
    gross = ai_context_window.aus_modell(
        Modell(model_id="m", name="m", denkt=False, kontext_tokens=128_000)
    )

    assert ai_compaction_service.needs_compaction(db, conversation) is True
    assert ai_compaction_service.needs_compaction(
        db, conversation, gross.zeichen
    ) is False


def test_the_operator_mark_moves_the_fold(db: Session, regular_user: User) -> None:
    """Die Marke ist eine Einstellung und keine Konstante — hier ist der Beweis."""
    conversation = _conversation(db, regular_user, messages=40, chars=3_000)
    # Faltbar sind rund 84.000 Zeichen (28 Nachrichten zu je ~3.000). Das
    # Budget ist so gewaehlt, dass die Marke genau dazwischen faellt: bei 75 %
    # liegt sie mit 105.000 darueber, bei 50 % mit 70.000 darunter.
    budget = 140_000

    ai_context_window.set_schwelle_prozent(75)
    assert ai_compaction_service.needs_compaction(db, conversation, budget) is False
    ai_context_window.set_schwelle_prozent(50)
    assert ai_compaction_service.needs_compaction(db, conversation, budget) is True


def test_an_unknown_window_keeps_the_old_constant(
    db: Session, regular_user: User
) -> None:
    """Ohne Fensterwissen bleibt alles, wie es war.

    Wichtig gerade fuer den Rueckfall: dessen Budget sind 24.000 Zeichen, und
    75 % davon waeren 18.000 — es wuerde also frueher gefaltet als vorher, ohne
    dass sich irgendetwas am Modell geaendert haette.
    """
    conversation = _conversation(db, regular_user, messages=20, chars=1_200)

    ai_context_window.set_schwelle_prozent(50)
    assert ai_compaction_service.needs_compaction(db, conversation, None) is False
    assert ai_compaction_service.faltschwelle(None) == (
        ai_compaction_service.COMPACTION_THRESHOLD_CHARS
    )


@pytest.mark.asyncio
async def test_a_large_window_allows_a_longer_summary(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sonst waere ein grosses Fenster an der schmalsten Stelle wieder eng.

    Fuenfzehn Saetze galten fuer jedes Gespraech. Bei einem 1M-Modell werden
    Hunderttausende Zeichen Verlauf gefaltet — auf fuenfzehn Saetze eingedampft
    waere der Verlust groesser als die Ersparnis.
    """
    _enable(db, regular_user)
    provider = _provider(db)
    # Rund 288.000 faltbare Zeichen — mehr als das Vierfache der frueher fest
    # verdrahteten Quellgrenze von 60.000.
    conversation = _conversation(db, regular_user, messages=60, chars=6_000)
    seen = _fake_summary(monkeypatch, "Zusammenfassung.")

    done = await ai_compaction_service.compact_conversation(
        client=None, user_id=regular_user.id,
        conversation_id=conversation.id, provider_id=provider.id,
        context_chars=300_000,
    )

    assert done is True
    prompt = str(seen["messages"][0]["content"])
    assert f"{ai_compaction_service.MAX_SUMMARY_SAETZE} Saetze" in prompt
    # Und es geht in einem Zug deutlich mehr hinaus als die alten 60.000.
    assert len(str(seen["messages"][1]["content"])) > 200_000


def test_an_orphaned_reservation_is_released_at_startup(
    db: Session, regular_user: User
) -> None:
    """Eine Reservierung ohne Nachricht sperrt sonst fuer immer.

    Die Verdichtung ist der einzige Pfad, der Kontingent bucht, ohne eine
    `AiMessage` anzulegen. Der Zaehler fuer `concurrent_operations` kennt kein
    Zeitfenster: bleibt so eine Zeile nach einem Prozessabbruch auf `reserved`,
    bekommt der Benutzer dauerhaft AiQuotaExceeded, obwohl nichts laeuft.
    """
    provider = _provider(db)
    verwaist = str(uuid4())
    mit_nachricht = str(uuid4())
    for request_id in (verwaist, mit_nachricht):
        db.add(AiUsageEvent(
            request_id=request_id, user_id=regular_user.id, provider_id=provider.id,
            model="model-a", status="reserved",
            reserved_tokens=120, reserved_cost_microunits=340,
            accounted_tokens=120, accounted_cost_microunits=340,
        ))
    conversation = AiConversation(
        id=str(uuid4()), user_id=regular_user.id, server_id=None, title="Lauf"
    )
    db.add(conversation)
    db.flush()
    db.add(AiMessage(
        id=str(uuid4()), conversation_id=conversation.id, role="assistant",
        content="", status="streaming", request_id=mit_nachricht,
        created_at=datetime.now(timezone.utc),
    ))
    db.commit()

    geschlossen = ai_usage_service.verwaiste_reservierungen_abgleichen(db)

    assert geschlossen == 1
    db.expire_all()
    frei = db.query(AiUsageEvent).filter(AiUsageEvent.request_id == verwaist).one()
    assert frei.status == "completed"
    # Konservativ mit dem reservierten Wert: nach einem Abbruch ist unbekannt,
    # wie viel der Anbieter schon geliefert hat.
    assert frei.accounted_tokens == 120
    # Zeilen mit Nachricht bleiben liegen — dafuer ist der Stream-Wiederanlauf
    # zustaendig, der zusaetzlich deren Zustand kennt.
    gehalten = db.query(AiUsageEvent).filter(
        AiUsageEvent.request_id == mit_nachricht
    ).one()
    assert gehalten.status == "reserved"
