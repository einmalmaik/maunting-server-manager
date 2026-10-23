/** Lese- und Zustellquittungen aus Steuerumschlägen. */

import type { Lesung } from '@/hooks/useKonversation'

/**
 * Woran sich eine Quittung im Umschlag erkennt.
 *
 * Steht in der `client_uuid`, weil das das einzige Feld ist, das den Weg durch
 * das Relais bis in den Lesepfad übersteht. Eine Quittung ist selbst ein
 * Umschlag in derselben Mailbox und darf weder als Nachricht erscheinen noch
 * erneut quittiert werden.
 */
export const QUITTUNG_PRAEFIX = 'deliv-'

export interface QuittungsZustand {
  maxPartnerReadId: number
  maxPartnerDeliveredId: number
}

/** Was ein Häkchen an einer Zeile bedeuten kann. */
export type HakenStatus = 'queued' | 'failed' | 'sent' | 'delivered' | 'read'

export interface HakenZeile {
  id: number
  isSelf: boolean
  isRead?: boolean
  isDelivered?: boolean
  status?: HakenStatus
}

/**
 * Die Häkchen einer Zeile aus dem Stand des Partners.
 *
 * **Der gespeicherte Stand ist die Untergrenze.** Quittungen sind kumulativ und
 * gehen nur nach oben: steht in der Zeile schon „gelesen", dann war sie es
 * einmal, und die Wahrheitswerte daneben dürfen das nicht zurücknehmen. Bis
 * 22.10.2026 konnten sie es: nach einem Chatwechsel standen die Partnerzähler
 * wieder auf 0, `status` behielt „read", `isRead`/`isDelivered` wurden auf
 * `false` geschrieben. Gespeichert war „gelesen, aber nicht gelesen" — und die
 * Blase zeigt das Häkchen an `isRead`, nicht an `status`.
 *
 * Beide Berechnungsstellen im Lesepfad (frisch entschlüsselt und die eigene
 * Zeile aus dem Zustand) gehen über diese Funktion, damit sie nicht wieder
 * auseinanderlaufen.
 */
export function hakenFuer(
  zeile: HakenZeile,
  quittung: QuittungsZustand,
): { isRead: boolean; isDelivered: boolean; status: HakenStatus } {
  const vorher: HakenStatus | undefined =
    zeile.isRead || zeile.status === 'read'
      ? 'read'
      : zeile.isDelivered || zeile.status === 'delivered'
        ? 'delivered'
        : undefined

  if (!zeile.isSelf) {
    return {
      isRead: Boolean(zeile.isRead),
      isDelivered: Boolean(zeile.isDelivered),
      status: vorher ?? zeile.status ?? (zeile.id > 0 ? 'sent' : 'queued'),
    }
  }

  /*
   * `id > 0` ist keine Vorsicht: eine noch nicht bestätigte Zeile trägt 0, und
   * `0 >= 0` machte aus einer Nachricht, die noch in der Warteschlange steht,
   * eine gelesene. Sie kommt aus einem Umschlag — dann ist ihre Kennung positiv.
   */
  const gelesen = (zeile.id > 0 && quittung.maxPartnerReadId >= zeile.id) || vorher === 'read'
  const angekommen =
    gelesen ||
    (zeile.id > 0 && quittung.maxPartnerDeliveredId >= zeile.id) ||
    vorher === 'delivered'
  return {
    isRead: gelesen,
    isDelivered: angekommen,
    status: gelesen
      ? 'read'
      : angekommen
        ? 'delivered'
        : zeile.status || (zeile.id > 0 ? 'sent' : 'queued'),
  }
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
