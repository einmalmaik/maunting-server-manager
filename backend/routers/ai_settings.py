"""Rollenbasierte KI-Limits und effektive Benutzergrenzen.

Diese Routen konfigurieren nur Kontingente. Provider-Schlüssel und Chats
folgen in separaten Schnitten, damit Rollenverwaltung und Secret-Flows nicht
zu einem schwer prüfbaren Monolithen werden.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from database import SessionLocal, get_db
from dependencies import get_current_user, require_global, verify_csrf
from models import Role, User
from schemas.ai_settings import (
    AiContextPolicyStatus,
    AiContextPolicyUpdate,
    AiCostPolicyStatus,
    AiCostPolicyUpdate,
    AiLearningPolicyStatus,
    AiLearningPolicyUpdate,
    AiRoleLimitsResponse,
    AiRoleLimitsUpdate,
    AiUsageEntry,
    AiUsageEventEntry,
    AiUsageEvents,
    AiUsageMine,
    AiUsageOverview,
    AiWebSearchKeyUpdate,
    AiWebSearchStatus,
    EffectiveAiLimitsResponse,
)
from services import ai_kosten, ai_limit_service, ai_usage_service, audit_service
from services.dis_client import DisSidecarError
from services.role_service import effective_user_role_ids


router = APIRouter(prefix="/api/ai", tags=["ai-settings"])


def _role_response(db: Session, role: Role) -> AiRoleLimitsResponse:
    """Zeigt eine unkonfigurierte Rolle mit leeren Feldern statt mit Null-Limits.

    Frueher stand hier 0. Das war doppelt irrefuehrend: es beschrieb weder den
    gespeicherten Zustand (es ist gar nichts gespeichert) noch das tatsaechliche
    Verhalten (bei den Kontingenten gilt ohne jede Rollenkonfiguration
    unbegrenzt, siehe ``ai_limit_service``) — und ein unbeabsichtigtes Speichern
    haette die Rolle hart gesperrt. ``configured`` bleibt der ehrliche
    Unterschied zwischen „nichts hinterlegt“ und „ausdruecklich unbegrenzt
    gesetzt“.

    „Bei den Kontingenten“ steht in dem Satz, seit ``max_memory_entries``
    mitfaehrt: dort heisst ein leeres Feld nicht unbegrenzt, sondern „diese
    Rolle sagt zum Vorrat nichts“, und durchgesetzt wird dann die Systemgrenze.
    Diese Antwort gibt beide Faelle als dasselbe ``null`` heraus und kann es
    nicht anders — sie zeigt den gespeicherten Zustand, nicht die Aufloesung.
    Wer gegen diesen Endpunkt baut, liest deshalb
    ``ai_limit_service.resolve_scope_memory_limit`` als die verbindliche
    Aufloesung und nicht das ``null`` hier; sonst meldet eine Tarifuebersicht
    „Gedaechtnis: unbegrenzt“, waehrend das Panel eine Zahl durchsetzt.
    """
    row = ai_limit_service.get_role_limit(db, role.id)
    values = {
        field: getattr(row, field) if row is not None else None
        for field in ai_limit_service.LIMIT_FIELDS
    }
    return AiRoleLimitsResponse(
        role_id=role.id,
        role_name=role.name,
        configured=row is not None,
        updated_at=row.updated_at if row is not None else None,
        **values,
    )


@router.get("/settings/role-limits", response_model=list[AiRoleLimitsResponse])
def list_role_limits(
    db: Session = Depends(get_db),
    _: User = Depends(require_global("panel.settings.read")),
) -> list[AiRoleLimitsResponse]:
    """Listet jede Rolle.

    Unkonfigurierte Rollen erscheinen mit ``configured=false`` und leeren
    Werten — die Begründung dazu steht in ``_role_response``.
    """
    roles = db.query(Role).order_by(Role.is_system.desc(), Role.name.asc()).all()
    return [_role_response(db, role) for role in roles]


@router.put(
    "/settings/role-limits/{role_id}",
    response_model=AiRoleLimitsResponse,
)
def update_role_limits(
    role_id: int,
    req: AiRoleLimitsUpdate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_global("panel.settings.write")),
    _: None = Depends(verify_csrf),
) -> AiRoleLimitsResponse:
    """Speichert alle Felder atomar und protokolliert nur nicht-sensible Metadaten."""
    role = db.query(Role).filter(Role.id == role_id).first()
    if role is None:
        raise HTTPException(status_code=404, detail="Rolle nicht gefunden")
    values = req.model_dump()
    try:
        ai_limit_service.set_role_limit(db, role.id, values)
        # Ein leeres Feld heisst nicht mehr ueberall dasselbe: bei den
        # Kontingenten „unbegrenzt“, bei ``max_memory_entries`` dagegen „der
        # Betreiber hat nichts hinterlegt“ — durchgesetzt wird dort die
        # Systemgrenze. Frueher trug der Trail beides als ``unlimited_fields``.
        # Ein Kommentar, der sich irrt, kostet eine Lesestunde; dieser Eintrag
        # beurkundet dauerhaft eine Betreiberhandlung. Wer ihn im Streitfall
        # liest — „warum merkt sich die KI nur 100 Dinge, obwohl unbegrenzt
        # eingetragen war“ —, fand dort schwarz auf weiss, der Betreiber habe
        # unbegrenzt gesetzt. Gesetzt hat er nichts, und er sucht den Fehler
        # danach im Memory-Service statt im leeren Feld.
        #
        # Die Zuordnung „welches Feld liest ``None`` wie“ wird hier bewusst nur
        # benutzt und nicht ein zweites Mal getroffen; sie steht einmal in
        # ``FELDER_OHNE_UNBEGRENZT``. Ein kuenftiges Feld dieser Art ordnet sich
        # dort ein und ist damit auch in diesem Protokoll sofort richtig
        # beschrieben, statt still in den falschen Schluessel zu rutschen.
        leere_felder = {field for field, value in values.items() if value is None}
        audit_service.record_privileged_action(
            db,
            user_id=actor.id,
            action="ai.role_limits.updated",
            target_type="role",
            target_id=role.id,
            details={
                "configured_fields": list(ai_limit_service.LIMIT_FIELDS),
                "unlimited_fields": sorted(
                    leere_felder - ai_limit_service.FELDER_OHNE_UNBEGRENZT
                ),
                # Beide Listen immer mitschreiben, auch leer: eine fehlende
                # Liste liesse sich spaeter als „damals gab es das Feld noch
                # nicht“ oder als „nichts leer gelassen“ lesen, und im Trail ist
                # das ein Unterschied.
                "unset_fields": sorted(
                    leere_felder & ai_limit_service.FELDER_OHNE_UNBEGRENZT
                ),
            },
        )
        db.commit()
        db.refresh(role)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="KI-Limits konnten wegen einer gleichzeitigen Änderung nicht gespeichert werden",
        ) from exc
    return _role_response(db, role)


@router.get("/settings/web-search", response_model=AiWebSearchStatus)
def get_web_search_status(
    _: User = Depends(require_global("panel.settings.read")),
) -> AiWebSearchStatus:
    """Nur ob ein Schluessel hinterlegt ist — nie der Schluessel selbst."""
    from services import ai_web_search_service

    return AiWebSearchStatus(configured=ai_web_search_service.is_configured())


@router.put("/settings/web-search", response_model=AiWebSearchStatus)
def set_web_search_key(
    payload: AiWebSearchKeyUpdate,
    actor: User = Depends(require_global("panel.settings.write")),
    _: None = Depends(verify_csrf),
) -> AiWebSearchStatus:
    """Hinterlegt oder entfernt den Suchschluessel.

    Ein leerer Wert entfernt ihn — dann verschwindet auch das Werkzeug aus dem
    Katalog, statt bei jedem Versuch zu scheitern.
    """
    from services import ai_web_search_service

    secret = payload.api_key.get_secret_value() if payload.api_key else ""
    try:
        ai_web_search_service.store_api_key(secret)
    except DisSidecarError as exc:
        raise HTTPException(
            status_code=503, detail="Suchschluessel konnte nicht sicher gespeichert werden"
        ) from exc

    configured = ai_web_search_service.is_configured()
    with SessionLocal() as audit_db:
        audit_service.record_privileged_action(
            audit_db,
            user_id=actor.id,
            action="ai.web_search.key.updated",
            target_type="panel_setting",
            target_id=None,
            # Bewusst nur der Zustand, nie ein Teil des Schluessels.
            details={"configured": configured},
        )
        audit_db.commit()
    return AiWebSearchStatus(configured=configured)


@router.get("/settings/learning", response_model=AiLearningPolicyStatus)
def get_learning_policy(
    db: Session = Depends(get_db),
    _: User = Depends(require_global("panel.settings.read")),
) -> AiLearningPolicyStatus:
    from services import ai_learning_policy, ai_skill_service

    return AiLearningPolicyStatus(
        policy=ai_learning_policy.policy(),
        pending_count=len(ai_skill_service.pending_skills(db)),
    )


@router.put("/settings/learning", response_model=AiLearningPolicyStatus)
def set_learning_policy(
    payload: AiLearningPolicyUpdate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_global("panel.settings.write")),
    _: None = Depends(verify_csrf),
) -> AiLearningPolicyStatus:
    """Legt fest, ob und wie die KI global gueltige Skills anlegen darf.

    Ein globaler Skill wirkt fuer jeden Benutzer des Panels, bei einem Hoster
    also fuer alle Kunden. Das ist die einzige Stelle, an der ein Gespraech
    Text in den Kontext fremder Gespraeche bringen kann — deshalb eine eigene
    Entscheidung des Betreibers und keine Voreinstellung im Code.
    """
    from services import ai_learning_policy, ai_skill_service

    try:
        current = ai_learning_policy.set_policy(payload.policy)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Unbekannte Lernpolitik") from exc

    audit_service.record_privileged_action(
        db,
        user_id=actor.id,
        action="ai.learning.policy.updated",
        target_type="panel_setting",
        target_id=None,
        details={"policy": current},
    )
    db.commit()
    return AiLearningPolicyStatus(
        policy=current, pending_count=len(ai_skill_service.pending_skills(db))
    )


@router.get("/settings/context", response_model=AiContextPolicyStatus)
def get_context_policy(
    _: User = Depends(require_global("panel.settings.read")),
) -> AiContextPolicyStatus:
    from services import ai_context_window

    return AiContextPolicyStatus(
        compaction_percent=ai_context_window.schwelle_prozent(),
        min_percent=ai_context_window.MIN_SCHWELLE,
        max_percent=ai_context_window.MAX_SCHWELLE,
    )


@router.put("/settings/context", response_model=AiContextPolicyStatus)
def set_context_policy(
    payload: AiContextPolicyUpdate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_global("panel.settings.write")),
    _: None = Depends(verify_csrf),
) -> AiContextPolicyStatus:
    """Ab wieviel Prozent des Kontextfensters zusammengefasst wird.

    Die Abwaegung dahinter ist Kosten gegen Gedaechtnis, und sie faellt bei
    einem Hoster anders aus als bei einer Privatinstallation: frueh falten heisst
    kleinere und billigere Anfragen, spaet falten heisst, dass die KI mehr vom
    Gespraech woertlich vor sich hat. Beides ist vertretbar — deshalb eine
    Einstellung und keine Konstante.

    Sie wirkt panelweit und nicht je Rolle: sonst waere dieselbe Unterhaltung
    je nachdem, wer sie zuletzt fortgesetzt hat, verschieden stark gefaltet.
    """
    from services import ai_context_window

    try:
        current = ai_context_window.set_schwelle_prozent(payload.compaction_percent)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Ungueltige Faltmarke") from exc

    audit_service.record_privileged_action(
        db,
        user_id=actor.id,
        action="ai.context.compaction.updated",
        target_type="panel_setting",
        target_id=None,
        details={"compaction_percent": current},
    )
    db.commit()
    return AiContextPolicyStatus(
        compaction_percent=current,
        min_percent=ai_context_window.MIN_SCHWELLE,
        max_percent=ai_context_window.MAX_SCHWELLE,
    )


@router.get("/limits/me", response_model=EffectiveAiLimitsResponse)
def get_my_effective_limits(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> EffectiveAiLimitsResponse:
    """Zeigt eigene effektive Grenzen; Nutzung bleibt separat permission-gated."""
    limits = ai_limit_service.resolve_effective_limits(db, user)
    return EffectiveAiLimitsResponse(
        role_ids=effective_user_role_ids(db, user),
        **limits.__dict__,
    )


def _kostenpolitik() -> AiCostPolicyStatus:
    """Waehrung und Kurs fuer die Anzeige — haengt an jeder Verbrauchsantwort.

    Hier stand einmal ``_cents``, eine Umrechnung von Mikroeinheiten in
    aufgerundete Cent. Sie ist entfallen: fuer eine Monatssumme war sie
    harmlos, fuer eine einzelne Anfrage nicht. Die meisten kosten weniger als
    einen Cent, und aufgerundet sah jede gleich teuer aus — genau die Ansicht,
    mit der sich eine Rechnung nicht pruefen laesst. Die API liefert jetzt
    durchgehend Mikroeinheiten, gerundet wird erst beim Anzeigen.
    """
    politik = ai_kosten.politik()
    return AiCostPolicyStatus(
        currency=politik.waehrung,
        usd_rate=format(politik.kurs, "f"),
        available_currencies=list(ai_kosten.WAEHRUNGEN),
        min_rate=format(ai_kosten.MIN_KURS, "f"),
        max_rate=format(ai_kosten.MAX_KURS, "f"),
    )


def _entry(row: ai_usage_service.AiUsageSummary) -> AiUsageEntry:
    return AiUsageEntry(
        user_id=row.user_id,
        username=row.username,
        tokens_today=row.tokens_today,
        tokens_week=row.tokens_week,
        tokens_month=row.tokens_month,
        cost_month_micro_usd=row.cost_month_microunits,
        requests_month=row.requests_month,
        last_request_at=row.last_request_at,
    )


def _event(row: ai_usage_service.AiUsageEventRow) -> AiUsageEventEntry:
    return AiUsageEventEntry(**row.__dict__)


@router.get("/usage/me", response_model=AiUsageMine)
def get_my_usage(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AiUsageMine:
    """Der eigene Verbrauch mit den eigenen Grenzen daneben.

    Bewusst **ohne** Sonderrecht: es sind die eigenen Zahlen, und wer wissen
    will, warum die KI ihn abgewiesen hat, muss sie sehen duerfen. Der Zugang
    zur KI selbst haengt an `ai.chat.use`; das hier ist die Rechnung dazu.
    """
    limits = ai_limit_service.resolve_effective_limits(db, user)
    return AiUsageMine(
        **_entry(ai_usage_service.usage_for_user(db, user)).model_dump(),
        limits=EffectiveAiLimitsResponse(
            role_ids=effective_user_role_ids(db, user), **limits.__dict__
        ),
        cost_policy=_kostenpolitik(),
    )


@router.get("/usage/me/events", response_model=AiUsageEvents)
def get_my_usage_events(
    limit: int = Query(default=ai_usage_service.STANDARD_EVENT_LIMIT, ge=1, le=ai_usage_service.MAX_EVENT_LIMIT),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AiUsageEvents:
    """Die eigenen Anfragen einzeln — der Nachweis hinter den eigenen Summen.

    Ohne Sonderrecht, aus demselben Grund wie `/usage/me`: es sind die eigenen
    Zahlen. Und es sind die, mit denen sich „das kann nicht stimmen" ueberhaupt
    erst pruefen laesst — Zeile fuer Zeile gegen das Dashboard des Anbieters,
    mit der Spalte, die sagt, ob ueberhaupt gemessen wurde.
    """
    rows, mehr = ai_usage_service.usage_events(db, user_id=user.id, limit=limit, offset=offset)
    return AiUsageEvents(
        entries=[_event(row) for row in rows],
        has_more=mehr,
        cost_policy=_kostenpolitik(),
    )


@router.get("/usage/events", response_model=AiUsageEvents)
def get_usage_events(
    limit: int = Query(default=ai_usage_service.STANDARD_EVENT_LIMIT, ge=1, le=ai_usage_service.MAX_EVENT_LIMIT),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _: User = Depends(require_global("ai.usage.read.all")),
) -> AiUsageEvents:
    """Die Anfragen **aller** Benutzer einzeln.

    An demselben Key wie die Uebersicht darueber und nicht an
    `panel.settings.read`: wer fremde Verbraeuche sieht, sieht fremdes
    Nutzungsverhalten — hier sogar zeitlich aufgeloest. Das ist eine eigene
    Entscheidung des Betreibers und kein Nebeneffekt einer Leseberechtigung
    fuer Einstellungen.
    """
    rows, mehr = ai_usage_service.usage_events(db, user_id=None, limit=limit, offset=offset)
    return AiUsageEvents(
        entries=[_event(row) for row in rows],
        has_more=mehr,
        cost_policy=_kostenpolitik(),
    )


@router.get("/usage", response_model=AiUsageOverview)
def get_usage_overview(
    db: Session = Depends(get_db),
    _: User = Depends(require_global("ai.usage.read.all")),
) -> AiUsageOverview:
    """Der Verbrauch **aller** Benutzer — die Wirkung von `ai.usage.read.all`.

    Der Key stand seit dem ersten Entwurf im Katalog und wurde an keiner Stelle
    geprueft; der Rollen-Editor musste ihn als „noch ohne Funktion“ beschriften.
    Das ist derselbe Fall wie frueher bei `ai.web_search.use`: ein Schalter, der
    nichts bewirkt, ist schlimmer als ein fehlender — der Betreiber haelt etwas
    fuer vergeben oder entzogen, was gar nicht existiert.

    Bewusst **nicht** an `panel.settings.read` gehaengt, obwohl die Ansicht dort
    steht: wer Verbraeuche einsehen darf, sieht damit das Nutzungsverhalten
    fremder Kunden. Das ist eine eigene Entscheidung und kein Nebeneffekt der
    Einstellungsberechtigung — genau deswegen gibt es den eigenen Key.
    """
    rows = ai_usage_service.usage_overview(db)
    entries = [_entry(row) for row in rows]
    return AiUsageOverview(
        entries=entries,
        total_tokens_month=sum(entry.tokens_month for entry in entries),
        total_cost_month_micro_usd=sum(row.cost_month_microunits for row in rows),
        cost_policy=_kostenpolitik(),
    )


@router.get("/settings/cost", response_model=AiCostPolicyStatus)
def get_cost_policy(
    _: User = Depends(require_global("panel.settings.read")),
) -> AiCostPolicyStatus:
    return _kostenpolitik()


@router.put("/settings/cost", response_model=AiCostPolicyStatus)
def set_cost_policy(
    payload: AiCostPolicyUpdate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_global("panel.settings.write")),
    _: None = Depends(verify_csrf),
) -> AiCostPolicyStatus:
    """In welcher Waehrung der Betreiber seine KI-Kosten liest.

    Ausdruecklich nur die **Anzeige**. Gebucht wird weiter in US-Cent, weil der
    Anbieter in USD abrechnet; eine Umrechnung vor der Buchung waere eine
    zweite Fehlerquelle, und ein Kurs, der sich taeglich aendert, wuerde
    rueckwirkend Zeilen veraendern, die laengst bezahlt sind.

    Der Kurs kommt vom Betreiber und nicht aus dem Netz. Ein Kursdienst waere
    ein weiterer Fremdzugriff, den ein selbstgehostetes Panel weder erklaeren
    noch abschalten kann — fuer eine Zahl, die niemand auf den Cent braucht.
    """
    try:
        politik = ai_kosten.setzen(
            neue_waehrung=payload.currency, neuer_kurs=payload.usd_rate
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    audit_service.record_privileged_action(
        db,
        user_id=actor.id,
        action="ai.cost.policy.updated",
        target_type="panel_setting",
        target_id=None,
        details={"currency": politik.waehrung, "usd_rate": format(politik.kurs, "f")},
    )
    db.commit()
    return _kostenpolitik()
