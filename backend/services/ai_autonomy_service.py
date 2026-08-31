"""Autonomer KI-Modus: wann eine Aktion ohne Rueckfrage laufen darf.

Zielpunkt 3.7. Der Standardmodus bleibt der unterstuetzte: die KI analysiert,
schlaegt vor und wartet. Autonomie ist die Ausnahme und muss vier Bedingungen
gleichzeitig erfuellen — Recht, ausdrueckliche Freigabe, erlaubtes Werkzeug und
freies Stundenbudget.

**Was Autonomie nicht tut:** sie hebt keine Berechtigung auf. Jede Aktion
durchlaeuft `_require_tool_permission` beim Anlegen und erneut unmittelbar vor
der Ausfuehrung, dazu die Aktivpruefung des Benutzers und den Server-Mutex.
Entfernt wird ausschliesslich der Schritt, in dem ein Mensch zustimmt.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from models import AiActionProposal, AiAutonomyGrant, User
from services import permission_service


def _now() -> datetime:
    return datetime.now(timezone.utc)


def resolve_grant(
    db: Session, *, user_id: int, server_id: int | None
) -> AiAutonomyGrant | None:
    """Sucht die zutreffende Freigabe: erst der Server, dann panelweit.

    Die spezifischere Angabe gewinnt — **auch wenn sie abschaltet**. Deshalb
    wird hier bewusst nicht nach `enabled` gefiltert: sonst waere ein gezielt
    deaktivierter Server-Grant unsichtbar und die panelweite Freigabe wuerde ihn
    ueberstimmen. Genau der Fall, den ein Betreiber braucht, der Autonomie
    grundsaetzlich erlaubt, aber auf einem empfindlichen Server nicht.

    Die `enabled`-Pruefung passiert deshalb erst beim Aufrufer.
    """
    rows = (
        db.query(AiAutonomyGrant)
        .filter(AiAutonomyGrant.user_id == user_id)
        .all()
    )
    by_scope = {row.server_id: row for row in rows}
    if server_id is not None and server_id in by_scope:
        return by_scope[server_id]
    return by_scope.get(None)


def hat_engere_server_freigabe(
    db: Session, *, user_id: int, panelweit: AiAutonomyGrant
) -> bool:
    """Gibt es eine Serverzeile, die enger ist als diese panelweite Freigabe?

    Die Frage stellt der Lageblock. Er fragt mit ``server_id=None``, bekommt die
    panelweite Freigabe und sagt dem Modell daraufhin, Schreibvorschläge liefen
    sofort. `resolve_grant` lässt aber die genauere Angabe gewinnen, und
    `autonomy_allows` schlägt jeden Vorschlag gegen **deren** Zeile nach. Auf
    einem so eingeschränkten Server wartet der Vorschlag also doch auf den
    Klick — die unbedingte Zusage wäre dort eine Falschauskunft, und das Modell
    meldete Vollzug, während die Karte unbeantwortet steht.

    Enger heißt zweierlei, und beides endet gleich: abgeschaltet (dann trägt
    `autonomie_grundlage` nicht), oder ein kleineres Budget — denn gezählt wird
    in `hourly_usage` **benutzerweit**, gehalten wird die Zahl aber gegen das
    Budget dieser Zeile. Erwartet wird deshalb die tragende panelweite Freigabe
    (aktiv, Budget größer Null); ein Server-Budget von Null ist dann von selbst
    das kleinere.

    Die Bedingung steht hier und nicht beim Aufrufer: `zustaendiger_freigeber`
    hält fest, dass eine Freigabe über `resolve_grant` gesucht wird und **nie**
    über eine eigene Abfrage auf `ai_autonomy_grants` — eine zweite Fassung
    derselben Lesart außerhalb dieses Dienstes ist genau die Gelegenheit, an der
    die beiden auseinanderlaufen.

    Nur das Ob, keine Namen: welcher Server gemeint ist, beantwortet erst der
    Vorschlag selbst.
    """
    zeilen = (
        db.query(AiAutonomyGrant.enabled, AiAutonomyGrant.max_actions_per_hour)
        .filter(
            AiAutonomyGrant.user_id == user_id,
            AiAutonomyGrant.server_id.isnot(None),
        )
        .all()
    )
    return any(
        not aktiv or budget < panelweit.max_actions_per_hour
        for aktiv, budget in zeilen
    )


def hourly_usage(db: Session, *, user_id: int, now: datetime | None = None) -> int:
    """Zaehlt die autonom erzeugten Aktionen der letzten Stunde."""
    since = (now or _now()) - timedelta(hours=1)
    return int(
        db.query(AiActionProposal)
        .filter(
            AiActionProposal.user_id == user_id,
            AiActionProposal.autonomous.is_(True),
            AiActionProposal.created_at >= since,
        )
        .count()
    )


def autonomie_grundlage(
    db: Session, *, user: User, server_id: int | None, tool_name: str
) -> AiAutonomyGrant | None:
    """Die Freigabe, die diese Aktion trägt — oder ``None``, wenn keine sie trägt.

    Hier stehen genau die Bedingungen, die **dauerhaft** gelten müssen:
    erlaubtes Werkzeug, `ai.autonomous.use` und eine aktive Freigabe mit einem
    Budget größer Null. Sie sind Zusagen, die ein Betreiber jederzeit
    zurückziehen kann, und ein Widerruf muss sofort wirken — deshalb werden sie
    nicht nur beim Anlegen eines Vorschlags gefragt, sondern auch unmittelbar
    vor seiner Ausführung.

    Das Stundenbudget steht bewusst **nicht** hier, sondern nur in
    `autonomy_allows`: der Vorschlag selbst zählt bereits in `hourly_usage` mit,
    und eine zweite Zählung vor der Ausführung würde bei
    ``max_actions_per_hour = 1`` die eigene Aktion verweigern.
    """
    from services.ai_tool_registry import ALWAYS_CONFIRM_TOOLS

    if tool_name in ALWAYS_CONFIRM_TOOLS:
        return None
    if not permission_service.has_global_permission(db, user, "ai.autonomous.use"):
        return None
    grant = resolve_grant(db, user_id=user.id, server_id=server_id)
    if grant is None or not grant.enabled:
        return None
    if grant.max_actions_per_hour <= 0:
        return None
    return grant


def autonomy_allows(
    db: Session,
    *,
    user: User,
    server_id: int | None,
    tool_name: str,
    now: datetime | None = None,
) -> bool:
    """Entscheidet, ob dieser eine Vorschlag ohne Bestaetigung laufen darf."""
    grant = autonomie_grundlage(
        db, user=user, server_id=server_id, tool_name=tool_name
    )
    if grant is None:
        return False
    # Persönliche Kalender-, Notiz- und Domain-Operationen des Nutzers unterliegen bei erteilter
    # Autonomie-Freigabe nicht dem strikten Server-Stundenkontingent.
    if tool_name in (
        "propose_calendar_event_create",
        "propose_calendar_event_update",
        "propose_calendar_event_delete",
        "calendar_event_create",
        "calendar_event_update",
        "calendar_event_delete",
        "propose_note_create",
        "propose_note_update",
        "propose_note_delete",
        "note_create",
        "note_update",
        "note_delete",
        "propose_cloudflare_dns_record",
        "propose_cloudflare_dns_delete",
    ):
        return True
    # Die Obergrenze begrenzt nicht die Berechtigung, sondern die Menge: ein in
    # eine Schleife geratenes Modell soll nicht in einer Minute vierzig Backups
    # anstossen. Bei Erreichen faellt die Aktion zurueck auf Bestaetigungspflicht
    # statt zu scheitern — der Benutzer verliert nur die Bequemlichkeit.
    return hourly_usage(db, user_id=user.id, now=now) < grant.max_actions_per_hour


def set_grant(
    db: Session,
    *,
    user: User,
    server_id: int | None,
    enabled: bool,
    max_actions_per_hour: int,
    granted_by: int | None,
) -> AiAutonomyGrant:
    from models.ai_autonomy_grant import MAX_ACTIONS_PER_HOUR_LIMIT

    if not 0 <= max_actions_per_hour <= MAX_ACTIONS_PER_HOUR_LIMIT:
        raise ValueError("Stundenbudget liegt ausserhalb des erlaubten Bereichs")
    row = (
        db.query(AiAutonomyGrant)
        .filter(
            AiAutonomyGrant.user_id == user.id,
            AiAutonomyGrant.server_id.is_(None)
            if server_id is None
            else AiAutonomyGrant.server_id == server_id,
        )
        .first()
    )
    if row is None:
        row = AiAutonomyGrant(user_id=user.id, server_id=server_id)
        db.add(row)
    row.enabled = enabled
    row.max_actions_per_hour = max_actions_per_hour
    row.granted_by = granted_by
    row.updated_at = _now()
    db.flush()
    return row


def clear_grant(db: Session, *, user_id: int, server_id: int | None) -> bool:
    row = (
        db.query(AiAutonomyGrant)
        .filter(
            AiAutonomyGrant.user_id == user_id,
            AiAutonomyGrant.server_id.is_(None)
            if server_id is None
            else AiAutonomyGrant.server_id == server_id,
        )
        .first()
    )
    if row is None:
        return False
    db.delete(row)
    db.flush()
    return True
