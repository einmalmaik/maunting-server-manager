/** Lese- und Zustellquittungen aus Steuerumschlägen. */

import type { Lesung } from '@/hooks/useKonversation'

export interface QuittungsZustand {
  maxPartnerReadId: number
  maxPartnerDeliveredId: number
}

/**
 * Verarbeitet eine Quittung. `true` heißt: der Umschlag ist verbraucht.
 *
 * Die Zähler werden direkt im übergebenen Zustand fortgeschrieben, damit die
 * Hauptschleife am Ende denselben Stand zurückgeben kann.
 */
export function verarbeiteQuittung(
  parsed: Record<string, any>,
  umschlag: Lesung['env'],
  currentMid: string,
  currentUserId: number,
  zustand: QuittungsZustand,
  quittiertGelesen: (bis: number) => void,
  quittiertZugestellt: (bis: number) => void,
  markAsRead: (mailbox: string) => void,
): boolean {
  if (parsed.type === 'read_receipt') {
    const readUpTo = Number(parsed.read_up_to_id || 0)
    const readerId = Number(parsed.reader_id || 0)
    if (Number(readerId) !== Number(currentUserId)) {
      if (readUpTo > zustand.maxPartnerReadId) {
        zustand.maxPartnerReadId = readUpTo
        quittiertGelesen(readUpTo)
      }
      if (readUpTo > zustand.maxPartnerDeliveredId) {
        zustand.maxPartnerDeliveredId = readUpTo
        quittiertZugestellt(readUpTo)
      }
    } else {
      // Multi-Device: Vom aktuellen Benutzer auf anderem Gerät gelesen
      markAsRead(currentMid)
    }
    return true
  }

  if (parsed.type === 'delivery_receipt') {
    const deliveredUpTo = Number(parsed.delivered_up_to_id || 0)
    const receiverId = Number(parsed.receiver_id || 0)
    if (
      Number(receiverId) !== Number(currentUserId) &&
      deliveredUpTo > zustand.maxPartnerDeliveredId
    ) {
      zustand.maxPartnerDeliveredId = deliveredUpTo
      quittiertZugestellt(deliveredUpTo)
    }
    return true
  }

  void umschlag
  return false
}
