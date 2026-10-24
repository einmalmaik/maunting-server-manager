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
  /**
   * Was der Partner ausdrücklich als gelesen bzw. zugestellt genannt hat.
   *
   * Die Zahlen daneben bleiben für Quittungen älterer Clients und für den
   * gespeicherten Stand. Eine ausdrückliche Kennung gilt ohne Rücksicht auf
   * die Zahlen: sie beweist genau diese eine Nachricht, keine Lücke und
   * keine Kopie für ein anderes Gerät dazwischen.
   */
  gelesenIds?: ReadonlySet<string>
  zugestelltIds?: ReadonlySet<string>
}

/** Was ein Häkchen an einer Zeile bedeuten kann. */
export type HakenStatus = 'queued' | 'failed' | 'sent' | 'delivered' | 'read'

export interface HakenZeile {
  id: number
  isSelf: boolean
  /** Die logische Nachrichtenkennung; Quittungen nennen genau diese. */
  clientUuid?: string
  /** Über das Gerätepostfach zugestellt: `id` ist eine Annahmenummer. */
  viaInbox?: boolean
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
  const ausdruecklichGelesen = Boolean(
    zeile.clientUuid && quittung.gelesenIds?.has(zeile.clientUuid),
  )
  const ausdruecklichZugestellt = Boolean(
    zeile.clientUuid && quittung.zugestelltIds?.has(zeile.clientUuid),
  )
  // Alte Zahlenquittungen gehören zum Umschlagraum der geteilten Mailbox. Eine
  // Zeile aus dem Gerätepostfach trägt dagegen eine Annahmenummer; die beiden
  // Folgen sind nicht vergleichbar.
  const zahlenGelten = !zeile.viaInbox
  const gelesen =
    ausdruecklichGelesen ||
    (zahlenGelten && zeile.id > 0 && quittung.maxPartnerReadId >= zeile.id) ||
    vorher === 'read'
  const angekommen =
    gelesen ||
    ausdruecklichZugestellt ||
    (zahlenGelten && zeile.id > 0 && quittung.maxPartnerDeliveredId >= zeile.id) ||
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
  quittiertGelesenIds?: (ids: string[]) => void,
  quittiertZugestelltIds?: (ids: string[]) => void,
): boolean {
  if (parsed.type === 'read_receipt') {
    const readerId = Number(parsed.reader_id || 0)
    if (Number(readerId) !== Number(currentUserId)) {
      const ids = kennungenAus(parsed.read_ids)
      if (ids.length > 0) {
        /*
         * Ausdrückliche Kennungen schlagen die Obergrenze.
         *
         * Die Zahl daneben zählte früher auch Kopien für andere Geräte und
         * Steuerpakete mit; sie darf hier nicht mehr gelten, sonst machte
         * eine hohe Zahl aus einer ungeöffneten Nachricht eine gelesene.
         */
        quittiertGelesenIds?.(ids)
        quittiertZugestelltIds?.(ids)
      } else {
        const readUpTo = Number(parsed.read_up_to_id || 0)
        if (readUpTo > zustand.maxPartnerReadId) {
          zustand.maxPartnerReadId = readUpTo
          quittiertGelesen(readUpTo)
        }
        if (readUpTo > zustand.maxPartnerDeliveredId) {
          zustand.maxPartnerDeliveredId = readUpTo
          quittiertZugestellt(readUpTo)
        }
      }
    } else {
      // Multi-Device: Vom aktuellen Benutzer auf anderem Gerät gelesen
      markAsRead(currentMid)
    }
    return true
  }

  if (parsed.type === 'delivery_receipt') {
    const receiverId = Number(parsed.receiver_id || 0)
    if (Number(receiverId) !== Number(currentUserId)) {
      const ids = kennungenAus(parsed.delivered_ids)
      if (ids.length > 0) {
        quittiertZugestelltIds?.(ids)
      } else {
        const deliveredUpTo = Number(parsed.delivered_up_to_id || 0)
        if (deliveredUpTo > zustand.maxPartnerDeliveredId) {
          zustand.maxPartnerDeliveredId = deliveredUpTo
          quittiertZugestellt(deliveredUpTo)
        }
      }
    }
    return true
  }

  void umschlag
  return false
}

/** Höchstens 200 brauchbare Kennungen aus einer Quittung. */
function kennungenAus(wert: unknown): string[] {
  if (!Array.isArray(wert)) return []
  const gesehen = new Set<string>()
  const liste: string[] = []
  for (const eintrag of wert) {
    if (typeof eintrag !== 'string' || !/^[A-Za-z0-9._:#-]{1,80}$/.test(eintrag)) continue
    if (gesehen.has(eintrag)) continue
    gesehen.add(eintrag)
    liste.push(eintrag)
    if (liste.length >= 200) break
  }
  return liste
}
