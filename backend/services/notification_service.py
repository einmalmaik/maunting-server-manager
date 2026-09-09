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
    def should_notify(
        *,
        recipient_id: int | str | None,
        current_user_id: int | str | None,
        is_read: bool = False,
        sender_user_id: int | str | None = None,
        is_group: bool = False,
    ) -> bool:
        """Prüft, ob für den aktuellen Benutzer ein Push-Alert oder Unread-Badge ausgelöst werden darf.

        Strikte Invarianten:
        - `sender_user_id == current_user_id`: Absender wird niemals benachrichtigt (Outgoing Echo Prevention).
        - `is_read`: Gesehene oder bereits gelesene Nachrichten triggern keinen Alert.
        - `recipient_id == current_user_id`: Push/Alerts nur wenn der Benutzer auch wirklich der Empfänger ist.
        """
        if current_user_id is None:
            return False

        try:
            cur_id = int(current_user_id)
        except (ValueError, TypeError):
            return False

        # 1. Outgoing Echo Prevention: Sender darf niemals über eigene Nachrichten benachrichtigt werden
        if sender_user_id is not None:
            try:
                if int(sender_user_id) == cur_id:
                    return False
            except (ValueError, TypeError):
                pass

        # 2. Bereits gesehene oder gelesene Nachrichten (z.B. aktiver Chat) -> kein Alert
        if is_read:
            return False

        # 3. Empfängerprüfung für Direktnachrichten
        if recipient_id is not None:
            try:
                return int(recipient_id) == cur_id
            except (ValueError, TypeError):
                return False

        # 4. Gruppen-Nachrichten (wenn recipient_id nicht definiert ist)
        if is_group:
            return True

        # Falls recipient_id nicht definiert ist (Legacy / Broadcast):
        # Nur benachrichtigen, wenn der Sender nicht der aktuelle Nutzer ist
        if sender_user_id is not None:
            try:
                return int(sender_user_id) != cur_id
            except (ValueError, TypeError):
                return False

        return False

    @staticmethod
    def should_notify_group(
        *,
        group_member_ids: list[int | str] | set[int | str],
        current_user_id: int | str | None,
        is_read: bool = False,
        sender_user_id: int | str | None = None,
    ) -> bool:
        """Prüft Gruppen-Nachrichten: Outgoing Echo Prevention und Gruppenmitgliedschaft."""
        if current_user_id is None:
            return False

        try:
            cur_id = int(current_user_id)
        except (ValueError, TypeError):
            return False

        if sender_user_id is not None:
            try:
                if int(sender_user_id) == cur_id:
                    return False
            except (ValueError, TypeError):
                pass

        if is_read:
            return False

        member_ids: set[int] = set()
        for mid in group_member_ids:
            try:
                member_ids.add(int(mid))
            except (ValueError, TypeError):
                pass

        return cur_id in member_ids
