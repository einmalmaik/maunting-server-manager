"""Desired-State-Logik der Hoster-Anbindung.

Der Shop meldet nur, welchen Zustand ein Vertrag haben soll ("aktiv",
"gesperrt", "beendet"). Diese Schicht uebersetzt das in genau die Aufrufe, die
das Panel selbst auch verwendet:

- Servererstellung ausschliesslich ueber `server_provisioning_service.provision_server`
- Start/Stop ausschliesslich ueber `server_action_service.request_lifecycle_operation`

Es gibt hier bewusst keine eigene, vereinfachte Servererstellung. Kapazitaets-,
Port-, Blueprint- und Rechtepruefungen gelten fuer eine Shop-Bestellung damit
exakt so wie fuer einen Klick im Panel.

Idempotenz: `(integration, external_service_id)` ist eindeutig. Sendet der Shop
denselben Auftrag nach einem Netzwerkfehler erneut, wird derselbe Service
weitergefuehrt statt ein zweiter Server erzeugt.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import HosterIntegration, HosterService, Server, User
from schemas import ServerCreate
from services import audit_service, permission_service
from services.actor_context import ActorContext
from services.hoster_integration_service import (
    HosterConfigurationError,
    get_product,
    normalize_external_id,
    resolve_identity,
)


logger = logging.getLogger(__name__)

DESIRED_STATES = frozenset({"active", "suspended", "terminated"})

# Vollstaendiges Statusvokabular eines Vertrags. Jeder Wert wird als Webhook
# `service.<status>` an den Shop gemeldet und ist damit Teil des oeffentlichen
# Vertrags — nicht nur ein interner Merker. Deshalb steht er hier an einer
# Stelle statt verstreut in den Zuweisungen: `test_hoster_api_docs_contract`
# prueft gegen diese Liste, dass jeder Wert in `docs/hoster-api.md` erklaert
# ist. Ein neuer Status ohne Doku laesst den Test fehlschlagen.
SERVICE_STATUSES: tuple[str, ...] = (
    "pending",
    "provisioning",
    "ready",
    "suspended",
    "terminating",
    "terminated",
    "failed",
)

# Rechte, die ein Kunde auf seinem eigenen gemieteten Server erhaelt.
# Bewusst NICHT enthalten: Netzwerk- und Ressourcenverwaltung (die bestimmt das
# gebuchte Produkt), Reinstall und Datenbankadministration. `servers.delete`
# ist ohnehin global und damit fuer Kunden unerreichbar.
CUSTOMER_SERVER_PERMISSIONS = (
    "server.view",
    "server.start",
    "server.stop",
    "server.restart",
    "server.config.write",
    "server.console.read",
    "server.console.write",
    "server.files.read",
    "server.files.write",
    "server.files.delete",
    "server.backups.read",
    "server.backups.create",
    "server.backups.restore",
    "server.backups.delete",
    "server.mods.read",
    "server.mods.write",
    "server.mods.toggle",
    # Zielpunkt 17.3: ein Kunde muss die fuer seinen Server noetigen
    # Zugangsdaten selbst hinterlegen koennen, ohne beim Betreiber ein Ticket
    # aufzumachen. Das Recht erlaubt nur die Bindung eigener Credentials an
    # diesen Server — die Geheimnisse selbst bleiben unlesbar.
    "server.credentials.manage",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _set_status(service: HosterService, status: str) -> None:
    """Setzt den Vertragsstatus und haelt ihn im dokumentierten Vokabular.

    Der Status verlaesst MSM als Webhook-Event `service.<status>`. Ein Tippfehler
    waere deshalb kein interner Schoenheitsfehler, sondern ein Ereignis, auf das
    der angebundene Shop nie reagieren wuerde.
    """
    if status not in SERVICE_STATUSES:
        raise HosterConfigurationError(f"Unbekannter Servicestatus: {status}")
    service.status = status


def _actor(db: Session, integration: HosterIntegration, correlation_id: str) -> ActorContext:
    """Baut den Auslöserkontext aus dem Dienstbenutzer der Integration.

    `origin="external"` macht im Audit und in den Tasks sichtbar, dass der
    Vorgang aus einem Shop stammt — bei identischer Fachlogik.
    """
    user = (
        db.query(User)
        .filter(User.id == integration.service_user_id, User.is_active.is_(True))
        .first()
    )
    if user is None:
        raise HosterConfigurationError(
            "Der Dienstbenutzer dieser Integration ist deaktiviert oder geloescht"
        )
    return ActorContext.for_user(user, origin="external", correlation_id=correlation_id)


def _server_name(external_service_id: str) -> str:
    """Stabiler, nicht erratbarer Anzeigename ohne Kundendaten."""
    return f"svc-{external_service_id}"[:128]


def get_service(
    db: Session, integration: HosterIntegration, external_service_id: str
) -> HosterService | None:
    key = normalize_external_id(external_service_id, label="Service-Kennung")
    return (
        db.query(HosterService)
        .filter(
            HosterService.integration_id == integration.id,
            HosterService.external_service_id == key,
        )
        .first()
    )


def _record(
    db: Session,
    *,
    integration: HosterIntegration,
    service: HosterService,
    action: str,
    succeeded: bool,
    extra: dict | None = None,
) -> None:
    """Schreibt einen secret-freien Audit-Eintrag mit gemeinsamer Korrelations-ID."""
    audit_service.record_privileged_action(
        db,
        user_id=integration.service_user_id,
        action=action,
        target_type="server" if service.server_id else "hoster_service",
        target_id=service.server_id,
        details={
            "integration": integration.slug,
            "service_id": service.id,
            "desired_state": service.desired_state,
            "status": service.status,
            "succeeded": succeeded,
            **(extra or {}),
        },
        origin="external",
        correlation_id=service.correlation_id,
    )


def _grant_customer_permissions(db: Session, service: HosterService) -> None:
    """Gibt dem Kunden genau die Rechte auf genau seinem Server.

    Bewusst serverbezogen statt ueber eine globale Rolle: ein Kunde darf seinen
    eigenen Server verwalten, sieht fremde Server aber nicht einmal.
    """
    if service.server_id is None:
        return
    permission_service.set_user_server_permissions(
        db,
        service.identity.user_id,
        service.server_id,
        list(CUSTOMER_SERVER_PERMISSIONS),
        granted_by=None,
    )


def _provision(
    db: Session, *, integration: HosterIntegration, service: HosterService
) -> None:
    """Erstellt den Server ueber den gemeinsamen Provisionierungsservice."""
    from services.server_provisioning_service import provision_server

    product = service.product
    if product is None or not product.enabled:
        raise HosterConfigurationError("Produkt ist unbekannt oder deaktiviert")

    request = ServerCreate(
        name=_server_name(service.external_service_id),
        game_type=product.game_type,
        cpu_limit_percent=product.cpu_limit_percent,
        ram_limit_mb=product.ram_limit_mb,
        disk_limit_gb=product.disk_limit_gb,
        node_id=product.node_id,
    )
    _set_status(service, "provisioning")
    service.status_code = None
    db.flush()

    # Der Idempotency-Key bindet die Provisionierung an genau diesen Vertrag.
    # Ein wiederholter Shop-Aufruf trifft damit dieselbe Task und erzeugt keinen
    # zweiten Server — selbst wenn die Zeile hier gleichzeitig gelesen wird.
    result = provision_server(
        db,
        request,
        _actor(db, integration, service.correlation_id),
        idempotency_key=f"hoster-{integration.id}-{service.id}",
    )
    service.server_id = result.server.id
    service.task_id = result.task.id
    _set_status(service, "ready")
    service.status_code = None
    _grant_customer_permissions(db, service)
    if product.backup_interval_hours:
        server = db.query(Server).filter(Server.id == result.server.id).first()
        if server is not None:
            server.backup_interval_hours = product.backup_interval_hours
            from services.scheduler_service import schedule_backup

            schedule_backup(server.id, product.backup_interval_hours)


def _lifecycle(
    db: Session, *, integration: HosterIntegration, service: HosterService, operation: str
) -> None:
    """Startet oder stoppt den Server ueber den gemeinsamen Lifecycle-Service."""
    from services.server_action_service import request_lifecycle_operation

    if service.server_id is None:
        return
    result = request_lifecycle_operation(
        db,
        server_id=service.server_id,
        operation=operation,
        actor=_actor(db, integration, service.correlation_id),
        idempotency_key=f"hoster-{integration.id}-{service.id}-{operation}-{uuid4()}",
    )
    service.task_id = result.get("task_id")


def apply_desired_state(
    db: Session,
    *,
    integration: HosterIntegration,
    external_service_id: str,
    desired_state: str,
    external_subject: str,
    product_key: str | None,
    email: str | None = None,
) -> HosterService:
    """Bringt einen Vertrag in den vom Shop gewuenschten Zustand.

    Der Aufruf ist wiederholbar: derselbe `external_service_id` fuehrt immer
    denselben Vertrag weiter. Fehler werden am Vertrag festgehalten
    (`status="failed"` plus stabiler Fehlercode), damit der Shop sie abfragen
    kann, statt sie nur als HTTP-Fehler zu sehen.
    """
    if desired_state not in DESIRED_STATES:
        raise HosterConfigurationError("Unbekannter Zielzustand")
    key = normalize_external_id(external_service_id, label="Service-Kennung")

    service = get_service(db, integration, key)
    if service is None:
        if desired_state == "terminated":
            # Eine Kuendigung fuer einen nie angelegten Vertrag ist kein Fehler:
            # der gewuenschte Endzustand ist bereits erreicht.
            raise HosterConfigurationError("Unbekannter Service")
        identity = resolve_identity(
            db, integration=integration, external_subject=external_subject, email=email
        )
        if not product_key:
            raise HosterConfigurationError("Fuer einen neuen Service wird eine Produktkennung benoetigt")
        product = get_product(db, integration, product_key)
        service = HosterService(
            integration_id=integration.id,
            external_service_id=key,
            identity_id=identity.id,
            product_id=product.id,
            desired_state=desired_state,
            status="pending",
            correlation_id=str(uuid4()),
        )
        db.add(service)
        try:
            db.flush()
        except IntegrityError as exc:
            # Zwei gleichzeitige Erstaufrufe fuer denselben Vertrag. Der
            # Verlierer uebernimmt den Gewinner statt einen zweiten Server
            # anzulegen — genau die Zusage aus Zielpunkt 15.2.
            db.rollback()
            existing = get_service(db, integration, key)
            if existing is None:
                raise HosterConfigurationError("Service konnte nicht angelegt werden") from exc
            service = existing
        else:
            # Der Vertrag wird festgeschrieben, BEVOR die eigentliche
            # Zustandsaenderung versucht wird. Scheitert die Provisionierung,
            # bleibt so ein abfragbarer Vertrag mit `status="failed"` zurueck —
            # sonst wuerde der Rollback ihn mit entfernen und der Shop haette
            # keinerlei Anhaltspunkt, was mit seiner Bestellung passiert ist.
            db.commit()
            db.refresh(service)
    elif product_key:
        # Tarifwechsel: das Produkt wird uebernommen. Ressourcenaenderungen an
        # einem bereits laufenden Server bleiben eine bewusste Operator-Aktion,
        # weil sie den Server neu starten muessen.
        product = get_product(db, integration, product_key)
        if service.product_id != product.id:
            service.product_id = product.id
            service.status_code = "product_changed_manual_resize_required"

    service.desired_state = desired_state
    service.updated_at = _now()

    try:
        if desired_state == "active":
            if service.server_id is None:
                _provision(db, integration=integration, service=service)
            else:
                _set_status(service, "ready")
                service.terminate_after = None
                _grant_customer_permissions(db, service)
        elif desired_state == "suspended":
            # Gesperrt heisst: Server aus, Kundenrechte entzogen, Daten bleiben.
            # Der Panelaccount des Kunden bleibt ausdruecklich bestehen.
            if service.server_id is not None:
                _lifecycle(db, integration=integration, service=service, operation="stop")
                permission_service.set_user_server_permissions(
                    db, service.identity.user_id, service.server_id, [], granted_by=None
                )
            _set_status(service, "suspended")
        else:
            # Kuendigung: sperren und eine Frist setzen. Es wird hier bewusst
            # nichts geloescht — das uebernimmt spaeter der Aufraeumlauf.
            if service.server_id is not None:
                _lifecycle(db, integration=integration, service=service, operation="stop")
                permission_service.set_user_server_permissions(
                    db, service.identity.user_id, service.server_id, [], granted_by=None
                )
            service.terminate_after = _now() + timedelta(days=integration.terminate_grace_days)
            _set_status(
                service, "terminating" if service.server_id is not None else "terminated"
            )
    except HTTPException as exc:
        db.rollback()
        _fail(db, integration=integration, service_id=service.id, code=_error_code(exc))
        raise
    except HosterConfigurationError:
        db.rollback()
        _fail(db, integration=integration, service_id=service.id, code="hoster_configuration_error")
        raise
    except Exception:
        db.rollback()
        logger.exception("Hoster-Desired-State fehlgeschlagen (integration=%s)", integration.slug)
        _fail(db, integration=integration, service_id=service.id, code="hoster_internal_error")
        raise HTTPException(
            status_code=503,
            detail={"code": "hoster_internal_error", "message": "errors.hoster_internal_error"},
        )

    _record(
        db,
        integration=integration,
        service=service,
        action="hoster.service.applied",
        succeeded=True,
    )
    db.commit()
    db.refresh(service)
    _notify(db, integration=integration, service=service)
    return service


def _error_code(exc: HTTPException) -> str:
    if isinstance(exc.detail, dict):
        code = exc.detail.get("code")
        if isinstance(code, str) and code:
            return code[:64]
    return f"http_{exc.status_code}"


def _fail(db: Session, *, integration: HosterIntegration, service_id: int, code: str) -> None:
    """Haelt einen Fehlschlag am Vertrag fest, ohne den urspruenglichen Fehler zu verlieren."""
    try:
        service = db.query(HosterService).filter(HosterService.id == service_id).first()
        if service is None:
            return
        _set_status(service, "failed")
        service.status_code = code
        service.updated_at = _now()
        _record(
            db,
            integration=integration,
            service=service,
            action="hoster.service.failed",
            succeeded=False,
            extra={"error_code": code},
        )
        db.commit()
        _notify(db, integration=integration, service=service)
    except Exception:
        db.rollback()
        logger.warning("Fehlerstatus des Hoster-Service konnte nicht gespeichert werden")


def _notify(db: Session, *, integration: HosterIntegration, service: HosterService) -> None:
    """Stellt eine Zustandsaenderung fuer den Shop-Webhook ein (best effort)."""
    try:
        from services.hoster_webhook_service import enqueue_service_event

        enqueue_service_event(db, integration=integration, service=service)
    except Exception:
        db.rollback()
        logger.warning("Hoster-Webhook konnte nicht eingestellt werden (service_id=%s)", service.id)


def purge_terminated_services(db: Session, *, now: datetime | None = None) -> int:
    """Loescht Server gekuendigter Vertraege nach Ablauf der Frist.

    Bewusst als eigener Lauf und nicht im Kuendigungsaufruf: eine Kuendigung
    soll nicht in derselben Sekunde alle Daten vernichten. Der Betreiber
    bestimmt die Frist je Integration.
    """
    current = now or _now()
    due = (
        db.query(HosterService)
        .filter(
            HosterService.desired_state == "terminated",
            HosterService.status == "terminating",
            HosterService.terminate_after.isnot(None),
            HosterService.terminate_after <= current,
        )
        .all()
    )
    purged = 0
    for service in due:
        integration = (
            db.query(HosterIntegration)
            .filter(HosterIntegration.id == service.integration_id)
            .first()
        )
        if integration is None:
            continue
        server_id = service.server_id
        try:
            if server_id is not None:
                from services.server_deletion_service import delete_server_completely

                # Derselbe Loeschpfad wie im Panel — inklusive erneuter
                # Rechtepruefung gegen den Dienstbenutzer der Integration.
                delete_server_completely(
                    db,
                    server_id=server_id,
                    actor=_actor(db, integration, service.correlation_id),
                )
            service.server_id = None
            _set_status(service, "terminated")
            service.status_code = None
            service.updated_at = current
            _record(
                db,
                integration=integration,
                service=service,
                action="hoster.service.terminated",
                succeeded=True,
            )
            db.commit()
            _notify(db, integration=integration, service=service)
            purged += 1
        except Exception:
            db.rollback()
            logger.warning(
                "Gekuendigter Hoster-Service konnte nicht geloescht werden (service_id=%s)",
                service.id,
            )
    return purged
