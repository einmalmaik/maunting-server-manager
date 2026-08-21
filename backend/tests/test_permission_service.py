"""Tests fuer services.permission_service.

Prueft die zentrale Permission-Logik:
- Owner-Bypass (is_owner=True)
- Globale Permissions via Rolle
- Server-Permissions via Rolle (pauschal) oder Delegation
- list_visible_servers / set_user_server_permissions
- Hoster-Kundenserver: pauschale Rollen brauchen `servers.hoster_customers.view`
"""
from sqlalchemy.orm import Session

from models import (
    HosterIdentity,
    HosterIntegration,
    HosterService,
    Role,
    RolePermission,
    Server,
    ServerPermission,
    Team,
    TeamMember,
    TeamServerGrant,
    User,
)
from services import permission_service
from services.role_service import ensure_system_roles, get_role_by_name


def _make_server(db: Session, name: str = "S") -> Server:
    server = Server(name=name, game_type="dayz", install_dir=f"/tmp/{name}", status="stopped")
    db.add(server)
    db.commit()
    db.refresh(server)
    return server


class TestHasGlobalPermission:
    def test_owner_bypass(self, db: Session, owner_user: User):
        assert permission_service.has_global_permission(db, owner_user, "servers.delete") is True

    def test_user_without_role_denied(self, db: Session, regular_user: User):
        regular_user.role_id = None
        db.commit()
        assert permission_service.has_global_permission(db, regular_user, "servers.create") is False

    def test_admin_role_grants_all_global(self, db: Session, regular_user: User):
        admin = get_role_by_name(db, "admin")
        assert admin is not None
        regular_user.role_id = admin.id
        db.commit()
        assert permission_service.has_global_permission(db, regular_user, "servers.create") is True
        assert permission_service.has_global_permission(db, regular_user, "servers.delete") is True

    def test_user_role_has_no_global(self, db: Session, regular_user: User):
        user_role = get_role_by_name(db, "user")
        assert user_role is not None
        regular_user.role_id = user_role.id
        db.commit()
        assert permission_service.has_global_permission(db, regular_user, "servers.create") is False


class TestHasServerPermission:
    def test_owner_bypass(self, db: Session, owner_user: User, test_server: Server):
        assert permission_service.has_server_permission(db, owner_user, test_server.id, "server.start") is True

    def test_user_with_delegation(self, db: Session, regular_user: User, test_server: Server):
        db.add(ServerPermission(user_id=regular_user.id, server_id=test_server.id, permission_key="server.start"))
        db.commit()
        assert permission_service.has_server_permission(db, regular_user, test_server.id, "server.start") is True
        # Andere Permission: nein
        assert permission_service.has_server_permission(db, regular_user, test_server.id, "server.stop") is False

    def test_role_grants_blanket(self, db: Session, regular_user: User, test_server: Server):
        admin = get_role_by_name(db, "admin")
        regular_user.role_id = admin.id
        db.commit()
        # Admin hat alle Server-Keys pauschal
        assert permission_service.has_server_permission(db, regular_user, test_server.id, "server.start") is True
        assert permission_service.has_server_permission(db, regular_user, test_server.id, "server.files.delete") is True

    def test_no_role_no_delegation_denied(self, db: Session, regular_user: User, test_server: Server):
        regular_user.role_id = None
        db.commit()
        assert permission_service.has_server_permission(db, regular_user, test_server.id, "server.view") is False


class TestListVisibleServers:
    def test_owner_sees_all(self, db: Session, owner_user: User, test_server: Server):
        another = _make_server(db, "another")
        result = permission_service.list_visible_servers(db, owner_user)
        ids = {s.id for s in result}
        assert test_server.id in ids and another.id in ids

    def test_role_with_server_view_sees_all(self, db: Session, regular_user: User, test_server: Server):
        # Gib der user-Rolle pauschal `server.view`
        user_role = get_role_by_name(db, "user")
        db.add(RolePermission(role_id=user_role.id, permission_key="server.view"))
        db.commit()
        regular_user.role_id = user_role.id
        db.commit()
        result = permission_service.list_visible_servers(db, regular_user)
        assert any(s.id == test_server.id for s in result)

    def test_only_servers_with_delegation_visible(self, db: Session, regular_user: User, test_server: Server):
        other = _make_server(db, "other")
        db.add(ServerPermission(user_id=regular_user.id, server_id=test_server.id, permission_key="server.view"))
        db.commit()
        result = permission_service.list_visible_servers(db, regular_user)
        ids = {s.id for s in result}
        assert test_server.id in ids
        assert other.id not in ids

    def test_no_permissions_no_servers(self, db: Session, regular_user: User, test_server: Server):
        regular_user.role_id = None
        db.commit()
        result = permission_service.list_visible_servers(db, regular_user)
        assert result == []

    def test_non_view_delegation_does_not_grant_visibility(
        self, db: Session, regular_user: User, test_server: Server
    ):
        """Eine Delegation ohne `server.view` darf den Server NICHT in der Liste zeigen
        (Konsistenz mit dem Detail-Endpoint, der ebenfalls `server.view` prueft)."""
        regular_user.role_id = None
        db.add(
            ServerPermission(
                user_id=regular_user.id,
                server_id=test_server.id,
                permission_key="server.start",
            )
        )
        db.commit()
        result = permission_service.list_visible_servers(db, regular_user)
        assert result == []


class TestListVisibleServersLaedtDiePorts:
    """Die Serverübersicht ist die Startseite und wird alle fünf Sekunden geholt.

    `ServerResponse` liest die Ports jedes Servers. Ohne Ladehinweis kostete das
    eine Abfrage **je Server** — gemessen 34 statt 2 bei dreißig Servern. Der
    Zähler hier schreibt keine Zahl fest; er fängt den Rückfall ab, dass die
    Zahl wieder mit der Serverzahl wächst.
    """

    def _mit_ports(self, db: Session, name: str) -> Server:
        server = _make_server(db, name)
        server.set_port("game", 27015 + server.id, "udp")
        server.set_port("query", 28015 + server.id, "udp")
        db.commit()
        return server

    def _zaehle(self, db: Session, user: User) -> tuple[int, list[list[int]]]:
        from sqlalchemy import event

        import database as db_module

        gesehen: list[str] = []

        def _hook(conn, cursor, statement, parameters, context, executemany) -> None:
            gesehen.append(statement)

        db.expire_all()
        event.listen(db_module.engine, "before_cursor_execute", _hook)
        try:
            server = permission_service.list_visible_servers(db, user)
            # Die Ports wirklich anfassen — nur so fällt lazy loading auf.
            ports = [sorted(p.port for p in s.ports) for s in server]
        finally:
            event.remove(db_module.engine, "before_cursor_execute", _hook)
        return len(gesehen), ports

    def test_owner_path_does_not_query_once_per_server(
        self, db: Session, owner_user: User
    ):
        erwartet = {}
        for i in range(8):
            server = self._mit_ports(db, f"s{i}")
            erwartet[server.id] = sorted(p.port for p in server.ports)

        abfragen, ports = self._zaehle(db, owner_user)

        assert sorted(ports) == sorted(erwartet.values())
        assert abfragen <= 3, f"{abfragen} Abfragen für acht Server"

    def test_delegated_path_does_not_query_once_per_server(
        self, db: Session, regular_user: User
    ):
        regular_user.role_id = None
        erwartet = []
        for i in range(8):
            server = self._mit_ports(db, f"s{i}")
            db.add(ServerPermission(
                user_id=regular_user.id, server_id=server.id,
                permission_key="server.view",
            ))
            erwartet.append(sorted(p.port for p in server.ports))
        db.commit()

        abfragen, ports = self._zaehle(db, regular_user)

        assert sorted(ports) == sorted(erwartet)
        assert abfragen <= 6, f"{abfragen} Abfragen für acht delegierte Server"


def _mark_as_hoster_server(
    db: Session, server: Server, customer: User, *, status: str = "ready"
) -> HosterService:
    """Haengt einen Server an einen Shop-Vertrag — die Markierung, auf die der
    Sichtbarkeitsfilter schaut. Der Vertragsstatus ist absichtlich Parameter:
    auch suspendierte Vertraege sind Kundendaten."""
    integration = HosterIntegration(
        name=f"Shop {server.name}",
        slug=f"shop-{server.id}",
        service_user_id=customer.id,
        api_key_hash=f"hash-{server.id}",
    )
    db.add(integration)
    db.flush()
    identity = HosterIdentity(
        integration_id=integration.id,
        external_subject_hash=f"subject-{server.id}",
        user_id=customer.id,
    )
    db.add(identity)
    db.flush()
    service = HosterService(
        integration_id=integration.id,
        external_service_id=f"svc-{server.id}",
        identity_id=identity.id,
        server_id=server.id,
        status=status,
        correlation_id=f"corr-{server.id}",
    )
    db.add(service)
    db.commit()
    return service


class TestHosterCustomerServerVisibility:
    """Server aus Shop-Vertraegen sind fuer pauschale Rollenrechte unsichtbar,
    solange die Rolle nicht `servers.hoster_customers.view` haelt.

    Der Filter sitzt in `direct_server_permission` und gilt damit fuer jeden
    server-scoped Key — nicht nur fuers Listing. Sonst waere die Liste sauber,
    aber Konsole und Dateien blieben ueber eine erratene Server-ID offen.
    """

    def _rolle_mit(self, db: Session, user: User, *keys: str) -> Role:
        role = Role(name=f"rolle-{user.id}-{len(keys)}", is_system=False)
        db.add(role)
        db.flush()
        for key in keys:
            db.add(RolePermission(role_id=role.id, permission_key=key))
        user.role_id = role.id
        db.commit()
        return role

    def test_blanket_role_does_not_see_customer_server(
        self, db: Session, regular_user: User, owner_user: User, test_server: Server
    ):
        kunde = _make_server(db, "kunde")
        _mark_as_hoster_server(db, kunde, owner_user)
        self._rolle_mit(db, regular_user, "server.view", "server.console.read")

        ids = permission_service.list_visible_server_ids(db, regular_user)
        assert ids is not None and test_server.id in ids and kunde.id not in ids
        # Nicht nur unsichtbar, sondern unbedienbar — fuer jeden Key.
        assert permission_service.has_server_permission(db, regular_user, kunde.id, "server.view") is False
        assert permission_service.has_server_permission(db, regular_user, kunde.id, "server.console.read") is False
        # Der eigene Bestand bleibt voll bedienbar.
        assert permission_service.has_server_permission(db, regular_user, test_server.id, "server.console.read") is True

    def test_role_with_hoster_key_sees_everything(
        self, db: Session, regular_user: User, owner_user: User, test_server: Server
    ):
        kunde = _make_server(db, "kunde")
        _mark_as_hoster_server(db, kunde, owner_user)
        self._rolle_mit(
            db, regular_user,
            "server.view", "server.console.read", "servers.hoster_customers.view",
        )

        assert permission_service.list_visible_server_ids(db, regular_user) is None
        assert permission_service.has_server_permission(db, regular_user, kunde.id, "server.console.read") is True

    def test_owner_is_never_filtered(self, db: Session, owner_user: User):
        kunde = _make_server(db, "kunde")
        _mark_as_hoster_server(db, kunde, owner_user)
        assert permission_service.list_visible_server_ids(db, owner_user) is None
        assert permission_service.has_server_permission(db, owner_user, kunde.id, "server.console.exec") is True

    def test_customer_delegation_is_untouched(
        self, db: Session, regular_user: User, test_server: Server
    ):
        """Der Kunde selbst haelt seine Rechte als Delegation — der Filter
        betrifft nur den pauschalen Rollen-Zweig."""
        regular_user.role_id = None
        _mark_as_hoster_server(db, test_server, regular_user)
        db.add(ServerPermission(user_id=regular_user.id, server_id=test_server.id, permission_key="server.view"))
        db.add(ServerPermission(user_id=regular_user.id, server_id=test_server.id, permission_key="server.console.read"))
        db.commit()

        ids = permission_service.list_visible_server_ids(db, regular_user)
        assert ids == [test_server.id]
        assert permission_service.has_server_permission(db, regular_user, test_server.id, "server.console.read") is True

    def test_support_who_is_also_customer_keeps_own_server(
        self, db: Session, regular_user: User, test_server: Server
    ):
        """Pauschale Rolle ohne Hoster-Key plus eigener Vertrag: der eigene
        Kundenserver bleibt ueber die Delegation sichtbar, fremde nicht."""
        fremd = _make_server(db, "fremd")
        _mark_as_hoster_server(db, fremd, regular_user)
        _mark_as_hoster_server(db, test_server, regular_user)
        db.add(ServerPermission(user_id=regular_user.id, server_id=test_server.id, permission_key="server.view"))
        db.commit()
        self._rolle_mit(db, regular_user, "server.view")

        ids = permission_service.list_visible_server_ids(db, regular_user)
        assert ids is not None and test_server.id in ids and fremd.id not in ids

    def test_team_grant_cannot_launder_customer_server(
        self, db: Session, regular_user: User, owner_user: User, test_server: Server
    ):
        """Ein Gruender mit pauschaler Rolle ohne Hoster-Key kann Kundenserver
        nicht ueber ein Team weiterreichen — der Deckel ist sein eigenes,
        gefiltertes Direktrecht."""
        kunde = _make_server(db, "kunde")
        _mark_as_hoster_server(db, kunde, owner_user)

        gruender = User(username="gruender", email="gruender@test.de", password_hash="x")
        db.add(gruender)
        db.flush()
        self._rolle_mit(db, gruender, "server.view")

        team = Team(name="support-team", owner_user_id=gruender.id)
        db.add(team)
        db.flush()
        db.add(TeamMember(team_id=team.id, user_id=regular_user.id))
        db.add(TeamServerGrant(team_id=team.id, server_id=kunde.id, permission_key="server.view"))
        db.commit()

        regular_user.role_id = None
        db.commit()
        assert permission_service.has_server_permission(db, regular_user, kunde.id, "server.view") is False
        ids = permission_service.list_visible_server_ids(db, regular_user)
        assert ids is not None and kunde.id not in ids

    def test_suspended_contract_still_counts(
        self, db: Session, regular_user: User, owner_user: User
    ):
        kunde = _make_server(db, "kunde")
        _mark_as_hoster_server(db, kunde, owner_user, status="suspended")
        self._rolle_mit(db, regular_user, "server.view")

        ids = permission_service.list_visible_server_ids(db, regular_user)
        assert ids is not None and kunde.id not in ids


class TestSetUserServerPermissions:
    def test_creates_delegations(self, db: Session, regular_user: User, test_server: Server, owner_user: User):
        keys = permission_service.set_user_server_permissions(
            db, regular_user.id, test_server.id,
            ["server.view", "server.start", "server.stop"],
            granted_by=owner_user.id,
        )
        assert set(keys) == {"server.view", "server.start", "server.stop"}
        rows = db.query(ServerPermission).filter(
            ServerPermission.user_id == regular_user.id,
            ServerPermission.server_id == test_server.id,
        ).all()
        assert len(rows) == 3

    def test_overwrites_existing(self, db: Session, regular_user: User, test_server: Server, owner_user: User):
        permission_service.set_user_server_permissions(
            db, regular_user.id, test_server.id, ["server.view", "server.start"], granted_by=owner_user.id
        )
        keys = permission_service.set_user_server_permissions(
            db, regular_user.id, test_server.id, ["server.stop"], granted_by=owner_user.id
        )
        assert keys == ["server.stop"]
        rows = db.query(ServerPermission).filter(
            ServerPermission.user_id == regular_user.id,
            ServerPermission.server_id == test_server.id,
        ).all()
        assert len(rows) == 1
        assert rows[0].permission_key == "server.stop"

    def test_unknown_keys_ignored(self, db: Session, regular_user: User, test_server: Server, owner_user: User):
        keys = permission_service.set_user_server_permissions(
            db, regular_user.id, test_server.id,
            ["server.view", "bogus.key", "servers.create"],  # servers.create ist global, kein server-key
            granted_by=owner_user.id,
        )
        assert keys == ["server.view"]
