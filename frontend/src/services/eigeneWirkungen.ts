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
 * Die Ablage ist kontogebunden (`offlineAblage`); beim Abmelden fällt sie mit.
 * Sie ist eine Warteschlange, kein Verlauf: nach dem Anwenden räumt der
 * Lesepfad die Einträge des Gesprächs weg.
 */

import { MESSENGER_KEYS, getStorageItem, setStorageItem } from '@/lib/offlineAblage'

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

function lesen(): EigeneWirkung[] {
  const roh = getStorageItem(MESSENGER_KEYS.EIGENE_WIRKUNGEN)
  if (!roh) return []
  try {
    const liste = JSON.parse(roh)
    if (!Array.isArray(liste)) return []
    return liste.filter(
      (e): e is EigeneWirkung =>
        Boolean(e) &&
        typeof e === 'object' &&
        typeof e.mailbox === 'string' &&
        typeof e.payload === 'string' &&
        typeof e.zeitpunkt === 'string',
    )
  } catch {
    return []
  }
}

function schreiben(liste: EigeneWirkung[]): void {
  setStorageItem(MESSENGER_KEYS.EIGENE_WIRKUNGEN, JSON.stringify(liste.slice(-HOECHSTENS)))
}

/** Legt eine Wirkung ab, die dieses Gerät gerade nicht anwenden kann. */
export function merkeEigeneWirkung(wirkung: EigeneWirkung): void {
  if (!wirkung.mailbox || !wirkung.payload) return
  const liste = lesen()
  // Dieselbe Zustellung kann durch einen Neustart ein zweites Mal ankommen.
  // Der Klartext trägt eine eigene Kennung; gleicher Inhalt heißt gleiche
  // Wirkung.
  if (liste.some((e) => e.payload === wirkung.payload)) return
  liste.push(wirkung)
  schreiben(liste)
}

/** Was für dieses Gespräch noch aussteht, älteste zuerst. */
export function eigeneWirkungenFuer(mailbox: string): EigeneWirkung[] {
  if (!mailbox) return []
  return lesen()
    .filter((e) => e.mailbox === mailbox)
    .sort((a, b) => a.zeitpunkt.localeCompare(b.zeitpunkt))
}

/**
 * Räumt die Wirkungen eines Gesprächs weg.
 *
 * Gerufen **nach** dem Anwenden. Ein Absturz dazwischen lässt sie stehen; die
 * Pakete sind idempotent (gleicher Text, gleiche Löschung, gleiche Reaktion),
 * und `uebernehmeVerfall` verwirft dieselbe Umstellung am Zeitpunkt.
 */
export function vergissEigeneWirkungen(mailbox: string): void {
  if (!mailbox) return
  const liste = lesen()
  if (!liste.some((e) => e.mailbox === mailbox)) return
  schreiben(liste.filter((e) => e.mailbox !== mailbox))
}

/** Nur für Tests und den Abmeldeweg, falls die Ablage einzeln geleert wird. */
export function leereEigeneWirkungen(): void {
  setStorageItem(MESSENGER_KEYS.EIGENE_WIRKUNGEN, '[]')
}
