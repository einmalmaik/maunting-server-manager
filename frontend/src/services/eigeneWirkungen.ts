/**
 * Wirkungen der eigenen Zweitgeräte, die ein noch nicht geöffnetes Gespräch
 * betreffen.
 *
 * Der Weg ist die Geräte-Mailbox: sie ist die einzige, in die ein Gerät an das
 * eigene Konto schreiben darf. Ihr Cursor wandert aber weiter, auch wenn das
 * Gespräch gerade nicht offen ist. Ohne diese Ablage wäre eine Bearbeitung aus
 * der App für das Web verloren, sobald es den Chat erst später öffnet — die
 * Nachricht stünde dort unverändert, obwohl sie längst korrigiert wurde.
 *
 * **Kein neuer Vertrauensweg.** Gespeichert wird der **verschlüsselte,
 * signierte Klartext**, den `holeGeraeteSteuerung` bereits gegen den
 * Geräteschlüssel geöffnet hat. Angewendet wird er später durch dieselbe
 * Prüfung wie ein Paket der Gegenseite (`werteUmschlaegeAus`): Signatur,
 * `actor_id`, `mailbox`-Bindung. Hier wird nichts ausgewertet, nur aufbewahrt.
 *
 * **Versiegelt wie der Verlauf.** Bis 10/2026 stand die Liste als offenes JSON
 * in `localStorage`. Eine Bearbeitung trägt ihren neuen Text (`new_text`) im
 * Klartext, und damit lag eine korrigierte Nachricht auch dann lesbar im
 * Browserprofil, wenn der Messenger per PIN verschlossen war. Jetzt geht jede
 * Zeile durch `versiegleZeile` — bei eingerichtetem PIN also durch denselben
 * Schlüssel wie der Nachrichtenverlauf, und im verschlossenen Zustand wird
 * nichts geschrieben.
 *
 * Die Ablage ist kontogebunden (`offlineAblage`); beim Abmelden fällt sie mit.
 * Sie ist eine Warteschlange, kein Verlauf: nach dem Anwenden räumt der
 * Lesepfad die Einträge des Gesprächs weg.
 */

import { MESSENGER_KEYS, getStorageItem, setStorageItem } from '@/lib/offlineAblage'
import { entsiegleZeile, versiegleZeile } from '@/services/lokaleVersiegelung'

export interface EigeneWirkung {
  /** Die Chat-Mailbox, zu der die Wirkung gehört. */
  mailbox: string
  /** Der entschlüsselte, signierte Klartext des Steuerpakets. */
  payload: string
  /** Wann das Zweitgerät sie abgeschickt hat — für die Reihenfolge. */
  zeitpunkt: string
}

/** Mehr braucht kein realistischer Rückstand; ältere fallen weg. */
const HOECHSTENS = 200

/**
 * Bindet eine Zeile an ihr Gespräch.
 *
 * Die Mailbox bleibt als Klarfeld stehen — ohne sie ließe sich die Ablage nicht
 * filtern, ohne jede Zeile zu entschlüsseln. Sie ist eine abgeleitete Kennung
 * und steht ohnehin im Mailboxverzeichnis des Benachrichtigungs-Stores; der
 * Inhalt bleibt es, der hinter dem Siegel liegt.
 */
function wirkungsAad(mailbox: string): string {
  return `msm-eigene-wirkung:${mailbox}`
}

function istWirkung(wert: unknown): wert is EigeneWirkung {
  if (!wert || typeof wert !== 'object') return false
  const e = wert as Record<string, unknown>
  return (
    typeof e.mailbox === 'string' &&
    Boolean(e.mailbox) &&
    typeof e.payload === 'string' &&
    typeof e.zeitpunkt === 'string'
  )
}

async function lesen(): Promise<EigeneWirkung[]> {
  const roh = getStorageItem(MESSENGER_KEYS.EIGENE_WIRKUNGEN)
  if (!roh) return []
  let liste: unknown
  try {
    liste = JSON.parse(roh)
  } catch {
    return []
  }
  if (!Array.isArray(liste)) return []

  const offen: EigeneWirkung[] = []
  for (const eintrag of liste) {
    if (!eintrag || typeof eintrag !== 'object') continue
    // Alte Zeilen liegen im Klartext da; `entsiegleZeile` gibt sie unverändert
    // zurück. Versiegelte brauchen einen Schlüssel — ohne ihn bleibt die Zeile
    // in der Ablage und wird beim nächsten Entsperren gelesen.
    const mailbox = (eintrag as Record<string, unknown>).mailbox
    if (typeof mailbox !== 'string' || !mailbox) continue
    const wert = await entsiegleZeile<Record<string, unknown>>(
      eintrag as Record<string, unknown>,
      wirkungsAad(mailbox),
    )
    if (istWirkung(wert)) offen.push(wert)
  }
  return offen
}

async function schreiben(liste: EigeneWirkung[]): Promise<void> {
  const gekappt = liste.slice(-HOECHSTENS)
  const raus: Record<string, unknown>[] = []
  for (const wirkung of gekappt) {
    // Wirft im verschlossenen Zustand (`MessengerVerschlossenError`) — dann
    // wird nichts abgelegt, statt den Klartext zu schreiben.
    raus.push(await versiegleZeile({ ...wirkung }, ['mailbox'], wirkungsAad(wirkung.mailbox)))
  }
  setStorageItem(MESSENGER_KEYS.EIGENE_WIRKUNGEN, JSON.stringify(raus))
}

/**
 * Ein Lauf zur Zeit.
 *
 * Lesen, ändern, schreiben ist ohne Klammer verlustbehaftet, sobald zwei
 * Aufrufe sich überlappen: der zweite liest den Stand vor dem ersten Schreiben
 * und überschreibt es. Genau das passiert beim Lesepfad, der mehrere Steuerpakete
 * hintereinander einsammelt (Regel 45: nach einem `await` gilt der alte Stand
 * nicht mehr).
 */
let kette: Promise<unknown> = Promise.resolve()

function nacheinander<T>(arbeit: () => Promise<T>): Promise<T> {
  const lauf = kette.then(arbeit, arbeit)
  kette = lauf.then(
    () => undefined,
    () => undefined,
  )
  return lauf
}

/** Legt eine Wirkung ab, die dieses Gerät gerade nicht anwenden kann. */
export function merkeEigeneWirkung(wirkung: EigeneWirkung): Promise<void> {
  return merkeEigeneWirkungen([wirkung])
}

/** Dasselbe für mehrere auf einmal — ein Schreibvorgang, ein Siegel. */
export function merkeEigeneWirkungen(wirkungen: readonly EigeneWirkung[]): Promise<void> {
  const gueltig = wirkungen.filter(
    (w) => Boolean(w.mailbox) && Boolean(w.payload) && typeof w.zeitpunkt === 'string',
  )
  if (gueltig.length === 0) return Promise.resolve()
  return nacheinander(async () => {
    const liste = await lesen()
    let geaendert = false
    for (const wirkung of gueltig) {
      // Dieselbe Zustellung kann durch einen Neustart ein zweites Mal ankommen.
      // Der Klartext trägt eine eigene Kennung; gleicher Inhalt heißt gleiche
      // Wirkung.
      if (liste.some((e) => e.payload === wirkung.payload)) continue
      liste.push(wirkung)
      geaendert = true
    }
    if (geaendert) await schreiben(liste)
  })
}

/**
 * Liest den Klartext eines Steuerpakets und legt die Wirkung für ihr Gespräch ab.
 *
 * Der Weg für den Start des Messengers: `holeGeraeteSteuerung` läuft dort
 * **ohne offenes Gespräch**, und was eine Bearbeitung oder Löschung aus einem
 * anderen Gerät dort ankommt, ließ sich bis 10/2026 gar nicht ablegen — der
 * Rückruf fehlte. Der Cursor wanderte weiter, und die Wirkung war weg.
 *
 * Hier wird nichts ausgewertet; abgelegt wird der signierte Klartext, später
 * angewendet durch dieselben Prüfungen wie ein Paket der Gegenseite.
 */
export function merkeWirkungAusKlartext(
  klartext: string,
  env: { id: number; created_at: string },
): Promise<void> {
  let roh: Record<string, unknown>
  try {
    const geparst = JSON.parse(klartext)
    if (!geparst || typeof geparst !== 'object') return Promise.resolve()
    roh = geparst as Record<string, unknown>
  } catch {
    return Promise.resolve()
  }
  const mailbox = typeof roh.mailbox === 'string' ? roh.mailbox : ''
  if (!mailbox) return Promise.resolve()
  return merkeEigeneWirkung({ mailbox, payload: klartext, zeitpunkt: env.created_at }).catch(
    () => {},
  )
}

/** Was für dieses Gespräch noch aussteht, älteste zuerst. */
export function eigeneWirkungenFuer(mailbox: string): Promise<EigeneWirkung[]> {
  if (!mailbox) return Promise.resolve([])
  return nacheinander(async () =>
    (await lesen())
      .filter((e) => e.mailbox === mailbox)
      .sort((a, b) => a.zeitpunkt.localeCompare(b.zeitpunkt)),
  )
}

/**
 * Räumt die Wirkungen eines Gesprächs weg.
 *
 * Gerufen **nach** dem Anwenden. Ein Absturz dazwischen lässt sie stehen; die
 * Pakete sind idempotent (gleicher Text, gleiche Löschung, gleiche Reaktion),
 * und `uebernehmeVerfall` verwirft dieselbe Umstellung am Zeitpunkt.
 */
export function vergissEigeneWirkungen(mailbox: string): Promise<void> {
  if (!mailbox) return Promise.resolve()
  return nacheinander(async () => {
    const liste = await lesen()
    if (!liste.some((e) => e.mailbox === mailbox)) return
    await schreiben(liste.filter((e) => e.mailbox !== mailbox))
  })
}

/** Nur für Tests und den Abmeldeweg, falls die Ablage einzeln geleert wird. */
export function leereEigeneWirkungen(): void {
  setStorageItem(MESSENGER_KEYS.EIGENE_WIRKUNGEN, '[]')
}
