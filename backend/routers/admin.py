from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session, selectinload

from database import get_db
from models import (
    AuditLog,
    BackupCode,
    JwtBlacklist,
    RefreshToken,
    Server,
    ServerPermission,
    User,
)
from schemas.user import AdminUserCreate, UserResponse, UserUpdate
from schemas.role import (
    AssignRoleRequest,
    AssignRolesRequest,
    ServerPermissionsRequest,
    ServerPermissionsResponse,
)
from dependencies import require_global, verify_csrf
from services import AuthService, EmailService
from services.email_verification_service import EmailVerificationService
from services.permission_service import list_user_server_permission_keys
from services.permission_catalog import SYSTEM_ROLE_USER
from services.role_service import (
    effective_user_role_permission_keys,
    get_role_by_name,
    set_user_roles,
)
from services import audit_service, postgres_service, rechtevergabe_service
from services.postgres_service import PostgresServiceError
from services.user_deletion_service import prepare_user_deletion

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _http(fehler: rechtevergabe_service.RechteFehler) -> HTTPException:
    return HTTPException(status_code=fehler.status_code, detail=fehler.detail)


def _ensure_no_global_escalation(
    db: Session, actor: User, required_keys: list[str]
) -> None:
    """Die Schranke steht in `rechtevergabe_service`; hier nur als HTTP-Antwort."""
    try:
        rechtevergabe_service.ensure_no_global_escalation(db, actor, required_keys)
    except rechtevergabe_service.RechteFehler as fehler:
        raise _http(fehler) from None


def _ensure_no_server_escalation(
    db: Session,
    actor: User,
    server_id: int,
    required_keys: list[str],
) -> None:
    """Die Schranke steht in `rechtevergabe_service`; hier nur als HTTP-Antwort.

    Warum gegen die **direkte** Berechtigung geprueft wird und nicht gegen die
    geliehene Teamberechtigung, steht dort.
    """
    try:
        rechtevergabe_service.ensure_no_server_escalation(
            db, actor, server_id, required_keys
        )
    except rechtevergabe_service.RechteFehler as fehler:
        raise _http(fehler) from None


@router.get("/users", response_model=list[UserResponse])
def list_users(
    db: Session = Depends(get_db),
    _: User = Depends(require_global("users.read")),
) -> list[User]:
    # Rollen mitladen: sonst kostet role_ids je Benutzer eine eigene Abfrage.
    return db.query(User).options(selectinload(User.role_assignments)).all()


@router.get("/users/{user_id}", response_model=UserResponse)
def get_user(
    user_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_global("users.read")),
) -> User:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User nicht gefunden")
    return user


@router.patch("/users/{user_id}", response_model=UserResponse)
def update_user(
    user_id: int,
    req: UserUpdate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_global("users.manage")),
    __: None = Depends(verify_csrf),
) -> User:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User nicht gefunden")
    # Owner-Account ist hart geschuetzt — niemand ausser dem Owner selbst
    # darf is_active, email oder 2FA des Owners aendern.
    if user.is_owner and not actor.is_owner:
        raise HTTPException(status_code=403, detail="Owner-Account kann nur vom Owner geaendert werden")
    # Selbst der Owner darf den Owner-Account nicht deaktivieren — sonst
    # waere das Panel nach dem Logout dauerhaft ausgesperrt (kein
    # Super-Admin-Recovery vorhanden).
    if user.is_owner and req.is_active is False:
        raise HTTPException(status_code=400, detail="Owner-Account darf nicht deaktiviert werden")

    # Dieselbe Grenze wie beim Loeschen darunter — und aus demselben Grund.
    #
    # Geschuetzt war bisher nur der Owner. Ein Konto mit `users.manage` und
    # sonst nichts konnte damit jeden Administrator uebernehmen, ohne je ein
    # Recht zu vergeben:
    #
    #   1. `PATCH /api/admin/users/{admin}` mit neuer E-Mail und
    #      `two_factor_enabled: false`. Der Setter schreibt `email_hash` mit.
    #   2. `POST /api/auth/forgot-password` mit dieser Adresse — der Reset-Link
    #      geht ins eigene Postfach.
    #   3. Passwort setzen, anmelden. Der zweite Faktor ist in Schritt 1 gefallen.
    #
    # Rechte lassen sich also nicht nur vergeben, sondern auch **erben**, indem
    # man sich das Konto nimmt, das sie hat. `delete_user` zieht diese Grenze
    # seit jeher; hier fehlte sie.
    _ensure_no_global_escalation(
        db,
        actor,
        effective_user_role_permission_keys(db, user),
    )

    if req.email is not None:
        user.email = req.email
    if req.is_active is not None:
        user.is_active = req.is_active
    if req.two_factor_enabled is not None:
        user.two_factor_enabled = req.two_factor_enabled
    if req.time_zone is not None:
        user.time_zone = req.time_zone
    db.commit()
    db.refresh(user)
    return user


@router.delete("/users/{user_id}")
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    actor: User = Depends(require_global("users.manage")),
    __: None = Depends(verify_csrf),
) -> dict:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User nicht gefunden")
    if user.is_owner:
        if not actor.is_owner:
            raise HTTPException(status_code=403, detail="Nur ein Owner kann einen anderen Owner-Account löschen")
        owner_count = db.query(User).filter(User.is_owner == True, User.is_active == True).count()
        if owner_count <= 1:
            raise HTTPException(status_code=403, detail="Owner kann nicht gelöscht werden")

    # Selbst-Loeschen verhindern — Account wuerde sofort wegbrechen, Session-
    # Cookies wuerden ins Leere zeigen und der User koennte nicht mehr
    # eingreifen, falls die Aktion versehentlich passiert.
    if user.id == actor.id:
        raise HTTPException(status_code=400, detail="Du kannst dich nicht selbst löschen")

    # Eskalations-Schutz: Wer einen User loescht, dessen Rolle Keys haelt, die
    # man selbst nicht hat, koennte indirekt Berechtigungen verschieben
    # (z.B. ein Non-Owner-Admin loescht einen Admin). Nur Subset zulassen.
    _ensure_no_global_escalation(
        db,
        actor,
        effective_user_role_permission_keys(db, user),
    )

    # Vor jedem Schreibzugriff: die drei RESTRICT-Fremdschluessel, die auf
    # diesen Benutzer zeigen. Ohne diese Zeile lief `db.commit()` unten in den
    # Fremdschluessel und die ungefangene IntegrityError wurde zu einer nackten
    # HTTP 500 — der Gruender eines Teams war damit dauerhaft nicht loeschbar,
    # ohne dass irgendwo stand, warum.
    prepare_user_deletion(db, user)

    # FK-Cleanup: AuditLogs entkoppeln, Sessions & BackupCodes loeschen.
    db.query(AuditLog).filter(AuditLog.user_id == user_id).update(
        {AuditLog.user_id: None}, synchronize_session=False
    )
    db.query(RefreshToken).filter(RefreshToken.user_id == user_id).delete(synchronize_session=False)
    db.query(JwtBlacklist).filter(JwtBlacklist.user_id == user_id).delete(synchronize_session=False)
    db.query(BackupCode).filter(BackupCode.user_id == user_id).delete(synchronize_session=False)
    db.flush()

    db.delete(user)
    db.commit()
    return {"message": "User gelöscht"}


@router.post("/users", response_model=UserResponse, status_code=201)
async def create_user_admin(
    req: AdminUserCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_global("users.manage")),
    _: None = Depends(verify_csrf),
) -> User:
    # Autorisierung VOR jeder DB-Aenderung: ein Non-Owner darf keinen
    # Owner-Account anlegen. Frueher hat create_user() den User bereits
    # committet, bevor die 403 geprueft wurde — das hinterliess phantom
    # Accounts mit dem gewuenschten Username.
    if req.is_owner and not actor.is_owner:
        raise HTTPException(status_code=403, detail="Nur Owner kann is_owner setzen")
    if AuthService.get_user_by_username(db, req.username):
        raise HTTPException(status_code=400, detail="Username bereits vergeben")
    if AuthService.get_user_by_email(db, req.email):
        raise HTTPException(status_code=400, detail="E-Mail bereits vergeben")
    user = AuthService.create_user(db, req.username, req.email, req.password)
    if req.is_owner:
        user.is_owner = True
    else:
        # Sicherer Default: System-Rolle `user` (entspricht der Lifespan-Migration
        # fuer bestehende Accounts; verhindert Accounts mit role_id=NULL).
        default_role = get_role_by_name(db, SYSTEM_ROLE_USER)
        if default_role is not None:
            user.role_id = default_role.id
    if req.auto_verify:
        user.email_verified = True
    else:
        if EmailService.is_configured():
            code = EmailVerificationService.create_verification(db, req.email, "setup")
            await EmailService.send_verification_code_email(req.email, req.username, code)
    db.commit()
    db.refresh(user)
    if not user.is_owner and user.role_id is not None:
        set_user_roles(db, user, [user.role_id])
    return user


# ── Rollen-Zuweisung ──────────────────────────────────────────────────


@router.patch("/users/{user_id}/role", response_model=UserResponse)
def assign_role(
    user_id: int,
    req: AssignRoleRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(require_global("users.permissions.manage")),
    __: None = Depends(verify_csrf),
) -> User:
    """Kompatibilitätsroute: ersetzt die Rollenmengen durch höchstens eine Rolle."""
    role_ids = [] if req.role_id is None else [req.role_id]
    return _assign_roles(user_id, role_ids, db, actor)


@router.put("/users/{user_id}/roles", response_model=UserResponse)
def assign_roles(
    user_id: int,
    req: AssignRolesRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(require_global("users.permissions.manage")),
    __: None = Depends(verify_csrf),
) -> User:
    """Ersetzt die globalen Rollen eines Benutzers durch eine validierte Menge."""
    return _assign_roles(user_id, req.role_ids, db, actor)


def _assign_roles(
    user_id: int,
    requested_role_ids: list[int],
    db: Session,
    actor: User,
) -> User:
    """Prüft Eskalationsgrenzen und speichert eine Multi-Role-Zuweisung.

    Die Grenzen stehen in `rechtevergabe_service.assign_roles` — dieselbe
    Funktion ruft die KI.
    """
    try:
        return rechtevergabe_service.assign_roles(db, actor, user_id, requested_role_ids)
    except rechtevergabe_service.RechteFehler as fehler:
        raise _http(fehler) from None


# ── Server-Permissions (Per-User-per-Server-Delegation) ───────────────


@router.get(
    "/users/{user_id}/server-permissions/{server_id}",
    response_model=ServerPermissionsResponse,
)
def get_server_permissions(
    user_id: int,
    server_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_global("users.permissions.manage")),
) -> ServerPermissionsResponse:
    if not db.query(User.id).filter(User.id == user_id).first():
        raise HTTPException(status_code=404, detail="User nicht gefunden")
    if not db.query(Server.id).filter(Server.id == server_id).first():
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    keys = list_user_server_permission_keys(db, user_id, server_id)
    return ServerPermissionsResponse(server_id=server_id, permissions=sorted(keys))


@router.put(
    "/users/{user_id}/server-permissions/{server_id}",
    response_model=ServerPermissionsResponse,
)
async def set_server_permissions(
    user_id: int,
    server_id: int,
    req: ServerPermissionsRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(require_global("users.permissions.manage")),
    _: None = Depends(verify_csrf),
) -> ServerPermissionsResponse:
    # Die Grenzen (Ziel, Owner, Eskalation in beide Richtungen) stehen in
    # `rechtevergabe_service.set_server_permissions` — dieselbe Funktion ruft
    # die KI.
    try:
        keys, erstmals, target_user, server = rechtevergabe_service.set_server_permissions(
            db, actor, user_id, server_id, req.permissions
        )
    except rechtevergabe_service.RechteFehler as fehler:
        raise _http(fehler) from None

    if erstmals and EmailService.is_configured() and target_user.email_notifications:
        await EmailService.send_user_added_to_server_notification(
            target_user.email, target_user.username, server.name, actor.username
        )

    return ServerPermissionsResponse(server_id=server_id, permissions=keys)


@router.delete(
    "/users/{user_id}/server-permissions/{server_id}",
    status_code=204,
)
def revoke_server_permissions(
    user_id: int,
    server_id: int,
    db: Session = Depends(get_db),
    actor: User = Depends(require_global("users.permissions.manage")),
    __: None = Depends(verify_csrf),
) -> None:
    # Spiegelt set_server_permissions(permissions=[]): Actor muss die
    # bestehenden Keys auf diesem Server selbst besitzen, sonst kann er
    # nicht entwaffnen.
    existing_keys = list_user_server_permission_keys(db, user_id, server_id)
    if existing_keys:
        _ensure_no_server_escalation(db, actor, server_id, list(existing_keys))
    db.query(ServerPermission).filter(
        ServerPermission.user_id == user_id,
        ServerPermission.server_id == server_id,
    ).delete()
    db.commit()


@router.get("/users/{user_id}/server-permissions", response_model=list[ServerPermissionsResponse])
def list_server_permissions_for_user(
    user_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_global("users.permissions.manage")),
) -> list[ServerPermissionsResponse]:
    rows = (
        db.query(ServerPermission.server_id, ServerPermission.permission_key)
        .filter(ServerPermission.user_id == user_id)
        .all()
    )
    grouped: dict[int, list[str]] = {}
    for server_id, key in rows:
        grouped.setdefault(server_id, []).append(key)
    return [
        ServerPermissionsResponse(server_id=sid, permissions=sorted(keys))
        for sid, keys in sorted(grouped.items())
    ]


# ── SaaS-Betrieb: Audit-Liste + Managed-Postgres-Admin-Rotation ─────────────


class AuditLogOut(BaseModel):
    """Oeffentliche Audit-Darstellung ohne Secrets."""

    id: int
    user_id: int | None
    action: str
    target_type: str | None
    target_id: str | None
    origin: str
    correlation_id: str | None
    details: str | None
    created_at: datetime | None

    class Config:
        from_attributes = True


class ManagedPostgresAdminRotateOut(BaseModel):
    ok: bool
    admin_user: str
    nodes_updated: list[int]
    nodes_skipped: list[int]


@router.get("/audit-logs", response_model=list[AuditLogOut])
def list_admin_audit_logs(
    db: Session = Depends(get_db),
    _: User = Depends(require_global("system.audit.read")),
    limit: int = Query(50, ge=1, le=200),
    action: str | None = Query(None, max_length=64),
    target_type: str | None = Query(None, max_length=64),
    target_id: str | None = Query(None, max_length=64),
) -> list[AuditLog]:
    """Listet privilegierte Operator-Aktionen. Unberechtigt: 403 (kein leeres OK)."""
    try:
        return audit_service.list_audit_logs(
            db,
            limit=limit,
            action=action,
            target_type=target_type,
            target_id=target_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/managed-postgres/rotate-admin",
    response_model=ManagedPostgresAdminRotateOut,
)
def rotate_managed_postgres_admin(
    db: Session = Depends(get_db),
    user: User = Depends(require_global("system.secrets.rotate")),
    _: None = Depends(verify_csrf),
) -> dict:
    """Rotiert das Cluster-Admin-Passwort (msm_admin) auf allen Nodes + Panel-Secret.

    Antwort enthaelt niemals das neue Passwort. Fehler: 400/503 mit Klartext.
    """
    try:
        result = postgres_service.rotate_cluster_admin_password(db)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PostgresServiceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception:
        raise HTTPException(
            status_code=500,
            detail="Admin-Passwort-Rotation unerwartet fehlgeschlagen.",
        ) from None

    audit_service.record_privileged_action(
        db,
        user_id=user.id,
        action="postgres.admin.rotate",
        target_type="managed_postgres",
        target_id=None,
        details={
            "nodes_updated": result.get("nodes_updated") or [],
            "nodes_skipped": result.get("nodes_skipped") or [],
            "admin_user": result.get("admin_user"),
        },
        commit=True,
    )
    return result
