"""Zentraler Vermittler für Server-Sent-Events (SSE) Synchronisations-Ereignisse.

Verwaltet aktive Client-Abonnements (z. B. Web-Browser, Desktop-App, APK)
und streamt autorisierte Änderungs-Signale für Notizen und Kalender in Echtzeit.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
import time
from typing import Any
import uuid

_log = logging.getLogger("msm.sync_events")

# Maximale Anzahl an gepufferten Ereignissen je Client-Warteschlange
MAX_QUEUE_SIZE = 128


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


#: Wie viele Mailboxen ein Client höchstens abonnieren darf.
#:
#: Ein Mensch hat Gespräche, keine Sammlung — 200 ist grosszügig und zugleich
#: die Grenze, ab der ein Abo nur noch Speicher belegt.
MAX_MAILBOXES = 200


class _Subscriber:
    __slots__ = (
        "conn_id", "user_id", "team_ids", "is_admin", "queue", "loop", "created_at",
        "mailboxes", "familie", "zustand", "zuletzt_gesehen",
    )

    def __init__(
        self,
        conn_id: str,
        user_id: int,
        team_ids: set[int],
        is_admin: bool,
        queue: asyncio.Queue[dict[str, Any]],
        loop: asyncio.AbstractEventLoop | None = None,
        familie: str | None = None,
    ) -> None:
        self.conn_id = conn_id
        #: Die Refresh-Familie des Tokens, mit dem die Verbindung aufgebaut
        #: wurde. Daran findet `trenne` die Verbindungen eines gesperrten
        #: Geraets.
        self.familie = familie
        self.user_id = user_id
        self.team_ids = team_ids
        self.is_admin = is_admin
        self.queue = queue
        self.loop = loop
        self.created_at = _iso_now()
        #: Die Mailboxen, über die dieser Client Bescheid wissen will.
        #:
        #: Der Weg für Mailboxen, die der Server nicht ausrechnen kann. Bis
        #: 09/2026 ging jede Zustellung über `user_id`, und das setzte voraus,
        #: dass der Server aus der Mailbox den Empfänger ableiten kann — also
        #: genau das Wissen, das ihm genommen werden soll.
        #:
        #: Was hier steht, ist bereits geprüft: der WebSocket lässt eine
        #: Kennung nur herein, wenn das Konto zur Mailbox gehört **oder** ein
        #: gültiger Besitznachweis vorlag. Diese Klasse prüft nicht nach.
        self.mailboxes: set[str] = set()
        #: `vordergrund`, `hintergrund` oder `unbekannt`.
        #:
        #: Eine Meldung des Betriebssystems soll **einmal** kommen. Welcher
        #: Client sie zeigt, entscheidet deshalb der Server und nicht die Zahl
        #: der offenen Fenster: wer im Vordergrund steht, zeigt selbst; ein
        #: Hintergrunddienst zeigt selbst; erst wenn keiner das kann, geht ein
        #: Push hinaus (`webpush_service`).
        #:
        #: `unbekannt` heisst: dieser Client hat noch nichts gemeldet — ältere
        #: Fassungen tun das nie. Für sie gilt das alte Verhalten (jede offene
        #: Verbindung unterdrückt den Push), damit ein Update niemanden stumm
        #: macht.
        self.zustand: str = "unbekannt"
        #: Nur zum Vergleichen: welcher Vordergrund-Client war zuletzt wach.
        self.zuletzt_gesehen: float = 0.0


class SyncEventService:
    """Verwaltet SSE-Abonnenten und verteilt Sync-Signale in Echtzeit."""

    _subscribers: dict[str, _Subscriber] = {}

    @classmethod
    def subscribe(
        cls,
        user_id: int,
        team_ids: list[int] | set[int] | None = None,
        is_admin: bool = False,
        familie: str | None = None,
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
            familie=familie,
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
    def set_mailboxes(
        cls, conn_id: str, mailboxes: set[str] | list[str], *, user_id: int
    ) -> int:
        """Setzt die Mailboxen, über die dieser Client Bescheid wissen will.

        Ersetzt die bisherige Liste vollständig — der Client sagt jedesmal, was
        ihn **jetzt** interessiert. Das ist wichtiger als es klingt: eine
        verlassene Gruppe muss sich abbestellen lassen, und ein Anhängen ohne
        Abbestellen wüchse bis zum Deckel.

        Ob die Kennungen selbst erlaubt sind, ist an dieser Stelle schon
        geprüft (siehe `_Subscriber.mailboxes`). Was **hier** geprüft wird, ist
        etwas anderes: dass die Verbindung dem Konto gehört, das sie ändern
        will. Die `conn_id` steht im `ready`-Signal und reist damit durch den
        Browser; ohne diese Zeile könnte ein fremdes Konto die Abos einer
        anderen Verbindung setzen — und sich so in deren Mailboxen einhängen.

        `user_id` ist ein Pflichtargument und benannt, damit es beim Aufruf
        sichtbar ist und nicht versehentlich an die Kennungsliste rutscht.

        Gibt zurück, wie viele Kennungen jetzt hinterlegt sind.
        """
        sub = cls._subscribers.get(conn_id)
        if sub is None or sub.user_id != user_id:
            return 0
        sauber = {m.strip().lower() for m in mailboxes if isinstance(m, str) and m.strip()}
        # Sortiert gekappt, nicht zufällig: ein Client, der über dem Deckel
        # liegt, soll bei jeder Verbindung dieselben Mailboxen bekommen und
        # nicht mal die einen, mal die anderen.
        if len(sauber) > MAX_MAILBOXES:
            sauber = set(sorted(sauber)[:MAX_MAILBOXES])
        sub.mailboxes = sauber
        return len(sauber)

    @classmethod
    def set_praesenz(cls, conn_id: str, zustand: str, *, user_id: int) -> bool:
        """Meldet, ob dieser Client gerade vorne oder hinten steht.

        Dieselbe Absicherung wie `set_mailboxes`: die `conn_id` reist durch den
        Client, also darf sie nur der ändern, dem die Verbindung gehört.

        Was hier ankommt, ist eine Angabe des Clients über sich selbst — kein
        Nachweis. Sie entscheidet nur, **wer** eine Meldung zeigt, nicht ob
        jemand sie zeigen darf; das prüft der Aufrufer vorher.
        """
        sub = cls._subscribers.get(conn_id)
        if sub is None or sub.user_id != user_id:
            return False
        sauber = (zustand or "").strip().lower()
        if sauber not in ("vordergrund", "hintergrund"):
            return False
        sub.zustand = sauber
        # Nanosekunden: zwei Fenster melden sich im selben Millisekundentakt,
        # und bei Gleichstand entschiede sonst die Reihenfolge im Dictionary
        # statt „wer zuletzt wach war".
        sub.zuletzt_gesehen = time.monotonic_ns()
        return True

    @classmethod
    def zusteller_je_mailbox(cls, mailbox_id: str) -> tuple[set[str], bool]:
        """Dasselbe fuer eine Mailbox — **ein** Zusteller je Konto, nicht je Mailbox.

        Der Unterschied ist wichtig: an einer Gruppenmailbox haengen mehrere
        Menschen. Wuerde hier einer fuer alle gewaehlt, schwiege der Rest der
        Gruppe. Gewaehlt wird deshalb je Konto einer, und die anderen Clients
        desselben Kontos bleiben still.

        Gibt ``(Menge der conn_ids, praesenz_bekannt)`` zurueck.
        """
        kennung = (mailbox_id or "").strip().lower()
        if not kennung:
            return set(), False
        nach_konto: dict[int, list[_Subscriber]] = {}
        for sub in cls._subscribers.values():
            if kennung in sub.mailboxes:
                nach_konto.setdefault(sub.user_id, []).append(sub)
        if not nach_konto:
            return set(), False

        bekannt = any(
            sub.zustand != "unbekannt"
            for gruppe in nach_konto.values()
            for sub in gruppe
        )
        gewaehlt: set[str] = set()
        for gruppe in nach_konto.values():
            for stufe in ("vordergrund", "hintergrund"):
                passend = [sub for sub in gruppe if sub.zustand == stufe]
                if passend:
                    gewaehlt.add(max(passend, key=lambda sub: sub.zuletzt_gesehen).conn_id)
                    break
        return gewaehlt, bekannt

    @classmethod
    def zustelllage(
        cls,
        *,
        user_id: int | None = None,
        mailbox_id: str | None = None,
    ) -> tuple[str | None, bool]:
        """Wer zeigt die Meldung — und weiß überhaupt jemand, wo er steht?

        Gibt ``(conn_id des Zustellers, praesenz_bekannt)`` zurück.

        Der Zusteller ist **einer**: erst der zuletzt aktive Vordergrund-Client,
        sonst der zuletzt aktive Hintergrund-Client, sonst niemand. Zwei offene
        Fenster oder zwei Apps auf demselben Gerät melden dieselbe Nachricht
        damit nicht doppelt.

        `praesenz_bekannt` ist der Schalter für das Altverhalten: solange kein
        Client etwas gemeldet hat (ältere Fassung), gilt weiter „jede offene
        Verbindung unterdrückt den Push". Ohne das verstummte nach einem
        Update jeder, der eine alte App offen hat.
        """
        kennung = (mailbox_id or "").strip().lower() or None
        kandidaten = [
            sub for sub in cls._subscribers.values()
            if (user_id is not None and sub.user_id == int(user_id))
            or (kennung is not None and kennung in sub.mailboxes)
        ]
        if not kandidaten:
            return None, False

        bekannt = any(sub.zustand != "unbekannt" for sub in kandidaten)
        for stufe in ("vordergrund", "hintergrund"):
            passend = [sub for sub in kandidaten if sub.zustand == stufe]
            if passend:
                return max(passend, key=lambda sub: sub.zuletzt_gesehen).conn_id, True
        return None, bekannt

    @classmethod
    def trenne(cls, user_id: int, familie: str | None = None) -> int:
        """Beendet die offenen Verbindungen einer gesperrten Sitzung.

        Bis 09/2026 sperrte das Entfernen eines Geraets nur neue Anfragen. Ein
        schon offener Strom lief weiter, denn das Token wird nur beim
        Verbindungsaufbau geprueft: ein gestohlenes Geraet las mit, bis es
        selbst auflegte.

        Mit ``familie`` trifft es genau dieses Geraet, ohne alle Verbindungen
        des Kontos. Eine Verbindung, deren Token keine Familie trug, laesst
        sich keinem Geraet zuordnen und bleibt dann stehen. Ohne ``familie``
        faellt jede Verbindung des Kontos; der Weg beim Sperren aller
        Sitzungen. Legitime Clients bauen danach mit ihrem gueltigen Token
        neu auf.

        Die Verbindung wird sofort aus der Verteilung genommen und bekommt
        das Signal zum Schliessen. Gibt die Zahl der getroffenen Verbindungen
        zurueck.
        """
        payload = {"type": "shutdown", "reason": "session_revoked", "timestamp": _iso_now()}
        getroffen = [
            sub for sub in cls._subscribers.values()
            if sub.user_id == user_id and (familie is None or sub.familie == familie)
        ]
        for sub in getroffen:
            cls._subscribers.pop(sub.conn_id, None)
            cls._signal(sub, payload)
        if getroffen:
            _log.info("SyncEventService: %d Verbindung(en) nach Sperre getrennt.", len(getroffen))
        return len(getroffen)

    @staticmethod
    def _signal(sub: _Subscriber, payload: dict[str, Any]) -> None:
        """Legt ein Signal in die Queue eines Abonnenten, auch aus fremden Threads."""
        def _enqueue(s: _Subscriber, p: dict[str, Any]):
            if s.queue.full():
                try:
                    s.queue.get_nowait()
                except Exception:
                    pass
            try:
                s.queue.put_nowait(p)
            except Exception:
                pass

        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        try:
            if sub.loop and sub.loop.is_running() and current_loop is not sub.loop:
                sub.loop.call_soon_threadsafe(_enqueue, sub, payload)
            else:
                _enqueue(sub, payload)
        except Exception:
            pass

    @classmethod
    def close_all(cls) -> None:
        """Informiert alle Abonnenten geordnet über das Herunterfahren des Backends.

        Sendet ein Shutdown-Signal in alle Queues, damit SSE- und WebSocket-Handler
        die Verbindung unverzüglich schließen, statt den Uvicorn-Shutdown zu blockieren.
        """
        payload = {"type": "shutdown", "reason": "server_restart", "timestamp": _iso_now()}
        for conn_id, sub in list(cls._subscribers.items()):
            try:
                def _enqueue(s: _Subscriber, p: dict[str, Any]):
                    if s.queue.full():
                        try:
                            s.queue.get_nowait()
                        except Exception:
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
            except Exception:
                pass
        _log.info("SyncEventService: Shutdown-Signal an %d Abonnent(en) gesendet.", len(cls._subscribers))

    @classmethod
    def publish(
        cls,
        event_data: dict[str, Any],
        *,
        user_id: int | None = None,
        team_id: int | None = None,
        mailbox_id: str | None = None,
        exclude_conn_id: str | None = None,
        zusteller: str | set[str] | None = None,
        melden_markieren: bool = False,
    ) -> int:
        """Verteilt ein Ereignis an alle berechtigten aktiven Abonnenten.

        Drei Wege zu einem Empfänger, und sie schliessen einander nicht aus:
        sein Konto, eines seiner Teams, oder eine Mailbox, die er abonniert
        hat. Der dritte ist der, der ohne Kontobezug auskommt — für Mailboxen,
        deren Kennung der Server nicht ausrechnen kann.

        Ein Aufruf mit **allen** Angaben nichts ist dasselbe wie ein
        systemweites Signal; das war schon so und bleibt so.

        Gibt die Anzahl der erreichten Empfänger zurück.

        `melden_markieren` setzt an **genau einem** Empfänger `melden: true` —
        dem, den `zustelllage` gewählt hat. Alle anderen bekommen `melden:
        false` und zeigen nichts von sich aus; der Server weiß dann, dass der
        Push draußen bleiben darf, weil die Meldung schon jemand zeigt.
        """
        ziel_mailbox = mailbox_id.strip().lower() if mailbox_id else None
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
            elif ziel_mailbox is not None and ziel_mailbox in sub.mailboxes:
                is_recipient = True
            elif user_id is None and team_id is None and ziel_mailbox is None:
                # Systemweites Signal
                is_recipient = True

            if is_recipient:
                # Eine Kopie je Empfänger nur, wenn die Marke gesetzt wird —
                # sonst teilen sich alle dasselbe Wörterbuch wie bisher.
                eigene = payload
                if melden_markieren:
                    eigene = dict(payload)
                    gewaehlt = (
                        {zusteller} if isinstance(zusteller, str)
                        else set(zusteller or ())
                    )
                    eigene["melden"] = conn_id in gewaehlt
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
                        sub.loop.call_soon_threadsafe(_enqueue, sub, eigene)
                    else:
                        _enqueue(sub, eigene)
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
    def has_active_subscribers(cls, user_id: int | str | None) -> bool:
        """Prüft, ob für den Benutzer aktuell mindestens ein aktiver SSE-Client verbunden ist."""
        if user_id is None:
            return False
        try:
            uid = int(user_id)
        except (ValueError, TypeError):
            return False
        return any(sub.user_id == uid for sub in cls._subscribers.values())

    @classmethod
    def clear_all_for_testing(cls) -> None:
        """Setzt den Abonnenten-Speicher zurück (für automatisierte Tests)."""
        cls._subscribers.clear()

