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
import { umgezogeneMailbox } from './gruppenSchluessel'
import { chatMediaBlobCache } from './klartextSpeicher'
import { istEndgueltigAbgelehnt, type LoeschAuftrag } from './loeschAuftraege'
import {
  leereUmschlagKlartext,
  loescheMedium,
  loescheSteuerAuftraegeFuerZiel,
  updateMessageInLocalStore,
} from './messengerLocalStore'

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
 * Baut den dauerhaften Auftrag für das Server-Aufräumen dieser Nachricht.
 *
 * Die Medienkennungen und ihre Bindung werden **jetzt** abgelesen. Nach dem
 * lokalen Löschen ist die Zeile nur noch ein Grabstein; die Zeiger auf die
 * Blobs stehen dann ausschließlich hier. Die logische Kennung bleibt der
 * Adressat der Umschläge — eine später neu angelegte Fassung derselben
 * Nachricht trägt eine andere und wird von diesem Auftrag nicht berührt.
 *
 * Gibt `null` zurück, wenn es nichts zu räumen gibt: ohne logische Kennung und
 * ohne Anhang hat der Server keine Kopie, die dieser Auftrag ansprechen könnte.
 */
export function baueLoeschauftrag(
  blindMailboxId: string,
  msg: TilgbareNachricht & { senderId?: number },
  fremd: boolean,
  jetzt = Date.now(),
): LoeschAuftrag | null {
  if (!blindMailboxId) return null
  const clientUuid =
    typeof msg.clientUuid === 'string' && msg.clientUuid ? msg.clientUuid : null
  const nachrichtId = Number.isFinite(msg.id) ? msg.id : 0
  const absenderId = Number(msg.senderId)
  const medien = medienKennungen(msg).map((mediaId) => ({
    mediaId,
    absenderId: Number.isFinite(absenderId) ? absenderId : 0,
    mailbox: blindMailboxId,
  }))
  if (!clientUuid && medien.length === 0) return null
  const kennung = clientUuid ? `u:${clientUuid}` : `#${nachrichtId}`
  return {
    id: `${blindMailboxId}:${kennung}:${fremd ? 'fremd' : 'eigen'}`,
    mailbox: blindMailboxId,
    clientUuid,
    nachrichtId,
    medien,
    fremd,
    angelegtAm: jetzt,
    versuche: 0,
    naechsterVersuch: jetzt,
  }
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

  /*
   * Und die ausstehenden Wirkungen zu dieser Nachricht.
   *
   * Eine Bearbeitung oder Reaktion, die noch in der Outbox steht, trägt die
   * Kennung dieser Nachricht. Ginge sie nach der Löschung hinaus, lebte die
   * Nachricht bei der Gegenseite wieder auf — genau das, was eine Löschung
   * verhindern soll. Die Löschung selbst bleibt in der Warteschlange; sie muss
   * weitergehen, auch wenn sie noch nicht draußen ist.
   *
   * Ein Fehler dabei ist keiner: die Nachricht ist lokal weg, und ein
   * liegengebliebener Auftrag scheitert später an ihr.
   */
  await loescheSteuerAuftraegeFuerZiel(blindMailboxId, {
    clientUuid: msg.clientUuid,
    id: msg.id,
  })

  /*
   * Und der Anhang selbst.
   *
   * Seit 10/2026 liegt ein geholter Anhang dauerhaft in der Ablage dieses
   * Geräts (`medienSicherung.ts`). Bliebe er dort stehen, wäre die gelöschte
   * Nachricht nur im Text verschwunden, ihr Bild aber weiterhin auf diesem
   * Gerät — und die Zusage „für alle gelöscht" wäre falsch.
   *
   * `loescheMedium` nimmt in einer Transaktion den Klartext, angefangene
   * Stuecke eines Historientransfers, einen offenen Nachholauftrag und setzt
   * den Grabstein. Der Arbeitsspeicher faellt hier mit: er ist nur die
   * Optimierung ueber der Ablage.
   *
   * Der Serverteil ist eine eigene Angelegenheit: dort darf nur der Absender
   * löschen, und das steht in `tilgeNachrichtBeimServer`.
   */
  for (const mediaId of medienKennungen(msg)) {
    await loescheMedium(mediaId).catch(() => {})
    chatMediaBlobCache.delete(mediaId)
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
    for (const kennung of loeschZiele(blindMailboxId)) {
      await loescheBlindeUmschlaege(kennung, msg.clientUuid)
    }
  }
}

/**
 * Alle Mailboxen, in denen diese Nachricht liegen könnte.
 *
 * Eine Gruppe zieht in eine Kennung aus ihrem Geheimnis um, und während des
 * Umzugs liegen Nachrichten in beiden. Nur die eine zu räumen hiesse: „gelöscht"
 * anzeigen, während der Chiffretext in der anderen liegenbleibt — genau die
 * falsche Zusage, gegen die der Rest dieser Datei geschrieben ist.
 *
 * Ein Löschen in der falschen Mailbox kostet nichts: es findet die Kennung
 * nicht und meldet `deleted: 0`.
 */
function loeschZiele(blindMailboxId: string): string[] {
  const neu = umgezogeneMailbox(blindMailboxId)
  return neu ? [neu, blindMailboxId] : [blindMailboxId]
}

/**
 * Dasselbe für eine **fremde** Nachricht, die moderiert wird.
 *
 * Zwei Dinge verhalten sich hier anders, und beide haben denselben Grund: der
 * Server weiss nicht, wer welchen Umschlag geschrieben hat — absichtlich.
 *
 * 1. **Die Umschläge gehen weg.** Wer zu einer Mailbox gehört, darf darin jeden
 *    Umschlag löschen, dessen Kennung er kennt. Das ist der Preis der blinden
 *    Adressierung und hier ausnahmsweise nützlich.
 * 2. **Der Anhang bleibt womöglich liegen.** Einen hochgeladenen Blob darf nur
 *    löschen, wer ihn hochgeladen hat. Das wird nicht verschluckt, sondern
 *    zurückgemeldet: „entfernt" zu lesen, während das Bild noch abrufbar ist,
 *    wäre eine falsche Zusage über fremde Daten.
 *
 * Ein Fehlschlag am Blob hält die Umschläge nicht auf. Andersherum bliebe der
 * Text für jedes noch nicht abgeholte Gerät stehen, nur weil ein Bild nicht
 * wegging.
 */
export async function tilgeFremdeNachrichtBeimServer(
  blindMailboxId: string,
  msg: TilgbareNachricht
): Promise<{ medienGeblieben: number }> {
  let medienGeblieben = 0
  for (const mediaId of medienKennungen(msg)) {
    try {
      await loescheChatMedium(mediaId)
    } catch {
      medienGeblieben++
    }
  }

  if (blindMailboxId && msg.clientUuid) {
    for (const kennung of loeschZiele(blindMailboxId)) {
      await loescheBlindeUmschlaege(kennung, msg.clientUuid)
    }
  }
  return { medienGeblieben }
}

/**
 * Führt das Server-Aufräumen eines dauerhaften Auftrags aus.
 *
 * Nur der Server ist hier im Spiel: kein lokaler Schreibzugriff. Ein Auftrag zu
 * einer bereits gelöschten Nachricht löscht also lediglich die Serverkopie und
 * legt nichts wieder an — weder die Zeile noch einen Grabstein.
 *
 * Ein endgültig abgelehnter Blob (403: ein fremder Anhang gehört dem
 * Hochladenden) wird gezählt und hält den Rest nicht auf: der Text soll
 * verschwinden, auch wenn ein Bild bleibt. Ein flüchtiger Fehler wird
 * weitergereicht; dann bleibt der ganze Auftrag offen und wird erneut versucht.
 */
export async function raeumeServerkopieAuf(
  auftrag: LoeschAuftrag
): Promise<{ medienGeblieben: number }> {
  let medienGeblieben = 0
  for (const medium of auftrag.medien ?? []) {
    try {
      await loescheChatMedium(medium.mediaId)
    } catch (fehler) {
      if (!istEndgueltigAbgelehnt(fehler)) throw fehler
      medienGeblieben++
    }
  }

  if (auftrag.mailbox && auftrag.clientUuid) {
    for (const kennung of loeschZiele(auftrag.mailbox)) {
      await loescheBlindeUmschlaege(kennung, auftrag.clientUuid)
    }
  }
  return { medienGeblieben }
}
