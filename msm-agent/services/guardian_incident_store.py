"""Transactional SQLite delivery queue for Guardian incidents."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid as uuid_module
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from services.guardian_state_store import (
    GuardianStateSecurityError,
    GuardianStateStore,
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _validated_uuid(value: str) -> str:
    try:
        parsed = uuid_module.UUID(str(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("incident UUID is invalid") from exc
    normalized = str(parsed)
    if str(value).lower() != normalized:
        raise ValueError("incident UUID must use canonical form")
    return normalized


class GuardianIncidentStore:
    def __init__(self, state_store: GuardianStateStore, server_id: int | str) -> None:
        self.state_store = state_store
        self.server_id = int(server_id)
        self.path = state_store.server_dir(server_id) / "guardian.db"
        self._lock = threading.Lock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        if self.path.is_symlink():
            raise GuardianStateSecurityError("Guardian incident database is a symlink")
        connection = sqlite3.connect(self.path, timeout=10.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        os.chmod(self.path, 0o600)
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS guardian_incidents (
                        uuid TEXT PRIMARY KEY,
                        server_id INTEGER NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        type TEXT NOT NULL,
                        status TEXT NOT NULL,
                        fingerprint TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        acknowledged INTEGER NOT NULL DEFAULT 0
                            CHECK (acknowledged IN (0, 1))
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS ix_guardian_incidents_delivery "
                    "ON guardian_incidents (acknowledged, created_at)"
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def upsert(
        self,
        *,
        incident_uuid: str,
        incident_type: str,
        status: str,
        fingerprint: str,
        payload: dict[str, Any],
        created_at: str | None = None,
    ) -> dict[str, Any]:
        incident_uuid = _validated_uuid(incident_uuid)
        if not incident_type or len(incident_type) > 64:
            raise ValueError("incident type is invalid")
        if not status or len(status) > 32:
            raise ValueError("incident status is invalid")
        if not fingerprint or len(fingerprint) > 256:
            raise ValueError("incident fingerprint is invalid")
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise ValueError("incident payload requires schema_version=1")
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        now = _utcnow()
        created = created_at or now

        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    "SELECT server_id, type, fingerprint, created_at FROM guardian_incidents WHERE uuid = ?",
                    (incident_uuid,),
                ).fetchone()
                if existing is not None and (
                    int(existing["server_id"]) != self.server_id
                    or existing["type"] != incident_type
                    or existing["fingerprint"] != fingerprint
                ):
                    raise ValueError("incident UUID conflicts with an existing incident")
                connection.execute(
                    """
                    INSERT INTO guardian_incidents
                        (uuid, server_id, created_at, updated_at, type, status,
                         fingerprint, payload_json, acknowledged)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
                    ON CONFLICT(uuid) DO UPDATE SET
                        updated_at = excluded.updated_at,
                        status = excluded.status,
                        payload_json = excluded.payload_json,
                        acknowledged = 0
                    """,
                    (
                        incident_uuid,
                        self.server_id,
                        created,
                        now,
                        incident_type,
                        status,
                        fingerprint,
                        encoded,
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self.get(incident_uuid) or {}

    def create(
        self,
        *,
        incident_type: str,
        status: str,
        fingerprint: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return self.upsert(
            incident_uuid=str(uuid_module.uuid4()),
            incident_type=incident_type,
            status=status,
            fingerprint=fingerprint,
            payload=payload,
        )

    def get(self, incident_uuid: str) -> dict[str, Any] | None:
        normalized = _validated_uuid(incident_uuid)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM guardian_incidents WHERE uuid = ?",
                (normalized,),
            ).fetchone()
        return self._row_to_dict(row) if row is not None else None

    def list_unacknowledged(self, *, limit: int = 1000) -> list[dict[str, Any]]:
        if limit < 1 or limit > 10_000:
            raise ValueError("incident delivery limit is invalid")
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM guardian_incidents WHERE acknowledged = 0 "
                "ORDER BY created_at, uuid LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def acknowledge(self, incident_uuids: Iterable[str]) -> list[str]:
        normalized = list(dict.fromkeys(_validated_uuid(value) for value in incident_uuids))
        if len(normalized) > 1000:
            raise ValueError("too many incident UUIDs")
        if not normalized:
            return []
        placeholders = ",".join("?" for _ in normalized)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    f"SELECT uuid FROM guardian_incidents WHERE uuid IN ({placeholders})",
                    normalized,
                ).fetchall()
                acknowledged = sorted(str(row["uuid"]) for row in existing)
                if acknowledged:
                    ack_placeholders = ",".join("?" for _ in acknowledged)
                    connection.execute(
                        f"UPDATE guardian_incidents SET acknowledged = 1, updated_at = ? "
                        f"WHERE uuid IN ({ack_placeholders})",
                        [_utcnow(), *acknowledged],
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return acknowledged

    def prune_acknowledged(self, *, keep_latest: int = 1000) -> int:
        """Bound only acknowledged history; queued incidents are never deleted."""
        if keep_latest < 0 or keep_latest > 100_000:
            raise ValueError("acknowledged retention is invalid")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    """
                    DELETE FROM guardian_incidents
                    WHERE acknowledged = 1 AND uuid NOT IN (
                        SELECT uuid FROM guardian_incidents
                        WHERE acknowledged = 1
                        ORDER BY updated_at DESC, uuid DESC
                        LIMIT ?
                    )
                    """,
                    (keep_latest,),
                )
                deleted = max(0, int(cursor.rowcount))
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return deleted

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        payload = json.loads(str(row["payload_json"]))
        return {
            "uuid": str(row["uuid"]),
            "server_id": int(row["server_id"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "type": str(row["type"]),
            "status": str(row["status"]),
            "fingerprint": str(row["fingerprint"]),
            "payload": payload,
            "acknowledged": bool(row["acknowledged"]),
        }

