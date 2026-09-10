from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class NotificationService:
    """Zentraler Notification-Service für Messenger- und System-Benachrichtigungen.

    Implementiert strikte Outgoing Echo Prevention und Empfänger-Filterung:
    - Wenn die KI eine Nachricht im Namen des angemeldeten Benutzers versendet,
      darf für den Sender-Account KEINE Push-Benachrichtigung oder Unread-Badge getriggert werden.
    - Push/Alerts und Unread-Badges gibt es ausschließlich für den Empfänger bei
      ungesehenen Nachrichten (recipient_id == current_user && !is_read).
    """

    @staticmethod
    def is_outgoing_echo(
        *,
        sender_user_id: int | str | None,
        current_user_id: int | str | None,
    ) -> bool:
        """Prüft strikt, ob die Aktion vom aktuellen Benutzer selbst (oder in seinem Namen via KI/Worker)
        ausgelöst wurde (Outgoing Echo).
        """
        if sender_user_id is None or current_user_id is None:
            return False
        try:
            return int(sender_user_id) == int(current_user_id)
        except (ValueError, TypeError):
            pass

        s_str = str(sender_user_id).strip()
        c_str = str(current_user_id).strip()
        if not s_str or not c_str:
            return False
        if s_str == c_str:
            return True

        # Unterstützt Worker/AI/Actor-Präfixe wie "worker:42", "ai:42", "actor:42", "user:42"
        for prefix in ("worker:", "ai:", "actor:", "user:"):
            if s_str.startswith(prefix):
                sub = s_str[len(prefix) :].strip()
                try:
                    if int(sub) == int(current_user_id):
                        return True
                except (ValueError, TypeError):
                    if sub == c_str:
                        return True

        return False

    @staticmethod
    def should_notify(
        *,
        recipient_id: int | str | None,
        current_user_id: int | str | None,
        is_read: bool = False,
        sender_user_id: int | str | None = None,
        is_group: bool = False,
        is_control: bool = False,
        control_type: str | None = None,
        has_active_foreground_client: bool = False,
    ) -> bool:
        """Prüft, ob für den aktuellen Benutzer ein Push-Alert oder Unread-Badge ausgelöst werden darf.

        Strikte Invarianten:
        - `sender_user_id == current_user_id`: Absender wird niemals benachrichtigt (Outgoing Echo Prevention).
        - `is_control`: Interne Steuernachrichten (Read Receipts, Delivery Receipts, Typing) triggern keine Alerts/Badges.
        - `is_read`: Gesehene oder bereits gelesene Nachrichten triggern keinen Alert (z. B. Chat-Fokus).
        - `has_active_foreground_client`: Bei aktiver Foreground-WebSocket/SSE-Verbindung keine doppelten OS-Pushes.
        - `recipient_id == current_user_id`: Push/Alerts nur wenn der Benutzer auch wirklich der Empfänger ist.
        """
        if current_user_id is None:
            return False

        # 1. Outgoing Echo Prevention: Sender darf niemals über eigene Aktionen benachrichtigt werden
        # (gilt ausnahmslos auch für Aktionen via KI/Worker im Namen des Nutzers)
        if NotificationService.is_outgoing_echo(
            sender_user_id=sender_user_id, current_user_id=current_user_id
        ):
            return False

        try:
            cur_id = int(current_user_id)
        except (ValueError, TypeError):
            cur_id = None

        # 2. Interne Steuernachrichten (z. B. read_receipt, delivery_receipt, edit_message, delete_message)
        if is_control or (control_type and control_type not in ("message", "normal")):
            return False

        # 3. Bereits gesehene oder gelesene Nachrichten (z. B. Chatfenster aktiv im Fokus) -> kein Alert
        if is_read:
            return False

        # 4. Aktive Verbindung im Vordergrund -> OS-Pushes unterdrücken, um Doppelung zu verhindern
        if has_active_foreground_client:
            return False

        # 5. Empfängerprüfung für Direktnachrichten
        if recipient_id is not None:
            if cur_id is not None:
                try:
                    return int(recipient_id) == cur_id
                except (ValueError, TypeError):
                    return False
            return str(recipient_id).strip() == str(current_user_id).strip()

        # 6. Gruppen-Nachrichten (wenn recipient_id nicht definiert ist)
        if is_group:
            return True

        # Falls recipient_id nicht definiert ist (Legacy / Broadcast):
        # Nur benachrichtigen, wenn der Sender nicht der aktuelle Nutzer ist
        if sender_user_id is not None:
            return not NotificationService.is_outgoing_echo(
                sender_user_id=sender_user_id, current_user_id=current_user_id
            )

        return False

    @staticmethod
    def should_notify_group(
        *,
        group_member_ids: list[int | str] | set[int | str],
        current_user_id: int | str | None,
        is_read: bool = False,
        sender_user_id: int | str | None = None,
        is_control: bool = False,
        control_type: str | None = None,
        has_active_foreground_client: bool = False,
    ) -> bool:
        """Prüft Gruppen-Nachrichten: Outgoing Echo Prevention, Steuernachrichten und Gruppenmitgliedschaft."""
        if current_user_id is None:
            return False

        # 1. Outgoing Echo Prevention
        if NotificationService.is_outgoing_echo(
            sender_user_id=sender_user_id, current_user_id=current_user_id
        ):
            return False

        # 2. Steuernachrichten ausschließen
        if is_control or (control_type and control_type not in ("message", "normal")):
            return False

        # 3. Bereits gelesen / Fokus
        if is_read:
            return False

        # 4. Aktiver Foreground-Client
        if has_active_foreground_client:
            return False

        try:
            cur_id = int(current_user_id)
        except (ValueError, TypeError):
            cur_id = None

        cur_str = str(current_user_id).strip()
        for mid in group_member_ids:
            if cur_id is not None:
                try:
                    if int(mid) == cur_id:
                        return True
                except (ValueError, TypeError):
                    pass
            if str(mid).strip() == cur_str:
                return True

        return False

    @staticmethod
    def sanitize_push_payload(
        payload: dict[str, Any],
        *,
        is_e2ee: bool = True,
        privacy_mode: bool = True,
    ) -> dict[str, Any]:
        """Bereinigt Push-Payloads (WebPush / FCM) gegen Privacy-Leaks.

        Strikte Zero-Knowledge- und Datenschutz-Garantien:
        - NIEMALS Klartext-Nachrichteninhalte (body, text, message_text, preview) in Push-Payloads leaken,
          wenn E2EE oder Privatsphäre-Filter aktiv sind.
        - Sensible kryptographische Details (ciphertext_envelope, secret, key, token) werden entfernt.
        - Bei E2EE wird ein generischer, sicherer Text verwendet ('Neue verschlüsselte Nachricht').
        """
        sanitized = dict(payload)

        # Gefährliche Klartext- und Sensitiv-Felder
        leak_keys = [
            "text",
            "message_text",
            "message",
            "msg",
            "plaintext",
            "content",
            "body",
            "preview",
            "ciphertext_envelope",
            "raw_payload",
            "secret",
            "token",
            "key",
            "password",
            "attachments",
            "attachment",
            "private_key",
            "secret_key",
            "channel_key",
            "iv",
            "auth_tag",
            "salt",
        ]

        if is_e2ee or privacy_mode:
            # Jeglichen Klartext entfernen
            for k in leak_keys:
                sanitized.pop(k, None)

            # Generischen, sicheren Text garantieren
            is_group = bool(payload.get("is_group", False))
            chat_name = payload.get("chat_name") or payload.get("sender_name")
            if chat_name:
                sanitized["title"] = f"Neue Nachricht: {chat_name}" if not is_group else f"Neue Nachricht in „{chat_name}“"
            else:
                sanitized["title"] = "Neue Nachricht"

            sanitized["body"] = (
                "Neue Nachricht in der Gruppe."
                if is_group
                else "Du hast eine neue verschlüsselte Nachricht erhalten."
            )
            sanitized["is_e2ee"] = True
            sanitized["privacy_filtered"] = True
        else:
            # Auch im normalen Modus niemals kryptographische Secrets leaken
            for k in (
                "ciphertext_envelope",
                "secret",
                "token",
                "key",
                "password",
                "private_key",
                "secret_key",
                "channel_key",
                "iv",
                "auth_tag",
                "salt",
            ):
                sanitized.pop(k, None)

        return sanitized

    @staticmethod
    def prepare_push_dispatch(
        *,
        target_user_id: int | str,
        sender_user_id: int | str | None = None,
        title: str,
        body: str | None = None,
        is_e2ee: bool = True,
        privacy_mode: bool = True,
        is_control: bool = False,
        control_type: str | None = None,
        has_active_foreground_connection: bool = False,
        extra_data: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Erzeugt einen validierten, bereinigten WebPush/FCM Push-Dispatch-Auftrag.

        Liefert None zurück, wenn:
        - target_user_id == sender_user_id (Outgoing Echo Prevention)
        - is_control True ist (keine Pushes für Lesequittungen / interne Steuersignale)
        - has_active_foreground_connection True ist (keine doppelten OS-Pushes bei aktivem Client)
        """
        # 1. Outgoing Echo Prevention: Sender darf niemals benachrichtigt werden
        if NotificationService.is_outgoing_echo(
            sender_user_id=sender_user_id, current_user_id=target_user_id
        ):
            return None

        # 2. Steuersignale triggern keine OS-Pushes
        if is_control or (control_type and control_type not in ("message", "normal")):
            return None

        # 3. Vordergrund-Prüfung: Aktive Verbindung unterdrückt OS-Push zur Vermeidung von Doppelung
        if has_active_foreground_connection:
            return None

        raw_payload = {
            "title": title,
            "body": body or "Neue Benachrichtigung",
            "target_user_id": target_user_id,
            "sender_user_id": sender_user_id,
            **(extra_data or {}),
        }

        return NotificationService.sanitize_push_payload(
            raw_payload, is_e2ee=is_e2ee, privacy_mode=privacy_mode
        )

    @staticmethod
    def dispatch_push(
        *,
        target_user_id: int | str,
        sender_user_id: int | str | None = None,
        title: str,
        body: str | None = None,
        is_e2ee: bool = True,
        privacy_mode: bool = True,
        is_control: bool = False,
        control_type: str | None = None,
        has_active_foreground_connection: bool = False,
        extra_data: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Zentraler Push-Dispatcher: Filtert, bereinigt und plant WebPush/FCM-Payloads."""
        payload = NotificationService.prepare_push_dispatch(
            target_user_id=target_user_id,
            sender_user_id=sender_user_id,
            title=title,
            body=body,
            is_e2ee=is_e2ee,
            privacy_mode=privacy_mode,
            is_control=is_control,
            control_type=control_type,
            has_active_foreground_connection=has_active_foreground_connection,
            extra_data=extra_data,
        )
        if payload is None:
            return None
        logger.debug("Push-Dispatch vorbereitet für User %s: %s", target_user_id, payload.get("title"))
        return payload

