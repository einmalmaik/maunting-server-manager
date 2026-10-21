/**
 * Reagieren, Anheften und Weiterleiten — die Wege, die eine bestehende
 * Nachricht noch einmal in Bewegung setzen.
 *
 * Der Block stand bis 10/2026 mitten in `Messenger.tsx`. Herausgelöst ist er
 * **unverändert**: dieselbe sofortige lokale Wirkung, dieselbe Rücknahme bei
 * einem gescheiterten Steuerumschlag, dieselbe Reihenfolge beim Weiterleiten.
 * Die Seite reicht Zustand und den fertigen Sendeweg hinein.
 */

import type { Dispatch, SetStateAction } from 'react'

import type { ChatGroupItem } from '@/api/social'
import type {
  CalendarAttachment,
  ChatMessage,
  NoteAttachment,
  StickerAttachment,
  StoryReplyAttachment,
} from '@/components/social/ChatMessageBubble'
import type {
  AudioAttachment,
  FileAttachment,
  ImageAttachment,
  MedienBindungsKontext,
} from '@/components/social/ChatMediaAttachments'
import type { SendeAuftrag } from '@/hooks/useMessengerSenden'
import type { Weiterleitungsziel } from '@/services/nachrichtWeiterleiten'
import { baueWeiterleitung } from '@/services/nachrichtWeiterleiten'
import { bezugFelder } from '@/services/nachrichtBezug'
import { anheftung, setzeAnheftung } from '@/services/nachrichtAnheftung'
import { schalteReaktion } from '@/services/reaktionen'
import { sessionChatCache } from '@/services/klartextSpeicher'
import { vergissMailbox } from '@/services/verlaufSuche'
import { updateMessageInLocalStore } from '@/services/messengerLocalStore'
import { toast } from '@/stores/toastStore'

interface Optionen {
  t: (schluessel: string, werte?: Record<string, unknown>) => string
  currentUserId: number
  blindMailboxId: string
  activeGroup: ChatGroupItem | null
  angeheftet: ChatMessage | null
  setAngeheftet: Dispatch<SetStateAction<ChatMessage | null>>
  darfAnheften: boolean
  weiterzuleiten: ChatMessage[] | null
  setWeiterzuleiten: Dispatch<SetStateAction<ChatMessage[] | null>>
  setWlFortschritt: Dispatch<SetStateAction<{ gesamt: number; fertig: number } | null>>
  beendeAuswahl: () => void
  setMessages: Dispatch<SetStateAction<ChatMessage[]>>
  sendE2eeControlMessage: (payload: Record<string, unknown>) => Promise<void>
  /** `false` heißt: ein Riegel hat gegriffen, es wurde nichts angelegt. */
  handleSendMessage: (auftrag?: SendeAuftrag) => Promise<boolean>
}

export function useMessengerInteraktionen(optionen: Optionen) {
  const {
    t,
    currentUserId,
    blindMailboxId,
    activeGroup,
    angeheftet,
    setAngeheftet,
    darfAnheften,
    weiterzuleiten,
    setWeiterzuleiten,
    setWlFortschritt,
    beendeAuswahl,
    setMessages,
    sendE2eeControlMessage,
    handleSendMessage,
  } = optionen

  /**
   * Reagieren — dieselbe Bewegung setzt und nimmt zurück.
   *
   * Die Wirkung wird **sofort lokal** angewendet, nicht erst beim nächsten
   * Abruf: der eigene Steuerumschlag kommt nie zurück, weil ein Ratchet sein
   * eigenes Erzeugnis nicht öffnet. Dieselbe Regel wie bei Bearbeiten und
   * Löschen.
   */
  const handleReaktion = async (msg: ChatMessage, emoji: string) => {
    if (!currentUserId || !blindMailboxId || msg.isDeleted) return
    const { reaktionen: neu, aktion } = schalteReaktion(msg.reaktionen, emoji, currentUserId)

    setMessages((prev) => {
      const geaendert = prev.map((m) =>
        m.id === msg.id || (msg.clientUuid && m.clientUuid === msg.clientUuid)
          ? { ...m, reaktionen: neu }
          : m,
      )
      sessionChatCache.set(blindMailboxId, geaendert.slice(-80))
      return geaendert
    })
    void updateMessageInLocalStore(blindMailboxId, msg.id, { reaktionen: neu }).catch(() => {})
    vergissMailbox(blindMailboxId)

    try {
      await sendE2eeControlMessage({
        type: 'reaction',
        ...bezugFelder(msg),
        emoji,
        aktion,
        actor_id: currentUserId,
        zeitpunkt: new Date().toISOString(),
      })
    } catch {
      // Eine Reaktion, die lokal steht und nie ankommt, ist eine Lüge — also
      // wird sie zurückgenommen und gesagt, dass es nicht geklappt hat.
      const zurueck = schalteReaktion(neu, emoji, currentUserId).reaktionen
      setMessages((prev) =>
        prev.map((m) =>
          m.id === msg.id || (msg.clientUuid && m.clientUuid === msg.clientUuid)
            ? { ...m, reaktionen: zurueck }
            : m,
        ),
      )
      void updateMessageInLocalStore(blindMailboxId, msg.id, { reaktionen: zurueck }).catch(() => {})
      toast.error(t('messenger.reactionFailed'))
    }
  }


  /**
   * Eine Nachricht über den Verlauf heften.
   *
   * Dieselbe Bauart wie `@everyone`: der Server kann den Inhalt nicht lesen und
   * deshalb nicht prüfen, wer was anheftet. Also entscheidet der **empfangende**
   * Client anhand der Rechte des Anheftenden, ob die Leiste erscheint.
   */
  const handleAnheften = async (msg: ChatMessage) => {
    if (!activeGroup || !msg.clientUuid) return
    if (!darfAnheften) {
      toast.error(t('messenger.pinNoRightInGroup'))
      return
    }
    const loesen = angeheftet?.clientUuid === msg.clientUuid
    const vorher = anheftung(blindMailboxId)
    // Ein Zeitpunkt für beides. Mit zwei knapp verschiedenen käme der eigene
    // Umschlag beim nächsten Abruf als „neuer" zurück.
    const jetzt = new Date().toISOString()
    setAngeheftet(loesen ? null : msg)
    setzeAnheftung(blindMailboxId, loesen ? '' : msg.clientUuid, jetzt)
    try {
      await sendE2eeControlMessage({
        type: 'pin_message',
        ...bezugFelder(msg),
        aktion: loesen ? 'loesen' : 'anheften',
        actor_id: currentUserId,
        zeitpunkt: jetzt,
      })
      toast.success(loesen ? t('messenger.unpinned') : t('messenger.pinned'))
    } catch {
      setAngeheftet(loesen ? msg : null)
      setzeAnheftung(blindMailboxId, vorher.clientUuid, vorher.stand || jetzt)
      toast.error(t('messenger.pinFailed'))
    }
  }


  /**
   * Woran die Anhänge einer Nachricht hängen.
   *
   * Absender und Mailbox kommen aus dem Gespräch, nicht aus dem Anhang. DIS
   * bindet beides in die gebundenen Daten jedes Stücks — deshalb lässt sich ein
   * Blob nicht in ein anderes Gespräch oder unter einen anderen Absender
   * umhängen.
   */
  const medienBindung = (msg: ChatMessage): MedienBindungsKontext => ({
    absenderId: Number(msg.senderId) || Number(currentUserId) || 0,
    blindMailboxId,
  })


  /**
   * Weiterleiten heißt neu verschlüsseln.
   *
   * Medien müssen wirklich noch einmal hoch: ein Anhang ist an Absender **und**
   * Mailbox gebunden und geht in einem anderen Gespräch nicht auf. Das dauert,
   * deshalb der Fortschritt.
   */
  const handleWeiterleiten = async (ziele: Weiterleitungsziel[]) => {
    const auswahl = weiterzuleiten
    if (!auswahl?.length || !currentUserId || !blindMailboxId) return
    let gescheitert = 0
    try {
      for (const ziel of ziele) {
        for (const msg of auswahl) {
          try {
            const inhalt = await baueWeiterleitung(
              msg,
              medienBindung(msg),
              ziel,
              currentUserId,
              setWlFortschritt,
            )
            await handleSendMessage({
              text: inhalt.text || '',
              note: inhalt.noteAttachment as NoteAttachment | undefined,
              cal: inhalt.calendarAttachment as CalendarAttachment | undefined,
              sticker: inhalt.stickerAttachment as StickerAttachment | undefined,
              storyReply: inhalt.storyReply as StoryReplyAttachment | undefined,
              img: inhalt.imageAttachment as ImageAttachment | undefined,
              file: inhalt.fileAttachment as FileAttachment | undefined,
              audio: inhalt.audioAttachment as AudioAttachment | undefined,
              weitergeleitet: true,
              antwortAuf: null,
              ziel,
            })
          } catch {
            gescheitert++
          }
        }
      }
    } finally {
      setWlFortschritt(null)
    }
    setWeiterzuleiten(null)
    beendeAuswahl()
    if (gescheitert) toast.error(t('messenger.forwardFailed', { count: gescheitert }))
    else toast.success(t('messenger.forwarded', { count: ziele.length }))
  }



  return {
    handleReaktion,
    handleAnheften,
    handleWeiterleiten,
    medienBindung,
  }
}
