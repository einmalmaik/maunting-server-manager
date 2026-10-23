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

import { utf8ToBytes } from '@msdis/shield/core'
import { sha256Hex } from '@msdis/shield/integrity'

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
import { sendeZustellungen, MAX_AUFTRAEGE_JE_ANFRAGE, type ZustellEintrag, type ZustellPayload } from './e2eeZustellung'
import { speichereMedium } from './messengerLocalStore'
import {
  entferneZustellung,
  hatZustellung,
  merkeZustellstand,
} from './ratchetSpeicher'
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
 * Die Absichtsfragen dürfen den Versand nicht aufhalten.
 *
 * Fehlt die Ratchet-Ablage (Tests, ein Browser ohne IndexedDB), gibt es eben
 * keine Absichten — dann trägt die Offline-Outbox wie bisher. Ein Fehler beim
 * Merken ist dagegen ernst: er bedeutet, dass ein Absturz den Umschlag
 * verlieren könnte. Deshalb wird er hier nicht verschluckt, sondern nur die
 * Frage „gibt es eine Absicht?" mit Nein beantwortet.
 */
async function hatAbsicht(clientUuid: string): Promise<boolean> {
  return hatZustellung(clientUuid).catch(() => false)
}

/** Ein Chiffretextobjekt: derselbe Inhalt, dieselbe Kennung im ganzen Aufruf. */
interface Chiffretextobjekt {
  id: string
  ciphertext_sha256: string
}

/** Die Gerätemarke einer Kennung (`<uuid>#<geraet>`, Aufbau mit `i` davor). */
function geraeteMarke(clientUuid: string): string {
  const raute = clientUuid ? clientUuid.indexOf('#') : -1
  if (raute === -1) return ''
  const roh = clientUuid.slice(raute + 1)
  return roh.startsWith('i') ? roh.slice(1) : roh
}

/**
 * Teilt die Aufträge eines Aufrufs in Stapel, die der Server annimmt.
 *
 * Ein Sitzungsaufbau und seine Nachricht bleiben zusammen: rissen die beiden
 * an einer Stapelgrenze auseinander, könnte der Aufbau durchfallen, während
 * die Nachricht ankommt — die Gegenstelle hätte dann keinen Schlüssel für sie.
 * Mehr als `MAX_AUFTRAEGE_JE_ANFRAGE` Einträge nimmt der Server nicht an.
 */
function teileInStapel<T extends { auftrag: Versandauftrag }>(eintraege: T[]): T[][] {
  const einheiten: T[][] = []
  for (let i = 0; i < eintraege.length; i += 1) {
    const jetzt = eintraege[i]
    const naechster = eintraege[i + 1]
    const aufbau = jetzt.auftrag.is_control && jetzt.auftrag.control_type === 'dr-init'
    // Dieselbe Paarung wie im Rückfall unten: ein Sitzungsaufbau und die
    // nächste Nachricht gehören zusammen, unabhängig von ihrer Gerätemarke.
    if (aufbau && naechster && !naechster.auftrag.is_control) {
      einheiten.push([jetzt, naechster])
      i += 1
    } else {
      einheiten.push([jetzt])
    }
  }

  const stapel: T[][] = []
  let aktuell: T[] = []
  for (const einheit of einheiten) {
    if (aktuell.length > 0 && aktuell.length + einheit.length > MAX_AUFTRAEGE_JE_ANFRAGE) {
      stapel.push(aktuell)
      aktuell = []
    }
    aktuell.push(...einheit)
  }
  if (aktuell.length > 0) stapel.push(aktuell)
  return stapel
}

/**
 * Stellt die Umschläge einer Nachricht zu, oder reiht sie für später ein.
 */
export async function stelleZu(
  auftraege: Versandauftrag[],
): Promise<{ niedrigsteId: number; verbindungsfehler: boolean; ueberInbox: boolean }> {
  /**
   * Die niedrigste Umschlagkennung der Auffächerung gilt als Kennung dieser
   * Nachricht. Quittungen der Gegenstelle nennen die Kennung der Kopie, die
   * *sie* gesehen hat — also eine aus derselben Auffächerung und damit nie
   * kleinere. Der Vergleich `quittiert >= meine` trägt deshalb weiter.
   */
  let niedrigsteId = 0
  let verbindungsfehler = false
  /** Woher die Kennung der Zeile stammt — die beiden Folgen sind getrennt. */
  let idQuelle: 'inbox' | 'mailbox' | '' = ''

  /*
   * Der neue Weg zuerst: je Zielgeraet eine eigene Inbox.
   *
   * Der Chiffretext ist derselbe wie im alten Weg — nur der Ort ist ein
   * anderer. Was der Server ablehnt (Postfach voll, Geraet unbekannt) oder was
   * gar nicht ankommt, geht fuer **dieses eine Ziel** ueber die geteilte
   * Mailbox. Ein stiller Gesamt-Rueckfall gibt es nicht.
   */
  const ueberMailbox: Versandauftrag[] = []
  const nachInbox = new Map<string, Versandauftrag[]>()
  for (const auftrag of auftraege) {
    if (!auftrag.ziel_inbox) {
      ueberMailbox.push(auftrag)
      continue
    }
    const liste = nachInbox.get(auftrag.blind_mailbox_id) ?? []
    liste.push(auftrag)
    nachInbox.set(auftrag.blind_mailbox_id, liste)
  }

  /*
   * Ein Chiffretextobjekt je **Inhalt**, nicht je Auftrag.
   *
   * Eine Gruppennachricht ist fuer alle Zielgeraete derselbe Chiffretext. Ohne
   * diese Zusammenfassung laege derselbe Inhalt einmal je Geraet beim Server —
   * bei einer grossen Gruppe also zigmal dieselben Bytes. Der Schluessel ist
   * der Chiffretext selbst: zwei gleiche Werte sind dasselbe Objekt, und nur
   * innerhalb dieses Aufrufs.
   */
  const objekte = new Map<string, Chiffretextobjekt>()
  const objektFuer = async (ciphertext: string): Promise<Chiffretextobjekt> => {
    const vorhanden = objekte.get(ciphertext)
    if (vorhanden) return vorhanden
    const neu = {
      id: crypto.randomUUID(),
      ciphertext_sha256: await sha256Hex(utf8ToBytes(ciphertext)),
    }
    objekte.set(ciphertext, neu)
    return neu
  }

  for (const [mailbox, gruppe] of nachInbox) {
    const jeKennung = new Map<string, { auftrag: Versandauftrag; objekt: Chiffretextobjekt }>()
    const vorbereitet: {
      auftrag: Versandauftrag
      deliveryId: string
      objekt: Chiffretextobjekt
    }[] = []
    for (const auftrag of gruppe) {
      const deliveryId = crypto.randomUUID()
      const objekt = await objektFuer(auftrag.ciphertext_envelope)
      jeKennung.set(deliveryId, { auftrag, objekt })
      vorbereitet.push({ auftrag, deliveryId, objekt })
    }

    for (const stapel of teileInStapel(vorbereitet)) {
      /*
       * Die Kennungen stehen **vor** dem HTTP-Aufruf in der Absicht.
       *
       * Ging die Antwort verloren — Absturz, Netz weg, Zeitüberschreitung —,
       * wiederholt der nächste Lauf den Auftrag mit genau diesen Kennungen.
       * Der Server gibt dann den angenommenen Auftrag zurück, statt einen
       * zweiten anzulegen; die Gegenstelle sieht die Nachricht einmal.
       */
      for (const eintrag of stapel) {
        await merkeZustellstand(eintrag.auftrag.client_uuid, {
          zustellung: { payloadId: eintrag.objekt.id, deliveryId: eintrag.deliveryId },
        }).catch(() => {})
      }
      const payloads = new Map<string, ZustellPayload>()
      const eintraege: ZustellEintrag[] = []
      for (const eintrag of stapel) {
        payloads.set(eintrag.objekt.id, {
          id: eintrag.objekt.id,
          ciphertext: eintrag.auftrag.ciphertext_envelope,
          ciphertext_sha256: eintrag.objekt.ciphertext_sha256,
        })
        eintraege.push({
          delivery_id: eintrag.deliveryId,
          payload_id: eintrag.objekt.id,
          inbox_hash: String(eintrag.auftrag.ziel_inbox),
        })
      }
      try {
        const antwort = await sendeZustellungen(mailbox, [...payloads.values()], eintraege)
        for (const an of antwort.angenommen) {
          const eintrag = jeKennung.get(an.delivery_id)
          if (!eintrag) continue
          // Angenommen heißt erledigt — die Absicht fällt.
          await entferneZustellung(eintrag.auftrag.client_uuid).catch(() => {})
          if (eintrag.auftrag.is_control || !(an.seq > 0)) continue
          if (niedrigsteId === 0 || an.seq < niedrigsteId) {
            niedrigsteId = an.seq
            idQuelle = 'inbox'
          }
        }
        for (const ab of antwort.abgelehnt) {
          const eintrag = jeKennung.get(ab.delivery_id)
          if (!eintrag) continue
          // Dieses Gerät nimmt den neuen Weg nicht (mehr). Der Umschlag bleibt
          // gültig; die Absicht merkt sich, dass nur noch die Mailbox zählt.
          await merkeZustellstand(eintrag.auftrag.client_uuid, {
            zustellung: null,
            nurMailbox: true,
          }).catch(() => {})
          ueberMailbox.push(eintrag.auftrag)
        }
      } catch (fehler) {
        /*
         * Ein Netzfehler ist **kein** Grund, auf den anderen Weg zu wechseln:
         * die Annahme kann beim Server längst stehen. Die Absicht bleibt mit
         * ihren Kennungen liegen und wird bytegleich wiederholt. Nur ein harter
         * Fehler (der Server kennt diesen Weg nicht) stellt um.
         */
        const status = statusVon(fehler)
        const hart = status === 400 || status === 404
        for (const eintrag of stapel) {
          const absichtOffen = await hatAbsicht(eintrag.auftrag.client_uuid)
          if (absichtOffen && !hart) {
            verbindungsfehler = true
            continue
          }
          if (absichtOffen) {
            await merkeZustellstand(eintrag.auftrag.client_uuid, {
              zustellung: null,
              nurMailbox: true,
            }).catch(() => {})
          }
          ueberMailbox.push(eintrag.auftrag)
        }
      }
    }
  }

  const gescheiterteGeraete = new Set<string>()
  let ueberspringeNaechsteNachricht = false

  for (const auftrag of ueberMailbox) {
    // H-6: Paarbildung im Sendepfad. Scheitert ein Auftrag (z. B. dr-init),
    // darf die zugehörige Ratchet-Nachricht desselben Zielgeräts nicht gesendet
    // werden, sondern muss ebenfalls eingereiht werden, um Sitzungsbrüche zu verhindern.
    const geraetKey = geraeteMarke(auftrag.client_uuid)

    const mussUeberspringen =
      (ueberspringeNaechsteNachricht && !auftrag.is_control) ||
      Boolean(geraetKey && gescheiterteGeraete.has(geraetKey))

    if (mussUeberspringen) {
      // Liegt eine dauerhafte Absicht, wiederholt der nächste Lauf sie
      // zusammen mit dem Aufbau. Sie zusätzlich in die Offline-Outbox zu
      // legen liesse beide Wege dasselbe senden.
      if (!(await hatAbsicht(auftrag.client_uuid))) enqueueMessageMutation(auftrag)
      ueberspringeNaechsteNachricht = false
      continue
    }

    try {
      const r = await relayE2eeEnvelope(auftrag)
      // Angenommen: die Absicht hat ihren Zweck erfüllt.
      await entferneZustellung(auftrag.client_uuid).catch(() => {})
      if (!auftrag.is_control && r && typeof r.id === 'number') {
        if (niedrigsteId === 0 || r.id < niedrigsteId) {
          niedrigsteId = r.id
          idQuelle = 'mailbox'
        }
      }
    } catch {
      if (geraetKey) {
        gescheiterteGeraete.add(geraetKey)
      }
      if (auftrag.is_control && auftrag.control_type === 'dr-init') {
        ueberspringeNaechsteNachricht = true
      }
      /*
       * Eine dauerhafte Absicht ist die bessere Warteschlange: sie trägt den
       * exakten Umschlag und wird ohne Zutun wiederholt. Nur was keine hat
       * (Gruppenumschläge), geht in die Offline-Outbox.
       */
      if (!(await hatAbsicht(auftrag.client_uuid))) enqueueMessageMutation(auftrag)
      verbindungsfehler = true
    }
  }

  return { niedrigsteId, verbindungsfehler, ueberInbox: idQuelle === 'inbox' }
}
