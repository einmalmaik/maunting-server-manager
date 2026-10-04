"""Die KI heisst fest Singra.

Bis zum 05.10.2026 konnte jeder Benutzer ihr einen eigenen Rufnamen geben
(``users.agent_name``, Standard "Assistent"); der stand deshalb im Lageblock.
Seitdem gilt:

1. Der Name steht im **statischen** Systemprompt (ROLLE und IDENTITAET) — fuer
   alle gleich, also unschaedlich fuer das Prompt-Caching. Der Lageblock
   traegt keine Namenszeile mehr.
2. IDENTITAET kennt die Herkunft (Singularitaet, zuerst als AEGIS gedacht) und
   gilt fuer vollen Betrieb, Gehirn und Stimme — nicht fuer den Worker, der
   nie mit Menschen redet.
3. Umbenennen geht nirgends mehr: kein Endpunkt, kein Werkzeug.
4. ``/auth/me`` liefert ``agent_name`` fest als "Singra" — fuer installierte
   Desktop-Apps, die das Feld noch lesen.
"""

from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from config import settings
from database import Base
from models import User
from services import ai_lage, ai_tool_registry
from services.ai_prompt import IDENTITAET, KI_NAME, ROLLE, build
from services.ai_tools import provider_tool_definitions
from services.ai_voice.live_session import live_anweisungen
from services.openai_compatible_adapter import ProviderToolCall, StreamChunk
from tests.test_ai_provider_chat import _enable_chat, _provider


def test_der_name_ist_singra():
    assert KI_NAME == "Singra"
    assert ROLLE.startswith("Du bist Singra ")
    assert IDENTITAET.startswith("Du heißt Singra")


class TestPromptIdentitaet:
    def test_identitaet_gilt_voll_gehirn_und_gesprochen(self):
        assert IDENTITAET in build()
        assert IDENTITAET in build(rolle="gehirn")
        # Gesprochen ist "wie heisst du?" die naheliegendste Frage.
        assert IDENTITAET in build(gesprochen=True)
        # Die GPT-Live-Stimme hat einen eigenen Prompt und antwortet darauf
        # selbst, ohne das Backend zu fragen.
        assert IDENTITAET in live_anweisungen([], "de")

    def test_worker_kennt_den_namen_aber_nicht_die_herkunft(self):
        worker = build(rolle="worker")
        assert IDENTITAET not in worker
        assert ROLLE in worker

    def test_herkunft_und_ki_ehrlichkeit(self):
        assert "Singularität" in IDENTITAET
        assert "AEGIS" in IDENTITAET
        assert "bestätige das klar und wahrheitsgemäß" in IDENTITAET
        assert "ohne Modellname oder Anbieter zu nennen" in IDENTITAET

    def test_kein_rest_des_waehlbaren_namens(self):
        prompt = build()
        assert "Dein Name" not in prompt
        assert "Rufname" not in prompt
        assert build() == build()


def test_lageblock_traegt_keine_namenszeile(db: Session, regular_user: User):
    text = ai_lage.lageblock(db, regular_user)
    assert "Dein Name" not in text
    assert not hasattr(ai_lage, "name_des_assistenten")


def test_umbenennen_ist_weg(
    client: TestClient, regular_user: User, user_cookies: dict, user_csrf_token: str
):
    resp = client.patch(
        "/api/auth/me/agent-name",
        json={"agent_name": "Jarvis"},
        headers={"X-CSRF-Token": user_csrf_token},
        cookies=user_cookies,
    )
    assert resp.status_code in (404, 405)
    assert "set_agent_name" not in ai_tool_registry.WERKZEUGE
    namen = {tool["function"]["name"] for tool in provider_tool_definitions()}
    assert "set_agent_name" not in namen
    assert not hasattr(User, "agent_name")


def test_me_liefert_den_festen_namen_fuer_alte_desktop_apps(
    client: TestClient, regular_user: User, user_cookies: dict
):
    me = client.get("/api/auth/me", cookies=user_cookies)
    assert me.status_code == 200
    assert me.json()["agent_name"] == KI_NAME


def test_die_migration_entfernt_die_spalte(pg_wegwerf):
    """Die Spalte verschwindet ueber die Kette, nicht nur aus dem Modell.

    Rueckbau legt sie leer wieder an — alle Konten landen dann auf dem
    damaligen Standardnamen, mehr verspricht der Downgrade nicht.
    """
    db_url = pg_wegwerf("kiname")
    vorher = settings.database_url
    settings.database_url = db_url
    backend_dir = Path(__file__).resolve().parent.parent
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "migrations"))
    engine = create_engine(db_url)

    def spalten() -> set[str]:
        engine.dispose()
        return {spalte["name"] for spalte in inspect(engine).get_columns("users")}

    try:
        Base.metadata.create_all(engine)
        command.stamp(config, "head")
        assert "agent_name" not in spalten()

        command.downgrade(config, "20261003_02")
        assert "agent_name" in spalten()

        command.upgrade(config, "head")
        assert "agent_name" not in spalten()
    finally:
        engine.dispose()
        settings.database_url = vorher


# ── Ueber die API: was beim Anbieter ankaeme ─────────────────────────────────


def _chat_senden(client: TestClient, cookies: dict, csrf: str, provider_id: int, text: str):
    assert client.get("/api/ai/conversation", cookies=cookies).status_code == 200
    return client.post(
        "/api/ai/conversation/messages/stream",
        json={"content": text, "provider_id": provider_id, "request_id": str(uuid4())},
        cookies=cookies,
        headers={"X-CSRF-Token": csrf},
    )


def test_chat_schickt_singra_an_den_anbieter(
    client: TestClient,
    db: Session,
    regular_user: User,
    user_cookies: dict,
    user_csrf_token: str,
    monkeypatch: pytest.MonkeyPatch,
):
    """Der echte Weg vom Endpunkt bis vor den Anbieter: Prompt, Lage, Katalog."""
    _enable_chat(db, regular_user)
    provider = _provider(db, monkeypatch)
    gesehen: dict = {}

    async def fake_stream(_client, *, messages, usage, tools=None, **_kwargs):
        gesehen["messages"] = messages
        gesehen["tools"] = tools or []
        usage.total_tokens = 7
        yield StreamChunk("content", "Ich bin Singra.")

    monkeypatch.setattr("services.ai_stream_service.stream_chat_completion", fake_stream)
    antwort = _chat_senden(
        client, user_cookies, user_csrf_token, provider.id, "Warum heißt du Singra?"
    )

    assert antwort.status_code == 200
    assert "Ich bin Singra." in antwort.text
    system = [m.get("content") or "" for m in gesehen["messages"] if m.get("role") == "system"]
    # Erste system-Nachricht ist der statische Prompt, die Herkunft steht darin.
    assert IDENTITAET in system[0]
    assert "Singularität" in system[0] and "AEGIS" in system[0]
    # Keine Namenszeile mehr in irgendeiner system-Nachricht (Lageblock).
    assert not any("Dein Name" in text for text in system)
    namen = {tool["function"]["name"] for tool in gesehen["tools"]}
    assert namen, "der Katalog ging leer hinaus"
    assert "set_agent_name" not in namen


def test_ein_erfundener_umbenennungsaufruf_aendert_nichts(
    client: TestClient,
    db: Session,
    regular_user: User,
    user_cookies: dict,
    user_csrf_token: str,
    monkeypatch: pytest.MonkeyPatch,
):
    """Ruft ein Modell das alte Werkzeug aus Gewohnheit trotzdem, laeuft das
    Gespraech weiter — und am Konto aendert sich nichts."""
    _enable_chat(db, regular_user)
    provider = _provider(db, monkeypatch)
    runde = 0

    async def fake_stream(_client, *, usage, **_kwargs):
        nonlocal runde
        runde += 1
        usage.total_tokens = 5
        if runde == 1:
            usage.tool_calls = [ProviderToolCall(
                id="call-name", name="set_agent_name", arguments={"name": "Jarvis"},
            )]
            if False:
                yield StreamChunk("content", "")
            return
        yield StreamChunk("content", "Ich bleibe Singra.")

    monkeypatch.setattr("services.ai_stream_service.stream_chat_completion", fake_stream)
    antwort = _chat_senden(
        client, user_cookies, user_csrf_token, provider.id, "Nenn dich ab jetzt Jarvis."
    )

    assert antwort.status_code == 200
    assert "Ich bleibe Singra." in antwort.text
    me = client.get("/api/auth/me", cookies=user_cookies).json()
    assert me["agent_name"] == KI_NAME
