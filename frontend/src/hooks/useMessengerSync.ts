/**
 * Der Echtzeitstrom des Messengers: Anrufe, eintreffende Nachrichten, Tippen.
 *
 * Der Block stand bis 10/2026 mitten in `Messenger.tsx`. Herausgelöst ist er
 * unverändert — dieselben Ereignisse, dieselben Filter, dasselbe Aufräumen.
 *
 * Er ist die einzige Stelle, an der ein **eingehender** Umschlag ein Neuladen
 * auslöst. Ein Abo je Mailbox meldet ihn (`mailboxAbo`), und was hier
 * entschieden wird, entscheidet über die Latenz des ganzen Chats: die Nachricht
 * wird nicht beim nächsten Fünf-Sekunden-Takt sichtbar, sondern sofort.
 *
 * `msm:messages-updated` kommt aus der Warteschlange und lädt ebenfalls nach;
 * `msm:message-confirmed` **nicht** — die Bestätigung schreibt ihren Status
 * zuerst in die Ablage, und ein paralleles Neuladen läse den alten Stand.
 */

import { useEffect } from 'react'
import type { Dispatch, SetStateAction } from 'react'

import type { ChatGroupItem } from '@/api/social'
import { useCallStore } from '@/stores/useCallStore'

type Aktivitaet = { status: 'typing' | 'recording'; username?: string } | null

export interface MessengerSyncOptionen {
  blindMailboxId: string
  currentUserId: number
  /** Nur für die Abhängigkeiten: eine geänderte Einstellung soll neu binden. */
  geraeteMeldungen: unknown
  loadMessages: (isInitial?: boolean) => Promise<void>
  setGroups: Dispatch<SetStateAction<ChatGroupItem[]>>
  setActiveGroup: Dispatch<SetStateAction<ChatGroupItem | null>>
  setPartnerActivity: Dispatch<SetStateAction<Aktivitaet>>
  partnerActivityTimeoutRef: { current: ReturnType<typeof setTimeout> | null }
}

export function useMessengerSync(optionen: MessengerSyncOptionen): void {
  const {
    blindMailboxId,
    currentUserId,
    geraeteMeldungen,
    loadMessages,
    setGroups,
    setActiveGroup,
    setPartnerActivity,
    partnerActivityTimeoutRef,
  } = optionen

  useEffect(() => {
    const handleSync = (e: Event) => {
      const ce = e as CustomEvent<any>
      const detail = ce.detail
      if (detail?.type === 'group_call_started') {
        if (!detail.group_id || !detail.room_token) return
        const groupId = Number(detail.group_id)
        const roomToken = String(detail.room_token)
        setGroups((prev) =>
          prev.map((group) =>
            group.id === groupId ? { ...group, room_token: roomToken } : group
          )
        )
        setActiveGroup((current) =>
          current?.id === groupId ? { ...current, room_token: roomToken } : current
        )
        useCallStore.getState().handleCallSyncEvent(detail)
      } else if (detail?.type === 'group_call_ended') {
        if (detail.group_id && detail.room_token) {
          const groupId = Number(detail.group_id)
          setGroups((prev) =>
            prev.map((group) =>
              group.id === groupId ? { ...group, room_token: null } : group
            )
          )
          setActiveGroup((current) =>
            current?.id === groupId ? { ...current, room_token: null } : current
          )
          useCallStore.getState().handleCallSyncEvent(detail)
        }
      } else if (
        detail?.type === 'direct_call_invitation' ||
        detail?.type === 'direct_call_rejected' ||
        detail?.type === 'direct_call_cancelled' ||
        detail?.type === 'call_key' ||
        detail?.type === 'user_call_state_changed' ||
        detail?.type === 'call_transferred' ||
        detail?.type === 'call_superseded' ||
        detail?.type === 'call_ended_remotely'
      ) {
        useCallStore.getState().handleCallSyncEvent(detail)
      } else if (detail?.type === 'e2ee_blind_message') {
        const isCurrentActive = detail.blind_mailbox_id === blindMailboxId
        // Outgoing Echo Prevention: Sender niemals benachrichtigen
        if (detail.sender_user_id && currentUserId && Number(detail.sender_user_id) === Number(currentUserId)) {
          if (isCurrentActive) {
            void loadMessages(false)
          }
          return
        }
        // Empfänger-Filterung: Nur Empfänger verarbeitet Nachricht
        if (detail.recipient_id && currentUserId && Number(detail.recipient_id) !== Number(currentUserId)) {
          return
        }
        if (isCurrentActive) {
          void loadMessages(false)
        }
      } else if (detail?.type === 'e2ee_typing_signal') {
        if (detail.blind_mailbox_id === blindMailboxId && detail.sender_id !== currentUserId) {
          if (partnerActivityTimeoutRef.current) {
            clearTimeout(partnerActivityTimeoutRef.current)
            partnerActivityTimeoutRef.current = null
          }
          if (detail.status === 'idle') {
            setPartnerActivity(null)
          } else if (detail.status === 'typing' || detail.status === 'recording') {
            setPartnerActivity({ status: detail.status, username: detail.sender_username })
            partnerActivityTimeoutRef.current = setTimeout(() => {
              setPartnerActivity(null)
            }, 4500)
          }
        }
      }
    }

    const handleMessagesUpdated = () => {
      void loadMessages(false)
    }

    const handleVisibilityChange = () => {
      if (typeof document !== 'undefined' && document.visibilityState === 'visible') {
        void loadMessages(false)
      }
    }

    window.addEventListener('msm:sync-event', handleSync)
    window.addEventListener('msm:messages-updated', handleMessagesUpdated)
    // `msm:message-confirmed` startet **kein** volles Neuladen mehr. Die
    // Bestätigung schreibt ihren Status zuerst in die Ablage; ein paralleles
    // `loadMessages` las vorher den alten Stand und setzte die Uhr zurück.
    if (typeof document !== 'undefined') {
      document.addEventListener('visibilitychange', handleVisibilityChange)
    }
    return () => {
      window.removeEventListener('msm:sync-event', handleSync)
      window.removeEventListener('msm:messages-updated', handleMessagesUpdated)
      if (typeof document !== 'undefined') {
        document.removeEventListener('visibilitychange', handleVisibilityChange)
      }
      if (partnerActivityTimeoutRef.current) {
        clearTimeout(partnerActivityTimeoutRef.current)
      }
    }
  }, [blindMailboxId, currentUserId, geraeteMeldungen])
}
