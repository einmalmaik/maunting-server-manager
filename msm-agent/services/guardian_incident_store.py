"""Durable file-based delivery queue for Guardian incidents.

One owner-only JSON file per incident below ``<server_dir>/incidents/``,
written atomically (fsync + rename) through ``GuardianStateStore``. The panel
fetches unacknowledged incidents and acknowledges them; acknowledged history
is bounded by ``prune_acknowledged``.
"""

from __future__ import annotations

import logging
import threading
import uuid as uuid_module
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from services.guardian_state_store import (
    STATE_SCHEMA_VERSION,
    CorruptedGuardianStateError,
    GuardianStateSecurityError,
    GuardianStateStore,
)

logger = logging.getLogger(__name__)

#: Acknowledged incidents kept per server after each acknowledgement.
ACKNOWLEDGED_RETENTION = 1000

# Every call site builds its own store instance, so the lock that serialises a
# read-check-write cycle must belong to the directory, not to the instance.
_LOCKS: dict[Path, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _directory_lock(directory: Path) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(directory, threading.Lock())


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
        server_dir = state_store.server_dir(server_id)
        self.path = server_dir / "incidents"
        self._lock = _directory_lock(self.path)
        with self._lock:
            self._ensure_directory()
            self._drop_legacy_database(server_dir)

    def _ensure_directory(self) -> None:
        if self.path.is_symlink():
            raise GuardianStateSecurityError("Guardian incident directory is a symlink")
        self.path.mkdir(mode=0o700, exist_ok=True)
        if self.path.is_symlink() or not self.path.is_dir():
            raise GuardianStateSecurityError("Guardian incident path is not a directory")

    @staticmethod
    def _drop_legacy_database(server_dir: Path) -> None:
        """Remove the former SQLite queue; its content is not carried over."""
        for name in ("guardian.db", "guardian.db-journal"):
            legacy = server_dir / name
            if legacy.is_file() and not legacy.is_symlink():
                legacy.unlink()
                logger.warning(
                    "Guardian: legacy incident database %s removed; undelivered "
                    "incidents from before the update are not carried over",
                    legacy,
                )

    def _file(self, incident_uuid: str) -> Path:
        return self.path / f"{incident_uuid}.json"

    def _read(self, path: Path) -> dict[str, Any] | None:
        try:
            return self.state_store.read_json_file(path)
        except CorruptedGuardianStateError as exc:
            # The corrupt file was moved aside for diagnosis; one broken
            # record must not block delivery of every other incident.
            logger.error("Guardian: corrupt incident retained at %s", exc.retained_path)
            return None

    def _all(self) -> list[dict[str, Any]]:
        records = []
        for path in self.path.glob("*.json"):
            record = self._read(path)
            if record is not None:
                records.append(record)
        return records

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
        now = _utcnow()

        with self._lock:
            existing = self._read(self._file(incident_uuid))
            if existing is not None and (
                int(existing["server_id"]) != self.server_id
                or existing["type"] != incident_type
                or existing["fingerprint"] != fingerprint
            ):
                raise ValueError("incident UUID conflicts with an existing incident")
            record = {
                "schema_version": STATE_SCHEMA_VERSION,
                "uuid": incident_uuid,
                "server_id": self.server_id,
                "created_at": existing["created_at"] if existing else (created_at or now),
                "updated_at": now,
                "type": incident_type,
                "status": status,
                "fingerprint": fingerprint,
                "payload": payload,
                "acknowledged": False,
            }
            self.state_store.write_json_file(self._file(incident_uuid), record)
        return self._public(record)

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
        with self._lock:
            record = self._read(self._file(normalized))
        return self._public(record) if record is not None else None

    def list_unacknowledged(self, *, limit: int = 1000) -> list[dict[str, Any]]:
        if limit < 1 or limit > 10_000:
            raise ValueError("incident delivery limit is invalid")
        with self._lock:
            records = [record for record in self._all() if not record["acknowledged"]]
        records.sort(key=lambda record: (record["created_at"], record["uuid"]))
        return [self._public(record) for record in records[:limit]]

    def acknowledge(self, incident_uuids: Iterable[str]) -> list[str]:
        normalized = list(dict.fromkeys(_validated_uuid(value) for value in incident_uuids))
        if len(normalized) > 1000:
            raise ValueError("too many incident UUIDs")
        if not normalized:
            return []
        acknowledged = []
        with self._lock:
            now = _utcnow()
            for incident_uuid in sorted(normalized):
                record = self._read(self._file(incident_uuid))
                if record is None:
                    continue
                record["acknowledged"] = True
                record["updated_at"] = now
                self.state_store.write_json_file(self._file(incident_uuid), record)
                acknowledged.append(incident_uuid)
        if acknowledged:
            self.prune_acknowledged(keep_latest=ACKNOWLEDGED_RETENTION)
        return acknowledged

    def prune_acknowledged(self, *, keep_latest: int = ACKNOWLEDGED_RETENTION) -> int:
        """Bound only acknowledged history; queued incidents are never deleted."""
        if keep_latest < 0 or keep_latest > 100_000:
            raise ValueError("acknowledged retention is invalid")
        with self._lock:
            done = [record for record in self._all() if record["acknowledged"]]
            done.sort(key=lambda record: (record["updated_at"], record["uuid"]), reverse=True)
            surplus = done[keep_latest:]
            for record in surplus:
                self._file(record["uuid"]).unlink(missing_ok=True)
        return len(surplus)

    @staticmethod
    def _public(record: dict[str, Any]) -> dict[str, Any]:
        return {
            "uuid": str(record["uuid"]),
            "server_id": int(record["server_id"]),
            "created_at": str(record["created_at"]),
            "updated_at": str(record["updated_at"]),
            "type": str(record["type"]),
            "status": str(record["status"]),
            "fingerprint": str(record["fingerprint"]),
            "payload": record["payload"],
            "acknowledged": bool(record["acknowledged"]),
        }
