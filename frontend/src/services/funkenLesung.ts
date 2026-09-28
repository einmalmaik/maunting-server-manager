/**
 * Augenblicke aus gelesenen Umschlägen, ohne das Gespräch zu öffnen.
 *
 * Für `FunkenWache`: das Abzeichen in der Chatliste soll springen, sobald die
 * Gegenseite einen Augenblick schickt, nicht erst beim Öffnen des Chats. Der
 * offene Chat nimmt seine Augenblicke aus `werteUmschlaegeAus`; hier steht
 * nur das Nötigste, mit derselben Absenderprüfung — ein Funke zählt nur mit
 * Beleg, wer ihn geschickt hat.
 *
 * Strenger als der Verlauf: ohne Unterschrift **und** ohne Ratchet zählt ein
 * Augenblick hier nicht. Der Verlauf zeigt so eine Nachricht noch der
 * Gegenseite zu, solange sie nicht unterschreiben kann; für den Funken lohnt
 * die Ausnahme nicht.
 */

import type { Lesung } from '@/hooks/useKonversation'
import { kontoNutztSignaturen } from './e2eeGeraet'
import {
  ereignisAusNachricht,
  leseAugenblickMarke,
  leseRettungsMarke,
  type FunkenEreignis,
} from './funkenService'
import { istSteuerpaket } from './nachrichtBezug'
import { pruefeNutzlast } from './nutzlastSignatur'
import { logischeUuid } from './ratchetSitzung'

export interface AugenblickLesart {
  /** Die Mailbox, an die die Unterschrift gebunden ist. */
  mid: string
  ich: number
  partner: number
}

export async function werteAugenblickeAus(
  gelesen: readonly Lesung[],
  { mid, ich, partner }: AugenblickLesart,
): Promise<FunkenEreignis[]> {
  const raus: FunkenEreignis[] = []
  for (const lesung of gelesen) {
    if (lesung.art !== 'klartext' || !lesung.text) continue
    let paket: Record<string, unknown>
    try {
      paket = JSON.parse(lesung.text)
    } catch {
      continue
    }
    if (!paket || typeof paket !== 'object' || istSteuerpaket(paket.type)) continue
    if (!paket.augenblick && !paket.funken_rettung) continue

    try {
      const beleg = await pruefeNutzlast(mid, paket)
      if (beleg.art === 'gefaelscht') continue
      const ratchet = lesung.vonKonto === undefined ? undefined : Number(lesung.vonKonto)
      let von: number
      if (beleg.art === 'geprueft') {
        if (ratchet !== undefined && ratchet !== beleg.vonKonto) continue
        von = beleg.vonKonto
      } else if (ratchet !== undefined) {
        // Wer unterschreiben kann, muss es auch — dieselbe Schranke wie im Verlauf.
        if (await kontoNutztSignaturen(ratchet)) continue
        von = ratchet
      } else {
        continue
      }
      if (paket.sender_id !== undefined && Number(paket.sender_id) !== von) continue

      const e = ereignisAusNachricht(
        {
          clientUuid:
            typeof paket.client_uuid === 'string' && paket.client_uuid
              ? paket.client_uuid
              : logischeUuid(lesung.env.client_uuid),
          senderId: von,
          createdAt: lesung.env.created_at,
          augenblick: leseAugenblickMarke(paket.augenblick),
          funkenRettung: paket.augenblick ? undefined : leseRettungsMarke(paket.funken_rettung),
        },
        ich,
        partner,
      )
      if (e) raus.push(e)
    } catch {
      // Eine Prüfung, die nicht zu Ende kommt, ist keine bestandene.
    }
  }
  return raus
}
