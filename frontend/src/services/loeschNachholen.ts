/**
 * Die dauerhaften Server-Aufräumaufträge gelöschter Nachrichten abarbeiten.
 *
 * Der Auftrag entsteht im Löschpfad, **bevor** der Server das erste Mal
 * angesprochen wird (`baueLoeschauftrag`, `messengerLocalStore`). Hier wird er
 * abgearbeitet — bei jedem Anlass, nicht in einem Timer:
 *
 * * Start, sobald die Identität dieses Geräts bereit ist,
 * * `online` und die Rückkehr der App nach vorn (`useMessengerKern`),
 * * das Entsperren des Messengers (derselbe Effekt),
 * * unmittelbar nach dem Löschen, damit der Normalfall ohne Wartezeit fertig ist.
 *
 * **Ein Lauf zur Zeit, ein Anstoß geht nicht verloren.** Zwei Anlässe kurz
 * hintereinander (Löschen und `online`) würden sonst dieselbe Serverkopie
 * doppelt löschen. Der Server antwortet zwar idempotent, aber ein zweiter
 * Aufruf ist trotzdem einer zu viel. Ein Anstoß während eines Laufs wird
 * gemerkt und danach genau einmal nachgeholt.
 *
 * **Kontowechsel bricht ab.** Der Lauf gehört dem Konto, das ihn angestoßen
 * hat; wechselt es, endet er, statt mit der Sitzung des neuen Kontos zu löschen
 * oder dessen Ablage anzufassen.
 *
 * **Kein stilles Aufgeben.** Ein endgültig abgelehnter Auftrag und ein nach
 * `MAX_VERSUCHE` erschöpfter fallen heraus und werden gemeldet; ein fremder
 * Anhang, den der Server nicht herausgibt, ebenso. Was übrig bleibt, ist ein
 * sichtbarer Fehler — nie eine Lücke.
 */

import { angemeldetesKonto } from '@/lib/angemeldetesKonto'
import {
  ladeLoeschAuftraege,
  legeLoeschauftragAn,
  loescheLoeschauftrag,
  schreibeLoeschauftrag,
} from './messengerLocalStore'
import { raeumeServerkopieAuf } from './nachrichtLoeschen'
import {
  istEndgueltigAbgelehnt,
  loeschRueckzugMs,
  MAX_VERSUCHE,
  type LoeschAuftrag,
} from './loeschAuftraege'

export { MAX_VERSUCHE } from './loeschAuftraege'

/**
 * Warum ein Auftrag aufgegeben wurde.
 *
 * `abgelehnt`: der Server hat das Aufräumen mit einem endgültigen Status
 * abgewiesen. `aufgegeben`: die Versuche sind erschöpft (Netz, 429, 5xx über
 * Tage). `medien`: der Text ist weg, ein Anhang ließ sich endgültig nicht
 * entfernen (etwa ein fremder Blob, den nur der Hochladende löschen darf).
 */
export type LoeschGrund = 'abgelehnt' | 'aufgegeben' | 'medien'

export type LoeschMelder = (
  auftrag: LoeschAuftrag,
  grund: LoeschGrund,
  medienGeblieben: number,
) => void

let melder: LoeschMelder | null = null

/**
 * Wer von einem aufgegebenen Auftrag erfährt.
 *
 * Der Lauf kennt keine React-Zustände. Die Oberfläche hängt sich hier ein und
 * zeigt eine Systemzeile oder einen Toast; in Tests ist es ein Zähler.
 */
export function setzeLoeschMelder(neu: LoeschMelder | null): () => void {
  melder = neu
  return () => {
    if (melder === neu) melder = null
  }
}

export interface LoeschLaufOptionen {
  /** Die Uhr für Tests und Proben; im Betrieb `Date.now`. */
  jetzt?: () => number
}

/** Was ein einzelner Auftrag in diesem Lauf ergeben hat. */
export interface LoeschErgebnis {
  /** Endgültig nicht entfernbare Anhänge (fremde Blobs). */
  medienGeblieben: number
}

/** Ein Lauf zur Zeit; ein Anstoß währenddessen geht nicht verloren. */
let laeuft: Promise<Map<string, LoeschErgebnis>> | null = null
let nochEinmal = false
let ergebnisse = new Map<string, LoeschErgebnis>()

export function stosseLoeschungenAn(
  optionen: LoeschLaufOptionen = {},
): Promise<Map<string, LoeschErgebnis>> {
  if (laeuft) {
    nochEinmal = true
    return laeuft
  }
  ergebnisse = new Map()
  laeuft = (async () => {
    try {
      do {
        nochEinmal = false
        await einLauf(optionen, ergebnisse)
      } while (nochEinmal)
    } catch {
      // Abarbeiten ist Hintergrundarbeit. Ein Fehler in der Ablage oder im
      // Netz darf weder den Chat aufhalten noch den nächsten Anlass.
    } finally {
      laeuft = null
    }
    return ergebnisse
  })()
  return laeuft
}

async function einLauf(
  { jetzt = () => Date.now() }: LoeschLaufOptionen,
  gesammelt: Map<string, LoeschErgebnis>,
): Promise<void> {
  const konto = angemeldetesKonto()
  if (konto === null) return
  const zeit = jetzt()
  for (const auftrag of await ladeLoeschAuftraege()) {
    if (angemeldetesKonto() !== konto) return
    // Was noch nicht dran ist, bleibt liegen; der nächste Anlass liest wieder.
    if (auftrag.naechsterVersuch > zeit) continue

    try {
      const { medienGeblieben } = await raeumeServerkopieAuf(auftrag)
      if (angemeldetesKonto() !== konto) return
      await loescheLoeschauftrag(auftrag.id).catch(() => {})
      if (angemeldetesKonto() !== konto) return
      gesammelt.set(auftrag.id, { medienGeblieben })
      if (medienGeblieben > 0) melder?.(auftrag, 'medien', medienGeblieben)
      continue
    } catch (fehler) {
      if (angemeldetesKonto() !== konto) return
      if (istEndgueltigAbgelehnt(fehler)) {
        await loescheLoeschauftrag(auftrag.id).catch(() => {})
        gesammelt.set(auftrag.id, { medienGeblieben: 0 })
        melder?.(auftrag, 'abgelehnt', 0)
        continue
      }
      // Netz, 401, 409, 429 und 5xx bleiben offen.
    }

    const versuche = (Number.isInteger(auftrag.versuche) ? auftrag.versuche : 0) + 1
    if (versuche >= MAX_VERSUCHE) {
      await loescheLoeschauftrag(auftrag.id).catch(() => {})
      gesammelt.set(auftrag.id, { medienGeblieben: 0 })
      melder?.(auftrag, 'aufgegeben', 0)
      continue
    }
    await schreibeLoeschauftrag({
      ...auftrag,
      versuche,
      naechsterVersuch: zeit + loeschRueckzugMs(versuche),
    }).catch(() => {})
  }
}

/** Was der Löschpfad von seinem sofortigen Versuch wissen muss. */
export interface LoeschAusgang {
  /** Der Auftrag liegt noch und wird beim nächsten Anlass erneut versucht. */
  offen: boolean
  /** Endgültig nicht entfernbare Anhänge (fremde Blobs). */
  medienGeblieben: number
}

/**
 * Legt den Auftrag an, versucht ihn sofort und sagt, was offen blieb.
 *
 * Der Auftrag wird **vor** dem ersten Serveraufruf abgelegt. Bricht die App
 * dazwischen ab, holt ihn der nächste Anlass nach. Gibt es keine Ablage
 * (gesperrt, voll, kein Konto), wird einmal direkt aufgeräumt: ohne Auftrag
 * gibt es keinen zweiten Anlass, und das sagt der Aufrufer dann ehrlich.
 */
export async function merkeUndRaeumeServerkopie(
  auftrag: LoeschAuftrag | null,
  optionen: LoeschLaufOptionen = {},
): Promise<LoeschAusgang> {
  if (!auftrag) return { offen: false, medienGeblieben: 0 }
  const konto = angemeldetesKonto()

  let gemerkt = false
  try {
    gemerkt = await legeLoeschauftragAn(auftrag)
  } catch {
    gemerkt = false
  }

  if (gemerkt) {
    const ergebnisse = await stosseLoeschungenAn(optionen)
    // Ein Wechsel mitten im Löschen: der Auftrag gehört der alten Ablage, und
    // was mit ihm geschieht, ist von hier aus nicht mehr zu beurteilen. Lieber
    // „bleibt" sagen als einen Erfolg behaupten, den niemand geprüft hat.
    if (angemeldetesKonto() !== konto) return { offen: true, medienGeblieben: 0 }
    const ergebnis = ergebnisse.get(auftrag.id)
    const offen = (await ladeLoeschAuftraege().catch(() => [])).some(
      (a) => a.id === auftrag.id,
    )
    return { offen, medienGeblieben: ergebnis?.medienGeblieben ?? 0 }
  }

  try {
    const { medienGeblieben } = await raeumeServerkopieAuf(auftrag)
    if (medienGeblieben > 0) melder?.(auftrag, 'medien', medienGeblieben)
    return { offen: false, medienGeblieben }
  } catch (fehler) {
    // Ohne Ablage gibt es keinen zweiten Anlass. Ein endgültiges Urteil wird
    // trotzdem gesagt; ein flüchtiger Fehler heißt „die Kopie bleibt", und das
    // meldet der Aufrufer über `offen`.
    if (istEndgueltigAbgelehnt(fehler)) melder?.(auftrag, 'abgelehnt', 0)
    return { offen: true, medienGeblieben: 0 }
  }
}

/**
 * Derselbe dauerhafte Auftrag für mehrere Zeilen, die zusammen fällig werden.
 *
 * Der Verfall räumt in einem Durchgang oft viele Nachrichten ab. Jede einzeln
 * anzustoßen hieße: ein vollständiger Lauf je Zeile, und der erste Lauf sähe
 * die noch nicht abgelegten gar nicht. Deshalb werden **erst alle** Aufträge
 * abgelegt, dann läuft genau ein Durchgang.
 *
 * Ohne Ablage (gesperrt, voll) wird jede Zeile einmal direkt versucht. Einen
 * zweiten Anlass gibt es dann nicht — die Zeile ist lokal schon Grabstein und
 * wird nie wieder fällig. Was scheitert, wird gemeldet statt verschluckt.
 */
export async function merkeUndRaeumeServerkopien(
  auftraege: readonly LoeschAuftrag[],
  optionen: LoeschLaufOptionen = {},
): Promise<void> {
  if (auftraege.length === 0) return
  const konto = angemeldetesKonto()

  let abgelegt = 0
  const ohneAblage: LoeschAuftrag[] = []
  for (const auftrag of auftraege) {
    // Ein Wechsel während des Ablegens: die weitere Arbeit gehörte in eine
    // fremde Ablage und würde mit der falschen Sitzung löschen.
    if (angemeldetesKonto() !== konto) return
    let gemerkt = false
    try {
      gemerkt = await legeLoeschauftragAn(auftrag)
    } catch {
      gemerkt = false
    }
    if (gemerkt) abgelegt += 1
    else ohneAblage.push(auftrag)
  }

  if (angemeldetesKonto() !== konto) return
  if (abgelegt > 0) await stosseLoeschungenAn(optionen).catch(() => {})

  for (const auftrag of ohneAblage) {
    if (angemeldetesKonto() !== konto) return
    try {
      const { medienGeblieben } = await raeumeServerkopieAuf(auftrag)
      if (medienGeblieben > 0) melder?.(auftrag, 'medien', medienGeblieben)
    } catch (fehler) {
      melder?.(auftrag, istEndgueltigAbgelehnt(fehler) ? 'abgelehnt' : 'aufgegeben', 0)
    }
  }
}
