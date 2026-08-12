"""Provider-Routen mit strikt getrennten Berechtigungen.

Zwei Haelften: `/settings/providers/*` gehoert dem Betreiber (`panel.settings.*`),
`/providers` ist die Auswahlliste fuer den Chat (`ai.chat.use`).

Es gab hier eine dritte Haelfte — vier Endpunkte, unter denen jeder Benutzer
einen eigenen API-Key hinterlegen konnte. Sie sind entfallen: Schluessel, Modell
und Providerkonfiguration liegen beim Betreiber, weil ein Nutzerschluessel in
einem gehosteten Panel ein zweiter Abrechnungspfad neben dem kalkulierten waere.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from database import get_db
from dependencies import require_global, verify_csrf
from models import AiProvider, User
from schemas.ai_provider import (
    AiCatalogModelResponse,
    AiProviderAvailableResponse,
    AiProviderCreate,
    AiProviderKindResponse,
    AiProviderResponse,
    AiProviderTestResponse,
    AiProviderUpdate,
)
from services import (
    ai_limit_service,
    ai_model_catalog,
    ai_provider_registry,
    ai_provider_service,
    ai_reasoning,
    audit_service,
)
from services.ai_provider_service import AiProviderConfigurationError
from services.dis_client import DisSidecarError
from services.openai_compatible_adapter import (
    AiProviderRequestError,
    StreamUsage,
    stream_chat_completion,
)


router = APIRouter(prefix="/api/ai", tags=["ai-providers"])


def _admin_response(provider: AiProvider) -> AiProviderResponse:
    return AiProviderResponse(
        id=provider.id,
        name=provider.name,
        provider_kind=provider.provider_kind,
        # Abgeleitet, nicht gespeichert — eine Kopie in der Zeile wuerde nach
        # einer Aenderung an der Registry still veralten.
        base_url=ai_provider_service.base_url(provider),
        default_model=provider.default_model,
        enabled=provider.enabled,
        requires_api_key=provider.requires_api_key,
        operator_key_configured=bool(provider.operator_api_key_encrypted),
        operator_key_hint=provider.operator_api_key_hint,
        token_price_micro_usd_per_million=provider.token_price_micro_usd_per_million,
        updated_at=provider.updated_at,
    )


@router.get("/settings/providers", response_model=list[AiProviderResponse])
def list_provider_settings(
    db: Session = Depends(get_db),
    _: User = Depends(require_global("panel.settings.read")),
) -> list[AiProviderResponse]:
    providers = db.query(AiProvider).order_by(AiProvider.name.asc()).all()
    return [_admin_response(provider) for provider in providers]


@router.post("/settings/providers", response_model=AiProviderResponse, status_code=status.HTTP_201_CREATED)
def create_provider(
    payload: AiProviderCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_global("panel.settings.write")),
    _: None = Depends(verify_csrf),
) -> AiProviderResponse:
    try:
        provider = ai_provider_service.create_provider(
            db,
            **payload.model_dump(exclude={"operator_api_key"}),
            operator_api_key=(
                payload.operator_api_key.get_secret_value() if payload.operator_api_key else None
            ),
        )
        audit_service.record_privileged_action(
            db,
            user_id=actor.id,
            action="ai.provider.created",
            target_type="ai_provider",
            target_id=provider.id,
            details={
                "name": provider.name,
                "provider_kind": provider.provider_kind,
                "model": provider.default_model,
                "operator_key_configured": bool(provider.operator_api_key_encrypted),
            },
        )
        db.commit()
        db.refresh(provider)
        return _admin_response(provider)
    except ai_provider_service.AiProviderConfigurationError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Provider-Name ist bereits vergeben") from exc
    except DisSidecarError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="Provider-Key konnte nicht sicher gespeichert werden") from exc


@router.patch("/settings/providers/{provider_id}", response_model=AiProviderResponse)
def update_provider(
    provider_id: int,
    payload: AiProviderUpdate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_global("panel.settings.write")),
    _: None = Depends(verify_csrf),
) -> AiProviderResponse:
    provider = db.get(AiProvider, provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider nicht gefunden")
    values = payload.model_dump(
        exclude_unset=True,
        exclude={"operator_api_key", "clear_operator_api_key"},
    )
    try:
        ai_provider_service.update_provider(
            db,
            provider,
            values=values,
            operator_api_key=(
                payload.operator_api_key.get_secret_value() if payload.operator_api_key else None
            ),
            clear_operator_api_key=payload.clear_operator_api_key,
        )
        audit_service.record_privileged_action(
            db,
            user_id=actor.id,
            action="ai.provider.updated",
            target_type="ai_provider",
            target_id=provider.id,
            details={
                "changed_fields": sorted(values),
                "key_changed": bool(payload.operator_api_key or payload.clear_operator_api_key),
            },
        )
        db.commit()
        db.refresh(provider)
        return _admin_response(provider)
    except ai_provider_service.AiProviderConfigurationError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Provider-Name ist bereits vergeben") from exc
    except DisSidecarError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="Provider-Key konnte nicht sicher gespeichert werden") from exc


@router.delete("/settings/providers/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_provider(
    provider_id: int,
    db: Session = Depends(get_db),
    actor: User = Depends(require_global("panel.settings.write")),
    _: None = Depends(verify_csrf),
) -> Response:
    provider = db.get(AiProvider, provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider nicht gefunden")
    try:
        audit_service.record_privileged_action(
            db,
            user_id=actor.id,
            action="ai.provider.deleted",
            target_type="ai_provider",
            target_id=provider.id,
            details={"name": provider.name},
        )
        db.delete(provider)
        db.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Provider konnte nicht geloescht werden") from exc


@router.post("/settings/providers/{provider_id}/test", response_model=AiProviderTestResponse)
async def test_provider(
    provider_id: int,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_global("panel.settings.write")),
    _: None = Depends(verify_csrf),
) -> AiProviderTestResponse:
    """Prueft die Providerkonfiguration mit einer echten, winzigen Anfrage.

    Warum das noetig ist: eine falsche Basis-URL, ein Tippfehler im Modellnamen
    und ein abgelaufener Key sind im Chat nicht auseinanderzuhalten — dort
    scheitert die Anfrage erst nach dem Absenden, und die Meldung ist fuer alle
    drei Faelle dieselbe. Hier bekommt der Betreiber die konkrete Antwort des
    Anbieters, bevor ein Benutzer darueber stolpert.

    Der Test laeuft ueber denselben Adapter wie ein echter Chat. Gesendet wird
    eine Ein-Wort-Anfrage ohne Werkzeuge; verbraucht wird das, was ein Anbieter
    dafuer berechnet.
    """
    provider = db.get(AiProvider, provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider nicht gefunden")
    try:
        api_key = ai_provider_service.resolve_api_key(db, provider, actor.id)
    except DisSidecarError as exc:
        raise HTTPException(
            status_code=503, detail="Provider-Key konnte nicht gelesen werden"
        ) from exc
    if provider.requires_api_key and not api_key:
        return AiProviderTestResponse(
            ok=False, code="AI_PROVIDER_KEY_MISSING", detail=None
        )

    usage = StreamUsage()
    try:
        received = False
        async for _chunk in stream_chat_completion(
            request.app.state.ai_http_client,
            provider=provider,
            api_key=api_key,
            messages=[{"role": "user", "content": "ping"}],
            usage=usage,
            tools=None,
        ):
            received = True
        return AiProviderTestResponse(
            ok=True,
            code=None,
            # Ehrlich bleiben: eine leere, aber technisch erfolgreiche Antwort
            # ist kein Fehler, aber auch kein Beweis, dass das Modell taugt.
            detail=None if received else "Der Anbieter hat eine leere Antwort geliefert.",
        )
    except AiProviderConfigurationError as exc:
        return AiProviderTestResponse(ok=False, code="AI_PROVIDER_URL_REJECTED", detail=str(exc))
    except AiProviderRequestError as exc:
        return AiProviderTestResponse(ok=False, code=exc.code, detail=exc.detail)


@router.get("/settings/provider-kinds", response_model=list[AiProviderKindResponse])
def list_provider_kinds(
    _: User = Depends(require_global("panel.settings.read")),
) -> list[AiProviderKindResponse]:
    """Die Anbieter, unter denen der Betreiber waehlen kann.

    Statisch aus `ai_provider_registry` — kein Datenbankzugriff. Ein weiterer
    Anbieter ist dort ein Eintrag und erscheint hier von selbst.
    """
    return [
        AiProviderKindResponse(
            kind=spec.kind, label=spec.label, base_url=spec.base_url,
            key_url=spec.key_url, key_prefix=spec.key_prefix,
        )
        for spec in ai_provider_registry.alle()
    ]


@router.get(
    "/settings/provider-kinds/{kind}/models",
    response_model=list[AiCatalogModelResponse],
)
async def list_catalog_models(
    kind: str,
    request: Request,
    refresh: bool = False,
    _: User = Depends(require_global("panel.settings.read")),
) -> list[AiCatalogModelResponse]:
    """Die Modelle eines Anbieters — die Auswahl statt eines Textfelds.

    Der Betreiber tippte den Modellnamen bisher ab. Ein Tippfehler fiel erst
    beim Testaufruf auf, und ueber die Faehigkeiten des Modells wusste MSM so
    oder so nichts. Aus dem Katalog gewaehlt ist beides geloest: der Name stimmt,
    und die Denkstufen stehen daneben.

    ``refresh=true`` umgeht den Zwischenspeicher — der Knopf „Modelle neu
    laden“. Der haeufigste Fall ist naemlich nicht das unbekannte Modell,
    sondern der ein paar Stunden alte Katalog.
    """
    if not ai_provider_registry.bekannt(kind):
        raise HTTPException(status_code=404, detail="Unbekannter KI-Anbieter")
    modelle = await ai_model_catalog.modelle(
        request.app.state.ai_http_client, kind, erzwingen=refresh
    )
    return [
        AiCatalogModelResponse(
            model_id=modell.model_id,
            name=modell.name,
            reasoning=modell.denkt,
            # Ohne Deckel: der Betreiber soll sehen, was das Modell **kann**.
            # Was ein einzelner Benutzer davon waehlen darf, entscheidet
            # spaeter seine Rolle — das ist eine andere Frage als diese.
            efforts=ai_reasoning.waehlbare_stufen(modell, None),
            default_effort=modell.standard_stufe,
            mandatory=modell.zwingend,
        )
        for modell in modelle
    ]


@router.get("/providers", response_model=list[AiProviderAvailableResponse])
async def list_available_providers(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.chat.use")),
) -> list[AiProviderAvailableResponse]:
    """Was dieser Benutzer im Chat auswaehlen kann — samt erlaubter Denktiefe.

    Eine Auswahl unter dem, was der Betreiber freigegeben hat — keine
    Konfiguration. Ob ein Provider nutzbar ist, haengt seit dem Wegfall von BYOK
    nur noch am Betreiberschluessel; ein Benutzer kann daran nichts aendern und
    bekommt deshalb auch keinen Knopf dafuer.

    Die Denkstufen kommen **fertig geklemmt** heraus: der Katalog sagt, was das
    Modell kann, die Rolle sagt, wie tief dieser Benutzer gehen darf. Die
    Oberflaeche zeigt damit eine Liste, statt Rechte auszuwerten — und die
    verbindliche Pruefung passiert trotzdem erneut beim Senden.

    Der Katalogabruf laeuft aus dem Zwischenspeicher und kostet nichts. Ist er
    leer und der Anbieter nicht erreichbar, bleiben die Denkangaben schlicht
    aus; die Providerauswahl funktioniert weiter.
    """
    providers = db.query(AiProvider).filter(AiProvider.enabled.is_(True)).order_by(AiProvider.name).all()
    deckel = ai_limit_service.resolve_effective_limits(db, user).max_reasoning_effort

    antworten: list[AiProviderAvailableResponse] = []
    for provider in providers:
        antwort = AiProviderAvailableResponse(
            id=provider.id,
            name=provider.name,
            default_model=provider.default_model,
            requires_api_key=provider.requires_api_key,
            operator_key_available=bool(provider.operator_api_key_encrypted),
            available=(
                not provider.requires_api_key
                or bool(provider.operator_api_key_encrypted)
            ),
        )
        modell = await ai_model_catalog.finde(
            request.app.state.ai_http_client,
            provider.provider_kind,
            provider.default_model,
        )
        if modell is not None:
            antwort.reasoning = ai_reasoning.darf_nachdenken(modell, deckel)
            antwort.efforts = ai_reasoning.waehlbare_stufen(modell, deckel)
            antwort.can_disable = ai_reasoning.darf_abschalten(modell)
            antwort.default_effort = (
                modell.standard_stufe if modell.standard_stufe in antwort.efforts else None
            )
        antworten.append(antwort)
    return antworten
