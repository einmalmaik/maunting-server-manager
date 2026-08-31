from __future__ import annotations

import logging
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy.orm import Session
from models import User
from services import permission_service
from services.ai_action_errors import AiActionValidationError
from services.ai_redaction import redact_sensitive_text
from services.ai_tools.base import _function

logger = logging.getLogger(__name__)

def _voice_tool_definitions() -> list[dict]:
    """Sitzungsgebundene Werkzeuge; andere Rollen filtern sie aus."""
    return [
        _function(
            "voice_resolve_latest_proposal",
            "BestÃ¤tigt oder verwirft ausschlieÃŸlich den zuletzt in dieser "
            "Sprachsitzung angezeigten Vorschlag. Nutze dies nur, wenn der "
            "Benutzer dem sichtbaren Vorschlag eindeutig zustimmt oder ihn "
            "eindeutig ablehnt.",
            {
                "decision": {
                    "type": "string",
                    "enum": ["confirm", "reject"],
                },
            },
            ["decision"],
        ),
        _function(
            "voice_set_region_view",
            "Steuert ausschlieÃŸlich die sichtbare Regionalansicht dieser Sprachsitzung. "
            "Nutze es unmittelbar bevor du Ã¼ber Wetter, Nachrichten, soziale BeitrÃ¤ge, Verkehr oder eine Satellitenszene sprichst. "
            "source_id und scene_id mÃ¼ssen aus den zuletzt erhaltenen Regionaldaten stammen.",
            {
                "tab": {"type": "string", "enum": ["overview", "satellite", "news", "social", "traffic", "weather"]},
                "source_id": {"type": "string", "maxLength": 512},
                "scene_id": {"type": "string", "maxLength": 128},
            },
            ["tab"],
        ),
        _function(
            "voice_leave_region_view",
            "SchlieÃŸt die Regionalansicht, wenn das GesprÃ¤ch zu einem Thema ohne Ortsbezug wechselt. "
            "Keine Server-, Log-, Kalender- oder allgemeine Antwort in einer alten Ortsansicht lassen.",
            {},
            [],
        ),
    ]

def voice_control_tool_definitions() -> list[dict]:
    """Nur der Realtime-Transport erhÃ¤lt diese sitzungsgebundenen Tools."""
    return _voice_tool_definitions()

def _region_request(
    db: Session, *, user: User, arguments: dict,
) -> tuple[str, str]:
    """PrÃ¼ft die gemeinsame Berechtigungs- und Eingabegrenze der Regionsanalyse."""
    from services import permission_service

    if not permission_service.has_global_permission(db, user, "ai.satellite.use"):
        raise AiActionValidationError("Satelliten- und Regionsanalyse ist fÃ¼r diesen Benutzer nicht freigegeben")

    location = arguments.get("location")
    if not isinstance(location, str) or not location.strip():
        raise AiActionValidationError("Ort (location) fehlt oder ist ungÃ¼ltig")
    camera = arguments.get("camera", "focus")
    if camera not in {"overview", "focus", "detail"}:
        raise AiActionValidationError("Kameramodus ist ungÃ¼ltig")
    return redact_sensitive_text(location.strip())[:100], camera

def execute_realtime_region_initial(
    db: Session, *, user: User, arguments: dict,
) -> dict:
    """Liefert den ersten, sofort darstellbaren Stand fÃ¼r den Sprachmodus."""
    from services import ai_geo_service

    safe_location, camera = _region_request(db, user=user, arguments=arguments)
    analysis = ai_geo_service.analyze_region_initial(safe_location)
    if analysis.get("status") == "success":
        analysis["camera"] = {"mode": camera, "command_id": str(uuid4())}
    return analysis

def execute_realtime_region_enrichment(
    db: Session,
    *,
    user: User,
    arguments: dict,
    initial: dict,
    prefetch_session_id: str | None = None,
) -> dict:
    """ErgÃ¤nzt einen bereits gezeigten Regionsstand um langsame, optionale Quellen."""
    from services import ai_geo_service, ai_web_search_service, permission_service

    safe_location, _camera = _region_request(db, user=user, arguments=arguments)
    if initial.get("status") != "success":
        return initial

    can_search = permission_service.has_global_permission(db, user, "ai.web_search.use")
    search_configured = can_search and ai_web_search_service.is_configured()

    def regional_news() -> tuple[list[dict], str]:
        if not can_search:
            return [], "not_allowed"
        if not search_configured:
            return [], "not_configured"
        try:
            results = ai_web_search_service.search(
                f"{safe_location} aktuelle Nachrichten Lagebericht",
                5,
                cache_scope=(f"voice:{user.id}:{prefetch_session_id}" if prefetch_session_id else None),
            )
            return results, "available"
        except ai_web_search_service.WebSearchUnavailable as exc:
            return [], exc.code.lower()

    regional_cache_scope = (
        f"regional:{user.id}:{prefetch_session_id}" if prefetch_session_id else None
    )
    # Verkehr, Ã¶ffentliche BeitrÃ¤ge und Weblage dÃ¼rfen den sofort sichtbaren
    # Wetter-/Kartenstand nie aufhalten, laden aber untereinander parallel.
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="msm-region") as executor:
        signals_future = executor.submit(
            ai_geo_service.enrich_region, initial, cache_scope=regional_cache_scope,
        )
        news_future = executor.submit(regional_news)
        signals = signals_future.result()
        news, news_status = news_future.result()
    analysis = dict(initial)
    analysis.update(signals)
    analysis["news"] = news
    analysis["news_status"] = news_status
    return analysis

def _execute_analyze_region(
    db: Session, *, user: User, arguments: dict, prefetch_session_id: str | None = None,
) -> dict:
    """FÃ¼hrt fÃ¼r den Chat die vollstÃ¤ndige regionale Analyse aus."""
    initial = execute_realtime_region_initial(db, user=user, arguments=arguments)
    return execute_realtime_region_enrichment(
        db,
        user=user,
        arguments=arguments,
        initial=initial,
        prefetch_session_id=prefetch_session_id,
    )

def _execute_control_region_camera(db: Session, *, user: User, arguments: dict) -> dict:
    """Erzeugt einen einmaligen Kamerabefehl ohne erneute Regionsanalyse."""
    from services import ai_geo_service

    if not permission_service.has_global_permission(db, user, "ai.satellite.use"):
        raise AiActionValidationError("Kartensteuerung ist fÃ¼r diesen Benutzer nicht freigegeben")
    if set(arguments) - {"action", "location"}:
        raise AiActionValidationError("Kartensteuerung hat ungÃ¼ltige Argumente")
    action = arguments.get("action")
    if action not in {"zoom_in", "zoom_out", "overview", "focus_location"}:
        raise AiActionValidationError("Kartenaktion ist ungÃ¼ltig")
    if action == "focus_location":
        location = arguments.get("location")
        if not isinstance(location, str) or not location.strip():
            raise AiActionValidationError("SehenswÃ¼rdigkeit (location) fehlt oder ist ungÃ¼ltig")
        safe_location = redact_sensitive_text(location.strip())[:100]
        geo = ai_geo_service.geocode_location(safe_location)
        if not geo:
            raise AiActionValidationError("SehenswÃ¼rdigkeit konnte nicht geocodiert werden")
        return {
            "action": action,
            "command_id": str(uuid4()),
            "location": geo["name"],
            "country": geo["country"],
            "coordinates": {
                "latitude": geo["latitude"],
                "longitude": geo["longitude"],
                "bbox": geo["bbox"],
            },
        }
    if set(arguments) != {"action"}:
        raise AiActionValidationError("location ist nur fÃ¼r focus_location zulÃ¤ssig")
    return {"action": action, "command_id": str(uuid4())}
