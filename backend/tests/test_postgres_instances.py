"""Datenbankserver: eigene Instanz, Routing zum richtigen Ziel, Verbindungs-Hub."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from models import PostgresDatabase, PostgresGrant, PostgresInstance, PostgresUser, Server
from models.server_port import ServerPort
from schemas.server import ServerCreate
from services import postgres_instance_service, postgres_service
from services.auth_service import AuthService

ADMIN_PW = "instanz-" + "admin-" + "pw"
OWNER_PW = "owner-" + "passwort-" + "123"
APP_PW = "app-" + "passwort-" + "456"


@pytest.fixture
def db_server(db) -> Server:
    """Datenbankserver wie nach der Provisionierung (ohne Container)."""
    server = Server(
        name="Kunden-DB",
        game_type="postgres",
        install_dir="/tmp/msm-db-server",
        container_name="msm-srv-x",
        status="running",
        public_bind_ip="127.0.0.1",
    )
    db.add(server)
    db.flush()
    db.add(ServerPort(server_id=server.id, role="database", port=25432, protocol="tcp"))
    db.add(
        PostgresInstance(
            server_id=server.id,
            admin_password_encrypted=AuthService.encrypt_secret(
                ADMIN_PW, aad=postgres_service.instance_aad(server.id)
            ),
            allowed_cidrs="203.0.113.0/24",
            ssl_required=True,
        )
    )
    database = PostgresDatabase(
        server_id=server.id,
        name="app",
        owner_role="app_owner",
        owner_password_encrypted=AuthService.encrypt_secret(OWNER_PW, aad="msm:pg:db:owner"),
        is_power_user=True,
    )
    user = PostgresUser(
        server_id=server.id,
        username="app_owner_app",
        password_mask="****",
        password_encrypted=AuthService.encrypt_secret(
            APP_PW, aad=postgres_service.user_password_aad(server.id, "app_owner_app")
        ),
    )
    db.add_all([database, user])
    db.flush()
    db.add(PostgresGrant(server_id=server.id, database_id=database.id, user_id=user.id))
    db.commit()
    db.refresh(server)
    return server


# ── Schema ─────────────────────────────────────────────────────────────────


def test_datenbankserver_bekommt_immer_die_postgres_blueprint():
    req = ServerCreate(name="db", server_kind="database", game_type="dayz")
    assert req.game_type == "postgres"
    assert req.database is not None and req.database.database_name == "app"
    assert req.postgres_enabled is False


def test_anwendungsserver_darf_die_postgres_blueprint_nicht_nehmen():
    # Ohne Instanzzeile liefen seine Datenbankaufrufe in den geteilten Cluster.
    with pytest.raises(ValidationError):
        ServerCreate(name="x", game_type="postgres")


def test_anwendungsserver_braucht_ein_spiel():
    with pytest.raises(ValidationError):
        ServerCreate(name="x")


def test_cidrs_werden_geprueft_und_normalisiert():
    req = ServerCreate(
        name="db",
        server_kind="database",
        database={"allowed_cidrs": ["10.0.0.5", "10.0.0.5/32", "192.168.1.7/24"]},
    )
    assert req.database.allowed_cidrs == ["10.0.0.5/32", "192.168.1.0/24"]
    with pytest.raises(ValidationError):
        ServerCreate(name="db", server_kind="database", database={"allowed_cidrs": ["kein-netz"]})


def test_datenbanknamen_werden_als_identifier_geprueft():
    with pytest.raises(ValidationError):
        ServerCreate(name="db", server_kind="database", database={"database_name": "app; drop"})


# ── pg_hba ────────────────────────────────────────────────────────────────


def test_hba_laesst_von_aussen_nur_die_allowlist_und_nur_mit_ssl_herein(db_server):
    instance = db_server.postgres_instance
    text = postgres_instance_service.hba_config(instance)
    assert "hostssl all  all  203.0.113.0/24" in text
    assert "0.0.0.0/0" not in text
    assert "trust" not in text
    instance.ssl_required = False
    assert "host    all  all  203.0.113.0/24" in postgres_instance_service.hba_config(instance)


def test_hba_ohne_allowlist_hat_keinen_externen_eintrag(db_server):
    instance = db_server.postgres_instance
    instance.allowed_cidrs = ""
    lines = [line for line in postgres_instance_service.hba_config(instance).splitlines() if line.startswith("host")]
    assert all(("127.0.0.1" in line or "::1" in line or "172.16.0.0/12" in line) for line in lines)


# ── Routing ────────────────────────────────────────────────────────────────


def test_datenbankserver_geht_an_die_eigene_instanz(db, db_server):
    with patch.object(postgres_service, "_client_for_server_id", return_value=MagicMock()):
        verbindung = postgres_service._verbindung_fuer(db, db_server.id)
    assert verbindung.admin_password == ADMIN_PW
    assert verbindung.target == {"host": "127.0.0.1", "port": 25432, "container": f"msm-srv-{db_server.id}"}


def test_spielserver_bleibt_im_geteilten_cluster(db, test_server):
    with patch.object(postgres_service, "_client_for_server_id", return_value=MagicMock()), \
         patch.object(postgres_service, "_admin_password", return_value="cluster"):
        verbindung = postgres_service._verbindung_fuer(db, test_server.id)
    assert verbindung.target is None
    assert verbindung.mit_ziel({"a": 1}) == {"a": 1}


def test_owner_abfrage_traegt_das_ziel(db, db_server):
    client = MagicMock()
    client.postgres_query.return_value = []
    database = db_server.postgres_databases[0]
    with patch.object(postgres_service, "_client_for_server_id", return_value=client):
        postgres_service.list_tables(db, db_server.id, database.id)
    payload = client.postgres_query.call_args[0][0]
    assert payload["target"]["port"] == 25432
    assert payload["owner_password"] == OWNER_PW


def test_loeschen_eines_datenbankservers_raeumt_den_geteilten_cluster_nicht_auf(db, db_server):
    """Ein Drop ohne Ziel haette gleichnamige Datenbanken im geteilten Cluster
    getroffen; ueberhaupt ein Drop haette gestoppte Datenbankserver unloeschbar
    gemacht."""
    client = MagicMock()
    with patch.object(postgres_service, "_client_for_server_id", return_value=client):
        postgres_service.drop_server_resources(db, db_server.id)
    client.postgres_drop.assert_not_called()
    assert db.query(PostgresDatabase).filter_by(server_id=db_server.id).count() == 0


def test_rotieren_speichert_das_neue_passwort_verschluesselt(db, db_server):
    client = MagicMock()
    user = db_server.postgres_users[0]
    with patch.object(postgres_service, "_client_for_server_id", return_value=client):
        result = postgres_service.rotate_user_password(db, db_server.id, user.id)
    db.refresh(user)
    assert user.password_encrypted and result["password"] not in user.password_encrypted.split(":")[0]
    assert AuthService.decrypt_secret(
        user.password_encrypted, aad=postgres_service.user_password_aad(db_server.id, user.username)
    ) == result["password"]
    assert client.postgres_rotate_user.call_args[0][0]["target"]["container"] == f"msm-srv-{db_server.id}"


def test_backup_kontext_nimmt_die_instanz_mit(db, db_server):
    context = postgres_service.backup_context(db, db_server.id)
    assert context["admin_password"] == ADMIN_PW
    assert context["target"]["port"] == 25432


# ── Instanz anlegen ────────────────────────────────────────────────────────


def test_instanz_anlegen_legt_zeilen_und_dateien_an(db, owner_user):
    server = Server(name="neu", game_type="postgres", install_dir="/tmp/x", status="stopped",
                    public_bind_ip="127.0.0.1")
    db.add(server)
    db.commit()
    geschrieben: dict[str, str] = {}
    with patch.object(
        postgres_instance_service, "write_instance_file",
        side_effect=lambda _db, _s, path, content: geschrieben.__setitem__(path, content),
    ):
        postgres_instance_service.create_instance(
            db, server, database_name="shop", username="shop_owner", password="eigenes-passwort-1",
            allowed_cidrs=["198.51.100.4"], ssl_required=True,
        )
    db.commit()
    db.refresh(server)
    database = server.postgres_databases[0]
    assert database.name == "shop" and database.owner_role == "shop_owner"
    assert database.is_power_user is True
    assert postgres_service._owner_password(database) == "eigenes-passwort-1"
    assert server.postgres_users[0].username == "shop_owner_app"
    admin = postgres_service.instance_admin_password(server)
    assert geschrieben[postgres_instance_service.ADMIN_PASSWORD_FILE] == admin
    assert "198.51.100.4/32" in geschrieben[postgres_instance_service.HBA_FILE]
    assert geschrieben[postgres_instance_service.TLS_CERT_FILE].startswith("-----BEGIN CERTIFICATE-----")
    assert "PRIVATE KEY" in geschrieben[postgres_instance_service.TLS_KEY_FILE]


@pytest.mark.parametrize("name", ["postgres", "msm_admin", "pg_evil", "template1"])
def test_reservierte_namen_werden_abgelehnt(db, name):
    server = Server(name="neu", game_type="postgres", install_dir="/tmp/x", status="stopped")
    db.add(server)
    db.commit()
    with patch.object(postgres_instance_service, "write_instance_file"):
        with pytest.raises(ValueError):
            postgres_instance_service.create_instance(
                db, server, database_name=name, username="ok_owner", password=None,
                allowed_cidrs=[], ssl_required=True,
            )


def test_bootstrap_legt_nur_fehlende_datenbanken_an_und_leert_das_initpasswort(db, db_server):
    client = MagicMock()
    client.postgres_run.return_value = {"results": [{"rows": []}]}
    geschrieben: dict[str, str] = {}
    with patch.object(postgres_service, "_client_for_server_id", return_value=client), \
         patch.object(postgres_service, "_client_for_server", return_value=client), \
         patch.object(
             postgres_instance_service, "write_instance_file",
             side_effect=lambda _db, _s, path, content: geschrieben.__setitem__(path, content),
         ):
        created = postgres_instance_service.bootstrap(db, db_server)
    assert created == ["app"]
    assert geschrieben[postgres_instance_service.ADMIN_PASSWORD_FILE] == ""
    payload = client.postgres_provision.call_args[0][0]
    assert payload["owner_password"] == OWNER_PW and payload["user_password"] == APP_PW
    assert payload["target"]["port"] == 25432

    client.postgres_run.return_value = {"results": [{"rows": [[1]]}]}
    client.postgres_provision.reset_mock()
    with patch.object(postgres_service, "_client_for_server_id", return_value=client), \
         patch.object(postgres_instance_service, "write_instance_file"):
        assert postgres_instance_service.bootstrap(db, db_server) == []
    client.postgres_provision.assert_not_called()


# ── Verbindungs-Hub (HTTP) ─────────────────────────────────────────────────


def test_hub_zeigt_verbindungen_ohne_passwoerter(client, owner_cookies, db_server):
    with patch.object(postgres_instance_service, "read_certificate", return_value="CERT"), \
         patch.object(postgres_instance_service, "needs_bootstrap", return_value=False):
        response = client.get(f"/api/servers/{db_server.id}/databases/connection", cookies=owner_cookies)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["kind"] == "dedicated"
    assert data["internal"] == {"host": f"msm-srv-{db_server.id}", "port": 25432}
    assert data["external"]["reachable"] is False  # an 127.0.0.1 gebunden
    assert data["external"]["allowed_cidrs"] == ["203.0.113.0/24"]
    assert data["databases"][0]["owner_revealable"] is True
    assert OWNER_PW not in response.text and APP_PW not in response.text


def test_passwort_abrufen_braucht_admin_und_csrf_und_wird_auditiert(
    client, owner_cookies, csrf_token, db, db_server
):
    from models import AuditLog

    database = db_server.postgres_databases[0]
    url = f"/api/servers/{db_server.id}/databases/credentials/reveal"
    ohne_csrf = client.post(url, json={"database_id": database.id}, cookies=owner_cookies)
    assert ohne_csrf.status_code == 403

    response = client.post(
        url, json={"database_id": database.id}, cookies=owner_cookies,
        headers={"X-CSRF-Token": csrf_token or ""},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"username": "app_owner", "password": OWNER_PW}
    assert response.headers.get("cache-control") == "no-store"
    eintrag = db.query(AuditLog).filter(AuditLog.action == "postgres.credential.reveal").one()
    assert OWNER_PW not in (eintrag.details or "")

    user = db_server.postgres_users[0]
    response = client.post(
        url, json={"database_id": database.id, "user_id": user.id}, cookies=owner_cookies,
        headers={"X-CSRF-Token": csrf_token or ""},
    )
    assert response.json()["password"] == APP_PW


def test_passwort_abrufen_ohne_recht_scheitert(client, user_cookies, user_csrf_token, db_server):
    database = db_server.postgres_databases[0]
    response = client.post(
        f"/api/servers/{db_server.id}/databases/credentials/reveal",
        json={"database_id": database.id}, cookies=user_cookies,
        headers={"X-CSRF-Token": user_csrf_token or ""},
    )
    assert response.status_code in {403, 404}


def test_owner_im_geteilten_cluster_nur_nach_power_user(db, test_server):
    database = PostgresDatabase(
        server_id=test_server.id, name="msm_s1_db1", owner_role="msm_s1_o1",
        owner_password_encrypted=AuthService.encrypt_secret("x" * 20, aad="msm:pg:db:owner"),
    )
    db.add(database)
    db.commit()
    with pytest.raises(ValueError, match="Power-User"):
        postgres_service.reveal_credential(db, test_server, database.id, None)


def test_netzwerk_aendern_schreibt_hba_und_laedt_neu(client, owner_cookies, csrf_token, db_server):
    geschrieben: dict[str, str] = {}
    run = MagicMock(return_value={"results": [{"rows": [[True]]}]})
    with patch.object(
        postgres_instance_service, "write_instance_file",
        side_effect=lambda _db, _s, path, content: geschrieben.__setitem__(path, content),
    ), patch.object(postgres_service, "run", run), \
         patch.object(postgres_instance_service, "read_certificate", return_value=None):
        response = client.put(
            f"/api/servers/{db_server.id}/databases/instance/network",
            json={"allowed_cidrs": ["198.51.100.0/24"], "ssl_required": False},
            cookies=owner_cookies, headers={"X-CSRF-Token": csrf_token or ""},
        )
    assert response.status_code == 200, response.text
    assert "host    all  all  198.51.100.0/24" in geschrieben[postgres_instance_service.HBA_FILE]
    assert any("pg_reload_conf" in call.kwargs["statements"][0][0] for call in run.call_args_list)
