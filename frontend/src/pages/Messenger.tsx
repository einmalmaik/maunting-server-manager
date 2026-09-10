import React, { useState, useEffect, useRef, useMemo } from 'react'
import { useSearchParams, useParams, useNavigate } from 'react-router-dom'
import {
  Button,
  Input,
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
  Avatar,
  ChatInputBar,
  VoiceRecordingBar,
} from '@/Singra/UI'
import {
  MessageSquare,
  Send,
  Lock,
  Camera,
  StickyNote,
  Calendar as CalendarIcon,
  Search,
  UsersRound,
  ChevronLeft,
  X,
  MapPin,
  Clock,
  RefreshCw,
  Mic,
  Trash2,
  Play,
  Pause,
  Share2,
  Plus,
  LogOut,
  Sparkles,
  Smile,
  Paperclip,
  FileText,
  Check,
  CheckCheck,
  Download,
  Upload,
  Shield,
  UserCheck,
  Briefcase,
  LayoutGrid,
  Globe,
  UserPlus,
  Pencil,
  Image as ImageIcon,
  Bell,
  BellOff,
  Ban,
} from 'lucide-react'
import { DeviceBadge } from '@/components/social/DeviceBadge'
import { StatusDot, type PresenceStatus } from '@/components/social/StatusIndicator'
import {
  type FriendItem,
  type ChatGroupItem,
  type ChatStoryItem,
  type PublicProfileResponse,
  type DirectChatItem,
  getFriends,
  getGroups,
  getDirectChats,
  createGroup,
  joinGroupByInvite,
  sendFriendRequest,
  getPublicProfiles,
  leaveGroup,
  deleteGroup,
  getStories,
  relayE2eeEnvelope,
  fetchE2eeEnvelopes,
  sendTypingSignal,
  getE2eePublicKey,
  setE2eePublicKey,
  uploadEncryptedChatAttachment,
  getChatMediaSignedUrl,
  downloadAndDecryptChatAttachment,
} from '@/api/social'
import { teamsApi, type TeamMember } from '@/api/teams'
import {
  loadNotesOfflineFirst,
  loadCalendarEventsOfflineFirst,
  saveNoteOffline,
  saveCalendarEventOffline,
} from '@/lib/offlineSync'
import type { NoteItem } from '@/pages/Notes'
import type { CalendarEventItem } from '@/pages/Calendar'
import {
  deriveBlindMailboxId,
  deriveGroupBlindMailboxId,
  encryptE2eeMessage,
  decryptE2eeMessage,
  decryptE2eeHybrid,
  encryptGroupE2eeMessage,
  decryptGroupE2eeMessage,
  getOrGenerateLocalKeyPair,
  type LocalE2eeKeyPair,
  type AttachmentCryptoContext,
} from '@/services/e2eeCrypto'
import { getAudioTrackConstraints } from '@/lib/audioSettings'
import { IN_HOUSE_STICKERS, CATEGORIZED_EMOJIS } from '@/services/stickerCatalog'
import { CameraSnapshotModal } from '@/components/social/CameraSnapshotModal'
import { CreateStoryModal, STORY_GRADIENTS } from '@/components/social/CreateStoryModal'
import { StoryViewerModal, type StoryReplyContext } from '@/components/social/StoryViewerModal'
import { GroupPermissionsModal } from '@/components/social/GroupPermissionsModal'
import {
  type ChatWallpaperConfig,
  loadChatWallpaperConfig,
} from '@/components/social/ChatWallpaper'
import { ChatWallpaperModal } from '@/components/social/ChatWallpaperModal'
import { sendeGeraeteBenachrichtigung } from '@/lib/benachrichtigung'
import { useAuthStore } from '@/stores/authStore'
import { toast } from '@/stores/toastStore'
import { useMessengerNotificationStore } from '@/stores/messengerNotificationStore'
import { sanitizeSvg, getSafeAttachmentUrl } from '@/lib/sanitizeSvg'

function formatChatDateBadge(isoDateString: string): string {
  try {
    const d = new Date(isoDateString)
    if (isNaN(d.getTime())) return ''
    const now = new Date()

    const isToday =
      d.getDate() === now.getDate() &&
      d.getMonth() === now.getMonth() &&
      d.getFullYear() === now.getFullYear()
    if (isToday) return 'Heute'

    const yesterday = new Date(now)
    yesterday.setDate(now.getDate() - 1)
    const isYesterday =
      d.getDate() === yesterday.getDate() &&
      d.getMonth() === yesterday.getMonth() &&
      d.getFullYear() === yesterday.getFullYear()
    if (isYesterday) return 'Gestern'

    const isSameYear = d.getFullYear() === now.getFullYear()
    return d.toLocaleDateString('de-DE', {
      day: 'numeric',
      month: 'long',
      ...(isSameYear ? {} : { year: 'numeric' }),
    })
  } catch {
    return ''
  }
}

function getWaveformBars(msgId: number, count = 28): number[] {
  const bars: number[] = []
  let seed = (Math.abs(msgId) || 1) * 9301 + 49297
  for (let i = 0; i < count; i++) {
    seed = (seed * 9301 + 49297) % 233280
    const rand = seed / 233280
    // Natural audio envelope: quieter at start & end, natural speech peaks in between
    const pos = i / (count - 1)
    const envelope = Math.sin(pos * Math.PI) * 0.45 + 0.55
    const height = Math.max(0.2, Math.min(1.0, (0.2 + rand * 0.8) * envelope))
    bars.push(height)
  }
  return bars
}

export interface ChatContact {
  id: number
  userId: number
  username: string
  avatarUrl?: string | null
  status: PresenceStatus
  deviceType?: string | null
  activityLabel?: string | null
  isFriend: boolean
  teamName?: string | null
  isPublicUser?: boolean
}

export interface NoteAttachment {
  title: string
  content: string
  color?: string
  category?: string
}

export interface CalendarAttachment {
  title: string
  start: string
  end: string
  description?: string
  location?: string
}

export interface ImageAttachment {
  dataUrl: string
  name?: string
  mediaId?: string
}

export interface AudioAttachment {
  dataUrl: string
  durationSeconds: number
  mimeType: string
}

export interface FileAttachment {
  name: string
  sizeBytes: number
  mimeType: string
  dataUrl: string
  mediaId?: string
}

export interface StickerAttachment {
  id: string
  label: string
  svg: string
}

export interface StoryReplyAttachment {
  storyId?: number
  storyContent: string
  storyMediaUrl?: string | null
  storyBackground?: string
  storyUsername?: string
}

export interface ChatMessage {
  id: number
  senderId: number
  senderName?: string
  text: string
  createdAt: string
  isSelf: boolean
  isDelivered?: boolean
  isRead?: boolean
  isEdited?: boolean
  editedAt?: string
  isDeleted?: boolean
  deletedAt?: string
  originalText?: string
  noteAttachment?: NoteAttachment
  calendarAttachment?: CalendarAttachment
  imageAttachment?: ImageAttachment
  audioAttachment?: AudioAttachment
  fileAttachment?: FileAttachment
  stickerAttachment?: StickerAttachment
  storyReply?: StoryReplyAttachment
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`
}

export { getSafeAttachmentUrl }

function formatDuration(sec: number): string {
  const m = Math.floor(sec / 60)
  const s = Math.floor(sec % 60)
  return `${m}:${s < 10 ? '0' : ''}${s}`
}

function getSupportedAudioMimeType(): string {
  if (typeof MediaRecorder === 'undefined' || !MediaRecorder.isTypeSupported) {
    return ''
  }
  const candidates = [
    'audio/webm;codecs=opus',
    'audio/webm',
    'audio/ogg;codecs=opus',
    'audio/mp4',
    'audio/aac',
  ]
  return candidates.find((c) => MediaRecorder.isTypeSupported(c)) || ''
}

const CONTACTS_CACHE_KEY = 'msm:chat_contacts_cache'
const getChatCacheKey = (mid: string) => `msm:chat_cache:${mid}`

function loadInitialContactsCache(): {
  friends: FriendItem[]
  groups: ChatGroupItem[]
  teamMembers: Array<{ member: TeamMember; teamName: string }>
  publicUsers: PublicProfileResponse[]
  stories: ChatStoryItem[]
} {
  if (typeof window === 'undefined') {
    return { friends: [], groups: [], teamMembers: [], publicUsers: [], stories: [] }
  }
  try {
    const raw = localStorage.getItem(CONTACTS_CACHE_KEY)
    if (raw) {
      const parsed = JSON.parse(raw)
      return {
        friends: Array.isArray(parsed.friends) ? parsed.friends : [],
        groups: Array.isArray(parsed.groups) ? parsed.groups : [],
        teamMembers: Array.isArray(parsed.teamMembers) ? parsed.teamMembers : [],
        publicUsers: Array.isArray(parsed.publicUsers) ? parsed.publicUsers : [],
        stories: Array.isArray(parsed.stories) ? parsed.stories : [],
      }
    }
  } catch {}
  return { friends: [], groups: [], teamMembers: [], publicUsers: [], stories: [] }
}

export function Messenger() {
  const { user } = useAuthStore()
  const [searchParams] = useSearchParams()
  const { inviteCode } = useParams<{ inviteCode?: string }>()
  const navigate = useNavigate()
  const queryUserId = searchParams.get('userId')

  const initialCache = useMemo(() => loadInitialContactsCache(), [])
  const [friends, setFriends] = useState<FriendItem[]>(initialCache.friends)
  const [groups, setGroups] = useState<ChatGroupItem[]>(initialCache.groups)
  const [teamMembers, setTeamMembers] = useState<Array<{ member: TeamMember; teamName: string }>>(initialCache.teamMembers)
  const [publicUsers, setPublicUsers] = useState<PublicProfileResponse[]>(initialCache.publicUsers)
  const [directChats, setDirectChats] = useState<DirectChatItem[]>([])
  const [stories, setStories] = useState<ChatStoryItem[]>(initialCache.stories)

  // Notification & Mute/Block Store
  const unreadCounts = useMessengerNotificationStore((s) => s.unreadCounts)
  const isChatMuted = useMessengerNotificationStore((s) => s.isMuted)
  const muteChat = useMessengerNotificationStore((s) => s.muteChat)
  const unmuteChat = useMessengerNotificationStore((s) => s.unmuteChat)
  const isBlocked = useMessengerNotificationStore((s) => s.isBlocked)
  const blockUser = useMessengerNotificationStore((s) => s.blockUser)
  const unblockUser = useMessengerNotificationStore((s) => s.unblockUser)
  const markAsRead = useMessengerNotificationStore((s) => s.markAsRead)

  // Mute & Block modals
  const [isMuteModalOpen, setIsMuteModalOpen] = useState(false)
  const [isBlockConfirmOpen, setIsBlockConfirmOpen] = useState(false)

  // Pre-computed mailbox IDs
  const [contactMailboxMap, setContactMailboxMap] = useState<Record<number, string>>({})
  const [groupMailboxMap, setGroupMailboxMap] = useState<Record<number, string>>({})
  
  // Selection
  const [activeContact, setActiveContact] = useState<ChatContact | null>(null)
  const [activeGroup, setActiveGroup] = useState<ChatGroupItem | null>(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [filterTab, setFilterTab] = useState<'all' | 'groups' | 'friends' | 'teams' | 'public'>('all')
  const [mobileNavTab, setMobileNavTab] = useState<'chats' | 'updates' | 'community'>('chats')

  // Stories (Aktuelles)
  const [isCreateStoryOpen, setIsCreateStoryOpen] = useState(false)
  const [createStoryInitialMode, setCreateStoryInitialMode] = useState<'text' | 'photo'>('text')
  const [pendingStoryPhotoUrl, setPendingStoryPhotoUrl] = useState<string | null>(null)
  const [isViewerStoryOpen, setIsViewerStoryOpen] = useState(false)
  const [viewerStoryIndex, setViewerStoryIndex] = useState(0)
  const [activeViewerStories, setActiveViewerStories] = useState<ChatStoryItem[]>([])

  // Seen stories persistence
  const SEEN_STORIES_KEY = 'msm_seen_story_ids'
  const [seenStoryIds, setSeenStoryIds] = useState<Set<number>>(() => {
    if (typeof window === 'undefined') return new Set()
    try {
      const raw = localStorage.getItem(SEEN_STORIES_KEY)
      return raw ? new Set(JSON.parse(raw)) : new Set()
    } catch {
      return new Set()
    }
  })

  const markStoriesAsSeen = (storiesToMark: ChatStoryItem[]) => {
    setSeenStoryIds((prev) => {
      const next = new Set(prev)
      let changed = false
      for (const s of storiesToMark) {
        if (!next.has(s.id)) {
          next.add(s.id)
          changed = true
        }
      }
      if (changed && typeof window !== 'undefined') {
        try {
          localStorage.setItem(SEEN_STORIES_KEY, JSON.stringify(Array.from(next)))
        } catch {}
      }
      return next
    })
  }

  // Group Permissions & Delete Modal State
  const [isGroupPermissionsOpen, setIsGroupPermissionsOpen] = useState(false)
  const [groupToDelete, setGroupToDelete] = useState<ChatGroupItem | null>(null)
  const [isDeletingGroup, setIsDeletingGroup] = useState(false)

  // Notification deduplication ref
  const lastNotifiedMessageIdRef = useRef<number>(0)

  // Camera & Attachments
  const [isCameraModalOpen, setIsCameraModalOpen] = useState(false)
  const [isAttachMenuOpen, setIsAttachMenuOpen] = useState(false)
  const attachMenuRef = useRef<HTMLDivElement>(null)
  const [isDragOver, setIsDragOver] = useState(false)
  const [stagedFile, setStagedFile] = useState<FileAttachment | null>(null)
  const docInputRef = useRef<HTMLInputElement>(null)

  // Close attachment menu on outside click
  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (attachMenuRef.current && !attachMenuRef.current.contains(e.target as Node)) {
        setIsAttachMenuOpen(false)
      }
    }
    if (isAttachMenuOpen) {
      document.addEventListener('mousedown', handleClickOutside)
    }
    return () => {
      document.removeEventListener('mousedown', handleClickOutside)
    }
  }, [isAttachMenuOpen])

  // Read receipts setting from profile
  const readReceiptsEnabled = useMemo(() => {
    try {
      return localStorage.getItem('msm_read_receipts_enabled') !== 'false'
    } catch {
      return true
    }
  }, [])

  // Conversation state
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [inputText, setInputText] = useState('')
  const [loadingMessages, setLoadingMessages] = useState(false)
  const [sending, setSending] = useState(false)
  const [blindMailboxId, setBlindMailboxId] = useState<string>('')
  const [localKeyPair, setLocalKeyPair] = useState<LocalE2eeKeyPair | null>(null)

  // Attachments
  const [isNotePickerOpen, setIsNotePickerOpen] = useState(false)
  const [userNotes, setUserNotes] = useState<NoteItem[]>([])
  const [noteSearch, setNoteSearch] = useState('')

  const [isCalendarPickerOpen, setIsCalendarPickerOpen] = useState(false)
  const [userEvents, setUserEvents] = useState<CalendarEventItem[]>([])
  const [calendarSearch, setCalendarSearch] = useState('')

  const [selectedImage, setSelectedImage] = useState<ImageAttachment | null>(null)
  const [viewingImage, setViewingImage] = useState<string | null>(null)
  const [isSendPhotoOpen, setIsSendPhotoOpen] = useState(false)
  const [pendingPhotoToSend, setPendingPhotoToSend] = useState<ImageAttachment | null>(null)

  // Stickers / Emojis
  const [isStickerPickerOpen, setIsStickerPickerOpen] = useState(false)
  const [stickerTab, setStickerTab] = useState<'stickers' | 'emojis'>('stickers')

  // Group creation modal
  const [isCreateGroupOpen, setIsCreateGroupOpen] = useState(false)
  const [groupName, setGroupName] = useState('')
  const [groupDesc, setGroupDesc] = useState('')
  const [creatingGroup, setCreatingGroup] = useState(false)

  // Voice recording state
  const [isRecording, setIsRecording] = useState(false)
  const [recordingDuration, setRecordingDuration] = useState(0)
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const mediaStreamRef = useRef<MediaStream | null>(null)
  const audioChunksRef = useRef<Blob[]>([])
  const timerIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // Voice playback state
  const [playingAudioId, setPlayingAudioId] = useState<number | null>(null)
  const [audioCurrentTime, setAudioCurrentTime] = useState<number>(0)
  const [audioPlaybackRate, setAudioPlaybackRate] = useState<number>(1)
  const audioInstanceRef = useRef<HTMLAudioElement | null>(null)

  // Double-import prevention state for shared notes & calendar entries
  const [importedAttachmentIds, setImportedAttachmentIds] = useState<Set<string>>(() => new Set())

  // Chat Wallpaper state (MSM Heimisch default or custom)
  const [wallpaperConfig, setWallpaperConfig] = useState<ChatWallpaperConfig>(() => loadChatWallpaperConfig())
  const [isWallpaperModalOpen, setIsWallpaperModalOpen] = useState(false)

  const fileInputRef = useRef<HTMLInputElement>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const scrollContainerRef = useRef<HTMLDivElement>(null)
  const justSentRef = useRef<boolean>(false)
  const activeMailboxIdRef = useRef<string>('')

  // Message Editing State
  const [editingMessage, setEditingMessage] = useState<ChatMessage | null>(null)
  const highestIncomingIdAcknowledgedRef = useRef<number>(0)
  const highestIncomingIdDeliveredRef = useRef<number>(0)

  // Real-time typing & voice recording indicator state
  const [partnerActivity, setPartnerActivity] = useState<{ status: 'typing' | 'recording'; username?: string } | null>(null)
  const partnerActivityTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const lastTypingSentRef = useRef<number>(0)

  const handleInputChange = (text: string) => {
    setInputText(text)
    if (!blindMailboxId) return
    const now = Date.now()
    if (text.trim()) {
      if (now - lastTypingSentRef.current > 2500) {
        lastTypingSentRef.current = now
        void sendTypingSignal({
          blind_mailbox_id: blindMailboxId,
          status: 'typing',
          recipient_id: activeContact?.userId ?? null,
        }).catch(() => {})
      }
    } else {
      if (lastTypingSentRef.current > 0) {
        lastTypingSentRef.current = 0
        void sendTypingSignal({
          blind_mailbox_id: blindMailboxId,
          status: 'idle',
          recipient_id: activeContact?.userId ?? null,
        }).catch(() => {})
      }
    }
  }

  const currentUserId = user?.id || 0

  // 1. Initialize local key pair
  useEffect(() => {
    if (!currentUserId) return
    let active = true

    getOrGenerateLocalKeyPair(currentUserId).then(async (kp) => {
      if (!active) return
      setLocalKeyPair(kp)
      try {
        const existing = await getE2eePublicKey(currentUserId)
        if (!existing?.public_key) {
          await setE2eePublicKey(kp.publicKeyJwk)
        }
      } catch {
        // Non-blocking
      }
    }).catch(() => {})

    return () => {
      active = false
    }
  }, [currentUserId])

  // 2. Load Friends, Groups, Team Members, Public Users, Direct Chats, and Stories
  const loadData = async () => {
    try {
      const [friendsData, groupsData, teamsData, storiesData, publicData, directChatsData] = await Promise.all([
        getFriends().catch(() => []),
        getGroups().catch(() => []),
        teamsApi.list().catch(() => []),
        getStories().catch(() => []),
        getPublicProfiles().catch(() => []),
        getDirectChats().catch(() => []),
      ])
      setFriends(friendsData)
      setGroups(groupsData)
      setStories(storiesData)
      setPublicUsers(publicData)
      setDirectChats(directChatsData)

      const teamDetails = await Promise.all(
        teamsData.map(async (t) => {
          try {
            const detail = await teamsApi.get(t.id)
            return { team: t, detail }
          } catch {
            return { team: t, detail: null }
          }
        })
      )

      const membersList: Array<{ member: TeamMember; teamName: string }> = []
      for (const { team, detail } of teamDetails) {
        if (detail && detail.members) {
          for (const m of detail.members) {
            if (m.user_id !== currentUserId) {
              membersList.push({ member: m, teamName: team.name })
            }
          }
        }
      }
      setTeamMembers(membersList)

      // Lokalen Cache für sofortiges 0ms-Laden beim nächsten Aufruf speichern
      try {
        localStorage.setItem(
          CONTACTS_CACHE_KEY,
          JSON.stringify({
            friends: friendsData,
            groups: groupsData,
            teamMembers: membersList,
            publicUsers: publicData,
            stories: storiesData,
          })
        )
      } catch {}
    } catch {
      // Offline fallback
    }
  }

  const handleStoryCreated = (story: ChatStoryItem) => {
    setStories((prev) => [story, ...prev])
    toast.success('Status-Story erfolgreich veröffentlicht!')
  }

  const handleStoryDeleted = (storyId: number) => {
    setStories((prev) => prev.filter((s) => s.id !== storyId))
    toast.success('Status-Story gelöscht.')
  }

  useEffect(() => {
    loadData()
    const interval = setInterval(loadData, 15000)
    return () => clearInterval(interval)
  }, [currentUserId])

  // 3. Handle public invite link join if inviteCode param is present
  useEffect(() => {
    if (!inviteCode || !currentUserId) return
    let active = true

    joinGroupByInvite(inviteCode)
      .then((joinedGroup) => {
        if (!active) return
        toast.success(`Gruppe "${joinedGroup.name}" erfolgreich beigetreten!`)
        setActiveGroup(joinedGroup)
        setActiveContact(null)
        loadData()
        navigate('/chat', { replace: true })
      })
      .catch(() => {
        if (!active) return
        toast.error('Einladungslink ist ungültig oder abgelaufen.')
      })

    return () => {
      active = false
    }
  }, [inviteCode, currentUserId, navigate])

  // Combine Contacts
  const contactsList: ChatContact[] = useMemo(() => {
    const list: ChatContact[] = []
    const seenUserIds = new Set<number>()

    for (const f of friends) {
      if (f.status === 'accepted') {
        const uid = f.user_id ?? f.id
        seenUserIds.add(uid)
        list.push({
          id: f.id,
          userId: uid,
          username: f.username,
          avatarUrl: f.avatar_url,
          status: (f.presence?.status as PresenceStatus) || 'invisible',
          deviceType: f.presence?.device_type,
          activityLabel: f.presence?.activity_label,
          isFriend: true,
          teamName: null,
        })
      }
    }

    for (const { member, teamName } of teamMembers) {
      if (!seenUserIds.has(member.user_id)) {
        seenUserIds.add(member.user_id)
        list.push({
          id: member.user_id,
          userId: member.user_id,
          username: member.username,
          avatarUrl: member.avatar_url,
          status: 'invisible',
          deviceType: null,
          activityLabel: null,
          isFriend: false,
          teamName,
        })
      } else {
        const existing = list.find((c) => c.userId === member.user_id)
        if (existing && !existing.teamName) {
          existing.teamName = teamName
        }
      }
    }

    for (const dc of directChats) {
      if (!seenUserIds.has(dc.other_user_id)) {
        seenUserIds.add(dc.other_user_id)
        list.push({
          id: dc.other_user_id,
          userId: dc.other_user_id,
          username: dc.other_username,
          avatarUrl: dc.other_avatar_url || null,
          status: (dc.presence?.status as PresenceStatus) || 'invisible',
          deviceType: dc.presence?.device_type,
          activityLabel: dc.presence?.activity_label,
          isFriend: dc.is_friend,
          teamName: null,
          isPublicUser: dc.other_privacy === 'public',
        })
      }
    }

    for (const p of publicUsers) {
      if (!seenUserIds.has(p.user_id)) {
        seenUserIds.add(p.user_id)
        list.push({
          id: p.user_id,
          userId: p.user_id,
          username: p.username,
          avatarUrl: p.avatar_url || null,
          status: (p.presence?.status as PresenceStatus) || 'invisible',
          deviceType: p.presence?.device_type,
          activityLabel: p.presence?.activity_label,
          isFriend: Boolean(p.is_friend),
          teamName: null,
          isPublicUser: true,
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
      const statusOrder: Record<string, number> = { online: 0, away: 1, invisible: 2 }
      const diff = (statusOrder[a.status] ?? 3) - (statusOrder[b.status] ?? 3)
      if (diff !== 0) return diff
      return a.username.localeCompare(b.username)
    })
  }, [friends, teamMembers, publicUsers, directChats])

  const filteredContacts = useMemo(() => {
    return contactsList.filter((c) => {
      if (filterTab === 'groups') return false
      if (filterTab === 'friends' && !c.isFriend) return false
      if (filterTab === 'teams' && !c.teamName) return false
      if (filterTab === 'public' && !c.isPublicUser) return false
      if (searchQuery.trim()) {
        const q = searchQuery.toLowerCase()
        return (
          c.username.toLowerCase().includes(q) ||
          (c.teamName && c.teamName.toLowerCase().includes(q))
        )
      }
      return true
    })
  }, [contactsList, filterTab, searchQuery])

  const filteredGroups = useMemo(() => {
    if (filterTab === 'friends' || filterTab === 'teams' || filterTab === 'public') return []
    return groups.filter((g) => {
      if (searchQuery.trim()) {
        const q = searchQuery.toLowerCase()
        return (
          g.name.toLowerCase().includes(q) ||
          (g.description && g.description.toLowerCase().includes(q))
        )
      }
      return true
    })
  }, [groups, filterTab, searchQuery])

  // Mailbox-Verzeichnis und Zuordnungen im Benachrichtigungs-Store registrieren
  useEffect(() => {
    if (!currentUserId) return
    let active = true
    const store = useMessengerNotificationStore.getState()

    for (const c of contactsList) {
      deriveBlindMailboxId(currentUserId, c.userId).then((mid) => {
        if (!active) return
        setContactMailboxMap((prev) => (prev[c.userId] === mid ? prev : { ...prev, [c.userId]: mid }))
        store.registerMailbox(mid, {
          name: c.username,
          avatarUrl: c.avatarUrl,
          isGroup: false,
          userId: c.userId,
        })
      }).catch(() => {})
    }

    for (const g of groups) {
      deriveGroupBlindMailboxId(g.id).then((mid) => {
        if (!active) return
        setGroupMailboxMap((prev) => (prev[g.id] === mid ? prev : { ...prev, [g.id]: mid }))
        store.registerMailbox(mid, {
          name: g.name,
          avatarUrl: g.avatar_url,
          isGroup: true,
          groupId: g.id,
        })
      }).catch(() => {})
    }

    return () => {
      active = false
    }
  }, [contactsList, groups, currentUserId])

  // Stories grouped for Tray and Status views
  const myStories = useMemo(() => {
    return stories.filter((s) => s.user_id === currentUserId)
  }, [stories, currentUserId])

  const friendsStoriesGrouped = useMemo(() => {
    const map = new Map<number, ChatStoryItem[]>()
    for (const story of stories) {
      if (story.user_id === currentUserId) continue
      const list = map.get(story.user_id) || []
      list.push(story)
      map.set(story.user_id, list)
    }
    const grouped = Array.from(map.entries()).map(([userId, userStories]) => {
      const contact = contactsList.find((c) => c.userId === userId)
      const first = userStories[0]
      const hasUnseen = userStories.some((s) => !seenStoryIds.has(s.id))
      return {
        userId,
        username: contact?.username || first?.username || 'Freund',
        avatarUrl: contact?.avatarUrl || first?.avatar_url,
        stories: userStories,
        latestStory: userStories[userStories.length - 1],
        hasUnseen,
      }
    })
    return grouped.sort((a, b) => {
      if (a.hasUnseen && !b.hasUnseen) return -1
      if (!a.hasUnseen && b.hasUnseen) return 1
      return 0
    })
  }, [stories, currentUserId, contactsList, seenStoryIds])

  const openStoryViewerForUser = (userStories: ChatStoryItem[], startIndex = 0) => {
    setActiveViewerStories(userStories)
    setViewerStoryIndex(startIndex)
    setIsViewerStoryOpen(true)
    markStoriesAsSeen(userStories)
  }

  // Auto-select contact if userId query parameter is present
  useEffect(() => {
    if (queryUserId && contactsList.length > 0) {
      const match = contactsList.find((c) => c.userId === Number(queryUserId))
      if (match && activeContact?.userId !== match.userId) {
        setActiveContact(match)
        setActiveGroup(null)
      }
    }
  }, [queryUserId, contactsList, activeContact])

  // 4. When active contact or active group changes, derive mailbox ID
  useEffect(() => {
    let active = true

    if (activeGroup) {
      setMessages([])
      setBlindMailboxId('')
      activeMailboxIdRef.current = ''
      deriveGroupBlindMailboxId(activeGroup.id).then((mid) => {
        if (active) {
          activeMailboxIdRef.current = mid
          setBlindMailboxId(mid)
          // Sofort aus lokalem Cache laden (0ms Ladezeit)
          try {
            const raw = localStorage.getItem(getChatCacheKey(mid))
            if (raw) {
              const parsed = JSON.parse(raw)
              if (Array.isArray(parsed) && parsed.length > 0) {
                setMessages(parsed)
              }
            }
          } catch {}
          useMessengerNotificationStore.getState().setActiveMailboxId(mid)
        }
      })
    } else if (activeContact && currentUserId) {
      const targetUserId = activeContact.userId
      setMessages([])
      setBlindMailboxId('')
      activeMailboxIdRef.current = ''

      deriveBlindMailboxId(currentUserId, targetUserId).then((mid) => {
        if (active) {
          activeMailboxIdRef.current = mid
          setBlindMailboxId(mid)
          // Sofort aus lokalem Cache laden (0ms Ladezeit)
          try {
            const raw = localStorage.getItem(getChatCacheKey(mid))
            if (raw) {
              const parsed = JSON.parse(raw)
              if (Array.isArray(parsed) && parsed.length > 0) {
                setMessages(parsed)
              }
            }
          } catch {}
          useMessengerNotificationStore.getState().setActiveMailboxId(mid)
        }
      })

    } else {
      activeMailboxIdRef.current = ''
      highestIncomingIdAcknowledgedRef.current = 0
      highestIncomingIdDeliveredRef.current = 0
      setBlindMailboxId('')
      setMessages([])
      useMessengerNotificationStore.getState().setActiveMailboxId(null)
    }

    return () => {
      active = false
      activeMailboxIdRef.current = ''
      highestIncomingIdAcknowledgedRef.current = 0
      highestIncomingIdDeliveredRef.current = 0
      useMessengerNotificationStore.getState().setActiveMailboxId(null)
    }
  }, [activeContact, activeGroup, currentUserId])

  // Helper: send an E2EE control envelope (e.g. read_receipt, edit_message, delete_message)
  const sendE2eeControlMessage = async (payloadObj: Record<string, unknown>) => {
    if (!blindMailboxId || !currentUserId || (!activeContact && !activeGroup)) return
    try {
      const payload = JSON.stringify(payloadObj)
      let ciphertext: string
      if (activeGroup) {
        ciphertext = await encryptGroupE2eeMessage(payload, activeGroup.id)
        await relayE2eeEnvelope({
          blind_mailbox_id: blindMailboxId,
          ciphertext_envelope: ciphertext,
        })
      } else if (activeContact) {
        const targetUserId = activeContact.userId
        ciphertext = await encryptE2eeMessage(payload, currentUserId, targetUserId)
        await relayE2eeEnvelope({
          blind_mailbox_id: blindMailboxId,
          ciphertext_envelope: ciphertext,
          recipient_id: targetUserId,
        })
      }
    } catch {
      // Control message failure is non-fatal
    }
  }

  // 5. Load and decrypt messages (non-flickering background sync + real-time)
  const loadMessages = async (isInitial = false) => {
    const currentMid = blindMailboxId
    if (!currentMid || !currentUserId) return
    if (activeMailboxIdRef.current && activeMailboxIdRef.current !== currentMid) return
    if (isInitial && messages.length === 0) {
      setLoadingMessages(true)
    }
    try {
      const envelopes = await fetchE2eeEnvelopes(currentMid)
      if (activeMailboxIdRef.current && activeMailboxIdRef.current !== currentMid) return
      const decryptedList: ChatMessage[] = []

      // Dictionaries to track edits, deletions, and read receipts across envelopes
      const editMap = new Map<number, { newText: string; editedAt: string }>()
      const deleteMap = new Map<number, { deletedAt: string }>()
      let maxPartnerReadId = 0
      let maxPartnerDeliveredId = 0
      let maxIncomingId = 0

      const decryptedEnvelopes = await Promise.all(
        envelopes.map(async (env) => {
          try {
            let plain = ''
            if (activeGroup) {
              plain = await decryptGroupE2eeMessage(env.ciphertext_envelope, activeGroup.id)
            } else if (activeContact) {
              const targetUserId = activeContact.userId
              if (env.ciphertext_envelope.startsWith('sv-e2ee-hybrid-v1:')) {
                if (localKeyPair) {
                  plain = await decryptE2eeHybrid(env.ciphertext_envelope, localKeyPair.privateKeyJwk)
                } else {
                  throw new Error('Local key not ready')
                }
              } else {
                plain = await decryptE2eeMessage(env.ciphertext_envelope, currentUserId, targetUserId)
              }
            }
            return { env, plain, ok: true }
          } catch {
            return { env, plain: '', ok: false }
          }
        })
      )

      for (const { env, plain, ok } of decryptedEnvelopes) {
        if (!ok || !plain) {
          decryptedList.push({
            id: env.id,
            senderId: activeContact ? activeContact.userId : 0,
            text: 'Verschlüsselte Nachricht',
            createdAt: env.created_at,
            isSelf: false,
          })
          continue
        }

        try {
          const parsed = JSON.parse(plain)
          if (typeof parsed === 'object' && parsed !== null) {
            // 1. Read receipt control packet
            if (parsed.type === 'read_receipt') {
              const readUpTo = Number(parsed.read_up_to_id || 0)
              const readerId = Number(parsed.reader_id || 0)
              if (readerId !== currentUserId) {
                if (readUpTo > maxPartnerReadId) {
                  maxPartnerReadId = readUpTo
                }
                if (readUpTo > maxPartnerDeliveredId) {
                  maxPartnerDeliveredId = readUpTo
                }
              }
              continue
            }

            // 1b. Delivery receipt control packet
            if (parsed.type === 'delivery_receipt') {
              const deliveredUpTo = Number(parsed.delivered_up_to_id || 0)
              const receiverId = Number(parsed.receiver_id || 0)
              if (receiverId !== currentUserId && deliveredUpTo > maxPartnerDeliveredId) {
                maxPartnerDeliveredId = deliveredUpTo
              }
              continue
            }

            // 2. Edit message control packet
            if (parsed.type === 'edit_message') {
              const targetId = Number(parsed.target_id || 0)
              if (targetId && parsed.new_text) {
                editMap.set(targetId, {
                  newText: String(parsed.new_text),
                  editedAt: String(parsed.edited_at || env.created_at),
                })
              }
              continue
            }

            // 3. Delete message control packet
            if (parsed.type === 'delete_message') {
              const targetId = Number(parsed.target_id || 0)
              if (targetId) {
                deleteMap.set(targetId, {
                  deletedAt: String(parsed.deleted_at || env.created_at),
                })
              }
              continue
            }

            // Normal Chat Message
            let senderId = parsed.sender_id || (activeContact ? activeContact.userId : 0)
            let senderName = parsed.sender_name || parsed.sender_username
            let isSelf = senderId === currentUserId

            if (!isSelf && env.id > maxIncomingId) {
              maxIncomingId = env.id
            }

            decryptedList.push({
              id: env.id,
              senderId,
              senderName,
              text: parsed.text || '',
              createdAt: env.created_at,
              isSelf,
              noteAttachment: parsed.note_attachment,
              calendarAttachment: parsed.calendar_attachment,
              imageAttachment: parsed.image_attachment,
              audioAttachment: parsed.audio_attachment,
              fileAttachment: parsed.file_attachment,
              stickerAttachment: parsed.sticker_attachment,
              storyReply: parsed.story_reply,
            })
            continue
          }
        } catch {
          // Legacy / simple text fallback
          let text = plain
          let isSelf = false
          let senderId = activeContact ? activeContact.userId : 0
          if (plain.startsWith('[ME]:')) {
            text = plain.replace('[ME]:', '')
            isSelf = true
            senderId = currentUserId
          }
          if (!isSelf && env.id > maxIncomingId) {
            maxIncomingId = env.id
          }
          decryptedList.push({
            id: env.id,
            senderId,
            text,
            createdAt: env.created_at,
            isSelf,
          })
        }
      }

      // Apply Edits, Deletions, and Read Status
      // Gelöscht = gelöscht. Kein Originaltext wird aufbewahrt (Zero Knowledge).
      const processedList: ChatMessage[] = decryptedList.map((msg) => {
        let text = msg.text
        let isEdited = false
        let editedAt: string | undefined = undefined
        let isDeleted = false
        let deletedAt: string | undefined = undefined
        let originalText: string | undefined = undefined

        if (editMap.has(msg.id)) {
          const editInfo = editMap.get(msg.id)!
          originalText = text
          text = editInfo.newText
          isEdited = true
          editedAt = editInfo.editedAt
        }

        if (deleteMap.has(msg.id)) {
          const delInfo = deleteMap.get(msg.id)!
          isDeleted = true
          deletedAt = delInfo.deletedAt
          // Kein originalText bei Löschung — gelöscht ist gelöscht.
          originalText = undefined
        }

        // Dynamisches Häkchen-System (WhatsApp-Style):
        // 1. Gelesen: Gesprächspartner hat die Nachricht quittiert (maxPartnerReadId >= msg.id)
        // 2. Zugestellt: Gesprächspartner hat die Nachricht empfangen (maxPartnerDeliveredId >= msg.id oder bereits gelesen)
        const isRead = msg.isSelf && maxPartnerReadId >= msg.id
        const isDelivered = msg.isSelf && (isRead || maxPartnerDeliveredId >= msg.id)

        return {
          ...msg,
          text,
          isEdited,
          editedAt,
          isDeleted,
          deletedAt,
          originalText,
          isDelivered,
          isRead,
        }
      })

      // Check for incoming messages to trigger device notifications
      if (processedList.length > 0) {
        const lastMsg = processedList[processedList.length - 1]
        if (
          lastMsg &&
          !lastMsg.isSelf &&
          lastMsg.id > lastNotifiedMessageIdRef.current
        ) {
          lastNotifiedMessageIdRef.current = lastMsg.id
          if (!isInitial) {
            void sendeGeraeteBenachrichtigung({
              titel: lastMsg.senderName ? `Neue Nachricht von ${lastMsg.senderName}` : 'Neue Nachricht',
              text:
                lastMsg.text ||
                (lastMsg.imageAttachment
                  ? '📷 Foto'
                  : lastMsg.audioAttachment
                  ? '🎙️ Sprachnachricht'
                  : lastMsg.fileAttachment
                  ? `📎 ${lastMsg.fileAttachment.name}`
                  : 'Neue Nachricht'),
            })
          }
        }
      }

      // Abort if the user has navigated to another chat in the meantime
      if (activeMailboxIdRef.current && activeMailboxIdRef.current !== currentMid) return

      setMessages(processedList)

      // Kürzliche Nachrichten lokal cachen für 0ms Sofort-Laden beim nächsten Aufruf
      try {
        localStorage.setItem(getChatCacheKey(currentMid), JSON.stringify(processedList.slice(-80)))
      } catch {}
      // Ungelesen-Zähler zurücksetzen
      markAsRead(currentMid)

      // Prüfen, ob der Ziel-Kontakt blockiert ist: Wenn blockiert, werden keinerlei
      // Zustell- oder Lesequittungen (delivery_receipt, read_receipt) an die Mailbox gesendet!
      // Dadurch verbleibt die Nachricht beim blockierten Absender dauerhaft auf genau 1 grauem Häkchen (✓).
      const isTargetBlocked = activeContact ? isBlocked(activeContact.userId) : false

      // Sende Zustellbestätigung (delivery_receipt), sobald neue Nachrichten empfangen wurden
      if (
        maxIncomingId > 0 &&
        maxIncomingId > highestIncomingIdDeliveredRef.current &&
        !isTargetBlocked
      ) {
        highestIncomingIdDeliveredRef.current = maxIncomingId
        void sendE2eeControlMessage({
          type: 'delivery_receipt',
          delivered_up_to_id: maxIncomingId,
          receiver_id: currentUserId,
          timestamp: new Date().toISOString(),
        })
      }

      // Send read receipt if there are new incoming unacknowledged messages
      if (
        maxIncomingId > 0 &&
        maxIncomingId > highestIncomingIdAcknowledgedRef.current &&
        readReceiptsEnabled &&
        !isTargetBlocked
      ) {
        highestIncomingIdAcknowledgedRef.current = maxIncomingId
        void sendE2eeControlMessage({
          type: 'read_receipt',
          read_up_to_id: maxIncomingId,
          reader_id: currentUserId,
          timestamp: new Date().toISOString(),
        })
      }
    } catch {
      // Offline fallback
    } finally {
      if (isInitial) {
        setLoadingMessages(false)
      }
    }
  }

  // Action: Edit existing message
  const handleEditMessage = async (msg: ChatMessage, newText: string) => {
    const cleanText = newText.trim()
    if (!cleanText || cleanText === msg.text) {
      setEditingMessage(null)
      return
    }
    try {
      await sendE2eeControlMessage({
        type: 'edit_message',
        target_id: msg.id,
        new_text: cleanText,
        edited_at: new Date().toISOString(),
      })
      toast.success('Nachricht bearbeitet.')
      setEditingMessage(null)
      setInputText('')
      await loadMessages(false)
    } catch {
      toast.error('Fehler beim Bearbeiten der Nachricht.')
    }
  }

  // Action: Delete message with victim protection preserved
  const handleDeleteMessage = async (msg: ChatMessage) => {
    try {
      await sendE2eeControlMessage({
        type: 'delete_message',
        target_id: msg.id,
        deleted_at: new Date().toISOString(),
      })
      toast.success('Nachricht für alle gelöscht.')
      await loadMessages(false)
    } catch {
      toast.error('Fehler beim Löschen der Nachricht.')
    }
  }

  // Real-time SSE event listener for zero-latency incoming messages & typing signals
  useEffect(() => {
    const handleSync = (e: Event) => {
      const ce = e as CustomEvent<any>
      const detail = ce.detail
      if (detail?.type === 'e2ee_blind_message') {
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

    window.addEventListener('msm:sync-event', handleSync)
    return () => {
      window.removeEventListener('msm:sync-event', handleSync)
      if (partnerActivityTimeoutRef.current) {
        clearTimeout(partnerActivityTimeoutRef.current)
      }
    }
  }, [blindMailboxId, currentUserId, user?.device_notifications])

  useEffect(() => {
    setPartnerActivity(null)
    if (partnerActivityTimeoutRef.current) {
      clearTimeout(partnerActivityTimeoutRef.current)
      partnerActivityTimeoutRef.current = null
    }
  }, [blindMailboxId])

  useEffect(() => {
    if (blindMailboxId && (activeContact || activeGroup)) {
      void loadMessages(true)
      const interval = setInterval(() => void loadMessages(false), 5000)
      return () => clearInterval(interval)
    }
  }, [blindMailboxId, activeContact, activeGroup, localKeyPair])

  // Autoscroll
  useEffect(() => {
    const container = scrollContainerRef.current
    if (!container) return
    const isNearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 120
    if (justSentRef.current || isNearBottom) {
      messagesEndRef.current?.scrollIntoView?.({ behavior: 'smooth' })
      justSentRef.current = false
    }
  }, [messages])

  // 6. Send message (text, note, cal, img, audio, file, sticker)
  const handleSendMessage = async (
    customText?: string,
    note?: NoteAttachment,
    cal?: CalendarAttachment,
    img?: ImageAttachment,
    audio?: AudioAttachment,
    file?: FileAttachment,
    sticker?: StickerAttachment,
    storyReply?: StoryReplyAttachment
  ) => {
    // If currently editing a message, redirect to edit handler
    if (editingMessage) {
      const textToSave = customText !== undefined ? customText : inputText
      await handleEditMessage(editingMessage, textToSave)
      return
    }

    const rawText = customText !== undefined ? customText : inputText.trim()
    if (
      (!rawText && !note && !cal && !img && !audio && !file && !sticker && !storyReply) ||
      (!activeContact && !activeGroup) ||
      !blindMailboxId ||
      !currentUserId ||
      sending
    ) {
      return
    }

    setSending(true)
    try {
      const payloadObj: Record<string, unknown> = {
        sender_id: currentUserId,
        sender_name: user?.username || 'Ich',
        text: rawText,
        timestamp: new Date().toISOString(),
      }

      let finalImg = img
      let finalFile = file

      const cryptoContext: AttachmentCryptoContext = activeGroup
        ? { groupId: activeGroup.id }
        : { userAId: currentUserId, userBId: activeContact?.userId }

      if (img && img.dataUrl && !img.mediaId) {
        try {
          const uploaded = await uploadEncryptedChatAttachment(
            img.dataUrl,
            img.name || 'image.png',
            blindMailboxId,
            cryptoContext,
            'image/png',
            { groupId: activeGroup?.id, recipientId: activeContact?.userId }
          )
          finalImg = {
            ...img,
            mediaId: uploaded.id,
          }
        } catch {
          // Fallback if media upload fails
        }
      }

      if (file && file.dataUrl && !file.mediaId) {
        try {
          const uploaded = await uploadEncryptedChatAttachment(
            file.dataUrl,
            file.name || 'attachment.bin',
            blindMailboxId,
            cryptoContext,
            file.mimeType || 'application/octet-stream',
            { groupId: activeGroup?.id, recipientId: activeContact?.userId }
          )
          finalFile = {
            ...file,
            mediaId: uploaded.id,
          }
        } catch {
          // Fallback if media upload fails
        }
      }

      if (note) payloadObj.note_attachment = note
      if (cal) payloadObj.calendar_attachment = cal
      if (finalImg) payloadObj.image_attachment = finalImg
      if (audio) payloadObj.audio_attachment = audio
      if (finalFile) payloadObj.file_attachment = finalFile
      if (sticker) payloadObj.sticker_attachment = sticker
      if (storyReply) payloadObj.story_reply = storyReply

      const payload = JSON.stringify(payloadObj)
      let ciphertext: string

      if (activeGroup) {
        ciphertext = await encryptGroupE2eeMessage(payload, activeGroup.id)
        await relayE2eeEnvelope({
          blind_mailbox_id: blindMailboxId,
          ciphertext_envelope: ciphertext,
        })
      } else if (activeContact) {
        const targetUserId = activeContact.userId
        ciphertext = await encryptE2eeMessage(payload, currentUserId, targetUserId)
        await relayE2eeEnvelope({
          blind_mailbox_id: blindMailboxId,
          ciphertext_envelope: ciphertext,
          recipient_id: targetUserId,
        })
      }

      setInputText('')
      setSelectedImage(null)
      setStagedFile(null)
      justSentRef.current = true
      lastTypingSentRef.current = 0
      if (blindMailboxId) {
        void sendTypingSignal({
          blind_mailbox_id: blindMailboxId,
          status: 'idle',
          recipient_id: activeContact?.userId ?? null,
        }).catch(() => {})
      }
      await loadMessages()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Fehler beim Senden'
      toast.error(msg)
    } finally {
      setSending(false)
    }
  }

  // Voice recording handlers
  const startRecording = async () => {
    if (typeof navigator === 'undefined' || !navigator.mediaDevices?.getUserMedia) {
      toast.error('Mikrofon in dieser Browser-Umgebung nicht verfügbar.')
      return
    }

    try {
      const constraints = getAudioTrackConstraints()
      let stream: MediaStream
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: constraints })
      } catch {
        // Fallback without exact deviceId if preferred mic is unavailable
        stream = await navigator.mediaDevices.getUserMedia({
          audio: {
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
          },
        })
      }
      mediaStreamRef.current = stream
      audioChunksRef.current = []

      const mimeType = getSupportedAudioMimeType()
      const options = mimeType ? { mimeType } : undefined
      const recorder = new MediaRecorder(stream, options)

      recorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) {
          audioChunksRef.current.push(e.data)
        }
      }

      recorder.start(100)
      mediaRecorderRef.current = recorder
      setIsRecording(true)
      setRecordingDuration(0)

      if (blindMailboxId) {
        void sendTypingSignal({
          blind_mailbox_id: blindMailboxId,
          status: 'recording',
          recipient_id: activeContact?.userId ?? null,
        }).catch(() => {})
      }

      timerIntervalRef.current = setInterval(() => {
        setRecordingDuration((prev) => prev + 1)
      }, 1000)
    } catch {
      toast.error('Mikrofonzugriff verweigert oder nicht verfügbar.')
    }
  }

  const stopRecording = (shouldSend: boolean) => {
    if (blindMailboxId) {
      void sendTypingSignal({
        blind_mailbox_id: blindMailboxId,
        status: 'idle',
        recipient_id: activeContact?.userId ?? null,
      }).catch(() => {})
    }

    if (timerIntervalRef.current) {
      clearInterval(timerIntervalRef.current)
      timerIntervalRef.current = null
    }

    const duration = recordingDuration
    setIsRecording(false)
    setRecordingDuration(0)

    const recorder = mediaRecorderRef.current
    const stream = mediaStreamRef.current

    if (recorder && recorder.state !== 'inactive') {
      recorder.onstop = () => {
        stream?.getTracks().forEach((t) => t.stop())
        mediaStreamRef.current = null
        mediaRecorderRef.current = null

        if (shouldSend && audioChunksRef.current.length > 0) {
          const mime = recorder.mimeType || getSupportedAudioMimeType() || 'audio/webm'
          const audioBlob = new Blob(audioChunksRef.current, { type: mime })
          const reader = new FileReader()
          reader.onload = () => {
            const dataUrl = reader.result as string
            if (dataUrl) {
              handleSendMessage('', undefined, undefined, undefined, {
                dataUrl,
                durationSeconds: Math.max(1, duration),
                mimeType: mime,
              })
            }
          }
          reader.readAsDataURL(audioBlob)
        }
        audioChunksRef.current = []
      }
      recorder.stop()
    } else {
      stream?.getTracks().forEach((t) => t.stop())
      mediaStreamRef.current = null
      mediaRecorderRef.current = null
      audioChunksRef.current = []
    }
  }

  // Voice playback handler
  const togglePlayAudio = (messageId: number, dataUrl: string) => {
    if (playingAudioId === messageId) {
      if (audioInstanceRef.current) {
        audioInstanceRef.current.pause()
        audioInstanceRef.current.ontimeupdate = null
        audioInstanceRef.current.onended = null
        audioInstanceRef.current.onerror = null
        audioInstanceRef.current = null
      }
      setPlayingAudioId(null)
    } else {
      if (audioInstanceRef.current) {
        audioInstanceRef.current.pause()
        audioInstanceRef.current.ontimeupdate = null
        audioInstanceRef.current.onended = null
        audioInstanceRef.current.onerror = null
        audioInstanceRef.current = null
      }
      const audio = new Audio(dataUrl)
      audio.playbackRate = audioPlaybackRate
      audioInstanceRef.current = audio
      setPlayingAudioId(messageId)
      setAudioCurrentTime(0)

      audio.ontimeupdate = () => {
        setAudioCurrentTime(audio.currentTime)
      }

      audio.onended = () => {
        setPlayingAudioId(null)
        setAudioCurrentTime(0)
      }

      audio.onerror = () => {
        setPlayingAudioId(null)
        toast.error('Sprachnachricht konnte nicht abgespielt werden.')
      }

      audio.play().catch(() => {
        setPlayingAudioId(null)
      })
    }
  }

  // Cycle playback speed between 1x, 1.5x, and 2x (WhatsApp style)
  const cycleAudioPlaybackRate = (e: React.MouseEvent) => {
    e.stopPropagation()
    const rates = [1, 1.5, 2]
    const nextRate = rates[(rates.indexOf(audioPlaybackRate) + 1) % rates.length] || 1
    setAudioPlaybackRate(nextRate)
    if (audioInstanceRef.current) {
      audioInstanceRef.current.playbackRate = nextRate
    }
  }

  // Seek audio playback when clicking anywhere on the waveform
  const handleWaveformSeek = (
    messageId: number,
    dataUrl: string,
    durationSeconds: number,
    e: React.MouseEvent<HTMLDivElement>
  ) => {
    e.stopPropagation()
    const rect = e.currentTarget.getBoundingClientRect()
    if (rect.width <= 0 || durationSeconds <= 0) return
    const clickX = Math.max(0, Math.min(e.clientX - rect.left, rect.width))
    const seekFrac = clickX / rect.width
    const targetTime = seekFrac * durationSeconds

    if (playingAudioId === messageId && audioInstanceRef.current) {
      audioInstanceRef.current.currentTime = targetTime
      setAudioCurrentTime(targetTime)
    } else {
      if (audioInstanceRef.current) {
        audioInstanceRef.current.pause()
        audioInstanceRef.current.ontimeupdate = null
        audioInstanceRef.current.onended = null
        audioInstanceRef.current.onerror = null
        audioInstanceRef.current = null
      }
      const audio = new Audio(dataUrl)
      audio.playbackRate = audioPlaybackRate
      audio.currentTime = targetTime
      audioInstanceRef.current = audio
      setPlayingAudioId(messageId)
      setAudioCurrentTime(targetTime)

      audio.ontimeupdate = () => {
        setAudioCurrentTime(audio.currentTime)
      }
      audio.onended = () => {
        setPlayingAudioId(null)
        setAudioCurrentTime(0)
        audioInstanceRef.current = null
      }
      audio.onerror = () => {
        setPlayingAudioId(null)
        toast.error('Sprachnachricht konnte nicht abgespielt werden.')
      }
      audio.play().catch(() => {
        setPlayingAudioId(null)
      })
    }
  }

  // Cleanup audio playback on unmount
  useEffect(() => {
    return () => {
      if (audioInstanceRef.current) {
        audioInstanceRef.current.pause()
        audioInstanceRef.current.ontimeupdate = null
        audioInstanceRef.current.onended = null
        audioInstanceRef.current.onerror = null
        audioInstanceRef.current = null
      }
      if (timerIntervalRef.current) {
        clearInterval(timerIntervalRef.current)
      }
      mediaStreamRef.current?.getTracks().forEach((t) => t.stop())
    }
  }, [])

  // Handle Photo / Camera capture
  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    if (!file.type.startsWith('image/')) {
      toast.error('Bitte ein gültiges Bild auswählen.')
      return
    }

    const reader = new FileReader()
    reader.onload = (event) => {
      const dataUrl = event.target?.result as string
      if (dataUrl) {
        setSelectedImage({ dataUrl, name: file.name })
      }
    }
    reader.readAsDataURL(file)
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  // Open Note Picker
  const handleOpenNotePicker = async () => {
    try {
      const res = await loadNotesOfflineFirst()
      setUserNotes(res.notes.filter((n) => !n.is_archived))
      setIsNotePickerOpen(true)
    } catch {
      toast.error('Notizen konnten nicht geladen werden.')
    }
  }

  // Open Calendar Event Picker
  const handleOpenCalendarPicker = async () => {
    try {
      const now = new Date()
      const start = new Date(now.getTime() - 30 * 86400000).toISOString()
      const end = new Date(now.getTime() + 90 * 86400000).toISOString()
      const res = await loadCalendarEventsOfflineFirst(start, end)
      setUserEvents(res.events)
      setIsCalendarPickerOpen(true)
    } catch {
      toast.error('Kalendereinträge konnten nicht geladen werden.')
    }
  }

  // Handle Create Group
  const handleCreateGroup = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!groupName.trim()) return
    setCreatingGroup(true)
    try {
      const newGroup = await createGroup({
        name: groupName.trim(),
        description: groupDesc.trim() || undefined,
      })
      toast.success(`Gruppe "${newGroup.name}" erfolgreich erstellt!`)
      setIsCreateGroupOpen(false)
      setGroupName('')
      setGroupDesc('')
      await loadData()
      setActiveGroup(newGroup)
      setActiveContact(null)
    } catch {
      toast.error('Gruppe konnte nicht erstellt werden.')
    } finally {
      setCreatingGroup(false)
    }
  }

  // Copy Group Invite Link
  const handleCopyInviteLink = (group: ChatGroupItem) => {
    const url = `${window.location.origin}/chat/join/${group.invite_code}`
    navigator.clipboard.writeText(url)
    toast.success('Einladungslink in Zwischenablage kopiert!')
  }

  // Leave Group
  const handleLeaveGroup = async (group: ChatGroupItem) => {
    try {
      await leaveGroup(group.id)
      toast.success(`Gruppe "${group.name}" verlassen.`)
      setActiveGroup(null)
      await loadData()
    } catch {
      toast.error('Gruppe konnte nicht verlassen werden.')
    }
  }

  // Delete Group (triggered via Design-DNA Confirmation Dialog)
  const handleDeleteGroup = (group: ChatGroupItem) => {
    setGroupToDelete(group)
  }

  const handleConfirmDeleteGroup = async () => {
    if (!groupToDelete) return
    setIsDeletingGroup(true)
    try {
      await deleteGroup(groupToDelete.id)
      toast.success(`Gruppe "${groupToDelete.name}" gelöscht.`)
      setActiveGroup(null)
      setGroupToDelete(null)
      await loadData()
    } catch {
      toast.error('Gruppe konnte nicht gelöscht werden.')
    } finally {
      setIsDeletingGroup(false)
    }
  }

  // File Attachment Helper (for drag-and-drop and document input)
  const handleFileAttachment = (file: File) => {
    // 1. Storage-Limits vor FileReader-Aufruf prüfen (Schutz vor Riesen-Dateien und Abstürzen)
    const MAX_FILE_BYTES = 25 * 1024 * 1024 // 25 MB Limit
    const MAX_IMAGE_BYTES = 8 * 1024 * 1024 // 8 MB Limit für Bilder

    const isImage = file.type.startsWith('image/')
    const limit = isImage ? MAX_IMAGE_BYTES : MAX_FILE_BYTES
    if (file.size > limit) {
      toast.error(`Datei ist zu groß (maximal ${limit / (1024 * 1024)} MB erlaubt).`)
      return
    }

    // 2. Blockiere ausfuehrbare Dateien clientseitig vorab
    const lowerName = file.name.toLowerCase()
    const blockedExtensions = ['.exe', '.dll', '.bat', '.cmd', '.sh', '.msi', '.vbs', '.ps1', '.elf', '.com', '.scr', '.pif']
    if (blockedExtensions.some((ext) => lowerName.endsWith(ext))) {
      toast.error('Ausführbare Dateien sind aus Sicherheitsgründen im Chat strikt untersagt.')
      return
    }

    if (isImage) {
      const reader = new FileReader()
      reader.onload = (event) => {
        const dataUrl = event.target?.result as string
        if (dataUrl) {
          setSelectedImage({ dataUrl, name: file.name })
        }
      }
      reader.readAsDataURL(file)
      return
    }

    const reader = new FileReader()
    reader.onload = (event) => {
      const dataUrl = event.target?.result as string
      if (dataUrl) {
        setStagedFile({
          name: file.name,
          sizeBytes: file.size,
          mimeType: file.type || 'application/octet-stream',
          dataUrl,
        })
      }
    }
    reader.readAsDataURL(file)
  }

  const handleImportNote = async (note: NoteAttachment, itemKey?: string) => {
    const key = itemKey || `${note.title}_${note.content?.slice(0, 30)}`
    if (importedAttachmentIds.has(key)) {
      toast.success('Diese Notiz wurde bereits in deine Notizen übernommen.')
      return
    }
    try {
      await saveNoteOffline({
        title: note.title || 'Geteilte Notiz',
        content: note.content || '',
        category: note.category || 'personal',
        color: note.color || 'primary',
        is_pinned: false,
        note_type: 'personal',
        team_id: null,
      })
      setImportedAttachmentIds((prev) => new Set([...prev, key]))
      toast.success(`Notiz "${note.title || 'Geteilte Notiz'}" in Notizen gespeichert!`)
    } catch {
      toast.error('Notiz konnte nicht gespeichert werden.')
    }
  }

  const handleImportCalendar = async (cal: CalendarAttachment, itemKey?: string) => {
    const key = itemKey || `${cal.title}_${cal.start}`
    if (importedAttachmentIds.has(key)) {
      toast.success('Dieser Termin wurde bereits in deinen Kalender eingetragen.')
      return
    }
    try {
      await saveCalendarEventOffline({
        title: cal.title || 'Geteilter Termin',
        start_time: cal.start,
        end_time: cal.end,
        description: cal.description || null,
        location: cal.location || null,
        all_day: false,
        color: 'primary',
        event_type: 'personal',
        team_id: null,
        server_id: null,
      })
      setImportedAttachmentIds((prev) => new Set([...prev, key]))
      toast.success(`Termin "${cal.title || 'Geteilter Termin'}" im Kalender eingetragen!`)
    } catch {
      toast.error('Termin konnte nicht im Kalender gespeichert werden.')
    }
  }

  const isChatOpen = Boolean(activeContact || activeGroup)

  return (
    <div className="flex h-full w-full min-h-0 flex-1 flex-col overflow-hidden bg-surface">
      {/* Slim, Compact Header - Only shown in overview mode when no chat is open, maximizing chat space */}
      {!isChatOpen && (
        <header className="h-12 shrink-0 border-b border-outline-variant/20 bg-surface-container/70 backdrop-blur px-3 sm:px-4 flex items-center justify-between z-10">
          <div className="flex items-center gap-2">
            <div className="w-7 h-7 rounded-lg bg-primary/10 flex items-center justify-center text-primary">
              <MessageSquare className="w-4 h-4" />
            </div>
            <span className="font-headline text-body-md font-bold text-primary">Messenger</span>
            <span className="text-[11px] text-on-surface-variant/60 hidden sm:inline">• Chats & Gruppen</span>
          </div>

          <div className="flex items-center gap-1">
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setIsCameraModalOpen(true)}
              className="h-8 w-8 text-on-surface-variant hover:text-primary"
              title="Foto aufnehmen"
              aria-label="Foto aufnehmen"
            >
              <Camera className="w-4 h-4" />
            </Button>

            <Button
              variant="ghost"
              size="icon"
              onClick={() => loadData()}
              className="h-8 w-8 text-on-surface-variant"
              aria-label="Aktualisieren"
              title="Aktualisieren"
            >
              <RefreshCw className="w-3.5 h-3.5" />
            </Button>
          </div>
        </header>
      )}

      {/* Main Split Layout: Left Contact/Group List, Right Chat Area */}
      <div className="flex-1 flex min-h-0 overflow-hidden">
        {/* Left Column: WhatsApp-style Contacts & Groups List */}
        <div
          className={`w-full md:w-80 lg:w-96 shrink-0 flex flex-col min-h-0 border-r border-outline-variant/20 bg-surface-container-low/60 ${
            isChatOpen ? 'hidden md:flex' : 'flex'
          }`}
        >
          {/* Mode Navigation (Chats | Aktuelles | Gruppen) - Available on desktop */}
          <div className="hidden md:flex px-2.5 pt-2 pb-1 border-b border-outline-variant/15 items-center gap-1 bg-surface-container/60">
            <button
              type="button"
              onClick={() => setMobileNavTab('chats')}
              className={`flex-1 py-1.5 px-2 rounded-lg text-xs font-medium flex items-center justify-center gap-1.5 transition-colors ${
                mobileNavTab === 'chats'
                  ? 'bg-primary text-on-primary shadow-xs font-semibold'
                  : 'text-on-surface-variant hover:text-on-surface hover:bg-surface-container-high/60'
              }`}
              aria-label="Desktop-Chats"
            >
              <MessageSquare className="w-3.5 h-3.5" />
              <span>Chats</span>
            </button>
            <button
              type="button"
              onClick={() => setMobileNavTab('updates')}
              className={`flex-1 py-1.5 px-2 rounded-lg text-xs font-medium flex items-center justify-center gap-1.5 transition-colors relative ${
                mobileNavTab === 'updates'
                  ? 'bg-primary text-on-primary shadow-xs font-semibold'
                  : 'text-on-surface-variant hover:text-on-surface hover:bg-surface-container-high/60'
              }`}
              aria-label="Desktop-Aktuelles"
            >
              <Sparkles className="w-3.5 h-3.5" />
              <span>Aktuelles</span>
              {stories.length > 0 && (
                <span className={`w-2 h-2 rounded-full ${mobileNavTab === 'updates' ? 'bg-white' : 'bg-emerald-500 animate-pulse'}`} />
              )}
            </button>
            <button
              type="button"
              onClick={() => setMobileNavTab('community')}
              className={`flex-1 py-1.5 px-2 rounded-lg text-xs font-medium flex items-center justify-center gap-1.5 transition-colors ${
                mobileNavTab === 'community'
                  ? 'bg-primary text-on-primary shadow-xs font-semibold'
                  : 'text-on-surface-variant hover:text-on-surface hover:bg-surface-container-high/60'
              }`}
              aria-label="Desktop-Gruppen"
            >
              <UsersRound className="w-3.5 h-3.5" />
              <span>Gruppen</span>
            </button>
          </div>

          {/* Top Search & Category Tabs */}
          <div className="p-2.5 border-b border-outline-variant/15 space-y-2 bg-surface-container/40">
            <div className="relative">
              <Search className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-on-surface-variant/60" />
              <Input
                value={searchQuery}
                onChange={(e: React.ChangeEvent<HTMLInputElement>) => setSearchQuery(e.target.value)}
                placeholder="Freunde oder Teammitglieder suchen …"
                className="text-xs pl-8 h-8 bg-surface-container-high/60 border-outline-variant/30 focus:border-primary/50 text-on-surface"
              />
            </div>

            {/* WhatsApp Filter Tabs (Chats Mode) - Clean Segmented Control with clear intuitive icons & counts */}
            {mobileNavTab === 'chats' && (
              <div className="grid grid-cols-5 gap-0.5 sm:gap-1 p-1 rounded-xl bg-surface-container-high/50 border border-outline-variant/15 w-full">
                <button
                  type="button"
                  onClick={() => setFilterTab('all')}
                  className={`h-7 rounded-lg flex items-center justify-center gap-1 transition-all text-xs font-semibold ${
                    filterTab === 'all'
                      ? 'bg-primary text-on-primary shadow-xs'
                      : 'text-on-surface-variant hover:text-on-surface hover:bg-surface-container-high/70'
                  }`}
                  title="Alle Chats"
                  aria-label="Alle Chats"
                >
                  <LayoutGrid className="w-3.5 h-3.5 shrink-0" />
                  <span className="text-[10px] leading-none hidden xs:inline">Alle</span>
                </button>

                <button
                  type="button"
                  onClick={() => setFilterTab('groups')}
                  className={`h-7 rounded-lg flex items-center justify-center gap-0.5 sm:gap-1 transition-all text-xs font-semibold ${
                    filterTab === 'groups'
                      ? 'bg-primary text-on-primary shadow-xs'
                      : 'text-on-surface-variant hover:text-on-surface hover:bg-surface-container-high/70'
                  }`}
                  title={`Gruppen (${groups.length})`}
                  aria-label={`Gruppen (${groups.length})`}
                >
                  <UsersRound className="w-3.5 h-3.5 shrink-0" />
                  {groups.length > 0 && (
                    <span
                      className={`text-[9px] px-1 py-0.2 rounded-full font-bold leading-none ${
                        filterTab === 'groups' ? 'bg-white/20 text-white' : 'bg-surface-container-highest text-on-surface-variant'
                      }`}
                    >
                      {groups.length}
                    </span>
                  )}
                </button>

                <button
                  type="button"
                  onClick={() => setFilterTab('friends')}
                  className={`h-7 rounded-lg flex items-center justify-center gap-0.5 sm:gap-1 transition-all text-xs font-semibold ${
                    filterTab === 'friends'
                      ? 'bg-primary text-on-primary shadow-xs'
                      : 'text-on-surface-variant hover:text-on-surface hover:bg-surface-container-high/70'
                  }`}
                  title={`Freunde (${contactsList.filter((c) => c.isFriend).length})`}
                  aria-label={`Freunde (${contactsList.filter((c) => c.isFriend).length})`}
                >
                  <UserCheck className="w-3.5 h-3.5 shrink-0" />
                  {contactsList.some((c) => c.isFriend) && (
                    <span
                      className={`text-[9px] px-1 py-0.2 rounded-full font-bold leading-none ${
                        filterTab === 'friends' ? 'bg-white/20 text-white' : 'bg-surface-container-highest text-on-surface-variant'
                      }`}
                    >
                      {contactsList.filter((c) => c.isFriend).length}
                    </span>
                  )}
                </button>

                <button
                  type="button"
                  onClick={() => setFilterTab('teams')}
                  className={`h-7 rounded-lg flex items-center justify-center gap-0.5 sm:gap-1 transition-all text-xs font-semibold ${
                    filterTab === 'teams'
                      ? 'bg-primary text-on-primary shadow-xs'
                      : 'text-on-surface-variant hover:text-on-surface hover:bg-surface-container-high/70'
                  }`}
                  title={`Teams (${contactsList.filter((c) => c.teamName).length})`}
                  aria-label={`Teams (${contactsList.filter((c) => c.teamName).length})`}
                >
                  <Briefcase className="w-3.5 h-3.5 shrink-0" />
                  {contactsList.some((c) => c.teamName) && (
                    <span
                      className={`text-[9px] px-1 py-0.2 rounded-full font-bold leading-none ${
                        filterTab === 'teams' ? 'bg-white/20 text-white' : 'bg-surface-container-highest text-on-surface-variant'
                      }`}
                    >
                      {contactsList.filter((c) => c.teamName).length}
                    </span>
                  )}
                </button>

                <button
                  type="button"
                  onClick={() => setFilterTab('public')}
                  className={`h-7 rounded-lg flex items-center justify-center gap-0.5 sm:gap-1 transition-all text-xs font-semibold ${
                    filterTab === 'public'
                      ? 'bg-primary text-on-primary shadow-xs'
                      : 'text-on-surface-variant hover:text-on-surface hover:bg-surface-container-high/70'
                  }`}
                  title={`Öffentlich / Entdecken (${contactsList.filter((c) => c.isPublicUser).length})`}
                  aria-label={`Öffentlich / Entdecken (${contactsList.filter((c) => c.isPublicUser).length})`}
                >
                  <Globe className="w-3.5 h-3.5 shrink-0" />
                  <span className="text-[10px] leading-none hidden xs:inline">Entdecken</span>
                  {contactsList.some((c) => c.isPublicUser) && (
                    <span
                      className={`text-[9px] px-1 py-0.2 rounded-full font-bold leading-none ${
                        filterTab === 'public' ? 'bg-white/20 text-white' : 'bg-surface-container-highest text-on-surface-variant'
                      }`}
                    >
                      {contactsList.filter((c) => c.isPublicUser).length}
                    </span>
                  )}
                </button>
              </div>
            )}
          </div>

          {/* Instagram/WhatsApp Stories Tray (Visible in Chats Mode) */}
          {mobileNavTab === 'chats' && !searchQuery.trim() && (
            <div className="px-2.5 py-2 border-b border-outline-variant/15 bg-surface-container/20">
              <div className="flex items-center gap-3 overflow-x-auto no-scrollbar py-1">
                {/* Dein Status Circle */}
                <div className="flex flex-col items-center gap-1 shrink-0 w-14">
                  <div
                    className="relative cursor-pointer group"
                    onClick={() => {
                      if (myStories.length > 0) {
                        openStoryViewerForUser(myStories, 0)
                      } else {
                        setCreateStoryInitialMode('text')
                        setIsCreateStoryOpen(true)
                      }
                    }}
                  >
                    <div
                      className={`w-12 h-12 rounded-full p-0.5 shrink-0 transition-transform group-hover:scale-105 flex items-center justify-center ${
                        myStories.length > 0
                          ? 'bg-gradient-to-tr from-cyan-400 via-sky-500 to-indigo-500 ring-2 ring-primary/30 ring-offset-2 ring-offset-surface'
                          : 'border-2 border-dashed border-outline-variant/70'
                      }`}
                    >
                      <Avatar
                        src={user?.avatar_url}
                        name={user?.username || 'Ich'}
                        size="md"
                      />
                    </div>
                    {myStories.length === 0 ? (
                      <div className="absolute -bottom-0.5 -right-0.5 w-4 h-4 rounded-full bg-primary text-on-primary flex items-center justify-center text-[10px] shadow-sm border-2 border-surface">
                        <Plus className="w-2.5 h-2.5" />
                      </div>
                    ) : (
                      <span className="absolute -top-0.5 -right-0.5 w-4 h-4 rounded-full bg-emerald-500 text-white text-[9px] font-bold flex items-center justify-center border-2 border-surface">
                        {myStories.length}
                      </span>
                    )}
                  </div>
                  <span className="text-[10px] text-on-surface-variant truncate w-full text-center">
                    {myStories.length > 0 ? 'Dein Status' : 'Neu'}
                  </span>
                </div>

                {/* Friends' Stories Circles */}
                {friendsStoriesGrouped.map((group) => (
                  <div
                    key={`tray-user-${group.userId}`}
                    className="flex flex-col items-center gap-1 shrink-0 w-14 cursor-pointer group"
                    onClick={() => openStoryViewerForUser(group.stories, 0)}
                  >
                    <div className="relative shrink-0">
                      <div
                        className={`w-12 h-12 rounded-full p-0.5 shrink-0 transition-all duration-300 group-hover:scale-105 flex items-center justify-center ${
                          group.hasUnseen
                            ? 'bg-gradient-to-tr from-cyan-400 via-indigo-500 to-fuchsia-500 ring-2 ring-primary ring-offset-2 ring-offset-surface shadow-[0_0_14px_rgba(99,102,241,0.65)] animate-pulse'
                            : 'bg-surface-container-highest ring-1 ring-outline-variant/50 opacity-85'
                        }`}
                      >
                        <Avatar
                          src={group.avatarUrl}
                          name={group.username}
                          size="md"
                        />
                      </div>
                      {group.stories.length > 1 && (
                        <span className="absolute -top-0.5 -right-0.5 w-4 h-4 rounded-full bg-indigo-600 text-white text-[9px] font-bold flex items-center justify-center border-2 border-surface">
                          {group.stories.length}
                        </span>
                      )}
                    </div>
                    <span
                      className={`text-[10px] truncate w-full text-center ${
                        group.hasUnseen ? 'text-primary font-bold' : 'text-on-surface-variant font-normal'
                      }`}
                    >
                      {group.username}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* List Scroll Area */}
          <div className="flex-1 overflow-y-auto p-2 space-y-1">
            {/* View 1: Standard Chats Mode */}
            {mobileNavTab === 'chats' && (
              <>
                {/* Groups Section */}
                {(filteredGroups.length > 0 || filterTab === 'all' || filterTab === 'groups') && (
                  <div className="space-y-1 mb-2">
                    <div className="px-2 py-1 text-[11px] font-semibold text-on-surface-variant/70 uppercase tracking-wider flex items-center justify-between">
                      <span>Gruppen</span>
                      <div className="flex items-center gap-1">
                        <span className="text-[10px]">{filteredGroups.length}</span>
                        <button
                          type="button"
                          onClick={() => setIsCreateGroupOpen(true)}
                          className="p-0.5 rounded text-on-surface-variant hover:text-primary transition-colors"
                          aria-label="Neue Gruppe erstellen"
                          title="Neue Gruppe erstellen"
                        >
                          <Plus className="w-3 h-3" />
                        </button>
                      </div>
                    </div>
                    {filteredGroups.map((g) => {
                      const isSelected = activeGroup?.id === g.id
                      const gmid = groupMailboxMap[g.id]
                      const unread = gmid ? (unreadCounts[gmid] || 0) : 0
                      const isMuted = gmid ? isChatMuted(gmid) : false
                      return (
                        <button
                          key={`g-${g.id}`}
                          type="button"
                          onClick={() => {
                            setActiveGroup(g)
                            setActiveContact(null)
                            if (gmid) markAsRead(gmid)
                          }}
                          className={`w-full flex items-center justify-between p-2.5 rounded-xl text-left transition-all ${
                            isSelected
                              ? 'bg-primary/15 border border-primary/30 shadow-xs'
                              : 'hover:bg-surface-container-high/60 border border-transparent'
                          }`}
                        >
                          <div className="flex items-center gap-3 min-w-0 flex-1">
                            <div className="w-9 h-9 rounded-full bg-primary/10 text-primary flex items-center justify-center font-bold text-xs shrink-0">
                              {g.avatar_url ? (
                                <img src={g.avatar_url} alt="" className="w-full h-full rounded-full object-cover" />
                              ) : (
                                <UsersRound className="w-4 h-4" />
                              )}
                            </div>
                            <div className="min-w-0 flex-1">
                              <div className="flex items-center justify-between gap-1">
                                <span className="text-xs font-semibold text-primary truncate">
                                  {g.name}
                                </span>
                                <span className="text-[10px] text-on-surface-variant/60 shrink-0">
                                  {g.member_count} M.
                                </span>
                              </div>
                              <p className="text-[11px] text-on-surface-variant/80 truncate">
                                {g.description || 'Verschlüsselte Gruppe'}
                              </p>
                            </div>
                          </div>
                          <div className="flex items-center gap-1.5 shrink-0 ml-2">
                            {isMuted && (
                              <BellOff className="w-3.5 h-3.5 text-on-surface-variant/50" />
                            )}
                            {unread > 0 && (
                              <span className="inline-flex items-center justify-center px-1.5 py-0.5 text-[10px] font-bold rounded-full bg-primary text-on-primary min-w-[18px]">
                                {unread > 99 ? '99+' : unread}
                              </span>
                            )}
                          </div>
                        </button>
                      )
                    })}
                  </div>
                )}

                {/* Contacts Section */}
                {filteredContacts.length > 0 && (
                  <div className="space-y-1">
                    {filteredGroups.length > 0 && (
                      <div className="px-2 py-1 text-[11px] font-semibold text-on-surface-variant/70 uppercase tracking-wider flex items-center justify-between">
                        <span>Direktnachrichten</span>
                        <span className="text-[10px]">{filteredContacts.length}</span>
                      </div>
                    )}
                    {filteredContacts.map((c) => {
                      const isSelected = activeContact?.userId === c.userId
                      const cmid = contactMailboxMap[c.userId]
                      const unread = cmid ? (unreadCounts[cmid] || 0) : 0
                      const isMuted = cmid ? isChatMuted(cmid) : false
                      const isUserBlocked = isBlocked(c.userId)
                      return (
                        <button
                          key={`${c.isFriend ? 'f' : 't'}-${c.userId}`}
                          type="button"
                          onClick={() => {
                            setActiveContact(c)
                            setActiveGroup(null)
                            if (cmid) markAsRead(cmid)
                          }}
                          className={`w-full flex items-center justify-between p-2.5 rounded-xl text-left transition-all ${
                            isSelected
                              ? 'bg-primary/15 border border-primary/30 shadow-xs'
                              : 'hover:bg-surface-container-high/60 border border-transparent'
                          }`}
                        >
                          <div className="flex items-center gap-3 min-w-0 flex-1">
                            <div className="relative shrink-0">
                              <Avatar src={c.avatarUrl} name={c.username} size="sm" />
                              <StatusDot status={c.status} size="sm" className="absolute bottom-0 right-0" />
                            </div>
                            <div className="min-w-0 flex-1">
                              <div className="flex items-center gap-1.5">
                                <span className="text-xs font-semibold text-primary truncate">
                                  {c.username}
                                </span>
                                {isUserBlocked && (
                                  <span className="text-[9px] px-1 rounded bg-status-error/15 text-status-error font-medium">
                                    Blockiert
                                  </span>
                                )}
                                {c.isPublicUser && !c.isFriend && !c.teamName && !isUserBlocked && (
                                  <span className="text-[9px] px-1.5 py-0.2 rounded-md bg-primary/10 text-primary font-medium flex items-center gap-0.5">
                                    <Globe className="w-2.5 h-2.5" />
                                    <span>Öffentlich</span>
                                  </span>
                                )}
                                <DeviceBadge deviceType={c.deviceType} />
                              </div>
                              {c.teamName && (
                                <p className="text-[10px] text-tertiary truncate flex items-center gap-1">
                                  <UsersRound className="w-2.5 h-2.5" />
                                  <span>{c.teamName}</span>
                                </p>
                              )}
                              {c.isPublicUser && !c.isFriend && !c.teamName && (
                                <p className="text-[10px] text-on-surface-variant/70 truncate flex items-center gap-1">
                                  <span>E2EE Chat bereit</span>
                                </p>
                              )}
                              {c.activityLabel && !c.teamName && (
                                <p className="text-[10px] text-on-surface-variant/80 truncate">
                                  {c.activityLabel}
                                </p>
                              )}
                            </div>
                          </div>
                          <div className="flex items-center gap-1.5 shrink-0 ml-2">
                            {isMuted && (
                              <BellOff className="w-3.5 h-3.5 text-on-surface-variant/50" />
                            )}
                            {unread > 0 && (
                              <span className="inline-flex items-center justify-center px-1.5 py-0.5 text-[10px] font-bold rounded-full bg-primary text-on-primary min-w-[18px]">
                                {unread > 99 ? '99+' : unread}
                              </span>
                            )}
                          </div>
                        </button>
                      )
                    })}
                  </div>
                )}

                {filteredContacts.length === 0 && filteredGroups.length === 0 && (
                  <p className="py-12 text-center text-xs text-on-surface-variant/70">
                    Keine Kontakte oder Gruppen gefunden.
                  </p>
                )}
              </>
            )}

            {/* View 2: Aktuelles (Stories / 24h Status Updates & Contacts Presence) */}
            {mobileNavTab === 'updates' && (
              <div className="space-y-4 p-1">
                {/* Clean Top Bar: Title & Direct Add Action */}
                <div className="flex items-center justify-between px-1">
                  <div className="flex items-center gap-2">
                    <Sparkles className="w-4 h-4 text-primary" />
                    <span className="text-sm font-headline font-bold text-on-surface">Status</span>
                  </div>
                  <Button
                    type="button"
                    variant="primary"
                    size="sm"
                    onClick={() => {
                      setCreateStoryInitialMode('text')
                      setPendingStoryPhotoUrl(null)
                      setIsCreateStoryOpen(true)
                    }}
                    className="h-8 text-xs gap-1.5 px-3 rounded-xl font-medium"
                    title="Status hinzufügen"
                  >
                    <Plus className="w-3.5 h-3.5" />
                    <span>Hinzufügen</span>
                  </Button>
                </div>

                {/* My Status Card with crisp contrast and clear visual identity */}
                <div className="p-3.5 rounded-2xl bg-surface-container/70 border border-outline-variant/35 shadow-xs transition-colors hover:bg-surface-container/90">
                  <div className="flex items-center justify-between mb-3">
                    <span className="text-xs font-headline font-bold text-on-surface">Mein Status</span>
                    <span className="text-[11px] text-on-surface-variant font-medium">24h sichtbar</span>
                  </div>

                  <div className="flex items-center gap-3">
                    <div
                      className="relative cursor-pointer shrink-0"
                      onClick={() => {
                        if (myStories.length > 0) {
                          openStoryViewerForUser(myStories, 0)
                        } else {
                          setCreateStoryInitialMode('text')
                          setPendingStoryPhotoUrl(null)
                          setIsCreateStoryOpen(true)
                        }
                      }}
                    >
                      <div className={`w-12 h-12 rounded-full p-0.5 shrink-0 flex items-center justify-center ${
                        myStories.length > 0
                          ? 'bg-gradient-to-tr from-cyan-400 via-sky-500 to-indigo-500 ring-2 ring-primary/40 ring-offset-2 ring-offset-surface'
                          : 'border-2 border-dashed border-outline-variant/80'
                      }`}>
                        <Avatar
                          src={user?.avatar_url}
                          name={user?.username || 'Ich'}
                          size="md"
                        />
                      </div>
                      <div className="absolute -bottom-1 -right-1 w-5 h-5 rounded-full bg-primary text-on-primary flex items-center justify-center text-xs shadow-md border-2 border-surface">
                        <Plus className="w-3 h-3" />
                      </div>
                    </div>
                    <div
                      className="min-w-0 flex-1 cursor-pointer"
                      onClick={() => {
                        if (myStories.length > 0) {
                          openStoryViewerForUser(myStories, 0)
                        } else {
                          setCreateStoryInitialMode('text')
                          setPendingStoryPhotoUrl(null)
                          setIsCreateStoryOpen(true)
                        }
                      }}
                    >
                      <div className="text-xs font-semibold text-on-surface truncate">
                        {myStories.length > 0 ? 'Status ansehen' : 'Status teilen'}
                      </div>
                      <p className="text-[11px] text-on-surface-variant truncate">
                        {myStories.length > 0
                          ? `${myStories.length} aktive Story${myStories.length === 1 ? '' : 's'} • Tippen zum Abspielen`
                          : 'Foto aufnehmen oder Text teilen'}
                      </p>
                    </div>
                  </div>
                </div>

                {/* Friends' Stories Section */}
                <div className="space-y-2">
                  <div className="px-1 text-[11px] font-semibold text-on-surface-variant/80 uppercase tracking-wider flex items-center justify-between">
                    <span>Kürzliche Updates</span>
                    <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-surface-container font-mono text-on-surface-variant">
                      {friendsStoriesGrouped.length}
                    </span>
                  </div>

                  {friendsStoriesGrouped.length === 0 ? (
                    <div className="p-4 rounded-xl bg-surface-container/40 border border-outline-variant/25 text-center text-xs text-on-surface-variant">
                      Noch keine Status-Updates von Freunden vorhanden.
                    </div>
                  ) : (
                    <div className="space-y-1.5">
                      {friendsStoriesGrouped.map((grp) => (
                        <div
                          key={`story-grp-${grp.userId}`}
                          onClick={() => openStoryViewerForUser(grp.stories, 0)}
                          className="flex items-center gap-3 p-2.5 rounded-xl border border-outline-variant/30 bg-surface-container/60 hover:bg-surface-container-high/80 cursor-pointer transition-colors"
                        >
                          <div className="relative shrink-0">
                            <div
                              className={`w-12 h-12 rounded-full p-0.5 shrink-0 transition-all duration-300 flex items-center justify-center ${
                                grp.hasUnseen
                                  ? 'bg-gradient-to-tr from-cyan-400 via-indigo-500 to-fuchsia-500 ring-2 ring-primary ring-offset-2 ring-offset-surface shadow-[0_0_14px_rgba(99,102,241,0.65)] animate-pulse'
                                  : 'bg-surface-container-highest ring-1 ring-outline-variant/50 opacity-85'
                              }`}
                            >
                              <Avatar
                                src={grp.avatarUrl}
                                name={grp.username}
                                size="md"
                              />
                            </div>
                            {grp.stories.length > 1 && (
                              <span className="absolute -top-1 -right-1 w-4 h-4 rounded-full bg-indigo-600 text-white text-[9px] font-bold flex items-center justify-center border border-surface">
                                {grp.stories.length}
                              </span>
                            )}
                          </div>
                          <div className="min-w-0 flex-1">
                            <div className="text-xs font-semibold text-on-surface truncate">
                              {grp.username}
                            </div>
                            <div className="text-[10px] text-on-surface-variant flex items-center gap-1">
                              <Clock className="w-3 h-3" />
                              <span>
                                {grp.latestStory
                                  ? new Date(grp.latestStory.created_at).toLocaleTimeString([], {
                                      hour: '2-digit',
                                      minute: '2-digit',
                                    })
                                  : ''}
                              </span>
                              {grp.stories.length > 1 && (
                                <span className="text-on-surface-variant/70">• {grp.stories.length} Updates</span>
                              )}
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                {/* Contacts Activity Section */}
                <div className="space-y-2 pt-2 border-t border-outline-variant/20">
                  <div className="px-1 text-[11px] font-semibold text-on-surface-variant/70 uppercase tracking-wider">
                    Aktivität deiner Kontakte
                  </div>
                  <div className="space-y-1">
                    {contactsList.map((c) => (
                      <div
                        key={`update-${c.userId}`}
                        className="flex items-center justify-between p-2.5 rounded-xl border border-outline-variant/20 bg-surface-container-lowest/60"
                      >
                        <div className="flex items-center gap-3 min-w-0">
                          <div className="relative shrink-0">
                            <Avatar src={c.avatarUrl} name={c.username} size="sm" />
                            <StatusDot status={c.status} size="sm" className="absolute bottom-0 right-0" />
                          </div>
                          <div className="min-w-0 flex-1">
                            <div className="text-xs font-semibold text-primary truncate flex items-center gap-1.5">
                              <span>{c.username}</span>
                              <DeviceBadge deviceType={c.deviceType} />
                            </div>
                            <div className="text-[10px] text-on-surface-variant/80 truncate">
                              {c.activityLabel || (c.status === 'online' ? 'Online' : c.status === 'away' ? 'Abwesend' : 'Offline')}
                            </div>
                          </div>
                        </div>

                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          onClick={() => {
                            setActiveContact(c)
                            setActiveGroup(null)
                          }}
                          className="text-xs h-7 px-2 text-primary"
                        >
                          Chat
                        </Button>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            )}

            {/* View 3: Community (Groups & public invite links) */}
            {mobileNavTab === 'community' && (
              <div className="space-y-3 p-1">
                <div className="flex items-center justify-between px-1 pt-1">
                  <div>
                    <div className="text-xs font-headline font-bold text-primary flex items-center gap-1.5">
                      <UsersRound className="w-3.5 h-3.5" />
                      <span>Communities & Gruppen</span>
                      <span className="text-[10px] text-on-surface-variant/70">({groups.length})</span>
                    </div>
                    <p className="text-[11px] text-on-surface-variant/80">
                      Öffentliche und private Gruppen mit Einladungslink
                    </p>
                  </div>
                  <Button
                    type="button"
                    variant="primary"
                    size="sm"
                    onClick={() => setIsCreateGroupOpen(true)}
                    className="h-7 text-xs gap-1 px-2.5 rounded-full"
                    aria-label="Neue Gruppe erstellen"
                    title="Neue Gruppe erstellen"
                  >
                    <Plus className="w-3.5 h-3.5" />
                    <span>Gruppe erstellen</span>
                  </Button>
                </div>

                <div className="space-y-1.5 pt-1">
                  {groups.length === 0 ? (
                    <p className="py-6 text-center text-xs text-on-surface-variant/70">
                      Noch keine Gruppen beigetreten.
                    </p>
                  ) : (
                    groups.map((g) => (
                      <div
                        key={`comm-g-${g.id}`}
                        className="flex items-center justify-between p-2.5 rounded-xl border border-outline-variant/20 bg-surface-container-lowest/60"
                      >
                        <div
                          className="flex items-center gap-3 min-w-0 flex-1 cursor-pointer"
                          onClick={() => {
                            setActiveGroup(g)
                            setActiveContact(null)
                          }}
                        >
                          <div className="w-9 h-9 rounded-full bg-primary/10 text-primary flex items-center justify-center font-bold text-xs shrink-0">
                            {g.avatar_url ? (
                              <img src={g.avatar_url} alt="" className="w-full h-full rounded-full object-cover" />
                            ) : (
                              <UsersRound className="w-4 h-4" />
                            )}
                          </div>
                          <div className="min-w-0 flex-1">
                            <div className="text-xs font-semibold text-primary truncate">{g.name}</div>
                            <div className="text-[10px] text-on-surface-variant/70">{g.member_count} Mitglieder</div>
                          </div>
                        </div>

                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          onClick={() => handleCopyInviteLink(g)}
                          className="h-7 px-2 text-xs gap-1 text-primary"
                          title="Einladungslink kopieren"
                        >
                          <Share2 className="w-3.5 h-3.5" />
                          <span>Link</span>
                        </Button>
                      </div>
                    ))
                  )}
                </div>
              </div>
            )}
          </div>



          {/* Mobile WhatsApp-Style Bottom Navigation Bar (Chats, Aktuelles, Community) */}
          {!isChatOpen && (
            <nav className="md:hidden shrink-0 h-14 border-t border-outline-variant/20 bg-surface-container/95 backdrop-blur flex items-center justify-around px-2 z-10">
              <button
                type="button"
                onClick={() => setMobileNavTab('chats')}
                className={`flex flex-col items-center justify-center flex-1 py-1 transition-colors ${
                  mobileNavTab === 'chats' ? 'text-primary font-semibold' : 'text-on-surface-variant/70 hover:text-on-surface'
                }`}
                aria-label="Chats"
              >
                <div className={`p-1 rounded-full ${mobileNavTab === 'chats' ? 'bg-primary/15' : ''}`}>
                  <MessageSquare className="w-4 h-4" />
                </div>
                <span className="text-[10px] mt-0.5">Chats</span>
              </button>

              <button
                type="button"
                onClick={() => setMobileNavTab('updates')}
                className={`flex flex-col items-center justify-center flex-1 py-1 transition-colors ${
                  mobileNavTab === 'updates' ? 'text-primary font-semibold' : 'text-on-surface-variant/70 hover:text-on-surface'
                }`}
                aria-label="Aktuelles"
              >
                <div className={`p-1 rounded-full ${mobileNavTab === 'updates' ? 'bg-primary/15' : ''}`}>
                  <Sparkles className="w-4 h-4" />
                </div>
                <span className="text-[10px] mt-0.5">Aktuelles</span>
              </button>

              <button
                type="button"
                onClick={() => setMobileNavTab('community')}
                className={`flex flex-col items-center justify-center flex-1 py-1 transition-colors ${
                  mobileNavTab === 'community' ? 'text-primary font-semibold' : 'text-on-surface-variant/70 hover:text-on-surface'
                }`}
                aria-label="Community"
              >
                <div className={`p-1 rounded-full ${mobileNavTab === 'community' ? 'bg-primary/15' : ''}`}>
                  <UsersRound className="w-4 h-4" />
                </div>
                <span className="text-[10px] mt-0.5">Community</span>
              </button>
            </nav>
          )}
        </div>

        {/* Right Column: Chat Thread & Input Area */}
        <div
          className={`flex-1 flex flex-col min-h-0 bg-surface-container-lowest/30 relative ${
            !isChatOpen ? 'hidden md:flex' : 'flex'
          }`}
          onDragOver={(e) => {
            e.preventDefault()
            e.stopPropagation()
            setIsDragOver(true)
          }}
          onDragLeave={(e) => {
            e.preventDefault()
            e.stopPropagation()
            if (!e.currentTarget.contains(e.relatedTarget as Node)) {
              setIsDragOver(false)
            }
          }}
          onDrop={(e) => {
            e.preventDefault()
            e.stopPropagation()
            setIsDragOver(false)
            const file = e.dataTransfer.files?.[0]
            if (file) handleFileAttachment(file)
          }}
        >
          {/* Drag and Drop Visual Dropzone Overlay */}
          {isDragOver && (
            <div className="absolute inset-0 z-40 bg-surface/85 backdrop-blur-xs border-2 border-dashed border-primary flex flex-col items-center justify-center p-6 text-center pointer-events-none">
              <Upload className="w-12 h-12 text-primary animate-bounce mb-2" />
              <p className="font-headline font-bold text-sm text-primary">Datei hier ablegen</p>
              <p className="text-xs text-on-surface-variant">Wird Ende-zu-Ende verschlüsselt an die Konversation angehängt</p>
            </div>
          )}

          {/* Chat Wallpaper Background Layer (Cyber / Petrol / Midnight / Minimal / Custom) */}
          <div className="absolute inset-0 pointer-events-none overflow-hidden z-0">
            {wallpaperConfig.preset === 'cyber' && (
              <div
                className="absolute inset-0 opacity-20"
                style={{
                  backgroundImage: 'radial-gradient(#06b6d4 1.2px, transparent 1.2px)',
                  backgroundSize: '16px 16px',
                }}
              />
            )}
            {wallpaperConfig.preset === 'petrol' && (
              <div className="absolute inset-0 bg-gradient-to-br from-[#06181d] via-[#092228] to-[#040e11]" />
            )}
            {wallpaperConfig.preset === 'midnight' && (
              <div className="absolute inset-0 bg-gradient-to-br from-[#0c1322] via-[#090e1a] to-[#040810]" />
            )}
            {wallpaperConfig.preset === 'minimal' && (
              <div className="absolute inset-0 bg-surface-container-lowest" />
            )}
            {wallpaperConfig.preset === 'custom' && wallpaperConfig.customDataUrl && (
              <img
                src={wallpaperConfig.customDataUrl}
                alt=""
                className="w-full h-full object-cover"
              />
            )}

            {/* Configurable Dimming Layer for Text Readability */}
            {wallpaperConfig.dimLevel > 0 && (
              <div
                className="absolute inset-0 bg-black"
                style={{ opacity: wallpaperConfig.dimLevel / 100 }}
              />
            )}
          </div>

          {isChatOpen ? (
            <>
              {/* Floating Chat Controls (Header-free, maximal chat space) */}
              <div className="absolute top-2.5 left-3 right-3 z-30 flex items-center justify-between pointer-events-none">
                <div className="flex items-center gap-2 pointer-events-auto">
                  {/* Mobile Back Button */}
                  <button
                    type="button"
                    onClick={() => {
                      setActiveContact(null)
                      setActiveGroup(null)
                    }}
                    className="md:hidden p-2 rounded-full bg-surface-container-high/85 hover:bg-surface-container-high backdrop-blur-md border border-outline-variant/30 text-on-surface-variant shadow-xs transition-colors"
                    aria-label="Zurück zur Kontaktliste"
                    title="Zurück zur Kontaktliste"
                  >
                    <ChevronLeft className="w-4 h-4" />
                  </button>

                  {/* Header Title Badge with Mute & Block Indicators */}
                  <div className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-surface-container-high/85 backdrop-blur-md border border-outline-variant/30 shadow-xs">
                    <span className="text-xs font-bold text-on-surface truncate max-w-[130px] sm:max-w-xs">
                      {activeGroup ? activeGroup.name : activeContact?.username}
                    </span>
                    {blindMailboxId && isChatMuted(blindMailboxId) && (
                      <span title="Stummgeschaltet" className="inline-flex items-center text-status-warning">
                        <BellOff className="w-3.5 h-3.5" />
                      </span>
                    )}
                    {activeContact && isBlocked(activeContact.userId) && (
                      <span className="px-1.5 py-0.2 rounded-md bg-status-error/15 text-status-error text-[9px] font-semibold">
                        Blockiert
                      </span>
                    )}
                  </div>
                </div>

                <div className="flex items-center gap-1.5 pointer-events-auto">
                  {activeGroup && (
                    <>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => handleCopyInviteLink(activeGroup)}
                        className="h-8 gap-1.5 text-xs px-2.5 bg-surface-container-high/85 hover:bg-surface-container-high backdrop-blur-md border border-outline-variant/30 text-primary shadow-xs"
                        title="Einladungslink kopieren"
                      >
                        <Share2 className="w-3.5 h-3.5" />
                        <span className="hidden sm:inline">Einladen</span>
                      </Button>

                      {(activeGroup.owner_user_id === currentUserId || activeGroup.role === 'admin') && (
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => setIsGroupPermissionsOpen(true)}
                          className="h-8 w-8 bg-surface-container-high/85 hover:bg-surface-container-high backdrop-blur-md border border-outline-variant/30 text-on-surface-variant hover:text-primary shadow-xs"
                          title="Gruppenrollen & Rechte verwalten"
                          aria-label="Gruppenrollen & Rechte verwalten"
                        >
                          <Shield className="w-4 h-4" />
                        </Button>
                      )}

                      {activeGroup.owner_user_id === currentUserId ? (
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => handleDeleteGroup(activeGroup)}
                          className="h-8 w-8 bg-surface-container-high/85 hover:bg-surface-container-high backdrop-blur-md border border-outline-variant/30 text-on-surface-variant hover:text-error shadow-xs"
                          title="Gruppe löschen"
                          aria-label="Gruppe löschen"
                        >
                          <Trash2 className="w-4 h-4" />
                        </Button>
                      ) : (
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => handleLeaveGroup(activeGroup)}
                          className="h-8 w-8 bg-surface-container-high/85 hover:bg-surface-container-high backdrop-blur-md border border-outline-variant/30 text-on-surface-variant hover:text-error shadow-xs"
                          title="Gruppe verlassen"
                          aria-label="Gruppe verlassen"
                        >
                          <LogOut className="w-4 h-4" />
                        </Button>
                      )}
                    </>
                  )}

                  {activeContact && !activeContact.isFriend && (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={async () => {
                        try {
                          await sendFriendRequest(activeContact.username)
                          toast.success(`Freundschaftsanfrage an ${activeContact.username} gesendet!`)
                        } catch (err: any) {
                          toast.error(err?.message || 'Konnte keine Anfrage senden.')
                        }
                      }}
                      className="h-8 gap-1.5 text-xs px-2.5 bg-surface-container-high/85 hover:bg-surface-container-high backdrop-blur-md border border-outline-variant/30 text-primary shadow-xs"
                      title="Freundschaftsanfrage senden"
                    >
                      <UserPlus className="w-3.5 h-3.5" />
                      <span className="hidden sm:inline">Anfrage senden</span>
                    </Button>
                  )}

                  {/* Stummschalten Button */}
                  {blindMailboxId && (
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() => setIsMuteModalOpen(true)}
                      className={`h-8 w-8 bg-surface-container-high/85 hover:bg-surface-container-high backdrop-blur-md border border-outline-variant/30 shadow-xs ${
                        isChatMuted(blindMailboxId)
                          ? 'text-status-warning'
                          : 'text-on-surface-variant hover:text-primary'
                      }`}
                      title={
                        isChatMuted(blindMailboxId)
                          ? 'Stummschaltung aktiv (Klicken zum Ändern)'
                          : 'Benachrichtigungen stummschalten'
                      }
                      aria-label="Benachrichtigungen stummschalten"
                    >
                      {isChatMuted(blindMailboxId) ? (
                        <BellOff className="w-4 h-4" />
                      ) : (
                        <Bell className="w-4 h-4" />
                      )}
                    </Button>
                  )}

                  {/* Kontakt Blockieren Button */}
                  {activeContact && (
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() => setIsBlockConfirmOpen(true)}
                      className={`h-8 w-8 bg-surface-container-high/85 hover:bg-surface-container-high backdrop-blur-md border border-outline-variant/30 shadow-xs ${
                        isBlocked(activeContact.userId)
                          ? 'text-status-error'
                          : 'text-on-surface-variant hover:text-status-error'
                      }`}
                      title={
                        isBlocked(activeContact.userId)
                          ? 'Kontakt blockiert (Klicken zum Aufheben)'
                          : 'Kontakt blockieren'
                      }
                      aria-label="Kontakt blockieren"
                    >
                      <Ban className="w-4 h-4" />
                    </Button>
                  )}

                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => setIsWallpaperModalOpen(true)}
                    className="h-8 w-8 bg-surface-container-high/85 hover:bg-surface-container-high backdrop-blur-md border border-outline-variant/30 text-on-surface-variant hover:text-primary shadow-xs"
                    title="Chat-Hintergrund anpassen"
                    aria-label="Chat-Hintergrund anpassen"
                  >
                    <ImageIcon className="w-4 h-4" />
                  </Button>
                </div>
              </div>

              {/* Message Thread Scroll Area */}
              <div
                ref={scrollContainerRef}
                className="flex-1 overflow-y-auto p-4 pt-12 space-y-3 relative z-1"
              >
                {/* WhatsApp-style encryption notice banner */}
                <div className="py-1 text-center">
                  <div className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-surface-container-high/60 border border-outline-variant/30 text-[11px] text-on-surface-variant shadow-2xs">
                    <Lock className="w-3 h-3 text-emerald-400" />
                    <span>Nachrichten in diesem Chat sind Ende-zu-Ende verschlüsselt.</span>
                  </div>
                </div>

                {messages.length === 0 && !loadingMessages && (
                  <div className="py-16 text-center text-xs text-on-surface-variant/70">
                    Noch keine Nachrichten. Schreibe die erste Nachricht!
                  </div>
                )}

                {messages.map((msg, idx) => {
                  const currentDateBadge = formatChatDateBadge(msg.createdAt)
                  const prevDateBadge = idx > 0 ? formatChatDateBadge(messages[idx - 1].createdAt) : null
                  const showDateSeparator = Boolean(currentDateBadge && currentDateBadge !== prevDateBadge)

                  return (
                    <React.Fragment key={msg.id}>
                      {showDateSeparator && (
                        <div className="flex justify-center my-3 sticky top-2 z-10 pointer-events-none">
                          <span className="px-3.5 py-1 rounded-full text-[11px] font-semibold bg-surface-container/90 text-on-surface-variant backdrop-blur-md border border-outline-variant/30 shadow-xs">
                            {currentDateBadge}
                          </span>
                        </div>
                      )}

                      <div
                        className={`group flex flex-col ${msg.isSelf ? 'items-end' : 'items-start'}`}
                      >
                    <div
                      className={`max-w-[85%] md:max-w-[70%] px-3.5 py-2 rounded-2xl text-xs break-words shadow-xs space-y-2 ${
                        msg.isSelf
                          ? 'bg-[#0c2e35] text-[#f0fdfa] rounded-br-xs border border-[#164e5c]/60 shadow-sm'
                          : 'bg-surface-container-high text-on-surface rounded-bl-xs border border-outline-variant/20 shadow-xs'
                      }`}
                    >
                      {/* Group sender name if in group and not self */}
                      {activeGroup && !msg.isSelf && (
                        <div className="text-[10px] font-bold text-tertiary">
                          {msg.senderName || `Benutzer #${msg.senderId}`}
                        </div>
                      )}

                      {/* Image Attachment */}
                      {!msg.isDeleted && msg.imageAttachment && (() => {
                        const safeUrl = getSafeAttachmentUrl(msg.imageAttachment.dataUrl)
                        if (!safeUrl && !msg.imageAttachment.mediaId) return null
                        return (
                          <div className="rounded-xl overflow-hidden border border-black/10 my-1 cursor-pointer">
                            <img
                              src={safeUrl || undefined}
                              alt="Chat Anhang"
                              onClick={async () => {
                                if (msg.imageAttachment?.mediaId && !msg.imageAttachment.dataUrl) {
                                  try {
                                    const { signed_url } = await getChatMediaSignedUrl(msg.imageAttachment.mediaId)
                                    const context: AttachmentCryptoContext = activeGroup
                                      ? { groupId: activeGroup.id }
                                      : { userAId: currentUserId, userBId: activeContact?.userId }
                                    const decrypted = await downloadAndDecryptChatAttachment(signed_url, context)
                                    setViewingImage(decrypted)
                                    return
                                  } catch {
                                    toast.error('Bild konnte nicht entschlüsselt werden.')
                                    return
                                  }
                                }
                                if (safeUrl) setViewingImage(safeUrl)
                              }}
                              className="max-h-60 w-auto object-cover rounded-lg hover:opacity-95 transition-opacity"
                            />
                          </div>
                        )
                      })()}

                      {/* File Attachment Card */}
                      {!msg.isDeleted && msg.fileAttachment && (() => {
                        const safeHref = getSafeAttachmentUrl(msg.fileAttachment.dataUrl)
                        const safeName = msg.fileAttachment.name?.replace(/[\r\n"']/g, '') || 'attachment'
                        return (
                          <a
                            href={safeHref || '#'}
                            download={safeName}
                            target={safeHref && !safeHref.startsWith('data:') ? '_blank' : undefined}
                            rel="noopener noreferrer"
                            onClick={async (e) => {
                              if (msg.fileAttachment?.mediaId && (!safeHref || safeHref === '#')) {
                                e.preventDefault()
                                try {
                                  const { signed_url } = await getChatMediaSignedUrl(msg.fileAttachment.mediaId)
                                  const context: AttachmentCryptoContext = activeGroup
                                    ? { groupId: activeGroup.id }
                                    : { userAId: currentUserId, userBId: activeContact?.userId }
                                  const decrypted = await downloadAndDecryptChatAttachment(signed_url, context)
                                  const downloadLink = document.createElement('a')
                                  downloadLink.href = decrypted
                                  downloadLink.download = safeName
                                  document.body.appendChild(downloadLink)
                                  downloadLink.click()
                                  document.body.removeChild(downloadLink)
                                } catch {
                                  toast.error('Entschlüsselung des Dateianhangs fehlgeschlagen.')
                                }
                                return
                              }
                              if (!safeHref) {
                                e.preventDefault()
                                toast.error('Unsicherer oder ungültiger Dateianhang blockiert.')
                              }
                            }}
                            className={`flex items-center gap-2.5 p-2.5 rounded-xl border transition-colors ${
                              msg.isSelf
                                ? 'bg-black/15 border-white/20 text-white hover:bg-black/25'
                                : 'bg-surface-container-low border-outline-variant/30 text-on-surface hover:bg-surface-container'
                            }`}
                          >
                            <div className="p-2 rounded-lg bg-primary/20 text-primary shrink-0">
                              <FileText className="w-4 h-4" />
                            </div>
                            <div className="min-w-0 flex-1">
                              <p className="font-semibold text-xs truncate">{safeName}</p>
                              <p className="text-[10px] opacity-75">{formatFileSize(msg.fileAttachment.sizeBytes)}</p>
                            </div>
                            <Download className="w-3.5 h-3.5 opacity-75 shrink-0" />
                          </a>
                        )
                      })()}

                      {/* Sticker Attachment */}
                      {!msg.isDeleted && msg.stickerAttachment && (
                        <div className="py-1">
                          <div
                            className="w-24 h-24 sm:w-28 sm:h-28 drop-shadow-md"
                            dangerouslySetInnerHTML={{ __html: sanitizeSvg(msg.stickerAttachment.svg) }}
                            title={msg.stickerAttachment.label}
                          />
                          <div className="text-[10px] opacity-60 text-center mt-1">{msg.stickerAttachment.label}</div>
                        </div>
                      )}

                      {/* Audio / Voice Message Attachment (WhatsApp Style) */}
                      {!msg.isDeleted && msg.audioAttachment && (
                        <div
                          className={`flex items-center gap-2.5 p-2 rounded-2xl min-w-[240px] max-w-[320px] ${
                            msg.isSelf ? 'bg-black/20 text-white' : 'bg-surface-container-high/90 text-on-surface'
                          }`}
                        >
                          {/* Sender Profile Picture on Left (WhatsApp-style: transitions to speed toggle button when playing) */}
                          {playingAudioId === msg.id ? (
                            <button
                              type="button"
                              onClick={cycleAudioPlaybackRate}
                              className={`w-10 h-10 rounded-full font-bold text-xs shadow-sm flex items-center justify-center shrink-0 hover:scale-105 active:scale-95 transition-all ${
                                msg.isSelf
                                  ? 'bg-white text-[#0c2e35] hover:bg-white/90'
                                  : 'bg-primary text-on-primary hover:opacity-90'
                              }`}
                              title="Wiedergabegeschwindigkeit ändern (1x / 1.5x / 2x)"
                              aria-label="Wiedergabegeschwindigkeit ändern"
                            >
                              {audioPlaybackRate}x
                            </button>
                          ) : (
                            <div
                              className="relative shrink-0 w-10 h-10 rounded-full cursor-pointer"
                              onClick={cycleAudioPlaybackRate}
                              title="Wiedergabegeschwindigkeit ändern (1x / 1.5x / 2x)"
                            >
                              <Avatar
                                src={msg.isSelf ? user?.avatar_url : (activeContact?.avatarUrl || null)}
                                name={msg.isSelf ? (user?.username || 'Ich') : (msg.senderName || activeContact?.username || 'Benutzer')}
                                size="md"
                                className="w-10 h-10"
                              />
                              <button
                                type="button"
                                onClick={cycleAudioPlaybackRate}
                                className={`absolute -bottom-1 -right-1 px-1 py-0.5 rounded-full font-bold text-[9px] shadow-xs border border-surface leading-none hover:scale-110 transition-transform ${
                                  msg.isSelf
                                    ? 'bg-white text-[#0c2e35]'
                                    : 'bg-primary text-on-primary'
                                }`}
                                title="Wiedergabegeschwindigkeit ändern (1x / 1.5x / 2x)"
                                aria-label="Wiedergabegeschwindigkeit ändern"
                              >
                                {audioPlaybackRate}x
                              </button>
                            </div>
                          )}

                          {/* Play / Pause Button */}
                          <button
                            type="button"
                            onClick={() => togglePlayAudio(msg.id, msg.audioAttachment!.dataUrl)}
                            className={`w-8 h-8 rounded-full shrink-0 shadow-xs flex items-center justify-center transition-all ${
                              msg.isSelf
                                ? 'bg-white text-[#0c2e35] hover:bg-white/90'
                                : 'bg-primary text-on-primary hover:opacity-90'
                            }`}
                            aria-label={playingAudioId === msg.id ? 'Pause' : 'Abspielen'}
                          >
                            {playingAudioId === msg.id ? (
                              <Pause className="w-3.5 h-3.5" />
                            ) : (
                              <Play className="w-3.5 h-3.5 translate-x-0.5" />
                            )}
                          </button>

                          {/* Dynamic Audio Waveform with Click-to-Seek */}
                          <div
                            className="flex-1 min-w-[130px] space-y-1 cursor-pointer select-none"
                            onClick={(e) =>
                              handleWaveformSeek(
                                msg.id,
                                msg.audioAttachment!.dataUrl,
                                msg.audioAttachment!.durationSeconds,
                                e
                              )
                            }
                            title="Klicken zum Spulen"
                          >
                            <div className="flex items-center gap-[2.5px] h-7 px-0.5">
                              {getWaveformBars(msg.id).map((barH, bIdx) => {
                                const count = 28
                                const progress =
                                  playingAudioId === msg.id && msg.audioAttachment!.durationSeconds > 0
                                    ? audioCurrentTime / msg.audioAttachment!.durationSeconds
                                    : 0
                                const barProgress = bIdx / count
                                const isPlayed = barProgress <= progress

                                return (
                                  <div
                                    key={bIdx}
                                    className={`flex-1 rounded-full transition-colors ${
                                      isPlayed
                                        ? msg.isSelf
                                          ? 'bg-white'
                                          : 'bg-primary'
                                        : msg.isSelf
                                        ? 'bg-white/35'
                                        : 'bg-on-surface-variant/35'
                                    }`}
                                    style={{
                                      height: `${Math.max(4, Math.round(barH * 24))}px`,
                                      minWidth: '2px',
                                      maxWidth: '4px',
                                    }}
                                  />
                                )
                              })}
                            </div>

                            <div className="flex justify-between items-center text-[10px] opacity-80 px-0.5">
                              <span>
                                {playingAudioId === msg.id
                                  ? formatDuration(audioCurrentTime)
                                  : formatDuration(msg.audioAttachment.durationSeconds)}
                              </span>
                              <span className="flex items-center gap-1 opacity-70">
                                <Mic className="w-2.5 h-2.5" />
                                <span>Sprachnachricht</span>
                              </span>
                            </div>
                          </div>
                        </div>
                      )}

                      {/* Note Attachment Card */}
                      {!msg.isDeleted && msg.noteAttachment && (
                        <div
                          className={`p-3 rounded-xl border text-xs shadow-sm space-y-2.5 ${
                            msg.isSelf
                              ? 'bg-slate-950/80 border-white/20 text-white'
                              : 'bg-surface-container-lowest border-outline-variant/50 text-on-surface'
                          }`}
                        >
                          <div
                            className={`flex items-center justify-between gap-2 border-b pb-2 ${
                              msg.isSelf ? 'border-white/15' : 'border-outline-variant/30'
                            }`}
                          >
                            <div className="flex items-center gap-1.5 font-bold text-xs truncate">
                              <StickyNote className="w-3.5 h-3.5 text-amber-400 shrink-0" />
                              <span className="truncate text-white font-medium">{msg.noteAttachment.title || 'Notiz'}</span>
                            </div>
                            {(() => {
                              const noteKey = `note_${msg.id}_${msg.noteAttachment.title}`
                              const isImported = importedAttachmentIds.has(noteKey)
                              return (
                                <Button
                                  type="button"
                                  variant={msg.isSelf ? 'secondary' : 'primary'}
                                  size="sm"
                                  disabled={isImported}
                                  onClick={() => void handleImportNote(msg.noteAttachment!, noteKey)}
                                  className={`h-6 px-2.5 text-[10px] gap-1 shrink-0 rounded-full font-medium ${
                                    isImported
                                      ? 'opacity-60 cursor-default bg-white/10 text-white border-none'
                                      : msg.isSelf
                                      ? 'bg-white/20 hover:bg-white/30 text-white border-none'
                                      : 'bg-primary text-on-primary hover:bg-primary/90'
                                  }`}
                                  title={isImported ? 'Bereits in eigene Notizen übernommen' : 'In eigene Notizen übernehmen'}
                                >
                                  {isImported ? <Check className="w-3 h-3 text-emerald-400" /> : <Download className="w-3 h-3" />}
                                  <span>{isImported ? 'Übernommen' : 'Übernehmen'}</span>
                                </Button>
                              )
                            })()}
                          </div>
                          <p className="whitespace-pre-wrap text-[11px] text-white/90 line-clamp-4 leading-relaxed font-sans">
                            {msg.noteAttachment.content}
                          </p>
                        </div>
                      )}

                      {/* Calendar Attachment Card */}
                      {!msg.isDeleted && msg.calendarAttachment && (
                        <div
                          className={`p-3 rounded-xl border text-xs shadow-sm space-y-2.5 ${
                            msg.isSelf
                              ? 'bg-slate-950/80 border-white/20 text-white'
                              : 'bg-surface-container-lowest border-outline-variant/50 text-on-surface'
                          }`}
                        >
                          <div
                            className={`flex items-center justify-between gap-2 border-b pb-2 ${
                              msg.isSelf ? 'border-white/15' : 'border-outline-variant/30'
                            }`}
                          >
                            <div className="flex items-center gap-1.5 font-bold text-xs truncate">
                              <div className="w-5 h-5 rounded-md bg-cyan-500/20 flex items-center justify-center shrink-0">
                                <CalendarIcon className="w-3.5 h-3.5 text-cyan-300" />
                              </div>
                              <span className="truncate text-white font-medium">{msg.calendarAttachment.title || 'Termin'}</span>
                            </div>
                            {(() => {
                              const calKey = `cal_${msg.id}_${msg.calendarAttachment.title}`
                              const isImported = importedAttachmentIds.has(calKey)
                              return (
                                <Button
                                  type="button"
                                  variant={msg.isSelf ? 'secondary' : 'primary'}
                                  size="sm"
                                  disabled={isImported}
                                  onClick={() => void handleImportCalendar(msg.calendarAttachment!, calKey)}
                                  className={`h-6 px-2.5 text-[10px] gap-1 shrink-0 rounded-full font-medium ${
                                    isImported
                                      ? 'opacity-60 cursor-default bg-white/10 text-white border-none'
                                      : msg.isSelf
                                      ? 'bg-white/20 hover:bg-white/30 text-white border-none'
                                      : 'bg-primary text-on-primary hover:bg-primary/90'
                                  }`}
                                  title={isImported ? 'Bereits in eigenen Kalender eingetragen' : 'In eigenen Kalender eintragen'}
                                >
                                  {isImported ? <Check className="w-3 h-3 text-emerald-400" /> : <Plus className="w-3 h-3" />}
                                  <span>{isImported ? 'Eingetragen' : 'Eintragen'}</span>
                                </Button>
                              )
                            })()}
                          </div>
                          <div className="text-[11px] text-white/90 flex items-center gap-1.5 font-medium">
                            <Clock className="w-3.5 h-3.5 text-cyan-400 shrink-0" />
                            <span>
                              {new Date(msg.calendarAttachment.start).toLocaleString([], {
                                dateStyle: 'short',
                                timeStyle: 'short',
                              })}
                            </span>
                          </div>
                          {msg.calendarAttachment.location && (
                            <div className="text-[11px] text-white/80 flex items-center gap-1.5">
                              <MapPin className="w-3.5 h-3.5 text-rose-400 shrink-0" />
                              <span>{msg.calendarAttachment.location}</span>
                            </div>
                          )}
                          {msg.calendarAttachment.description && (
                            <p className="whitespace-pre-wrap text-[11px] text-white/90 line-clamp-3 leading-relaxed font-sans pt-0.5">
                              {msg.calendarAttachment.description}
                            </p>
                          )}
                        </div>
                      )}

                      {/* Quoted Story Reply Preview */}
                      {!msg.isDeleted && msg.storyReply && (
                        <div
                          className={`mb-2 p-2 rounded-xl border flex items-center justify-between gap-2.5 overflow-hidden text-xs select-none transition-all ${
                            msg.isSelf
                              ? 'bg-black/25 border-white/20 text-white'
                              : 'bg-surface-container-highest border-outline-variant/30 text-on-surface'
                          }`}
                        >
                          <div className="min-w-0 flex-1 space-y-0.5">
                            <div className="flex items-center gap-1.5 text-[10px] font-semibold text-primary">
                              <Sparkles className="w-3 h-3 text-primary shrink-0" />
                              <span className="truncate">Status von {msg.storyReply.storyUsername || 'Kontakt'}</span>
                            </div>
                            <p className="line-clamp-2 text-[11px] opacity-85 leading-snug">
                              {msg.storyReply.storyContent || 'Status-Update'}
                            </p>
                          </div>
                          {msg.storyReply.storyMediaUrl ? (
                            <img
                              src={msg.storyReply.storyMediaUrl}
                              alt="Status"
                              className="w-11 h-11 rounded-lg object-cover shrink-0 border border-white/10"
                            />
                          ) : (
                            <div
                              className={`w-11 h-11 rounded-lg shrink-0 flex items-center justify-center text-[8px] font-bold text-white shadow-xs ${
                                STORY_GRADIENTS[msg.storyReply.storyBackground || 'gradient-1']?.class || 'bg-slate-800'
                              }`}
                            >
                              Status
                            </div>
                          )}
                        </div>
                      )}

                      {/* Fallback preview for legacy [Antwort auf Status]: messages */}
                      {!msg.isDeleted && !msg.storyReply && msg.text.startsWith('[Antwort auf Status]:') && (
                        <div
                          className={`mb-1.5 p-1.5 px-2 rounded-lg border flex items-center gap-1.5 overflow-hidden text-[11px] select-none ${
                            msg.isSelf
                              ? 'bg-black/25 border-white/20 text-white'
                              : 'bg-surface-container-highest border-outline-variant/30 text-on-surface'
                          }`}
                        >
                          <Sparkles className="w-3 h-3 text-primary shrink-0" />
                          <span className="font-semibold text-primary truncate">Antwort auf Status</span>
                        </div>
                      )}

                      {/* Text content or Deleted indicator */}
                      {msg.isDeleted ? (
                        <div className="flex items-center gap-2 py-0.5 italic opacity-85">
                          <Trash2 className="w-3.5 h-3.5 shrink-0 opacity-70" />
                          <span>Diese Nachricht wurde gelöscht.</span>
                        </div>
                      ) : (
                        msg.text && (
                          <div className="space-y-1">
                            <p className="leading-relaxed">
                              {msg.text.startsWith('[Antwort auf Status]:')
                                ? msg.text.replace(/^\[Antwort auf Status\]:\s*"?/, '').replace(/"?$/, '')
                                : msg.text}
                            </p>
                            {msg.isEdited && (
                              <span className="text-[9px] opacity-70 italic inline-flex items-center gap-1">
                                <Pencil className="w-2.5 h-2.5" />
                                <span>bearbeitet</span>
                              </span>
                            )}
                          </div>
                        )
                      )}
                    </div>

                    <div className="flex items-center justify-end gap-1.5 text-[10px] text-on-surface-variant/60 mt-1 px-1">
                      {/* Message Actions Menu (Edit & Delete for self) */}
                      {!msg.isDeleted && msg.isSelf && (
                        <div className="opacity-0 group-hover:opacity-100 hover:opacity-100 focus-within:opacity-100 transition-opacity flex items-center gap-1 mr-1">
                          {msg.text && (
                            <button
                              type="button"
                              onClick={() => {
                                setEditingMessage(msg)
                                setInputText(msg.text)
                              }}
                              className="p-1 rounded-md hover:bg-surface-container-highest text-on-surface-variant hover:text-primary transition-colors"
                              title="Nachricht bearbeiten"
                              aria-label="Nachricht bearbeiten"
                            >
                              <Pencil className="w-3 h-3" />
                            </button>
                          )}
                          <button
                            type="button"
                            onClick={() => void handleDeleteMessage(msg)}
                            className="p-1 rounded-md hover:bg-surface-container-highest text-on-surface-variant hover:text-destructive transition-colors"
                            title="Nachricht für alle löschen"
                            aria-label="Nachricht für alle löschen"
                          >
                            <Trash2 className="w-3 h-3" />
                          </button>
                        </div>
                      )}

                      <span>
                        {new Date(msg.createdAt).toLocaleTimeString([], {
                          hour: '2-digit',
                          minute: '2-digit',
                        })}
                      </span>
                      {msg.isSelf && (
                        msg.isRead && readReceiptsEnabled ? (
                          <span title="Gelesen vom Gesprächspartner" className="inline-flex items-center">
                            <CheckCheck className="w-3.5 h-3.5 text-cyan-400" />
                          </span>
                        ) : msg.isDelivered ? (
                          <span title="Zugestellt / Vom Gesprächspartner empfangen" className="inline-flex items-center">
                            <CheckCheck className="w-3.5 h-3.5 opacity-60" />
                          </span>
                        ) : (
                          <span title="Gesendet" className="inline-flex items-center">
                            <Check className="w-3.5 h-3.5 opacity-60" />
                          </span>
                        )
                      )}
                      </div>
                    </div>
                  </React.Fragment>
                )
              })}
                {/* Floating Typing / Audio Recording Activity Indicator */}
                {partnerActivity && (
                  <div className="flex items-center gap-2 text-xs py-1.5 px-3 rounded-full bg-surface-container-high/90 border border-outline-variant/30 text-on-surface w-fit shadow-xs animate-in fade-in slide-in-from-bottom-2">
                    {partnerActivity.status === 'recording' ? (
                      <>
                        <Mic className="w-3.5 h-3.5 text-rose-400 animate-pulse" />
                        <span className="text-[11px] text-rose-400 font-medium">
                          {activeGroup ? `${partnerActivity.username || 'Jemand'} nimmt Audio auf …` : 'Nimmt eine Sprachnachricht auf …'}
                        </span>
                      </>
                    ) : (
                      <>
                        <span className="flex gap-1 items-center px-0.5">
                          <span className="w-1.5 h-1.5 rounded-full bg-primary animate-bounce [animation-delay:-0.3s]" />
                          <span className="w-1.5 h-1.5 rounded-full bg-primary animate-bounce [animation-delay:-0.15s]" />
                          <span className="w-1.5 h-1.5 rounded-full bg-primary animate-bounce" />
                        </span>
                        <span className="text-[11px] text-primary font-medium">
                          {activeGroup ? `${partnerActivity.username || 'Jemand'} schreibt …` : 'Schreibt …'}
                        </span>
                      </>
                    )}
                  </div>
                )}
                <div ref={messagesEndRef} />
              </div>

              {/* Staged Image Preview Bar */}
              {selectedImage && (
                <div className="px-4 py-2 border-t border-outline-variant/20 bg-surface-container flex items-center gap-3">
                  <div className="relative">
                    <img
                      src={selectedImage.dataUrl}
                      alt="Vorschau"
                      className="w-12 h-12 object-cover rounded-lg border border-outline-variant/40"
                    />
                    <button
                      type="button"
                      onClick={() => setSelectedImage(null)}
                      className="absolute -top-1 -right-1 p-0.5 rounded-full bg-surface-container-highest text-on-surface"
                      aria-label="Bild entfernen"
                    >
                      <X className="w-3 h-3" />
                    </button>
                  </div>
                  <span className="text-xs text-on-surface-variant truncate">
                    Foto angehängt: {selectedImage.name || 'image.png'}
                  </span>
                </div>
              )}

              {/* Staged Document / File Preview Bar */}
              {stagedFile && (
                <div className="px-4 py-2 border-t border-outline-variant/20 bg-surface-container flex items-center justify-between gap-3">
                  <div className="flex items-center gap-2 min-w-0">
                    <div className="p-1.5 rounded-lg bg-primary/10 text-primary shrink-0">
                      <FileText className="w-4 h-4" />
                    </div>
                    <div className="min-w-0">
                      <p className="text-xs font-semibold text-primary truncate">{stagedFile.name}</p>
                      <p className="text-[10px] text-on-surface-variant">{formatFileSize(stagedFile.sizeBytes)}</p>
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => setStagedFile(null)}
                    className="p-1 rounded-full hover:bg-surface-container-highest text-on-surface-variant"
                    aria-label="Datei entfernen"
                  >
                    <X className="w-3.5 h-3.5" />
                  </button>
                </div>
              )}

              {/* Editing Mode Banner */}
              {editingMessage && (
                <div className="px-4 py-2 border-t border-outline-variant/20 bg-primary/10 flex items-center justify-between gap-3">
                  <div className="flex items-center gap-2 min-w-0">
                    <div className="p-1.5 rounded-lg bg-primary/20 text-primary shrink-0">
                      <Pencil className="w-3.5 h-3.5" />
                    </div>
                    <div className="min-w-0">
                      <p className="text-xs font-semibold text-primary">Nachricht bearbeiten</p>
                      <p className="text-[10px] text-on-surface-variant truncate max-w-md">
                        {editingMessage.text}
                      </p>
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => {
                      setEditingMessage(null)
                      setInputText('')
                    }}
                    className="p-1 rounded-full hover:bg-surface-container-highest text-on-surface-variant"
                    aria-label="Bearbeiten abbrechen"
                    title="Abbrechen"
                  >
                    <X className="w-3.5 h-3.5" />
                  </button>
                </div>
              )}

              {/* Footer Input Area */}
              <div className="p-2.5 border-t border-outline-variant/20 bg-surface-container-low relative z-1">
                {activeContact && isBlocked(activeContact.userId) ? (
                  <div className="flex flex-col sm:flex-row items-center justify-between gap-3 p-3 rounded-xl bg-status-error/10 border border-status-error/30 text-xs text-status-error">
                    <div className="flex items-center gap-2">
                      <Ban className="w-4 h-4 shrink-0" />
                      <span>Du hast diesen Kontakt blockiert.</span>
                    </div>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => void unblockUser(activeContact.userId)}
                      className="h-7 text-xs px-3 border border-status-error/30 hover:bg-status-error/20 text-status-error font-medium"
                    >
                      Blockierung aufheben
                    </Button>
                  </div>
                ) : isRecording ? (
                  <VoiceRecordingBar
                    durationSeconds={recordingDuration}
                    statusLabel="Sprachaufnahme läuft …"
                    stream={mediaStreamRef.current}
                    variant="danger"
                    onCancel={() => stopRecording(false)}
                    onConfirm={() => stopRecording(true)}
                    cancelLabel="Abbrechen"
                    confirmLabel="Senden"
                    cancelIcon={<Trash2 className="w-3.5 h-3.5" />}
                    confirmIcon={<Send className="w-3.5 h-3.5" />}
                  />
                ) : (
                  <>
                    {/* WhatsApp-Style Sticker & Emoji Picker Popover */}
                    {isStickerPickerOpen && (
                      <div className="mb-2 p-2.5 rounded-xl bg-surface-container border border-outline-variant/30 shadow-lg animate-in fade-in slide-in-from-bottom-2">
                        <div className="flex items-center justify-between pb-1.5 mb-1.5 border-b border-outline-variant/20">
                          <div className="flex items-center gap-1.5">
                            <Button
                              type="button"
                              variant={stickerTab === 'stickers' ? 'primary' : 'ghost'}
                              size="sm"
                              onClick={() => setStickerTab('stickers')}
                              className="h-6 px-2.5 text-xs rounded-full"
                            >
                              Sticker
                            </Button>
                            <Button
                              type="button"
                              variant={stickerTab === 'emojis' ? 'primary' : 'ghost'}
                              size="sm"
                              onClick={() => setStickerTab('emojis')}
                              className="h-6 px-2.5 text-xs rounded-full"
                            >
                              Emojis
                            </Button>
                          </div>
                          <button
                            type="button"
                            onClick={() => setIsStickerPickerOpen(false)}
                            className="p-1 rounded-md text-on-surface-variant hover:text-on-surface"
                            aria-label="Schließen"
                          >
                            <X className="w-3.5 h-3.5" />
                          </button>
                        </div>

                        {stickerTab === 'stickers' ? (
                          <div className="grid grid-cols-4 sm:grid-cols-8 gap-2 max-h-48 overflow-y-auto p-1.5">
                            {IN_HOUSE_STICKERS.map((stk) => (
                              <button
                                key={stk.id}
                                type="button"
                                onClick={() => {
                                  void handleSendMessage(
                                    undefined,
                                    undefined,
                                    undefined,
                                    undefined,
                                    undefined,
                                    undefined,
                                    stk
                                  )
                                  setIsStickerPickerOpen(false)
                                }}
                                className="flex flex-col items-center justify-center p-1.5 rounded-xl hover:bg-surface-container-high transition-transform hover:scale-105"
                                title={stk.label}
                              >
                                <div
                                  className="w-11 h-11 flex items-center justify-center"
                                  dangerouslySetInnerHTML={{ __html: sanitizeSvg(stk.svg) }}
                                />
                                <span className="text-[9px] text-on-surface-variant/80 truncate w-full text-center mt-1 font-medium">
                                  {stk.label}
                                </span>
                              </button>
                            ))}
                          </div>
                        ) : (
                          <div className="space-y-3 max-h-52 overflow-y-auto p-1.5">
                            {CATEGORIZED_EMOJIS.map((cat) => (
                              <div key={cat.category} className="space-y-1">
                                <div className="text-[10px] font-bold text-on-surface-variant/70 uppercase tracking-wider px-1">
                                  {cat.category}
                                </div>
                                <div className="grid grid-cols-8 sm:grid-cols-12 gap-1">
                                  {cat.emojis.map((emoji) => (
                                    <button
                                      key={emoji}
                                      type="button"
                                      onClick={() => setInputText((prev) => prev + emoji)}
                                      className="p-1 text-lg rounded-lg hover:bg-surface-container-high transition-transform hover:scale-125 flex items-center justify-center"
                                    >
                                      {emoji}
                                    </button>
                                  ))}
                                </div>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    )}

                    <form
                      onSubmit={(e) => {
                        e.preventDefault()
                        handleSendMessage(
                          inputText,
                          undefined,
                          undefined,
                          selectedImage || undefined,
                          undefined,
                          stagedFile || undefined
                        )
                      }}
                    >
                      {/* Hidden Image Input */}
                      <input
                        ref={fileInputRef}
                        type="file"
                        accept="image/*"
                        capture="environment"
                        className="hidden"
                        onChange={handleFileChange}
                      />

                      {/* Hidden Doc/File Input */}
                      <input
                        ref={docInputRef}
                        type="file"
                        className="hidden"
                        onChange={(e) => {
                          const file = e.target.files?.[0]
                          if (file) handleFileAttachment(file)
                          if (docInputRef.current) docInputRef.current.value = ''
                        }}
                      />

                      <ChatInputBar
                        value={inputText}
                        onChange={handleInputChange}
                        onSubmit={() => {
                          handleSendMessage(
                            inputText,
                            undefined,
                            undefined,
                            selectedImage || undefined,
                            undefined,
                            stagedFile || undefined
                          )
                        }}
                        disabled={sending}
                        placeholder={editingMessage ? 'Nachricht bearbeiten …' : 'Nachricht schreiben …'}
                        leftActions={
                          <>
                            <Button
                              type="button"
                              variant={isStickerPickerOpen ? 'secondary' : 'ghost'}
                              size="icon"
                              onClick={() => setIsStickerPickerOpen((prev) => !prev)}
                              className="h-8 w-8 rounded-full p-0 text-on-surface-variant hover:text-amber-400"
                              title="Sticker & Emojis"
                              aria-label="Sticker auswählen"
                            >
                              <Smile className="w-4 h-4" />
                            </Button>

                            {/* Unified Attachment Button with sleek Popover */}
                            <div className="relative shrink-0" ref={attachMenuRef}>
                              <Button
                                type="button"
                                variant={isAttachMenuOpen ? 'secondary' : 'ghost'}
                                size="icon"
                                onClick={() => setIsAttachMenuOpen((prev) => !prev)}
                                className="h-8 w-8 rounded-full p-0 text-on-surface-variant hover:text-primary transition-all"
                                title="Anhang hinzufügen"
                                aria-label="Anhang hinzufügen"
                              >
                                <Plus className={`w-4 h-4 transition-transform duration-200 ${isAttachMenuOpen ? 'rotate-45 text-primary' : ''}`} />
                              </Button>

                              {/* Attachment Popover Menu */}
                              {isAttachMenuOpen && (
                                <div className="absolute bottom-10 left-0 z-30 min-w-[210px] p-1.5 rounded-2xl bg-surface-container-high/95 backdrop-blur-md border border-outline-variant/30 shadow-xl space-y-1 animate-in fade-in slide-in-from-bottom-2 duration-150">
                                  <button
                                    type="button"
                                    onClick={() => {
                                      setIsAttachMenuOpen(false)
                                      setIsCameraModalOpen(true)
                                    }}
                                    className="w-full flex items-center gap-2.5 px-3 py-2 rounded-xl text-left hover:bg-surface-container-highest/80 transition-colors group"
                                    aria-label="Foto anhängen"
                                  >
                                    <div className="w-7 h-7 rounded-lg bg-pink-500/15 text-pink-400 flex items-center justify-center shrink-0 group-hover:scale-105 transition-transform">
                                      <Camera className="w-4 h-4" />
                                    </div>
                                    <div className="min-w-0 flex-1">
                                      <div className="text-xs font-semibold text-primary">Foto aufnehmen</div>
                                      <div className="text-[10px] text-on-surface-variant/70">Kamera Snapshot</div>
                                    </div>
                                  </button>

                                  <button
                                    type="button"
                                    onClick={() => {
                                      setIsAttachMenuOpen(false)
                                      docInputRef.current?.click()
                                    }}
                                    className="w-full flex items-center gap-2.5 px-3 py-2 rounded-xl text-left hover:bg-surface-container-highest/80 transition-colors group"
                                    aria-label="Datei anhängen"
                                  >
                                    <div className="w-7 h-7 rounded-lg bg-indigo-500/15 text-indigo-400 flex items-center justify-center shrink-0 group-hover:scale-105 transition-transform">
                                      <Paperclip className="w-4 h-4" />
                                    </div>
                                    <div className="min-w-0 flex-1">
                                      <div className="text-xs font-semibold text-primary">Dokument & Datei</div>
                                      <div className="text-[10px] text-on-surface-variant/70">Verschlüsselt senden</div>
                                    </div>
                                  </button>

                                  <button
                                    type="button"
                                    onClick={() => {
                                      setIsAttachMenuOpen(false)
                                      handleOpenNotePicker()
                                    }}
                                    className="w-full flex items-center gap-2.5 px-3 py-2 rounded-xl text-left hover:bg-surface-container-highest/80 transition-colors group"
                                    aria-label="Notiz teilen"
                                  >
                                    <div className="w-7 h-7 rounded-lg bg-amber-500/15 text-amber-400 flex items-center justify-center shrink-0 group-hover:scale-105 transition-transform">
                                      <StickyNote className="w-4 h-4" />
                                    </div>
                                    <div className="min-w-0 flex-1">
                                      <div className="text-xs font-semibold text-primary">Notiz anhängen</div>
                                      <div className="text-[10px] text-on-surface-variant/70">Aus Notizen wählen</div>
                                    </div>
                                  </button>

                                  <button
                                    type="button"
                                    onClick={() => {
                                      setIsAttachMenuOpen(false)
                                      handleOpenCalendarPicker()
                                    }}
                                    className="w-full flex items-center gap-2.5 px-3 py-2 rounded-xl text-left hover:bg-surface-container-highest/80 transition-colors group"
                                    aria-label="Kalendereintrag teilen"
                                  >
                                    <div className="w-7 h-7 rounded-lg bg-cyan-500/15 text-cyan-400 flex items-center justify-center shrink-0 group-hover:scale-105 transition-transform">
                                      <CalendarIcon className="w-4 h-4" />
                                    </div>
                                    <div className="min-w-0 flex-1">
                                      <div className="text-xs font-semibold text-primary">Termin anhängen</div>
                                      <div className="text-[10px] text-on-surface-variant/70">Aus Kalender wählen</div>
                                    </div>
                                  </button>
                                </div>
                              )}
                            </div>
                          </>
                        }
                        rightActions={
                          inputText.trim() || selectedImage || stagedFile ? (
                            <Button
                              type="submit"
                              disabled={sending}
                              size="sm"
                              className="h-8 w-8 rounded-full p-0 flex items-center justify-center"
                              title="Senden"
                              aria-label="Senden"
                            >
                              <Send className="w-3.5 h-3.5" />
                            </Button>
                          ) : (
                            <Button
                              type="button"
                              variant="ghost"
                              size="icon"
                              onClick={startRecording}
                              className="h-8 w-8 p-0 text-on-surface-variant hover:text-primary hover:bg-primary/10 rounded-full"
                              title="Sprachnachricht aufnehmen"
                              aria-label="Sprachnachricht aufnehmen"
                            >
                              <Mic className="w-4 h-4" />
                            </Button>
                          )
                        }
                      />
                    </form>
                  </>
                )}
              </div>
            </>
          ) : (
            <div className="flex-1 flex flex-col items-center justify-center p-8 text-center">
              <div className="p-4 rounded-2xl bg-primary/10 border border-primary/20 text-primary mb-3">
                <MessageSquare className="w-8 h-8" />
              </div>
              <h3 className="font-headline text-body-lg font-bold text-primary mb-1">
                Deine Konversationen
              </h3>
              <p className="max-w-sm font-body text-xs text-on-surface-variant">
                Wähle einen Kontakt oder eine Gruppe aus, um einen direkten, Ende-zu-Ende verschlüsselten Chat zu starten.
              </p>
            </div>
          )}
        </div>
      </div>

      {/* Create Group Modal */}
      <Dialog open={isCreateGroupOpen} onOpenChange={setIsCreateGroupOpen}>
        <DialogContent className="max-w-md p-5">
          <div className="flex items-center justify-between pb-3 border-b border-outline-variant/20">
            <div className="flex items-center gap-2">
              <UsersRound className="w-5 h-5 text-primary" />
              <span className="font-headline text-body-md font-bold text-primary">Neue Gruppe erstellen</span>
            </div>
          </div>

          <form onSubmit={handleCreateGroup} className="space-y-4 pt-3">
            <div className="space-y-1.5">
              <label className="text-xs font-semibold text-on-surface">Gruppenname *</label>
              <Input
                value={groupName}
                onChange={(e: React.ChangeEvent<HTMLInputElement>) => setGroupName(e.target.value)}
                placeholder="z. B. Server-Admins oder Gaming"
                required
                className="text-xs h-9"
              />
            </div>

            <div className="space-y-1.5">
              <label className="text-xs font-semibold text-on-surface">Beschreibung (optional)</label>
              <Input
                value={groupDesc}
                onChange={(e: React.ChangeEvent<HTMLInputElement>) => setGroupDesc(e.target.value)}
                placeholder="Worum geht es in dieser Gruppe?"
                className="text-xs h-9"
              />
            </div>

            <div className="p-3 rounded-xl bg-surface-container-high/60 border border-outline-variant/30 text-xs text-on-surface-variant space-y-1">
              <div className="flex items-center gap-1.5 font-semibold text-primary">
                <Sparkles className="w-3.5 h-3.5" />
                <span>Ende-zu-Ende verschlüsselte Gruppe</span>
              </div>
              <p className="text-[11px]">
                Nach der Erstellung erhältst du einen Einladungslink, den du mit Freunden oder Teammitgliedern teilen kannst.
              </p>
            </div>

            <div className="flex justify-end gap-2 pt-2">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => setIsCreateGroupOpen(false)}
                disabled={creatingGroup}
              >
                Abbrechen
              </Button>
              <Button
                type="submit"
                variant="primary"
                size="sm"
                disabled={!groupName.trim() || creatingGroup}
              >
                {creatingGroup ? 'Erstelle…' : 'Gruppe erstellen'}
              </Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>

      {/* Note Picker Modal */}
      <Dialog open={isNotePickerOpen} onOpenChange={setIsNotePickerOpen}>
        <DialogContent className="max-w-md max-h-[75vh] flex flex-col p-4">
          <div className="flex items-center justify-between pb-3 border-b border-outline-variant/20">
            <div className="flex items-center gap-2">
              <StickyNote className="w-4 h-4 text-amber-400" />
              <span className="font-headline text-body-sm font-bold text-primary">Notiz teilen</span>
            </div>
          </div>

          <div className="py-2">
            <Input
              value={noteSearch}
              onChange={(e: React.ChangeEvent<HTMLInputElement>) => setNoteSearch(e.target.value)}
              placeholder="Notiz suchen …"
              className="text-xs h-8"
            />
          </div>

          <div className="flex-1 overflow-y-auto space-y-2 py-2">
            {userNotes.filter((n) => n.title.toLowerCase().includes(noteSearch.toLowerCase())).length === 0 ? (
              <p className="text-center py-6 text-xs text-on-surface-variant/70">
                Keine passenden Notizen gefunden.
              </p>
            ) : (
              userNotes
                .filter((n) => n.title.toLowerCase().includes(noteSearch.toLowerCase()))
                .map((n) => (
                  <div
                    key={n.id}
                    onClick={() => {
                      setIsNotePickerOpen(false)
                      handleSendMessage(
                        '',
                        {
                          title: n.title,
                          content: n.content,
                          color: n.color,
                          category: n.category,
                        },
                        undefined,
                        undefined
                      )
                    }}
                    className="p-3 rounded-xl border border-outline-variant/30 hover:border-primary/50 hover:bg-surface-container transition-all cursor-pointer text-left"
                  >
                    <div className="font-semibold text-xs text-primary">{n.title}</div>
                    <p className="text-[11px] text-on-surface-variant line-clamp-2 mt-0.5">
                      {n.content}
                    </p>
                  </div>
                ))
            )}
          </div>
        </DialogContent>
      </Dialog>

      {/* Calendar Picker Modal */}
      <Dialog open={isCalendarPickerOpen} onOpenChange={setIsCalendarPickerOpen}>
        <DialogContent className="max-w-md max-h-[75vh] flex flex-col p-4">
          <div className="flex items-center justify-between pb-3 border-b border-outline-variant/20">
            <div className="flex items-center gap-2">
              <CalendarIcon className="w-4 h-4 text-cyan-400" />
              <span className="font-headline text-body-sm font-bold text-primary">Termin teilen</span>
            </div>
          </div>

          <div className="py-2">
            <Input
              value={calendarSearch}
              onChange={(e: React.ChangeEvent<HTMLInputElement>) => setCalendarSearch(e.target.value)}
              placeholder="Termin suchen …"
              className="text-xs h-8"
            />
          </div>

          <div className="flex-1 overflow-y-auto space-y-2 py-2">
            {userEvents.filter((ev) => ev.title.toLowerCase().includes(calendarSearch.toLowerCase())).length === 0 ? (
              <p className="text-center py-6 text-xs text-on-surface-variant/70">
                Keine Termine gefunden.
              </p>
            ) : (
              userEvents
                .filter((ev) => ev.title.toLowerCase().includes(calendarSearch.toLowerCase()))
                .map((ev) => (
                  <div
                    key={ev.event_id || ev.id}
                    onClick={() => {
                      setIsCalendarPickerOpen(false)
                      handleSendMessage(
                        '',
                        undefined,
                        {
                          title: ev.title,
                          start: ev.start,
                          end: ev.end,
                          description: ev.description,
                          location: ev.location,
                        },
                        undefined
                      )
                    }}
                    className="p-3 rounded-xl border border-outline-variant/30 hover:border-primary/50 hover:bg-surface-container transition-all cursor-pointer text-left"
                  >
                    <div className="font-semibold text-xs text-primary">{ev.title}</div>
                    <div className="text-[10px] text-on-surface-variant flex items-center gap-1 mt-0.5">
                      <Clock className="w-3 h-3" />
                      <span>
                        {new Date(ev.start).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' })}
                      </span>
                    </div>
                  </div>
                ))
            )}
          </div>
        </DialogContent>
      </Dialog>

      {/* Full-size Image Viewer */}
      {viewingImage && (
        <div
          className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4"
          onClick={() => setViewingImage(null)}
        >
          <div className="relative max-w-4xl max-h-[90vh]">
            <img
              src={viewingImage}
              alt="Großansicht"
              className="max-h-[85vh] max-w-full rounded-xl object-contain shadow-2xl"
            />
            <button
              type="button"
              onClick={() => setViewingImage(null)}
              className="absolute -top-3 -right-3 p-1.5 rounded-full bg-surface-container-highest text-on-surface shadow-md"
              aria-label="Schließen"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>
      )}

      {/* Send Photo To Contact / Group Dialog */}
      <Dialog open={isSendPhotoOpen} onOpenChange={setIsSendPhotoOpen}>
        <DialogContent className="max-w-md max-h-[80vh] flex flex-col p-4">
          <div className="flex items-center justify-between pb-3 border-b border-outline-variant/20">
            <div className="flex items-center gap-2">
              <Camera className="w-4 h-4 text-primary" />
              <span className="font-headline text-body-sm font-bold text-primary">Foto senden an …</span>
            </div>
          </div>

          <div className="flex-1 overflow-y-auto space-y-1 py-2">
            <div className="text-[11px] font-semibold text-on-surface-variant/70 uppercase tracking-wider px-2 py-1">
              Wähle einen Kontakt oder eine Gruppe
            </div>
            {filteredGroups.map((g) => (
              <button
                key={`photo-g-${g.id}`}
                type="button"
                onClick={() => {
                  setActiveGroup(g)
                  setActiveContact(null)
                  if (pendingPhotoToSend) setSelectedImage(pendingPhotoToSend)
                  setPendingPhotoToSend(null)
                  setIsSendPhotoOpen(false)
                }}
                className="w-full flex items-center gap-2.5 p-2.5 rounded-xl hover:bg-surface-container-high transition-colors text-left"
              >
                <div className="w-8 h-8 rounded-full bg-primary/10 text-primary flex items-center justify-center text-xs font-bold shrink-0">
                  {g.avatar_url ? (
                    <img src={g.avatar_url} alt="" className="w-full h-full rounded-full object-cover" />
                  ) : (
                    <UsersRound className="w-4 h-4" />
                  )}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="text-xs font-semibold text-primary truncate">{g.name}</div>
                  <div className="text-[10px] text-on-surface-variant/70">Gruppe ({g.member_count} Mitglieder)</div>
                </div>
              </button>
            ))}

            {filteredContacts.map((c) => (
              <button
                key={`photo-c-${c.userId}`}
                type="button"
                onClick={() => {
                  setActiveContact(c)
                  setActiveGroup(null)
                  if (pendingPhotoToSend) setSelectedImage(pendingPhotoToSend)
                  setPendingPhotoToSend(null)
                  setIsSendPhotoOpen(false)
                }}
                className="w-full flex items-center gap-2.5 p-2.5 rounded-xl hover:bg-surface-container-high transition-colors text-left"
              >
                <div className="relative shrink-0">
                  <Avatar src={c.avatarUrl} name={c.username} size="sm" />
                  <StatusDot status={c.status} size="sm" className="absolute bottom-0 right-0" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="text-xs font-semibold text-primary truncate">{c.username}</div>
                  <div className="text-[10px] text-on-surface-variant/70">{c.teamName || (c.isFriend ? 'Freund' : 'Kontakt')}</div>
                </div>
              </button>
            ))}
          </div>
        </DialogContent>
      </Dialog>

      {/* Create Story Modal */}
      <CreateStoryModal
        open={isCreateStoryOpen}
        onOpenChange={(open) => {
          setIsCreateStoryOpen(open)
          if (!open) {
            setPendingStoryPhotoUrl(null)
          }
        }}
        onCreated={handleStoryCreated}
        initialMode={createStoryInitialMode}
        initialPhotoUrl={pendingStoryPhotoUrl}
      />

      {/* Story Viewer Modal */}
      <StoryViewerModal
        open={isViewerStoryOpen}
        onOpenChange={setIsViewerStoryOpen}
        stories={activeViewerStories.length > 0 ? activeViewerStories : stories}
        initialIndex={viewerStoryIndex}
        onDeleted={handleStoryDeleted}
        onReply={(targetUserId, _targetUsername, text, storyContext: StoryReplyContext) => {
          const contact = contactsList.find((c) => c.userId === targetUserId)
          if (contact) {
            setActiveContact(contact)
            setActiveGroup(null)
            setIsViewerStoryOpen(false)
            void handleSendMessage(
              text,
              undefined,
              undefined,
              undefined,
              undefined,
              undefined,
              undefined,
              storyContext
            )
          } else {
            toast.error('Kontakt für direkte Antwort nicht gefunden.')
          }
        }}
      />

      {/* Live Camera Snapshot Modal */}
      <CameraSnapshotModal
        open={isCameraModalOpen}
        onOpenChange={setIsCameraModalOpen}
        onCapture={(dataUrl) => {
          // If the user took a photo while on the "Aktuelles" (updates) tab, directly open the Story Creator with the photo!
          if (mobileNavTab === 'updates') {
            setPendingStoryPhotoUrl(dataUrl)
            setCreateStoryInitialMode('photo')
            setIsCreateStoryOpen(true)
            return
          }

          const img: ImageAttachment = { dataUrl, name: 'kamera-aufnahme.jpg' }
          if (activeContact || activeGroup) {
            setSelectedImage(img)
          } else {
            setPendingPhotoToSend(img)
            setIsSendPhotoOpen(true)
          }
        }}
      />

      {/* Group Permissions & Roles Management Modal */}
      <GroupPermissionsModal
        open={isGroupPermissionsOpen}
        onOpenChange={setIsGroupPermissionsOpen}
        group={activeGroup}
        currentUserId={currentUserId || 0}
        onGroupUpdated={(updatedGroup) => {
          setActiveGroup(updatedGroup)
          setGroups((prev) => prev.map((g) => (g.id === updatedGroup.id ? updatedGroup : g)))
        }}
      />

      {/* Design-DNA Confirmation Dialog for Deleting Group */}
      <Dialog open={Boolean(groupToDelete)} onOpenChange={(open) => !open && setGroupToDelete(null)}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="text-error flex items-center gap-2">
              <Trash2 className="w-5 h-5 text-error" />
              <span>Gruppe löschen?</span>
            </DialogTitle>
            <DialogDescription>
              Möchtest du die Gruppe <strong>"{groupToDelete?.name}"</strong> wirklich unwiderruflich löschen?
              Alle Mitglieder werden entfernt und der Chatverlauf kann nicht wiederhergestellt werden.
            </DialogDescription>
          </DialogHeader>

          <DialogFooter>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setGroupToDelete(null)}
              disabled={isDeletingGroup}
            >
              Abbrechen
            </Button>
            <Button
              variant="destructive"
              size="sm"
              onClick={handleConfirmDeleteGroup}
              disabled={isDeletingGroup}
              className="gap-1.5"
            >
              <Trash2 className="w-4 h-4" />
              <span>{isDeletingGroup ? 'Wird gelöscht …' : 'Endgültig löschen'}</span>
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      {/* Chat Wallpaper Customization Modal */}
      <ChatWallpaperModal
        open={isWallpaperModalOpen}
        onOpenChange={setIsWallpaperModalOpen}
        currentConfig={wallpaperConfig}
        onSaveConfig={(newCfg) => setWallpaperConfig(newCfg)}
      />

      {/* Design-DNA Mute Dialog */}
      <Dialog open={isMuteModalOpen} onOpenChange={setIsMuteModalOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <BellOff className="w-5 h-5 text-primary" />
              <span>Benachrichtigungen stummschalten</span>
            </DialogTitle>
            <DialogDescription>
              Wähle, wie lange Benachrichtigungen für {activeGroup ? `"${activeGroup.name}"` : activeContact ? `"${activeContact.username}"` : 'diesen Chat'} stummgeschaltet werden sollen.
            </DialogDescription>
          </DialogHeader>

          <div className="flex flex-col gap-2 px-6 py-4">
            <Button
              variant="secondary"
              className="w-full justify-start text-left text-xs py-2.5 h-auto"
              onClick={() => {
                if (blindMailboxId) {
                  muteChat(blindMailboxId, 480)
                  toast.success('Für 8 Stunden stummgeschaltet')
                }
                setIsMuteModalOpen(false)
              }}
            >
              <Clock className="w-4 h-4 mr-2.5 text-on-surface-variant" />
              <div>
                <div className="font-semibold">8 Stunden</div>
                <div className="text-[10px] text-on-surface-variant/70">Bis morgen stummschalten</div>
              </div>
            </Button>

            <Button
              variant="secondary"
              className="w-full justify-start text-left text-xs py-2.5 h-auto"
              onClick={() => {
                if (blindMailboxId) {
                  muteChat(blindMailboxId, 10080)
                  toast.success('Für 1 Woche stummgeschaltet')
                }
                setIsMuteModalOpen(false)
              }}
            >
              <Clock className="w-4 h-4 mr-2.5 text-on-surface-variant" />
              <div>
                <div className="font-semibold">1 Woche</div>
                <div className="text-[10px] text-on-surface-variant/70">7 Tage lang keine Töne oder Popups</div>
              </div>
            </Button>

            <Button
              variant="secondary"
              className="w-full justify-start text-left text-xs py-2.5 h-auto"
              onClick={() => {
                if (blindMailboxId) {
                  muteChat(blindMailboxId, 0)
                  toast.success('Dauerhaft stummgeschaltet')
                }
                setIsMuteModalOpen(false)
              }}
            >
              <BellOff className="w-4 h-4 mr-2.5 text-on-surface-variant" />
              <div>
                <div className="font-semibold">Immer</div>
                <div className="text-[10px] text-on-surface-variant/70">Bis du es manuell wieder einschaltest</div>
              </div>
            </Button>

            {blindMailboxId && isChatMuted(blindMailboxId) && (
              <Button
                variant="ghost"
                className="w-full justify-start text-left text-xs py-2.5 h-auto text-primary hover:bg-primary/10 mt-1 border border-primary/20"
                onClick={() => {
                  unmuteChat(blindMailboxId)
                  toast.success('Stummschaltung aufgehoben')
                  setIsMuteModalOpen(false)
                }}
              >
                <Bell className="w-4 h-4 mr-2.5 text-primary" />
                <div>
                  <div className="font-semibold">Stummschaltung aufheben</div>
                  <div className="text-[10px] text-on-surface-variant/70">Wieder Töne und Banner empfangen</div>
                </div>
              </Button>
            )}
          </div>

          <DialogFooter>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setIsMuteModalOpen(false)}
            >
              Abbrechen
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Design-DNA Block / Unblock Confirmation Dialog */}
      <Dialog open={isBlockConfirmOpen} onOpenChange={setIsBlockConfirmOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className={`flex items-center gap-2 ${activeContact && isBlocked(activeContact.userId) ? 'text-primary' : 'text-status-error'}`}>
              <Ban className="w-5 h-5" />
              <span>
                {activeContact && isBlocked(activeContact.userId)
                  ? 'Blockierung aufheben?'
                  : 'Kontakt blockieren?'}
              </span>
            </DialogTitle>
            <DialogDescription>
              {activeContact && isBlocked(activeContact.userId) ? (
                <>
                  Möchtest du <strong>"{activeContact.username}"</strong> wieder entsperren? Ihr könnt euch danach wieder gegenseitig Nachrichten schreiben.
                </>
              ) : (
                <>
                  Möchtest du <strong>"{activeContact?.username}"</strong> wirklich blockieren? Du erhältst keine Nachrichten, Töne oder Benachrichtigungen mehr von diesem Kontakt.
                </>
              )}
            </DialogDescription>
          </DialogHeader>

          <DialogFooter>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setIsBlockConfirmOpen(false)}
            >
              Abbrechen
            </Button>
            {activeContact && isBlocked(activeContact.userId) ? (
              <Button
                variant="primary"
                size="sm"
                onClick={async () => {
                  await unblockUser(activeContact.userId)
                  toast.success(`Blockierung von ${activeContact.username} aufgehoben`)
                  setIsBlockConfirmOpen(false)
                }}
              >
                Blockierung aufheben
              </Button>
            ) : (
              <Button
                variant="destructive"
                size="sm"
                onClick={async () => {
                  if (activeContact) {
                    await blockUser(activeContact.userId, activeContact.username, activeContact.avatarUrl)
                    toast.success(`${activeContact.username} blockiert`)
                  }
                  setIsBlockConfirmOpen(false)
                }}
              >
                Blockieren
              </Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
