"""Provider-Routen mit strikt getrennten Berechtigungen.

Zwei Haelften: `/settings/providers/*` gehoert dem Betreiber (`panel.settings.*`),
`/providers` ist die Auswahlliste fuer den Chat (`ai.chat.use`).

Es gab hier eine dritte Haelfte — vier Endpunkte, unter denen jeder Benutzer
einen eigenen API-Key hinterlegen konnte. Sie sind entfallen: Schluessel, Modell
und Providerkonfiguration liegen beim Betreiber, weil ein Nutzerschluessel in
einem gehosteten Panel ein zweiter Abrechnungspfad neben dem kalkulierten waere.
"""

import logging
from contextlib import contextmanager

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.concurrency import run_in_threadpool
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
    ai_tts,
    audit_service,
)
from services.ai_provider_service import AiProviderConfigurationError
from services.dis_client import DisSidecarError
from services.openai_compatible_adapter import (
    AiProviderRequestError,
    StreamUsage,
    stream_chat_completion,
)


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai", tags=["ai-providers"])


def _adresse_zum_anzeigen(provider: AiProvider) -> str | None:
    """Die Adresse dieses Zugangs, oder ``None`` — aber nie ein Fehler.

    ``None`` deckt zwei Fälle ab, und beide sind Zeilen, die der Betreiber
    **sehen** muss, um sie zu reparieren:

    * Diese Version kennt den Anbieter nicht. Genau solche Zeilen parkt die
      Migration `20260811_01` mit leerem `provider_kind`.
    * Der Anbieter braucht einen Ressourcennamen, und in der Zeile steht
      keiner oder ein unbrauchbarer. Das kann eine halb angelegte Zeile sein
      oder eine, die auf einem anderen Weg als über das Formular entstanden
      ist.

    Der zweite Fall ist der Grund für diese Funktion. `base_url()` wirft dort
    inzwischen — richtig so, denn sie steht unmittelbar vor dem HTTP-Aufruf —,
    und ein geworfener Fehler beim blossen **Auflisten** hätte die ganze
    Einstellungsseite mit einer 500 beantwortet: eine unbrauchbare Zeile hätte
    alle anderen mitgenommen, und ausgerechnet die Seite, auf der man sie
    korrigiert, wäre nicht mehr erreichbar gewesen.
    """
    if not ai_provider_registry.bekannt(provider.provider_kind):
        return None
    try:
        return ai_provider_service.base_url(provider)
    except AiProviderConfigurationError:
        return None


def _admin_response(provider: AiProvider) -> AiProviderResponse:
    return AiProviderResponse(
        id=provider.id,
        name=provider.name,
        provider_kind=provider.provider_kind,
        # Abgeleitet, nicht gespeichert — eine Kopie in der Zeile wuerde nach
        # einer Änderung an der Registry still veralten.
        base_url=_adresse_zum_anzeigen(provider),
        default_model=provider.default_model,
        # Roh aus der Zeile, ``None`` bleibt ``None``. Hier die Standardstimme
        # einzusetzen wäre bequem und falsch: das Formular zeigte dann eine
        # Wahl, die der Betreiber nie getroffen hat, und speicherte sie beim
        # nächsten Klick auf „Speichern" als seine.
        default_voice=provider.default_voice,
        transcription_model=provider.transcription_model,
        worker_model=provider.worker_model,
        worker_reasoning_effort=provider.worker_reasoning_effort,
        azure_resource_name=provider.azure_resource_name,
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


@contextmanager
def _provider_fehler_uebersetzen(db: Session):
    """Uebersetzt die Fehler des Provider-Service in HTTP-Antworten.

    Stand woertlich gleich in `create_provider` und `update_provider`. Eine
    neue Fehlerklasse im Service musste an beiden Orten nachgezogen werden;
    vergisst man einen, antwortet derselbe Fehler einmal mit 400 und einmal
    als nackter 500.
    """
    try:
        yield
    except ai_provider_service.AiProviderConfigurationError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Provider-Name ist bereits vergeben") from exc
    except DisSidecarError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="Provider-Key konnte nicht sicher gespeichert werden") from exc


@router.post("/settings/providers", response_model=AiProviderResponse, status_code=status.HTTP_201_CREATED)
def create_provider(
    payload: AiProviderCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_global("panel.settings.write")),
    _: None = Depends(verify_csrf),
) -> AiProviderResponse:
    with _provider_fehler_uebersetzen(db):
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
    with _provider_fehler_uebersetzen(db):
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
        raise HTTPException(status_code=409, detail="Provider konnte nicht gelöscht werden") from exc


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
        # Im Threadpool wie an jeder anderen Stelle dieses Routers: die
        # Entschluesselung ist ein synchroner HTTP-Gang zum DIS-Sidecar mit
        # 15-s-Timeout, und ein haengender Sidecar friere sonst den gesamten
        # Event-Loop ein.
        api_key = await run_in_threadpool(
            ai_provider_service.resolve_api_key, db, provider, actor.id
        )
    except DisSidecarError as exc:
        raise HTTPException(
            status_code=503, detail="Provider-Key konnte nicht gelesen werden"
        ) from exc
    if provider.requires_api_key and not api_key:
        return AiProviderTestResponse(
            ok=False, code="AI_PROVIDER_KEY_MISSING", detail=None
        )

    # Ein Stimmzugang wird gesprochen geprüft und nicht getippt. Der Chattest
    # unten schickt ein „ping" an `/chat/completions` — eine Adresse, die es
    # bei ElevenLabs gar nicht gibt. Der Betreiber bekäme eine Fehlermeldung
    # für einen völlig richtig eingerichteten Zugang.
    if ai_provider_service.spricht(provider, ai_provider_registry.TTS):
        return await _stimmzugang_pruefen(provider, api_key or "")

    test_model = provider.default_model or provider.transcription_model
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
            model=test_model,
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


async def _stimmzugang_pruefen(
    provider: AiProvider, api_key: str
) -> AiProviderTestResponse:
    """Die Probe für einen Stimmzugang: Sitzung auf, Sitzung zu.

    Geprüft wird dasselbe wie im Betrieb — Adresse, Schlüssel, Stimme und
    Modell zusammen. Schon der Handschlag entscheidet, und er kostet nichts:
    abgerechnet werden Zeichen, und es geht keines hinaus.

    Ohne hinterlegte Stimme wird gar nicht erst gefragt. Die Kennung steht im
    Pfad der Adresse; ohne sie zeigte die Anfrage auf einen Endpunkt, den es
    nicht gibt, und der Betreiber läse „nicht erreichbar", wo „keine Stimme
    ausgewählt" gemeint ist.
    """
    grund = ai_tts.unmoeglich(provider.provider_kind)
    if grund is not None:
        return AiProviderTestResponse(
            ok=False, code="AI_PROVIDER_UNAVAILABLE", detail=grund
        )
    stimme = (provider.default_voice or "").strip()
    if not stimme:
        return AiProviderTestResponse(
            ok=False, code="AI_PROVIDER_VOICE_MISSING", detail=None
        )
    weg = ai_tts.stimmweg(provider.provider_kind)
    adresse = weg.verbindungsadresse(
        ai_provider_service.base_url(provider), stimme, provider.default_model
    )
    try:
        await weg.pruefen(adresse, api_key)
    except Exception as fehler:
        # Der Wortlaut des Anbieters bleibt im Protokoll. Nach aussen geht der
        # Code — er sagt dem Betreiber, was zu tun ist, ohne Kontonamen und
        # Kontingentstände mitzuschicken.
        code = weg.probe_fehlercode(fehler)
        logger.info(
            "Stimmprobe fehlgeschlagen provider=%s code=%s error=%s",
            provider.id, code, type(fehler).__name__,
        )
        return AiProviderTestResponse(ok=False, code=code, detail=None)
    return AiProviderTestResponse(ok=True, code=None, detail=None)


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
            protokoll=spec.protokoll,
            katalog_braucht_schluessel=spec.katalog_braucht_schluessel,
            ressource_noetig=spec.ressource_noetig,
            # Aus der Registry abgeleitet und nicht als eigenes Feld dort
            # gefuehrt: „hat eine Katalogadresse" **ist** die Antwort auf
            # „fuehrt eine Modelliste". Ein zweites Feld waere eine zweite
            # Wahrheit, die irgendwann auseinanderliefe.
            fuehrt_katalog=spec.catalog_url is not None,
            # Dieselbe Ableitung wie oben und aus demselben Grund: „hat einen
            # Weg zum Zuhoeren" **ist** die Antwort auf „kann hoeren". Es ist
            # genau die Bedingung, an der `routers/ai_voice.py` einen Zugang
            # fuer den Sprachmodus annimmt oder ueberspringt.
            kann_hoeren=bool(spec.gehoer_wege),
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
    provider_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_global("panel.settings.read")),
) -> list[AiCatalogModelResponse]:
    """Die Modelle eines Anbieters — die Auswahl statt eines Textfelds.

    Der Betreiber tippte den Modellnamen bisher ab. Ein Tippfehler fiel erst
    beim Testaufruf auf, und ueber die Faehigkeiten des Modells wusste MSM so
    oder so nichts. Aus dem Katalog gewaehlt ist beides geloest: der Name stimmt,
    und die Denkstufen stehen daneben.

    ``refresh=true`` umgeht den Zwischenspeicher — der Knopf „Modelle neu
    laden“. Der haeufigste Fall ist naemlich nicht das unbekannte Modell,
    sondern der ein paar Stunden alte Katalog.

    ``provider_id`` nennt den Zugang, dessen Schluessel den Katalog holen soll.
    Gebraucht wird das nur von Anbietern mit ``katalog_braucht_schluessel``:
    OpenRouter gibt seine Liste offen heraus, ElevenLabs nicht. Fehlt der Schluessel
    dort, kommt eine **leere Liste** und kein Fehler — beim Anlegen eines
    Zugangs gibt es die Zeile mit dem Schluessel naemlich noch gar nicht, und
    eine Fehlermeldung an dieser Stelle waere die Meldung eines Normalzustands.
    Die Oberflaeche sagt dann „erst Schluessel speichern, dann Modell waehlen".
    """
    if not ai_provider_registry.bekannt(kind):
        raise HTTPException(status_code=404, detail="Unbekannter KI-Anbieter")

    schluessel: str | None = None
    if ai_provider_registry.anbieter(kind).katalog_braucht_schluessel:
        provider = db.get(AiProvider, provider_id) if provider_id else None
        # Der genannte Zugang muss zu **diesem** Anbieter gehoeren. Sonst holte
        # eine falsch gesetzte Kennung den Schluessel eines fremden Zugangs und
        # schickte ihn an eine Adresse, fuer die er nicht ausgestellt wurde.
        if provider is None or provider.provider_kind != kind:
            return []
        schluessel = await run_in_threadpool(
            ai_provider_service.resolve_api_key, db, provider, user.id
        )
        if not schluessel:
            return []

    modelle = await ai_model_catalog.modelle(
        request.app.state.ai_http_client,
        kind,
        erzwingen=refresh,
        schluessel=schluessel,
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
            # Die Empfehlung steht in der Anbieterliste und wird hier nur
            # zugeordnet. Trifft sie auf kein Modell — weil der Anbieter die
            # Kennung umbenannt oder abgekuendigt hat —, bleibt schlicht jede
            # Zeile ohne Marke. Nie eine erfundene daneben.
            recommended=modell.model_id == ai_provider_registry.anbieter(kind).empfehlung,
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
    # Ein Sprachzugang gehoert nicht in die Chatauswahl. Er spricht kein
    # `/chat/completions`, und der Katalog haette ueber sein Modell ohnehin
    # nichts zu sagen — die Zeile stuende mit leeren Denkangaben da und liefe
    # beim Absenden in ein 404 aus `_fuer_chat`. Gefiltert wird hier und nicht
    # erst dort, damit gar nicht erst auswaehlbar ist, was nicht funktioniert.
    providers = [
        provider for provider in providers
        if ai_provider_service.fuer_chat(provider)
    ]
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
        # Der Schluessel gehoert zum Katalogabruf, sobald der Anbieter seine
        # Liste nur gegen einen herausgibt. Hier stand der Aufruf ohne — und
        # das war fuer OpenRouter folgenlos (dessen Liste liegt offen), fuer
        # OpenAI aber der Grund, warum die Denkstufen in der Oberflaeche
        # fehlten: `finde` lief ins 401, gab `None` zurueck, und die drei
        # Felder darunter blieben auf ihren Vorgaben stehen. Sichtbar war davon
        # nichts — kein Fehler, nur eine Auswahl, die es nicht gab.
        #
        # `run_in_threadpool`, weil `resolve_api_key` ueber den DIS-Sidecar geht
        # und das ein **synchrones** `httpx.post` ist; direkt aufgerufen stuende
        # die Ereignisschleife des Panels so lange still (dieselbe Naht wie in
        # `ai_run_service.lauf_beginnen_nebenher` und `_segment_anlaufen`).
        # Genommen wird derselbe Weg wie beim Katalogendpunkt oben, nicht
        # `asyncio.to_thread` — zwei Wege in einer Datei fuer dieselbe Sache
        # sind eine Frage mehr, als der Leser beantworten muss.
        #
        # Nur wenn der Anbieter ihn wirklich braucht: fuer die uebrigen bleibt
        # es beim schluessellosen Abruf, und ein Zugang ohne hinterlegten
        # Schluessel laeuft weiter wie bisher — `finde` vertraegt `None`.
        schluessel: str | None = None
        if ai_provider_registry.anbieter(
            provider.provider_kind
        ).katalog_braucht_schluessel and provider.operator_api_key_encrypted:
            schluessel = await run_in_threadpool(
                ai_provider_service.resolve_api_key, db, provider, user.id
            )
        modell = await ai_model_catalog.finde(
            request.app.state.ai_http_client,
            provider.provider_kind,
            provider.default_model,
            schluessel=schluessel,
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
