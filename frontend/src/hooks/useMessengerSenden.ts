/**
 * Senden, Bearbeiten und Löschen — die drei Wege, die den Verlauf verändern.
 *
 * Der Block stand bis 10/2026 mitten in `Messenger.tsx`. Herausgelöst ist er
 * **unverändert**: dieselben Riegel gegen Doppelklicks, dieselbe Reihenfolge
 * von Gegenseite, Server und lokalem Speicher, dieselben Fehlertexte. Die
 * Seite reicht nur Zustand und Rückrufe hinein.
 *
 * Was hier bewusst **nicht** liegt: das Verfassen (Eingabefeld, Anhänge,
 * Vorschau) und das Weiterleiten. Beide brauchen die sichtbare Oberfläche und
 * rufen `handleSendMessage` mit einem ausdrücklichen Ziel auf.
 */

import type { Dispatch, SetStateAction } from 'react'

import type { ChatGroupItem } from '@/api/social'
import type {
  AntwortBezug,
  CalendarAttachment,
  ChatMessage,
  NoteAttachment,
  StickerAttachment,
  StoryReplyAttachment,
} from '@/components/social/ChatMessageBubble'
import type { VideoNoteAufnahme } from '@/components/social/CircularVideoNoteRecorder'
import type {
  AudioAttachment,
  FileAttachment,
  ImageAttachment,
} from '@/components/social/ChatMediaAttachments'
import type { ChatContact } from '@/components/social/sidebar/ConversationListItem'
import type { Konversation } from '@/hooks/useKonversation'
import type { E2eeIdentity } from '@/services/e2eeIdentity'
import type { Erwaehnungsvorschlag } from '@/services/erwaehnungen'
import type { getOutbox } from '@/lib/offlineSync'
import { sendTypingSignal } from '@/api/social'
import { maxAnhangBytes } from '@/services/medienKrypto'
import { baueVersandFuer } from '@/hooks/useKonversation'
import {
  logischeUuid,
  DrGeraetNichtEingetragenError,
  DrZustellungFehlgeschlagenError,
} from '@/services/ratchetSitzung'
import {
  resolveIdentity,
  forgetRecipientPublicKey,
  E2eeRecipientKeyMissingError,
} from '@/services/e2eeIdentity'
import { meldeErrungenschaft } from '@/lib/errungenschaft'
import { signiereNutzlast } from '@/services/nutzlastSignatur'
import {
  entferneLokaleNachricht,
  saveLocalMessages,
  sortMessagesChronologically,
  updateMessageInLocalStore,
} from '@/services/messengerLocalStore'
import {
  tilgeInhalt,
  tilgeNachrichtBeimServer,
  tilgeFremdeNachrichtBeimServer,
  tilgeNachrichtLokal,
} from '@/services/nachrichtLoeschen'
import { sessionChatCache } from '@/services/klartextSpeicher'
import { partnerKennungen } from '@/services/quittungsstand'
import { vergissMailbox } from '@/services/verlaufSuche'
import { findeErwaehnungen } from '@/services/erwaehnungen'
import { verfaelltAm as berechneVerfall } from '@/services/nachrichtVerfall'
import { baueNutzlast, ladeAnhaengeHoch, stelleZu } from '@/services/nachrichtVersand'
import {
  baueAugenblickNutzlast,
  baueWiederherstellungNutzlast,
  ereignisAusNachricht,
  rettungMoeglich,
  type AugenblickMarke,
  type FunkenEreignis,
  type RettungsMarke,
} from '@/services/funkenService'
import { useFunkenStore } from '@/stores/funkenStore'
import { toast } from '@/stores/toastStore'

/** Was gesendet werden soll. */
export interface SendeAuftrag {
  /** Ohne Angabe wird genommen, was im Eingabefeld steht. */
  text?: string
  note?: NoteAttachment
  cal?: CalendarAttachment
  img?: ImageAttachment
  audio?: AudioAttachment
  file?: FileAttachment
  sticker?: StickerAttachment
  storyReply?: StoryReplyAttachment
  videoNote?: VideoNoteAufnahme
  videoUrl?: string
  /** Worauf geantwortet wird. Ohne Angabe gilt, was gerade im Zitatkopf steht. */
  antwortAuf?: AntwortBezug | null
  /** Setzt die Marke „Weitergeleitet" über der Blase. */
  weitergeleitet?: boolean
  /** Ein anderes Ziel als der offene Chat — fürs Weiterleiten. */
  ziel?: { blindMailboxId: string; recipientId?: number | null; groupId?: number | null }
  /** Das Bild ist ein Augenblick und zählt für den Funken. Nur unter Freunden. */
  augenblick?: boolean
  /** Stellt den erloschenen Funken mit der Gegenseite wieder her. */
  funkenRettung?: boolean
}

/**
 * Der Kontoschlüssel ist auf diesem Gerät nicht zu öffnen — nicht gesendet.
 *
 * Der Text hier ist für den Entwickler, nicht für die Oberfläche: die
 * Fangstelle unten schreibt ihre eigene, übersetzte Meldung.
 */
class E2eeIdentityLockedError extends Error {
  constructor() {
    super('Der Schlüssel dieses Kontos ist auf diesem Gerät gesperrt.')
    this.name = 'E2eeIdentityLockedError'
  }
}

/**
 * Das Foto eines Augenblicks kam nicht auf den Server — nicht gesendet.
 *
 * Ein gewöhnliches Bild fiele auf eine Zeile nur mit Dateinamen zurück. Ein
 * Augenblick ohne Bild wäre aber ein Funke für nichts.
 */
class AugenblickNichtHochgeladenError extends Error {
  constructor() {
    super('Das Foto des Augenblicks wurde nicht hochgeladen.')
    this.name = 'AugenblickNichtHochgeladenError'
  }
}

interface Optionen {
  t: (schluessel: string, werte?: Record<string, unknown>) => string
  currentUserId: number
  user: { username?: string | null } | null
  activeContact: ChatContact | null
  activeGroup: ChatGroupItem | null
  groups: ChatGroupItem[]
  blindMailboxId: string
  darfSchreiben: boolean
  darfAnhaengen: boolean
  darfFremdeLoeschen: boolean
  editingMessage: ChatMessage | null
  setEditingMessage: Dispatch<SetStateAction<ChatMessage | null>>
  inputText: string
  setInputText: Dispatch<SetStateAction<string>>
  antwortAuf: AntwortBezug | null
  setAntwortAuf: Dispatch<SetStateAction<AntwortBezug | null>>
  setSelectedImage: Dispatch<SetStateAction<ImageAttachment | null>>
  setStagedFile: Dispatch<SetStateAction<FileAttachment | null>>
  setErwaehnungsVorschlaege: Dispatch<SetStateAction<Erwaehnungsvorschlag[]>>
  verfallSekunden: number
  setMessages: Dispatch<SetStateAction<ChatMessage[]>>
  setSending: Dispatch<SetStateAction<boolean>>
  setIdentity: Dispatch<SetStateAction<E2eeIdentity>>
  identityRef: { current: E2eeIdentity }
  maxPartnerReadIdRef: { current: number }
  maxPartnerDeliveredIdRef: { current: number }
  justSentRef: { current: boolean }
  lastTypingSentRef: { current: number }
  sendeLaeuft: { current: boolean }
  entwuerfe: { verwirf: (mailboxId: string) => void }
  konversation: Konversation
  loadMessages: (isInitial?: boolean) => Promise<void>
  sendE2eeControlMessage: (payload: Record<string, unknown>) => Promise<void>
  getOutbox: typeof getOutbox
  setOutbox: (items: ReturnType<typeof getOutbox>) => void
}

/**
 * Abzeichen für das, was nur dieses Gerät sieht: den Inhalt der Nachricht.
 * Gemeldet wird nur die Art, nie Empfänger oder Gruppe.
 */
function meldeInhaltsErrungenschaften(auftrag: SendeAuftrag, text: string): void {
  if (auftrag.audio || auftrag.videoNote) meldeErrungenschaft('social_voice_memo')
  if (auftrag.augenblick || auftrag.img?.name === 'kamera-aufnahme.jpg') meldeErrungenschaft('social_camera_moment')
  if (auftrag.sticker || /\p{Extended_Pictographic}/u.test(text)) meldeErrungenschaft('social_sticker_fun')
  if (auftrag.note) meldeErrungenschaft('social_note_share')
  if (auftrag.cal) meldeErrungenschaft('social_event_share')
}

export function useMessengerSenden(optionen: Optionen) {
  const {
    t,
    currentUserId,
    user,
    activeContact,
    activeGroup,
    groups,
    blindMailboxId,
    darfSchreiben,
    darfAnhaengen,
    darfFremdeLoeschen,
    editingMessage,
    setEditingMessage,
    inputText,
    setInputText,
    antwortAuf,
    setAntwortAuf,
    setSelectedImage,
    setStagedFile,
    setErwaehnungsVorschlaege,
    verfallSekunden,
    setMessages,
    setSending,
    setIdentity,
    identityRef,
    maxPartnerReadIdRef,
    maxPartnerDeliveredIdRef,
    justSentRef,
    lastTypingSentRef,
    sendeLaeuft,
    entwuerfe,
    konversation,
    loadMessages,
    sendE2eeControlMessage,
    getOutbox,
    setOutbox,
  } = optionen

  /**
   * Wer in diesem Text genannt wird.
   *
   * Nur in Gruppen: ein Direktchat hat genau einen Gegenüber, den man nicht
   * erst adressieren muss. Aufgelöst wird beim **Senden**, gegen die
   * Mitgliederliste — der Text trägt danach den Namen, die Wirkung die Kennung.
   */
  const erwaehnungsFelder = (text: string) => {
    if (!activeGroup) return {}
    const { erwaehnungen, erwaehntAlle } = findeErwaehnungen(text, activeGroup.members || [])
    return {
      erwaehnungen: erwaehnungen.length ? erwaehnungen : undefined,
      erwaehntAlle: erwaehntAlle || undefined,
    }
  }

  // Action: Edit existing message
  const handleEditMessage = async (msg: ChatMessage, newText: string) => {
    const cleanText = newText.trim()
    if (!cleanText || cleanText === msg.text) {
      /*
       * Nichts zu ändern — dann auch nichts stehen lassen.
       *
       * Bis hierher wurde nur `setEditingMessage(null)` gerufen. Wer eine
       * Nachricht zum Bearbeiten öffnete, alles löschte und Enter drückte,
       * sah die Bearbeiten-Leiste verschwinden und fünf Leerzeichen im Feld
       * zurückbleiben, ohne ein Wort dazu. Aufgeräumt wird jetzt wie im
       * Erfolgsfall, samt der wartenden Entwurfs-Entprellung: sonst schreibt
       * die den Rest gleich wieder als Entwurf in die Chatliste.
       */
      setEditingMessage(null)
      setInputText('')
      entwuerfe.verwirf(blindMailboxId)
      return
    }
    const bearbeitetAm = new Date().toISOString()
    try {
      await sendE2eeControlMessage({
        type: 'edit_message',
        target_id: msg.id,
        target_client_uuid: msg.clientUuid,
        new_text: cleanText,
        edited_at: bearbeitetAm,
      })

      /**
       * Die Änderung auch hier anwenden — und zwar selbst, nicht über den
       * Umschlag.
       *
       * Der Steuerumschlag ist gegen die Geräte der Gegenseite versiegelt. Wer
       * mit dem Double Ratchet verschlüsselt, kann sein eigenes Erzeugnis nicht
       * wieder öffnen; beim nächsten `loadMessages` liegt für dieses Gerät
       * nichts vor, was es in `editMap` eintragen könnte. Die Gegenseite sah die
       * Änderung also, der Absender nie — die Nachricht stand unverändert da,
       * obwohl die Meldung „Nachricht bearbeitet" erschien.
       *
       * Der eigene Verlauf ist die einzige Fassung, die dieses Gerät je hat.
       * Also wird sie hier geändert, vor dem Neuladen: `loadMessages` liest sie
       * von dort und würde eine spätere Änderung sonst wieder überschreiben.
       */
      await updateMessageInLocalStore(blindMailboxId, msg.id, {
        text: cleanText,
        isEdited: true,
        editedAt: bearbeitetAm,
      }).catch(() => {})

      setMessages((prev) => {
        const geaendert = prev.map((m) =>
          m.id === msg.id
            ? { ...m, text: cleanText, isEdited: true, editedAt: bearbeitetAm, originalText: m.text }
            : m,
        )
        sessionChatCache.set(blindMailboxId, geaendert.slice(-80))
        return geaendert
      })

      toast.success(t('messenger.messageEdited'))
      setEditingMessage(null)
      setInputText('')
      /**
       * Der Entwurf muss mit weg — wie beim Senden.
       *
       * `setInputText('')` leert nur das Feld. Der Text lag zusätzlich als
       * Entwurf in der Ablage, und die entprellte Übernahme schrieb ihn dort
       * sogar noch einmal hin. Sichtbar wurde das in der Chatliste als
       * „Entwurf: …" zu einem Chat mit leerer Eingabe, und beim nächsten
       * Öffnen stand der bearbeitete Satz wieder im Feld.
       */
      entwuerfe.verwirf(blindMailboxId)
      await loadMessages(false)
    } catch {
      toast.error(t('messenger.messageEditFailed'))
    }
  }


  // Action: Delete message with victim protection preserved
  const handleDeleteMessage = async (msg: ChatMessage) => {
    // Eine Nachricht, die noch in der Warteschlange steht, gibt es nur hier.
    // Sie „für alle" zu löschen ging ins Leere: der Steuerumschlag nannte eine
    // Kennung, die kein anderes Gerät je gesehen hat, und die Zeile blieb
    // stehen. Am laufenden System waren das die Nachrichten mit der Uhr, an
    // die niemand mehr herankam. Also lokal entfernen, aus Ansicht,
    // Warteschlange und Verlauf.
    if (msg.status === 'queued' || msg.status === 'failed') {
      const uuid = msg.clientUuid
      if (uuid) {
        /**
         * Die Warteschlange führt einen Auftrag je Zielgerät, und `#<geraet>`
         * hält sie auseinander; die Zeile im Verlauf trägt die logische Kennung
         * ohne Zusatz. Der Vergleich auf Gleichheit traf im Direktchat deshalb
         * nie zu — der Auftrag blieb liegen, und eine verworfene Nachricht wäre
         * später doch noch hinausgegangen.
         *
         * Der Sitzungsaufbau (`dr-init`) bleibt bewusst stehen. Er entsteht nur,
         * solange es keine Sitzung gibt; wirft man ihn weg, findet die
         * Gegenstelle für alles Spätere keine und läuft in den Sitzungsbruch.
         * Die ausgelassene Nachricht überspringt der Ratchet von selbst.
         */
        setOutbox(
          getOutbox().filter((m) => {
            if (m.entity !== 'message' || m.payload?.is_control) return true
            return (logischeUuid(m.payload?.client_uuid ?? m.id) ?? m.id) !== uuid
          })
        )
      }
      setMessages((prev) => {
        const uebrig = prev.filter((m) => m.clientUuid !== msg.clientUuid)
        sessionChatCache.set(blindMailboxId, uebrig.slice(-80))
        return uebrig
      })
      // Die Ablage muss die Zeile aktiv verlieren. `saveLocalMessages` schreibt
      // nur — die weggelassene Nachricht blieb dort stehen und kam beim nächsten
      // Abgleich zurück.
      void entferneLokaleNachricht(blindMailboxId, {
        clientUuid: msg.clientUuid,
        id: msg.id,
      }).catch(() => {})
      toast.success(t('messenger.pendingDiscarded'))
      return
    }

    // Eine fremde Nachricht zu entfernen ist Moderation und braucht das Recht
    // dafür. Der Menüeintrag erscheint ohne es gar nicht; diese Zeile fängt den
    // Weg über die Mehrfachauswahl und über die Leiste ab.
    const fremd = !msg.isSelf
    if (fremd && !darfFremdeLoeschen) {
      toast.error(t('messenger.deleteNoRight'))
      return
    }

    const geloeschtAm = new Date().toISOString()
    try {
      // 1. Die Gegenseite erfährt es. Steht am Anfang, weil nur dieser Schritt
      //    ein fremdes Gerät erreicht; was danach kommt, kann man wiederholen.
      await sendE2eeControlMessage({
        type: 'delete_message',
        target_id: msg.id,
        target_client_uuid: msg.clientUuid,
        deleted_at: geloeschtAm,
      })

      // 2. Chiffretext und Anhänge vom Server nehmen — vor dem lokalen Tilgen.
      //    Die Medienkennungen stehen ausschließlich in dieser Zeile; ist sie
      //    erst ein Grabstein, findet kein zweiter Versuch die Blobs mehr.
      let medienGeblieben = 0
      if (fremd) {
        ;({ medienGeblieben } = await tilgeFremdeNachrichtBeimServer(blindMailboxId, msg))
      } else {
        await tilgeNachrichtBeimServer(blindMailboxId, msg)
      }

      // 3. Und zuletzt dieses Gerät. Der eigene Löschbefehl kommt hier nie an:
      //    eine Ratchet-Nachricht kann ihr Absender nicht öffnen, `deleteMap`
      //    bliebe für diese Nachricht auf immer leer.
      await tilgeNachrichtLokal(blindMailboxId, msg, geloeschtAm)

      setMessages((prev) => {
        const geaendert = prev.map((m) =>
          m.id === msg.id || (msg.clientUuid && m.clientUuid === msg.clientUuid)
            ? tilgeInhalt(m, geloeschtAm)
            : m
        )
        sessionChatCache.set(blindMailboxId, geaendert.slice(-80))
        return geaendert
      })

      if (!fremd) {
        toast.success(t('messenger.messageDeletedForAll'))
      } else if (medienGeblieben > 0) {
        // Nicht verschweigen: der Text ist weg, das Bild liegt noch da.
        toast.info(t('messenger.messageRemovedMediaStays', { count: medienGeblieben }))
      } else {
        toast.success(t('messenger.messageRemovedByModeration'))
      }
      await loadMessages(false)
    } catch {
      toast.error(t('messenger.messageDeleteFailed'))
    }
  }


  /**
   * Sendet eine Nachricht.
   *
   * Der Rückgabewert sagt, ob der Versand **angenommen** wurde: `false` heißt,
   * dass einer der Riegel gegriffen hat (kein Ziel, kein Recht, ein laufender
   * Versand) und nichts angelegt wurde. „Erneut senden“ hängt davon ab — die
   * alte Zeile darf erst fallen, wenn ihre Ersetzung existiert.
   */
  // Send message (text, note, cal, img, audio, file, sticker)
  const handleSendMessage = async (auftrag: SendeAuftrag = {}): Promise<boolean> => {
    const {
      text: customText,
      note,
      cal,
      img,
      audio,
      file,
      sticker,
      storyReply,
      videoNote,
      videoUrl,
      weitergeleitet,
    } = auftrag
    // Ohne ausdrückliche Angabe gilt der Zitatkopf über der Eingabe.
    const bezug = auftrag.antwortAuf !== undefined ? auftrag.antwortAuf : antwortAuf
    // Ein ausdrückliches Ziel (Weiterleiten, Story-Antwort) ist ein anderes
    // Gespräch als das offene. Dessen Zustand (Bearbeiten, Rechte der offenen
    // Gruppe, Eingabefeld) gilt dafür nicht.
    const fremdesZiel = auftrag.ziel ?? null
    const zielDirekt = !!fremdesZiel?.recipientId && !fremdesZiel.groupId

    // If currently editing a message, redirect to edit handler
    if (editingMessage && !fremdesZiel && !auftrag.augenblick && !auftrag.funkenRettung) {
      const textToSave = customText !== undefined ? customText : inputText
      await handleEditMessage(editingMessage, textToSave)
      return false
    }

    const rawText = customText !== undefined ? customText : inputText.trim()
    if (
      (!rawText && !note && !cal && !img && !audio && !file && !sticker && !storyReply && !videoNote &&
        !auftrag.funkenRettung) ||
      (!fremdesZiel && ((!activeContact && !activeGroup) || !blindMailboxId)) ||
      !currentUserId
    ) {
      return false
    }

    // Die Rechtelage der Gruppe, bevor irgendetwas verschlüsselt wird. Das
    // Eingabefeld ist bereits gesperrt; dies fängt die anderen Wege ab —
    // Weiterleiten, Sprachnachricht, Videonotiz, Sticker.
    if (!zielDirekt && !darfSchreiben) {
      toast.error(t('messenger.sendNoRight'))
      return false
    }
    if (
      !zielDirekt &&
      !darfAnhaengen &&
      (note || cal || img || audio || file || sticker || storyReply || videoNote)
    ) {
      toast.error(t('messenger.attachNoRight'))
      return false
    }

    // Die Videonotiz wird vor dem Verschlüsseln gemessen, wie jede andere Datei
    // auch. Ohne diese Zeile lief eine lange Aufnahme durch die ganze
    // Verschlüsselung und scheiterte erst am Deckel des Servers — im Chat stand
    // dann eine Zeile, die wieder verschwand, und niemand erfuhr warum. Der
    // Rekorder hält die Größe zwar im Blick; dies ist der Fangnetz dahinter.
    if (videoNote && videoNote.blob.size > maxAnhangBytes()) {
      toast.error(
        t('messenger.videoNoteTooLarge', { limit: Math.floor(maxAnhangBytes() / (1024 * 1024)) })
      )
      return false
    }

    /*
     * Augenblick und Wiederherstellung gibt es nur im Direktchat unter
     * Freunden (`kernVon` gibt sonst `null`). Die Marke entsteht hier, vor der
     * optimistischen Zeile: das Abzeichen springt sofort, und der Zeitpunkt in
     * der Nutzlast ist derselbe, mit dem dieses Gerät selbst rechnet.
     */
    const funkenPartner =
      auftrag.augenblick || auftrag.funkenRettung
        ? Number((fremdesZiel ? fremdesZiel.recipientId : activeContact?.userId) || 0)
        : 0
    let augenblickMarke: AugenblickMarke | undefined
    let rettungsMarke: RettungsMarke | undefined
    if (auftrag.augenblick || auftrag.funkenRettung) {
      const jetzt = Date.now()
      const kern =
        funkenPartner && !fremdesZiel?.groupId ? useFunkenStore.getState().kernVon(funkenPartner, jetzt) : null
      if (!kern) {
        toast.error(t('messenger.moment.friendsOnly'))
        return false
      }
      const markenZeit = new Date(jetzt).toISOString()
      if (auftrag.funkenRettung) {
        if (!rettungMoeglich(kern, jetzt)) {
          toast.error(t('messenger.streak.restoreUnavailable'))
          return false
        }
        rettungsMarke = baueWiederherstellungNutzlast(markenZeit, kern)
      } else {
        if (!img) return false
        augenblickMarke = baueAugenblickNutzlast(markenZeit, kern)
      }
    }

    /*
     * Der Riegel gegen den Doppelklick — und er muss eine Ref sein.
     *
     * Am Knopf steht `disabled={sending}`, und `sending` ist Zustand. React
     * rendert erst nach dem Tick neu; acht Klicks im selben Tick laufen
     * deshalb alle durch. Nachgemessen am 21.09.2026: acht Klicks ergaben
     * **acht** Nachrichten mit acht eigenen Umschlägen. Für einen ungeduldigen
     * Doppelklick heisst das zwei Nachrichten — und jeder Umschlag geht vom
     * Hundert-Umschläge-Fenster ab.
     *
     * Er steht hier, direkt hinter dem letzten Abbruchgrund und **vor** der
     * optimistischen Zeile. Weiter unten am `setSending(true)` reichte nicht:
     * die Nachzügler kamen zwar nicht mehr zum Versand, legten aber schon
     * Zeilen in der Ablage an, die dann als „queued" liegenblieben. Bis
     * hierher ist nichts `await`, ein zweiter Aufruf kann also nur hier
     * auflaufen. Das Weiterleiten ruft in einer Schleife auf, aber mit
     * `await` — jeder Durchgang gibt den Riegel im `finally` wieder frei.
     */
    if (sendeLaeuft.current) return false
    sendeLaeuft.current = true

    /**
     * Wohin diese Nachricht geht.
     *
     * Beim Weiterleiten ist das ein **anderes** Gespräch als das offene. Bis
     * 09/2026 stand hier stur `blindMailboxId`, und jede Weiterleitung landete
     * still im gerade geöffneten Chat statt beim gewählten Empfänger.
     */
    const targetBlindMailboxId = fremdesZiel?.blindMailboxId ?? blindMailboxId
    const targetUserId = fremdesZiel ? (fremdesZiel.recipientId ?? undefined) : activeContact?.userId
    const currentGroupId = fremdesZiel ? (fremdesZiel.groupId ?? undefined) : activeGroup?.id

    const clientUuid =
      typeof crypto !== 'undefined' && crypto.randomUUID
        ? crypto.randomUUID()
        : 'msg-' + Date.now() + '-' + Math.random().toString(36).substring(2, 9)

    // Instant optimistic update (<5ms, non-blocking UI)
    const optimisticMessage: ChatMessage = {
      id: Date.now(),
      clientUuid,
      senderId: currentUserId,
      senderName: user?.username || 'Ich',
      text: rawText,
      createdAt: new Date().toISOString(),
      isSelf: true,
      imageAttachment: img, // keeps local dataUrl for instant sender rendering
      fileAttachment: file,
      noteAttachment: note,
      calendarAttachment: cal,
      audioAttachment: audio,
      stickerAttachment: sticker,
      storyReply,
      // Der Zeiger auf den Blob wird nachgetragen, sobald der Upload durch ist.
      videoNoteAttachment: videoNote
        ? {
            durationSeconds: videoNote.durationSeconds,
            width: videoNote.width,
            height: videoNote.height,
            mimeType: videoNote.mimeType,
          }
        : undefined,
      videoUrl,
      antwortAuf: bezug || undefined,
      weitergeleitet: weitergeleitet || undefined,
      ...erwaehnungsFelder(rawText),
      verfaelltAm: berechneVerfall(verfallSekunden),
      augenblick: augenblickMarke,
      funkenRettung: rettungsMarke,
      isDelivered: false,
      isRead: false,
      status: 'queued',
    }

    // Der eigene Augenblick zählt ab jetzt. Scheitert der Versand, nimmt der
    // catch-Zweig ihn zurück. Der eigene Ratchet-Umschlag kommt nie zurück;
    // dies ist die einzige Stelle, an der dieses Gerät ihn sieht.
    const funkenEreignis: FunkenEreignis | null =
      funkenPartner && (augenblickMarke || rettungsMarke)
        ? ereignisAusNachricht(optimisticMessage, currentUserId, funkenPartner)
        : null
    if (funkenEreignis) void useFunkenStore.getState().nimmAuf(funkenPartner, [funkenEreignis])

    /**
     * Wann die optimistische Zeile wirklich auf der Platte liegt.
     *
     * Hier lag ein Rennen mit dauerhaftem Schaden. Der Schreibvorgang lief
     * ungewartet los, und nach dem Versand zog `updateMessageInLocalStore` die
     * Serverkennung nach — ebenfalls ungewartet. Kam das Nachziehen zuerst,
     * fand es über den `by_client_uuid`-Index noch nichts und kehrte
     * **stillschweigend** um; die verspätete Erstablage schrieb danach die
     * alte Fassung fest. Am wahrscheinlichsten beim allerersten Mal, weil dann
     * auch noch die Datenbank und der Versiegelungsschlüssel entstehen.
     *
     * Was blieb, war eine zugestellte Nachricht mit `status: 'queued'` und
     * einer Zeitstempel-Notkennung. Und das ist nicht bloss ein falsches
     * Symbol: `isOptimisticMessage` steuert damit auch die Sortierung (die
     * Zeile sinkt unter jede später empfangene) und den Vorrang beim Mischen.
     *
     * Geschrieben wird nur die neue Zeile, nicht der halbe Verlauf:
     * `saveLocalMessages` legt je Nachricht ab und löscht nie.
     */
    let ablageBereit: Promise<unknown> = Promise.resolve()

    if (fremdesZiel) {
      // Der offene Verlauf bleibt unberührt — die Nachricht gehört woandershin.
      // Sie muss trotzdem lokal landen: den eigenen Ratchet-Umschlag kann
      // dieses Gerät nie wieder öffnen, die Ablage ist die einzige Fassung.
      ablageBereit = saveLocalMessages(targetBlindMailboxId, [optimisticMessage])
        .then(() => vergissMailbox(targetBlindMailboxId))
        .catch(() => {
          // Ohne lokale Zeile ist die Nachricht draußen, aber hier unsichtbar.
        })
    } else {
      setMessages((prev) => {
        const updated = [...prev, optimisticMessage]
        sessionChatCache.set(targetBlindMailboxId, updated.slice(-80))
        return updated
      })
      ablageBereit = saveLocalMessages(targetBlindMailboxId, [optimisticMessage]).catch(() => {})
      // Was im offenen Chat getippt steht, bleibt bei einem fremden Ziel stehen.
      setInputText('')
      setSelectedImage(null)
      setStagedFile(null)
      setAntwortAuf(null)
      setErwaehnungsVorschlaege([])
    }
    // Der Entwurf ist verschickt, also keiner mehr.
    entwuerfe.verwirf(targetBlindMailboxId)
    justSentRef.current = true
    lastTypingSentRef.current = 0

    if (targetBlindMailboxId) {
      void sendTypingSignal({
        blind_mailbox_id: targetBlindMailboxId,
        status: 'idle',
      }).catch(() => {})
    }

    setSending(true)
    try {
      // Die Schlüsselprüfung steht hinter dem optimistischen Einfügen, damit die
      // Eingabe sofort leer ist. Scheitert sie, nimmt der catch-Zweig die
      // Nachricht wieder aus dem Verlauf.
      let aktiveIdentitaet = identityRef.current
      if (targetUserId) {
        // Kurz nach dem Öffnen kann die Antwort noch ausstehen. Dann hier
        // warten, statt abzulehnen — gesperrt und „noch unbekannt" sind zwei
        // verschiedene Dinge.
        if (aktiveIdentitaet.state === 'loading') {
          aktiveIdentitaet = await resolveIdentity(currentUserId)
          setIdentity(aktiveIdentitaet)
        }
        if (aktiveIdentitaet.state !== 'ready' || !aktiveIdentitaet.sendPair) {
          throw new E2eeIdentityLockedError()
        }
      }

      const erwaehnt = erwaehnungsFelder(rawText)
      const zeitpunkt = new Date().toISOString()

      const { finalImg, finalFile, finalAudio, finalVideoNote } = await ladeAnhaengeHoch(
        { img, file, audio, videoNote },
        { blindMailboxId: targetBlindMailboxId, absenderId: currentUserId, groupId: currentGroupId },
      )
      if (augenblickMarke && !finalImg?.mediaId) throw new AugenblickNichtHochgeladenError()

      // Was hochgeladen wurde, gehört auch in die eigene Zeile: sonst zeigt sie
      // nach einem Neuladen auf eine Blob-URL, die es nicht mehr gibt.
      if (finalAudio || finalVideoNote) {
        const nachtrag = {
          ...(finalAudio ? { audioAttachment: finalAudio } : {}),
          ...(finalVideoNote ? { videoNoteAttachment: finalVideoNote } : {}),
        }
        setMessages((prev) =>
          prev.map((m) => (m.clientUuid === clientUuid ? { ...m, ...nachtrag } : m))
        )
        await ablageBereit
        await updateMessageInLocalStore(targetBlindMailboxId, clientUuid, nachtrag).catch(() => {})
      }

      const payloadObj = baueNutzlast({
        clientUuid,
        absenderId: currentUserId,
        absenderName: user?.username || 'Ich',
        text: rawText,
        zeitpunkt,
        bezug,
        weitergeleitet,
        erwaehnt,
        verfaelltAm: optimisticMessage.verfaelltAm,
        note,
        cal,
        sticker,
        storyReply,
        finalImg,
        finalFile,
        finalAudio,
        finalVideoNote,
        augenblick: augenblickMarke,
        funkenRettung: rettungsMarke,
      })

      // Der Beleg über den Absender. Im Direktchat trägt ihn schon der Ratchet,
      // in der Gruppe gäbe es ihn sonst nirgends — siehe `nutzlastSignatur.ts`.
      const payload = JSON.stringify(
        await signiereNutzlast(targetBlindMailboxId, currentUserId, payloadObj),
      )

      // Welche Umschläge daraus werden, entscheidet `useKonversation`: einer
      // für die Gruppe, oder je Empfängergerät und eigenem Zweitgerät einer aus
      // dem Double Ratchet, dem sein Sitzungsaufbau vorausgeht. Der
      // Gruppenschlüssel rotiert dabei, falls sich die Mitgliedschaft geändert
      // hat. Das steht bewusst vor der Offline-Abzweigung: ohne Netz gibt es
      // weder einen frischen Schlüssel noch einen Weg, ihn zu verteilen.
      if (!currentGroupId && !aktiveIdentitaet.sendPair) {
        throw new Error(t('messenger.deviceKeyNotReady'))
      }
      const auftraege = fremdesZiel
        ? await baueVersandFuer(
            // Für eine fremde Gruppe braucht der Schlüssel ihre Mitglieder;
            // für einen fremden Direktchat reicht das Gegenüber.
            fremdesZiel.groupId
              ? {
                  groupId: fremdesZiel.groupId,
                  blindMailboxId: targetBlindMailboxId,
                  eigeneId: currentUserId,
                  mitglieder: (() => {
                    const g = groups.find((x) => x.id === fremdesZiel.groupId)
                    const ids = (g?.members ?? []).map((m) => Number(m.user_id))
                    if (!ids.includes(currentUserId)) ids.push(currentUserId)
                    return ids
                  })(),
                  istEigentuemer:
                    groups.find((x) => x.id === fremdesZiel.groupId)?.role === 'owner',
                }
              : null,
            fremdesZiel.recipientId
              ? { eigeneId: currentUserId, peerId: Number(fremdesZiel.recipientId) }
              : null,
            targetBlindMailboxId,
            payload,
            clientUuid,
          )
        : await konversation.baueVersand(payload, clientUuid)
      if (auftraege.length === 0) {
        // Früher fiel der Sendepfad hier auf einen Schlüssel zurück, den das
        // Backend aus den beiden Benutzerkennungen selbst bilden kann. Lieber
        // nicht senden und es sagen.
        if (currentGroupId) throw new Error(t('messenger.groupNotReady'))
        throw new E2eeRecipientKeyMissingError(targetUserId ?? 0)
      }

      const { niedrigsteId, verbindungsfehler, ueberInbox } = await stelleZu(auftraege)

      if (verbindungsfehler && niedrigsteId === 0) {
        toast.info(t('messenger.queuedOffline'))
      }

      if (niedrigsteId > 0) {
        if (!weitergeleitet) meldeInhaltsErrungenschaften(auftrag, rawText)
        const serverId = niedrigsteId
        // Ausdrückliche Kennungen gelten vor der Obergrenze — der Partner
        // nennt seit 23.10.2026 die Nachricht selbst.
        const bestaetigt = partnerKennungen(targetBlindMailboxId)
        const perIdRead = bestaetigt.gelesen.has(clientUuid)
        // Eine Zeile aus dem Gerätepostfach trägt eine Annahmenummer; die
        // alten Zahlenquittungen gehören zu einem anderen Kennungsraum.
        const zahlenGelten = !ueberInbox
        const isRead = perIdRead || (zahlenGelten && maxPartnerReadIdRef.current >= serverId)
        const isDelivered =
          isRead ||
          bestaetigt.zugestellt.has(clientUuid) ||
          (zahlenGelten && maxPartnerDeliveredIdRef.current >= serverId)
        const status: 'queued' | 'sent' | 'delivered' | 'read' = isRead
          ? 'read'
          : isDelivered
            ? 'delivered'
            : 'sent'
        const nachziehen = (m: ChatMessage): ChatMessage =>
          m.clientUuid === clientUuid
            ? {
                ...m,
                id: serverId,
                status,
                isRead: m.isRead || isRead,
                isDelivered: m.isDelivered || isDelivered,
                // Einmal über das Postfach, immer über das Postfach: die
                // Kennung dieser Zeile bleibt eine Annahmenummer.
                ...(ueberInbox ? { viaInbox: true } : {}),
              }
            : m
        setMessages((prev) => sortMessagesChronologically(prev.map(nachziehen)))
        const cached = sessionChatCache.get(targetBlindMailboxId)
        if (cached) {
          sessionChatCache.set(targetBlindMailboxId, sortMessagesChronologically(cached.map(nachziehen)))
        }
        // Beides gewartet, und in dieser Reihenfolge: die Zeile muss liegen,
        // bevor sie nachgezogen wird, und nachgezogen sein, bevor
        // `loadMessages` sie liest und wieder wegschreibt.
        await ablageBereit
        await updateMessageInLocalStore(targetBlindMailboxId, clientUuid, {
          id: serverId,
          status,
          isRead,
          isDelivered,
          ...(ueberInbox ? { viaInbox: true } : {}),
        }).catch(() => {})
      }

      await loadMessages()
    } catch (err: unknown) {
      // Was nicht hinausging, zählt nicht für den Funken.
      if (funkenEreignis) void useFunkenStore.getState().nimmZurueck(funkenPartner, funkenEreignis.kennung)

      const istSchluesselProblem =
        err instanceof E2eeRecipientKeyMissingError ||
        err instanceof E2eeIdentityLockedError ||
        err instanceof DrZustellungFehlgeschlagenError ||
        err instanceof DrGeraetNichtEingetragenError ||
        err instanceof AugenblickNichtHochgeladenError

      if (istSchluesselProblem) {
        // Konnte nicht verschlüsselt werden: die optimistisch eingefügte
        // Nachricht wieder herausnehmen, sonst stünde im Verlauf etwas, das nie
        // gesendet wurde. Der Text kommt in die Eingabe zurück, damit er nicht
        // verloren geht.
        if (err instanceof E2eeRecipientKeyMissingError && targetUserId) {
          forgetRecipientPublicKey(targetUserId)
        }
        setMessages((prev) => {
          const updated = prev.filter((m) => m.clientUuid !== clientUuid)
          sessionChatCache.set(targetBlindMailboxId, updated.slice(-80))
          return updated
        })
        if (rawText) setInputText(rawText)

        // Auch aus der Ablage: sonst holte der nächste Abgleich die Zeile
        // zurück, und mit ihr den Augenblick.
        if (funkenEreignis) {
          void ablageBereit.then(() => entferneLokaleNachricht(targetBlindMailboxId, { clientUuid }))
        }

        if (err instanceof AugenblickNichtHochgeladenError) {
          toast.error(t('messenger.moment.uploadFailed'))
        } else if (err instanceof E2eeIdentityLockedError) {
          // Der Schlüssel dieses Geräts war noch nicht fertig angelegt. Beim
          // nächsten Versuch steht er — es gibt nichts, was der Benutzer dafür
          // tun müsste.
          toast.error(t('messenger.deviceKeyNotReadyRetry'))
        } else if (err instanceof DrZustellungFehlgeschlagenError) {
          // Die Gegenstelle ist angemeldet, das Verschlüsseln hat versagt. Der
          // nächste Versuch setzt die Sitzung neu auf, deshalb der Hinweis auf
          // das Wiederholen statt einer Aussage über den Kontakt.
          toast.error(t('messenger.encryptFailed'))
        } else if (err instanceof DrGeraetNichtEingetragenError) {
          // Dieses Gerät wurde in der Geräteliste entfernt. Der Text sagt,
          // warum nicht gesendet wird und was es wieder einträgt.
          toast.error(err.message)
        } else {
          toast.error(
            t('messenger.noDeviceOnline', {
              name: activeContact?.username ?? t('messenger.thisContact'),
            })
          )
        }
      } else {
        const msg = err instanceof Error ? err.message : t('messenger.sendFailed')
        toast.error(msg)
      }
    } finally {
      sendeLaeuft.current = false
      setSending(false)
    }
    return true
  }



  return { handleSendMessage, handleEditMessage, handleDeleteMessage }
}
