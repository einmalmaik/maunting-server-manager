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
 *
 * **Übergeben wird erst nach einer Rückfrage.** Für welches Gerät versiegelt
 * wird, sagt der Server (`neue_geraete`), und genau das ist die Stelle, an der
 * er ein eigenes unterschieben könnte. Deshalb zeigt das Panel Name und
 * Sicherheitsnummer des Geräts und übergibt erst, wenn der Mensch bestätigt —
 * die App zeigt dieselbe Nummer (`sicherheitsnummer`). Haben sich mehrere
 * Geräte gemeldet, wird gar nicht übergeben.
 *
 * **Der Notizschlüssel geht nur mit Unterschrift.** Er ist die eine Ausnahme
 * von „Schlüssel wandern nicht mit", und ihn nimmt das neue Gerät nur, wenn
 * ein Gerät dieses Kontos laut Verzeichnis die Übergabe an genau dieses Gerät
 * unterschrieben hat (`pruefeNotizUebergabe`). Ohne Unterschrift genügte ein
 * selbst gebautes Paket mit einem Schlüssel nach Wahl, und jede Notiz, die das
 * Gerät danach schreibt, wäre für den lesbar, der ihn gewählt hat. Die
 * Unterschrift bindet an ein Gerät, das das Verzeichnis unter diesem Konto
 * führt, und an genau das Zielgerät. Den Server hält sie nicht auf, und keine
 * andere Sitzung desselben Kontos — beide können ein Gerät mit eigenem
 * Signaturschlüssel eintragen. Die Sicherheitsnummer hilft hier ohnehin
 * nicht: sie weist dem Panel das Gerät nach, nicht dem Gerät das Paket.
 */

import { api } from '@/api/client'
import { angemeldetesKonto } from '@/lib/angemeldetesKonto'

import { decryptE2eeHybrid, encryptE2eeHybrid } from './e2eeCrypto'
import { eigenesGeraet, type EigenesGeraet } from './e2eeGeraet'
import {
  exportUserNotesKey,
  pruefeNotizUebergabe,
  setUserNotesKey,
  unterschreibeNotizUebergabe,
} from './notesCalendarCrypto'
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
  /**
   * Das Konto, dem `notesKey` gehört. Fehlt es — Pakete von vor 09/2026 —,
   * bleibt der Schlüssel liegen: damals ging der unter der Kennung 1 mit,
   * gleich wer angemeldet war.
   */
  konto?: number | null
  /** Das Gerät, das übergibt. Fehlt es, bleibt der Schlüssel liegen. */
  vonGeraet?: string
  /** Seine Unterschrift unter die Übergabe an **dieses** Zielgerät. */
  sig?: string
}

export interface UebergabeZiel {
  device_id: string
  public_key: string
  /** Wie das Gerät sich selbst genannt hat. Nur zur Anzeige in der Rückfrage. */
  label?: string
  /** Wann es sich gemeldet hat. Nur zur Anzeige in der Rückfrage. */
  created_at?: string | null
}

/** Was `GET /auth/devices/pairing/{code}/status` über den Erstabgleich sagt. */
export interface KopplungsStatus {
  exists: boolean
  redeemed: boolean
  expired: boolean
  label?: string
  /** Die Anmeldung, die das Einlösen erzeugt hat. Zum Widerrufen, falls das Gerät fremd ist. */
  family?: string | null
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
  // Der Notizschlüssel des angemeldeten Kontos. Bis 09/2026 stand hier
  // `exportUserNotesKey()` mit dem Vorgabewert 1: für jedes andere Konto ging
  // nichts mit, und auf einem geteilten Gerät ging der Schlüssel von Konto 1
  // an das neue Gerät eines anderen.
  const konto = angemeldetesKonto()
  const rawNotesKey = konto ? exportUserNotesKey(konto) : null
  const schluesselJe = await unterschriebeneSchluessel(konto, rawNotesKey, ziele)

  const verlaeufe = await Promise.all(
    mailboxen.map(async (mid) => ({ mid, nachrichten: await loadLocalMessages(mid) })),
  )
  const belegt = verlaeufe.filter((v) => v.nachrichten.length > 0)
  if (belegt.length === 0 && schluesselJe.size === 0) return null

  for (const stufe of STUFEN) {
    const verlauf: VerlaufPaket['mailboxen'] = {}
    for (const { mid, nachrichten } of belegt) {
      verlauf[mid] = sortMessagesChronologically(nachrichten).slice(-stufe)
    }

    // Je Ziel ein eigenes Paket: die Unterschrift nennt das Zielgerät.
    const umschlaege: string[] = []
    for (const ziel of ziele) {
      const paket: VerlaufPaket = { v: 1, mailboxen: verlauf, ...schluesselJe.get(ziel.device_id) }
      umschlaege.push(await encryptE2eeHybrid(JSON.stringify(paket), ziel.public_key))
    }
    const blob = JSON.stringify(umschlaege)
    if (byteLaenge(blob) <= MAX_BLOB_BYTES) return blob
  }

  // Selbst die kleinste Stufe passt nicht. Lieber nichts übergeben als eine
  // Anfrage schicken, die das Backend zurückweist.
  return null
}

/**
 * Der Notizschlüssel mit Unterschrift, je Zielgerät.
 *
 * Leer, wenn es keinen gibt oder dieses Gerät nicht unterschreiben kann. Dann
 * geht der Verlauf ohne Schlüssel: unsigniert nähme ihn das neue Gerät ohnehin
 * nicht, und es holt ihn sich später über die Geräte-Mailbox.
 */
async function unterschriebeneSchluessel(
  konto: number | null,
  rawNotesKey: string | null,
  ziele: readonly UebergabeZiel[],
): Promise<Map<string, Pick<VerlaufPaket, 'notesKey' | 'konto' | 'vonGeraet' | 'sig'>>> {
  const je = new Map<string, Pick<VerlaufPaket, 'notesKey' | 'konto' | 'vonGeraet' | 'sig'>>()
  if (!konto || !rawNotesKey) return je
  let meins: EigenesGeraet
  try {
    meins = await eigenesGeraet()
  } catch {
    return je
  }
  for (const ziel of ziele) {
    const sig = await unterschreibeNotizUebergabe(meins, konto, ziel.device_id, rawNotesKey)
    if (sig) je.set(ziel.device_id, { notesKey: rawNotesKey, konto, vonGeraet: meins.kennung, sig })
  }
  return je
}

/**
 * Nimmt den Notizschlüssel aus einem Paket — wenn er belegt ist.
 *
 * Überschreibt einen vorhandenen. Beim Koppeln übernimmt das Gerät den Stand
 * des Kontos, und ein abweichender Schlüssel kann hier nur einer sein, den das
 * Gerät während der Rückfrage selbst erzeugt hat. Ihn zu behalten hiesse, dass
 * die beiden Geräte ihre Notizen dauerhaft mit zwei Schlüsseln schreiben; ihn
 * zu ersetzen kostet die Notizen aus diesen Minuten. Das Kleinere von beidem.
 */
async function uebernimmNotizschluessel(konto: number, paket: VerlaufPaket): Promise<void> {
  if (typeof paket.notesKey !== 'string' || !paket.notesKey) return
  if (typeof paket.vonGeraet !== 'string' || !paket.vonGeraet) return
  let meins: EigenesGeraet
  try {
    meins = await eigenesGeraet()
  } catch {
    return
  }
  if (paket.vonGeraet === meins.kennung) return
  if (!(await pruefeNotizUebergabe(konto, paket.vonGeraet, meins.kennung, paket.notesKey, paket.sig))) {
    return
  }
  const vorhanden = exportUserNotesKey(konto)
  if (vorhanden && vorhanden !== paket.notesKey) {
    console.warn('[Verlauf] Ersetze den während der Kopplung erzeugten Notizschlüssel durch den des Kontos')
  }
  await setUserNotesKey(konto, paket.notesKey)
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

  // Nur unter dem Konto, das hier angemeldet ist, und nur, wenn das Paket
  // dasselbe nennt. Gekoppelt wird innerhalb eines Kontos; ein Paket, das ein
  // anderes nennt, gehört nicht hierher. Belegt sein muss es obendrein.
  const konto = angemeldetesKonto()
  if (paket.notesKey && konto && paket.konto === konto) {
    try {
      await uebernimmNotizschluessel(konto, paket)
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
 * wurde, dann nachfragt, versiegelt und ablegt. Die Nachfrage ist das Lange
 * daran: dort vergleicht ein Mensch die Sicherheitsnummer, und das dauert
 * Minuten. Deshalb zehn Minuten ab jetzt, also ab dem Einlösen — so lange
 * nimmt auch der Server an (`_uebergabe_endet` im Backend), danach nichts mehr.
 * Liegt bis dahin nichts, gab es nichts, und das Gerät beginnt mit einem
 * leeren Verlauf.
 */
export async function holeVerlaufAb(
  code: string,
  eigenerPrivateKey: string,
  fristMs = 10 * 60_000,
  taktMs = 3_000,
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
