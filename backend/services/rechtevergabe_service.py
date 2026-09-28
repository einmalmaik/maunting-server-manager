"""Rechte vergeben: die eine Stelle fuer Panel und KI.

Die Schranken standen bis 09/2026 in den Routern (`routers/admin.py`,
`routers/roles.py`) und warfen `HTTPException`. Seit die KI Rollen und
Serverrechte verwalten kann, braucht sie dieselben Schranken — eine zweite
Abschrift waere genau die Handlung mit zwei Regeln, gegen die die
Werkzeugtabelle sonst ueberall gebaut ist. Deshalb stehen sie hier, und beide
Wege rufen sie auf. Der Router uebersetzt `RechteFehler` in eine HTTP-Antwort,
der Vorschlagspfad in eine Werkzeugantwort.

Die Meldungen sind woertlich die der Router; die Routertests pruefen sie.
"""
from __future__ import annotations

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from models import Role, Server, User
from services import audit_service, role_service
from services.permission_catalog import SYSTEM_ROLE_ADMIN
from services.permission_service import (
    direct_server_permission,
    has_global_permission,
    list_user_server_permission_keys,
    set_user_server_permissions,
)


class RechteFehler(Exception):
    """Eine abgewiesene Rechteaenderung — mit dem Status, den das Panel meldet."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def ensure_no_global_escalation(
    db: Session, actor: User, required_keys: list[str]
) -> None:
    """Non-Owner darf nur Aktionen ausloesen, die Permissions verlangen,
    die er selbst global besitzt — sonst Eskalation."""
    if actor.is_owner:
        return
    missing = sorted(
        {k for k in required_keys if not has_global_permission(db, actor, k)}
    )
    if missing:
        raise RechteFehler(
            403,
            "Du kannst nur Permissions vergeben/zuweisen, die du selbst "
            f"besitzt. Fehlend: {missing}",
        )


def ensure_no_server_escalation(
    db: Session,
    actor: User,
    server_id: int,
    required_keys: list[str],
) -> None:
    """Non-Owner darf einem Sub-User auf einem Server nur die Server-Keys
    delegieren, die er auf diesem Server **selbst und dauerhaft** hat.

    Geprueft wird mit ``direct_server_permission`` und nicht mit
    ``has_server_permission``. Der Unterschied sind die geliehenen Teamrechte:
    ``has_server_permission`` zaehlt seit der Team-Erweiterung auch das mit, was
    jemandem nur ueber eine Mitgliedschaft zusteht.

    Geliehenes weiterzugeben heisst hier, es dauerhaft zu machen. Eine
    ``ServerPermission``-Zeile ueberlebt den Austritt aus dem Team, das
    Aufloesen des Teams und den Rechteverlust dessen, der das Recht ins Team
    gebracht hat. Genau das soll die Leihe nicht koennen — ``permission_service``
    begruendet im Modul-Docstring, dass ein entzogenes Teamrecht sich „selbst
    heilt“, und ``team_service.set_server_grants`` prueft aus demselben Grund
    bereits gegen die direkte Berechtigung.

    Der Owner-Umweg bleibt erhalten: ``direct_server_permission`` beruecksichtigt
    den Serverbesitzer selbst.
    """
    if actor.is_owner:
        return
    missing = sorted(
        {k for k in required_keys if not direct_server_permission(db, actor, server_id, k)}
    )
    if missing:
        raise RechteFehler(
            403,
            "Du kannst auf diesem Server nur Permissions delegieren, die "
            f"du selbst besitzt. Fehlend: {missing}",
        )


def ensure_no_role_escalation(
    db: Session, actor: User, requested_keys: list[str] | None
) -> None:
    """Verhindert Privilege-Escalation via Rollen-Mutation.

    Ein Non-Owner darf nur Permission-Keys vergeben, die er selbst bereits
    besitzt. Sonst koennte z. B. ein User mit nur `roles.manage` seiner
    eigenen Rolle `users.manage` oder `servers.delete` hinzufuegen.
    """
    if requested_keys is None or actor.is_owner:
        return
    missing = sorted(
        {k for k in requested_keys if not has_global_permission(db, actor, k)}
    )
    if missing:
        raise RechteFehler(
            403,
            "Du kannst nur Permissions vergeben, die du selbst besitzt. "
            f"Fehlend: {missing}",
        )


# ── Rollen eines Benutzers ────────────────────────────────────────────


def check_role_assignment(
    db: Session, actor: User, user_id: int, requested_role_ids: list[int]
) -> tuple[User, list[int]]:
    """Alle Grenzen einer Rollenzuweisung, ohne etwas zu schreiben.

    Getrennt von `assign_roles`, weil die KI dieselbe Frage schon beim
    Vorschlagen stellt: eine Karte, die beim Klick ohnehin abprallt, soll gar
    nicht erst entstehen.
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise RechteFehler(404, "User nicht gefunden")
    if user.is_owner:
        raise RechteFehler(400, "Owner-Account hat keine zuweisbare Rolle")
    # Self-Lockout-Schutz: ein User darf seine eigene Rolle nicht aendern.
    # Ohne diesen Guard koennte ein Admin sich versehentlich oder durch
    # Drittparteien (CSRF, kompromittierte Session) zum User downgraden und
    # sich damit selbst aussperren. Rollenwechsel passiert immer durch einen
    # anderen Account mit `users.permissions.manage`.
    if user.id == actor.id:
        raise RechteFehler(400, "Du kannst deine eigene Rolle nicht ändern")
    # Auch das Entfernen der aktuellen Rolle ist eine Eskalations-Aktion: ein
    # Non-Owner darf einem User keine Rolle wegnehmen, deren Keys er selbst
    # nicht besitzt (sonst koennte er einen Admin-Account "entwaffnen").
    current_keys = role_service.effective_user_role_permission_keys(db, user)
    ensure_no_global_escalation(db, actor, current_keys)

    desired_role_ids = sorted(set(requested_role_ids))
    desired_keys: set[str] = set()
    for role_id in desired_role_ids:
        role = role_service.get_role(db, role_id)
        if not role:
            raise RechteFehler(404, "Rolle nicht gefunden")
        # Zuweisung der `admin`-System-Rolle ist nur dem Owner erlaubt
        # (verhindert Privilege-Escalation ueber `users.permissions.manage`).
        if role.is_system and role.name == SYSTEM_ROLE_ADMIN and not actor.is_owner:
            raise RechteFehler(403, "Nur Owner kann die admin-Rolle zuweisen")
        # Generalisiertes Eskalationsverbot: Actor muss alle Keys der
        # Ziel-Rolle selbst global besitzen — sonst koennte er sich (oder
        # andere) ueber eine Custom-Rolle hochziehen.
        desired_keys.update(role_service.role_permission_keys(db, role.id))
    ensure_no_global_escalation(db, actor, sorted(desired_keys))
    return user, desired_role_ids


def assign_roles(
    db: Session, actor: User, user_id: int, requested_role_ids: list[int]
) -> User:
    """Prüft Eskalationsgrenzen und speichert eine Multi-Role-Zuweisung."""
    user, desired_role_ids = check_role_assignment(db, actor, user_id, requested_role_ids)
    try:
        role_service.set_user_roles(db, user, desired_role_ids, commit=False)
        audit_service.record_privileged_action(
            db,
            user_id=actor.id,
            action="user.roles.updated",
            target_type="user",
            target_id=user.id,
            details={"role_ids": desired_role_ids},
        )
        db.commit()
        db.refresh(user)
    except ValueError as exc:
        db.rollback()
        raise RechteFehler(400, str(exc)) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        raise RechteFehler(
            409,
            "Rollenzuweisung konnte wegen einer gleichzeitigen Änderung nicht gespeichert werden",
        ) from exc
    return user


# ── Serverrechte eines Benutzers ──────────────────────────────────────


def check_server_permissions(
    db: Session, actor: User, user_id: int, server_id: int, keys: list[str]
) -> tuple[User, Server, list[str]]:
    """Alle Grenzen einer Server-Delegation, ohne etwas zu schreiben.

    Zurueck kommen Ziel, Server und die bisherigen Keys.
    """
    target_user = db.query(User).filter(User.id == user_id).first()
    if not target_user:
        raise RechteFehler(404, "User nicht gefunden")
    if target_user.is_owner:
        raise RechteFehler(400, "Owner braucht keine Permissions")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise RechteFehler(404, "Server nicht gefunden")
    # Non-Owner darf auf einem Server nur Keys delegieren, die er selbst auf
    # diesem Server besitzt — sonst kann ein User mit nur
    # `users.permissions.manage` (ohne eigene Server-Rechte) beliebige
    # Server-Aktionen an andere weiterreichen.
    ensure_no_server_escalation(db, actor, server_id, keys)
    # De-Eskalations-Schutz: Keys, die durch das Set entfernt werden,
    # zaehlen ebenfalls als Mutation. Sonst koennte ein User ohne eigene
    # Server-Rechte einem anderen User per leerem Set die Rechte entziehen.
    existing_keys = list_user_server_permission_keys(db, user_id, server_id)
    removed = [k for k in existing_keys if k not in set(keys)]
    if removed:
        ensure_no_server_escalation(db, actor, server_id, removed)
    return target_user, server, existing_keys


def set_server_permissions(
    db: Session, actor: User, user_id: int, server_id: int, keys: list[str]
) -> tuple[list[str], bool, User, Server]:
    """Setzt die Delegation — (Keys, erstmals Zugang, Ziel, Server).

    ``erstmals`` heisst: vorher keine Zeile, jetzt mindestens eine. Daran
    haengt die Benachrichtigung des Ziels; verschickt wird sie vom Aufrufer,
    weil der Versand asynchron ist und der KI-Pfad synchron laeuft.
    """
    target_user, server, existing_keys = check_server_permissions(
        db, actor, user_id, server_id, keys
    )
    gesetzt = set_user_server_permissions(
        db, user_id, server_id, keys, granted_by=actor.id
    )
    return gesetzt, not existing_keys and bool(gesetzt), target_user, server


# ── Rollen ────────────────────────────────────────────────────────────


def create_role(
    db: Session, actor: User, name: str, description: str | None, keys: list[str]
) -> Role:
    if role_service.get_role_by_name(db, name):
        raise RechteFehler(400, "Name bereits vergeben")
    ensure_no_role_escalation(db, actor, keys)
    try:
        role = role_service.create_role(db, name, description, keys)
        audit_service.record_privileged_action(
            db,
            user_id=actor.id,
            action="roles.create",
            target_type="role",
            target_id=role.id,
            details={"name": role.name, "permissions": keys or []},
            commit=True,
        )
    except ValueError as e:
        raise RechteFehler(400, str(e)) from e
    return role


def update_role(
    db: Session,
    actor: User,
    role_id: int,
    name: str | None,
    description: str | None,
    keys: list[str] | None,
) -> Role:
    role = role_service.get_role(db, role_id)
    if not role:
        raise RechteFehler(404, "Rolle nicht gefunden")
    if name is not None and name != role.name:
        existing = role_service.get_role_by_name(db, name)
        if existing and existing.id != role.id:
            raise RechteFehler(400, "Name bereits vergeben")
    # De-Eskalations-Schutz: wer Permissions einer Rolle mutiert, muss alle
    # AKTUELLEN Keys der Rolle selbst besitzen — sonst koennte ein
    # `roles.manage`-User durch das Stripping einer maechtigen Custom-Rolle
    # andere User effektiv entwaffnen. Name/Description-only-Updates
    # (permissions=None) sind davon nicht betroffen.
    if keys is not None:
        ensure_no_role_escalation(
            db, actor, role_service.role_permission_keys(db, role.id)
        )
    ensure_no_role_escalation(db, actor, keys)
    try:
        role = role_service.update_role(db, role, name, description, keys)
        audit_service.record_privileged_action(
            db,
            user_id=actor.id,
            action="roles.update",
            target_type="role",
            target_id=role.id,
            details={
                "name": role.name,
                "permissions": keys if keys is not None else "[unchanged]",
            },
            commit=True,
        )
    except ValueError as e:
        raise RechteFehler(400, str(e)) from e
    return role


def delete_role(db: Session, actor: User, role_id: int) -> None:
    role = role_service.get_role(db, role_id)
    if not role:
        raise RechteFehler(404, "Rolle nicht gefunden")
    role_name = role.name
    try:
        role_service.delete_role(db, role)
        audit_service.record_privileged_action(
            db,
            user_id=actor.id,
            action="roles.delete",
            target_type="role",
            target_id=role_id,
            details={"name": role_name},
            commit=True,
        )
    except ValueError as e:
        raise RechteFehler(400, str(e)) from e
