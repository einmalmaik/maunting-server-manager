"""Websuche — endlich mit Wirkung, und mit klaren Grenzen.

`ai.web_search.use` stand seit Monaten im Rechtekatalog und wurde an keiner
Stelle geprueft. Ein Schalter, der nichts bewirkt, ist schlimmer als ein
fehlender: der Betreiber haelt etwas fuer freigeschaltet oder begrenzt, was es
nicht ist.

Die Tests halten drei Dinge fest: das Recht greift, Fremdtext aus dem Web wird
wie Fremdtext behandelt, und ein fehlender Schluessel legt nichts lahm.
"""

from __future__ import annotations

import json
import socket
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

    def fake_get(url, *, params=None, headers=None):
        seen["url"] = url
        seen["params"] = params or {}
        seen["headers"] = headers or {}
        return httpx.Response(status, json=payload)

    class FakeClient:
        get = staticmethod(fake_get)

    monkeypatch.setattr(ai_web_search_service, "_http_client", lambda: FakeClient())
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


def test_session_scoped_cache_reuses_only_the_same_voice_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    class FakeClient:
        def get(self, *_args, **_kwargs):
            nonlocal calls
            calls += 1
            return httpx.Response(200, json={"web": {"results": []}})

    ai_web_search_service.shutdown_http_client()
    monkeypatch.setattr(ai_web_search_service, "api_key", lambda: "test-schluessel")
    monkeypatch.setattr(ai_web_search_service, "_http_client", lambda: FakeClient())

    assert ai_web_search_service.search("MSM update", cache_scope="voice:1:a") == []
    assert ai_web_search_service.search("MSM update", cache_scope="voice:1:a") == []
    assert ai_web_search_service.search("MSM update", cache_scope="voice:1:b") == []

    assert calls == 2


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


# ── SearXNG Sidecar & Default-Endpunkt ──────────────────────────────────────

def test_searxng_defaults_to_local_sidecar_when_running(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ai_web_search_service, "_panel_searxng_url", lambda: None)
    monkeypatch.setattr(ai_web_search_service, "is_sidecar_running", lambda *_: True)

    assert ai_web_search_service.default_searxng_url() == "http://127.0.0.1:8888"
    assert ai_web_search_service.searxng_url() == "http://127.0.0.1:8888"
    assert ai_web_search_service.is_default_searxng() is True
    assert ai_web_search_service.is_configured() is True


def test_searxng_custom_url_overrides_default_sidecar(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ai_web_search_service, "_panel_searxng_url", lambda: "http://searxng.internal:8080")
    monkeypatch.setattr(ai_web_search_service, "is_sidecar_running", lambda *_: True)

    assert ai_web_search_service.custom_searxng_url() == "http://searxng.internal:8080"
    assert ai_web_search_service.searxng_url() == "http://searxng.internal:8080"
    assert ai_web_search_service.is_default_searxng() is False


def test_searxng_search_parses_json_results(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = {}

    def fake_get(url, *, params=None, headers=None):
        seen["url"] = url
        seen["params"] = params
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Minecraft Server Guide",
                        "url": "https://example.com/mc",
                        "content": "Default port is 25565.",
                    }
                ]
            },
        )

    class FakeClient:
        get = staticmethod(fake_get)

    ai_web_search_service.shutdown_http_client()
    monkeypatch.setattr(ai_web_search_service, "_http_client", lambda: FakeClient())
    monkeypatch.setattr(ai_web_search_service, "api_key", lambda: None)
    monkeypatch.setattr(ai_web_search_service, "searxng_url", lambda: "http://127.0.0.1:8888")

    hits = ai_web_search_service.search("minecraft port")

    assert seen["url"] == "http://127.0.0.1:8888/search"
    assert seen["params"]["q"] == "minecraft port"
    assert seen["params"]["format"] == "json"
    assert len(hits) == 1
    assert hits[0]["title"] == "Minecraft Server Guide"
    assert hits[0]["url"] == "https://example.com/mc"
    assert "25565" in hits[0]["snippet"]


def test_additive_multi_engine_fusion(monkeypatch: pytest.MonkeyPatch) -> None:
    """SearXNG und Brave liefern beide Treffer und werden additiv verschmolzen."""
    def fake_get(url, *, params=None, headers=None):
        if "api.search.brave.com" in url:
            return httpx.Response(
                200,
                json={
                    "web": {
                        "results": [
                            {"title": "Brave Hit 1", "url": "https://example.com/shared", "description": "Brave snippet"},
                            {"title": "Brave Hit 2", "url": "https://example.com/brave-only", "description": "Brave exclusive"},
                        ]
                    }
                },
            )
        elif "127.0.0.1:8888" in url:
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"title": "SearXNG Hit 1", "url": "https://example.com/shared/", "content": "SearXNG snippet", "thumbnail": "https://example.com/img.png"},
                        {"title": "SearXNG Hit 2", "url": "https://example.com/searx-only", "content": "SearXNG exclusive"},
                    ]
                },
            )
        return httpx.Response(404)

    class FakeClient:
        get = staticmethod(fake_get)

    ai_web_search_service.shutdown_http_client()
    monkeypatch.setattr(ai_web_search_service, "_http_client", lambda: FakeClient())
    monkeypatch.setattr(ai_web_search_service, "api_key", lambda: "brave-key")
    monkeypatch.setattr(ai_web_search_service, "searxng_url", lambda: "http://127.0.0.1:8888")
    monkeypatch.setattr(ai_web_search_service, "google_cse_credentials", lambda: None)
    monkeypatch.setattr(ai_web_search_service, "ai_web_search_enabled", lambda: True)

    # In searxng_ergebnis
    ergebnis = ai_web_search_service.searxng_ergebnis("the witcher 4", limit=15)
    treffer = ergebnis["treffer"]

    # Dedupliziert: shared URL taucht nur einmal auf
    urls = [t["url"] for t in treffer]
    assert urls.count("https://example.com/shared") == 1
    assert "https://example.com/brave-only" in urls
    assert "https://example.com/searx-only" in urls
    # Vorschaubild aus SearXNG angereichert
    shared_hit = next(t for t in treffer if t["url"] == "https://example.com/shared")
    assert shared_hit.get("bild") == "https://example.com/img.png"

    # In search() fuer Singra
    singra_hits = ai_web_search_service.search("the witcher 4", count=10)
    singra_urls = [t["url"] for t in singra_hits]
    assert singra_urls.count("https://example.com/shared") == 1
    assert "https://example.com/brave-only" in singra_urls
    assert "https://example.com/searx-only" in singra_urls


def test_scrape_top_urls_parallel(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_scrape(url, max_chars=3500):
        if "forbidden" in url:
            return {"error": "http_status_403", "url": url}
        return {"title": f"Titel {url}", "url": url, "content": f"Volltext Inhalt {url}"}

    monkeypatch.setattr(ai_web_search_service, "scrape_url", fake_scrape)

    urls = [
        "https://example.com/seite1",
        "https://example.com/forbidden",
        "https://example.com/seite2",
    ]
    res = ai_web_search_service.scrape_top_urls_parallel(urls, max_chars=1000, limit=3)
    assert "https://example.com/seite1" in res
    assert "Volltext Inhalt https://example.com/seite1" in res["https://example.com/seite1"]
    assert "https://example.com/seite2" in res
    assert "https://example.com/forbidden" not in res


def test_read_web_page_tool(db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    # 1. Ohne Berechtigung abgelehnt
    with pytest.raises(ai_action_errors.AiActionValidationError):
        ai_action_service.execute_read_tool(
            db, user=regular_user, tool_name="read_web_page", arguments={"url": "https://example.com/page"}
        )

    # 2. Mit Berechtigung ausfuehrbar
    _allow_search(db, regular_user)
    monkeypatch.setattr(
        ai_web_search_service,
        "scrape_url",
        lambda url, max_chars=8000, deep=False: {"title": "Artikel", "url": url, "content": "Grosser Textinhalt"},
    )

    res = ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="read_web_page", arguments={"url": "https://example.com/page", "server_id": 42}
    )
    assert res["success"] is True
    assert res["title"] == "Artikel"
    assert res["content"] == "Grosser Textinhalt"

    # 3. Mit deep=True
    gesehen_deep: dict = {}

    def fake_scrape_deep(url, max_chars=8000, deep=False):
        gesehen_deep["url"] = url
        gesehen_deep["max_chars"] = max_chars
        gesehen_deep["deep"] = deep
        return {"title": "Deep Artikel", "url": url, "content": "Sehr langer Textinhalt"}

    monkeypatch.setattr(ai_web_search_service, "scrape_url", fake_scrape_deep)
    res_deep = ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="read_web_page", arguments={"url": "https://example.com/deep", "deep": True}
    )
    assert res_deep["success"] is True
    assert gesehen_deep["deep"] is True
    assert gesehen_deep["max_chars"] == 25000

    # 4. Wenn Websuche global deaktiviert ist
    monkeypatch.setattr(ai_web_search_service, "ai_web_search_enabled", lambda: False)
    res_disabled = ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="read_web_page", arguments={"url": "https://example.com/page"}
    )
    assert res_disabled["success"] is False
    assert res_disabled["error"] == "AI_WEB_SEARCH_NOT_CONFIGURED"


def test_multi_engine_fusion_interleaves_without_starvation() -> None:
    """Wenn Brave 15 Treffer liefert, werden SearXNG und Google nicht verdrängt, sondern verzahnt."""
    brave_hits = [{"title": f"Brave {i}", "url": f"https://b.example.com/{i}"} for i in range(15)]
    searx_hits = [{"title": f"Searx {i}", "url": f"https://s.example.com/{i}"} for i in range(15)]
    google_hits = [{"title": f"Google {i}", "url": f"https://g.example.com/{i}"} for i in range(10)]

    fused = ai_web_search_service._mische_treffer([brave_hits, searx_hits, google_hits], limit=15)
    assert len(fused) == 15

    b_count = sum(1 for r in fused if "Brave" in r["title"])
    s_count = sum(1 for r in fused if "Searx" in r["title"])
    g_count = sum(1 for r in fused if "Google" in r["title"])

    assert b_count == 8
    assert s_count == 4
    assert g_count == 3
    # Reihenfolge beginnt mit Top-Brave, enthält aber frühzeitig SearXNG und Google
    assert "Brave" in fused[0]["title"]
    assert "Brave" in fused[1]["title"]
    assert "Searx" in fused[2]["title"]
    assert "Google" in fused[3]["title"]


def test_normalisiere_url_strips_tracking_params() -> None:
    u1 = "https://example.com/artikel?utm_source=brave&utm_medium=cpc&id=123"
    u2 = "https://example.com/artikel/?id=123"
    norm1 = ai_web_search_service._normalisiere_url(u1)
    norm2 = ai_web_search_service._normalisiere_url(u2)
    assert norm1 == norm2 == "https://example.com/artikel?id=123"


def test_scrape_url_blocks_dns_rebinding_ssrf_and_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    # 1. DNS Rebinding zu Loopback
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, family=0, socktype=0: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))
        ],
    )
    res_local = ai_web_search_service.scrape_url("http://rebind.attacker.com/secret")
    assert res_local.get("error") == "invalid_or_private_url"

    # 2. Redirect zu privater IP wird blockiert
    calls = []

    def fake_redirect_get(url, headers=None, follow_redirects=False):
        calls.append(url)
        if url == "https://example.com/redirect-to-internal":
            return httpx.Response(
                302,
                headers={"Location": "http://127.0.0.1:15499/internal"},
                request=httpx.Request("GET", url),
            )
        return httpx.Response(200, text="OK", request=httpx.Request("GET", url))

    class FakeClient:
        get = staticmethod(fake_redirect_get)

    monkeypatch.setattr(ai_web_search_service, "_http_client", lambda: FakeClient())
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, family=0, socktype=0: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 80))
        ],
    )
    res_redir = ai_web_search_service.scrape_url("https://example.com/redirect-to-internal")
    assert res_redir.get("error") == "invalid_or_private_url"


def test_convert_tables_to_markdown() -> None:
    # 1. Standard-Tabelle mit Header
    html_tab = """
    <div>
        <h3>GPU Benchmarks</h3>
        <table>
            <thead>
                <tr><th>GPU</th><th>TimeSpy (Punkte)</th><th>FPS 4K</th></tr>
            </thead>
            <tbody>
                <tr><td>RTX 4090</td><td>19500</td><td>115.4</td></tr>
                <tr><td>RX 7900 XTX</td><td>16800</td><td>98.2</td></tr>
            </tbody>
        </table>
    </div>
    """
    clean = ai_web_search_service.html_to_clean_text(html_tab)
    assert "| GPU | TimeSpy (Punkte) | FPS 4K |" in clean
    assert "| --- | --- | --- |" in clean
    assert "| RTX 4090 | 19500 | 115.4 |" in clean
    assert "| RX 7900 XTX | 16800 | 98.2 |" in clean

    # 2. Verschachtelte Tabelle und Pipes im Zelleninhalt
    nested_html = """
    <table>
        <tr><th>Kategorie</th><th>Details | Optionen</th></tr>
        <tr><td>Modelle</td><td><table><tr><th>Sub</th></tr><tr><td>Sub-Val</td></tr></table></td></tr>
    </table>
    """
    clean_nested = ai_web_search_service.html_to_clean_text(nested_html)
    assert "| Kategorie | Details \\| Optionen |" in clean_nested
    assert "Sub-Val" in clean_nested


def test_convert_definition_lists() -> None:
    html_dl = """
    <dl>
        <dt>Architektur</dt>
        <dd>Ada Lovelace</dd>
        <dt>TDP</dt>
        <dd>450 Watt</dd>
    </dl>
    """
    clean = ai_web_search_service.html_to_clean_text(html_dl)
    assert "- **Architektur**: Ada Lovelace" in clean
    assert "- **TDP**: 450 Watt" in clean

    # Unclosed <dt> and <dd> tags as common in HTML5
    html_dl_unclosed = """
    <dl>
        <dt>Architektur<dd>Ada Lovelace
        <dt>TDP<dd>450 Watt
    </dl>
    """
    clean_unclosed = ai_web_search_service.html_to_clean_text(html_dl_unclosed)
    assert "- **Architektur**: Ada Lovelace" in clean_unclosed
    assert "- **TDP**: 450 Watt" in clean_unclosed


def test_json_ld_schema_org_extraction() -> None:
    html_doc = """
    <html>
        <head>
            <script type="application/ld+json">
            {
                "@context": "https://schema.org",
                "@type": "Product",
                "name": "Nvidia GeForce RTX 4090",
                "description": "High-End Gaming-Grafikkarte",
                "aggregateRating": {
                    "@type": "AggregateRating",
                    "ratingValue": 4.9,
                    "reviewCount": 85
                },
                "offers": {
                    "@type": "Offer",
                    "price": "1899.00",
                    "priceCurrency": "EUR"
                },
                "additionalProperty": [
                    {"name": "VRAM", "value": "24 GB GDDR6X"},
                    {"name": "TDP", "value": "450 W"}
                ]
            }
            </script>
        </head>
        <body>
            <p>Ein kurzer Artikelueberblick.</p>
        </body>
    </html>
    """
    json_ld = ai_web_search_service._extract_json_ld_and_meta(html_doc)
    assert "**Product: Nvidia GeForce RTX 4090**" in json_ld
    assert "- Wertung: 4.9/5 (85 Bewertungen)" in json_ld
    assert "- Preis: 1899.00 EUR" in json_ld
    assert "- **VRAM**: 24 GB GDDR6X" in json_ld
    assert "- **TDP**: 450 W" in json_ld


def test_xml_content_parsing() -> None:
    # 1. RSS Feed
    rss_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
        <channel>
            <title>Tech Benchmarks RSS</title>
            <description>Aktuelle Hardware-News</description>
            <item>
                <title>RTX 5090 Test veroeffentlicht</title>
                <link>https://example.com/rtx5090</link>
                <description>Enorme Performance-Steigerung gemessen.</description>
            </item>
        </channel>
    </rss>
    """
    res_rss = ai_web_search_service._parse_xml_content(rss_xml)
    assert res_rss["title"] == "Tech Benchmarks RSS"
    assert "RTX 5090 Test veroeffentlicht" in res_rss["content"]
    assert "https://example.com/rtx5090" in res_rss["content"]

    # 2. Generisches Benchmark XML
    bench_xml = """<benchmarks>
        <gpu name="RTX 4090">
            <timespy>19500</timespy>
            <power>450W</power>
        </gpu>
    </benchmarks>
    """
    res_bench = ai_web_search_service._parse_xml_content(bench_xml)
    assert "- **timespy**: 19500" in res_bench["content"]
    assert "- **power**: 450W" in res_bench["content"]


def test_youtube_video_id_and_content_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    # 1. Video-ID Erkennung
    assert ai_web_search_service._extract_youtube_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert ai_web_search_service._extract_youtube_video_id("https://youtu.be/dQw4w9WgXcQ?t=42") == "dQw4w9WgXcQ"
    assert ai_web_search_service._extract_youtube_video_id("https://www.youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    # 2. Mocking YouTube Extraction mit PlayerResponse und Timedtext
    fake_player_response = {
        "videoDetails": {
            "title": "RTX 4090 vs RX 7900 XTX Benchmark",
            "author": "Hardware Channel",
            "lengthSeconds": "630",
            "shortDescription": "00:00 Intro\n01:30 4K Benchmarks\nTest-Setup: Ryzen 7800X3D",
        },
        "captions": {
            "playerCaptionsTracklistRenderer": {
                "captionTracks": [
                    {
                        "languageCode": "de",
                        "baseUrl": "https://www.youtube.com/api/timedtext?v=fake_de",
                    }
                ]
            }
        },
    }
    html_page = f"""
    <html>
        <script>var ytInitialPlayerResponse = {json.dumps(fake_player_response)};</script>
    </html>
    """
    timedtext_xml = """<?xml version="1.0" encoding="utf-8" ?>
    <transcript>
        <text dur="2.5" start="0.0">Willkommen zum grossen GPU Vergleich.</text>
        <text dur="3.0" id="1" start="90.0">Hier sehen wir die 4K FPS Ergebnisse.</text>
    </transcript>
    """

    def fake_get(url, headers=None):
        if "timedtext" in url:
            return httpx.Response(200, text=timedtext_xml)
        return httpx.Response(200, text=html_page)

    class FakeClient:
        get = staticmethod(fake_get)

    monkeypatch.setattr(ai_web_search_service, "_http_client", lambda: FakeClient())

    scraped = ai_web_search_service._extract_youtube_content("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert "[YouTube-Video & Transkript]: RTX 4090 vs RX 7900 XTX Benchmark" in scraped["content"]
    assert "**Kanal / Ersteller**: Hardware Channel" in scraped["content"]
    assert "01:30 4K Benchmarks" in scraped["content"]
    assert "Willkommen zum grossen GPU Vergleich." in scraped["content"]
    assert "Hier sehen wir die 4K FPS Ergebnisse." in scraped["content"]


def test_scrape_url_deep_and_effective_max(monkeypatch: pytest.MonkeyPatch) -> None:
    grosser_text = "Benchmark Zahl: " + ("123456789 " * 3000)  # > 27.000 Zeichen
    html_content = f"<html><title>Testseite</title><body><p>{grosser_text}</p></body></html>"

    def fake_get(url, headers=None, follow_redirects=False):
        return httpx.Response(200, text=html_content, headers={"Content-Type": "text/html"})

    class FakeClient:
        get = staticmethod(fake_get)

    monkeypatch.setattr(ai_web_search_service, "_http_client", lambda: FakeClient())
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, family=0, socktype=0: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 80))
        ],
    )

    # 1. Standard-Scrape (max_chars = 3500)
    normal = ai_web_search_service.scrape_url("https://example.com/test", max_chars=3500, deep=False)
    assert len(normal["content"]) <= 3500

    # 2. Deep-Scrape (deep = True -> bis zu 25.000 Zeichen)
    deep_res = ai_web_search_service.scrape_url("https://example.com/test", max_chars=3500, deep=True)
    assert len(deep_res["content"]) > 15000
    assert len(deep_res["content"]) <= 25000



