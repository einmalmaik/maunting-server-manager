/**
 * Die eigenständigen Schritte beim Senden einer Nachricht.
 *
 * `handleSendMessage` in `Messenger.tsx` führt Regie: optimistische Zeile,
 * Eingabefeld, Rücknahme und Meldungen, wenn etwas scheitert. Was davon nicht
 * an der Oberfläche hängt, steht hier und lässt sich ohne die Seite prüfen:
 * Anhänge verschlüsseln und hochladen, die Nutzlast bauen, die Umschläge
 * zustellen.
 *
 * Bis 09/2026 stand das alles in der Seite. Beim Umzug ist der Code wortgleich
 * geblieben; die Namen im Inneren sind die der Seite.
 */

import { ladeAnhangHoch, relayE2eeEnvelope, sendMessageQueue, type QueueEnvelope } from '@/api/social'
import type {
  AntwortBezug,
  CalendarAttachment,
  NoteAttachment,
  StickerAttachment,
  StoryReplyAttachment,
} from '@/components/social/ChatMessageBubble'
import type {
  AudioAttachment,
  FileAttachment,
  ImageAttachment,
  VideoNoteAttachment,
} from '@/components/social/ChatMediaAttachments'
import type { VideoNoteAufnahme } from '@/components/social/CircularVideoNoteRecorder'
import type { Versandauftrag } from '@/hooks/useKonversation'
import type { AugenblickMarke, RettungsMarke } from './funkenService'
import { enqueueMessageMutation } from '@/lib/offlineSync'
import { chatMediaBlobCache } from './klartextSpeicher'
import { eigenesGeraet } from './e2eeGeraet'
import { speichereMedium } from './messengerLocalStore'
/**
 * Legt den Anhang zusätzlich dauerhaft ab.
 *
 * Der Arbeitsspeicher allein reicht nicht: bis 10/2026 lag ein gesendetes Bild
 * nur im RAM und war nach dem Neuladen weg, obwohl der Server es noch 30 Tage
 * hält. Der lokale Bestand ist die Zusage, und der Arbeitsspeicher ist nur die
 * Optimierung darüber.
 *
 * Scheitert das Ablegen, geht die Nachricht trotzdem raus: der Anhang liegt
 * beim Server, und der Empfänger holt ihn sich von dort.
 */
async function sichereLokal(
  mediaId: string,
  klartext: string,
  name?: string,
): Promise<void> {
  try {
    await speichereMedium(mediaId, klartext, name ?? null)
  } catch {
    // Kein Grund, das Senden abzubrechen.
  }
}

/** Macht aus einer Aufnahme die Zeichenkette, die `medienKrypto` verschlüsselt. */
function blobAlsDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const leser = new FileReader()
    leser.onload = () => resolve(String(leser.result || ''))
    leser.onerror = () => reject(leser.error ?? new Error('Aufnahme nicht lesbar'))
    leser.readAsDataURL(blob)
  })
}

/** Was an Anhängen aus dem Eingabefeld kommt, noch unverschlüsselt. */
export interface RoheAnhaenge {
  img?: ImageAttachment
  file?: FileAttachment
  audio?: AudioAttachment
  videoNote?: VideoNoteAufnahme
}

/** Woran die Anhänge gebunden werden: Gespräch und Absender. */
export interface AnhangBindung {
  blindMailboxId: string
  absenderId: number
  groupId?: number | null
}

/** Was nach dem Hochladen in die Nutzlast geht. */
export interface HochgeladeneAnhaenge {
  finalImg?: ImageAttachment
  finalFile?: FileAttachment
  finalAudio?: AudioAttachment
  finalVideoNote?: VideoNoteAttachment
}

/**
 * Verschlüsselt die Anhänge auf diesem Gerät und lädt sie hoch.
 *
 * Bild und Datei haben einen Ersatz ohne Blob (nur der Name), Ton und
 * Videonotiz nicht: scheitert deren Upload, wirft diese Funktion.
 */
export async function ladeAnhaengeHoch(
  { img, file, audio, videoNote }: RoheAnhaenge,
  bindung: AnhangBindung,
): Promise<HochgeladeneAnhaenge> {
  const {
    blindMailboxId: targetBlindMailboxId,
    absenderId: currentUserId,
    groupId: currentGroupId,
  } = bindung

  let finalImg: ImageAttachment | undefined = undefined
  let finalFile: FileAttachment | undefined = undefined
  let finalAudio: AudioAttachment | undefined = undefined
  let finalVideoNote: VideoNoteAttachment | undefined = undefined

  /**
   * Verschlüsselt einen Anhang auf diesem Gerät und lädt ihn hoch.
   *
   * Mailbox und Absender gehen als Bindung mit ein: ein Blob, den jemand in
   * ein anderes Gespräch umhängt, scheitert beim Empfänger am Tag. Deshalb
   * steht der Upload hier und nicht schon beim Aufnehmen — dort ist noch
   * nicht klar, wohin die Aufnahme geht.
   */
  const anhangHochladen = (klartext: string, dateiname: string, mimeType: string) =>
    ladeAnhangHoch({
      klartext,
      dateiname,
      mimeType,
      blindMailboxId: targetBlindMailboxId,
      absenderId: currentUserId,
      groupId: currentGroupId,
    })

  if (img) {
    if (img.mediaId) {
      finalImg = {
        mediaId: img.mediaId,
        paketSchluessel: img.paketSchluessel,
        fileId: img.fileId,
        name: img.name,
      }
    } else if (img.dataUrl) {
      try {
        const mimeType = img.dataUrl.split(';')[0]?.replace('data:', '') || 'image/png'
        const zeiger = await anhangHochladen(img.dataUrl, img.name || 'bild.png', mimeType)
        chatMediaBlobCache.set(zeiger.mediaId, img.dataUrl)
        await sichereLokal(zeiger.mediaId, img.dataUrl, img.name)
        finalImg = { ...zeiger, name: img.name }
      } catch {
        finalImg = { name: img.name }
      }
    }
  }

  if (file) {
    if (file.mediaId) {
      finalFile = {
        mediaId: file.mediaId,
        paketSchluessel: file.paketSchluessel,
        fileId: file.fileId,
        name: file.name,
        sizeBytes: file.sizeBytes,
        mimeType: file.mimeType,
      }
    } else if (file.dataUrl) {
      try {
        const zeiger = await anhangHochladen(
          file.dataUrl,
          file.name || 'anhang.bin',
          file.mimeType || 'application/octet-stream'
        )
        chatMediaBlobCache.set(zeiger.mediaId, file.dataUrl)
        await sichereLokal(zeiger.mediaId, file.dataUrl, file.name)
        finalFile = {
          ...zeiger,
          name: file.name,
          sizeBytes: file.sizeBytes,
          mimeType: file.mimeType,
        }
      } catch {
        finalFile = { name: file.name, sizeBytes: file.sizeBytes, mimeType: file.mimeType }
      }
    }
  }

  // Ton und Videonotiz haben keinen Ersatz ohne Blob: eine Sprachnachricht
  // ohne Aufnahme wäre eine leere Zeile. Scheitert der Upload, scheitert das
  // Senden, und der catch-Zweig nimmt die Nachricht wieder aus dem Verlauf.
  if (audio?.dataUrl) {
    const zeiger = await anhangHochladen(
      audio.dataUrl,
      'sprachnachricht.webm',
      audio.mimeType || 'audio/webm'
    )
    chatMediaBlobCache.set(zeiger.mediaId, audio.dataUrl)
    await sichereLokal(zeiger.mediaId, audio.dataUrl, 'sprachnachricht.webm')
    finalAudio = {
      ...zeiger,
      durationSeconds: audio.durationSeconds,
      mimeType: audio.mimeType,
    }
  }

  if (videoNote) {
    const dataUrl = await blobAlsDataUrl(videoNote.blob)
    const zeiger = await anhangHochladen(dataUrl, 'videonotiz.webm', videoNote.mimeType)
    chatMediaBlobCache.set(zeiger.mediaId, dataUrl)
    await sichereLokal(zeiger.mediaId, dataUrl, 'videonotiz.webm')
    finalVideoNote = {
      ...zeiger,
      durationSeconds: videoNote.durationSeconds,
      width: videoNote.width,
      height: videoNote.height,
      mimeType: videoNote.mimeType,
    }
  }

  return { finalImg, finalFile, finalAudio, finalVideoNote }
}

export interface Nutzlastangaben extends HochgeladeneAnhaenge {
  clientUuid: string
  absenderId: number
  absenderName: string
  text: string
  /** Wann die Nachricht abgeschickt wurde, als ISO-Zeitpunkt. */
  zeitpunkt: string
  bezug?: AntwortBezug | null
  weitergeleitet?: boolean
  erwaehnt: { erwaehnungen?: number[]; erwaehntAlle?: boolean }
  verfaelltAm?: string
  note?: NoteAttachment
  cal?: CalendarAttachment
  sticker?: StickerAttachment
  storyReply?: StoryReplyAttachment
  /** Ein Augenblick für den Funken. */
  augenblick?: AugenblickMarke
  /** Eine Wiederherstellung des Funkens. */
  funkenRettung?: RettungsMarke
}

/**
 * Die Nutzlast einer Nachricht, bevor sie unterschrieben wird.
 *
 * Die Reihenfolge der Felder ist die, in der sie immer standen.
 */
export function baueNutzlast(angaben: Nutzlastangaben): Record<string, unknown> {
  const {
    clientUuid,
    absenderId: currentUserId,
    absenderName,
    text: rawText,
    zeitpunkt,
    bezug,
    weitergeleitet,
    erwaehnt,
    verfaelltAm,
    note,
    cal,
    sticker,
    storyReply,
    finalImg,
    finalFile,
    finalAudio,
    finalVideoNote,
    augenblick,
    funkenRettung,
  } = angaben

  const payloadObj: Record<string, unknown> = {
    client_uuid: clientUuid,
    sender_id: currentUserId,
    sender_name: absenderName,
    text: rawText,
    timestamp: zeitpunkt,
    // Nur setzen, was es gibt: ein Umschlag voller `undefined` kostet
    // Bytes, und jedes Byte reist verschlüsselt mit.
    ...(bezug ? { antwort_auf: bezug } : {}),
    ...(weitergeleitet ? { weitergeleitet: true } : {}),
    ...(erwaehnt.erwaehnungen?.length ? { erwaehnungen: erwaehnt.erwaehnungen } : {}),
    ...(erwaehnt.erwaehntAlle ? { erwaehnt_alle: true } : {}),
    ...(verfaelltAm ? { verfaellt_am: verfaelltAm } : {}),
  }

  if (note) payloadObj.note_attachment = note
  if (cal) payloadObj.calendar_attachment = cal
  if (finalImg) payloadObj.image_attachment = finalImg
  if (finalAudio) payloadObj.audio_attachment = finalAudio
  if (finalFile) payloadObj.file_attachment = finalFile
  if (sticker) payloadObj.sticker_attachment = sticker
  if (storyReply) payloadObj.story_reply = storyReply
  if (finalVideoNote) payloadObj.video_note_attachment = finalVideoNote
  if (augenblick) payloadObj.augenblick = augenblick
  if (funkenRettung) payloadObj.funken_rettung = funkenRettung

  return payloadObj
}

/** Der HTTP-Status eines Fehlers, sofern er einen trägt. */
function statusVon(fehler: unknown): number | null {
  const status = (fehler as { status?: unknown } | null)?.status
  return typeof status === 'number' ? status : null
}

/**
 * Stellt die Umschläge einer Nachricht zu, oder reiht sie für später ein.
 */
export async function stelleZu(
  auftraege: Versandauftrag[],
): Promise<{ niedrigsteId: number; verbindungsfehler: boolean; ueberInbox: boolean }> {
  if (auftraege.length === 0) {
    return { niedrigsteId: 0, verbindungsfehler: false, ueberInbox: true }
  }

  const erster = auftraege[0]
  if (erster && erster.chat_type && erster.chat_id) {
    const baseUuid = erster.client_uuid.split('#')[0]
    try {
      const ownDevice = await eigenesGeraet().catch(() => null)
      const envelopes: QueueEnvelope[] = auftraege.map((a) => ({
        target_device_id: a.target_device_db_id,
        device_id: a.target_device_id,
        encrypted_payload: a.ciphertext_envelope,
      }))

      await sendMessageQueue({
        client_uuid: baseUuid,
        chat_type: erster.chat_type,
        chat_id: erster.chat_id,
        envelopes,
        device_id: ownDevice?.kennung,
      })
      return { niedrigsteId: 1, verbindungsfehler: false, ueberInbox: true }
    } catch (fehler) {
      const status = statusVon(fehler)
      if (!status || status >= 500) {
        for (const a of auftraege) {
          enqueueMessageMutation(a)
        }
        return { niedrigsteId: 0, verbindungsfehler: true, ueberInbox: true }
      }
      throw fehler
    }
  }

  // Fallback für Mailbox-Altsysteme / Tests:
  let niedrigsteId = 0
  let verbindungsfehler = false
  const gescheiterteGeraete = new Set<string>()
  let ueberspringeNaechsteNachricht = false

  for (const a of auftraege) {
    const geraetKey = a.client_uuid ? a.client_uuid.split('#')[1]?.replace(/^i/, '') : ''
    const mussUeberspringen =
      (ueberspringeNaechsteNachricht && !a.is_control) ||
      Boolean(geraetKey && gescheiterteGeraete.has(geraetKey))

    if (mussUeberspringen) {
      enqueueMessageMutation(a)
      ueberspringeNaechsteNachricht = false
      continue
    }

    try {
      const r = await relayE2eeEnvelope(a)
      if (!a.is_control && r && typeof r.id === 'number') {
        if (niedrigsteId === 0 || r.id < niedrigsteId) {
          niedrigsteId = r.id
        }
      }
    } catch {
      if (geraetKey) gescheiterteGeraete.add(geraetKey)
      if (a.is_control && a.control_type === 'dr-init') {
        ueberspringeNaechsteNachricht = true
      }
      enqueueMessageMutation(a)
      verbindungsfehler = true
    }
  }
  return { niedrigsteId, verbindungsfehler, ueberInbox: false }
}
