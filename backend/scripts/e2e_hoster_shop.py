"""End-to-End-Durchlauf der Hoster-Anbindung — ein Shop, der Server verkauft.

Was hier echt laeuft und was nicht, damit niemand das Ergebnis fuer mehr haelt
als es ist:

ECHT
  - die vollstaendige FastAPI-Anwendung ueber HTTP (ASGI, keine Direktaufrufe)
  - eine frische Datenbank; die beiden neuen Hoster-Migrationen fahren echt
    rueckwaerts und wieder vorwaerts (die Basisrevision der Kette ist
    PostgreSQL-spezifisch, das Grundschema kommt deshalb aus den Modellen —
    dieselbe Technik wie in tests/test_schema_constraints.py)
  - Cookie-Login, CSRF, RBAC, jede Rechtepruefung
  - die Blueprint-Registry von der Platte, echte Portvergabe
  - das Installationsverzeichnis wird wirklich angelegt und wirklich geloescht
  - Rollenvergabe, Rechtevergabe, KI-Kontingente, Webhook-Warteschlange samt
    HMAC-Signatur, der Wartungslauf des Schedulers

GESTUBBT — und zwar nur an der Infrastrukturgrenze, weil diese Maschine sie
nicht hat:
  - DIS-Sidecar (Krypto)  → dieselben Ersatzfunktionen wie in der Testsuite
  - `plugin.install()`    → SteamCMD/Container-Start; die Spieldateien wuerden
                            hier tatsaechlich heruntergeladen
  - Docker                → Container anlegen/entfernen
  - `is_port_available()`   → der Host-Check ruft `ss`, das es unter Windows
                            nicht gibt. Die Portvergabe selbst (Bereich, Rollen,
                            Konfliktpruefung gegen die Datenbank) laeuft echt.
  - PostgreSQL            → Serverdatenbanken anlegen/verwerfen

Aufruf:  python scripts/e2e_hoster_shop.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):  # Windows-Konsole ist cp1252
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

ARBEIT = Path(tempfile.mkdtemp(prefix="msm-e2e-"))
DB_DATEI = ARBEIT / "panel.db"
SERVER_DIR = ARBEIT / "servers"
SERVER_DIR.mkdir()

# Muss vor jedem Import stehen, der die Settings liest.
os.environ["MSM_DATABASE_URL"] = f"sqlite:///{DB_DATEI.as_posix()}"
os.environ["MSM_SECRET_KEY"] = "e2e-secret-key-32-chars-long!!!!"
os.environ["MSM_DEBUG"] = "true"
os.environ["MSM_TESTING"] = "true"
os.environ["MSM_PANEL_URL"] = "http://localhost:3000"
os.environ["MSM_SERVERS_DIR"] = str(SERVER_DIR)

import hashlib as _hl  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402
from contextlib import ExitStack  # noqa: E402
from unittest.mock import patch  # noqa: E402

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine, event as sa_event  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

import database as db_module  # noqa: E402

db_module.engine = create_engine(
    os.environ["MSM_DATABASE_URL"], connect_args={"check_same_thread": False}
)
db_module.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=db_module.engine)


@sa_event.listens_for(db_module.engine, "connect")
def _fremdschluessel_scharf(dbapi_connection, _record) -> None:
    dbapi_connection.execute("PRAGMA foreign_keys=ON")


from services.dis_client import DisClient  # noqa: E402


def _enc(plaintext: str, aad: str | None = None) -> str:
    return "test-enc-v1:" + (aad or "").encode().hex() + ":" + plaintext.encode().hex()


def _dec(ciphertext: str, aad: str | None = None) -> str:
    teile = ciphertext.split(":")
    return bytes.fromhex(teile[2]).decode()


def _hash(passwort: str) -> str:
    return "msm-pw-v1:test:" + _hl.sha256(passwort.encode()).hexdigest()


DisClient.encrypt = staticmethod(_enc)
DisClient.decrypt = staticmethod(_dec)
DisClient.hash_password = staticmethod(_hash)
DisClient.verify_password = staticmethod(lambda p, h: h == _hash(p))
DisClient.is_dis_hash = staticmethod(lambda h: h.startswith("msm-pw-v1:"))
DisClient.health_check = staticmethod(lambda: True)

from fastapi.testclient import TestClient  # noqa: E402

from main import app  # noqa: E402
from models import (  # noqa: E402
    HosterService,
    Node,
    Role,
    RoleAiLimit,
    RolePermission,
    Server,
    ServerPermission,
    User,
)
from services.ai_limit_service import resolve_effective_limits  # noqa: E402
from services.auth_service import AuthService  # noqa: E402
from services.role_service import (  # noqa: E402
    effective_user_role_ids,
    ensure_system_roles,
    set_user_roles,
)


# ── Ausgabe ────────────────────────────────────────────────────────────────

GRUEN, ROT, GELB, GRAU, AUS = "\033[32m", "\033[31m", "\033[33m", "\033[90m", "\033[0m"
_zaehler = {"ok": 0, "fehler": 0}


def abschnitt(titel: str) -> None:
    print(f"\n{GELB}{'-' * 74}{AUS}\n{GELB}  {titel}{AUS}\n{GELB}{'-' * 74}{AUS}")


def pruefe(bedingung: bool, beschreibung: str, detail: str = "") -> None:
    if bedingung:
        _zaehler["ok"] += 1
        print(f"  {GRUEN}OK  {AUS} {beschreibung}" + (f" {GRAU}({detail}){AUS}" if detail else ""))
    else:
        _zaehler["fehler"] += 1
        print(f"  {ROT}FEHL{AUS} {beschreibung}" + (f" {ROT}({detail}){AUS}" if detail else ""))


def hinweis(text: str) -> None:
    print(f"  {GRAU}--   {text}{AUS}")


# ── Infrastrukturgrenze ────────────────────────────────────────────────────

INSTALLIERT: list[int] = []


def _install(self, server):
    """Statt SteamCMD: merkt sich, welcher Server installiert werden sollte.

    Wird als Klassenmethode ersetzt, bekommt also `self` mit.
    """
    INSTALLIERT.append(server.id)
    return {"status": "installing"}


class _Docker:
    """Jede Docker-Operation meldet Erfolg. Diese Maschine hat kein Docker."""

    def __getattr__(self, _name):
        return lambda *a, **k: {"ok": True}


def stubs_kauf():
    """Alles, was ein Kauf an Infrastruktur braucht und hier nicht existiert."""
    stapel = ExitStack()
    stapel.enter_context(patch("games.blueprint_plugin.BlueprintPlugin.install", _install))
    stapel.enter_context(patch("services.port_allocation_service.is_port_available", lambda *a, **k: True))
    stapel.enter_context(patch("services.server_action_service.request_lifecycle_operation", lambda *a, **k: {"task_id": None}))
    return stapel


def stubs_zustand():
    """Nur der Lifecycle-Aufruf — fuer Sperren und Kuendigen ohne Provisionierung."""
    stapel = ExitStack()
    stapel.enter_context(patch("services.server_action_service.request_lifecycle_operation", lambda *a, **k: {"task_id": None}))
    return stapel


def stubs_loeschen():
    """Container, Firewall und PostgreSQL beim Loeschen. Das Dateisystem NICHT."""
    stapel = ExitStack()
    stapel.enter_context(patch("services.server_deletion_service.docker_service", _Docker()))
    stapel.enter_context(patch("services.server_deletion_service.close_ports", lambda *a, **k: None))
    stapel.enter_context(patch("services.server_deletion_service.iptables_revoke_server", lambda *a, **k: None))
    stapel.enter_context(patch("services.server_deletion_service.postgres_service.drop_server_resources", lambda *a, **k: None))
    return stapel


def installation_melden(db, server_id: int) -> None:
    """Was in Produktion der Node-Agent tut, wenn die Installation fertig ist.

    Ohne diese Rueckmeldung bleibt die globale Installationssperre stehen und
    jeder weitere Kauf liefe in `install_update_already_running` — nicht wegen
    eines Fehlers im Panel, sondern weil hier niemand SteamCMD zu Ende bringt.
    """
    from services.install_update_lock_service import release_install_update_lock
    from services.operation_task_service import finish_server_provisioning

    finish_server_provisioning(db, server_id, succeeded=True)
    release_install_update_lock(server_id)
    db.commit()


VERTRAEGE_DES_KUNDEN = ("SVC-1", "SVC-2")


def kundenvertraege(db):
    """Nur die beiden echten Vertraege — nicht die absichtlich gescheiterten."""
    return (
        db.query(HosterService)
        .filter(HosterService.external_service_id.in_(VERTRAEGE_DES_KUNDEN))
        .all()
    )


def kundenserver(db):
    """Die Server hinter diesen Vertraegen, soweit es sie noch gibt."""
    ids = [v.server_id for v in kundenvertraege(db) if v.server_id]
    return db.query(Server).filter(Server.id.in_(ids)).all() if ids else []


def main() -> int:
    abschnitt("0 - Einrichtung — echte Migrationen auf leerer Datenbank")

    from sqlalchemy import inspect as sa_inspect

    from database import Base
    import models  # noqa: F401 — registriert das vollstaendige ORM-Schema

    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "migrations"))
    Base.metadata.create_all(db_module.engine)
    command.stamp(config, "head")
    print(f"  {GRAU}Datenbank: {DB_DATEI}{AUS}")
    print(f"  {GRAU}Serververzeichnis: {SERVER_DIR}{AUS}")

    # Die beiden neuen Revisionen wirklich fahren — rueckwaerts und wieder vor.
    command.downgrade(config, "20260812_01")
    weg_p = {s["name"] for s in sa_inspect(db_module.engine).get_columns("hoster_products")}
    weg_s = {s["name"] for s in sa_inspect(db_module.engine).get_columns("hoster_services")}
    pruefe("role_id" not in weg_p and "granted_role_id" not in weg_s,
           "Downgrade nimmt beide neuen Spalten wieder heraus")
    command.upgrade(config, "head")

    db = db_module.SessionLocal()
    spalten = {s["name"] for s in sa_inspect(db_module.engine).get_columns("hoster_products")}
    pruefe("role_id" in spalten, "Migration brachte hoster_products.role_id")
    spalten = {s["name"] for s in sa_inspect(db_module.engine).get_columns("hoster_services")}
    pruefe("granted_role_id" in spalten, "Migration brachte hoster_services.granted_role_id")

    ensure_system_roles(db)
    db.add(Node(name="local", host="127.0.0.1", auth_token_enc=_enc("node-token"),
                is_local=True, status="online"))
    owner = AuthService.create_owner(db, "owner", "owner@shop.test", "OwnerPass123!")
    owner.email_verified = True
    db.commit()

    # Der Betreiber legt zwei Tarifrollen an — mit unterschiedlichem KI-Kontingent.
    tarife = {}
    for name, tokens in (("gamer-basis", 50_000), ("gamer-premium", 500_000)):
        rolle = Role(name=name, is_system=False)
        db.add(rolle)
        db.flush()
        db.add(RoleAiLimit(
            role_id=rolle.id,
            daily_token_limit=tokens,
            weekly_token_limit=tokens * 5,
            monthly_token_limit=tokens * 20,
            requests_per_minute=20,
            concurrent_operations=2,
            monthly_cost_limit_cents=1000,
            max_reasoning_effort=3,
        ))
        tarife[name] = rolle
    db.commit()
    print(f"  {GRAU}Tarifrollen: gamer-basis (50k Tokens/Tag), gamer-premium (500k){AUS}")

    dienstbenutzer = AuthService.create_user(db, "shop-dienst", "dienst@shop.test", "DienstPass123!")
    dienstrolle = Role(name="shop-dienst", is_system=False)
    db.add(dienstrolle)
    db.flush()
    for key in ("servers.create", "servers.delete"):
        db.add(RolePermission(role_id=dienstrolle.id, permission_key=key))
    db.commit()
    set_user_roles(db, dienstbenutzer, [dienstrolle.id])
    db.commit()

    client = TestClient(app)

    anmeldung = client.post(
        "/api/auth/login",
        json={"username": "owner", "password": "OwnerPass123!", "otp_code": None},
    )
    pruefe(anmeldung.status_code == 200, "Betreiber meldet sich am Panel an")
    cookies = dict(anmeldung.cookies)
    csrf = {"X-CSRF-Token": cookies.get("__Secure-csrf_token", "")}

    abschnitt("1 - Der Betreiber richtet die Shop-Anbindung ein")

    angelegt = client.post(
        "/api/hoster/integrations",
        json={
            "name": "Mein Gameserver-Shop",
            "slug": "gameshop",
            "enabled": True,
            "service_user_id": dienstbenutzer.id,
            "webhook_url": "https://shop.example/hooks/msm",
            "terminate_grace_days": 7,
        },
        cookies=cookies,
        headers=csrf,
    )
    pruefe(angelegt.status_code == 201, "Integration angelegt", f"HTTP {angelegt.status_code}")
    api_key = angelegt.json()["value"]
    pruefe(len(api_key) > 20, "API-Key genau einmal ausgegeben", f"…{api_key[-4:]}")

    integration_id = client.get("/api/hoster/integrations", cookies=cookies).json()[0]["id"]

    # Ohne Webhook-Secret stellt MSM bewusst keine Zustellung ein: eine Zeile,
    # die nie zugestellt werden kann, waere nur irrefuehrender Ballast.
    geheimnis = client.post(
        f"/api/hoster/integrations/{integration_id}/webhook-secret",
        cookies=cookies,
        headers=csrf,
    )
    pruefe(geheimnis.status_code == 200, "Webhook-Secret erzeugt",
           f"…{geheimnis.json().get('value', '')[-4:]}")
    gelesen = client.get("/api/hoster/integrations", cookies=cookies).json()[0]
    pruefe(
        "value" not in gelesen and gelesen["api_key_hint"] is not None,
        "Leseantwort enthaelt nur den Hinweis, nie den Schluessel",
    )

    def produkt_speichern(key: str, spiel: str, ram: int, rolle_id: int | None, **rest):
        nutzlast = {
            "external_product_key": key,
            "game_type": spiel,
            "ram_limit_mb": ram,
            "cpu_limit_percent": 200,
            "disk_limit_gb": 20,
            "node_id": None,
            "backup_interval_hours": 24,
            "role_id": rolle_id,
            "enabled": True,
        }
        nutzlast.update(rest)
        return client.put(
            f"/api/hoster/integrations/{integration_id}/products",
            json=nutzlast,
            cookies=cookies,
            headers=csrf,
        )

    p1 = produkt_speichern("MC-BASIS", "minecraft_paper", 2048, tarife["gamer-basis"].id)
    p2 = produkt_speichern("MC-PREMIUM", "minecraft_paper", 8192, tarife["gamer-premium"].id)
    pruefe(p1.status_code == 200, "Produkt MC-BASIS zugeordnet", "minecraft_paper, 2 GB, Rolle gamer-basis")
    pruefe(p2.status_code == 200, "Produkt MC-PREMIUM zugeordnet", "minecraft_paper, 8 GB, Rolle gamer-premium")

    abschnitt("2 - Fehlerwege beim Einrichten")

    unbekannt = produkt_speichern("MC-KAPUTT", "gibt-es-nicht", 2048, None)
    pruefe(unbekannt.status_code == 422, "Unbekannter Blueprint wird beim Speichern abgelehnt")

    fremde_rolle = Role(name="fast-admin", is_system=False)
    db.add(fremde_rolle)
    db.flush()
    db.add(RolePermission(role_id=fremde_rolle.id, permission_key="roles.manage"))
    db.commit()
    zu_maechtig = produkt_speichern("MC-MACHT", "minecraft_paper", 2048, fremde_rolle.id)
    pruefe(
        zu_maechtig.status_code == 422,
        "Rolle oberhalb des Dienstbenutzers wird abgelehnt",
        f"HTTP {zu_maechtig.status_code}",
    )
    adminrolle = db.query(Role).filter(Role.name == "admin").one()
    admin_versuch = produkt_speichern("MC-ADMIN", "minecraft_paper", 2048, adminrolle.id)
    pruefe(
        admin_versuch.status_code == 422,
        "admin-Rolle als Produktrolle wird abgelehnt",
        f"HTTP {admin_versuch.status_code}",
    )
    hinweis(
        "Hier greift die zweite Schranke: der Owner darf die Rolle zwar waehlen (403 "
        "traefe einen Nicht-Owner), aber der Dienstbenutzer traegt sie nicht - also 422."
    )

    abschnitt("3 - Ein Kunde kauft — die Fehlerwege zuerst")

    def kaufen(vertrag: str, kunde: str, produkt: str | None, zustand: str = "active",
               schluessel: str | None = None, email: str | None = None):
        koerper = {"desired_state": zustand, "external_subject": kunde}
        if produkt:
            koerper["product_key"] = produkt
        if email:
            koerper["email"] = email
        kopf = {} if schluessel == "" else {"X-MSM-Hoster-Key": schluessel or api_key}
        return client.put(f"/api/hoster/v1/services/{vertrag}", json=koerper, headers=kopf)

    pruefe(kaufen("SVC-1", "kunde-1", "MC-BASIS", schluessel="").status_code == 401,
           "Kauf ohne API-Key wird abgewiesen")
    pruefe(kaufen("SVC-1", "kunde-1", "MC-BASIS", schluessel="falscher-key").status_code == 401,
           "Kauf mit falschem API-Key wird abgewiesen")
    pruefe(kaufen("SVC-1", "kunde-1", "GIBT-ES-NICHT").status_code == 422,
           "Kauf eines unbekannten Produkts wird abgewiesen")
    pruefe(kaufen("SVC-1", "kunde-1", None).status_code == 422,
           "Neuer Vertrag ohne Produktkennung wird abgewiesen")
    pruefe(db.query(Server).count() == 0, "Nach allen Fehlversuchen existiert kein Server")

    abschnitt("4 - Der erste echte Kauf — Konto, Rolle, Rechte, Spiel")

    with stubs_kauf():
        kauf = kaufen("SVC-1", "kunde-1", "MC-BASIS", email="spieler@kunde.test")
    pruefe(kauf.status_code == 200, "Kauf angenommen", f"HTTP {kauf.status_code} - {kauf.text[:80]}")
    if kauf.status_code != 200:
        return 1

    db.expire_all()
    vertrag = db.query(HosterService).filter(HosterService.external_service_id == "SVC-1").one()
    kunde = db.query(User).filter(User.id == vertrag.identity.user_id).one()
    server = db.query(Server).filter(Server.id == vertrag.server_id).one()

    pruefe(kunde is not None, "Panel-Konto wurde angelegt", kunde.username)
    pruefe(kunde.email == "spieler@kunde.test", "E-Mail des Shops uebernommen", kunde.email or "—")
    pruefe(kunde.email_verified, "Keine zweite E-Mail-Verifikation noetig")
    pruefe(kunde.password_hash is not None and len(kunde.password_hash) > 10,
           "Kein leeres Passwortfeld (waere ein Loginpfad)")

    rollen = set(effective_user_role_ids(db, kunde))
    pruefe(tarife["gamer-basis"].id in rollen, "Tarifrolle gamer-basis zugewiesen")
    systemrolle = db.query(Role).filter(Role.name == "user").one()
    pruefe(systemrolle.id in rollen, "Standardrolle user bleibt daneben bestehen")

    grenzen = resolve_effective_limits(db, kunde)
    pruefe(grenzen.daily_token_limit == 50_000,
           "KI-Kontingent des Tarifs greift", f"{grenzen.daily_token_limit:,} Tokens/Tag")

    rechte = {
        r.permission_key
        for r in db.query(ServerPermission).filter(
            ServerPermission.user_id == kunde.id, ServerPermission.server_id == server.id
        )
    }
    pruefe("server.start" in rechte and "server.files.write" in rechte,
           "Serverrechte auf genau seinem Server gesetzt", f"{len(rechte)} Rechte")
    pruefe("servers.delete" not in rechte and "server.reinstall" not in rechte,
           "Gefaehrliche Rechte bewusst nicht dabei")

    pruefe(server.game_type == "minecraft_paper", "Das gekaufte Spiel wird eingerichtet", server.game_type)
    pruefe(server.ram_limit_mb == 2048 and server.cpu_limit_percent == 200,
           "Ressourcen kommen aus dem Tarif", f"{server.ram_limit_mb} MB / {server.cpu_limit_percent} %")
    pruefe(server.backup_interval_hours == 24, "Backup-Intervall aus dem Tarif")
    pruefe(server.id in INSTALLIERT, "Installation des Blueprints wurde angestossen")
    pruefe(server.install_dir and Path(server.install_dir).is_dir(),
           "Installationsverzeichnis liegt wirklich auf der Platte", server.install_dir or "—")
    ports = {p.role: p.port for p in server.ports} if hasattr(server, "ports") else {}
    pruefe(bool(ports), "Ports wurden vergeben", ", ".join(f"{k}={v}" for k, v in ports.items()) or "—")

    installation_melden(db, server.id)
    from models import OperationTask

    aufgabe = (
        db.query(OperationTask)
        .filter(OperationTask.server_id == server.id)
        .order_by(OperationTask.created_at.desc())
        .first()
    )
    pruefe(aufgabe is not None and aufgabe.status == "succeeded",
           "Die Rueckmeldung des Agenten schliesst die Provisionierungsaufgabe ab",
           aufgabe.status if aufgabe else "keine Aufgabe")
    hinweis("server.status bleibt hier auf 'installing' - den Endzustand setzt der "
            "Container-Start, und Docker gibt es auf dieser Maschine nicht.")

    abschnitt("5 - Wiederholter Auftrag — der Shop hatte einen Netzwerkfehler")

    with stubs_kauf():
        nochmal = kaufen("SVC-1", "kunde-1", "MC-BASIS")
    pruefe(nochmal.status_code == 200, "Derselbe Auftrag wird erneut angenommen")
    db.expire_all()
    pruefe(db.query(Server).count() == 1, "Es entsteht KEIN zweiter Server", f"{db.query(Server).count()} Server")

    abschnitt("6 - Derselbe Kunde kauft einen zweiten, groesseren Server")

    with stubs_kauf():
        zweiter = kaufen("SVC-2", "kunde-1", "MC-PREMIUM")
    pruefe(zweiter.status_code == 200, "Zweiter Kauf angenommen",
           f"HTTP {zweiter.status_code} - {zweiter.text[:160]}")
    db.expire_all()
    zweiter_vertrag = db.query(HosterService).filter(
        HosterService.external_service_id == "SVC-2").one_or_none()
    if zweiter_vertrag is not None and zweiter_vertrag.server_id:
        installation_melden(db, zweiter_vertrag.server_id)
    pruefe(db.query(Server).count() == 2, "Zweiter Server angelegt")
    from models import HosterIdentity

    anzahl_identitaeten = db.query(HosterIdentity).count()
    pruefe(anzahl_identitaeten == 1,
           "Kein zweites Konto — die Identitaet wurde wiedererkannt",
           f"{anzahl_identitaeten} Identitaet(en)")
    rollen = set(effective_user_role_ids(db, kunde))
    pruefe(tarife["gamer-premium"].id in rollen and tarife["gamer-basis"].id in rollen,
           "Kunde traegt jetzt beide Tarifrollen")
    grenzen = resolve_effective_limits(db, kunde)
    pruefe(grenzen.daily_token_limit == 500_000,
           "Das hoehere Kontingent gewinnt", f"{grenzen.daily_token_limit:,} Tokens/Tag")

    abschnitt("7 - Der Kunde kommt aus dem Shop ins Panel (Handoff)")

    handoff = client.post(
        "/api/hoster/v1/handoffs",
        json={"external_service_id": "SVC-1", "target_path": "/servers"},
        headers={"X-MSM-Hoster-Key": api_key},
    )
    pruefe(handoff.status_code == 200, "Einmal-Link erzeugt", f"HTTP {handoff.status_code}")
    if handoff.status_code == 200:
        token = handoff.json().get("token") or handoff.json().get("url", "").rsplit("/", 1)[-1]
        eingeloest = client.get(f"/api/hoster/handoff/{token}", follow_redirects=False)
        ziel = eingeloest.headers.get("location", "")
        pruefe("handoff=invalid" not in ziel and eingeloest.status_code == 302,
               "Link loest eine Panel-Sitzung aus", ziel)
        zweimal = client.get(f"/api/hoster/handoff/{token}", follow_redirects=False)
        ziel2 = zweimal.headers.get("location", "")
        pruefe("handoff=invalid" in ziel2, "Ein zweites Einloesen wird verworfen", ziel2)
        hinweis("Beide Faelle antworten mit 302 - bewusst nicht unterscheidbar: "
                "unbekannt, abgelaufen und verbraucht sehen fuer einen Angreifer gleich aus.")


    abschnitt("7b - Mandantentrennung und Fehlschlaege mitten im Kauf")

    # Ein eigener Client fuer den Kunden: der Betreiber-Client haelt seine
    # eigenen Cookies, und die duerfen sich hier nicht mischen.
    kundenclient = TestClient(app)
    zweiter_link = client.post(
        "/api/hoster/v1/handoffs",
        json={"external_service_id": "SVC-1", "target_path": "/servers"},
        headers={"X-MSM-Hoster-Key": api_key},
    )
    kunden_token = zweiter_link.json().get("token") or zweiter_link.json().get("url", "").rsplit("/", 1)[-1]
    einloesung = kundenclient.get(f"/api/hoster/handoff/{kunden_token}", follow_redirects=False)
    # Die Sitzungscookies tragen das `__Secure-`-Praefix und landen ueber http
    # nicht im Cookie-Jar. Sie werden deshalb ausdruecklich mitgereicht — genau
    # wie oben beim Betreiber.
    kunden_cookies = dict(einloesung.cookies)

    fremdserver = Server(
        name="server-eines-anderen",
        game_type="minecraft_paper",
        status="stopped",
        install_dir=str(SERVER_DIR / "fremd"),
    )
    db.add(fremdserver)
    db.commit()

    eigene = kundenclient.get("/api/servers", cookies=kunden_cookies)
    pruefe(eigene.status_code == 200, "Der Kunde ist angemeldet und darf seine Serverliste lesen",
           f"HTTP {eigene.status_code}")
    if eigene.status_code == 200:
        sichtbar = {s["id"] for s in eigene.json()}
        pruefe(fremdserver.id not in sichtbar,
               "Der fremde Server taucht in seiner Liste NICHT auf",
               f"sichtbar: {sorted(sichtbar)}")
        einzeln = kundenclient.get(f"/api/servers/{fremdserver.id}", cookies=kunden_cookies)
        pruefe(einzeln.status_code in (403, 404),
               "Auch der direkte Zugriff auf den fremden Server wird verwehrt",
               f"HTTP {einzeln.status_code}")

    # Die Installation scheitert mitten im Kauf.
    vorher_server = db.query(Server).count()
    with ExitStack() as stapel:
        stapel.enter_context(patch("services.port_allocation_service.is_port_available", lambda *a, **k: True))
        stapel.enter_context(patch(
            "games.blueprint_plugin.BlueprintPlugin.install",
            lambda self, server: (_ for _ in ()).throw(RuntimeError("SteamCMD kaputt")),
        ))
        kaputt = kaufen("SVC-KAPUTT", "kunde-9", "MC-BASIS")
    pruefe(kaputt.status_code >= 500, "Fehlgeschlagene Installation meldet einen Fehler",
           f"HTTP {kaputt.status_code}")
    db.expire_all()
    pruefe(db.query(Server).count() == vorher_server,
           "Es bleibt KEIN halber Server zurueck",
           f"{db.query(Server).count()} statt {vorher_server}")
    kaputter_vertrag = db.query(HosterService).filter(
        HosterService.external_service_id == "SVC-KAPUTT").one_or_none()
    pruefe(kaputter_vertrag is not None and kaputter_vertrag.status == "failed",
           "Der Vertrag bleibt abfragbar und steht auf failed",
           kaputter_vertrag.status if kaputter_vertrag else "kein Vertrag")
    hinweis(f"stabiler Fehlercode fuer den Shop: {kaputter_vertrag.status_code if kaputter_vertrag else '—'}")

    # Der Betreiber deaktiviert den Dienstbenutzer.
    db.refresh(dienstbenutzer)
    dienstbenutzer.is_active = False
    db.commit()
    with stubs_kauf():
        ohne_dienst = kaufen("SVC-OHNE-DIENST", "kunde-8", "MC-BASIS")
    pruefe(ohne_dienst.status_code == 422,
           "Ohne aktiven Dienstbenutzer nimmt MSM keinen Auftrag an",
           f"HTTP {ohne_dienst.status_code}")
    dienstbenutzer.is_active = True
    db.commit()

    abschnitt("8 - Der Kunde zahlt nicht — Vertrag wird gesperrt")

    with stubs_zustand():
        gesperrt = kaufen("SVC-2", "kunde-1", None, zustand="suspended")
    pruefe(gesperrt.status_code == 200, "Sperre angenommen")
    db.expire_all()
    vertrag2 = db.query(HosterService).filter(HosterService.external_service_id == "SVC-2").one()
    pruefe(vertrag2.status == "suspended", "Vertrag ist gesperrt", vertrag2.status)
    rechte2 = db.query(ServerPermission).filter(
        ServerPermission.user_id == kunde.id, ServerPermission.server_id == vertrag2.server_id
    ).count()
    pruefe(rechte2 == 0, "Serverrechte am gesperrten Server entzogen")
    rollen = set(effective_user_role_ids(db, kunde))
    pruefe(tarife["gamer-premium"].id not in rollen, "Premium-Rolle entzogen")
    pruefe(tarife["gamer-basis"].id in rollen, "Basis-Rolle bleibt — der andere Vertrag laeuft")
    grenzen = resolve_effective_limits(db, kunde)
    pruefe(grenzen.daily_token_limit == 50_000,
           "KI-Kontingent faellt auf den laufenden Tarif zurueck", f"{grenzen.daily_token_limit:,}")
    pruefe(db.query(Server).filter(Server.id == vertrag2.server_id).count() == 1,
           "Server und Daten bleiben erhalten")
    db.refresh(kunde)
    pruefe(kunde.is_active, "Der Panelaccount bleibt bestehen")

    abschnitt("9 - Der Kunde zahlt doch — Vertrag laeuft wieder")

    with stubs_kauf():
        entsperrt = kaufen("SVC-2", "kunde-1", None, zustand="active")
    pruefe(entsperrt.status_code == 200, "Entsperrung angenommen")
    db.expire_all()
    rechte2 = db.query(ServerPermission).filter(
        ServerPermission.user_id == kunde.id, ServerPermission.server_id == vertrag2.server_id
    ).count()
    pruefe(rechte2 > 0, "Serverrechte zurueck", f"{rechte2} Rechte")
    rollen = set(effective_user_role_ids(db, kunde))
    pruefe(tarife["gamer-premium"].id in rollen, "Premium-Rolle zurueck")
    db.expire_all()
    pruefe(len(kundenserver(db)) == 2, "Kein dritter Server entstanden",
           f"{len(kundenserver(db))} Server an den Vertraegen")

    abschnitt("10 - Tarifwechsel — Downgrade auf den kleinen Tarif")

    with stubs_kauf():
        gewechselt = kaufen("SVC-2", "kunde-1", "MC-BASIS", zustand="active")
    pruefe(gewechselt.status_code == 200, "Tarifwechsel angenommen")
    db.expire_all()
    rollen = set(effective_user_role_ids(db, kunde))
    pruefe(tarife["gamer-premium"].id not in rollen,
           "Premium-Rolle beim Downgrade entzogen — nicht gestapelt")
    grenzen = resolve_effective_limits(db, kunde)
    pruefe(grenzen.daily_token_limit == 50_000,
           "KI-Kontingent folgt dem Downgrade", f"{grenzen.daily_token_limit:,}")
    vertrag2 = db.query(HosterService).filter(HosterService.external_service_id == "SVC-2").one()
    pruefe(vertrag2.status_code == "product_changed_manual_resize_required",
           "Hinweis auf noetige Ressourcenanpassung gesetzt", vertrag2.status_code or "—")

    abschnitt("11 - Kuendigung — und die Aufbewahrungsfrist")

    with stubs_zustand():
        for vertragsnummer in ("SVC-1", "SVC-2"):
            gekuendigt = kaufen(vertragsnummer, "kunde-1", None, zustand="terminated")
            pruefe(gekuendigt.status_code == 200, f"Kuendigung {vertragsnummer} angenommen")

    db.expire_all()
    vertraege = kundenvertraege(db)
    pruefe(all(v.status == "terminating" for v in vertraege),
           "Beide Vertraege stehen auf terminating", ", ".join(v.status for v in vertraege))
    pruefe(all(v.terminate_after is not None for v in vertraege), "Aufbewahrungsfrist gesetzt")
    rollen = set(effective_user_role_ids(db, kunde))
    pruefe(tarife["gamer-basis"].id not in rollen and tarife["gamer-premium"].id not in rollen,
           "Beide Tarifrollen entzogen")
    pruefe(systemrolle.id in rollen, "Standardrolle user bleibt — der Account bleibt nutzbar")
    grenzen = resolve_effective_limits(db, kunde)
    hinweis(f"KI-Kontingent nach Kuendigung: {grenzen.daily_token_limit} (None = unbegrenzt, "
            "weil keine Rolle des Kunden mehr ein Kontingent hinterlegt hat)")
    pruefe(len(kundenserver(db)) == 2, "Server sind noch da — die Frist laeuft")
    verzeichnisse = [Path(s.install_dir) for s in kundenserver(db) if s.install_dir]
    pruefe(all(p.is_dir() for p in verzeichnisse), "Auch die Daten liegen noch auf der Platte")

    abschnitt("12 - Der Aufraeumlauf des Schedulers")

    from services.hoster_service_lifecycle import purge_terminated_services

    with stubs_loeschen():
        vorher = purge_terminated_services(db)
    pruefe(vorher == 0, "Vor Fristablauf wird nichts geloescht", f"{vorher} geloescht")
    pruefe(len(kundenserver(db)) == 2, "Beide Server stehen noch")

    db.expire_all()
    for v in kundenvertraege(db):
        v.terminate_after = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()

    with stubs_loeschen():
        geloescht = purge_terminated_services(db)
    pruefe(geloescht == 2, "Nach Fristablauf werden beide Server geloescht", f"{geloescht} geloescht")
    db.expire_all()
    pruefe(len(kundenserver(db)) == 0, "Keine Serverzeile mehr zu den Vertraegen")
    pruefe(db.query(Server).filter(Server.name == "server-eines-anderen").count() == 1,
           "Der fremde Server wurde NICHT mitgeloescht")
    pruefe(all(not p.exists() for p in verzeichnisse),
           "Die Installationsverzeichnisse sind wirklich von der Platte verschwunden")
    pruefe(all(v.status == "terminated" for v in kundenvertraege(db)),
           "Vertraege stehen auf terminated",
           ", ".join(v.status for v in kundenvertraege(db)))
    db.refresh(kunde)
    pruefe(kunde.is_active, "Der Panelaccount des Kunden bleibt bestehen")
    rollen = set(effective_user_role_ids(db, kunde))
    pruefe(rollen == {systemrolle.id}, "Nur noch die Standardrolle", str(sorted(rollen)))

    abschnitt("13 - Weitere Fehlerwege")

    unbekannter_vertrag = kaufen("SVC-GIBTESNICHT", "kunde-1", None, zustand="terminated")
    pruefe(unbekannter_vertrag.status_code == 422, "Kuendigung eines unbekannten Vertrags wird abgewiesen")

    produkt_speichern("MC-BASIS", "minecraft_paper", 2048, tarife["gamer-basis"].id, enabled=False)
    with stubs_kauf():
        deaktiviert = kaufen("SVC-3", "kunde-2", "MC-BASIS")
    pruefe(deaktiviert.status_code == 422, "Kauf eines deaktivierten Produkts wird abgewiesen")

    client.patch(
        f"/api/hoster/integrations/{integration_id}",
        json={"enabled": False},
        cookies=cookies,
        headers=csrf,
    )
    with stubs_kauf():
        tot = kaufen("SVC-4", "kunde-3", "MC-PREMIUM")
    pruefe(tot.status_code == 401, "Deaktivierte Integration nimmt keine Auftraege mehr an")

    abschnitt("14 - Was der Shop zurueckgemeldet bekommt")

    zustellungen = client.get(
        f"/api/hoster/integrations/{integration_id}/deliveries", cookies=cookies
    ).json()
    pruefe(len(zustellungen) > 0, "Webhook-Zustellungen wurden eingestellt", f"{len(zustellungen)} Stueck")
    ereignisse = [z["event_type"] for z in zustellungen]
    for erwartet in ("service.ready", "service.suspended", "service.terminated"):
        pruefe(erwartet in ereignisse, f"Ereignis {erwartet} gemeldet")
    pruefe(all("signature" not in str(z) for z in zustellungen),
           "Das Zustellprotokoll enthaelt weder Signatur noch Body")

    abschnitt("Ergebnis")
    gesamt = _zaehler["ok"] + _zaehler["fehler"]
    farbe = GRUEN if _zaehler["fehler"] == 0 else ROT
    print(f"  {farbe}{_zaehler['ok']} von {gesamt} Zusagen erfuellt{AUS}")
    if _zaehler["fehler"]:
        print(f"  {ROT}{_zaehler['fehler']} Fehlschlaege{AUS}")
    return 1 if _zaehler["fehler"] else 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(ARBEIT, ignore_errors=True)
    sys.exit(code)
