/**
 * Eine Empfangsquittung zurück an das Quellgerät.
 *
 * Text und Anhänge haben verschiedene Inhalte, aber denselben Weg: eine
 * signierte Nutzlast, gegen den Geräteschlüssel des Absenders versiegelt, in
 * die eigene Geräte-Mailbox gelegt. Bis 10/2026 baute nur der Anhang diesen
 * Weg selbst; die Textquittung hätte ihn ein zweites Mal gebaut und dabei
 * leicht eine andere Bindung oder ein anderes Ziel getroffen.
 *
 * Geprüft wird die Quittung beim Empfänger mit denselben Schranken wie jedes
 * andere Gerätepaket: Zielgerät, Unterschrift eines Geräts dieses Kontos und
 * Bindung an die Geräte-Mailbox. Was sie **bedeutet**, entscheidet der
 * jeweilige Typ — `history_media_ack` gibt einen Anhang frei,
 * `history_part_ack` einen Textteil, `history_done` den ganzen Verlauf.
 */

import { angemeldetesKonto } from '@/lib/angemeldetesKonto'
import { deriveUserDeviceMailboxId } from '../e2eeCrypto'
import { eigenesGeraet } from './geraeteAblage'
import { geraeteVon } from './geraeteVerzeichnis'
import { VERLAUF_DONE_TYP, VERLAUF_MEDIA_ACK_TYP, VERLAUF_PART_ACK_TYP } from './verlaufsTypen'
import { endung, kennungInOrdnung, sendeSigniertesPaket } from './verlaufsVersand'

export type QuittungsTyp =
  | typeof VERLAUF_PART_ACK_TYP
  | typeof VERLAUF_DONE_TYP
  | typeof VERLAUF_MEDIA_ACK_TYP

function quittungsKennung(
  typ: QuittungsTyp,
  quellGeraet: string,
  transferId: string,
  felder: Record<string, unknown>,
): string {
  const basis =
    typ === VERLAUF_PART_ACK_TYP
      ? 'partack'
      : typ === VERLAUF_DONE_TYP
        ? 'historydone'
        : 'mediaack'
  const mailbox = typeof felder.mailbox === 'string' ? `-${endung(felder.mailbox)}` : ''
  const teil = Number.isInteger(felder.teil) ? `-${felder.teil}` : ''
  // Die Schlussquittung darf **wiederholt** werden müssen: die Quelle nimmt
  // sie erst an, wenn jeder Teil einzeln bestätigt ist. Eine feste Kennung
  // würde die Wiederholung beim Relais als „schon abgelegt" abtun, und die
  // Quelle bekäme sie nie zu sehen. Jeder Versuch ist deshalb ein eigenes
  // Paket; doppelt ankommende Schlussquittungen sind idempotent.
  const versuch = typ === VERLAUF_DONE_TYP ? `-${Date.now()}` : ''
  return `${basis}-${endung(quellGeraet)}-${transferId.slice(0, 10)}${mailbox}${teil}${versuch}`
}

/**
 * Schickt eine Quittung. Gibt `false` zurück, wenn sie nicht hinausging —
 * etwa weil das Quellgerät nicht mehr im Verzeichnis steht. Der Aufrufer
 * wiederholt sie beim nächsten Anlass; ein Fehlschlag ist kein Fehler des
 * empfangenen Teils.
 */
export async function sendeGeraeteQuittung(
  quellGeraet: string,
  typ: QuittungsTyp,
  transferId: string,
  felder: Record<string, unknown> = {},
): Promise<boolean> {
  const konto = angemeldetesKonto()
  if (!konto || !kennungInOrdnung(quellGeraet) || !kennungInOrdnung(transferId)) return false
  try {
    const meins = await eigenesGeraet()
    if (meins.kennung === quellGeraet) return false
    const ziel = (await geraeteVon(konto)).find((g) => g.device_id === quellGeraet)
    if (!ziel?.public_key) return false
    const geraeteMailbox = await deriveUserDeviceMailboxId(konto)
    await sendeSigniertesPaket({
      konto,
      meins,
      ziel,
      mailbox: geraeteMailbox,
      typ,
      clientUuid: quittungsKennung(typ, quellGeraet, transferId, felder),
      felder: { transferId, ...felder },
    })
    return true
  } catch {
    // Kein Netz, gesperrter Schlüsselbund, Gerät verschwunden: der nächste
    // Anlass (Wiederholung des Teils oder erneutes Manifest) quittiert neu.
    return false
  }
}
