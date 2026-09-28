/**
 * Die angeheftete Nachricht einer Gruppe.
 *
 * ## Die Schranke sitzt beim Empfänger
 *
 * Wie bei `@everyone`: der Server sieht den Umschlag, nicht den Inhalt, und
 * kann deshalb nicht prüfen, wer was angeheftet hat. Also prüft das empfangende
 * Gerät, ob der Anheftende `pin_messages` hatte — anhand der Marke, die der
 * Server je Mitglied ausrechnet. Ohne das Recht bleibt der Umschlag folgenlos,
 * und es erscheint keine Leiste.
 *
 * ## Warum ein Zeitpunkt danebensteht
 *
 * Derselbe Grund wie bei der Verfallsfrist: die Mailbox liefert dieselben 100
 * Umschläge bei jedem Abruf erneut. Ohne Zeitpunkt würde ein Lösen Sekunden
 * später vom alten Anheften wieder überrollt. Wer zuletzt gehandelt hat,
 * gewinnt.
 *
 * ## Warum das hier liegt und nicht in der Zeile
 *
 * Eine Anheftung gilt für den Chat, nicht für eine Nachricht. Sie steht neben
 * den Stummschaltungen: dass in einem Chat etwas angeheftet ist, ist dieselbe
 * Klasse Metadatum. Der Inhalt bleibt, wo er ist.
 */

import type { ChatGroupItem } from '@/api/social'
import { zeitAlsZahl } from './nachrichtBezug'

const SCHLUESSEL = 'msm:chat_pinned_message'

export interface Anheftung {
  /** Die logische Kennung der angehefteten Nachricht, leer heißt „keine". */
  clientUuid: string
  /** ISO-Zeitpunkt der letzten Änderung. */
  stand: string
}

function lies(): Record<string, Anheftung> {
  try {
    const roh = localStorage.getItem(SCHLUESSEL)
    const gelesen = roh ? JSON.parse(roh) : {}
    return gelesen && typeof gelesen === 'object' ? gelesen : {}
  } catch {
    return {}
  }
}

export function anheftung(blindMailboxId: string): Anheftung {
  const eintrag = lies()[blindMailboxId]
  if (!eintrag || typeof eintrag !== 'object') return { clientUuid: '', stand: '' }
  return {
    clientUuid: String(eintrag.clientUuid || ''),
    stand: String(eintrag.stand || ''),
  }
}

/**
 * Auch das Lösen wird geschrieben, nicht gelöscht.
 *
 * Ein fehlender Eintrag hätte keinen Zeitpunkt und verlöre gegen jedes ältere
 * Anheften, das noch im Mailbox-Fenster liegt.
 */
export function setzeAnheftung(
  blindMailboxId: string,
  clientUuid: string,
  stand: string = new Date().toISOString(),
): void {
  if (!blindMailboxId) return
  const alle = lies()
  alle[blindMailboxId] = { clientUuid, stand }
  try {
    localStorage.setItem(SCHLUESSEL, JSON.stringify(alle))
  } catch {
    // Ohne localStorage gilt die Anheftung eben nur für diese Sitzung.
  }
}

/**
 * Eine fremde Anheftung übernehmen.
 *
 * `true` heißt: dadurch hat sich etwas geändert. Nur dann lohnt es, die Leiste
 * neu zu setzen — derselbe Umschlag kommt bei jedem Abruf erneut vorbei.
 */
export function uebernehmeAnheftung(
  blindMailboxId: string,
  clientUuid: string,
  stand: string,
): boolean {
  const bisher = anheftung(blindMailboxId)
  if (zeitAlsZahl(stand) <= zeitAlsZahl(bisher.stand)) return false
  setzeAnheftung(blindMailboxId, clientUuid, stand)
  return bisher.clientUuid !== clientUuid
}

/**
 * Ob dieses Mitglied anheften durfte.
 *
 * Die Antwort kommt aus der Gruppenantwort des Servers, nicht aus einer hier
 * nachgebauten Rollenlogik. Fehlt die Marke, gilt **nein**: eine ausbleibende
 * Leiste ist der sichere Ausgang, eine unberechtigte nicht.
 */
export function durfteAnheften(
  gruppe: Pick<ChatGroupItem, 'members'> | null | undefined,
  absenderId: number,
): boolean {
  if (!gruppe?.members) return false
  const m = gruppe.members.find((x) => Number(x.user_id) === Number(absenderId))
  return Boolean(m?.can_pin_messages)
}
