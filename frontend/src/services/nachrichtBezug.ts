/**
 * Wie ein Steuerumschlag seine Nachricht nennt — und warum seine Wirkung
 * sofort in die lokale Zeile muss.
 *
 * ## Die zwei Kennungen
 *
 * Eine Nachricht geht als **eine Kopie je Zielgerät** hinaus, jede mit eigener
 * Umschlagkennung (`<uuid>#<geraet>`). Der Absender merkt sich die der ersten
 * Bestätigung; ein zweites Gerät der Gegenseite liest eine andere und fand die
 * Nachricht zu `target_id` nicht. Bearbeiten und Löschen liefen dort ins Leere.
 *
 * Deshalb nennt jeder Steuerumschlag beides: die Umschlagkennung als
 * `target_id` und die **logische** Kennung als `target_client_uuid`. Gesucht
 * wird über beide, gefunden reicht einmal.
 *
 * ## Die Fensterregel
 *
 * `GET /api/social/e2ee/mailbox/<mid>` liefert die **letzten 100 Umschläge**.
 * Quittungen, Änderungen, Löschungen, Reaktionen und das Anheften teilen sich
 * dieses Fenster mit den Nachrichten selbst. Eine Wirkung, die nur im Umschlag
 * lebt, verschwindet also, sobald er herausrutscht — die Nachricht bleibt, die
 * Reaktion darauf wäre weg.
 *
 * **Jede Wirkung wird beim ersten Sehen in die lokale Zeile geschrieben.**
 * Nicht bei jedem Abruf neu aus dem Umschlag gelesen. Das ist keine
 * Beschleunigung, das ist der Grund, aus dem Reaktionen und Anheften überhaupt
 * funktionieren können.
 *
 * ## Der eigene Umschlag bleibt zu
 *
 * Wer mit dem Double Ratchet verschlüsselt, kann sein eigenes Erzeugnis nicht
 * wieder öffnen. Für das absendende Gerät kommt die eigene Reaktion, Änderung
 * oder Löschung **nie** zurück. Jede Wirkung muss der Absender deshalb beim
 * Senden zusätzlich selbst anwenden.
 */

/**
 * Pakete, die keine Gesprächsbeiträge sind.
 *
 * Dieselbe Liste hält `altbestandUebernahme.ts`. Was hier steht und dort
 * fehlt, landet beim einmaligen Umzug des Altbestands als leere Zeile im
 * Verlauf.
 */
export type Steuertyp =
  | 'read_receipt'
  | 'delivery_receipt'
  | 'edit_message'
  | 'delete_message'
  | 'reaction'
  | 'pin_message'
  | 'retention'

export const STEUERTYPEN: ReadonlySet<string> = new Set<Steuertyp>([
  'read_receipt',
  'delivery_receipt',
  'edit_message',
  'delete_message',
  'reaction',
  'pin_message',
  'retention',
])

export function istSteuerpaket(typ: unknown): typ is Steuertyp {
  return typeof typ === 'string' && STEUERTYPEN.has(typ)
}

/**
 * Ein Zeitpunkt aus einem Umschlag als Zahl.
 *
 * Wo zwei Seiten dieselbe Einstellung umstellen dürfen — Verfallsfrist,
 * angeheftete Nachricht —, entscheidet der Zeitpunkt, wer gewinnt. Zwei Fallen
 * stecken darin:
 *
 * 1. `Date.parse` liest einen ISO-Zeitpunkt **ohne** Zeitzone als Ortszeit. Das
 *    `created_at` des Servers kommt teils ohne `Z`, ist aber UTC; ungeprüft
 *    verschöbe es sich um den Zonenversatz. Fehlt die Zone, wird sie ergänzt.
 * 2. Buchstabenweise verglichen stünde `…:01` vor `…:01.000Z`. Deshalb Zahlen.
 *
 * Unlesbares ergibt 0 und verliert damit gegen jede echte Angabe.
 */
export function zeitAlsZahl(stand: string): number {
  if (!stand) return 0
  const hatZone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(stand)
  const t = Date.parse(hatZone ? stand : `${stand}Z`)
  return Number.isFinite(t) ? t : 0
}

/** Eine Nachricht, so weit sie für den Bezug gebraucht wird. */
export interface Bezugsziel {
  id: number
  clientUuid?: string
}

/** Was ein Steuerumschlag über sein Ziel sagt. */
export interface Bezugsangabe {
  target_id: number
  target_client_uuid?: string
}

/**
 * Die Zielangabe für einen neuen Steuerumschlag.
 *
 * Immer beide Felder, solange beide bekannt sind. Wer nur eins schickt, baut
 * genau den Fehler wieder ein, den der Modulkopf beschreibt.
 */
export function bezugFelder(msg: Bezugsziel): Bezugsangabe {
  return { target_id: msg.id, target_client_uuid: msg.clientUuid }
}

/** Das rohe, schon geparste Steuerpaket. */
interface RohesPaket {
  target_id?: unknown
  target_client_uuid?: unknown
}

function ziele(paket: RohesPaket): { id: number; uuid: string } {
  return {
    id: Number(paket.target_id || 0),
    uuid: String(paket.target_client_uuid || ''),
  }
}

/** Ein Eintrag je Nachricht; der zuletzt gemerkte gewinnt. */
export interface Bezugstafel<T> {
  merke(paket: RohesPaket, wert: T): void
  finde(msg: Bezugsziel): T | undefined
  readonly anzahl: number
}

export function neueBezugstafel<T>(): Bezugstafel<T> {
  const nachId = new Map<number, T>()
  const nachUuid = new Map<string, T>()
  return {
    merke(paket, wert) {
      const { id, uuid } = ziele(paket)
      if (id) nachId.set(id, wert)
      if (uuid) nachUuid.set(uuid, wert)
    },
    finde(msg) {
      return nachId.get(msg.id) ?? (msg.clientUuid ? nachUuid.get(msg.clientUuid) : undefined)
    },
    get anzahl() {
      return nachId.size + nachUuid.size
    },
  }
}

/**
 * Mehrere Einträge je Nachricht, in der Reihenfolge ihres Eintreffens.
 *
 * Für Reaktionen: auf eine Nachricht reagieren viele Leute mit vielen Zeichen,
 * und jede dieser Meldungen ist ein eigener Umschlag.
 */
export interface Sammeltafel<T> {
  ergaenze(paket: RohesPaket, wert: T): void
  finde(msg: Bezugsziel): T[]
  readonly anzahl: number
}

export function neueSammeltafel<T>(): Sammeltafel<T> {
  const nachId = new Map<number, T[]>()
  const nachUuid = new Map<string, T[]>()
  let gesamt = 0
  return {
    ergaenze(paket, wert) {
      const { id, uuid } = ziele(paket)
      if (!id && !uuid) return
      if (id) nachId.set(id, [...(nachId.get(id) || []), wert])
      if (uuid) nachUuid.set(uuid, [...(nachUuid.get(uuid) || []), wert])
      gesamt++
    },
    finde(msg) {
      // Nie beide Listen aneinanderhängen: derselbe Umschlag steht unter
      // beiden Kennungen, das gäbe jede Reaktion doppelt.
      const ueberUuid = msg.clientUuid ? nachUuid.get(msg.clientUuid) : undefined
      if (ueberUuid && ueberUuid.length) return ueberUuid
      return nachId.get(msg.id) || []
    },
    get anzahl() {
      return gesamt
    },
  }
}
