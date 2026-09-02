"""Sicherheits- und Grenztests für rollenbasierte KI-Kontingente.

Am Ende steht zusätzlich ein Vertragsteil, der die Einstellungsmaske liest. Er
gehört hierher und nicht ins Frontend: die beiden Zahlen, um die es geht,
``MAX_SYSTEM_SCOPE_ENTRIES`` und ``MAX_MEMORY_ENTRIES_MAX``, stehen im Backend,
und nur von hier aus sind Konstante und Oberfläche gleichzeitig zu sehen. Das
Vorgehen ist dasselbe wie in ``test_permission_catalog_ui_contract.py`` und
``test_ai_tool_label_contract.py`` — Locale-Dateien als JSON, die TSX-Quelle als
Text mit einem eng gefassten regulären Ausdruck.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4
import json
import re

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import BigInteger
from sqlalchemy.orm import Session

from models import (
    AiMemoryEntry,
    AiUsageEvent,
    AuditLog,
    Role,
    RoleAiLimit,
    RolePermission,
    Server,
    User,
)
from services import ai_action_service, ai_memory_service, team_service
from services.ai_action_errors import AiActionValidationError
from services.ai_limit_service import (
    LIMIT_FIELDS,
    LIMIT_MAXIMA,
    MAX_MEMORY_ENTRIES_MAX,
    MAX_SYSTEM_SCOPE_ENTRIES,
    resolve_effective_limits,
    resolve_scope_memory_limit,
    set_role_limit,
)
from services.auth_service import AuthService
from services.role_service import set_user_roles
from services.ai_usage_service import (
    AiQuotaExceeded,
    AiUsageConflict,
    complete_ai_usage,
    fail_ai_usage,
    reserve_ai_usage,
)


def _limits(**overrides: int | None) -> dict[str, int | None]:
    """Erzeugt ein vollständiges, standardmäßig gesperrtes Limit-Set."""
    values: dict[str, int | None] = {field: 0 for field in LIMIT_FIELDS}
    values.update(overrides)
    return values


def _role(db: Session, name: str) -> Role:
    """Legt eine isolierte Testrolle ohne implizite Rechte an."""
    role = Role(name=name, description=None, is_system=False)
    db.add(role)
    db.commit()
    db.refresh(role)
    return role


def _csrf(cookies: dict) -> dict[str, str]:
    return {"X-CSRF-Token": cookies.get("__Secure-csrf_token", "")}


def test_missing_configuration_resolves_to_unlimited(
    db: Session,
    regular_user: User,
) -> None:
    """Ohne jede Rollenkonfiguration gilt unbegrenzt, nicht gesperrt.

    Regression: frueher ergab die leere Zeilenmenge ueber ``max(default=0)``
    ein Limit von 0. Damit brach jede Anfrage auf einer frischen Installation
    mit ``ai.errors.quota`` ab, obwohl der Betreiber gar keine Grenze gesetzt
    hatte. Die Zugangsgrenze ist ``ai.chat.use``, nicht ein stilles Nulllimit.
    """
    role = _role(db, "ai-unconfigured")
    set_user_roles(db, regular_user, [role.id])

    effective = resolve_effective_limits(db, regular_user)

    assert all(getattr(effective, field) is None for field in LIMIT_FIELDS)
    # Und die Reservierung laeuft dann auch wirklich durch.
    event = reserve_ai_usage(
        db,
        regular_user,
        request_id=uuid4(),
        estimated_tokens=5_000,
    )
    assert event.status == "reserved"


def test_unconfigured_role_does_not_lift_a_configured_one(
    db: Session,
    regular_user: User,
) -> None:
    """Eine zusaetzliche Rolle ohne Konfiguration hebt kein Limit auf.

    Nur der voellig unkonfigurierte Fall bedeutet „unbegrenzt“. Sobald *eine*
    Rolle Werte traegt, tragen die uebrigen nichts bei — sonst waere jede neu
    angelegte Rolle ein stiller Freibrief.
    """
    limited = _role(db, "ai-limited-role")
    blank = _role(db, "ai-blank-role")
    set_role_limit(db, limited.id, _limits(daily_token_limit=100))
    db.commit()
    set_user_roles(db, regular_user, [limited.id, blank.id])

    effective = resolve_effective_limits(db, regular_user)

    assert effective.daily_token_limit == 100
    assert effective.requests_per_minute == 0


def test_unconfigured_role_is_listed_as_unlimited_not_zero(
    client: TestClient,
    db: Session,
    owner_cookies: dict,
) -> None:
    """Die Einstellungsansicht zeigt „nicht konfiguriert“ als unbegrenzt.

    Vorher stand dort 0. Ein unbeabsichtigtes Speichern haette die Rolle damit
    hart gesperrt, obwohl der angezeigte Zustand nie gespeichert worden war.
    """
    role = _role(db, "ai-listed-unconfigured")

    listed = client.get("/api/ai/settings/role-limits", cookies=owner_cookies)

    row = next(item for item in listed.json() if item["role_id"] == role.id)
    assert row["configured"] is False
    assert all(row[field] is None for field in LIMIT_FIELDS)


def test_service_rejects_invalid_internal_limit_values(db: Session) -> None:
    """Auch interne Aufrufer können die API-Validierung nicht umgehen."""
    role = _role(db, "ai-invalid-internal")
    with pytest.raises(ValueError, match="daily_token_limit"):
        set_role_limit(db, role.id, _limits(daily_token_limit=True))
    with pytest.raises(ValueError, match="requests_per_minute"):
        set_role_limit(db, role.id, _limits(requests_per_minute=10_001))


def test_erlaubte_maxima_passen_in_die_spaltenbreite() -> None:
    """Kein erlaubtes Maximum darf breiter sein als die Spalte, die es aufnimmt.

    Die Tests laufen auf SQLite, wo INTEGER acht Byte hat — dort fällt ein zu
    großes Maximum nie auf. In Produktion (PostgreSQL) endet INTEGER bei
    2^31-1, und ein darüber liegender Wert bricht erst beim Speichern ab.
    """
    for feld, maximum in LIMIT_MAXIMA.items():
        spalte = RoleAiLimit.__table__.columns[feld]
        grenze = 2**63 - 1 if isinstance(spalte.type, BigInteger) else 2**31 - 1
        assert maximum <= grenze, f"{feld} erlaubt {maximum}, die Spalte fasst nur {grenze}"


def test_highest_limit_and_explicit_unlimited_win(
    db: Session,
    regular_user: User,
) -> None:
    """Mehrere Rollen addieren Verbrauch nicht; sie wählen genau eine Grenze."""
    standard = _role(db, "ai-standard")
    vip = _role(db, "ai-vip-limits")
    set_role_limit(
        db,
        standard.id,
        _limits(daily_token_limit=1_000, requests_per_minute=5),
    )
    set_role_limit(
        db,
        vip.id,
        _limits(daily_token_limit=10_000, requests_per_minute=None),
    )
    db.commit()
    set_user_roles(db, regular_user, [standard.id, vip.id])

    effective = resolve_effective_limits(db, regular_user)

    assert effective.daily_token_limit == 10_000
    assert effective.requests_per_minute is None
    assert effective.weekly_token_limit == 0


def test_owner_updates_limits_atomically_and_audited(
    client: TestClient,
    db: Session,
    owner_cookies: dict,
) -> None:
    """Settings-Write speichert ein Vollset und erzeugt korrelierbares Audit."""
    role = _role(db, "ai-api-role")
    payload = _limits(
        daily_token_limit=25_000,
        weekly_token_limit=100_000,
        monthly_token_limit=None,
        requests_per_minute=20,
        concurrent_operations=2,
        monthly_cost_limit_cents=5_000,
    )

    response = client.put(
        f"/api/ai/settings/role-limits/{role.id}",
        json=payload,
        cookies=owner_cookies,
        headers=_csrf(owner_cookies),
    )

    assert response.status_code == 200
    assert response.json()["monthly_token_limit"] is None
    audit = (
        db.query(AuditLog)
        .filter(AuditLog.action == "ai.role_limits.updated")
        .one()
    )
    assert audit.origin == "direct"
    assert str(UUID(audit.correlation_id or "")) == audit.correlation_id
    assert "25000" not in (audit.details or "")


def test_update_rejects_partial_negative_and_extreme_payloads(
    client: TestClient,
    db: Session,
    owner_cookies: dict,
) -> None:
    """Fehlende, negative und übergroße Werte scheitern vor der DB-Mutation."""
    role = _role(db, "ai-invalid-limits")
    cases = [
        {"daily_token_limit": 1},
        _limits(daily_token_limit=-1),
        _limits(requests_per_minute=10_001),
    ]

    for payload in cases:
        response = client.put(
            f"/api/ai/settings/role-limits/{role.id}",
            json=payload,
            cookies=owner_cookies,
            headers=_csrf(owner_cookies),
        )
        assert response.status_code == 422

    listed = client.get(
        "/api/ai/settings/role-limits",
        cookies=owner_cookies,
    )
    row = next(item for item in listed.json() if item["role_id"] == role.id)
    assert row["configured"] is False


def test_role_limit_settings_require_backend_permissions_and_csrf(
    client: TestClient,
    db: Session,
    regular_user: User,
    user_cookies: dict,
    owner_cookies: dict,
) -> None:
    """Frontend-Sichtbarkeit kann weder RBAC noch CSRF ersetzen."""
    role = _role(db, "ai-rbac-limits")
    payload = _limits(daily_token_limit=100)

    forbidden = client.get(
        "/api/ai/settings/role-limits",
        cookies=user_cookies,
    )
    no_csrf = client.put(
        f"/api/ai/settings/role-limits/{role.id}",
        json=payload,
        cookies=owner_cookies,
    )

    assert forbidden.status_code == 403
    assert no_csrf.status_code == 403


def test_effective_limits_endpoint_returns_union_rule(
    client: TestClient,
    db: Session,
    regular_user: User,
    user_cookies: dict,
) -> None:
    """Der Client erhält nur das bereits backendseitig aufgelöste Ergebnis."""
    role = _role(db, "ai-own-limits")
    db.add(RolePermission(role_id=role.id, permission_key="ai.chat.use"))
    db.commit()
    set_role_limit(db, role.id, _limits(daily_token_limit=321))
    db.commit()
    set_user_roles(db, regular_user, [role.id])

    response = client.get("/api/ai/limits/me", cookies=user_cookies)

    assert response.status_code == 200
    assert response.json()["daily_token_limit"] == 321
    assert response.json()["role_ids"] == [role.id]


def _enable_usage(db: Session, user: User, role: Role, **overrides: int | None) -> None:
    """Gibt einem Testuser eine vollständig nutzbare, gezielt überschreibbare Quote."""
    values = _limits(
        daily_token_limit=10_000,
        weekly_token_limit=10_000,
        monthly_token_limit=10_000,
        requests_per_minute=10,
        concurrent_operations=2,
        monthly_cost_limit_cents=100,
    )
    values.update(overrides)
    set_role_limit(
        db,
        role.id,
        values,
    )
    db.commit()
    set_user_roles(db, user, [role.id])


def test_usage_reservation_counts_retry_exactly_once(
    db: Session,
    regular_user: User,
) -> None:
    """Dieselbe Request-ID liefert dieselbe Zeile und verbraucht keine zweite Quote."""
    role = _role(db, "ai-idempotent-usage")
    _enable_usage(db, regular_user, role)
    request_id = uuid4()

    first = reserve_ai_usage(
        db,
        regular_user,
        request_id=request_id,
        estimated_tokens=500,
        estimated_cost_microunits=10_000,
    )
    db.commit()
    second = reserve_ai_usage(
        db,
        regular_user,
        request_id=request_id,
        estimated_tokens=500,
        estimated_cost_microunits=10_000,
    )

    assert second.id == first.id
    assert db.query(AiUsageEvent).count() == 1

    complete_ai_usage(
        db,
        first,
        actual_tokens=450,
        actual_cost_microunits=9_000,
    )
    db.commit()
    completed_retry = reserve_ai_usage(
        db,
        regular_user,
        request_id=request_id,
        estimated_tokens=500,
        estimated_cost_microunits=10_000,
    )
    assert completed_retry.id == first.id
    assert completed_retry.accounted_tokens == 450


def test_usage_reservation_enforces_tokens_rpm_and_concurrency(
    db: Session,
    regular_user: User,
) -> None:
    """Alle synchron prüfbaren Limits greifen vor einem späteren Provider-Aufruf."""
    role = _role(db, "ai-enforced-usage")
    _enable_usage(
        db,
        regular_user,
        role,
        daily_token_limit=100,
        weekly_token_limit=100,
        monthly_token_limit=100,
        requests_per_minute=1,
        concurrent_operations=1,
    )
    now = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    event = reserve_ai_usage(
        db,
        regular_user,
        request_id=uuid4(),
        estimated_tokens=80,
        now=now,
    )
    db.commit()

    with pytest.raises(AiQuotaExceeded, match="requests_per_minute"):
        reserve_ai_usage(
            db,
            regular_user,
            request_id=uuid4(),
            estimated_tokens=1,
            now=now,
        )

    complete_ai_usage(db, event, actual_tokens=80, actual_cost_microunits=0, now=now)
    db.commit()
    with pytest.raises(AiQuotaExceeded, match="daily_token_limit"):
        reserve_ai_usage(
            db,
            regular_user,
            request_id=uuid4(),
            estimated_tokens=21,
            now=now + timedelta(minutes=2),
        )


def test_failed_usage_frees_reservation_and_conflicting_retry_is_rejected(
    db: Session,
    regular_user: User,
) -> None:
    """Fehler erfinden keinen Verbrauch; UUID-Reuse mit anderer Payload bleibt verboten."""
    role = _role(db, "ai-failed-usage")
    _enable_usage(db, regular_user, role, concurrent_operations=1)
    request_id = uuid4()
    event = reserve_ai_usage(
        db,
        regular_user,
        request_id=request_id,
        estimated_tokens=50,
    )
    with pytest.raises(AiQuotaExceeded, match="concurrent_operations"):
        reserve_ai_usage(
            db,
            regular_user,
            request_id=uuid4(),
            estimated_tokens=1,
        )
    fail_ai_usage(db, event)
    db.commit()

    assert event.status == "failed"
    assert event.accounted_tokens == 0
    replacement = reserve_ai_usage(
        db,
        regular_user,
        request_id=uuid4(),
        estimated_tokens=1,
    )
    assert replacement.status == "reserved"
    with pytest.raises(AiUsageConflict):
        reserve_ai_usage(
            db,
            regular_user,
            request_id=request_id,
            estimated_tokens=51,
        )


def _memory_role(
    db: Session,
    user: User,
    name: str,
    entries: int | None,
    *permissions: str,
) -> Role:
    """Gibt einem Benutzer genau eine Rolle mit genau diesem Memory-Vorrat.

    Die uebrigen Felder stehen ueber `_limits` auf 0 und bleiben es: beim
    Merken wird kein Kontingent gezaehlt, dort entscheidet allein
    ``max_memory_entries``.
    """
    role = _role(db, name)
    for key in permissions:
        db.add(RolePermission(role_id=role.id, permission_key=key))
    set_role_limit(db, role.id, _limits(max_memory_entries=entries))
    db.commit()
    set_user_roles(db, user, [role.id])
    return role


def _zwei_memory_rollen(
    db: Session,
    user: User,
    name: str,
    erste: int | None,
    zweite: int | None,
) -> tuple[Role, Role]:
    """Gibt einem Benutzer zwei Rollen, die sich nur im Memory-Vorrat unterscheiden.

    Die Reihenfolge ist Absicht und keine Bequemlichkeit: `zweite` ist die
    *zusaetzliche* Rolle, um die es in den Tests darunter geht — die, die
    erhoehen darf und nie etwas wegnehmen. Wer die beiden Zahlen vertauscht,
    prueft eine andere Zusage.
    """
    eine = _role(db, f"{name}-a")
    andere = _role(db, f"{name}-b")
    set_role_limit(db, eine.id, _limits(max_memory_entries=erste))
    set_role_limit(db, andere.id, _limits(max_memory_entries=zweite))
    db.commit()
    set_user_roles(db, user, [eine.id, andere.id])
    return eine, andere


#: Sachlich verschiedene Inhalte fuer die Testeintraege.
#:
#: Gebraucht seit der Duplikatpruefung (`ai_memory_service.aehnlicher_eintrag`,
#: 19.08.2026): stuenden in allen Eintraegen Varianten desselben Satzes, wuerde
#: der naechste Aufruf an der Aehnlichkeit scheitern statt an der Mengengrenze,
#: um die es hier geht. Die Themen sind bewusst weit auseinander.
_THEMEN = (
    "Startzeit liegt bei vier Minuten.",
    "Der Kartenwechsel braucht eine Bestaetigung.",
    "Mods werden nur sonntags aktualisiert.",
    "Die Zeitzone steht auf Europe/Berlin.",
    "Zwoelf Spielerplaetze sind vergeben.",
    "Backups laufen um drei Uhr nachts.",
    "Der Weltordner heisst Nordkueste.",
    "Sprachchat ist abgeschaltet.",
    "RCON hoert auf einem eigenen Port.",
    "Die Whitelist wird von Hand gepflegt.",
)


def _merken(db: Session, user: User, key: str, **bezug: int | None) -> None:
    """Legt einen Eintrag ueber denselben Weg an, den auch die KI nimmt.

    Der Wert traegt den Schluessel **und** einen sachlich anderen Inhalt. Bis
    zum 19.08.2026 stand hier schlicht ``f"Wert zu {key}"`` — mit der neuen
    Duplikatpruefung (`aehnlicher_eintrag`) waeren `notiz.0` bis `notiz.6`
    damit sieben Fassungen desselben Satzes, und der achte Aufruf scheiterte
    an der Aehnlichkeit statt an der Mengengrenze, um die es diesen Tests
    geht. Verschiedene Themen halten die Faelle auseinander.

    Der Index kommt aus einer **stabilen** Zeichensumme und nicht aus
    ``hash()``: Pythons Zeichenketten-Hash ist je Prozess zufaellig
    (PYTHONHASHSEED), und ein Test, der mal so und mal anders zuordnet, ist
    schlimmer als gar keiner.
    """
    thema = _THEMEN[sum(key.encode()) % len(_THEMEN)]
    ai_memory_service.upsert_entry(
        db, user=user, key=key, value=f"{key}: {thema}",
        scope=bezug.pop("scope", "user"), server_id=bezug.pop("server_id", None),
        **bezug,
    )


def _server(db: Session, name: str) -> Server:
    """Eine Anlage, an der eine serverbezogene Notiz haengen kann."""
    server = Server(
        name=name, game_type="dayz", install_dir=f"/tmp/{name}", status="stopped"
    )
    db.add(server)
    db.commit()
    db.refresh(server)
    return server


def test_memory_limit_kommt_aus_der_rolle(db: Session, regular_user: User) -> None:
    """Wieviel sich die KI merken darf, verkauft der Tarif — nicht der Code.

    Und der Tarif nennt eine Zahl, keinen Gesamtvorrat: dieselbe Grenze gilt in
    jedem Bereich noch einmal. Ein volles Serverwissen sperrt deshalb weder die
    naechste Anlage noch das persoenliche Gedaechtnis.
    """
    _memory_role(db, regular_user, "ai-memory-single", 2, "server.view")
    erster = _server(db, "vorrat-a")
    zweiter = _server(db, "vorrat-b")

    assert resolve_effective_limits(db, regular_user).max_memory_entries == 2
    assert resolve_scope_memory_limit(db, "user", regular_user) == 2
    # Dieselbe Zahl auch fuer den Serverbereich — aber ausdruecklich **nicht**
    # derselbe Vorrat, so stand es hier vorher als Kommentar. `scope_identity`
    # bildet fuer `server` die Kennung `server:{sid}:user:{uid}`; jede Anlage
    # hat damit ihre eigene Kasse. Der blosse Vergleich der Rueckgabewerte
    # belegt das nicht — bei einem gemeinsamen Vorrat saehe er genauso aus.
    # Deshalb wird hier wirklich gefuellt.
    assert resolve_scope_memory_limit(
        db, "server", regular_user, server_id=erster.id
    ) == 2

    for nummer in range(2):
        _merken(db, regular_user, f"notiz.{nummer}", scope="server", server_id=erster.id)
    with pytest.raises(HTTPException) as exc:
        _merken(db, regular_user, "notiz.2", scope="server", server_id=erster.id)
    assert exc.value.status_code == 409
    db.rollback()

    # Der zweite Server merkt trotzdem, und das persoenliche Gedaechtnis auch.
    _merken(db, regular_user, "notiz.0", scope="server", server_id=zweiter.id)
    _merken(db, regular_user, "notiz.0")

    assert db.query(AiMemoryEntry).filter(
        AiMemoryEntry.scope_identity == f"server:{zweiter.id}:user:{regular_user.id}"
    ).count() == 1
    assert db.query(AiMemoryEntry).filter(
        AiMemoryEntry.scope_identity == f"user:{regular_user.id}"
    ).count() == 1


def test_eine_leere_zusatzrolle_nimmt_dem_vorrat_nichts_weg(
    db: Session,
    regular_user: User,
) -> None:
    """Zwei Rollen ergeben eine Grenze — und die zweite kann nur erhoehen.

    Hier stand vorher {10, leer} -> 100 als Sollverhalten, also der Fehler
    selbst: eine Rolle mit leerem Feld verdraengte den Wert der anderen. In
    *dieser* Richtung faellt das nicht auf, weil die Zahl dabei zufaellig
    steigt. Wehtun tut der Fehler andersherum, und so ist der Test jetzt
    gebaut: den hoeheren Wert traegt die zweite Rolle. Ein VIP mit 800
    bekommt zusaetzlich eine Bestandsrolle, deren Feld die Migration auf NULL
    gesetzt hat — bliebe der alte Stand, haette ihm diese zusaetzliche Rolle
    700 Eintraege *genommen*. Das ist das Gegenteil der Zusage aus dem
    Moduldocstring von `ai_limit_service`.

    Ein leeres Feld heisst beim Gedaechtnis deshalb weder „unbegrenzt“ noch
    „100“, sondern „diese Rolle sagt zum Vorrat nichts“ — und traegt so viel
    bei wie eine Rolle ganz ohne Zeile: nichts.
    """
    knapp, _ = _zwei_memory_rollen(db, regular_user, "ai-memory-hoechster", 10, 800)

    assert resolve_scope_memory_limit(db, "user", regular_user) == 800

    set_role_limit(db, knapp.id, _limits(max_memory_entries=None))
    db.commit()

    assert resolve_effective_limits(db, regular_user).max_memory_entries == 800
    assert resolve_scope_memory_limit(db, "user", regular_user) == 800


def test_eine_ausdrueckliche_null_haelt_gegen_eine_leere_rolle(
    db: Session,
    regular_user: User,
) -> None:
    """Wer das Gedaechtnis abschaltet, hat es abgeschaltet.

    Die 0 ist eine Ansage — „diese Rolle darf sich nichts merken“ —, das leere
    Feld daneben sagt gar nichts. Vorher gewann das Nichts: aus {0, leer} wurde
    die Systemgrenze, der Betreiber sah seine Sperre in der Maske stehen und
    die KI merkte munter weiter. Eine Sperre, die jede beliebige zweite Rolle
    aufhebt, ist keine.
    """
    _zwei_memory_rollen(db, regular_user, "ai-memory-null-gegen-leer", 0, None)

    assert resolve_effective_limits(db, regular_user).max_memory_entries == 0
    assert resolve_scope_memory_limit(db, "user", regular_user) == 0
    # Und die Sperre wird auch durchgesetzt, nicht nur aufgeloest.
    with pytest.raises(HTTPException) as exc:
        _merken(db, regular_user, "notiz.0")
    assert exc.value.status_code == 409
    db.rollback()


def test_eine_zusaetzliche_rolle_erhoeht_den_vorrat(
    db: Session,
    regular_user: User,
) -> None:
    """Unter den konfigurierten Rollen gewinnt der hoechste Vorrat.

    Das ist die andere Haelfte der Zusage: eine Sperre bleibt nicht kleben,
    sobald der Betreiber dem Benutzer eine Rolle gibt, die mehr erlaubt. Sonst
    waere die 0 einer beliebigen Bestandsrolle ein Riegel, den kein Tarif mehr
    aufbekommt.
    """
    _zwei_memory_rollen(db, regular_user, "ai-memory-erhoeht", 0, 500)

    assert resolve_effective_limits(db, regular_user).max_memory_entries == 500
    assert resolve_scope_memory_limit(db, "user", regular_user) == 500


def test_schweigen_aller_rollen_wird_erst_beim_merken_zu_einer_zahl(
    db: Session,
    regular_user: User,
) -> None:
    """„Nichts hinterlegt“ und „100“ sind zwei Aussagen; nur die erste ist wahr.

    Sagt keine der Rollen etwas zum Vorrat, bleibt die rohe Aufloesung
    ``None`` — genau das leere Feld, das der Betreiber in der Maske sieht. Erst
    beim Merken wird daraus ``MAX_SYSTEM_SCOPE_ENTRIES``. Geprueft werden
    beide Seiten getrennt, weil sie verschiedene Fragen beantworten: wer die
    Maske gegen die Durchsetzung haelt, liest dort ein „unbegrenzt“ heraus, das
    nie jemand eingetragen hat.
    """
    _zwei_memory_rollen(db, regular_user, "ai-memory-stumm", None, None)

    assert resolve_effective_limits(db, regular_user).max_memory_entries is None
    assert resolve_scope_memory_limit(
        db, "user", regular_user
    ) == MAX_SYSTEM_SCOPE_ENTRIES


def test_die_sonderregel_des_vorrats_faerbt_nicht_auf_die_kontingente_ab(
    db: Session,
    regular_user: User,
) -> None:
    """Nur der Memory-Vorrat liest ein leeres Feld als Schweigen.

    Bei den Kontingenten ist ein leeres Feld weiterhin selbst ein Wert,
    naemlich „unbegrenzt“ — und damit der hoechste, der gewinnt. Haette die
    Korrektur am Vorrat sie mitgerissen, waere aus einem ausdruecklich
    unbegrenzten Tageslimit still die Zahl der Nachbarrolle geworden: eine
    Verschaerfung, die kein Betreiber eingetragen hat. Beide Lesarten stehen
    hier absichtlich in *einer* Zeilenmenge nebeneinander.
    """
    offen = _role(db, "ai-memory-nebenwirkung-offen")
    begrenzt = _role(db, "ai-memory-nebenwirkung-begrenzt")
    set_role_limit(
        db, offen.id, _limits(daily_token_limit=None, max_memory_entries=800)
    )
    set_role_limit(
        db, begrenzt.id, _limits(daily_token_limit=1_000, max_memory_entries=None)
    )
    db.commit()
    set_user_roles(db, regular_user, [offen.id, begrenzt.id])

    effective = resolve_effective_limits(db, regular_user)

    assert effective.daily_token_limit is None
    assert effective.max_memory_entries == 800


def test_ohne_konfigurierte_rolle_gilt_weiter_die_alte_feste_grenze(
    db: Session,
    regular_user: User,
) -> None:
    """Diese Aenderung nimmt niemandem etwas und gibt niemandem etwas.

    Nach der Migration traegt **jede** Bestandsrolle NULL, und eine frische
    Installation hat gar keine Rollenkonfiguration. Genau dort muss weiterhin
    die Grenze gelten, die bis eben als Konstante im Memory-Service stand —
    sonst waere aus einer konfigurierbaren Grenze auf jeder Anlage still eine
    fehlende geworden. Der Leseweg haette das ausgebadet: er laedt alle
    sichtbaren Zeilen ohne LIMIT und entschluesselt jede einzeln ueber den
    DIS-Sidecar, bei jeder Chatanfrage.

    Die Einstellungsmaske zeigt daneben unveraendert ein leeres Feld. „Nichts
    hinterlegt“ und „100“ sind zwei verschiedene Aussagen; nur die erste ist
    wahr, solange der Betreiber nichts gesetzt hat.
    """
    assert resolve_effective_limits(db, regular_user).max_memory_entries is None
    assert resolve_scope_memory_limit(
        db, "user", regular_user
    ) == MAX_SYSTEM_SCOPE_ENTRIES


def test_ohne_konfigurierte_rolle_greift_die_grenze_auch_beim_merken(
    db: Session,
    regular_user: User,
) -> None:
    """Der unkonfigurierte Fall wird nicht nur aufgeloest, er wird durchgesetzt.

    Die Aufloesung daneben koennte richtig sein und die Zaehlung im
    Memory-Service trotzdem uebersprungen werden — genau so war es einen Stand
    lang, weil ein „unbegrenzt“ die Zaehlung ganz umging. Deshalb prueft dieser
    Test nicht die Zahl, sondern die Absage.
    """
    for nummer in range(MAX_SYSTEM_SCOPE_ENTRIES):
        _merken(db, regular_user, f"notiz.{nummer}")

    with pytest.raises(HTTPException) as exc:
        _merken(db, regular_user, "notiz.zuviel")

    assert exc.value.status_code == 409
    db.rollback()
    assert db.query(AiMemoryEntry).filter(
        AiMemoryEntry.scope_identity == f"user:{regular_user.id}"
    ).count() == MAX_SYSTEM_SCOPE_ENTRIES


def test_teamwissen_haengt_am_gruender_nicht_am_schreiber(
    db: Session,
    regular_user: User,
) -> None:
    """Der Vorrat eines Teams gehoert dem, dem das Team gehoert.

    Waere es der Vorrat des gerade Schreibenden, haette das schwaechste
    Mitglied das Sagen: der Beitritt eines Kunden mit knappem Tarif senkte die
    Grenze eines fremden Teams, sobald er der naechste ist, der etwas merkt.
    Der Test ist deshalb absichtlich andersherum gebaut als die Erwartung —
    der Gruender ist der Knappe, das Mitglied der Grosszuegige.
    """
    gruender = regular_user
    mitglied = AuthService.create_user(
        db, "teamkollege", "teamkollege@test.de", "MgmtPass123!"
    )
    mitglied.email_verified = True
    db.commit()
    db.refresh(mitglied)
    _memory_role(db, gruender, "ai-memory-gruender", 2, "teams.create")
    _memory_role(db, mitglied, "ai-memory-mitglied", 50)

    team = team_service.create_team(db, user=gruender, name="Betrieb")
    team_service.invite_member(
        db, team=team, user=gruender, new_user_id=mitglied.id,
        can_manage_skills=False, can_manage_memory=True,
    )
    team_service.accept_invitation(db, user=mitglied, team_id=team.id)

    # Die beiden Zahlen muessen auseinanderliegen, sonst beweist der Test nichts:
    # nur so waere das naive Verhalten — der Vorrat des Schreibenden — hier rot.
    assert resolve_effective_limits(db, mitglied).max_memory_entries == 50
    assert resolve_scope_memory_limit(db, "team", mitglied, team_id=team.id) == 2

    for nummer in range(2):
        _merken(db, mitglied, f"regel.{nummer}", scope="team", team_id=team.id)

    with pytest.raises(HTTPException) as exc:
        _merken(db, mitglied, "regel.2", scope="team", team_id=team.id)

    assert exc.value.status_code == 409
    db.rollback()
    assert db.query(AiMemoryEntry).filter(
        AiMemoryEntry.scope_identity == f"team:{team.id}"
    ).count() == 2


def test_die_absage_nennt_die_grenze_die_wirklich_gilt(
    db: Session,
    regular_user: User,
) -> None:
    """Ist der Bereich genau voll, stehen Bestand *und* Grenze in der Absage.

    Naehme sie weiterhin die alte feste 100, stuende dort eine Zahl, die
    niemanden mehr betrifft — und das Modell suchte den Fehler bei sich statt
    beim Vorrat. Gemessen wird deshalb gegen ``MAX_SYSTEM_SCOPE_ENTRIES``:
    hier stand vorher ``"100" not in meldung``, ausgerechnet der Test gegen
    eine veraltete Zahl schrieb sie selbst als Literal fest.

    Bestand und Grenze sind in diesem Fall zwangslaeufig dieselbe Zahl. Ein
    ``"4" in meldung`` belegt deshalb nichts: es bliebe auch dann gruen, wenn
    der Satz nur noch den Bestand naennte — genau der Fehler, gegen den dieser
    Test antritt. Geprueft wird darum nicht die Ziffer, sondern die Stelle, an
    der sie steht. Zwei *verschiedene* Zahlen gibt es erst nach einer Senkung;
    dort steht die andere Haelfte dieser Zusage.
    """
    grenze = 4
    _memory_role(db, regular_user, "ai-memory-knapp", grenze)
    for nummer in range(grenze):
        _merken(db, regular_user, f"notiz.{nummer}")

    with pytest.raises(HTTPException) as exc:
        _merken(db, regular_user, "notiz.zuviel")

    meldung = str(exc.value.detail)
    assert exc.value.status_code == 409
    assert f"{grenze} von {grenze} erlaubten" in meldung
    assert str(MAX_SYSTEM_SCOPE_ENTRIES) not in meldung
    # Genau voll heisst „einer muss weichen“, nicht „keiner“: der neue Eintrag
    # will ja auch noch hinein. Eine Meldung, die hier `bestand - grenze`
    # rechnete, schickte das Modell mit „0 weichen“ ohne Loeschung sofort in
    # denselben Fehlschlag zurueck.
    assert "Einer muss weichen" in meldung
    # Werkzeugnamen stehen hier bewusst nicht mehr: derselbe Satz geht ueber
    # `routers/ai_memory.py` als Toast an einen Menschen, der weder
    # `search_memory` noch `forget_memory` hat. Was das *Modell* tun soll, steht
    # an der Naht in `_execute_remember` — und wird weiter unten dort geprueft.
    assert "search_memory" not in meldung and "forget_memory" not in meldung
    db.rollback()


def test_die_absage_nennt_den_bereich_um_den_es_geht(
    db: Session,
    regular_user: User,
) -> None:
    """Jede Absage sagt, *wo* es klemmt — sonst gilt sie fuer alles.

    „Voll“ ohne Bereich liest sich wie „das Gedaechtnis ist voll“ und stimmt
    dann fuer jeden anderen Vorrat des Benutzers nicht. Beim Server ist die
    Nummer der richtige Name: `remember` und `forget_memory` sprechen eine
    Anlage ueber `server_id` an, und `list_my_servers` liefert genau diese
    Nummer. Beim Team ist es umgekehrt — dort gibt es nur den Namen, siehe den
    Test dazu weiter unten.

    Die Warnung, nur im eigenen Bereich zu loeschen, stand frueher hier. Sie
    ist eine Anweisung an das Modell und deshalb an die Naht gewandert; geprueft
    wird sie in `test_die_volle_absage_nennt_dem_modell_beide_werkzeuge`.
    """
    _memory_role(db, regular_user, "ai-memory-bereichsname", 2, "server.view")
    anlage = _server(db, "bereichsname")
    for nummer in range(2):
        _merken(db, regular_user, f"notiz.{nummer}", scope="server", server_id=anlage.id)

    with pytest.raises(HTTPException) as exc:
        _merken(db, regular_user, "notiz.2", scope="server", server_id=anlage.id)

    meldung = str(exc.value.detail)
    assert exc.value.status_code == 409
    assert f"Server {anlage.id}" in meldung
    db.rollback()

    # Der Name folgt wirklich dem Ziel. Ohne diese zweite Haelfte belegt die
    # Nummer oben nichts: ein fest eingebauter Serverbezug saehe genauso aus,
    # und das Modell schickte seine Loeschung weiterhin in den falschen Vorrat.
    for nummer in range(2):
        _merken(db, regular_user, f"notiz.{nummer}")

    with pytest.raises(HTTPException) as exc:
        _merken(db, regular_user, "notiz.2")

    persoenlich = str(exc.value.detail)
    assert "persönliches Gedächtnis" in persoenlich
    assert f"Server {anlage.id}" not in persoenlich
    db.rollback()


def test_die_absage_bei_null_raet_nicht_zum_loeschen(
    db: Session,
    regular_user: User,
) -> None:
    """Wo nichts hineinpasst, ist „raeum auf“ kein Rat, sondern ein Schaden.

    Der Ausweg im Test darueber setzt voraus, dass Platz frei werden *kann*.
    Bei einer Grenze von 0 ist das nicht so: ein Modell, das dem Text folgt,
    loescht der Reihe nach den gesamten Bereich des Benutzers — und scheitert
    danach trotzdem. Deshalb darf hier weder `forget_memory` noch
    `search_memory` stehen, und die Absage muss die Ursache nennen, damit das
    Modell sie dem Benutzer sagen kann statt sie fuer einen eigenen Fehler zu
    halten.

    Dazu die zweite Zusage: derselbe Satz geht ueber `routers/ai_memory.py` als
    Toast an einen Menschen, der gerade selbst einen Eintrag angelegt hat. Er
    liest ihn also **ueber sich** — eine Meldung, die von „dem Benutzer“ in der
    dritten Person spricht, klaenge fuer ihn wie eine Notiz an jemand anderen.
    """
    _memory_role(db, regular_user, "ai-memory-gesperrt", 0)

    with pytest.raises(HTTPException) as exc:
        _merken(db, regular_user, "notiz.0")

    meldung = str(exc.value.detail)
    assert exc.value.status_code == 409
    assert "forget_memory" not in meldung
    assert "search_memory" not in meldung
    assert "freigegeben" in meldung
    assert "Benutzer" not in meldung
    # Auch hier steht das Ziel im Text: „nicht freigegeben“ ohne Bereich liest
    # sich wie „die KI darf sich nichts merken“ und stimmt dann fuer jeden
    # anderen Vorrat des Benutzers nicht.
    assert "persönliches Gedächtnis" in meldung
    db.rollback()
    assert db.query(AiMemoryEntry).count() == 0


def test_die_null_absage_schiebt_es_nicht_auf_den_tarif_des_schreibenden(
    db: Session,
    regular_user: User,
) -> None:
    """Die 0-Absage nennt keinen Tarif — sie kann nicht wissen, wessen es waere.

    Dort stand „dein Tarif enthaelt kein Gedaechtnis fuer diesen Bereich“. Im
    Team ist das nachweislich falsch: die 0 kommt vom Gruender, nicht vom
    Schreibenden. Ein Mitglied mit grosszuegigem eigenem Vorrat hoerte, sein
    Tarif sei schuld — und haette das durch keine Buchung der Welt beheben
    koennen. Der Test ist deshalb wie der Teamtest weiter oben andersherum
    gebaut: der Knappe ist der Gruender.
    """
    gruender = regular_user
    mitglied = AuthService.create_user(
        db, "tarifkollege", "tarifkollege@test.de", "MgmtPass123!"
    )
    mitglied.email_verified = True
    db.commit()
    db.refresh(mitglied)
    _memory_role(db, gruender, "ai-memory-null-gruender", 0, "teams.create")
    _memory_role(db, mitglied, "ai-memory-null-mitglied", 500)

    team = team_service.create_team(db, user=gruender, name="Nullbetrieb")
    team_service.invite_member(
        db, team=team, user=gruender, new_user_id=mitglied.id,
        can_manage_skills=False, can_manage_memory=True,
    )
    team_service.accept_invitation(db, user=mitglied, team_id=team.id)

    # Ohne diese Zeile belegt der Test nichts: erst ein Mitglied, das selbst
    # reichlich darf, macht die Aussage ueber seinen Tarif nachweislich falsch.
    assert resolve_effective_limits(db, mitglied).max_memory_entries == 500

    with pytest.raises(HTTPException) as exc:
        _merken(db, mitglied, "regel.0", scope="team", team_id=team.id)

    meldung = str(exc.value.detail)
    assert exc.value.status_code == 409
    assert "Tarif" not in meldung
    # Und der Bereich, um den es geht, ist das Team — nicht der Vorrat des
    # Mitglieds, in dem noch 500 Plaetze frei sind. Beim Namen und nicht bei
    # der Nummer, warum, steht im Test darunter.
    assert f"Team „{team.name}“" in meldung
    db.rollback()
    assert db.query(AiMemoryEntry).count() == 0


def test_die_absage_nennt_das_team_beim_namen_und_nicht_bei_der_nummer(
    db: Session,
    regular_user: User,
) -> None:
    """Ein volles Team wird so benannt, wie das Modell es ansprechen kann.

    `remember` und `forget_memory` erreichen ein Team ausschliesslich ueber
    `team="<Name>"`, aufgeloest ueber Namensgleichheit; ein Werkzeug, das eine
    Nummer in einen Namen uebersetzt, gibt es nicht. „Team 3“ benennt fuer das
    Modell also nichts, was es ansprechen koennte — und dem Benutzer koennte es
    den vollen Bereich nur als Nummer nennen.

    Der Schaden bliebe nicht beim Nichtstun. Schluessel sind bewusst stabil und
    wiederholen sich ueber Teams hinweg: wer den Bereich nicht trifft, greift
    den gleichnamigen Treffer des falschen Teams und loescht dort. Deshalb ist
    die zweite Haelfte der Zusage ein Verbot — steht im Satz eine Zahl, wo der
    Name hingehoert, ist dieser Test rot.
    """
    _memory_role(db, regular_user, "ai-memory-teamname", 2, "teams.create")
    team = team_service.create_team(db, user=regular_user, name="Nachtschicht")
    for nummer in range(2):
        _merken(db, regular_user, f"regel.{nummer}", scope="team", team_id=team.id)

    with pytest.raises(HTTPException) as exc:
        _merken(db, regular_user, "regel.2", scope="team", team_id=team.id)

    meldung = str(exc.value.detail)
    assert exc.value.status_code == 409
    assert f"Team „{team.name}“" in meldung
    # Nicht `str(team.id) not in meldung`: die Nummer koennte zufaellig mit dem
    # Bestand oder der Grenze zusammenfallen, und der Test waere dann mal streng
    # und mal blind. Gesucht wird die *Stelle* — eine Ziffer da, wo der Name
    # stehen muesste.
    assert re.search(r"Team\s+\d", meldung) is None, meldung
    db.rollback()


def test_die_absage_nennt_ist_und_soll_nach_einer_senkung(
    db: Session,
    regular_user: User,
) -> None:
    """Nach einer Senkung nennt die Absage Bestand, Grenze und die Ueberzaehligen.

    Das ist der Weg, den ein Betreiber wirklich geht: erst grosszuegig, dann
    knapper. Danach steht ein Bereich weit ueber seiner Grenze — und „loesch
    eines und versuch es erneut“ waere eine Anleitung zu so vielen
    Fehlschlaegen, wie der Bereich zu viel hat. Die Meldung nennt deshalb beide
    Zahlen, die dritte, auf die es ankommt (wieviele weichen muessen), und den
    Grund, damit das Modell die Senkung nicht fuer einen eigenen Fehler haelt.

    Zum Loeschen fordert sie trotzdem nicht auf: bei 100 Eintraegen und Grenze
    20 bekaeme das Modell hier sonst einen Auftrag ueber 81 Stueck. Und sie
    spricht ueber niemanden in der dritten Person — warum, steht unten an der
    Zeile, die das prueft.
    """
    rolle = _memory_role(db, regular_user, "ai-memory-gesenkt", 7)
    for nummer in range(7):
        _merken(db, regular_user, f"notiz.{nummer}")

    set_role_limit(db, rolle.id, _limits(max_memory_entries=3))
    db.commit()

    with pytest.raises(HTTPException) as exc:
        _merken(db, regular_user, "notiz.neu")

    meldung = str(exc.value.detail)
    assert exc.value.status_code == 409
    # Der einzige Fall mit zwei *verschiedenen* Zahlen — und deshalb der
    # einzige, in dem sich pruefen laesst, dass jede an ihrer Stelle steht.
    # Ein Satz, der die Grenze verloren hat, faellt hier auf.
    assert "führt 7 Einträge, erlaubt sind 3" in meldung
    assert "nachträglich gesenkt" in meldung
    # 7 - 3 + 1, nicht 7 - 3: der neue Eintrag will ja auch noch hinein. Und
    # ausdruecklich eine Zahl statt „eines“ — sonst zaehlt das Modell die
    # Fehlschlaege einzeln ab, fuenf Mal, und gibt vorher auf.
    assert f"{7 - 3 + 1} müssen weichen" in meldung
    # Auskunft, kein Auftrag. Wer weichen soll, weiss weder der Dienst noch das
    # Modell: `search_memory` liefert die zur Frage relevantesten Treffer, also
    # ausgerechnet das, was zuletzt gebraucht wurde.
    assert "lösch" not in meldung.lower()
    # Hier stand `"entscheidet der Benutzer" in meldung` und nagelte damit den
    # Satz „Welche das sind, entscheidet der Benutzer.“ fest. Er ist gefallen:
    # dieses `detail` geht ueber `routers/ai_memory.py` als Toast an genau den
    # Menschen, ueber den er in der dritten Person spricht — und die zwei Saetze
    # davor duzen ihn. Derselbe Fehler wie die frueheren Regieanweisungen an das
    # Modell, nur eine Stufe leiser. Geprueft wird deshalb dieselbe Sprechweise
    # wie im 0-Fall-Test, und zwar ohne den Artikel: „Sag dem Benutzer …“ waere
    # genauso ueber ihn hinweggeredet wie „der Benutzer entscheidet“.
    #
    # Der Gedanke selbst ist nicht weg, er steht nur dort, wo er hingehoert —
    # als Anweisung an das Modell in `_execute_remember`, geprueft in
    # `test_die_gesenkte_absage_schickt_das_modell_zum_benutzer`. Die Zusage
    # dieses Tests sind die drei Zahlen darueber.
    assert "Benutzer" not in meldung
    db.rollback()


# ── Die Naht zum Modell ───────────────────────────────────────────────
#
# Die Tests darueber lesen `detail` aus dem Dienst — den Satz, den ein Mensch
# als Toast bekommt. Was das *Modell* mit der Absage anfangen soll, steht
# dahinter in `_execute_remember` und ist in jedem der drei Faelle ein anderer
# Rat. Geprueft wird er deshalb auf dem Weg, den die KI wirklich nimmt: ueber
# `execute_read_tool` und die `AiActionValidationError`, die dort ankommt.
# Ein Test gegen `_execute_remember` allein saehe nicht, ob die Ausnahme des
# Dienstes ueberhaupt bis hierher durchkommt.


def _merkendes_modell(db: Session, user: User, name: str, entries: int | None) -> Role:
    """Ein Benutzer, fuer den das Modell wirklich schreiben darf.

    Der Werkzeugweg prueft zwei Dinge, die der Dienst darunter nicht kennt: das
    Recht ``ai.memory.use`` und den Einwilligungsschalter. Fehlt eines davon,
    kommt eine Absage ueber die Einwilligung zurueck statt einer ueber den
    Vorrat — und der Test prueft gruen den falschen Satz.
    """
    rolle = _memory_role(db, user, name, entries, "ai.memory.use")
    ai_memory_service.set_preference(db, user, True)
    return rolle


def _modell_merkt(db: Session, user: User, key: str) -> None:
    """Derselbe Vorgang wie `_merken`, aber ueber das Werkzeug der KI."""
    ai_action_service.execute_read_tool(
        db, user=user, tool_name="remember",
        arguments={"scope": "user", "key": key, "value": f"Wert zu {key}"},
    )


def test_die_gesperrte_absage_haelt_das_modell_vom_naechsten_versuch_ab(
    db: Session,
    regular_user: User,
) -> None:
    """Bei Grenze 0 bekommt das Modell einen Schlusspunkt, keinen Auftrag.

    Hier kann kein Aufraeumen Platz schaffen. Stuende auch nur einer der beiden
    Werkzeugnamen im Rat, loeschte ein folgsames Modell der Reihe nach den
    gesamten Bereich und scheiterte danach trotzdem.
    """
    _merkendes_modell(db, regular_user, "ai-modell-gesperrt", 0)

    with pytest.raises(AiActionValidationError) as exc:
        _modell_merkt(db, regular_user, "notiz.0")

    ansage = str(exc.value)
    assert "Versuch es nicht erneut" in ansage
    assert "search_memory" not in ansage
    assert "forget_memory" not in ansage
    # Und die Tatsache aus dem Dienst steht weiterhin davor — ohne sie wuesste
    # das Modell nicht, was es dem Benutzer sagen soll.
    assert "freigegeben" in ansage
    assert db.query(AiMemoryEntry).count() == 0


def test_die_volle_absage_nennt_dem_modell_beide_werkzeuge(
    db: Session,
    regular_user: User,
) -> None:
    """Genau voll ist der eine Fall, in dem Aufraeumen wirklich hilft.

    Ohne diesen Rat hoert die KI fuer den Bereich schlicht auf zu lernen,
    obwohl beide Werkzeuge vor ihr liegen. Die Auflage daneben ist genauso
    wichtig: `search_memory` nimmt allein eine Suchanfrage und rankt ueber
    alles, was der Benutzer sehen darf — ohne den Zusatz raeumte das Modell die
    persoenlichen Notizen ab, waehrend die volle Anlage voll bliebe.
    """
    _merkendes_modell(db, regular_user, "ai-modell-voll", 2)
    for nummer in range(2):
        _merken(db, regular_user, f"notiz.{nummer}")

    with pytest.raises(AiActionValidationError) as exc:
        _modell_merkt(db, regular_user, "notiz.zuviel")

    ansage = str(exc.value)
    assert "search_memory" in ansage
    assert "forget_memory" in ansage
    assert "aus genau diesem Bereich" in ansage
    assert "2 von 2 erlaubten" in ansage
    assert db.query(AiMemoryEntry).count() == 2


def test_die_gesenkte_absage_schickt_das_modell_zum_benutzer(
    db: Session,
    regular_user: User,
) -> None:
    """Steht ein Bereich ueber seiner Grenze, fragt das Modell und loescht nicht.

    Das ist die Zusage, um die es dieser Runde geht. Bei 100 Eintraegen und
    einer nachtraeglich auf 20 gesenkten Grenze duerfte hier kein Loeschauftrag
    ueber 81 Eintraege stehen: `search_memory` liefert hoechstens fuenfzehn
    Treffer, und zwar die zur Frage relevantesten. Wer daraus dutzende
    wegraeumt, loescht nicht, was nicht mehr gilt, sondern was zuletzt gebraucht
    wurde — `forget_memory` fragt vorher niemanden.
    """
    rolle = _merkendes_modell(db, regular_user, "ai-modell-gesenkt", 7)
    for nummer in range(7):
        _merken(db, regular_user, f"notiz.{nummer}")
    set_role_limit(db, rolle.id, _limits(max_memory_entries=3))
    db.commit()

    with pytest.raises(AiActionValidationError) as exc:
        _modell_merkt(db, regular_user, "notiz.neu")

    ansage = str(exc.value)
    assert "forget_memory" not in ansage
    assert "frag" in ansage
    assert "Benutzer" in ansage
    # Nichts ist weg: der Fehlschlag allein darf keinen Bestand kosten.
    assert db.query(AiMemoryEntry).count() == 7


def test_systembereiche_kennen_das_rollenlimit_nicht(
    db: Session,
    regular_user: User,
) -> None:
    """Anlagenwissen und panelweite Notizen haengen an keiner Benutzerrolle.

    Sie gehoeren dem Server beziehungsweise dem Betreiber. Der Tarif dessen,
    der den Eintrag zufaellig anlegt, waere dort das falsche Mass.
    """
    _memory_role(db, regular_user, "ai-memory-sysscope", 3)

    assert resolve_scope_memory_limit(db, "user", regular_user) == 3
    assert resolve_scope_memory_limit(db, "panel", regular_user) == MAX_SYSTEM_SCOPE_ENTRIES
    assert resolve_scope_memory_limit(
        db, "server_shared", regular_user, server_id=62
    ) == MAX_SYSTEM_SCOPE_ENTRIES
    # Ein Tippfehler im Bereichsnamen und eine ins Leere zeigende Teamnummer
    # oeffnen keinen unbegrenzten Vorrat, sondern fallen auf dieselbe Grenze.
    assert resolve_scope_memory_limit(
        db, "gibt-es-nicht", regular_user
    ) == MAX_SYSTEM_SCOPE_ENTRIES
    assert resolve_scope_memory_limit(
        db, "team", regular_user, team_id=424_242
    ) == MAX_SYSTEM_SCOPE_ENTRIES


def test_die_bestandszaehlung_sperrt_den_bereich_bis_zum_commit(
    db: Session, regular_user: User
) -> None:
    """Die Bereichsgrenze war eine Bitte, solange sie ungesperrt zählte.

    Zwischen der Zählung in `upsert_entry` und dem `db.add()` danach lag nichts.
    Zwei gleichzeitige Läufe mit **verschiedenen** Schlüsseln — Chat und
    Sprachsitzung laufen ausdrücklich nebeneinander — sahen beide denselben
    Bestand und legten beide an; die einzige Datenbankzusage ist der UNIQUE auf
    (scope_identity, key) und greift bei verschiedenen Schlüsseln gar nicht.

    Geprüft wird das erzeugte SQL und nicht das Verhalten, und das gehört
    ehrlich gesagt: SQLite kennt keine Zeilensperre, `FOR UPDATE` ist dort ein
    No-Op. Ein Verhaltenstest wäre hier grün, egal was der Code tut. Die Zusage,
    die wirklich trägt, ist die gegen den PostgreSQL-Dialekt kompilierte
    Abfrage — fällt die Sperre weg, wird dieser Test rot.
    """
    from sqlalchemy.dialects import postgresql

    abfrage = ai_memory_service._sperrzeile(db, f"user:{regular_user.id}")

    sql = str(
        abfrage.statement.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )

    assert "FOR UPDATE" in sql, "ohne Zeilensperre zählen zwei Schreiber denselben Stand"
    # Die Reihenfolge ist kein Schmuck, sondern der Treffpunkt: beide Schreiber
    # lesen aufsteigend und landen deshalb auf derselben Zeile.
    assert "ORDER BY" in sql
    # Und es bleibt bei dieser einen Zeile. Gesperrt wurde früher der ganze
    # Bereich; seit `MAX_MEMORY_ENTRIES_MAX` bei 5.000 steht, wären das 5.000
    # Zeilensperren je gemerktem Satz — ohne einen Gramm mehr
    # Ausschließlichkeit, weil sich zwei Schreiber ohnehin an der ersten Zeile
    # begegnen.
    assert "LIMIT 1" in sql, (
        "ohne LIMIT sperrt eine Neuanlage den gesamten Bereich"
    )


def test_die_gesperrte_zaehlung_liefert_dieselbe_zahl_wie_vorher(
    db: Session, regular_user: User
) -> None:
    """Die Sperre ist eine Sperre und die Zählung eine Zählung.

    Beides tat einmal eine einzige Abfrage: `count()` verträgt kein
    `FOR UPDATE`, also musste die Liste der IDs die Zahl liefern. Seit die
    Sperre nur noch eine Zeile nimmt, kann sie das nicht mehr, und die Zahl
    kommt aus einem eigenen `count()` **hinter** der Sperre. Dass dabei
    dieselbe Zahl herauskommt, ist die Bedingung dafür, dass alle Grenztests
    darüber weiterhin dasselbe messen.
    """
    _memory_role(db, regular_user, "ai-memory-sperre", 10)
    for nummer in range(4):
        _merken(db, regular_user, f"notiz.{nummer}")

    identity = f"user:{regular_user.id}"

    assert ai_memory_service._bestand_unter_sperre(db, identity) == 4
    assert db.query(AiMemoryEntry).filter(
        AiMemoryEntry.scope_identity == identity
    ).count() == 4
    # Und die Sperre greift nur den Bereich, um den es geht: ein Nachbarbereich
    # mit demselben Präfix darf weder mitgezählt noch mitgesperrt werden.
    assert ai_memory_service._bestand_unter_sperre(db, f"{identity}0") == 0


def test_memory_limit_ueberlebt_den_weg_durch_die_api(
    client: TestClient,
    db: Session,
    owner_cookies: dict,
) -> None:
    """Was der Betreiber einstellt, liest er unveraendert wieder — bis zum Deckel."""
    role = _role(db, "ai-memory-api")

    gespeichert = client.put(
        f"/api/ai/settings/role-limits/{role.id}",
        json=_limits(max_memory_entries=250),
        cookies=owner_cookies,
        headers=_csrf(owner_cookies),
    )

    assert gespeichert.status_code == 200
    assert gespeichert.json()["max_memory_entries"] == 250
    listed = client.get("/api/ai/settings/role-limits", cookies=owner_cookies)
    row = next(item for item in listed.json() if item["role_id"] == role.id)
    assert row["max_memory_entries"] == 250

    zu_gross = client.put(
        f"/api/ai/settings/role-limits/{role.id}",
        json=_limits(max_memory_entries=MAX_MEMORY_ENTRIES_MAX + 1),
        cookies=owner_cookies,
        headers=_csrf(owner_cookies),
    )

    assert zu_gross.status_code == 422
    # Und der abgewiesene Wert hat den gespeicherten nicht angefasst.
    listed = client.get("/api/ai/settings/role-limits", cookies=owner_cookies)
    row = next(item for item in listed.json() if item["role_id"] == role.id)
    assert row["max_memory_entries"] == 250

    # Der Deckel selbst muss erreichbar sein — sonst wäre er nicht der Deckel,
    # sondern die erste abgewiesene Zahl. Zwei Stellen entscheiden darüber, das
    # Pydantic-Feld (`le=`) und `set_role_limit`; ein `<` statt `<=` in einer
    # von beiden bliebe ohne diese Zeile unbemerkt.
    genau_am_deckel = client.put(
        f"/api/ai/settings/role-limits/{role.id}",
        json=_limits(max_memory_entries=MAX_MEMORY_ENTRIES_MAX),
        cookies=owner_cookies,
        headers=_csrf(owner_cookies),
    )

    assert genau_am_deckel.status_code == 200
    assert genau_am_deckel.json()["max_memory_entries"] == MAX_MEMORY_ENTRIES_MAX


def test_die_null_ueberlebt_den_weg_durch_die_api_und_sperrt(
    client: TestClient,
    db: Session,
    regular_user: User,
    owner_cookies: dict,
) -> None:
    """Eine Rolle ganz ohne Gedaechtnis muss einstellbar sein — und sperren.

    Der Schalter „Unbegrenzt“ in der Oberflaeche schickt beim Ausschalten
    genau diese 0. Sie ist der einzige Wert, den ein ``x or FALLBACK`` auf dem
    Weg still in etwas anderes verwandelt: erst in das leere Feld, dann bei der
    Aufloesung in die Systemgrenze. Der Betreiber saehe danach seine 0 nicht
    wieder, und die Rolle merkte munter weiter. Geprueft wird deshalb die
    ganze Strecke — speichern, wieder lesen, und dann der Versuch zu merken.
    """
    role = _role(db, "ai-memory-null")

    gespeichert = client.put(
        f"/api/ai/settings/role-limits/{role.id}",
        json=_limits(max_memory_entries=0),
        cookies=owner_cookies,
        headers=_csrf(owner_cookies),
    )

    assert gespeichert.status_code == 200
    assert gespeichert.json()["max_memory_entries"] == 0
    listed = client.get("/api/ai/settings/role-limits", cookies=owner_cookies)
    row = next(item for item in listed.json() if item["role_id"] == role.id)
    assert row["max_memory_entries"] == 0
    # `configured` und nicht bloss der Wert: eine 0, die als „nicht
    # konfiguriert“ zurueckkommt, waere in der Maske ein leeres Feld.
    assert row["configured"] is True

    set_user_roles(db, regular_user, [role.id])

    assert resolve_scope_memory_limit(db, "user", regular_user) == 0
    with pytest.raises(HTTPException) as exc:
        _merken(db, regular_user, "notiz.0")

    assert exc.value.status_code == 409
    db.rollback()


def _letzter_limit_trail(db: Session, role: Role) -> dict:
    """Die Details des juengsten ``ai.role_limits.updated`` zu dieser Rolle.

    Bewusst der juengste und nicht `.one()` wie im Audittest weiter oben: der
    Test darunter speichert mehrfach, weil sich die drei Faelle nur ueber
    verschiedene Nutzlasten zeigen lassen.
    """
    eintrag = (
        db.query(AuditLog)
        .filter(
            AuditLog.action == "ai.role_limits.updated",
            AuditLog.target_id == str(role.id),
        )
        .order_by(AuditLog.id.desc())
        .first()
    )
    assert eintrag is not None, "Zu dieser Rolle wurde ueberhaupt nichts protokolliert."
    return json.loads(eintrag.details or "{}")


def test_der_audit_trail_nennt_ein_leeres_gedaechtnisfeld_nicht_unbegrenzt(
    client: TestClient,
    db: Session,
    owner_cookies: dict,
) -> None:
    """Was der Trail „unbegrenzt“ nennt, muss auch unbegrenzt sein.

    Ein leeres Feld heisst nicht mehr ueberall dasselbe: bei den Kontingenten
    „unbegrenzt“, bei ``max_memory_entries`` dagegen „der Betreiber hat nichts
    hinterlegt“ — durchgesetzt wird dort die Systemgrenze. Der Trail trug
    frueher beides als ``unlimited_fields``, und ausgerechnet dieses Artefakt
    bleibt dauerhaft stehen und wird im Streitfall gelesen: „warum merkt sich
    die KI nur 100 Dinge, obwohl unbegrenzt eingetragen war“. Dort stand dann
    schwarz auf weiss, der Betreiber habe unbegrenzt gesetzt. Gesetzt hat er
    nichts, und er sucht den Fehler danach im Memory-Service statt im leeren
    Feld.

    Gedeckt war die Trennung von keinem Test — wer die beiden Listen spaeter
    wieder zusammenfasst, bekam bis hierhin eine gruene Suite. Geprueft wird
    deshalb an konkreten Feldnamen und nicht ueber ``FELDER_OHNE_UNBEGRENZT``:
    aus der Menge abgeleitet bliebe der Test auch dann gruen, wenn dort
    versehentlich ein Kontingentfeld landet.

    Die dritte Zusage ist die leiseste: beide Listen stehen auch dann im
    Protokoll, wenn sie leer sind. Eine fehlende Liste liesse sich spaeter als
    „damals gab es das Feld noch nicht“ **oder** als „nichts leer gelassen“
    lesen, und im Trail ist das ein Unterschied.
    """
    role = _role(db, "ai-memory-audit")

    def speichern(**overrides: int | None) -> dict:
        antwort = client.put(
            f"/api/ai/settings/role-limits/{role.id}",
            json=_limits(**overrides),
            cookies=owner_cookies,
            headers=_csrf(owner_cookies),
        )
        assert antwort.status_code == 200, antwort.text
        return _letzter_limit_trail(db, role)

    # Ueberall `.get(...)`: eine fehlende Liste soll als fehlende Liste
    # scheitern und nicht als KeyError mitten im Test. Genau darum geht es im
    # dritten Fall.
    leeres_gedaechtnisfeld = speichern(max_memory_entries=None)
    assert leeres_gedaechtnisfeld.get("unset_fields") == ["max_memory_entries"]
    assert leeres_gedaechtnisfeld.get("unlimited_fields") == []

    # Und andersherum, sonst belegt der Fall darueber nur, dass irgendetwas
    # sortiert wurde: beim Kontingent ist das leere Feld weiterhin eine Ansage.
    leeres_kontingent = speichern(monthly_token_limit=None)
    assert leeres_kontingent.get("unlimited_fields") == ["monthly_token_limit"]
    assert leeres_kontingent.get("unset_fields") == []

    nichts_leer = speichern(max_memory_entries=250)
    assert nichts_leer.get("unlimited_fields") == []
    assert nichts_leer.get("unset_fields") == []


# ── Dieselben Zahlen, zweimal aufgeschrieben ──────────────────────────
#
# Beide Konstanten stehen ein zweites Mal im Frontend: die Systemgrenze als
# Wort im Hinweistext unter dem Feld, der Deckel als `max` der Feldliste. Bis
# hierhin war das nur eine Verabredung in einem Kommentar — wer die Konstante
# verschob, bekam eine gruene Suite und eine Maske, die dem Betreiber die alte
# Zahl nennt oder gueltige Werte abweist. Es ist derselbe Waechter wie
# `test_the_scale_matches_the_limit_services_maximum` fuer die Denkstufen, nur
# ueber die Sprachgrenze hinweg.

ROOT = Path(__file__).resolve().parents[2]
AI_TAB = ROOT / "frontend" / "src" / "pages" / "settings" / "AiTab.tsx"
LOCALES = ROOT / "frontend" / "src" / "locales"
# Nur diese beiden Sprachen tragen den Hinweis; die uebrigen neun sind bewusst
# unvollstaendig und fallen auf Englisch zurueck.
SPRACHEN = ("de", "en")
HINWEIS_SCHLUESSEL = "aiSettings.maxMemoryEntriesHint"


def _feld_definition() -> str:
    """Der Eintrag zu ``max_memory_entries`` aus ``FIELD_DEFINITIONS``.

    Kein TypeScript-Parser, sondern der Ausschnitt zwischen dem Feldnamen und
    der schliessenden Klammer seines Objektliterals — dasselbe enge Verfahren
    wie im Vertragstest zum Rechtekatalog. Faellt die Marke weg, schlaegt der
    Test mit einer Ansage fehl, statt still nichts mehr zu pruefen.
    """
    quelle = AI_TAB.read_text(encoding="utf-8")
    marke = "key: 'max_memory_entries'"
    assert marke in quelle, (
        f"{AI_TAB.name} kennt kein Feld `max_memory_entries` mehr — dann prueft "
        "dieser Test eine Maske, die es so nicht gibt."
    )
    start = quelle.index(marke)
    return quelle[start:quelle.index("}", start)]


def _hinweis(sprache: str) -> str:
    pfad = LOCALES / f"{sprache}.json"
    assert pfad.is_file(), f"Sprachdatei nicht gefunden: {pfad}"
    daten = json.loads(pfad.read_text(encoding="utf-8"))
    hinweis = daten.get("aiSettings", {}).get("maxMemoryEntriesHint")
    assert hinweis, f"{sprache}.json hat keinen Text unter {HINWEIS_SCHLUESSEL}."
    return hinweis


def _zahlen(satz: str) -> set[int]:
    """Alle Zahlen eines Satzes, Tausendertrenner weggerechnet.

    Ein blosses ``"100" in satz`` waere kein Waechter: es stimmte auch bei
    „1000 Eintraegen“ und schwiege genau dann, wenn der Text falsch ist.
    """
    return {
        int(treffer.replace(".", "").replace(",", ""))
        for treffer in re.findall(r"\d[\d.,]*", satz)
    }


@pytest.mark.parametrize("sprache", SPRACHEN)
def test_der_hinweis_nennt_genau_die_systemgrenze_aus_dem_code(sprache: str) -> None:
    """Die 100 unter dem Feld ist eine Zusage, kein Beispiel.

    Sie sagt dem Betreiber, was fuer eine nicht konfigurierte Rolle gilt — und
    unter genau dieser Zusage („strikt additiv, fuer niemanden aendert sich
    etwas“) ist das Feld gebaut worden. Verschiebt jemand
    ``MAX_SYSTEM_SCOPE_ENTRIES``, ohne den Satz anzufassen, steht in der Maske
    weiter eine Zahl, die niemanden mehr betrifft, und der Betreiber plant seine
    Tarife danach.

    Geprueft wird die *Menge* der Zahlen im Satz, nicht ihr Vorkommen: eine
    zweite Zahl daneben waere ebenso eine Behauptung, fuer die dieser Test dann
    nicht mehr geradesteht.
    """
    # Ohne diese Zeile prueft der Test irgendwann einen Text, den die Maske gar
    # nicht mehr anzeigt — und bleibt gruen, waehrend unter dem Feld etwas
    # anderes steht. Derselbe Grund wie beim Rechtekatalog.
    assert f"hintKey: '{HINWEIS_SCHLUESSEL}'" in _feld_definition(), (
        f"Das Feld `max_memory_entries` zeigt {HINWEIS_SCHLUESSEL} nicht mehr an; "
        "dann prueft dieser Test die falsche Quelle."
    )

    assert _zahlen(_hinweis(sprache)) == {MAX_SYSTEM_SCOPE_ENTRIES}, (
        f"Der Hinweis in {sprache}.json nennt nicht die Systemgrenze aus "
        f"`ai_limit_service` ({MAX_SYSTEM_SCOPE_ENTRIES}): "
        f"{_hinweis(sprache)!r}"
    )


def test_das_formular_deckelt_bei_derselben_zahl_wie_das_backend() -> None:
    """Der Deckel im Zahlenfeld muss der sein, den das Backend durchlaesst.

    Ist er hoeher, traegt der Betreiber eine Zahl ein, die das Speichern mit
    einer Validierungsmeldung abweist — der Fehler steht dann an der Stelle,
    an der er nichts falsch gemacht hat. Ist er niedriger, kann er einen Wert,
    den das Backend erlaubt, ueberhaupt nicht mehr erreichen und haelt ihn fuer
    unmoeglich. Im Kommentar der Zeile stand „muss entsprechen“ schon vorher;
    gesehen hat es nur niemand.
    """
    treffer = re.search(r"max:\s*([\d_]+)", _feld_definition())

    assert treffer is not None, (
        "Im Eintrag zu `max_memory_entries` steht kein `max:` mehr — das "
        "Zahlenfeld haette damit gar keinen Deckel."
    )
    assert int(treffer.group(1).replace("_", "")) == MAX_MEMORY_ENTRIES_MAX, (
        f"{AI_TAB.name} deckelt bei {treffer.group(1)}, das Backend bei "
        f"{MAX_MEMORY_ENTRIES_MAX}."
    )
