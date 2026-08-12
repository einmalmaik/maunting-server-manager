"""Die Auswertung des KI-Verbrauchs — und das Recht, das sie bewacht.

`ai.usage.read.all` stand seit dem ersten Entwurf im Katalog und wurde an keiner
Stelle geprueft; der Rollen-Editor musste ihn als „noch ohne Funktion“
beschriften. Diese Datei ist die Gegenprobe zu beidem: dass es die Funktion gibt
und dass sie ohne das Recht nicht erreichbar ist.

Der zweite Schwerpunkt ist die **Uebereinstimmung mit der Durchsetzung**. Die
Ansicht muss dieselben Zahlen zeigen, die `reserve_ai_usage` gegen die Grenzen
haelt — sonst kann niemand erklaeren, warum jemand mit scheinbar freiem
Kontingent abgewiesen wird.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import Role, RolePermission, User
from services.ai_usage_service import (
    MICROUNITS_PER_CENT,
    _period_starts,
    complete_ai_usage,
    fail_ai_usage,
    reserve_ai_usage,
    usage_for_user,
    usage_overview,
)
from services.auth_service import AuthService
from services.role_service import set_user_roles


def _user(db: Session, name: str) -> User:
    user = AuthService.create_user(db, name, f"{name}@test.de", "UsagePass123!")
    user.email_verified = True
    db.commit()
    db.refresh(user)
    return user


def _allow(db: Session, user: User, *keys: str) -> None:
    """Setzt die Rechte eines Benutzers auf genau diese Liste.

    Bewusst ersetzend und nicht anlegend: ein Test nimmt einem Benutzer ein
    Recht wieder weg oder gibt ihm eines dazu, und beides soll ohne einen
    zweiten Benutzer samt eigener Anmeldung gehen.
    """
    name = f"usage-{user.username}"
    role = db.query(Role).filter(Role.name == name).first()
    if role is None:
        role = Role(name=name, description=None, is_system=False)
        db.add(role)
        db.flush()
    else:
        db.query(RolePermission).filter(RolePermission.role_id == role.id).delete()
    for key in keys:
        db.add(RolePermission(role_id=role.id, permission_key=key))
    db.commit()
    set_user_roles(db, user, [role.id])
    db.commit()


def _spend(
    db: Session, user: User, *, tokens: int, cents: int = 0, when: datetime | None = None
) -> None:
    """Bucht eine abgeschlossene Anfrage — der Normalfall im Betrieb."""
    event = reserve_ai_usage(
        db, user, request_id=uuid4(), estimated_tokens=tokens,
        estimated_cost_microunits=cents * MICROUNITS_PER_CENT, now=when,
    )
    complete_ai_usage(
        db, event, actual_tokens=tokens,
        actual_cost_microunits=cents * MICROUNITS_PER_CENT,
    )
    db.commit()


# ── Das Recht ─────────────────────────────────────────────────────────


def test_the_overview_needs_its_own_permission(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict,
) -> None:
    """Ohne `ai.usage.read.all` bleibt die Gesamtansicht verschlossen.

    Und zwar auch mit `panel.settings.read`: die Ansicht steht unter
    Einstellungen, aber sie zeigt das Nutzungsverhalten fremder Benutzer. Wer
    Kontingente konfigurieren darf, darf deshalb nicht automatisch sehen, wer
    wieviel verbraucht — das ist der Grund, warum es diesen Key ueberhaupt gibt.
    """
    _allow(db, regular_user, "ai.chat.use", "panel.settings.read")

    verboten = client.get("/api/ai/usage", cookies=user_cookies)
    assert verboten.status_code == 403

    _allow(db, regular_user, "ai.chat.use", "panel.settings.read", "ai.usage.read.all")

    erlaubt = client.get("/api/ai/usage", cookies=user_cookies)
    assert erlaubt.status_code == 200
    assert "entries" in erlaubt.json()


def test_own_usage_needs_no_special_permission(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict,
) -> None:
    """Der eigene Verbrauch ist ohne Sonderrecht sichtbar.

    Wer von der KI mit „Kontingent ausgeschoepft“ abgewiesen wird, muss
    nachsehen koennen, woran es liegt. Ein Recht davor waere eine Grenze, die
    ihre eigene Begruendung verdeckt.
    """
    _allow(db, regular_user, "ai.chat.use")
    _spend(db, regular_user, tokens=1_234, cents=7)

    antwort = client.get("/api/ai/usage/me", cookies=user_cookies)

    assert antwort.status_code == 200
    daten = antwort.json()
    assert daten["tokens_today"] == 1_234
    assert daten["cost_month_micro_usd"] == 7 * MICROUNITS_PER_CENT
    assert daten["user_id"] == regular_user.id
    # Die Grenzen stehen daneben: erst das Paar ergibt eine Aussage.
    assert "limits" in daten


def test_own_usage_shows_only_the_own_numbers(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict,
) -> None:
    """`/usage/me` ist kein Schlupfloch um `ai.usage.read.all` herum."""
    _allow(db, regular_user, "ai.chat.use")
    fremder = _user(db, "fremder-verbraucher")
    _spend(db, fremder, tokens=999_000)
    _spend(db, regular_user, tokens=10)

    daten = client.get("/api/ai/usage/me", cookies=user_cookies).json()

    assert daten["tokens_month"] == 10
    assert daten["username"] == regular_user.username


def test_a_user_without_events_gets_zeros_not_an_error(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict,
) -> None:
    """Wer die KI noch nie benutzt hat, sieht „0 von X“ statt einer Leerstelle."""
    _allow(db, regular_user, "ai.chat.use")

    daten = client.get("/api/ai/usage/me", cookies=user_cookies).json()

    assert daten["tokens_today"] == 0
    assert daten["requests_month"] == 0
    assert daten["last_request_at"] is None


# ── Die Zahlen ────────────────────────────────────────────────────────


def test_the_periods_are_counted_separately(db: Session, regular_user: User) -> None:
    """Heute, diese Woche, dieser Monat — drei Zahlen aus einer Abfrage."""
    now = datetime(2026, 3, 18, 12, 0, tzinfo=timezone.utc)
    day_start, week_start, month_start = _period_starts(now)
    assert month_start < week_start < day_start  # ein Mittwoch, mitten im Monat

    _spend(db, regular_user, tokens=100, when=month_start + timedelta(hours=1))
    _spend(db, regular_user, tokens=20, when=week_start + timedelta(hours=1))
    _spend(db, regular_user, tokens=3, when=day_start + timedelta(hours=1))

    summe = usage_for_user(db, regular_user, now=now)

    assert summe.tokens_today == 3
    assert summe.tokens_week == 23
    assert summe.tokens_month == 123
    assert summe.requests_month == 3


def test_a_week_reaching_into_the_previous_month_is_complete(
    db: Session, regular_user: User,
) -> None:
    """Die ISO-Woche beginnt regelmaessig vor dem Monatsanfang.

    Am 1. März 2026 — einem Sonntag — liegt der Wochenanfang im Februar. Wer die
    Auswertung nur ab Monatsanfang laedt, zeigt an solchen Tagen eine zu
    niedrige Wochenzahl: der Verbrauch vom Donnerstag davor faellt heraus,
    obwohl er in derselben Woche liegt und gegen dasselbe Wochenlimit zaehlt.
    """
    now = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)
    _, week_start, month_start = _period_starts(now)
    assert week_start < month_start

    _spend(db, regular_user, tokens=500, when=week_start + timedelta(days=2))

    summe = usage_for_user(db, regular_user, now=now)

    assert summe.tokens_week == 500
    # Und der Monat bleibt trotzdem sauber getrennt: die Zeile liegt davor.
    assert summe.tokens_month == 0


def test_failed_requests_do_not_count(db: Session, regular_user: User) -> None:
    """Was nie beim Anbieter ankam, hat nichts gekostet.

    Dieselbe Regel wie bei der Durchsetzung: `fail_ai_usage` setzt den
    verbuchten Verbrauch auf 0 zurueck, und die Auswertung filtert denselben
    Status heraus. Eine Ansicht, die abgebrochene Anfragen mitzaehlt, waere
    hoeher als das, was die Sperre sieht.
    """
    fehlschlag = reserve_ai_usage(
        db, regular_user, request_id=uuid4(), estimated_tokens=5_000,
        estimated_cost_microunits=50 * MICROUNITS_PER_CENT,
    )
    fail_ai_usage(db, fehlschlag)
    db.commit()
    _spend(db, regular_user, tokens=10)

    summe = usage_for_user(db, regular_user)

    assert summe.tokens_month == 10
    assert summe.cost_month_microunits == 0
    assert summe.requests_month == 1


def test_reserved_requests_already_count(db: Session, regular_user: User) -> None:
    """Eine laufende Anfrage zaehlt sofort, nicht erst nach dem Abschluss.

    Auch das folgt der Durchsetzung: `reserve_ai_usage` prueft gegen
    `ACTIVE_STATUSES`, und dort steht `reserved` mit drin. Wuerde die Ansicht
    erst abgeschlossene Anfragen zeigen, saehe ein Benutzer mitten in einem
    langen Lauf weniger Verbrauch, als ihm gerade angerechnet wird.
    """
    reserve_ai_usage(
        db, regular_user, request_id=uuid4(), estimated_tokens=7_000,
    )
    db.commit()

    assert usage_for_user(db, regular_user).tokens_month == 7_000


# ── Die Gesamtansicht ─────────────────────────────────────────────────


def test_the_overview_ranks_the_biggest_consumer_first(
    db: Session, regular_user: User,
) -> None:
    """Die teuerste Zeile steht oben — sie ist der Grund fuer den Aufruf."""
    viel = _user(db, "viel-verbraucher")
    wenig = _user(db, "wenig-verbraucher")
    _spend(db, wenig, tokens=10)
    _spend(db, viel, tokens=90_000)
    _spend(db, regular_user, tokens=500)

    reihen = usage_overview(db)

    assert [reihe.username for reihe in reihen] == [
        viel.username, regular_user.username, wenig.username,
    ]


def test_users_without_consumption_stay_out(db: Session, regular_user: User) -> None:
    """Wer nichts verbraucht hat, fuellt die Tabelle nicht mit Nullen.

    Bei einem Hoster mit zweihundert Kunden beantwortet eine Liste aus lauter
    Nullzeilen die Frage „wohin fliessen die Kosten“ schlechter als eine mit den
    zwoelf, die tatsaechlich etwas verbrauchen.
    """
    _user(db, "stiller-kunde")
    _spend(db, regular_user, tokens=42)

    reihen = usage_overview(db)

    assert [reihe.user_id for reihe in reihen] == [regular_user.id]


def test_die_api_rundet_kleinstbetraege_nicht_weg(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict,
) -> None:
    """Die Schnittstelle liefert Mikroeinheiten, nicht aufgerundete Cent.

    Hier wurde frueher je Zeile auf ganze Cent aufgerundet, damit eine
    Kostenangabe nie zu niedrig aussieht. Fuer eine Monatssumme war das
    harmlos, fuer eine einzelne Anfrage nicht: die meisten kosten weniger als
    einen Cent, und aufgerundet sah jede gleich teuer aus. Drei Zeilen zu je
    einer Mikroeinheit wurden so zu drei Cent — dem Zehntausendfachen.

    Gerundet wird jetzt erst beim Anzeigen. Die Schnittstelle traegt die Zahl,
    die auch gebucht wurde, und genau daran laesst sie sich pruefen.
    """
    _allow(db, regular_user, "ai.chat.use", "ai.usage.read.all")
    for name in ("a-winzig", "b-winzig", "c-winzig"):
        nutzer = _user(db, name)
        ereignis = reserve_ai_usage(
            db, nutzer, request_id=uuid4(), estimated_tokens=1,
            estimated_cost_microunits=1,
        )
        complete_ai_usage(db, ereignis, actual_tokens=1, actual_cost_microunits=1)
    db.commit()

    daten = client.get("/api/ai/usage", cookies=user_cookies).json()

    assert [eintrag["cost_month_micro_usd"] for eintrag in daten["entries"]] == [1, 1, 1]
    assert daten["total_cost_month_micro_usd"] == 3
    # Die Waehrung steht daneben, damit die Oberflaeche nicht zweimal fragt.
    assert daten["cost_policy"]["currency"] in {"EUR", "USD"}


def test_the_overview_covers_every_user(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict,
) -> None:
    """Die Gesamtansicht zeigt fremden Verbrauch — dafuer gibt es das Recht."""
    _allow(db, regular_user, "ai.chat.use", "ai.usage.read.all")
    fremder = _user(db, "anderer-kunde")
    _spend(db, fremder, tokens=4_000, cents=3)
    _spend(db, regular_user, tokens=1_000, cents=2)

    daten = client.get("/api/ai/usage", cookies=user_cookies).json()

    namen = {eintrag["username"] for eintrag in daten["entries"]}
    assert namen == {fremder.username, regular_user.username}
    assert daten["total_tokens_month"] == 5_000
    assert daten["total_cost_month_micro_usd"] == 5 * MICROUNITS_PER_CENT
