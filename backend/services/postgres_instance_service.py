"""Eigene PostgreSQL-Instanz eines Datenbankservers.

Die Instanz ist ein Servercontainer mit der Blueprint ``postgres``. Dieses
Modul erledigt nur, was darueber hinausgeht:

- Dateien, die das Image beim Start liest: Admin-Passwort fuer das erste
  ``initdb`` (``.msm/admin_password``, danach geleert), ``conf/pg_hba.conf``
  und ein selbstsigniertes Zertifikat unter ``tls/``.
- Einrichten der Datenbanken, deren Zeilen das Panel schon kennt
  (``bootstrap``). Das ist wiederholbar: vorhandene Datenbanken bleiben stehen.

Geschrieben wird ohne Dateiversionen: der Versionsspeicher wuerde sonst das
Admin-Passwort und den TLS-Schluessel aufheben.
"""

from __future__ import annotations

import ipaddress
import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from models import PostgresDatabase, PostgresGrant, PostgresInstance, Server
from services import postgres_service
from services.auth_service import AuthService
from services.node_client import NodeClientError
from services.postgres_service import PostgresServiceError

logger = logging.getLogger(__name__)

POSTGRES_BLUEPRINT_ID = "postgres"
ADMIN_PASSWORD_FILE = ".msm/admin_password"
HBA_FILE = "conf/pg_hba.conf"
TLS_CERT_FILE = "tls/server.crt"
TLS_KEY_FILE = "tls/server.key"
CONTROL_DB = "msm_control"
RESERVED_NAMES = {
    "postgres", "msm_admin", "msm_control", "template0", "template1", "public",
}
MAX_CIDRS = 50
# Docker legt seine Bridge-Netze standardmaessig in 172.16.0.0/12. Von dort
# kommen der Agent (ueber den docker-proxy des veroeffentlichten Ports) und die
# Container im internen Netz.
DOCKER_NETZE = "172.16.0.0/12"


def normalize_cidrs(values: list[str]) -> list[str]:
    """Prueft und normalisiert CIDR-Eintraege (``10.0.0.5`` wird ``10.0.0.5/32``)."""
    result: list[str] = []
    for raw in values:
        text = (raw or "").strip()
        if not text:
            continue
        try:
            net = ipaddress.ip_network(text, strict=False)
        except ValueError as exc:
            raise ValueError(f"'{text}' ist kein gueltiges Netz (CIDR).") from exc
        if str(net) not in result:
            result.append(str(net))
    if len(result) > MAX_CIDRS:
        raise ValueError(f"Hoechstens {MAX_CIDRS} Netze sind erlaubt.")
    return result


def cidrs_of(instance: PostgresInstance) -> list[str]:
    return [line for line in (instance.allowed_cidrs or "").splitlines() if line.strip()]


def hba_config(instance: PostgresInstance) -> str:
    extern = "hostssl" if instance.ssl_required else "host"
    lines = [
        "# Verwaltet vom MSM-Panel (Datenbankserver -> Verbindung).",
        "# Aenderungen hier ueberschreibt das naechste Speichern im Panel.",
        "local   all  all                  scram-sha-256",
        "host    all  all  127.0.0.1/32    scram-sha-256",
        "host    all  all  ::1/128         scram-sha-256",
        f"host    all  all  {DOCKER_NETZE}   scram-sha-256",
    ]
    for cidr in cidrs_of(instance):
        lines.append(f"{extern:<7} all  all  {cidr:<15} scram-sha-256")
    return "\n".join(lines) + "\n"


def self_signed_certificate(common_name: str) -> tuple[str, str]:
    """Selbstsigniertes Zertifikat fuer ``sslmode=require``.

    Clients mit ``verify-full`` brauchen dieses Zertifikat als ``sslrootcert``;
    der Verbindungs-Hub zeigt es an.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name[:64] or "msm-postgres")])
    alt: list[x509.GeneralName] = []
    try:
        alt.append(x509.IPAddress(ipaddress.ip_address(common_name)))
    except ValueError:
        alt.append(x509.DNSName(common_name))
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=5 * 365))
        .add_extension(x509.SubjectAlternativeName(alt), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode("ascii")
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")
    return cert_pem, key_pem


def write_instance_file(db: Session, server: Server, relative_path: str, content: str) -> None:
    """Schreibt eine Datei ins Serververzeichnis — lokal oder ueber den Agenten."""
    from services import file_edit_service
    from services.server_file_access_service import _agent, _agent_key, apply_permissions, safe_path

    agent = _agent(server, db)
    if agent is not None:
        try:
            agent.files_write(_agent_key(server), relative_path, content, None, False)
        except NodeClientError as exc:
            raise PostgresServiceError(exc.message or "Datei konnte nicht geschrieben werden") from exc
        return
    target = safe_path(server.install_dir, relative_path)
    file_edit_service.write_text(target, content)
    apply_permissions(server.install_dir, target)


def read_certificate(db: Session, server: Server) -> str | None:
    from services.server_file_access_service import _agent, _agent_key, safe_path

    agent = _agent(server, db)
    try:
        if agent is not None:
            info = agent.files_read_info(_agent_key(server), TLS_CERT_FILE)
            return str(info.get("content") or "") or None
        target = safe_path(server.install_dir, TLS_CERT_FILE)
        return target.read_text(encoding="ascii") if target.is_file() else None
    except (NodeClientError, OSError):
        return None


def public_host(server: Server) -> str:
    """Adresse, unter der Clients von aussen die Instanz erreichen."""
    from urllib.parse import urlparse

    bind = (server.public_bind_ip or "").strip()
    if bind and bind not in {"0.0.0.0", "127.0.0.1"}:
        return bind
    node = server.node
    if node is not None and node.host:
        host = urlparse(node.host if "://" in node.host else f"https://{node.host}").hostname
        if host and host not in {"localhost", "127.0.0.1"}:
            return host
    return bind or "127.0.0.1"


def create_instance(
    db: Session,
    server: Server,
    *,
    database_name: str,
    username: str,
    password: str | None,
    allowed_cidrs: list[str],
    ssl_required: bool,
) -> None:
    """Legt Instanz, erste Datenbank und Startdateien an (noch ohne Container).

    Die Datenbankzeilen entstehen sofort mit verschluesselten Passwoertern;
    ``bootstrap`` bringt die Instanz spaeter auf diesen Stand. So geht bei einem
    abgebrochenen ersten Start kein Passwort verloren.
    """
    from services.postgres_service import (
        _generate_password,
        _mask_secret,
        _validate_identifier,
        instance_aad,
        user_password_aad,
    )
    from models import PostgresUser

    db_name = _validate_identifier(database_name)
    owner = _validate_identifier(username)
    app_user = _validate_identifier(f"{owner}_app"[:63])
    for name in (db_name, owner, app_user):
        if name in RESERVED_NAMES or name.startswith("pg_"):
            raise ValueError(f"Der Name '{name}' ist reserviert.")

    admin_password = _generate_password()
    owner_password = password or _generate_password()
    app_password = _generate_password()

    instance = PostgresInstance(
        server_id=server.id,
        admin_password_encrypted=AuthService.encrypt_secret(
            admin_password, aad=instance_aad(server.id)
        ),
        allowed_cidrs="\n".join(normalize_cidrs(allowed_cidrs)),
        ssl_required=ssl_required,
    )
    db.add(instance)
    database = PostgresDatabase(
        server_id=server.id,
        name=db_name,
        owner_role=owner,
        owner_password_encrypted=AuthService.encrypt_secret(owner_password, aad="msm:pg:db:owner"),
        # Der Owner einer eigenen Instanz gehoert dem Kunden: seine Zugangsdaten
        # sind von Anfang an herausgegeben (abrufbar und rotierbar im Hub).
        is_power_user=True,
        power_credentials_issued_at=datetime.now(timezone.utc),
    )
    user = PostgresUser(
        server_id=server.id,
        username=app_user,
        password_mask=_mask_secret(app_password),
        password_encrypted=AuthService.encrypt_secret(
            app_password, aad=user_password_aad(server.id, app_user)
        ),
    )
    db.add(database)
    db.add(user)
    db.flush()
    db.add(
        PostgresGrant(
            server_id=server.id, database_id=database.id, user_id=user.id, privilege="read_write"
        )
    )
    db.flush()
    db.refresh(server)

    cert_pem, key_pem = self_signed_certificate(public_host(server))
    write_instance_file(db, server, ADMIN_PASSWORD_FILE, admin_password)
    write_instance_file(db, server, HBA_FILE, hba_config(instance))
    write_instance_file(db, server, TLS_CERT_FILE, cert_pem)
    write_instance_file(db, server, TLS_KEY_FILE, key_pem)


def update_network(
    db: Session, server: Server, *, allowed_cidrs: list[str], ssl_required: bool
) -> PostgresInstance:
    instance = server.postgres_instance
    if instance is None:
        raise ValueError("Server ist kein Datenbankserver.")
    instance.allowed_cidrs = "\n".join(normalize_cidrs(allowed_cidrs))
    instance.ssl_required = bool(ssl_required)
    write_instance_file(db, server, HBA_FILE, hba_config(instance))
    db.commit()
    # Laeuft die Instanz, gilt die neue Liste sofort; sonst beim naechsten Start.
    try:
        postgres_service.run(
            db, server, database_name=CONTROL_DB, identity="admin",
            statements=[("SELECT pg_reload_conf()", None)],
        )
    except (PostgresServiceError, ValueError):
        logger.info("pg_reload_conf fuer Server %s nicht moeglich (Instanz aus?)", server.id)
    return instance


def is_ready(db: Session, server: Server) -> bool:
    try:
        postgres_service.run(
            db, server, database_name=CONTROL_DB, identity="admin",
            statements=[("SELECT 1", None)], timeout_ms=2000,
        )
        return True
    except (PostgresServiceError, ValueError):
        return False


def bootstrap(db: Session, server: Server) -> list[str]:
    """Bringt die Instanz auf den Stand der Datenbankzeilen. Wiederholbar.

    Gibt die neu angelegten Datenbanken zurueck.
    """
    from models import PostgresUser

    if server.postgres_instance is None:
        raise ValueError("Server ist kein Datenbankserver.")
    # Das Passwort brauchte nur das erste initdb. Danach kennt es nur noch das
    # Panel (DIS) — nicht mehr jeder mit Dateizugriff auf den Server.
    write_instance_file(db, server, ADMIN_PASSWORD_FILE, "")

    created: list[str] = []
    rows = (
        db.query(PostgresDatabase)
        .filter(PostgresDatabase.server_id == server.id)
        .order_by(PostgresDatabase.id)
        .all()
    )
    for database in rows:
        found = postgres_service.run(
            db, server, database_name=CONTROL_DB, identity="admin",
            statements=[("SELECT 1 FROM pg_database WHERE datname = %s", [database.name])],
        )["results"][0]["rows"]
        if found:
            continue
        grant = (
            db.query(PostgresGrant)
            .filter(PostgresGrant.database_id == database.id)
            .order_by(PostgresGrant.id)
            .first()
        )
        user = db.get(PostgresUser, grant.user_id) if grant else None
        if user is None or not user.password_encrypted:
            raise PostgresServiceError(f"Zugangsdaten fuer '{database.name}' fehlen.")
        verbindung = postgres_service._verbindung(db, server)
        try:
            verbindung.client.postgres_provision(
                verbindung.mit_ziel(
                    {
                        "admin_password": verbindung.admin_password,
                        "db_name": database.name,
                        "owner_role": database.owner_role,
                        "owner_password": postgres_service._owner_password(database),
                        "user_name": user.username,
                        "user_password": AuthService.decrypt_secret(
                            user.password_encrypted,
                            aad=postgres_service.user_password_aad(server.id, user.username),
                        ),
                        "power_user": False,
                    }
                )
            )
        except NodeClientError as exc:
            raise PostgresServiceError(exc.message or "Datenbank konnte nicht angelegt werden") from exc
        created.append(database.name)
    return created


def bootstrap_in_background(server_id: int, *, timeout_seconds: float = 300.0) -> threading.Thread:
    """Wartet, bis der erste Start fertig ist, und richtet dann die Datenbanken ein.

    Beim ersten Start zieht Docker das Image und ``initdb`` laeuft — das dauert.
    Scheitert es, steht der Grund am Server; ``POST .../instance/bootstrap``
    wiederholt es.
    """

    def arbeit() -> None:
        from database import SessionLocal

        deadline = time.monotonic() + timeout_seconds
        db = SessionLocal()
        try:
            while True:
                server = db.get(Server, server_id)
                if server is None:
                    return
                if is_ready(db, server):
                    break
                if time.monotonic() > deadline:
                    _status(db, server_id, "Datenbank wurde nicht rechtzeitig bereit — bitte Einrichtung wiederholen.")
                    return
                db.expire_all()
                time.sleep(3)
            bootstrap(db, server)
            db.commit()
        except Exception as exc:  # noqa: BLE001 — Thread darf nicht sterben
            db.rollback()
            logger.warning("Postgres-Einrichtung fuer Server %s fehlgeschlagen: %s", server_id, type(exc).__name__)
            _status(db, server_id, "Einrichtung der Datenbank fehlgeschlagen — bitte wiederholen.")
        finally:
            db.close()

    thread = threading.Thread(target=arbeit, daemon=True, name=f"msm-pg-bootstrap-{server_id}")
    thread.start()
    return thread


def _status(db: Session, server_id: int, message: str) -> None:
    server = db.get(Server, server_id)
    if server is not None:
        server.status_message = message
        db.commit()


def needs_bootstrap(db: Session, server: Server) -> bool:
    """True, solange eine Datenbankzeile in der Instanz noch fehlt (fuer den Hub)."""
    try:
        names = [row.name for row in db.query(PostgresDatabase).filter(PostgresDatabase.server_id == server.id)]
        if not names:
            return False
        found = postgres_service.run(
            db, server, database_name=CONTROL_DB, identity="admin",
            statements=[("SELECT count(*) FROM pg_database WHERE datname = ANY(%s)", [names])],
            timeout_ms=2000,
        )["results"][0]["rows"][0][0]
        return int(found) < len(names)
    except (PostgresServiceError, ValueError):
        return True

