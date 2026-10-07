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
from services import ai_memory_service, team_service
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
    assert effective.monthly_realtime_minutes_limit == 0


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
    with pytest.raises(ValueError, match="monthly_realtime_minutes_limit"):
        set_role_limit(db, role.id, _limits(monthly_realtime_minutes_limit=100_001))


def test_erlaubte_maxima_passen_in_die_spaltenbreite() -> None:
    """Kein erlaubtes Maximum darf breiter sein als die Spalte, die es aufnimmt.

    In PostgreSQL endet INTEGER bei 2^31-1, und ein darüber liegender Wert
    bricht erst beim Speichern ab — ein Verhaltenstest fände das nur, wenn er
    zufällig genau das Maximum schreibt. Deshalb wird die Spaltenbreite hier
    direkt gegen jedes erlaubte Maximum gehalten.
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
        _limits(daily_token_limit=1_000, monthly_realtime_minutes_limit=5),
    )
    set_role_limit(
        db,
        vip.id,
        _limits(daily_token_limit=10_000, monthly_realtime_minutes_limit=None),
    )
    db.commit()
    set_user_roles(db, regular_user, [standard.id, vip.id])

    effective = resolve_effective_limits(db, regular_user)

    assert effective.daily_token_limit == 10_000
    assert effective.monthly_realtime_minutes_limit is None
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
        monthly_realtime_minutes_limit=30,
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
        _limits(monthly_realtime_minutes_limit=100_001),
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
        monthly_realtime_minutes_limit=None,
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


def test_usage_reservation_enforces_tokens(
    db: Session,
    regular_user: User,
) -> None:
    """Die Tokenlimits greifen vor einem späteren Provider-Aufruf.

    Anfragen pro Minute und gleichzeitige Vorgänge sind am 07.10.2026
    entfallen: eine zweite Anfrage in derselben Minute geht durch, solange
    Tokens übrig sind.
    """
    role = _role(db, "ai-enforced-usage")
    _enable_usage(
        db,
        regular_user,
        role,
        daily_token_limit=100,
        weekly_token_limit=100,
        monthly_token_limit=100,
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

    zweite = reserve_ai_usage(
        db,
        regular_user,
        request_id=uuid4(),
        estimated_tokens=1,
        now=now,
    )
    assert zweite.status == "reserved"
    fail_ai_usage(db, zweite)

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
    _enable_usage(db, regular_user, role)
    request_id = uuid4()
    event = reserve_ai_usage(
        db,
        regular_user,
        request_id=request_id,
        estimated_tokens=50,
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


# ── Sprachminuten ──────────────────────────────────────────────────────


def _sprachsitzung(db: Session, user: User, *, vor_sekunden: int = 0) -> AiUsageEvent:
    """Eröffnet eine Sprachsitzung, die ``vor_sekunden`` begonnen hat."""
    event = reserve_ai_usage(
        db, user, request_id=uuid4(), estimated_tokens=0,
        minimum_token_headroom=1, realtime=True,
    )
    event.created_at = datetime.now(timezone.utc) - timedelta(seconds=vor_sekunden)
    db.commit()
    return event


def test_null_sprachminuten_sperren_die_sitzung_vor_dem_beginn(
    db: Session, regular_user: User,
) -> None:
    role = _role(db, "ai-ohne-sprache")
    _enable_usage(db, regular_user, role, monthly_realtime_minutes_limit=0)

    with pytest.raises(AiQuotaExceeded, match="monthly_realtime_minutes_limit"):
        _sprachsitzung(db, regular_user)
    # Der Chat bleibt davon unberührt.
    assert reserve_ai_usage(db, regular_user, request_id=uuid4(), estimated_tokens=10).status == "reserved"


def test_die_sitzung_zaehlt_ihre_dauer_und_endet_an_der_minutengrenze(
    db: Session, regular_user: User,
) -> None:
    """Gezählt wird die Zeit, die die Sitzung offen war — nicht Audiotokens."""
    from services.ai_usage_service import realtime_restsekunden, realtime_verbrauch_ergaenzen

    role = _role(db, "ai-eine-sprachminute")
    _enable_usage(db, regular_user, role, monthly_realtime_minutes_limit=1)
    event = _sprachsitzung(db, regular_user, vor_sekunden=30)
    assert event.realtime_seconds == 0

    werte = dict(text_input=1, text_output=1, audio_input=1, audio_output=1, cost_microunits=0)
    realtime_verbrauch_ergaenzen(db, event_id=event.id, **werte)
    db.commit()
    assert 30 <= event.realtime_seconds < 40
    assert 20 <= realtime_restsekunden(db, regular_user) <= 30

    event.created_at = datetime.now(timezone.utc) - timedelta(seconds=90)
    db.commit()
    with pytest.raises(AiQuotaExceeded, match="monthly_realtime_minutes_limit"):
        realtime_verbrauch_ergaenzen(db, event_id=event.id, **werte)
    db.rollback()
    # Schon gelaufene Zeit wird trotzdem gebucht, sonst begänne der nächste
    # Anlauf wieder unter der Grenze.
    realtime_verbrauch_ergaenzen(db, event_id=event.id, grenzen_pruefen=False, **werte)
    db.commit()
    assert event.realtime_seconds >= 90
    assert realtime_restsekunden(db, regular_user) == 0
    with pytest.raises(AiQuotaExceeded, match="monthly_realtime_minutes_limit"):
        _sprachsitzung(db, regular_user)


def test_die_sitzung_endet_mit_dem_rest_der_minuten(
    db: Session, regular_user: User,
) -> None:
    """Die Höchstdauer einer Sitzung ist der Rest — und die Meldung sagt, warum."""
    from services.ai_voice.contracts import MAX_SITZUNGSSEKUNDEN
    from services.ai_voice.realtime_session import RealtimeVorbereitung, hoechstdauer, zeit_um

    assert hoechstdauer(db, regular_user) == MAX_SITZUNGSSEKUNDEN, "ohne Limit die feste Grenze"
    assert zeit_um(RealtimeVorbereitung(provider_id=1)) == {"art": "abgelaufen"}

    role = _role(db, "ai-zwei-sprachminuten")
    _enable_usage(db, regular_user, role, monthly_realtime_minutes_limit=2)
    assert hoechstdauer(db, regular_user) == 120
    gekuerzt = RealtimeVorbereitung(provider_id=1, hoechstdauer=hoechstdauer(db, regular_user))
    assert zeit_um(gekuerzt) == {"art": "stoerung", "grund": "realtime_kontingent"}


def test_eine_sitzung_ohne_antwort_kostet_keine_minuten(
    db: Session, regular_user: User,
) -> None:
    from services.ai_usage_service import realtime_restsekunden, realtime_sitzung_abschliessen

    role = _role(db, "ai-sitzung-ohne-antwort")
    _enable_usage(db, regular_user, role, monthly_realtime_minutes_limit=1)
    event = _sprachsitzung(db, regular_user, vor_sekunden=50)

    realtime_sitzung_abschliessen(db, event.id)
    db.commit()

    assert event.status == "failed"
    assert realtime_restsekunden(db, regular_user) == 60


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
#: Eingefuehrt fuer die Duplikatpruefung von `remember` (19.08.2026, mit Stufe 2
#: des Gedaechtnisses entfallen): stuenden in allen Eintraegen Varianten
#: desselben Satzes, scheiterte der naechste Aufruf an der Aehnlichkeit statt an
#: der Mengengrenze, um die es hier geht. Die Themen sind bewusst weit
#: auseinander.
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
    """Legt einen Eintrag ueber den Namensweg an (`upsert_entry`).

    Der Wert traegt den Schluessel **und** einen sachlich anderen Inhalt. Bis
    zum 19.08.2026 stand hier schlicht ``f"Wert zu {key}"`` — mit der
    Duplikatpruefung von `remember` waeren `notiz.0` bis `notiz.6`
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


def test_eine_unbegrenzte_zusatzrolle_hebt_den_vorrat_auf(
    db: Session,
    regular_user: User,
) -> None:
    """Zwei Rollen ergeben eine Grenze — und „Unbegrenzt“ ist die hoechste.

    Bis zum 05.10.2026 stand hier das Gegenteil: ein leeres Feld trug beim
    Gedaechtnis nichts bei, aus {10, unbegrenzt} wurde 10 und aus
    {unbegrenzt} die Systemgrenze 100 — obwohl der Schalter in der Maske
    „Unbegrenzt“ zeigte. Jetzt gilt dieselbe Regel wie bei den Kontingenten.
    """
    knapp, _ = _zwei_memory_rollen(db, regular_user, "ai-memory-hoechster", 10, 800)

    assert resolve_scope_memory_limit(db, "user", regular_user) == 800

    set_role_limit(db, knapp.id, _limits(max_memory_entries=None))
    db.commit()

    assert resolve_effective_limits(db, regular_user).max_memory_entries is None
    assert resolve_scope_memory_limit(db, "user", regular_user) is None


def test_eine_unbegrenzte_rolle_schlaegt_eine_gesperrte(
    db: Session,
    regular_user: User,
) -> None:
    """{0, unbegrenzt} ist unbegrenzt — wie bei jedem Kontingent.

    Die 0 sperrt nur, solange keine andere Rolle des Benutzers mehr erlaubt.
    Eine Sperre, die eine grosszuegigere Rolle nicht aufhebt, waere ein Riegel,
    den kein Tarif mehr aufbekommt.
    """
    _zwei_memory_rollen(db, regular_user, "ai-memory-null-gegen-offen", 0, None)

    assert resolve_effective_limits(db, regular_user).max_memory_entries is None
    assert resolve_scope_memory_limit(db, "user", regular_user) is None
    _merken(db, regular_user, "notiz.0")
    assert db.query(AiMemoryEntry).filter(
        AiMemoryEntry.scope_identity == f"user:{regular_user.id}"
    ).count() == 1


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


def test_unbegrenzt_in_allen_rollen_bleibt_auch_beim_merken_unbegrenzt(
    db: Session,
    regular_user: User,
) -> None:
    """Maske und Durchsetzung sagen dasselbe: „Unbegrenzt“ ist unbegrenzt.

    Vorher wurde ``None`` erst beim Merken zu ``MAX_SYSTEM_SCOPE_ENTRIES`` —
    die rohe Aufloesung und die durchgesetzte Grenze widersprachen sich.
    """
    _zwei_memory_rollen(db, regular_user, "ai-memory-offen", None, None)

    assert resolve_effective_limits(db, regular_user).max_memory_entries is None
    assert resolve_scope_memory_limit(db, "user", regular_user) is None


def test_der_vorrat_liest_ein_leeres_feld_wie_die_kontingente(
    db: Session,
    regular_user: User,
) -> None:
    """Ein leeres Feld heisst in jedem Feld dasselbe: unbegrenzt.

    Beide Felder stehen absichtlich in *einer* Zeilenmenge nebeneinander und
    jeweils einmal leer, einmal mit Zahl — eine Sonderregel fuer eines von
    beiden faellt hier sofort auf.
    """
    offen = _role(db, "ai-memory-gleich-offen")
    begrenzt = _role(db, "ai-memory-gleich-begrenzt")
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
    assert effective.max_memory_entries is None


def test_ohne_konfigurierte_rolle_ist_der_vorrat_unbegrenzt(
    db: Session,
    regular_user: User,
) -> None:
    """Ohne jede Rollenkonfiguration gilt unbegrenzt — wie bei den Kontingenten.

    Die Maske zeigt eine solche Rolle mit eingeschaltetem „Unbegrenzt“; das
    Backend setzt seit dem 05.10.2026 genau das durch statt still 100.
    """
    assert resolve_effective_limits(db, regular_user).max_memory_entries is None
    assert resolve_scope_memory_limit(db, "user", regular_user) is None


def test_unbegrenzt_laesst_mehr_als_die_systemgrenze_zu(
    db: Session,
    regular_user: User,
) -> None:
    """Der gemeldete Fehler: „Unbegrenzt“ eingeschaltet, trotzdem bei 100 Schluss.

    Die Aufloesung allein belegt das nicht — die Zaehlung im Memory-Service
    koennte trotzdem an einer festen Zahl abbrechen. Deshalb wird wirklich
    ueber ``MAX_SYSTEM_SCOPE_ENTRIES`` hinaus gemerkt.
    """
    _memory_role(db, regular_user, "ai-memory-unbegrenzt", None)
    for nummer in range(MAX_SYSTEM_SCOPE_ENTRIES + 1):
        _merken(db, regular_user, f"notiz.{nummer}")

    assert db.query(AiMemoryEntry).filter(
        AiMemoryEntry.scope_identity == f"user:{regular_user.id}"
    ).count() == MAX_SYSTEM_SCOPE_ENTRIES + 1


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
    niemanden mehr betrifft — und wer sie liest, suchte den Fehler bei sich
    statt beim Vorrat. Gemessen wird deshalb gegen ``MAX_SYSTEM_SCOPE_ENTRIES``:
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
    # rechnete, schickte ihren Leser mit „0 weichen“ ohne Loeschung sofort in
    # denselben Fehlschlag zurueck.
    assert "Einer muss weichen" in meldung
    # Werkzeugnamen stehen hier bewusst nicht mehr: derselbe Satz geht ueber
    # `routers/ai_memory.py` als Toast an einen Menschen, der keine Werkzeuge
    # hat. Bis Stufe 2 des Gedaechtnisses machte `remember` aus ihm einen Rat
    # an das Modell; der Gedaechtnisschreiber sieht den freien Platz vorher.
    # Gesucht wird jeder Bezeichner aus dem Code, nicht eine Namensliste, die
    # mit dem naechsten Werkzeug veraltet.
    assert re.search(r"\b[a-z]+_[a-z_]+\b", meldung) is None, meldung
    db.rollback()


def test_die_absage_nennt_den_bereich_um_den_es_geht(
    db: Session,
    regular_user: User,
) -> None:
    """Jede Absage sagt, *wo* es klemmt — sonst gilt sie fuer alles.

    „Voll“ ohne Bereich liest sich wie „das Gedaechtnis ist voll“ und stimmt
    dann fuer jeden anderen Vorrat des Benutzers nicht. Beim Server steht die
    Nummer — so sprachen ihn `remember` und `forget_memory` an, solange das
    Modell selbst schrieb (bis Stufe 2 des Gedaechtnisses). Beim Team steht
    der Name, siehe den Test dazu weiter unten.
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
    # und wer aufraeumt, raeumte im falschen Vorrat.
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
    Bei einer Grenze von 0 ist das nicht so: wer dem Text folgt, loescht der
    Reihe nach den gesamten Bereich — und scheitert danach trotzdem. Bis
    Stufe 2 des Gedaechtnisses war das ein folgsames Modell mit
    `forget_memory`; heute ist es der Mensch vor dem Toast. Die Absage nennt
    deshalb die Ursache und keinen Ausweg.

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
    assert re.search(r"\b[a-z]+_[a-z_]+\b", meldung) is None, meldung
    assert "weichen" not in meldung
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
    """Ein volles Team wird so benannt, wie ein Mensch es kennt.

    Die Absage liest ein Mensch als Toast, und „Team 3“ benennt fuer ihn
    nichts — Teams kennt er beim Namen. Bis Stufe 2 des Gedaechtnisses las sie
    auch das Modell, und fuer das galt dasselbe: `remember` und
    `forget_memory` erreichten ein Team ausschliesslich ueber `team="<Name>"`.

    Deshalb ist die zweite Haelfte der Zusage ein Verbot — steht im Satz eine
    Zahl, wo der Name hingehoert, ist dieser Test rot.
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
    Grund, damit niemand die Senkung fuer einen eigenen Fehler haelt.

    Zum Loeschen fordert sie trotzdem nicht auf: bei 100 Eintraegen und Grenze
    20 bekaeme der Leser hier sonst einen Auftrag ueber 81 Stueck. Und sie
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
    # ausdruecklich eine Zahl statt „eines“ — sonst zaehlt der Leser die
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
    # Der Gedanke selbst ist nicht weg: seit Stufe 2 des Gedaechtnisses
    # vergisst der Hintergrund nur, was der Mensch im Gespraech verlangt
    # (`test_ai_gedaechtnis_schreiber.test_vergessen_nur_auf_wunsch_des_menschen`).
    # Die Zusage dieses Tests sind die drei Zahlen darueber.
    assert "Benutzer" not in meldung
    db.rollback()


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
    ehrlich gesagt: auf der geteilten Testverbindung (StaticPool) stecken alle
    Sitzungen in derselben Transaktion, `FOR UPDATE` sperrt dort niemanden aus.
    Ein Verhaltenstest wäre hier grün, egal was der Code tut. Die Zusage,
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


def test_der_audit_trail_nennt_ein_leeres_gedaechtnisfeld_unbegrenzt(
    client: TestClient,
    db: Session,
    owner_cookies: dict,
) -> None:
    """Was der Trail „unbegrenzt“ nennt, ist jetzt auch beim Gedaechtnis unbegrenzt.

    Bis zum 05.10.2026 stand das leere Memory-Feld getrennt unter
    ``unset_fields``, weil es beim Merken zur Systemgrenze wurde. Seitdem
    gehoert es zu den unbegrenzten Feldern wie jedes andere.
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

    leeres_gedaechtnisfeld = speichern(max_memory_entries=None)
    assert leeres_gedaechtnisfeld.get("unlimited_fields") == ["max_memory_entries"]
    assert "unset_fields" not in leeres_gedaechtnisfeld

    nichts_leer = speichern(max_memory_entries=250)
    assert nichts_leer.get("unlimited_fields") == []


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

    Sie sagt dem Betreiber, welche feste Grenze fuer Server- und Panelwissen
    gilt, die an keiner Rolle haengen. Verschiebt jemand
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
