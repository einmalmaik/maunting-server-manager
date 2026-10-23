/**
 * Offene Anhaenge nachholen — beim Start, bei `online`, beim Entsperren und
 * nach jedem Ladepfad.
 *
 * Bis 10/2026 wurde ein gescheiterter Download nur beim naechsten Verarbeiten
 * derselben Umschlaege erneut versucht. Eine quittierte Postfach-Nachricht
 * kommt nie wieder, und ohne offenen Chat gab es keinen zweiten Anlauf: der
 * Anhang blieb fuer immer ungesichert, obwohl der Server ihn noch 30 Tage
 * haelt.
 *
 * Der Auftrag (`medienAuftraege.ts`) ueberlebt den Neustart. Hier wird er
 * abgearbeitet, mit begrenzten Versuchen und Rueckzug. Es gibt keinen Timer:
 * jeder Anlass (Start, Netz, Entsperren, Ladepfad) liest, was faellig ist.
 */

import { angemeldetesKonto } from '@/lib/angemeldetesKonto'
import {
  ladeMedienAuftraege,
  loescheMedienAuftrag,
  schreibeMedienAuftrag,
} from './messengerLocalStore'
import { MAX_VERSUCHE, medienRueckzugMs } from './medienAuftraege'
import { sichereMediumMitGrund } from './medienSicherung'

export { MAX_VERSUCHE } from './medienAuftraege'

export interface NachholOptionen {
  /** Die Uhr fuer Tests und fuer eine Probe; im Betrieb `Date.now`. */
  jetzt?: () => number
}

/**
 * Ein Lauf zur Zeit, und ein Anstoss waehrenddessen geht nicht verloren.
 *
 * Ohne die Sperre holten zwei gleichzeitige Anlaesse (Start und `online`)
 * denselben Anhang doppelt. Der zweite Anstoss merkt sich, dass er da war,
 * und laeuft nach dem ersten genau einmal nach — dieselbe Regel wie beim
 * Tresor-Uploader.
 */
let laeuft: Promise<void> | null = null
let nochEinmal = false

export function holeOffeneMedien(optionen: NachholOptionen = {}): Promise<void> {
  if (laeuft) {
    nochEinmal = true
    return laeuft
  }
  laeuft = (async () => {
    try {
      do {
        nochEinmal = false
        await einLauf(optionen)
      } while (nochEinmal)
    } catch {
      // Nachholen ist Hintergrundarbeit. Ein Fehler in der Ablage oder im
      // Netz darf weder den Chat aufhalten noch den naechsten Anlass.
    } finally {
      laeuft = null
    }
  })()
  return laeuft
}

async function einLauf({ jetzt = () => Date.now() }: NachholOptionen): Promise<void> {
  // Ein Hintergrundlauf gehoert dem Konto, das ihn angestossen hat. Wechselt
  // es mittendrin, endet er — sonst laedt er Anhaenge des alten Kontos in die
  // Ablage des neuen (dieselbe Regel wie beim Tresor-Uploader).
  const konto = angemeldetesKonto()
  if (konto === null) return
  const zeit = jetzt()
  for (const auftrag of await ladeMedienAuftraege()) {
    if (angemeldetesKonto() !== konto) return
    // Was noch nicht dran ist, bleibt liegen; der naechste Anlass liest wieder.
    if (auftrag.naechsterVersuch > zeit) continue

    const grund = await sichereMediumMitGrund(
      {
        mediaId: auftrag.mediaId,
        paketSchluessel: auftrag.paketSchluessel,
        fileId: auftrag.fileId,
      },
      { absenderId: auftrag.absenderId, blindMailboxId: auftrag.mailbox },
      auftrag.name,
      konto,
    )
    if (angemeldetesKonto() !== konto) return

    // Gesichert, geloescht oder endgueltig weg: der Auftrag hat sich erledigt.
    if (grund === 'gesichert' || grund === 'geloescht' || grund === 'nicht_mehr_da') {
      await loescheMedienAuftrag(auftrag.id).catch(() => {})
      continue
    }

    // Eine verbogene Zeile (kein Zaehler) wird nicht zum Dauerfeuer: sie gilt
    // als letzter Versuch und faellt danach weg.
    const bisher = Number.isInteger(auftrag.versuche) && auftrag.versuche >= 0
      ? auftrag.versuche
      : MAX_VERSUCHE - 1
    const versuche = bisher + 1
    if (versuche >= MAX_VERSUCHE) {
      await loescheMedienAuftrag(auftrag.id).catch(() => {})
      continue
    }
    await schreibeMedienAuftrag({
      ...auftrag,
      versuche,
      naechsterVersuch: zeit + medienRueckzugMs(versuche),
    }).catch(() => {})
  }
}
