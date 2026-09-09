/**
 * NotificationService
 *
 * Verwaltet die Filterlogik für Geräte-, Push- und Unread-Benachrichtigungen.
 *
 * Strikte Sicherheits- und UX-Invarianten:
 * 1. Outgoing Echo Prevention: Wenn der Benutzer (oder ein KI-Worker in seinem Namen)
 *    eine Nachricht versendet, darf für den Sender-Account KEINE Push-Benachrichtigung
 *    oder Unread-Badge getriggert werden.
 * 2. Empfänger-Filterung: Push/Alerts gibt es ausschließlich für den tatsächlichen
 *    Empfänger bei ungesehenen Nachrichten (`recipient_id == current_user && !is_read`).
 */

export interface NotificationFilterParams {
  recipientId?: number | string | null
  currentUserId?: number | string | null
  isRead?: boolean
  senderUserId?: number | string | null
  isGroup?: boolean
}

export class NotificationService {
  /**
   * Prüft strikt, ob eine Benachrichtigung / Unread-Badge getriggert werden darf.
   */
  static shouldNotify({
    recipientId,
    currentUserId,
    isRead = false,
    senderUserId,
    isGroup = false,
  }: NotificationFilterParams): boolean {
    const curId = currentUserId != null ? Number(currentUserId) : null
    const sndId = senderUserId != null ? Number(senderUserId) : null
    const recId = recipientId != null ? Number(recipientId) : null

    if (curId === null || isNaN(curId)) {
      return false
    }

    // 1. Outgoing Echo Prevention: Sender darf niemals benachrichtigt werden
    if (sndId !== null && !isNaN(sndId) && sndId === curId) {
      return false
    }

    // 2. Bereits gesehene/gelesene Nachricht (z. B. Chatfenster aktiv geöffnet) -> kein Alert/Badge
    if (isRead) {
      return false
    }

    // 3. Strikte Empfängerprüfung für Direktnachrichten
    if (recId !== null && !isNaN(recId)) {
      return recId === curId
    }

    // 4. Gruppen-Nachrichten (wenn recipientId nicht gesetzt ist)
    if (isGroup) {
      return true
    }

    // Falls recipientId nicht im Event enthalten ist (z. B. Legacy Broadcast):
    // Alert nur auslösen, wenn senderUserId nicht der aktuelle Benutzer ist
    if (sndId !== null && !isNaN(sndId)) {
      return sndId !== curId
    }

    return false
  }

  /**
   * Prüft Gruppen-Nachrichten auf Mitgliedschaft und Outgoing Echo Prevention.
   */
  static shouldNotifyGroup({
    groupMemberIds,
    currentUserId,
    isRead = false,
    senderUserId,
  }: {
    groupMemberIds: (number | string)[]
    currentUserId?: number | string | null
    isRead?: boolean
    senderUserId?: number | string | null
  }): boolean {
    const curId = currentUserId != null ? Number(currentUserId) : null
    const sndId = senderUserId != null ? Number(senderUserId) : null

    if (curId === null || isNaN(curId)) return false
    if (sndId !== null && !isNaN(sndId) && sndId === curId) return false
    if (isRead) return false

    return groupMemberIds.some((id) => Number(id) === curId)
  }
}
