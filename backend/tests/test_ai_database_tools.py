"""Die KI im PostgreSQL-Studio — derselbe Weg wie der Benutzer, kein zweiter.

`read_database` und `propose_database_change` rufen `postgres_studio_service`
und damit `postgres_ddl.compile_operation`, genau wie die Studio-Routen. Ersetzt
ist hier nur die Leitung zur Instanz (`postgres_service.run`): sie zeichnet auf,
was gelaufen wäre. Alles davor — Operation prüfen, Plan kompilieren, Rechte aus
dem Plan, Probe mit Rollback, Ausführung, Audit — läuft echt.

Zusagen:

* Funktion, Trigger und Erweiterung entstehen über dieselben Operationen wie
  im Studio; beim Vorschlag läuft der Plan einmal verworfen (Probe), nach dem
  Klick einmal wirklich.
* Scheitert die Probe, entsteht kein Vorschlag — mit dem PostgreSQL-Fehler.
* Das Recht kommt aus dem Plan: Struktur verlangt `server.databases.admin`,
  auch wenn das Werkzeug mit `write` angeboten wird. Die Probe läuft erst nach
  dieser Prüfung — sie führt den Plan wirklich aus.
* Datenvernichtendes fragt immer (`always_confirm`), freies SQL auch.
* Nur der Worker schreibt; das Gehirn liest.
* Spielserver (gemeinsamer Cluster) und Datenbankserver teilen den Weg.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from models import (
    AiConversation,
    AuditLog,
    PostgresDatabase,
    PostgresInstance,
        Server,
    ServerPermission,
    User,
)
from services import ai_action_service, ai_proposal_service, ai_tool_registry, postgres_service
from services.ai_action_errors import AiActionValidationError
from services.ai_tool_registry import verlangt_klick
from services.auth_service import AuthService
from services.dis_client import DisClient
from services.role_service import create_role, set_user_roles


class Leitung:
    """Statt der Instanz: zeichnet jeden Lauf auf und antwortet leer."""

    def __init__(self) -> None:
        self.laeufe: list[dict] = []
        self.fehler: Exception | None = None

    def __call__(self, db, server, *, database_name, statements, identity, database, mode, rollback, row_limit, timeout_ms):
        self.laeufe.append(
            {"sql": [text for text, _ in statements], "identity": identity, "mode": mode, "rollback": rollback}
        )
        if self.fehler is not None:
            raise self.fehler
        return {
            "results": [{"columns": [], "rows": [], "row_count": 0, "status": "OK"} for _ in statements],
            "notices": [],
            "duration_ms": 1,
        }


@pytest.fixture
def leitung(monkeypatch: pytest.MonkeyPatch) -> Leitung:
    fake = Leitung()
    monkeypatch.setattr(postgres_service, "run", fake)
    return fake


def _server(db: Session, tmp_path: Path, *, game_type: str = "minecraft") -> Server:
    install_dir = tmp_path / uuid4().hex[:8]
    install_dir.mkdir()
    server = Server(
        name=f"DB-Test {game_type}",
        game_type=game_type,
        install_dir=str(install_dir),
        container_name=f"msm-db-{uuid4().hex[:8]}",
        status="running",
    )
    db.add(server)
    db.commit()
    db.refresh(server)
    if game_type == "postgres":
        # Datenbankserver heisst: eine eigene Instanz (`is_database_server`).
        db.add(
            PostgresInstance(
                server_id=server.id,
                admin_password_encrypted=AuthService.encrypt_secret("y" * 24, aad=f"msm:pg:instance:{server.id}"),
            )
        )
    db.add(
        PostgresDatabase(
            server_id=server.id,
            name="shop",
            owner_role=f"msm_s{server.id}_o1",
            owner_password_encrypted=AuthService.encrypt_secret("x" * 24, aad="msm:pg:db:owner"),
        )
    )
    db.commit()
    return server


def _conversation(db: Session, user: User, server: Server) -> AiConversation:
    # Ein Hauptgespraech je Benutzer (Eindeutigkeit auf user_id + kind).
    row = db.query(AiConversation).filter(AiConversation.user_id == user.id).first()
    if row is not None:
        return row
    row = AiConversation(id=str(uuid4()), user_id=user.id, server_id=server.id, title="Datenbank")
    db.add(row)
    db.flush()
    return row


def _vorschlagen(db: Session, user: User, server: Server, argumente: dict):
    return ai_proposal_service.create_proposal(
        db,
        user=user,
        conversation=_conversation(db, user, server),
        tool_name="propose_database_change",
        arguments={
            "server_id": server.id,
            "reason": "Testbegruendung",
            "expected_effect": "Testwirkung",
            **argumente,
        },
        correlation_id=str(uuid4()),
    )


def _ausfuehren(db: Session, user: User, proposal) -> dict:
    db.commit()
    _, token = ai_proposal_service.confirm_proposal(db, proposal_id=proposal.id, user=user)
    _, result = ai_proposal_service.execute_proposal(
        db, proposal_id=proposal.id, user=user, confirmation_token=token
    )
    return result


def _preview(proposal) -> dict:
    return json.loads(proposal.preview_json)


def _nur_schreiben(db: Session, user: User, server: Server) -> None:
    """Ein Benutzer mit Datenbank-Schreibrecht, aber ohne Admin."""
    role = create_role(db, f"ki-{uuid4().hex[:6]}", None, ["ai.chat.use"])
    set_user_roles(db, user, [role.id])
    for key in ("server.view", "server.databases.read", "server.databases.write"):
        db.add(ServerPermission(user_id=user.id, server_id=server.id, permission_key=key))
    db.commit()


FUNKTION = {
    "op": "create_function",
    "name": "touch_updated_at",
    "returns": "trigger",
    "language": "plpgsql",
    "body": "BEGIN NEW.updated_at := now(); RETURN NEW; END;",
}
TRIGGER = {
    "op": "create_trigger",
    "table": "kunden",
    "name": "kunden_touch",
    "timing": "before",
    "events": ["update"],
    "function_name": "touch_updated_at",
}


def test_funktion_und_trigger_entstehen_wie_im_studio(db, owner_user, tmp_path, leitung) -> None:
    server = _server(db, tmp_path)

    proposal = _vorschlagen(db, owner_user, server, {"database": "shop", "operation": FUNKTION})
    preview = _preview(proposal)
    # Beim Vorschlag genau ein Lauf: die Probe, verworfen.
    assert [lauf["rollback"] for lauf in leitung.laeufe] == [True]
    assert "CREATE OR REPLACE FUNCTION" in preview["diff"]
    assert preview["operation"] == "create_function"
    assert preview["database"] == "shop"
    assert not verlangt_klick("propose_database_change", preview)

    _ausfuehren(db, owner_user, proposal)
    assert [lauf["rollback"] for lauf in leitung.laeufe] == [True, False]
    assert leitung.laeufe[1]["sql"] == leitung.laeufe[0]["sql"]

    leitung.laeufe.clear()
    proposal = _vorschlagen(db, owner_user, server, {"operation": TRIGGER})
    _ausfuehren(db, owner_user, proposal)
    ausgefuehrt = leitung.laeufe[-1]["sql"][0]
    assert ausgefuehrt.startswith('CREATE TRIGGER "kunden_touch" BEFORE UPDATE ON "public"."kunden"')
    assert 'EXECUTE FUNCTION "public"."touch_updated_at"()' in ausgefuehrt

    # Wie an der Studio-Route: ein Audit ohne SQL, erkennbar als KI-Weg.
    audit = (
        db.query(AuditLog)
        .filter(AuditLog.action == "postgres.studio.execute", AuditLog.target_id == server.id)
        .order_by(AuditLog.id.desc())
        .first()
    )
    assert audit is not None
    assert "kunden_touch" not in (audit.details or "")
    assert '"via": "ai"' in (audit.details or "")


def test_erweiterung_im_datenbankserver_laeuft_als_admin(db, owner_user, tmp_path, leitung) -> None:
    server = _server(db, tmp_path, game_type="postgres")

    proposal = _vorschlagen(db, owner_user, server, {"operation": {"op": "create_extension", "name": "pg_trgm"}})
    _ausfuehren(db, owner_user, proposal)

    assert leitung.laeufe[-1]["sql"] == ['CREATE EXTENSION IF NOT EXISTS "pg_trgm"']
    assert leitung.laeufe[-1]["identity"] == "admin"
    assert _preview(proposal)["kind"] == "dedicated"


def test_gescheiterte_probe_ergibt_keinen_vorschlag(db, owner_user, tmp_path, leitung) -> None:
    server = _server(db, tmp_path)
    leitung.fehler = postgres_service.PostgresServiceError('function "fehlt"() does not exist')

    with pytest.raises(AiActionValidationError, match="PostgreSQL lehnt ab: .*does not exist"):
        _vorschlagen(db, owner_user, server, {"operation": TRIGGER})


def test_struktur_verlangt_admin_und_probt_nicht_ohne(db, regular_user, tmp_path, leitung) -> None:
    server = _server(db, tmp_path)
    _nur_schreiben(db, regular_user, server)

    with pytest.raises(AiActionValidationError, match="server.databases.admin"):
        _vorschlagen(db, regular_user, server, {"operation": FUNKTION})
    with pytest.raises(AiActionValidationError, match="server.databases.admin"):
        _vorschlagen(db, regular_user, server, {"sql": "SELECT 1"})
    # Weder Probe noch sonst ein Lauf: die Pruefung steht vor der Leitung.
    assert leitung.laeufe == []

    # Zeilen pflegen darf, wer schreiben darf — wie im Daten-Grid.
    proposal = _vorschlagen(
        db, regular_user, server,
        {"rows": {"action": "insert", "table": "kunden", "values": {"name": "Anna"}}},
    )
    assert _preview(proposal)["table"] == "public.kunden"


def test_datenvernichtendes_und_freies_sql_fragen_immer(db, owner_user, tmp_path, leitung) -> None:
    server = _server(db, tmp_path)

    loeschen = _preview(_vorschlagen(db, owner_user, server, {"operation": {"op": "drop_table", "name": "kunden"}}))
    assert loeschen["destructive"] is True
    assert verlangt_klick("propose_database_change", loeschen)

    zeilen = _preview(_vorschlagen(
        db, owner_user, server,
        {"rows": {"action": "delete", "table": "kunden", "keys": [{"values": {"id": 7}}]}},
    ))
    assert verlangt_klick("propose_database_change", zeilen)

    laeufe_vorher = len(leitung.laeufe)
    sql = _preview(_vorschlagen(db, owner_user, server, {"sql": "UPDATE kunden SET name = 'x'"}))
    assert verlangt_klick("propose_database_change", sql)
    # Freies SQL wird nicht geprobt: es liefe vor dem Klick.
    assert len(leitung.laeufe) == laeufe_vorher


def test_rollenpasswort_nimmt_die_ki_nicht(db, owner_user, tmp_path, leitung) -> None:
    server = _server(db, tmp_path, game_type="postgres")
    geheim = "Pw-" + "7" * 12

    with pytest.raises(AiActionValidationError, match="Rollenpasswort"):
        _vorschlagen(
            db, owner_user, server,
            {"operation": {"op": "create_role", "name": "leser", "attributes": {"login": True, "password": geheim}}},
        )
    assert leitung.laeufe == []


def test_unbekannte_operation_zeigt_den_weg_zur_liste(db, owner_user, tmp_path, leitung) -> None:
    server = _server(db, tmp_path)
    with pytest.raises(AiActionValidationError, match="view=operations"):
        _vorschlagen(db, owner_user, server, {"operation": {"op": "create_everything"}})
    with pytest.raises(AiActionValidationError, match="operation_schema name=create_trigger"):
        _vorschlagen(db, owner_user, server, {"operation": {"op": "create_trigger", "name": "x"}})


def test_lesen_nutzt_die_studio_funktionen(db, owner_user, tmp_path, leitung) -> None:
    server = _server(db, tmp_path)

    liste = ai_action_service.execute_read_tool(
        db, user=owner_user, tool_name="read_database",
        arguments={"server_id": server.id, "view": "operations"},
    )
    assert {"create_function", "create_trigger", "create_extension", "grant", "revoke"} <= set(liste["operations"])
    assert "function_name" in liste["operations"]["create_trigger"]

    schema = ai_action_service.execute_read_tool(
        db, user=owner_user, tool_name="read_database",
        arguments={"server_id": server.id, "view": "operation_schema", "name": "create_trigger"},
    )
    assert "timing" in schema["schema"]["properties"]

    ergebnis = ai_action_service.execute_read_tool(
        db, user=owner_user, tool_name="read_database",
        arguments={"server_id": server.id, "view": "extensions"},
    )
    assert ergebnis["database"] == "shop" and ergebnis["kind"] == "shared"
    assert "pg_available_extensions" in leitung.laeufe[-1]["sql"][0]


def test_parameter_lesen_verlangt_admin(db, regular_user, tmp_path, leitung) -> None:
    server = _server(db, tmp_path, game_type="postgres")
    _nur_schreiben(db, regular_user, server)

    with pytest.raises(AiActionValidationError, match="server.databases.admin"):
        ai_action_service.execute_read_tool(
            db, user=regular_user, tool_name="read_database",
            arguments={"server_id": server.id, "view": "parameters"},
        )
    assert leitung.laeufe == []


def test_nur_der_worker_schreibt() -> None:
    assert "read_database" in ai_tool_registry.GEHIRN_TOOLS
    assert "propose_database_change" not in ai_tool_registry.GEHIRN_TOOLS
    assert "propose_database_change" in ai_tool_registry.WRITE_TOOLS
    assert "propose_database_change" not in ai_tool_registry.worker_ausschluss()
