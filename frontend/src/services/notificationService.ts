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
  isControl?: boolean
  controlType?: string | null
  hasActiveForegroundConnection?: boolean
}

export interface PushPayloadSanitizeParams {
  title?: string
  body?: string
  isGroup?: boolean
  chatName?: string
  senderName?: string
  isE2ee?: boolean
  privacyMode?: boolean
  extraData?: Record<string, unknown>
}

export interface PreparePushDispatchParams {
  targetUserId: number | string
  senderUserId?: number | string | null
  title: string
  body?: string
  isE2ee?: boolean
  privacyMode?: boolean
  isControl?: boolean
  controlType?: string | null
  hasActiveForegroundConnection?: boolean
  extraData?: Record<string, unknown>
}

export class NotificationService {
  /**
   * Prüft strikt, ob die Aktion vom aktuellen Benutzer selbst (oder in seinem Namen via KI/Worker)
   * ausgelöst wurde (Outgoing Echo).
   */
  static isOutgoingEcho(
    senderUserId: number | string | null | undefined,
    currentUserId: number | string | null | undefined
  ): boolean {
    if (senderUserId == null || currentUserId == null) return false
    const sndStr = String(senderUserId).trim()
    const curStr = String(currentUserId).trim()
    if (!sndStr || !curStr) return false
    if (sndStr === curStr) return true

    const sndNum = Number(sndStr)
    const curNum = Number(curStr)
    if (!isNaN(sndNum) && !isNaN(curNum) && sndNum === curNum) {
      return true
    }

    for (const prefix of ['worker:', 'ai:', 'actor:', 'user:']) {
      if (sndStr.startsWith(prefix)) {
        const sub = sndStr.slice(prefix.length).trim()
        const subNum = Number(sub)
        if (!isNaN(subNum) && !isNaN(curNum) && subNum === curNum) {
          return true
        }
        if (sub === curStr) {
          return true
        }
      }
    }

    return false
  }

  /**
   * Prüft strikt, ob eine Benachrichtigung / Unread-Badge getriggert werden darf.
   */
  static shouldNotify({
    recipientId,
    currentUserId,
    isRead = false,
    senderUserId,
    isGroup = false,
    isControl = false,
    controlType,
    hasActiveForegroundConnection = false,
  }: NotificationFilterParams): boolean {
    if (currentUserId == null) {
      return false
    }

    // 1. Outgoing Echo Prevention: Sender darf niemals benachrichtigt werden
    if (NotificationService.isOutgoingEcho(senderUserId, currentUserId)) {
      return false
    }

    // 2. Interne Steuersignale (read receipts, delivery receipts, typing, edit, delete) ausschließen
    if (isControl || (controlType && controlType !== 'message' && controlType !== 'normal')) {
      return false
    }

    // 3. Bereits gesehene/gelesene Nachricht (z. B. Chatfenster aktiv geöffnet) -> kein Alert/Badge
    if (isRead) {
      return false
    }

    // 4. Bei aktiver Verbindung im Vordergrund keine doppelten OS-Pushes auslösen
    if (hasActiveForegroundConnection) {
      return false
    }

    // 5. Strikte Empfängerprüfung für Direktnachrichten
    if (recipientId != null) {
      const recNum = Number(recipientId)
      const curNum = Number(currentUserId)
      if (!isNaN(recNum) && !isNaN(curNum)) {
        return recNum === curNum
      }
      return String(recipientId).trim() === String(currentUserId).trim()
    }

    // 6. Gruppen-Nachrichten (wenn recipientId nicht gesetzt ist)
    if (isGroup) {
      return true
    }

    // Falls recipientId nicht im Event enthalten ist (z. B. Legacy Broadcast):
    // Alert nur auslösen, wenn senderUserId nicht der aktuelle Benutzer ist
    if (senderUserId != null) {
      return !NotificationService.isOutgoingEcho(senderUserId, currentUserId)
    }

    return false
  }

  /**
   * Prüft Gruppen-Nachrichten auf Mitgliedschaft, Outgoing Echo Prevention und Steuernachrichten.
   */
  static shouldNotifyGroup({
    groupMemberIds,
    currentUserId,
    isRead = false,
    senderUserId,
    isControl = false,
    controlType,
    hasActiveForegroundConnection = false,
  }: {
    groupMemberIds: (number | string)[]
    currentUserId?: number | string | null
    isRead?: boolean
    senderUserId?: number | string | null
    isControl?: boolean
    controlType?: string | null
    hasActiveForegroundConnection?: boolean
  }): boolean {
    if (currentUserId == null) return false
    if (NotificationService.isOutgoingEcho(senderUserId, currentUserId)) return false
    if (isControl || (controlType && controlType !== 'message' && controlType !== 'normal')) return false
    if (isRead) return false
    if (hasActiveForegroundConnection) return false

    const curNum = Number(currentUserId)
    const curStr = String(currentUserId).trim()

    return groupMemberIds.some((id) => {
      const num = Number(id)
      if (!isNaN(num) && !isNaN(curNum) && num === curNum) return true
      return String(id).trim() === curStr
    })
  }

  /**
   * Bereinigt Push-Payloads gegen Privacy-Leaks (Klartext, Metadaten, Secrets).
   */
  static sanitizePushPayload({
    title,
    body,
    isGroup = false,
    chatName,
    senderName,
    isE2ee = true,
    privacyMode = true,
    extraData,
  }: PushPayloadSanitizeParams): Record<string, unknown> {
    const sanitized: Record<string, unknown> = { ...(extraData || {}) }

    // Bereinige gefährliche Klartextfelder
    const leakKeys = [
      'text',
      'message_text',
      'message',
      'msg',
      'plaintext',
      'content',
      'body',
      'preview',
      'ciphertext_envelope',
      'raw_payload',
      'secret',
      'token',
      'key',
      'password',
      'attachments',
      'attachment',
      'private_key',
      'secret_key',
      'channel_key',
      'iv',
      'auth_tag',
      'salt',
    ]
    for (const k of leakKeys) {
      delete sanitized[k]
    }

    if (isE2ee || privacyMode) {
      const name = chatName || senderName
      sanitized.title = name ? (isGroup ? `Neue Nachricht in „${name}“` : `Neue Nachricht: ${name}`) : 'Neue Nachricht'
      sanitized.body = isGroup ? 'Neue Nachricht in der Gruppe.' : 'Du hast eine neue verschlüsselte Nachricht erhalten.'
      sanitized.is_e2ee = true
      sanitized.privacy_filtered = true
    } else {
      sanitized.title = title || 'Neue Benachrichtigung'
      sanitized.body = body || 'Neue Nachricht erhalten.'
    }

    return sanitized
  }

  /**
   * Erzeugt einen validierten, bereinigten Push-Dispatch-Auftrag oder null, wenn unterdrückt.
   */
  static preparePushDispatch({
    targetUserId,
    senderUserId,
    title,
    body,
    isE2ee = true,
    privacyMode = true,
    isControl = false,
    controlType,
    hasActiveForegroundConnection = false,
    extraData,
  }: PreparePushDispatchParams): Record<string, unknown> | null {
    if (NotificationService.isOutgoingEcho(senderUserId, targetUserId)) {
      return null
    }
    if (isControl || (controlType && controlType !== 'message' && controlType !== 'normal')) {
      return null
    }
    if (hasActiveForegroundConnection) {
      return null
    }

    return NotificationService.sanitizePushPayload({
      title,
      body,
      isE2ee,
      privacyMode,
      extraData: {
        target_user_id: targetUserId,
        sender_user_id: senderUserId,
        ...extraData,
      },
    })
  }

  /**
   * Zentraler Push-Dispatcher: Filtert, bereinigt und plant Push-Payloads.
   */
  static dispatchPush(params: PreparePushDispatchParams): Record<string, unknown> | null {
    return NotificationService.preparePushDispatch(params)
  }
}


