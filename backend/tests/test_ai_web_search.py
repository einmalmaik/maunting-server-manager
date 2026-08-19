"""Websuche — endlich mit Wirkung, und mit klaren Grenzen.

`ai.web_search.use` stand seit Monaten im Rechtekatalog und wurde an keiner
Stelle geprueft. Ein Schalter, der nichts bewirkt, ist schlimmer als ein
fehlender: der Betreiber haelt etwas fuer freigeschaltet oder begrenzt, was es
nicht ist.

Die Tests halten drei Dinge fest: das Recht greift, Fremdtext aus dem Web wird
wie Fremdtext behandelt, und ein fehlender Schluessel legt nichts lahm.
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy.orm import Session

from models import Role, RolePermission, Server, ServerPermission, User
from services import ai_action_errors, ai_action_service, ai_web_search_service
from services.role_service import set_user_roles


def _allow_search(db: Session, user: User) -> None:
    role = Role(name=f"suche-{user.id}", description=None, is_system=False)
    db.add(role)
    db.flush()
    db.add(RolePermission(role_id=role.id, permission_key="ai.web_search.use"))
    db.commit()
    set_user_roles(db, user, [role.id])


def _mock_response(monkeypatch: pytest.MonkeyPatch, payload: dict, status: int = 200) -> dict:
    """Ersetzt den Suchaufruf und gibt zurueck, was tatsaechlich gesendet wurde."""
    seen: dict = {}

    def fake_get(url, *, params=None, headers=None, timeout=None):
        seen["url"] = url
        seen["params"] = params or {}
        seen["headers"] = headers or {}
        return httpx.Response(status, json=payload)

    monkeypatch.setattr(ai_web_search_service.httpx, "get", fake_get)
    monkeypatch.setattr(ai_web_search_service, "api_key", lambda: "test-schluessel")
    return seen


# ── Das Recht greift ──────────────────────────────────────────────────────

def test_search_without_the_permission_is_rejected(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_response(monkeypatch, {"web": {"results": []}})

    with pytest.raises(ai_action_errors.AiActionValidationError):
        ai_action_service.execute_read_tool(
            db, user=regular_user, tool_name="web_search",
            arguments={"query": "irgendwas"},
        )


def test_a_missing_key_is_reported_honestly_not_as_no_results(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Eine leere Trefferliste waere eine falsche Aussage ueber das Web."""
    _allow_search(db, regular_user)
    monkeypatch.setattr(ai_web_search_service, "api_key", lambda: None)

    result = ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="web_search",
        arguments={"query": "minecraft port"},
    )

    assert result["available"] is False
    assert result["reason"] == "AI_WEB_SEARCH_NOT_CONFIGURED"
    assert result["results"] == []


# ── Der Katalog ───────────────────────────────────────────────────────────

def test_without_a_key_the_tool_is_not_even_offered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ein Werkzeug, das immer scheitert, verwirrt ein Modell mehr als es hilft."""
    monkeypatch.setattr(ai_web_search_service, "is_configured", lambda: False)

    names = {item["function"]["name"] for item in ai_action_service.provider_tool_definitions()}

    assert "web_search" not in names


def test_with_a_key_the_tool_appears(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ai_web_search_service, "is_configured", lambda: True)

    names = {item["function"]["name"] for item in ai_action_service.provider_tool_definitions()}

    assert "web_search" in names


def test_an_unreadable_configuration_never_breaks_the_tool_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Die wichtigste Zusage dieser Datei.

    `is_configured` entscheidet mit, welche Werkzeuge der Katalog enthaelt.
    Laeuft sie in einen Fehler — unerreichbare Datenbank, kaputter Sidecar —
    waere ohne diesen Schutz nicht nur die Suche weg, sondern der ganze Chat.
    """
    def boom():
        raise RuntimeError("Datenbank nicht erreichbar")

    monkeypatch.setattr(ai_web_search_service, "api_key", boom)

    assert ai_web_search_service.is_configured() is False
    # Und der Katalog steht trotzdem.
    assert len(ai_action_service.provider_tool_definitions()) > 0


# ── Fremdtext bleibt Fremdtext ────────────────────────────────────────────

def test_results_are_shortened_and_redacted(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Treffer stammen aus dem offenen Web und werden wie Logzeilen behandelt."""
    _allow_search(db, regular_user)
    _mock_response(monkeypatch, {"web": {"results": [{
        "title": "T" * 500,
        "url": "https://example.invalid/hilfe",
        "description": "Nimm api_key=sk-abcdefghijklmnopqrstuvwxyz012345 " + "x" * 900,
    }]}})

    result = ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="web_search",
        arguments={"query": "fehler"},
    )

    hit = result["results"][0]
    assert len(hit["title"]) <= ai_web_search_service.MAX_TITLE_CHARS
    assert len(hit["snippet"]) <= ai_web_search_service.MAX_SNIPPET_CHARS
    assert "sk-abcdefghijklmnopqrstuvwxyz012345" not in hit["snippet"]


def test_non_http_targets_are_dropped(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ein javascript:-Ziel hat in einer spaeter verlinkten Liste nichts verloren."""
    _allow_search(db, regular_user)
    _mock_response(monkeypatch, {"web": {"results": [
        {"title": "Boese", "url": "javascript:alert(1)", "description": "x"},
        {"title": "Gut", "url": "https://example.invalid/ok", "description": "y"},
    ]}})

    result = ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="web_search",
        arguments={"query": "test"},
    )

    assert [hit["url"] for hit in result["results"]] == ["https://example.invalid/ok"]


def test_the_target_is_fixed_and_the_key_travels_in_the_header(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Benutzer steuert die Anfrage, niemals das Ziel — keine SSRF-Flaeche."""
    _allow_search(db, regular_user)
    seen = _mock_response(monkeypatch, {"web": {"results": []}})

    ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="web_search",
        arguments={"query": "https://interner-host.invalid/admin"},
    )

    assert seen["url"] == ai_web_search_service._ENDPOINT
    assert seen["headers"]["X-Subscription-Token"] == "test-schluessel"
    # Die Anfrage landet als Parameter, nicht als Ziel.
    assert seen["params"]["q"] == "https://interner-host.invalid/admin"


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (401, "AI_WEB_SEARCH_AUTH_FAILED"),
        (429, "AI_WEB_SEARCH_RATE_LIMITED"),
        (500, "AI_WEB_SEARCH_REJECTED"),
    ],
)
def test_each_failure_gets_its_own_code(
    status: int, code: str, db: Session, regular_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_search(db, regular_user)
    _mock_response(monkeypatch, {"error": "nope"}, status=status)

    result = ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="web_search", arguments={"query": "test"},
    )

    assert result["available"] is False
    assert result["reason"] == code


def test_too_many_results_are_capped(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die KI soll nachschlagen koennen, nicht das Web einlesen."""
    _allow_search(db, regular_user)

    with pytest.raises(ai_action_errors.AiActionValidationError):
        ai_action_service.execute_read_tool(
            db, user=regular_user, tool_name="web_search",
            arguments={"query": "test", "count": 50},
        )


# ── Die Abgrenzung: oeffentlich dokumentiert oder selbstgebaut ─────────────
#
# Vorgabe des Betreibers: die KI soll offizielle Dokumentation holen, wenn es um
# ein Spiel geht — aber nicht, wenn der Server etwas Eigenes faehrt, "z.B. ein
# Discord-Bot". Dann soll sie nachfragen.
#
# Entschieden wird das an einer Tatsache aus den Daten, nicht an der
# Einschaetzung des Modells: mitgelieferte Blueprints beschreiben oeffentlich
# dokumentierte Spiele, selbst importierte koennen alles sein.


def _server_mit_typ(db: Session, user: User, game_type: str) -> Server:
    row = Server(
        name=f"Server {game_type}",
        game_type=game_type,
        install_dir=f"/tmp/{game_type}",
        container_name=f"msm-{game_type}",
        status="stopped",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    db.add(ServerPermission(
        user_id=user.id, server_id=row.id, permission_key="server.view",
    ))
    db.commit()
    return row


def test_a_search_about_a_self_built_server_goes_through_as_well(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die Websuche haengt am Recht des Benutzers, an sonst nichts.

    Frueher entschied die **Herkunft des Blueprints** darueber: mitgeliefert
    hiess suchbar, selbst importiert hiess gesperrt. Die Annahme dahinter war
    "nativ = oeffentlich dokumentiert, community = privater Discord-Bot" — und
    sie ist im Betrieb umgekippt. Ein selbst gepflegter ARK-Blueprint ist
    community und beschreibt trotzdem ein Spiel mit oeffentlichem Wiki; die
    Suche war dort gesperrt, und das Modell fiel auf sein Trainingswissen
    zurueck. Genau das war der Anlass: falsche Werte in einer Datei, die es
    nicht gab.

    Die Vorgabe des Betreibers ist deshalb ausnahmslos: wer das Recht hat,
    darf suchen lassen — unabhaengig von Blueprint, Spiel oder Geraet. Der
    Schutz sensibler Werte haengt nicht mehr an einer Herkunftsvermutung,
    sondern an der Schwaerzung der Anfrage (Test unten).
    """
    _allow_search(db, regular_user)
    server = _server_mit_typ(db, regular_user, "mein_discord_bot")
    gesucht: list[str] = []

    def merken(q: str, c: int) -> list[dict]:
        gesucht.append(q)
        return [{"title": "T", "url": "https://x.invalid", "snippet": "s"}]

    monkeypatch.setattr(ai_web_search_service, "search", merken)

    ergebnis = ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="web_search",
        arguments={"query": "discord bot config", "server_id": server.id},
    )

    assert ergebnis["available"] is True
    assert ergebnis["results"]
    assert gesucht == ["discord bot config"]


def test_the_search_query_is_redacted_before_it_leaves_the_panel(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ein echter Wert in der Anfrage geht nicht an den Suchanbieter.

    Solange die Herkunftssperre bestand, war sie der faktische Schutz davor,
    dass etwas Vertrauliches nach draussen wandert. Sie faellt weg — der
    Schutz darf es nicht. Die Anfrage laeuft deshalb durch dieselbe
    Schwaerzung wie die Treffer.

    Die Grenze ist bewusst eng gezogen: geschwaerzt wird der **Wert hinter dem
    Schluessel**, nicht der Schluessel selbst. Ohne diese Trennung waere die
    Websuche fuer ihren haeufigsten Zweck unbrauchbar — nach einem
    Konfigurationsnamen zu suchen ist der Normalfall.
    """
    _allow_search(db, regular_user)
    gesucht: list[str] = []

    def merken(q: str, c: int) -> list[dict]:
        gesucht.append(q)
        return []

    monkeypatch.setattr(ai_web_search_service, "search", merken)

    ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="web_search",
        arguments={"query": "warum geht ServerAdminPassword=Maik1234 nicht"},
    )

    assert gesucht, "Die Suche haette laufen muessen"
    assert "Maik1234" not in gesucht[0]
    # Der Schluesselname bleibt stehen, sonst sucht niemand mehr etwas.
    assert "ServerAdminPassword" in gesucht[0]


def test_a_harmless_query_reaches_the_provider_unchanged(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die Gegenprobe zur Schwaerzung — ohne sie waere sie nicht messbar.

    Eine Schwaerzung, die zu viel greift, macht die Suche schlechter statt
    sicherer. Fachbegriffe wie `ServerAdminPassword` oder `RCONPort` sind
    genau das, wonach jemand sucht.
    """
    _allow_search(db, regular_user)
    gesucht: list[str] = []

    def merken(q: str, c: int) -> list[dict]:
        gesucht.append(q)
        return []

    monkeypatch.setattr(ai_web_search_service, "search", merken)

    frage = "ARK ServerAdminPassword in welcher Datei RCONPort Standardwert"
    ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="web_search",
        arguments={"query": frage},
    )

    assert gesucht == [frage]


def test_a_search_about_a_shipped_game_goes_through(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bei einem mitgelieferten Blueprint ist die Suche der richtige Weg."""
    _allow_search(db, regular_user)
    server = _server_mit_typ(db, regular_user, "minecraft_forge")
    monkeypatch.setattr(
        ai_web_search_service,
        "search",
        lambda q, c: [{"title": "Forge", "url": "https://x.invalid", "snippet": "s"}],
    )

    ergebnis = ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="web_search",
        arguments={"query": "forge server.properties", "server_id": server.id},
    )

    assert ergebnis["available"] is True
    assert ergebnis["results"]


def test_a_foreign_server_id_reveals_nothing(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die `server_id` laeuft ueber `_resolve_server`.

    Sonst waere sie ein Orakel: wer keinen Zugriff hat, koennte an der Antwort
    ablesen, ob ein Server existiert und was er faehrt.
    """
    _allow_search(db, regular_user)
    fremd = Server(
        name="Fremd", game_type="minecraft_forge",
        install_dir="/tmp/fremd", container_name="msm-fremd", status="stopped",
    )
    db.add(fremd)
    db.commit()
    db.refresh(fremd)
    monkeypatch.setattr(ai_web_search_service, "search", lambda q, c: [])

    with pytest.raises(ai_action_errors.AiActionValidationError):
        ai_action_service.execute_read_tool(
            db, user=regular_user, tool_name="web_search",
            arguments={"query": "irgendwas", "server_id": fremd.id},
        )


def test_the_server_list_no_longer_carries_a_search_verdict(
    db: Session, regular_user: User
) -> None:
    """`docs_searchable` ist ersatzlos weg — es gibt kein Urteil mehr zu faellen.

    Das Feld trug frueher die Herkunftsvermutung in die Serverliste, damit das
    Modell die Tatsache vor sich hat, statt sie am Namen zu erraten. Der
    Gedanke war richtig; die Tatsache war es nicht. Sie hat einen Server
    gesperrt, dessen Spiel ein oeffentliches Wiki hat.

    Ein Feld, das immer `true` waere, ist keine Information — es ist Ballast
    im Kontextfenster und eine Einladung, die Sperre spaeter wieder
    einzufuehren. Deshalb faellt es ganz.
    """
    _allow_search(db, regular_user)
    _server_mit_typ(db, regular_user, "minecraft_forge")
    _server_mit_typ(db, regular_user, "mein_discord_bot")

    liste = ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="list_my_servers", arguments={},
    )

    assert liste["servers"]
    for row in liste["servers"]:
        assert "docs_searchable" not in row
