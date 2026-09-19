/**
 * Der Verlauf zieht auf ein frisch gekoppeltes Gerät um.
 *
 * Ein neues Gerät hat keine Ratchet-Sitzungen und bekommt vom Server nichts
 * Rückwirkendes: die Umschläge in der Mailbox sind gegen Geräte versiegelt, die
 * es nicht ist, und ein Nachrichtenschlüssel ist nach dem ersten Öffnen
 * ohnehin verbraucht. Ohne diesen Weg stünde es vor einem leeren Gespräch,
 * obwohl daneben ein Gerät desselben Kontos den ganzen Verlauf hat.
 *
 * Also gibt das eingerichtete Gerät ihn weiter, über den Kanal, den es schon
 * gibt: die Kopplung. Es sieht am Status, dass eingelöst wurde, versiegelt
 * seinen lokalen Klartextverlauf gegen den **Geräteschlüssel** des neuen
 * Geräts und legt ihn an der Einladung ab. Das neue Gerät holt ihn einmal, der
 * Server löscht ihn dabei.
 *
 * **Schlüssel wandern nicht mit.** Übertragen wird der gelesene Verlauf, nicht
 * die Fähigkeit, ihn zu lesen. Das neue Gerät bleibt bei seinem eigenen Paar,
 * baut eigene Sitzungen auf und kann ältere Umschläge weiterhin nicht öffnen —
 * es braucht sie auch nicht mehr, es hat ja den Klartext.
 *
 * Der Server sieht einen Textblock. Aufmachen kann ihn nur das Gerät, für das
 * versiegelt wurde.
 */

import { api } from '@/api/client'

import { decryptE2eeHybrid, encryptE2eeHybrid } from './e2eeCrypto'
import { exportUserNotesKey, setUserNotesKey } from './notesCalendarCrypto'
import {
  listeLokaleMailboxen,
  loadLocalMessages,
  saveLocalMessages,
  mischeVerlauf,
  sortMessagesChronologically,
  type LocalStoredMessage,
} from './messengerLocalStore'

/** Derselbe Deckel wie im Backend (`MAX_VERLAUF_BYTES`). */
const MAX_BLOB_BYTES = 25 * 1024 * 1024

/**
 * Wie viele Nachrichten je Mailbox mitgehen — absteigend, bis es passt.
 *
 * Erst wird großzügig gepackt, dann gemessen; passt es nicht, kommt die
 * nächstkleinere Stufe. Gemessen wird am **versiegelten** Blob, nicht am
 * Klartext: die Hülle kostet ein Drittel, und ein Deckel, der den Aufschlag
 * nicht kennt, lässt den Upload in einen 400er laufen (dieselbe Lehre wie bei
 * `maxKlartextBytes` für Anhänge).
 */
const STUFEN = [200, 100, 50, 20, 5]

interface VerlaufPaket {
  v: number
  /** Je Mailbox die jüngsten Nachrichten, so wie sie lokal liegen. */
  mailboxen: Record<string, LocalStoredMessage[]>
  notesKey?: string | null
}

export interface UebergabeZiel {
  device_id: string
  public_key: string
}

/** Was `GET /auth/devices/pairing/{code}/status` über den Erstabgleich sagt. */
export interface KopplungsStatus {
  exists: boolean
  redeemed: boolean
  expired: boolean
  label?: string
  neue_geraete?: UebergabeZiel[]
  verlauf_abgelegt?: boolean
}

function byteLaenge(text: string): number {
  return new TextEncoder().encode(text).length
}

/**
 * Packt den lokalen Verlauf und versiegelt ihn für die genannten Geräte.
 *
 * Ein Umschlag je Zielgerät, als Liste. In aller Regel ist es genau einer;
 * mehrere entstehen nur, wenn sich im selben Zeitfenster noch ein Gerät
 * gemeldet hat. Jedes liest alle, nur eines öffnet sich — dieselbe Form wie
 * bei der Zustellung der Gruppenschlüssel.
 *
 * `null` heißt: es gibt nichts zu übergeben.
 */
export async function packeUndVersiegele(ziele: readonly UebergabeZiel[]): Promise<string | null> {
  if (ziele.length === 0) return null

  const mailboxen = await listeLokaleMailboxen()
  const rawNotesKey = exportUserNotesKey()

  const verlaeufe = await Promise.all(
    mailboxen.map(async (mid) => ({ mid, nachrichten: await loadLocalMessages(mid) })),
  )
  const belegt = verlaeufe.filter((v) => v.nachrichten.length > 0)
  if (belegt.length === 0 && !rawNotesKey) return null

  for (const stufe of STUFEN) {
    const paket: VerlaufPaket = { v: 1, mailboxen: {}, notesKey: rawNotesKey }
    for (const { mid, nachrichten } of belegt) {
      paket.mailboxen[mid] = sortMessagesChronologically(nachrichten).slice(-stufe)
    }
    const klartext = JSON.stringify(paket)

    const umschlaege: string[] = []
    for (const ziel of ziele) {
      umschlaege.push(await encryptE2eeHybrid(klartext, ziel.public_key))
    }
    const blob = JSON.stringify(umschlaege)
    if (byteLaenge(blob) <= MAX_BLOB_BYTES) return blob
  }

  // Selbst die kleinste Stufe passt nicht. Lieber nichts übergeben als eine
  // Anfrage schicken, die das Backend zurückweist.
  return null
}

/**
 * Öffnet einen übergebenen Verlauf und mischt ihn in den lokalen Speicher.
 *
 * Gemischt, nicht ersetzt: zwischen Kopplung und Abholung kann das neue Gerät
 * längst selbst Nachrichten gesehen haben, und die sind der aktuellere Stand.
 *
 * Liefert die Zahl der übernommenen Nachrichten. Wirft nicht — ein
 * gescheiterter Erstabgleich kostet den Verlauf, nicht die Kopplung.
 */
export async function uebernimmVerlauf(blob: string, eigenerPrivateKey: string): Promise<number> {
  let umschlaege: unknown
  try {
    umschlaege = JSON.parse(blob)
  } catch {
    return 0
  }
  if (!Array.isArray(umschlaege)) return 0

  let paket: VerlaufPaket | null = null
  for (const umschlag of umschlaege) {
    if (typeof umschlag !== 'string') continue
    try {
      const klartext = await decryptE2eeHybrid(umschlag, eigenerPrivateKey)
      const gelesen = JSON.parse(klartext) as VerlaufPaket
      if (gelesen && typeof gelesen === 'object' && gelesen.mailboxen) {
        paket = gelesen
        break
      }
    } catch {
      // Für ein anderes Gerät versiegelt. Der nächste ist vielleicht meiner.
    }
  }
  if (!paket) return 0

  if (paket.notesKey) {
    try {
      await setUserNotesKey(1, paket.notesKey)
    } catch {}
  }

  let uebernommen = 0
  for (const [mid, nachrichten] of Object.entries(paket.mailboxen)) {
    if (!mid || !Array.isArray(nachrichten) || nachrichten.length === 0) continue
    const vorhanden = await loadLocalMessages(mid)
    const zusammen = vorhanden.length
      ? (mischeVerlauf(vorhanden, nachrichten) as LocalStoredMessage[])
      : sortMessagesChronologically(nachrichten)
    await saveLocalMessages(mid, zusammen)
    uebernommen += nachrichten.length
  }
  return uebernommen
}

// ==========================================
// Die beiden Seiten des Kanals
// ==========================================

/**
 * Panel-Seite: übergibt den Verlauf an die Geräte, die dieser Code eingebracht hat.
 *
 * Gibt `true` zurück, wenn etwas abgelegt wurde. `false` heißt „nichts zu tun"
 * und ist kein Fehler: ein Konto ohne Verlauf hat nichts zu übergeben.
 */
export async function uebergebeVerlauf(code: string, ziele: readonly UebergabeZiel[]): Promise<boolean> {
  const blob = await packeUndVersiegele(ziele)
  if (!blob) return false
  await api(`/auth/devices/pairing/${encodeURIComponent(code)}/verlauf`, {
    method: 'PUT',
    body: JSON.stringify({ blob }),
  })
  return true
}

/**
 * Geräteseite: holt den Verlauf ab, solange der Code lebt.
 *
 * Gepollt wird, weil die andere Seite erst am Status merkt, dass eingelöst
 * wurde, dann versiegelt und ablegt — das dauert ein paar Sekunden, und in
 * dieser Zeit steht hier schon ein angemeldetes Gerät. Nach `fristMs` ist
 * Schluss: liegt dann nichts, gab es nichts, und das Gerät beginnt mit einem
 * leeren Verlauf.
 */
export async function holeVerlaufAb(
  code: string,
  eigenerPrivateKey: string,
  fristMs = 60_000,
  taktMs = 2_000,
): Promise<number> {
  const ende = Date.now() + fristMs
  while (Date.now() < ende) {
    try {
      const antwort = await api<{ blob: string | null }>(
        `/auth/devices/pairing/${encodeURIComponent(code)}/verlauf`,
      )
      if (antwort.blob) return await uebernimmVerlauf(antwort.blob, eigenerPrivateKey)
    } catch {
      // Abgelaufener Code, kein Netz — beides beendet den Erstabgleich, nicht
      // die Kopplung.
      return 0
    }
    await new Promise((r) => setTimeout(r, taktMs))
  }
  return 0
}
