/**
 * Messenger Notification & Unread State Store
 *
 * Verwaltet:
 * - Ungelesene Nachrichten (`unreadCounts`, `totalUnreadCount`)
 * - Stummschaltungen (`mutedChats`: 8h, 1w, dauerhaft)
 * - Blockierungen (`blockedUserIds`)
 * - Blind-Mailbox Verzeichnis (`mailboxDirectory`: Name, Avatar, Typ)
 * - Web Audio API Zweiklang-Glocke (`playNotificationChime()`)
 */

import { create } from 'zustand'
import { api } from '@/api/client'
import { deriveBlindMailboxId, deriveGroupBlindMailboxId } from '@/services/e2eeCrypto'

const STORAGE_MUTES_KEY = 'msm:chat_mutes'
const STORAGE_BLOCKS_KEY = 'msm:chat_blocks'
const STORAGE_BLOCKED_PROFILES_KEY = 'msm:chat_blocked_profiles'
const STORAGE_UNREAD_KEY = 'msm:chat_unread'
const STORAGE_MAILBOX_DIR_KEY = 'msm:chat_mailbox_dir'

export interface MailboxMeta {
  name: string
  avatarUrl?: string | null
  isGroup?: boolean
  userId?: number
  groupId?: number
}

export interface BlockedProfile {
  userId: number
  username: string
  avatarUrl?: string | null
}

interface MessengerNotificationState {
  unreadCounts: Record<string, number>
  totalUnreadCount: number
  mutedChats: Record<string, number> // mailboxId -> expiryTimestamp (0 = permanent)
  blockedUserIds: number[]
  blockedProfiles: Record<number, { username: string; avatarUrl?: string | null }>
  mailboxDirectory: Record<string, MailboxMeta>
  activeMailboxId: string | null

  // Actions
  setActiveMailboxId: (id: string | null) => void
  registerMailbox: (mailboxId: string, meta: MailboxMeta) => void
  registerMailboxes: (map: Record<string, MailboxMeta>) => void
  incrementUnread: (mailboxId: string) => void
  markAsRead: (mailboxId: string) => void
  clearAllUnread: () => void

  // Mute
  isMuted: (mailboxId: string) => boolean
  muteChat: (mailboxId: string, durationMinutes?: number) => void
  unmuteChat: (mailboxId: string) => void

  // Block
  isBlocked: (userId: number) => boolean
  blockUser: (userId: number, username?: string, avatarUrl?: string | null) => Promise<void>
  unblockUser: (userId: number) => Promise<void>
  syncBlockedFromBackend: () => Promise<void>
  syncMailboxDirectoryFromBackend: (currentUserId: number) => Promise<void>
}

function loadMailboxDirectory(): Record<string, MailboxMeta> {
  try {
    const raw = localStorage.getItem(STORAGE_MAILBOX_DIR_KEY)
    if (raw) return JSON.parse(raw)
  } catch {}
  return {}
}

function loadMutes(): Record<string, number> {
  try {
    const raw = localStorage.getItem(STORAGE_MUTES_KEY)
    if (raw) return JSON.parse(raw)
  } catch {}
  return {}
}

function loadBlocks(): number[] {
  try {
    const raw = localStorage.getItem(STORAGE_BLOCKS_KEY)
    if (raw) return JSON.parse(raw)
  } catch {}
  return []
}

function loadBlockedProfiles(): Record<number, { username: string; avatarUrl?: string | null }> {
  try {
    const raw = localStorage.getItem(STORAGE_BLOCKED_PROFILES_KEY)
    if (raw) return JSON.parse(raw)
  } catch {}
  return {}
}

function loadUnread(): Record<string, number> {
  try {
    const raw = localStorage.getItem(STORAGE_UNREAD_KEY)
    if (raw) return JSON.parse(raw)
  } catch {}
  return {}
}

function calcTotal(unread: Record<string, number>, mutes: Record<string, number>): number {
  const now = Date.now()
  let sum = 0
  for (const [mid, count] of Object.entries(unread)) {
    if (count > 0) {
      const expiry = mutes[mid]
      const isMuted = expiry !== undefined && (expiry === 0 || expiry > now)
      if (!isMuted) {
        sum += count
      }
    }
  }
  return sum
}

/**
 * Web Audio API Zweiklang-Glocke („Bing“-Chime)
 * Erzeugt einen kristallklaren, unaufdringlichen Zweiton-Glockenklang (880 Hz -> 1320 Hz)
 * ohne externe Audio-Dateien oder Netzwerklatenz.
 */
export function playNotificationChime() {
  if (typeof window === 'undefined') return
  try {
    const AudioContextClass =
      window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext
    if (!AudioContextClass) return
    const ctx = new AudioContextClass()
    if (ctx.state === 'suspended') {
      void ctx.resume()
    }

    const now = ctx.currentTime

    // Oszillator: Grundton A5 (880 Hz) gleitet kurz auf E6 (1320 Hz)
    const osc = ctx.createOscillator()
    const gain = ctx.createGain()

    osc.type = 'sine'
    osc.frequency.setValueAtTime(880, now)
    osc.frequency.exponentialRampToValueAtTime(1320, now + 0.09)

    // Sanfte Lautstärken-Hüllkurve
    gain.gain.setValueAtTime(0.18, now)
    gain.gain.exponentialRampToValueAtTime(0.001, now + 0.42)

    osc.connect(gain)
    gain.connect(ctx.destination)

    osc.start(now)
    osc.stop(now + 0.43)
  } catch {
    // Stiller Fallback bei blockiertem AudioContext
  }
}

export const useMessengerNotificationStore = create<MessengerNotificationState>((set, get) => {
  const initialMutes = loadMutes()
  const initialBlocks = loadBlocks()
  const initialProfiles = loadBlockedProfiles()
  const initialUnread = loadUnread()

  return {
    unreadCounts: initialUnread,
    totalUnreadCount: calcTotal(initialUnread, initialMutes),
    mutedChats: initialMutes,
    blockedUserIds: initialBlocks,
    blockedProfiles: initialProfiles,
    mailboxDirectory: loadMailboxDirectory(),
    activeMailboxId: null,

    setActiveMailboxId: (id) => {
      set({ activeMailboxId: id })
      if (id) {
        get().markAsRead(id)
      }
    },

    registerMailbox: (mailboxId, meta) => {
      set((state) => {
        const updated = {
          ...state.mailboxDirectory,
          [mailboxId]: meta,
        }
        try {
          localStorage.setItem(STORAGE_MAILBOX_DIR_KEY, JSON.stringify(updated))
        } catch {}
        return { mailboxDirectory: updated }
      })
    },

    registerMailboxes: (map) => {
      set((state) => {
        const updated = {
          ...state.mailboxDirectory,
          ...map,
        }
        try {
          localStorage.setItem(STORAGE_MAILBOX_DIR_KEY, JSON.stringify(updated))
        } catch {}
        return { mailboxDirectory: updated }
      })
    },

    incrementUnread: (mailboxId) => {
      set((state) => {
        const current = state.unreadCounts[mailboxId] || 0
        const updated = { ...state.unreadCounts, [mailboxId]: current + 1 }
        try {
          localStorage.setItem(STORAGE_UNREAD_KEY, JSON.stringify(updated))
        } catch {}
        return {
          unreadCounts: updated,
          totalUnreadCount: calcTotal(updated, state.mutedChats),
        }
      })
    },

    markAsRead: (mailboxId) => {
      set((state) => {
        if (!state.unreadCounts[mailboxId]) return state
        const updated = { ...state.unreadCounts, [mailboxId]: 0 }
        try {
          localStorage.setItem(STORAGE_UNREAD_KEY, JSON.stringify(updated))
        } catch {}
        return {
          unreadCounts: updated,
          totalUnreadCount: calcTotal(updated, state.mutedChats),
        }
      })
    },

    clearAllUnread: () => {
      try {
        localStorage.removeItem(STORAGE_UNREAD_KEY)
      } catch {}
      set({ unreadCounts: {}, totalUnreadCount: 0 })
    },

    isMuted: (mailboxId) => {
      const { mutedChats } = get()
      const expiry = mutedChats[mailboxId]
      if (expiry === undefined) return false
      if (expiry === 0) return true
      if (expiry > Date.now()) return true
      // Abgelaufen -> aufräumen
      get().unmuteChat(mailboxId)
      return false
    },

    muteChat: (mailboxId, durationMinutes) => {
      set((state) => {
        const expiry = durationMinutes && durationMinutes > 0 ? Date.now() + durationMinutes * 60 * 1000 : 0
        const updated = { ...state.mutedChats, [mailboxId]: expiry }
        try {
          localStorage.setItem(STORAGE_MUTES_KEY, JSON.stringify(updated))
        } catch {}
        return {
          mutedChats: updated,
          totalUnreadCount: calcTotal(state.unreadCounts, updated),
        }
      })
    },

    unmuteChat: (mailboxId) => {
      set((state) => {
        const updated = { ...state.mutedChats }
        delete updated[mailboxId]
        try {
          localStorage.setItem(STORAGE_MUTES_KEY, JSON.stringify(updated))
        } catch {}
        return {
          mutedChats: updated,
          totalUnreadCount: calcTotal(state.unreadCounts, updated),
        }
      })
    },

    isBlocked: (userId) => {
      return get().blockedUserIds.includes(userId)
    },

    blockUser: async (userId, username, avatarUrl) => {
      set((state) => {
        const nextIds = state.blockedUserIds.includes(userId)
          ? state.blockedUserIds
          : [...state.blockedUserIds, userId]
        const nextProfiles = { ...state.blockedProfiles }
        if (username) {
          nextProfiles[userId] = { username, avatarUrl: avatarUrl ?? null }
        }
        try {
          localStorage.setItem(STORAGE_BLOCKS_KEY, JSON.stringify(nextIds))
          localStorage.setItem(STORAGE_BLOCKED_PROFILES_KEY, JSON.stringify(nextProfiles))
        } catch {}
        return { blockedUserIds: nextIds, blockedProfiles: nextProfiles }
      })
      try {
        await api(`/social/friends/${userId}/block`, { method: 'POST' })
      } catch {
        // Lokaler Fallback bleibt aktiv
      }
    },

    unblockUser: async (userId) => {
      set((state) => {
        const nextIds = state.blockedUserIds.filter((id) => id !== userId)
        const nextProfiles = { ...state.blockedProfiles }
        delete nextProfiles[userId]
        try {
          localStorage.setItem(STORAGE_BLOCKS_KEY, JSON.stringify(nextIds))
          localStorage.setItem(STORAGE_BLOCKED_PROFILES_KEY, JSON.stringify(nextProfiles))
        } catch {}
        return { blockedUserIds: nextIds, blockedProfiles: nextProfiles }
      })
      try {
        await api(`/social/friends/${userId}/unblock`, { method: 'POST' })
      } catch {
        // Lokaler Fallback bleibt aktiv
      }
    },

    syncBlockedFromBackend: async () => {
      try {
        const list = await api<Array<{ user_id: number; username: string; avatar_url?: string | null }>>(
          '/social/friends/blocked'
        )
        if (Array.isArray(list)) {
          const ids = list.map((item) => item.user_id)
          const profiles: Record<number, { username: string; avatarUrl?: string | null }> = {}
          for (const item of list) {
            profiles[item.user_id] = { username: item.username, avatarUrl: item.avatar_url }
          }
          set({ blockedUserIds: ids, blockedProfiles: profiles })
          try {
            localStorage.setItem(STORAGE_BLOCKS_KEY, JSON.stringify(ids))
            localStorage.setItem(STORAGE_BLOCKED_PROFILES_KEY, JSON.stringify(profiles))
          } catch {}
        }
      } catch {
        // Stiller Fallback auf lokalen Cache
      }
    },

    syncMailboxDirectoryFromBackend: async (currentUserId: number) => {
      if (!currentUserId) return
      try {
        const [friends, groups, publicProfiles] = await Promise.all([
          api<Array<{ user_id: number; username: string; avatar_url?: string | null; status: string }>>(
            '/social/friends'
          ).catch(() => []),
          api<Array<{ id: number; name: string; avatar_url?: string | null }>>('/social/groups').catch(() => []),
          api<Array<{ user_id: number; username: string }>>('/social/profiles/public').catch(() => []),
        ])

        const dirUpdates: Record<string, MailboxMeta> = {}

        if (Array.isArray(friends)) {
          for (const f of friends) {
            if (f.user_id && f.status === 'accepted') {
              try {
                const mid = await deriveBlindMailboxId(currentUserId, f.user_id)
                dirUpdates[mid] = {
                  name: f.username,
                  avatarUrl: f.avatar_url,
                  isGroup: false,
                  userId: f.user_id,
                }
              } catch {}
            }
          }
        }

        if (Array.isArray(groups)) {
          for (const g of groups) {
            if (g.id) {
              try {
                const mid = await deriveGroupBlindMailboxId(g.id)
                dirUpdates[mid] = {
                  name: g.name,
                  avatarUrl: g.avatar_url,
                  isGroup: true,
                  groupId: g.id,
                }
              } catch {}
            }
          }
        }

        if (Array.isArray(publicProfiles)) {
          for (const p of publicProfiles) {
            if (p.user_id && p.user_id !== currentUserId) {
              try {
                const mid = await deriveBlindMailboxId(currentUserId, p.user_id)
                if (!dirUpdates[mid]) {
                  dirUpdates[mid] = {
                    name: p.username,
                    avatarUrl: null,
                    isGroup: false,
                    userId: p.user_id,
                  }
                }
              } catch {}
            }
          }
        }

        if (Object.keys(dirUpdates).length > 0) {
          get().registerMailboxes(dirUpdates)
        }
      } catch {
        // Stiller Fallback
      }
    },
  }
})
