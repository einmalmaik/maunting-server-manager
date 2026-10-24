from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, WebSocket
from starlette.websockets import WebSocketDisconnect

from database import SessionLocal
from dependencies import (
    get_current_user_for_ws,
    ws_session_familie,
    ws_subprotokoll,
)
from services.social_service import SocialService
from services.sync_event_service import MAX_MAILBOXES, SyncEventService

logger = logging.getLogger(__name__)

router = APIRouter()



@router.websocket("/ws")
async def social_websocket(
    websocket: WebSocket,
) -> None:
    """Echtzeit-WebSocket für Social Presence, Messaging und Live-Benachrichtigungen.

    Stabilitäts-Invariante:
    - Verhindert DB-Session-Leaks & Pool-Erschöpfung: DB-Sitzungen werden nur bedarfsgerecht
      je Aktion kurzzeitig geöffnet und direkt freigegeben.
    - Robuste Nebenläufigkeit: Sende- und Empfangs-Loops sind gekoppelt; bricht eine Seite
      ab, wird die andere unmittelbar gecancelt und alle Ressourcen freigegeben.
    - Ghost-Mode Schutz: 'invisible' und 'private' Status lecken niemals über Join-Events
      an Nicht-Freunde.
    - Nachrichten-Deduplizierung: Client-UUIDs werden bei Relays verarbeitet und quittiert.
    """
    with SessionLocal() as db:
        try:
            user = get_current_user_for_ws(websocket, db)
            user_id = user.id
            user_username = user.username
        except Exception:
            await websocket.close(code=1008)
            return

    subprotocol = ws_subprotokoll(websocket)
    await websocket.accept(subprotocol=subprotocol)

    conn_id, queue = SyncEventService.subscribe(
        user_id=user_id, familie=ws_session_familie(websocket)
    )
    ws_lock = asyncio.Lock()

    async def _send_loop():
        try:
            while True:
                event = await queue.get()
                if event.get("type") == "shutdown":
                    try:
                        async with ws_lock:
                            await websocket.send_json(event)
                            await websocket.close(code=1001, reason="Server restart")
                    except Exception:
                        pass
                    break
                async with ws_lock:
                    await websocket.send_json(event)
        except (asyncio.CancelledError, WebSocketDisconnect):
            raise
        except Exception:
            pass

    async def _mit_sitzung(arbeit):
        """Datenbankarbeit im Thread, nie auf der Ereignisschleife.

        Bis 27.09.2026 lief sie hier direkt. Die Mailbox-Pruefung rechnet je
        aktivem Konto zwei SHA-256, und ein Abo mit 200 Mailboxen hielt so bei
        jedem Neuverbinden das ganze Panel an: Stimme, Konsole, alle Stroeme.
        """

        def _lauf():
            with SessionLocal() as db:
                return arbeit(db)

        return await asyncio.to_thread(_lauf)

    async def _recv_loop():
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")
            if msg_type == "ping":
                # Heartbeat Pong - strikt isoliert, keine Präsenz-Leaks an Dritte
                async with ws_lock:
                    await websocket.send_json({"type": "pong"})
            elif msg_type == "join":
                # WebSocket Join Event:
                # Ghost-Mode & Privacy-Schutz delegiert an SocialService
                try:
                    await _mit_sitzung(
                        lambda db: SocialService.broadcast_user_joined(db, user_id, user_username)
                    )
                    async with ws_lock:
                        await websocket.send_json({"type": "joined", "status": "ok", "user_id": user_id})
                except Exception as exc:
                    logger.debug("Fehler bei broadcast_user_joined: %s", exc)
            elif msg_type == "mailboxes":
                # Der Client sagt, über welche Mailboxen er Bescheid wissen
                # will. Der Weg für Kennungen, die der Server nicht ausrechnen
                # kann — bei ihnen gibt es keinen Empfänger nachzuschlagen.
                #
                # Geprüft wird in `SocialService.erlaubte_mailboxen` — dieselbe
                # Prüfung wie auf dem SSE-Weg und bei der Push-Adresse. Ohne
                # sie wäre das Abo eine Verkehrsanalyse für jedermann:
                # abonnieren und zusehen, wann es sich regt.
                eintraege = data.get("eintraege")
                erlaubt: list[str] = []
                if isinstance(eintraege, list):
                    wunsch = [
                        (str(e.get("mailbox_id") or ""), e.get("mailbox_token"))
                        for e in eintraege[:MAX_MAILBOXES]
                        if isinstance(e, dict)
                    ]
                    try:
                        erlaubt = await _mit_sitzung(
                            lambda db: SocialService.erlaubte_mailboxen(db, user_id, wunsch)
                        )
                    except Exception as exc:
                        logger.debug("Fehler beim Setzen der Mailbox-Abos: %s", exc)
                anzahl = SyncEventService.set_mailboxes(conn_id, erlaubt, user_id=user_id)
                async with ws_lock:
                    await websocket.send_json({"type": "mailboxes_ok", "count": anzahl})
            elif msg_type == "presence":
                try:
                    await _mit_sitzung(lambda db: SocialService.update_presence(db, user_id, data))
                except Exception as exc:
                    logger.debug("Fehler bei update_presence: %s", exc)
            elif msg_type == "typing":
                blind_mailbox_id = data.get("blind_mailbox_id", "")
                status = data.get("status", "idle")
                def _tippen(db, blind_mailbox_id=blind_mailbox_id, status=status, data=data):
                    # Dieselbe Prüfung wie auf dem HTTP-Weg. Ohne sie wäre
                    # der WebSocket die offene Hintertür neben der
                    # verschlossenen Vordertür.
                    SocialService.assert_mailbox_zugang(
                        db, user_id, blind_mailbox_id, data.get("mailbox_token")
                    )
                    SocialService.broadcast_typing_signal(
                        blind_mailbox_id=blind_mailbox_id,
                        status=status,
                        sender_id=user_id,
                        sender_username=user_username,
                        db=db,
                    )

                try:
                    await _mit_sitzung(_tippen)
                except Exception as exc:
                    logger.debug("Fehler bei broadcast_typing_signal: %s", exc)
            elif msg_type == "relay":
                blind_mailbox_id = data.get("blind_mailbox_id", "")
                ciphertext_envelope = data.get("ciphertext_envelope", "")
                is_control = bool(data.get("is_control", False))
                control_type = data.get("control_type")
                client_uuid = data.get("client_uuid")
                def _weiterleiten(
                    db,
                    blind_mailbox_id=blind_mailbox_id,
                    ciphertext_envelope=ciphertext_envelope,
                    client_uuid=client_uuid,
                    is_control=is_control,
                    control_type=control_type,
                    data=data,
                ):
                    SocialService.assert_mailbox_token(
                        db, blind_mailbox_id, data.get("mailbox_token"), user_id
                    )
                    return SocialService.relay_blind_envelope(
                        db,
                        blind_mailbox_id=blind_mailbox_id,
                        ciphertext_envelope=ciphertext_envelope,
                        sender_user_id=user_id,
                        client_uuid=client_uuid,
                        is_control=is_control,
                        control_type=control_type,
                        mailbox_token=data.get("mailbox_token"),
                    )

                try:
                    envelope = await _mit_sitzung(_weiterleiten)
                    # Sofortige Bestätigung an den WebSocket-Sender (Acknowledge zur Queue-Bereinigung)
                    async with ws_lock:
                        await websocket.send_json({
                            "type": "relay_ack",
                            "client_uuid": envelope.client_uuid or client_uuid,
                            "id": envelope.id,
                            "blind_mailbox_id": envelope.blind_mailbox_id,
                            "created_at": envelope.created_at.isoformat() if hasattr(envelope.created_at, "isoformat") else str(envelope.created_at),
                        })
                except HTTPException as he:
                    logger.debug("HTTPException bei WebSocket Relay für User %s: %s", user_id, he.detail)
                    async with ws_lock:
                        await websocket.send_json({
                            "type": "error",
                            "error": "relay_failed",
                            "status_code": he.status_code,
                            "detail": he.detail,
                            "client_uuid": client_uuid,
                        })
                except Exception as exc:
                    logger.warning("Unerwarteter Fehler bei WebSocket Relay für User %s: %s", user_id, exc)
                    async with ws_lock:
                        await websocket.send_json({
                            "type": "error",
                            "error": "relay_failed",
                            "status_code": 500,
                            "detail": "Interner Fehler beim Verarbeiten der Nachricht.",
                            "client_uuid": client_uuid,
                        })

    send_task = asyncio.create_task(_send_loop())
    recv_task = asyncio.create_task(_recv_loop())

    try:
        # Sobald eine Task endet (z.B. Disconnect beim Lesen oder Schreiben),
        # wird die andere Task sauber gecancelt.
        done, pending = await asyncio.wait(
            [send_task, recv_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug("Social WebSocket getrennt: %s", e)
    finally:
        send_task.cancel()
        recv_task.cancel()
        await asyncio.gather(send_task, recv_task, return_exceptions=True)
        SyncEventService.unsubscribe(conn_id)
