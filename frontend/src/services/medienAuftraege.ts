/**
 * Der dauerhafte Auftrag zu einem Anhang, der nicht zu holen war.
 *
 * Der Auftrag liegt in der kontogebundenen Ablage des Messengers
 * (`messengerLocalStore`, Store `medien_auftraege`) und nicht im localStorage:
 * er traegt Paketschluessel, Dateiname und Bindung, also dieselben Angaben wie
 * die Nachricht daneben. Dort gilt das Siegel des Messengers, und die Ablage
 * gehoert ohnehin einem Konto: ein Wechsel fuehrt in eine andere Datenbank,
 * nicht in dieselbe.
 *
 * Diese Datei kennt nur das Anlegen und die Wartezeiten. Das Abarbeiten steht
 * in `medienNachholen.ts`.
 */

import type { MedienZeiger } from './medienKrypto'
import { legeMedienAuftragAn } from './messengerLocalStore'

/** Nach so vielen Fehlversuchen faellt der Auftrag. */
export const MAX_VERSUCHE = 6

/**
 * Der Rueckzug zwischen zwei Versuchen, in Millisekunden.
 *
 * Der erste Versuch kommt eine Minute nach dem Fehler, danach wird der Abstand
 * groesser. Kein Dauerfeuer: ein Anhang, dessen Server gerade nicht antwortet,
 * soll nicht im Takt des offenen Chats erneut geladen werden. Nach dem letzten
 * Versuch bleibt der Anhang „nicht lokal gesichert" — das ist die ehrliche
 * Auskunft, kein stiller Erfolg.
 */
const RUECKZUG_MS = [
  60_000,
  5 * 60_000,
  30 * 60_000,
  2 * 60 * 60_000,
  12 * 60 * 60_000,
  24 * 60 * 60_000,
]

export function medienRueckzugMs(versuche: number): number {
  const stelle = Math.max(0, Math.min(RUECKZUG_MS.length - 1, Math.floor(versuche)))
  return RUECKZUG_MS[stelle]
}

/** Woran ein Anhang haengt: wer ihn schickte und in welche Mailbox. */
export interface MedienAuftragBindung {
  absenderId: number
  blindMailboxId: string
}

/**
 * Legt den Auftrag an — oder laesst den bestehenden stehen.
 *
 * `legeMedienAuftragAn` ist absichtlich idempotent: ein zweiter Fehlversuch
 * desselben Anhangs darf die Versuche nicht zuruecksetzen, sonst waere der
 * naechste Termin immer „in einer Minute" und der Anhang kaeme nie dran.
 */
export async function merkeMedienNachholauftrag(
  zeiger: MedienZeiger,
  bindung: MedienAuftragBindung,
  name?: string | null,
): Promise<void> {
  if (!zeiger?.mediaId || !bindung?.blindMailboxId) return
  const jetzt = Date.now()
  await legeMedienAuftragAn({
    id: `${bindung.blindMailboxId}:${zeiger.mediaId}`,
    mailbox: bindung.blindMailboxId,
    mediaId: zeiger.mediaId,
    paketSchluessel: zeiger.paketSchluessel,
    fileId: zeiger.fileId,
    absenderId: Number(bindung.absenderId),
    name: name ?? null,
    versuche: 0,
    naechsterVersuch: jetzt + medienRueckzugMs(0),
    angelegtAm: jetzt,
  })
}
