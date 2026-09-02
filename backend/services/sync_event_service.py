"""Zentraler Vermittler für Server-Sent-Events (SSE) Synchronisations-Ereignisse.

Verwaltet aktive Client-Abonnements (z. B. Web-Browser, Desktop-App, APK)
und streamt autorisierte Änderungs-Signale für Notizen und Kalender in Echtzeit.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
from typing import Any
import uuid

_log = logging.getLogger("msm.sync_events")

# Maximale Anzahl an gepufferten Ereignissen je Client-Warteschlange
MAX_QUEUE_SIZE = 128


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class _Subscriber:
    __slots__ = ("conn_id", "user_id", "team_ids", "is_admin", "queue", "loop", "created_at")

    def __init__(
        self,
        conn_id: str,
        user_id: int,
        team_ids: set[int],
        is_admin: bool,
        queue: asyncio.Queue[dict[str, Any]],
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self.conn_id = conn_id
        self.user_id = user_id
        self.team_ids = team_ids
        self.is_admin = is_admin
        self.queue = queue
        self.loop = loop
        self.created_at = _iso_now()


class SyncEventService:
    """Verwaltet SSE-Abonnenten und verteilt Sync-Signale in Echtzeit."""

    _subscribers: dict[str, _Subscriber] = {}

    @classmethod
    def subscribe(
        cls,
        user_id: int,
        team_ids: list[int] | set[int] | None = None,
        is_admin: bool = False,
    ) -> tuple[str, asyncio.Queue[dict[str, Any]]]:
        """Registriert einen neuen SSE-Client und gibt eine Event-Queue zurück."""
        conn_id = f"conn-{uuid.uuid4().hex[:12]}"
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=MAX_QUEUE_SIZE)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        sub = _Subscriber(
            conn_id=conn_id,
            user_id=user_id,
            team_ids=set(team_ids or []),
            is_admin=is_admin,
            queue=queue,
            loop=loop,
        )
        cls._subscribers[conn_id] = sub
        _log.debug("SSE-Client verbunden: %s (User %d, Teams %s)", conn_id, user_id, sub.team_ids)
        return conn_id, queue

    @classmethod
    def unsubscribe(cls, conn_id: str) -> None:
        """Entfernt einen SSE-Client nach Verbindungsabbruch."""
        if conn_id in cls._subscribers:
            cls._subscribers.pop(conn_id, None)
            _log.debug("SSE-Client getrennt: %s", conn_id)

    @classmethod
    def publish(
        cls,
        event_data: dict[str, Any],
        *,
        user_id: int | None = None,
        team_id: int | None = None,
        exclude_conn_id: str | None = None,
    ) -> int:
        """Verteilt ein Ereignis an alle berechtigten aktiven Abonnenten.

        Gibt die Anzahl der erreichten Empfänger zurück.
        """
        payload = dict(event_data)
        if "timestamp" not in payload:
            payload["timestamp"] = _iso_now()

        delivered = 0
        for conn_id, sub in list(cls._subscribers.items()):
            if exclude_conn_id and conn_id == exclude_conn_id:
                continue

            # Berechtigungsprüfung für das Ereignis
            is_recipient = False

            if user_id is not None and sub.user_id == user_id:
                is_recipient = True
            elif team_id is not None and team_id in sub.team_ids:
                is_recipient = True
            elif user_id is None and team_id is None:
                # Systemweites Signal
                is_recipient = True

            if is_recipient:
                try:
                    def _enqueue(s: _Subscriber, p: dict[str, Any]):
                        if s.queue.full():
                            try:
                                s.queue.get_nowait()
                            except (asyncio.QueueEmpty, Exception):
                                pass
                        try:
                            s.queue.put_nowait(p)
                        except Exception:
                            pass

                    try:
                        current_loop = asyncio.get_running_loop()
                    except RuntimeError:
                        current_loop = None

                    if sub.loop and sub.loop.is_running() and current_loop is not sub.loop:
                        sub.loop.call_soon_threadsafe(_enqueue, sub, payload)
                    else:
                        _enqueue(sub, payload)
                    delivered += 1
                except Exception as e:
                    _log.warning("Fehler beim Senden des SSE-Signals an %s: %s", conn_id, e)

        return delivered

    @classmethod
    def format_sse(cls, event: str, data: dict[str, Any] | str) -> str:
        """Formatiert ein Ereignis nach dem Server-Sent-Events Standard."""
        if isinstance(data, (dict, list)):
            data_str = json.dumps(data, ensure_ascii=False)
        else:
            data_str = str(data)
        return f"event: {event}\ndata: {data_str}\n\n"

    @classmethod
    def ping_sse(cls) -> str:
        """Liefert ein Keepalive-Signal für SSE."""
        return ": ping\n\n"

    @classmethod
    def get_subscriber_count(cls) -> int:
        """Liefert die Anzahl aktuell verbundener SSE-Clients."""
        return len(cls._subscribers)

    @classmethod
    def clear_all_for_testing(cls) -> None:
        """Setzt den Abonnenten-Speicher zurück (für automatisierte Tests)."""
        cls._subscribers.clear()
