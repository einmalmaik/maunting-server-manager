import { useCallback, useEffect, useMemo, useState } from 'react'

import type { ChatGroupItem } from '@/api/social'
import type { PresenceStatus } from '@/components/social/StatusIndicator'
import type { ChatContact } from '@/components/social/sidebar/ConversationListItem'
import { useKontaktdaten } from '@/hooks/useKontaktdaten'
import { useStoryAnsicht } from '@/hooks/useStoryAnsicht'
import { deriveBlindMailboxId, deriveGroupBlindMailboxId } from '@/services/e2eeCrypto'
import { fuelleNamenNach } from '@/services/gespraechsListe'
import { useMessengerNotificationStore } from '@/stores/messengerNotificationStore'

export type MessengerFilterTab = 'all' | 'groups' | 'friends' | 'teams' | 'public'
export type MessengerNavTab = 'chats' | 'updates' | 'community'

interface Optionen {
  currentUserId: number
  messengerGesperrt: boolean
  gruppenTitel: (gruppe: { name?: string | null } | null | undefined) => string
}

/**
 * Was die Seitenleiste kennt: Kontakte, Gruppen, Stories, Suche, Filter und
 * die Mailbox-Kennung je Zeile. Bewusst getrennt von der Auswahl des offenen
 * Gesprächs — diese Datei weiss nur, *welche* Chats es gibt.
 */
export function useMessengerKontakte({ currentUserId, messengerGesperrt, gruppenTitel }: Optionen) {
  const {
    friends, groups, setGroups, teamMembers, publicUsers, directChats, stories, setStories,
    laden: loadData, geladen: kontakteGeladen,
  } = useKontaktdaten(currentUserId, messengerGesperrt)

  const pinnedChats = useMessengerNotificationStore((s) => s.pinnedChats)
  const archivedChats = useMessengerNotificationStore((s) => s.archivedChats)
  const unreadCounts = useMessengerNotificationStore((s) => s.unreadCounts)

  const [searchQuery, setSearchQuery] = useState('')
  const [filterTab, setFilterTab] = useState<MessengerFilterTab>('all')
  const [mobileNavTab, setMobileNavTab] = useState<MessengerNavTab>('chats')
  const [archivOffen, setArchivOffen] = useState(false)
  const [contactMailboxMap, setContactMailboxMap] = useState<Record<number, string>>({})
  const [groupMailboxMap, setGroupMailboxMap] = useState<Record<number, string>>({})

  const contactsList: ChatContact[] = useMemo(() => {
    const list: ChatContact[] = []
    const seen = new Set<number>()
    let zaehler = 0
    const key = (uid: number) => `k${++zaehler}-${uid}`
    for (const f of friends) {
      if (f.status !== 'accepted' && !(f as any).friend_user_id) continue
      const uid = f.user_id ?? (f as any).friend_user_id ?? f.id
      if (seen.has(uid)) continue
      seen.add(uid)
      list.push({
        listKey: key(uid), id: f.id, userId: uid, username: f.username, avatarUrl: f.avatar_url,
        status: (f.presence?.status as PresenceStatus) || 'invisible',
        deviceType: f.presence?.device_type, activityLabel: f.presence?.activity_label,
        isFriend: true, teamName: null,
      })
    }
    for (const { member, teamName } of teamMembers) {
      if (!seen.has(member.user_id)) {
        seen.add(member.user_id)
        list.push({
          listKey: key(member.user_id), id: member.user_id, userId: member.user_id,
          username: member.username, avatarUrl: member.avatar_url, status: 'invisible',
          deviceType: null, activityLabel: null, isFriend: false, teamName,
        })
      } else {
        const existing = list.find((c) => c.userId === member.user_id)
        if (existing && !existing.teamName) existing.teamName = teamName
      }
    }
    for (const dc of directChats) {
      if (seen.has(dc.other_user_id)) continue
      seen.add(dc.other_user_id)
      list.push({
        listKey: key(dc.other_user_id), id: dc.other_user_id, userId: dc.other_user_id,
        username: dc.other_username, avatarUrl: dc.other_avatar_url || null,
        status: (dc.presence?.status as PresenceStatus) || 'invisible',
        deviceType: dc.presence?.device_type, activityLabel: dc.presence?.activity_label,
        isFriend: dc.is_friend, teamName: null, isPublicUser: dc.other_privacy === 'public',
      })
    }
    for (const p of publicUsers) {
      if (!seen.has(p.user_id)) {
        seen.add(p.user_id)
        list.push({
          listKey: key(p.user_id), id: p.user_id, userId: p.user_id, username: p.username,
          avatarUrl: p.avatar_url || null,
          status: (p.presence?.status as PresenceStatus) || 'invisible',
          deviceType: p.presence?.device_type, activityLabel: p.presence?.activity_label,
          isFriend: Boolean(p.is_friend), teamName: null, isPublicUser: true,
        })
      } else {
        const existing = list.find((c) => c.userId === p.user_id)
        if (existing) {
          existing.isPublicUser = true
          if (p.avatar_url && !existing.avatarUrl) existing.avatarUrl = p.avatar_url
        }
      }
    }
    return list.sort((a, b) => {
      const order: Record<string, number> = { online: 0, away: 1, invisible: 2 }
      const diff = (order[a.status] ?? 3) - (order[b.status] ?? 3)
      return diff !== 0 ? diff : a.username.localeCompare(b.username)
    })
  }, [friends, teamMembers, publicUsers, directChats])

  // Namenlose Gespräche bekommen ihren Namen aus der Kontaktliste dieses Geräts.
  useEffect(() => {
    if (!contactsList.length) return
    void fuelleNamenNach(contactsList.map((c) => ({
      userId: c.userId, username: c.username, avatarUrl: c.avatarUrl,
    }))).then((geaendert) => { if (geaendert) void loadData() }).catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [contactsList.length])

  const filteredContacts = useMemo(() => contactsList.filter((c) => {
    if (filterTab === 'groups') return false
    if (filterTab === 'friends' && !c.isFriend) return false
    if (filterTab === 'teams' && !c.teamName) return false
    if (filterTab === 'public' && !c.isPublicUser) return false
    if (!searchQuery.trim()) return true
    const q = searchQuery.toLowerCase()
    return c.username.toLowerCase().includes(q) || Boolean(c.teamName?.toLowerCase().includes(q))
  }), [contactsList, filterTab, searchQuery])

  const filteredGroups = useMemo(() => {
    if (filterTab === 'friends' || filterTab === 'teams' || filterTab === 'public') return []
    return groups.filter((g) => {
      if (!searchQuery.trim()) return true
      const q = searchQuery.toLowerCase()
      return gruppenTitel(g).toLowerCase().includes(q) || Boolean(g.description?.toLowerCase().includes(q))
    })
  }, [groups, filterTab, searchQuery, gruppenTitel])

  // Angeheftetes nach oben, Archiviertes heraus.
  const ordne = useCallback(<T,>(eintraege: T[], midVon: (e: T) => string | undefined) => {
    const sichtbar: T[] = []
    const imArchiv: T[] = []
    for (const e of eintraege) {
      const mid = midVon(e)
      if (mid && archivedChats.includes(mid)) imArchiv.push(e)
      else sichtbar.push(e)
    }
    sichtbar.sort((a, b) => {
      const pa = pinnedChats.indexOf(midVon(a) || '')
      const pb = pinnedChats.indexOf(midVon(b) || '')
      return pa === pb ? 0 : (pa < 0 ? 99 : pa) - (pb < 0 ? 99 : pb)
    })
    return { sichtbar, imArchiv }
  }, [pinnedChats, archivedChats])

  const gruppenNachArchiv = useMemo(() => ordne(filteredGroups, (g) => groupMailboxMap[g.id]), [ordne, filteredGroups, groupMailboxMap])
  const kontakteNachArchiv = useMemo(() => ordne(filteredContacts, (c) => contactMailboxMap[c.userId]), [ordne, filteredContacts, contactMailboxMap])
  const archivZahl = gruppenNachArchiv.imArchiv.length + kontakteNachArchiv.imArchiv.length
  const archivUngelesen = useMemo(() => {
    let summe = 0
    for (const g of gruppenNachArchiv.imArchiv) summe += unreadCounts[groupMailboxMap[g.id]] || 0
    for (const c of kontakteNachArchiv.imArchiv) summe += unreadCounts[contactMailboxMap[c.userId]] || 0
    return summe
  }, [gruppenNachArchiv, kontakteNachArchiv, unreadCounts, groupMailboxMap, contactMailboxMap])

  // Mailbox-Verzeichnis und Zuordnungen im Benachrichtigungs-Store registrieren.
  useEffect(() => {
    if (!currentUserId) return
    let active = true
    const store = useMessengerNotificationStore.getState()
    for (const c of contactsList) {
      deriveBlindMailboxId(currentUserId, c.userId).then((mid) => {
        if (!active) return
        setContactMailboxMap((prev) => (prev[c.userId] === mid ? prev : { ...prev, [c.userId]: mid }))
        store.registerMailbox(mid, { name: c.username, avatarUrl: c.avatarUrl, isGroup: false, userId: c.userId })
      }).catch(() => {})
    }
    for (const g of groups) {
      deriveGroupBlindMailboxId(g.id).then((mid) => {
        if (!active) return
        setGroupMailboxMap((prev) => (prev[g.id] === mid ? prev : { ...prev, [g.id]: mid }))
        store.registerMailbox(mid, { name: gruppenTitel(g), avatarUrl: g.avatar_url, isGroup: true, groupId: g.id })
      }).catch(() => {})
    }
    return () => { active = false }
  }, [contactsList, groups, currentUserId, gruppenTitel])

  const storyAnsicht = useStoryAnsicht(stories, currentUserId, contactsList)

  return {
    friends, groups, setGroups, teamMembers, publicUsers, directChats, stories, setStories,
    loadData, kontakteGeladen, contactsList, filteredContacts, filteredGroups,
    gruppenNachArchiv, kontakteNachArchiv, archivZahl, archivUngelesen,
    contactMailboxMap, groupMailboxMap, storyAnsicht,
    searchQuery, setSearchQuery, filterTab, setFilterTab,
    mobileNavTab, setMobileNavTab, archivOffen, setArchivOffen,
  }
}

export type MessengerKontakte = ReturnType<typeof useMessengerKontakte>
export type MessengerGroups = ChatGroupItem[]
