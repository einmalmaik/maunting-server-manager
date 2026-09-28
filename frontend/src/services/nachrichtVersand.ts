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

import { ladeAnhangHoch, relayE2eeEnvelope } from '@/api/social'
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

/**
 * Stellt die Umschläge einer Nachricht zu, oder reiht sie für später ein.
 */
export async function stelleZu(
  auftraege: Versandauftrag[],
): Promise<{ niedrigsteId: number; verbindungsfehler: boolean }> {
  /**
   * Die niedrigste Umschlagkennung der Auffächerung gilt als Kennung dieser
   * Nachricht. Quittungen der Gegenstelle nennen die Kennung der Kopie, die
   * *sie* gesehen hat — also eine aus derselben Auffächerung und damit nie
   * kleinere. Der Vergleich `quittiert >= meine` trägt deshalb weiter.
   */
  let niedrigsteId = 0
  let verbindungsfehler = false
  const gescheiterteGeraete = new Set<string>()
  let ueberspringeNaechsteNachricht = false

  for (const auftrag of auftraege) {
    // H-6: Paarbildung im Sendepfad. Scheitert ein Auftrag (z. B. dr-init),
    // darf die zugehörige Ratchet-Nachricht desselben Zielgeräts nicht gesendet
    // werden, sondern muss ebenfalls eingereiht werden, um Sitzungsbrüche zu verhindern.
    const raute = auftrag.client_uuid ? auftrag.client_uuid.indexOf('#') : -1
    const rawSuffix = raute !== -1 ? auftrag.client_uuid.slice(raute + 1) : ''
    const geraetKey = rawSuffix.startsWith('i') ? rawSuffix.slice(1) : rawSuffix

    const mussUeberspringen =
      (ueberspringeNaechsteNachricht && !auftrag.is_control) ||
      Boolean(geraetKey && gescheiterteGeraete.has(geraetKey))

    if (mussUeberspringen) {
      enqueueMessageMutation(auftrag)
      ueberspringeNaechsteNachricht = false
      continue
    }

    try {
      const r = await relayE2eeEnvelope(auftrag)
      if (!auftrag.is_control && r && typeof r.id === 'number') {
        if (niedrigsteId === 0 || r.id < niedrigsteId) niedrigsteId = r.id
      }
    } catch {
      if (geraetKey) {
        gescheiterteGeraete.add(geraetKey)
      }
      if (auftrag.is_control && auftrag.control_type === 'dr-init') {
        ueberspringeNaechsteNachricht = true
      }
      enqueueMessageMutation(auftrag)
      verbindungsfehler = true
    }
  }

  return { niedrigsteId, verbindungsfehler }
}
