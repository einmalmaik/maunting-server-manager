import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

import type { ChatGroupItem } from '@/api/social'
import type { ChatContact } from '@/components/social/sidebar/ConversationListItem'
import { useKontaktAusUrl } from '@/hooks/useKontaktAusUrl'
import { PINS_MAX, useMessengerNotificationStore } from '@/stores/messengerNotificationStore'
import { toast } from '@/stores/toastStore'

interface Optionen {
  kontakte: readonly ChatContact[]
  gruppen: readonly ChatGroupItem[]
  kontakteGeladen: boolean
  t: (schluessel: string, optionen?: Record<string, unknown>) => string
}

/** Welches Gespräch offen ist und wie die Adresse dazu steht. */
export function useMessengerAuswahl({ kontakte, gruppen, kontakteGeladen, t }: Optionen) {
  const [searchParams, setSearchParams] = useSearchParams()
  const savedUserId = typeof window !== 'undefined' && window.sessionStorage
    ? sessionStorage.getItem('msm:active_messenger_user_id') : null
  const queryUserId = searchParams.get('userId') || searchParams.get('contact') || savedUserId
  const savedGroupId = typeof window !== 'undefined' && window.sessionStorage
    ? sessionStorage.getItem('msm:active_messenger_group_id') : null
  const queryGroupId = searchParams.get('groupId') || savedGroupId

  const [activeContact, setActiveContact] = useState<ChatContact | null>(null)
  const [activeGroup, setActiveGroup] = useState<ChatGroupItem | null>(null)
  const [zeilenMenue, setZeilenMenue] = useState<{ mid: string; name: string } | null>(null)
  const [widerruf, setWiderruf] = useState<{ text: string; zurueck: () => void } | null>(null)

  const pinnedChats = useMessengerNotificationStore((s) => s.pinnedChats)
  const archivedChats = useMessengerNotificationStore((s) => s.archivedChats)
  const schalteAnheften = useMessengerNotificationStore((s) => s.schalteAnheften)
  const schalteArchiv = useMessengerNotificationStore((s) => s.schalteArchiv)

  useKontaktAusUrl({
    queryUserId, queryGroupId, kontakte, gruppen, kontakteGeladen,
    activeContact, activeGroup, setActiveContact, setActiveGroup,
    searchParams, setSearchParams, t,
  })

  useEffect(() => {
    if (!widerruf) return
    const uhr = window.setTimeout(() => setWiderruf(null), 6000)
    return () => window.clearTimeout(uhr)
  }, [widerruf])

  const handleZurueckZurListe = useCallback(() => {
    try {
      sessionStorage.removeItem('msm:active_messenger_user_id')
      sessionStorage.removeItem('msm:active_messenger_group_id')
    } catch {}
    setActiveContact(null)
    setActiveGroup(null)
    const next = new URLSearchParams(searchParams)
    let geaendert = false
    for (const key of ['userId', 'contact', 'groupId']) {
      if (next.has(key)) { next.delete(key); geaendert = true }
    }
    if (geaendert) setSearchParams(next, { replace: true })
  }, [searchParams, setSearchParams])

  useEffect(() => {
    if (activeContact) {
      try {
        sessionStorage.setItem('msm:active_messenger_user_id', String(activeContact.userId))
        sessionStorage.removeItem('msm:active_messenger_group_id')
      } catch {}
      const cur = searchParams.get('userId') || searchParams.get('contact')
      if (cur !== String(activeContact.userId) || searchParams.has('groupId')) {
        const next = new URLSearchParams(searchParams)
        next.set('userId', String(activeContact.userId))
        next.delete('groupId')
        setSearchParams(next, { replace: true })
      }
    } else if (activeGroup) {
      try {
        sessionStorage.setItem('msm:active_messenger_group_id', String(activeGroup.id))
        sessionStorage.removeItem('msm:active_messenger_user_id')
      } catch {}
      const cur = searchParams.get('groupId')
      if (cur !== String(activeGroup.id) || searchParams.has('userId') || searchParams.has('contact')) {
        const next = new URLSearchParams(searchParams)
        next.set('groupId', String(activeGroup.id))
        next.delete('userId')
        next.delete('contact')
        setSearchParams(next, { replace: true })
      }
    } else {
      try {
        sessionStorage.removeItem('msm:active_messenger_user_id')
        sessionStorage.removeItem('msm:active_messenger_group_id')
      } catch {}
    }
  }, [activeContact, activeGroup, searchParams, setSearchParams])

  const handleAnheftenChat = (mid: string) => {
    const war = pinnedChats.includes(mid)
    if (!schalteAnheften(mid).ok) {
      toast.error(t('messenger.pinLimit', { count: PINS_MAX }))
      return
    }
    setWiderruf({
      text: war ? t('messenger.unpinned') : t('messenger.pinned'),
      zurueck: () => { schalteAnheften(mid) },
    })
  }

  const handleArchivieren = (mid: string) => {
    const war = archivedChats.includes(mid)
    schalteArchiv(mid)
    setWiderruf({
      text: war ? t('messenger.unarchived') : t('messenger.archived'),
      zurueck: () => { schalteArchiv(mid) },
    })
  }

  return {
    activeContact, setActiveContact, activeGroup, setActiveGroup,
    searchParams, setSearchParams, queryUserId, queryGroupId,
    zeilenMenue, setZeilenMenue, widerruf, setWiderruf,
    handleZurueckZurListe, handleAnheftenChat, handleArchivieren,
  }
}

export type MessengerAuswahl = ReturnType<typeof useMessengerAuswahl>
