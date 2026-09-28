"""Studio-Sicherung: Dump/Restore einer Datenbank über den Agenten."""

from __future__ import annotations

import base64
import hashlib
from unittest.mock import MagicMock, patch

from models import AuditLog
from services import postgres_service
from tests.test_postgres_instances import ADMIN_PW, OWNER_PW, db_server  # noqa: F401  (Fixture)


def _url(server, path: str) -> str:
    return f"/api/servers/{server.id}/databases/{server.postgres_databases[0].id}/studio{path}"


def _agent(**methods):
    client = MagicMock()
    for name, value in methods.items():
        getattr(client, name).return_value = value
    return client


def test_dump_geht_an_die_eigene_instanz_und_traegt_die_pruefsumme(client, owner_cookies, csrf_token, db, db_server):
    data = b"PGDMP\x00\xff\xfe"
    agent = _agent(postgres_dump_db={"data_b64": base64.b64encode(data).decode(), "size_bytes": len(data)})
    with patch.object(postgres_service, "_client_for_server_id", return_value=agent):
        response = client.post(
            _url(db_server, "/backup/dump"),
            json={"format": "custom", "tables": [{"schema_name": "public", "name": "kunden"}]},
            cookies=owner_cookies, headers={"X-CSRF-Token": csrf_token or ""},
        )
    assert response.status_code == 200, response.text
    assert response.content == data
    assert response.headers["x-msm-dump-sha256"] == hashlib.sha256(data).hexdigest()
    assert response.headers["content-disposition"].endswith('.dump"')
    payload = agent.postgres_dump_db.call_args.args[0]
    assert payload["target"]["container"] == f"msm-srv-{db_server.id}"
    assert payload["admin_password"] == ADMIN_PW
    assert payload["tables"] == [{"schema_name": "public", "name": "kunden"}]
    audit = db.query(AuditLog).filter(AuditLog.action == "postgres.studio.dump").one()
    assert ADMIN_PW not in (audit.details or "")


def test_restore_braucht_den_datenbanknamen_und_laeuft_als_owner(client, owner_cookies, csrf_token, db_server):
    agent = _agent(postgres_restore_db={"ok": True, "database": "app", "duration_ms": 3})
    body = {"format": "plain", "data_b64": base64.b64encode(b"SELECT 1;").decode(), "confirm_name": "falsch"}
    headers = {"X-CSRF-Token": csrf_token or ""}
    with patch.object(postgres_service, "_client_for_server_id", return_value=agent):
        falsch = client.post(_url(db_server, "/backup/restore"), json=body, cookies=owner_cookies, headers=headers)
        assert falsch.status_code == 400
        agent.postgres_restore_db.assert_not_called()
        body["confirm_name"] = "app"
        ok = client.post(_url(db_server, "/backup/restore"), json=body, cookies=owner_cookies, headers=headers)
    assert ok.status_code == 200, ok.text
    payload = agent.postgres_restore_db.call_args.args[0]
    assert payload["owner_role"] == "app_owner" and payload["owner_password"] == OWNER_PW
    assert "admin_password" not in payload
    assert payload["target"]["port"] == 25432


def test_liegengebliebene_dumps_nur_der_eigenen_datenbank(client, owner_cookies, csrf_token, db_server):
    agent = _agent(postgres_pending=[{"database": "app", "size_bytes": 10}, {"database": "andere", "size_bytes": 5}],
                   postgres_restore_db={"ok": True})
    with patch.object(postgres_service, "_client_for_server_id", return_value=agent):
        listed = client.get(_url(db_server, "/backup/pending"), cookies=owner_cookies)
        applied = client.post(_url(db_server, "/backup/pending/apply"), cookies=owner_cookies,
                              headers={"X-CSRF-Token": csrf_token or ""})
    assert [row["database"] for row in listed.json()] == ["app"]
    assert applied.status_code == 200
    assert agent.postgres_restore_db.call_args.args[0]["pending_server_id"] == db_server.id


def test_veralteter_agent_bekommt_einen_klaren_hinweis(client, owner_cookies, csrf_token, db_server):
    from services.node_client import NodeClientError

    agent = MagicMock()
    agent.postgres_dump_db.side_effect = NodeClientError("Not Found", status_code=404)
    with patch.object(postgres_service, "_client_for_server_id", return_value=agent):
        response = client.post(_url(db_server, "/backup/dump"), json={}, cookies=owner_cookies,
                               headers={"X-CSRF-Token": csrf_token or ""})
    assert response.status_code == 503
    assert "Agent aktualisieren" in response.json()["detail"]
