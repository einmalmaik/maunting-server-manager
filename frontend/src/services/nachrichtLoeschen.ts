/**
 * Eine Nachricht löschen — und zwar überall, wo sie liegt.
 *
 * Bis 09/2026 hieß „für alle löschen": einen Steuerumschlag schicken. Die
 * Gegenseite blendete die Zeile daraufhin aus. Alles andere blieb stehen:
 *
 *  - Auf dem eigenen Gerät passierte gar nichts. Ein Ratchet-Umschlag lässt
 *    sich von seinem Absender nicht öffnen, also kam der eigene Löschbefehl bei
 *    diesem Gerät nie an. Die Nachricht stand unverändert da.
 *  - In der Ablage der Gegenseite blieb der Klartext liegen. Ausgeblendet ist
 *    nicht gelöscht: wer die IndexedDB aufmacht, liest ihn weiter.
 *  - Der Chiffretext blieb in der blinden Mailbox. Ein neu eingerichtetes Gerät
 *    holt die Mailbox von vorn und bekäme die Nachricht zurück.
 *  - Der Anhang blieb als Blob auf dem Server. Bild und Datei liegen nicht im
 *    Umschlag, sondern daneben, und jedes Chat-Mitglied kann sich dafür eine
 *    signierte URL ausstellen lassen.
 *
 * Diese Datei erledigt die vier Teile. Die Reihenfolge ist Absicht: erst das
 * Eigene, dann das Fremde. Was lokal schon weg ist, kann kein späterer Fehler
 * zurückholen; scheitert dagegen der Serverteil, steht die Nachricht hier
 * trotzdem als gelöscht und der nächste Versuch räumt den Rest.
 */

import { loescheBlindeUmschlaege, loescheChatMedium } from '@/api/social'
import { leereUmschlagKlartext, updateMessageInLocalStore } from './messengerLocalStore'

/** Ein Anhang, der auf einen hochgeladenen Blob zeigt. */
interface Medienanhang {
  mediaId?: string
}

/**
 * Das, was zum Löschen einer Nachricht gebraucht wird — von der Zeile im
 * Verlauf und von der Zeile in der Ablage gleichermaßen erfüllt.
 */
export interface TilgbareNachricht {
  id: number
  clientUuid?: string
  text: string
  isDeleted?: boolean
  deletedAt?: string
  originalText?: string
  noteAttachment?: unknown
  calendarAttachment?: unknown
  imageAttachment?: Medienanhang | null
  audioAttachment?: Medienanhang | null
  fileAttachment?: Medienanhang | null
  videoNoteAttachment?: Medienanhang | null
  stickerAttachment?: unknown
  storyReply?: unknown
  videoUrl?: string
}

/** Die Kennungen der Blobs, die zu dieser Nachricht hochgeladen wurden. */
export function medienKennungen(msg: TilgbareNachricht): string[] {
  const zeiger = [
    msg.imageAttachment,
    msg.audioAttachment,
    msg.fileAttachment,
    msg.videoNoteAttachment,
  ]
  const kennungen = new Set<string>()
  for (const z of zeiger) {
    if (z?.mediaId) kennungen.add(z.mediaId)
  }
  return [...kennungen]
}

/**
 * Die Felder eines Grabsteins: Zeitpunkt bleibt, Inhalt geht.
 *
 * Jedes Inhaltsfeld steht hier **ausdrücklich** auf `undefined`, keines fehlt.
 * `mischeVerlauf` führt die alte und die neue Fassung mit `{ ...alt, ...neu }`
 * zusammen, und ein fehlender Schlüssel ließe den alten Wert durch — der Anhang
 * wäre nach dem nächsten Abruf wieder da.
 *
 * Ein Originaltext wird nicht aufbewahrt. Gelöscht ist gelöscht.
 */
export function grabsteinFelder(geloeschtAm: string) {
  return {
    text: '',
    isDeleted: true,
    deletedAt: geloeschtAm,
    originalText: undefined,
    noteAttachment: undefined,
    calendarAttachment: undefined,
    imageAttachment: undefined,
    audioAttachment: undefined,
    fileAttachment: undefined,
    videoNoteAttachment: undefined,
    stickerAttachment: undefined,
    storyReply: undefined,
    videoUrl: undefined,
  }
}

/** Dieselbe Nachricht, nur ohne Inhalt. Kennung und Zeit bleiben unberührt. */
export function tilgeInhalt<T extends TilgbareNachricht>(msg: T, geloeschtAm: string): T {
  return { ...msg, ...grabsteinFelder(geloeschtAm) }
}

/**
 * Nimmt den Inhalt aus der Ablage dieses Geräts.
 *
 * Beides gehört zusammen: die Zeile im Verlauf **und** der abgelegte
 * Umschlag-Klartext. Der zweite ist die Fassung, aus der ein späterer Abruf die
 * Nachricht wieder aufbaut — bliebe er stehen, käme sie beim nächsten Öffnen
 * des Gesprächs zurück.
 *
 * Geleert, nicht entfernt: die Zeile ist zugleich die Marke „dieser Umschlag
 * ist geöffnet". Ohne sie liefe der Ratchet ein zweites Mal über denselben
 * Umschlag, sein Nachrichtenschlüssel ist aber verbraucht — das Ergebnis wäre
 * ein Sitzungsbruch.
 */
export async function tilgeNachrichtLokal(
  blindMailboxId: string,
  msg: TilgbareNachricht,
  geloeschtAm: string
): Promise<void> {
  if (!blindMailboxId) return

  // Nur die Inhaltsfelder als Änderung, nie die Kennung: `updateMessageInLocalStore`
  // schreibt eine Zeile unter neuem Schlüssel, sobald sich `id` unterscheidet.
  await updateMessageInLocalStore(
    blindMailboxId,
    msg.clientUuid ?? msg.id,
    grabsteinFelder(geloeschtAm)
  )

  if (msg.id > 0) {
    await leereUmschlagKlartext(blindMailboxId, msg.id)
  }
}

/**
 * Nimmt Umschläge und Anhänge vom Server.
 *
 * Nur der Absender kommt hier durch: das Löschen eines Blobs ist dem
 * Hochladenden vorbehalten, und den Knopf „für alle löschen" gibt es ohnehin
 * nur an der eigenen Nachricht.
 *
 * Fehlschläge werden gemeldet, nicht verschluckt. Wer „gelöscht" liest,
 * während der Chiffretext noch in der Mailbox liegt, glaubt etwas Falsches über
 * seine eigenen Daten.
 */
export async function tilgeNachrichtBeimServer(
  blindMailboxId: string,
  msg: TilgbareNachricht
): Promise<void> {
  for (const mediaId of medienKennungen(msg)) {
    await loescheChatMedium(mediaId)
  }

  if (blindMailboxId && msg.clientUuid) {
    await loescheBlindeUmschlaege(blindMailboxId, msg.clientUuid)
  }
}
