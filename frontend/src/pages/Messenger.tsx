import React, { useState, useEffect, useRef, useMemo, useCallback } from 'react'
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
  Blattmenue,
  Blatteintrag,
  type ChatInputBarRef,
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
  Clock,
  RefreshCw,
  Mic,
  Trash2,
  Share2,
  Plus,
  LogOut,
  Sparkles,
  Smile,
  Paperclip,
  FileText,
  Upload,
  Shield,
  UserCheck,
  Briefcase,
  LayoutGrid,
  Globe,
  UserPlus,
  Pencil,
  Image as ImageIcon,
  ImagePlus,
  Bell,
  BellOff,
  Ban,
  Phone,
  Video,
  Forward,
  Copy,
  Star,
  Pin,
  PinOff,
  Archive,
  ArchiveRestore,
  ChevronDown,
  Timer,
  ArrowDown,
  AtSign,
} from 'lucide-react'
import { useCallStore, setzeAnrufIdentitaet } from '@/stores/useCallStore'
import { starteGruppenanruf } from '@/api/calls'
import { apiUrl } from '@/config/api'
import {
  CircularVideoNoteRecorder,
  type VideoNoteAufnahme,
} from '@/components/social/CircularVideoNoteRecorder'
import {
  ChatMessageBubble,
  type AntwortBezug,
  type CalendarAttachment,
  type ChatMessage,
  type NoteAttachment,
  type StickerAttachment,
  type StoryReplyAttachment,
} from '@/components/social/ChatMessageBubble'
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
  sendTypingSignal,
  ladeAnhangHoch,
  uploadGroupAvatar,
} from '@/api/social'
import { maxKlartextBytes } from '@/services/medienKrypto'
import {
  chatMediaBlobCache,
  holeAnhangUrl,
  type AudioAttachment,
  type FileAttachment,
  type ImageAttachment,
  type MedienBindungsKontext,
  type VideoNoteAttachment,
} from '@/components/social/ChatMediaAttachments'
import { teamsApi, type TeamMember } from '@/api/teams'
import { useKonversation, type GespraechsZiel } from '@/hooks/useKonversation'
import { uebernimmAltbestand } from '@/services/altbestandUebernahme'
import {
  loadNotesOfflineFirst,
  loadCalendarEventsOfflineFirst,
  saveNoteOffline,
  saveCalendarEventOffline,
  enqueueMessageMutation,
  getOutbox,
  setOutbox,
  replayOutbox,
} from '@/lib/offlineSync'
import type { NoteItem } from '@/pages/Notes'
import type { CalendarEventItem } from '@/pages/Calendar'
import {
  deriveBlindMailboxId,
  deriveGroupBlindMailboxId,
  scrubPlaintextStorage,
} from '@/services/e2eeCrypto'
import {
  resolveIdentity,
  forgetRecipientPublicKey,
  E2eeRecipientKeyMissingError,
  IDENTITY_LOADING,
  type E2eeIdentity,
} from '@/services/e2eeIdentity'
import { logischeUuid, DrZustellungFehlgeschlagenError } from '@/services/ratchetSitzung'
import { verwirfGruppenSchluessel } from '@/services/gruppenSchluessel'
import {
  entferneLokaleNachricht,
  loadLocalMessages,
  mischeVerlauf,
  saveLocalMessages,
  sichereDauerhafteAblage,
  updateMessageInLocalStore,
  sortMessagesChronologically,
} from '@/services/messengerLocalStore'
import {
  tilgeInhalt,
  tilgeNachrichtBeimServer,
  tilgeNachrichtLokal,
} from '@/services/nachrichtLoeschen'
import {
  bezugFelder,
  neueBezugstafel,
  neueSammeltafel,
  type Bezugsziel,
} from '@/services/nachrichtBezug'
import {
  schalteReaktion,
  wendeReaktionenAn,
  type RohReaktion,
} from '@/services/reaktionen'
import {
  binIchGemeint,
  findeErwaehnungen,
  offeneErwaehnung,
  setzeVorschlagEin,
  sucheVorschlaege,
  type Erwaehnungsvorschlag,
} from '@/services/erwaehnungen'
import {
  baueWeiterleitung,
  istWeiterleitbar,
  type Weiterleitungsziel,
} from '@/services/nachrichtWeiterleiten'
import {
  sammleAnMich,
  sammleMarkierte,
  sucheImChat,
  sucheUeberall,
  vergissMailbox,
  type ChatTreffer,
  type Treffer,
} from '@/services/verlaufSuche'
import {
  faelligeZeilen,
  setzeVerfallsfrist,
  stufenLabel,
  VERFALL_STUFEN,
  verfaelltAm as berechneVerfall,
  verfallsfrist,
} from '@/services/nachrichtVerfall'
import {
  ladeAlleEntwuerfe,
  ladeEntwurf,
  speichereEntwurf,
} from '@/services/messengerLocalStore'
import { ErwaehnungsWache } from '@/components/social/ErwaehnungsWache'
import { NachrichtenMenue } from '@/components/social/NachrichtenMenue'
import { WeiterleitenAnsicht } from '@/components/social/WeiterleitenAnsicht'
import { VerlaufSuchleiste } from '@/components/social/VerlaufSuchleiste'
import { TrefferListe } from '@/components/social/TrefferListe'
import { ChatZeilenGeste } from '@/components/social/ChatZeilenGeste'

/** Der Kontoschlüssel ist auf diesem Gerät nicht zu öffnen — nicht gesendet. */
class E2eeIdentityLockedError extends Error {
  constructor() {
    super('Der Schlüssel dieses Kontos ist auf diesem Gerät gesperrt.')
    this.name = 'E2eeIdentityLockedError'
  }
}
import { compressImageFile } from '@/lib/imageCompression'
import { getAudioTrackConstraints } from '@/lib/audioSettings'
import { IN_HOUSE_STICKERS, CATEGORIZED_EMOJIS } from '@/services/stickerCatalog'
import { CameraSnapshotModal } from '@/components/social/CameraSnapshotModal'
import { CreateStoryModal } from '@/components/social/CreateStoryModal'
import { MessengerSperrschirm } from '@/components/social/MessengerSperrschirm'
import { siegelAktiv } from '@/services/lokaleVersiegelung'
import { useMessengerSperre } from '@/services/messengerSperre'
import { StoryViewerModal, type StoryReplyContext } from '@/components/social/StoryViewerModal'
import { GroupPermissionsModal } from '@/components/social/GroupPermissionsModal'
import {
  type ChatWallpaperConfig,
  loadChatWallpaperConfig,
} from '@/components/social/ChatWallpaper'
import { ChatWallpaperModal } from '@/components/social/ChatWallpaperModal'
import { useAuthStore } from '@/stores/authStore'
import { toast } from '@/stores/toastStore'
import { useMessengerNotificationStore, PINS_MAX } from '@/stores/messengerNotificationStore'
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

export interface ChatContact {
  /**
   * Schlüssel für die Kontaktlisten. Wird beim Zusammenführen vergeben und
   * stammt nicht aus der Server-Antwort: eine Benutzer-Id kann doppelt
   * ankommen, dieser Wert nicht.
   */
  listKey: string
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

// Die Typen stehen bei den Komponenten, die sie anzeigen. Hier standen bis
// 09/2026 zweite Fassungen davon, die auseinanderliefen, sobald sich eine
// änderte. Weitergereicht wird nur, damit der bisherige Importweg bleibt.
export type {
  ImageAttachment,
  AudioAttachment,
  FileAttachment,
  VideoNoteAttachment,
} from '@/components/social/ChatMediaAttachments'
export type {
  AntwortBezug,
  CalendarAttachment,
  ChatMessage,
  NoteAttachment,
  StickerAttachment,
  StoryReplyAttachment,
}

/**
 * Was gesendet werden soll.
 *
 * Bis 09/2026 nahm `handleSendMessage` zehn Positionsargumente, und die
 * Aufrufe sahen entsprechend aus: `handleSendMessage('', undefined, undefined,
 * undefined, { … })`. Wer eine Sprachnachricht verschicken wollte, musste vier
 * Lücken abzählen, und jede neue Möglichkeit wäre Argument elf geworden.
 *
 * Benannte Felder kosten beim Aufruf ein paar Zeichen mehr und ersparen das
 * Zählen. Alles ist freiwillig; was nichts zu senden hat, kommt gar nicht erst
 * bis zum Umschlag.
 */
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
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`
}

export { getSafeAttachmentUrl }

/** Macht aus einer Aufnahme die Zeichenkette, die `medienKrypto` verschlüsselt. */
function blobAlsDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const leser = new FileReader()
    leser.onload = () => resolve(String(leser.result || ''))
    leser.onerror = () => reject(leser.error ?? new Error('Aufnahme nicht lesbar'))
    leser.readAsDataURL(blob)
  })
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
// Zero-Knowledge In-Memory Session Cache: verhindert das unverschlüsselte Speichern von Plaintext-Nachrichten im LocalStorage
export const sessionChatCache = new Map<string, ChatMessage[]>()

export function clearSessionChatCache(): void {
  sessionChatCache.clear()
}

function loadInitialContactsCache(): {
  friends: FriendItem[]
  groups: ChatGroupItem[]
  teamMembers: Array<{ member: TeamMember; teamName: string }>
  publicUsers: PublicProfileResponse[]
  stories: ChatStoryItem[]
  directChats: DirectChatItem[]
} {
  if (typeof window === 'undefined') {
    return { friends: [], groups: [], teamMembers: [], publicUsers: [], stories: [], directChats: [] }
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
        directChats: Array.isArray(parsed.directChats) ? parsed.directChats : [],
      }
    }
  } catch {}
  return { friends: [], groups: [], teamMembers: [], publicUsers: [], stories: [], directChats: [] }
}

export function Messenger() {
  const { user } = useAuthStore()
  // Der Sperrzustand wird ganz oben gelesen, damit kein Effekt darunter auf
  // eine Ablage greift, die ohne Schlüssel nichts herausgibt.
  //
  // `siegelAktiv()` steht daneben, weil der Store seinen Stand erst nach
  // `initialisiere()` kennt. Ohne diesen zweiten Blick zeigte der erste
  // Durchlauf nach jedem Neuladen einen kurz aufblitzenden, leeren Messenger,
  // bevor der Sperrschirm ihn ablöst. Gelesen hätte er nichts — die Ablagen
  // geben ohne Schlüssel nichts heraus —, aber es sähe kaputt aus.
  const messengerGesperrt = useMessengerSperre(
    (s) => !s.entsperrt && (s.eingerichtet || siegelAktiv()),
  )
  const [searchParams, setSearchParams] = useSearchParams()
  const { inviteCode } = useParams<{ inviteCode?: string }>()
  const navigate = useNavigate()
  const savedUserId = typeof window !== 'undefined' && window.sessionStorage ? sessionStorage.getItem('msm:active_messenger_user_id') : null
  const queryUserId = searchParams.get('userId') || searchParams.get('contact') || savedUserId
  const savedGroupId = typeof window !== 'undefined' && window.sessionStorage ? sessionStorage.getItem('msm:active_messenger_group_id') : null
  const queryGroupId = searchParams.get('groupId') || savedGroupId

  const initialCache = useMemo(() => loadInitialContactsCache(), [])
  const [friends, setFriends] = useState<FriendItem[]>(initialCache.friends)
  const [groups, setGroups] = useState<ChatGroupItem[]>(initialCache.groups)
  const [teamMembers, setTeamMembers] = useState<Array<{ member: TeamMember; teamName: string }>>(initialCache.teamMembers)
  const [publicUsers, setPublicUsers] = useState<PublicProfileResponse[]>(initialCache.publicUsers)
  const [directChats, setDirectChats] = useState<DirectChatItem[]>(initialCache.directChats)
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
  const mailboxDirectory = useMessengerNotificationStore((s) => s.mailboxDirectory)
  const pinnedChats = useMessengerNotificationStore((s) => s.pinnedChats)
  const archivedChats = useMessengerNotificationStore((s) => s.archivedChats)
  const mentionedChats = useMessengerNotificationStore((s) => s.mentionedChats)
  const schalteAnheften = useMessengerNotificationStore((s) => s.schalteAnheften)
  const schalteArchiv = useMessengerNotificationStore((s) => s.schalteArchiv)
  const merkeErwaehnung = useMessengerNotificationStore((s) => s.merkeErwaehnung)

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
  // Der Identitätsschlüssel gehört dem Konto. `state` sagt, ob dieses Gerät ihn
  // gerade öffnen kann; `decryptionKeys` enthält zusätzlich die alten
  // Gerätesschlüssel, ohne die der Verlauf von vor der Umstellung stumm bliebe.
  const [identity, setIdentity] = useState<E2eeIdentity>(IDENTITY_LOADING)
  // `loadMessages` läuft auch aus Listenern, die nur an `blindMailboxId`
  // hängen. Läse es die Identität aus der Closure, bliebe dort für immer der
  // Stand vom Zeitpunkt der Registrierung stehen — und wäre das `loading`,
  // käme über diesen Weg nie wieder eine Nachricht an.
  const identityRef = useRef<E2eeIdentity>(IDENTITY_LOADING)
  identityRef.current = identity
  // Ein Gerät legt seinen Schlüssel beim ersten Öffnen selbst an. Es gibt
  // nichts einzurichten, also auch keinen Zustand, in dem das Schreiben auf
  // Dauer gesperrt wäre — nur die kurze Spanne bis `ready`. Ist der Messenger
  // per PIN zu, steht ohnehin der Sperrschirm statt dieser Leiste.
  const istSchreibenGesperrt = Boolean(activeContact) && identity.state === 'loading'

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
  const [isVideoNoteRecording, setIsVideoNoteRecording] = useState(false)
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
  const currentLoadSeqRef = useRef<number>(0)

  // Message Editing State
  const [editingMessage, setEditingMessage] = useState<ChatMessage | null>(null)

  /**
   * Worauf die nächste Nachricht antwortet.
   *
   * Der Auszug wird hier festgehalten und reist gleich mit — nicht nachgeschlagen
   * beim Anzeigen. Sonst stünde das Zitat beim Empfänger leer, wenn die zitierte
   * Nachricht bei ihm nie ankam oder inzwischen gelöscht wurde.
   */
  const [antwortAuf, setAntwortAuf] = useState<AntwortBezug | null>(null)
  /** Die Nachricht, für die gerade das Langdruck-Menü offen ist. */
  const [menueNachricht, setMenueNachricht] = useState<ChatMessage | null>(null)
  /** Mehrfachauswahl: aus der Kopfzeile wird eine Aktionsleiste. */
  const [auswahlModus, setAuswahlModus] = useState(false)
  const [gewaehlteUuids, setGewaehlteUuids] = useState<string[]>([])
  /** Was weitergeleitet werden soll, und wie weit das Neu-Hochladen ist. */
  const [weiterzuleiten, setWeiterzuleiten] = useState<ChatMessage[] | null>(null)
  const [wlFortschritt, setWlFortschritt] = useState<{ gesamt: number; fertig: number } | null>(null)
  /** Suche im offenen Chat. */
  const [sucheOffen, setSucheOffen] = useState(false)
  const [suchTreffer, setSuchTreffer] = useState<Treffer[]>([])
  const [suchIndex, setSuchIndex] = useState(0)
  const [sucheGesperrt, setSucheGesperrt] = useState(false)
  /** Die Ansicht über alle Chats: Suche, Markiertes oder „an mich". */
  const [ueberall, setUeberall] = useState<'aus' | 'suche' | 'markiert' | 'anMich'>('aus')
  const [ueberallChats, setUeberallChats] = useState<ChatTreffer[]>([])
  const [ueberallLaeuft, setUeberallLaeuft] = useState(false)
  const [ueberallGesperrt, setUeberallGesperrt] = useState(false)
  const [ueberallFrage, setUeberallFrage] = useState('')
  /** Kurz aufleuchtende Zielzeile nach einem Sprung. */
  const [hervorgehoben, setHervorgehoben] = useState<string | null>(null)
  /** Die Nachricht, die in dieser Gruppe oben klebt. */
  const [angeheftet, setAngeheftet] = useState<ChatMessage | null>(null)
  /** Verfallsfrist dieses Chats in Sekunden, 0 = aus. */
  const [verfallSekunden, setVerfallSekunden] = useState(0)
  const [verfallOffen, setVerfallOffen] = useState(false)
  /** Chats mit ungesendetem Text, für die Vorschau in der Liste. */
  const [entwuerfe, setEntwuerfe] = useState<Record<string, string>>({})

  const highestIncomingIdAcknowledgedRef = useRef<number>(0)
  const highestIncomingIdDeliveredRef = useRef<number>(0)
  const maxPartnerReadIdRef = useRef<number>(0)
  const maxPartnerDeliveredIdRef = useRef<number>(0)

  // Real-time typing & voice recording indicator state
  const [partnerActivity, setPartnerActivity] = useState<{ status: 'typing' | 'recording'; username?: string } | null>(null)
  const partnerActivityTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const lastTypingSentRef = useRef<number>(0)

  const chatInputRef = useRef<ChatInputBarRef>(null)
  const [erwaehnungsVorschlaege, setErwaehnungsVorschlaege] = useState<Erwaehnungsvorschlag[]>([])

  /** Höchstens so viel vom Text steht im Zitat — der Rest wäre eine Kopie. */
  const ZITAT_MAX = 120

  /**
   * Der Auszug, der mit einer Antwort mitreist.
   *
   * Bei einer Nachricht ohne Text beschreibt er den Anhang. „Antwort auf Bild"
   * ist eine Auskunft; ein leeres Zitat ist keine.
   */
  const auszugFuerZitat = (msg: ChatMessage): string => {
    if (msg.text?.trim()) return msg.text.trim().slice(0, ZITAT_MAX)
    if (msg.imageAttachment) return 'Bild'
    if (msg.videoNoteAttachment) return 'Videonotiz'
    if (msg.audioAttachment) return 'Sprachnachricht'
    if (msg.stickerAttachment) return msg.stickerAttachment.label || 'Aufkleber'
    if (msg.fileAttachment) return String(msg.fileAttachment.name || 'Datei')
    if (msg.noteAttachment) return msg.noteAttachment.title || 'Notiz'
    if (msg.calendarAttachment) return msg.calendarAttachment.title || 'Termin'
    return 'Nachricht'
  }

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

  /** Setzt den gewählten Namen dort ein, wo gerade `@…` getippt wurde. */
  const waehleErwaehnung = (vorschlag: Erwaehnungsvorschlag) => {
    const feld = chatInputRef.current?.textarea
    const cursor = feld?.selectionStart ?? inputText.length
    const offen = offeneErwaehnung(inputText, cursor)
    if (!offen) return
    const { text, cursor: neuerCursor } = setzeVorschlagEin(inputText, offen.start, cursor, vorschlag.name)
    setInputText(text)
    setErwaehnungsVorschlaege([])
    window.requestAnimationFrame(() => {
      feld?.focus()
      feld?.setSelectionRange(neuerCursor, neuerCursor)
    })
  }

  /**
   * Entwürfe: entprellt schreiben, versiegelt ablegen.
   *
   * Ein Entwurf ist ungesendeter Klartext und damit das Empfindlichste, was
   * hier anfällt. Er geht deshalb in die versiegelte IndexedDB, nicht in den
   * localStorage neben die Stummschaltungen.
   */
  const entwurfUhr = useRef<ReturnType<typeof setTimeout> | null>(null)
  const entwurfOffen = useRef<{ mid: string; text: string } | null>(null)

  const schreibeEntwurf = useCallback(() => {
    const offen = entwurfOffen.current
    if (!offen) return
    entwurfOffen.current = null
    void speichereEntwurf(offen.mid, offen.text).catch(() => {})
    setEntwuerfe((prev) => {
      const kurz = offen.text.trim().slice(0, 80)
      if ((prev[offen.mid] || '') === kurz) return prev
      const neu = { ...prev }
      if (kurz) neu[offen.mid] = kurz
      else delete neu[offen.mid]
      return neu
    })
  }, [])

  const merkeEntwurf = useCallback(
    (mid: string, text: string) => {
      entwurfOffen.current = { mid, text }
      if (entwurfUhr.current) clearTimeout(entwurfUhr.current)
      entwurfUhr.current = setTimeout(schreibeEntwurf, 600)
    },
    [schreibeEntwurf],
  )

  /**
   * Am Telefon reißt ein Anruf oder ein Zurückwischen das Getippte weg, bevor
   * die Entprellung greift. `beforeunload` läuft auf iOS nicht zuverlässig,
   * deshalb diese beiden.
   */
  useEffect(() => {
    const sichern = () => schreibeEntwurf()
    document.addEventListener('visibilitychange', sichern)
    window.addEventListener('pagehide', sichern)
    return () => {
      document.removeEventListener('visibilitychange', sichern)
      window.removeEventListener('pagehide', sichern)
      sichern()
    }
  }, [schreibeEntwurf])

  /** Die Vorschauen für die Chatliste einmal beim Öffnen der Seite. */
  useEffect(() => {
    ladeAlleEntwuerfe()
      .then((alle) => {
        const kurz: Record<string, string> = {}
        for (const [mid, text] of Object.entries(alle)) {
          const gekuerzt = text.trim().slice(0, 80)
          if (gekuerzt) kurz[mid] = gekuerzt
        }
        setEntwuerfe(kurz)
      })
      .catch(() => {})
  }, [])

  const handleInputChange = (text: string) => {
    setInputText(text)

    // Vorschlagsliste: nur in Gruppen, und nur solange der Cursor hinter einem
    // `@…` steht. Die Auswahl ist Bequemlichkeit — die Schranke für `@everyone`
    // sitzt beim Empfänger, nicht hier.
    if (activeGroup) {
      const feld = chatInputRef.current?.textarea
      const cursor = feld?.selectionStart ?? text.length
      const offen = offeneErwaehnung(text, cursor)
      setErwaehnungsVorschlaege(
        offen ? sucheVorschlaege(activeGroup, offen.praefix, currentUserId) : [],
      )
    } else if (erwaehnungsVorschlaege.length) {
      setErwaehnungsVorschlaege([])
    }

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

    merkeEntwurf(blindMailboxId, text)
  }

  const currentUserId = user?.id || 0

  // 1. Identität des Kontos auflösen und Klartextreste aus der Altzeit entfernen.
  //
  // Hier wird bewusst nichts erzeugt und nichts veröffentlicht. Vorher stand an
  // dieser Stelle `getOrGenerateLocalKeyPair` plus Upload: jedes Gerät schrieb
  // seinen eigenen Schlüssel auf das Konto, überschrieb den des vorigen, und ab
  // da war der ganze Verlauf auf beiden Seiten unlesbar. Fehlt der Schlüssel
  // hier, lautet die Antwort `locked` und der Benutzer entsperrt ihn selbst.
  useEffect(() => {
    scrubPlaintextStorage()
    if (!currentUserId) return
    // Gesperrt gibt die Ablage den Geräteausweis nicht heraus, und das ist so
    // gewollt. Hier trotzdem zu fragen, hieße: der Versuch scheitert, die
    // Identität bleibt auf `loading` stehen — und weil dieser Effekt nur am
    // Konto hängt, käme er nach dem Entsperren nie wieder vorbei. Die Folge war
    // eine Eingabeleiste, die dauerhaft „zuerst den Schlüssel entsperren"
    // verlangte, obwohl längst entsperrt war. Deshalb steht der Sperrzustand
    // mit in den Abhängigkeiten: geht das Schloss auf, wird neu gefragt.
    if (messengerGesperrt) return
    let active = true

    // Legt beim ersten Mal den Geräteschlüssel an und meldet ihn beim Konto.
    // Es gibt nichts mehr nachzureichen: der Schlüssel gehört diesem Gerät und
    // war noch nie woanders.
    resolveIdentity(currentUserId)
      .then((next) => {
        if (active) setIdentity(next)
      })
      .catch(() => {})

    return () => {
      active = false
    }
  }, [currentUserId, messengerGesperrt])

  /**
   * Den Verlauf vor dem Aufräumen des Browsers schützen.
   *
   * Steht vor der Übernahme des Altbestands, und zwar mit Absicht: gleich
   * danach wird der alte Kontoschlüssel gelöscht, und ab dann ist die lokale
   * Ablage die einzige Stelle, an der der eigene Gesprächsanteil existiert.
   * Erst das Dach, dann einräumen.
   */
  useEffect(() => {
    if (!currentUserId) return
    void sichereDauerhafteAblage()
  }, [currentUserId])

  /**
   * Der Verlauf aus der Zeit des Kontoschlüssels zieht einmal um.
   *
   * Nur Direktchats: Gruppennachrichten von damals lagen unter einem Schlüssel,
   * der sich aus der Gruppenkennung ableiten ließ, und den gibt es nicht mehr —
   * für sie ist nichts zu retten. Läuft genau einmal je Gerät und Konto, siehe
   * `altbestandUebernahme.ts`; danach ist der Kontoschlüssel gelöscht.
   */
  useEffect(() => {
    if (!currentUserId || friends.length === 0) return
    // Gesperrt bricht der Umzug bei der ersten Zeile ab, die geschrieben werden
    // soll. Verloren geht dabei nichts — der alte Schlüssel bleibt liegen, und
    // beim nächsten Anlauf fängt es von vorn an. Trotzdem nicht anfangen: ein
    // Durchlauf, der nur scheitern kann, ist keine Arbeit, sondern Lärm.
    if (messengerGesperrt) return
    let active = true

    Promise.all(friends.map((f) => deriveBlindMailboxId(currentUserId, f.user_id)))
      .then((mailboxen) => {
        if (!active) return
        return uebernimmAltbestand(currentUserId, mailboxen)
      })
      .catch(() => {})

    return () => {
      active = false
    }
  }, [currentUserId, friends.length, messengerGesperrt])

  // Der Anruf-Store braucht dieselbe Identität, um Raumschlüssel zu verpacken
  // und auszupacken. Er hängt bewusst nicht selbst am Schlüsselbund: er soll
  // nicht wissen, wie eine Identität zustande kommt, nur dass es eine gibt.
  useEffect(() => {
    if (!currentUserId) {
      setzeAnrufIdentitaet(null)
      return
    }
    if (identity.state !== 'ready' || !identity.sendPair) {
      return
    }
    setzeAnrufIdentitaet({
      userId: currentUserId,
      publicKeyJwk: identity.sendPair.publicKeyJwk,
      decryptionKeys: identity.decryptionKeys,
    })
  }, [currentUserId, identity])

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
            directChats: directChatsData,
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

  /** Gruppenlogo: Auswahl, Prüfung, Upload. */
  const gruppenLogoInputRef = useRef<HTMLInputElement | null>(null)
  const [logoLaedt, setLogoLaedt] = useState(false)

  const handleGruppenLogo = async (datei: File | undefined) => {
    if (!datei || !activeGroup) return
    // Vorabprüfung nur für die Rückmeldung; die verbindliche Prüfung samt
    // Magic Bytes macht das Backend.
    if (!/^image\/(jpeg|png|webp|gif)$/.test(datei.type)) {
      toast.error('Erlaubt sind JPEG, PNG, WebP und GIF.')
      return
    }
    if (datei.size > 5 * 1024 * 1024) {
      toast.error('Das Logo darf höchstens 5 MB groß sein.')
      return
    }
    setLogoLaedt(true)
    try {
      const aktualisiert = await uploadGroupAvatar(activeGroup.id, datei)
      setActiveGroup((aktuell) =>
        aktuell?.id === aktualisiert.id ? { ...aktuell, avatar_url: aktualisiert.avatar_url } : aktuell
      )
      setGroups((vorher) =>
        vorher.map((g) => (g.id === aktualisiert.id ? { ...g, avatar_url: aktualisiert.avatar_url } : g))
      )
      toast.success('Gruppenlogo aktualisiert.')
    } catch {
      toast.error('Das Gruppenlogo konnte nicht gesetzt werden.')
    } finally {
      setLogoLaedt(false)
    }
  }

  /** Beitritt über die Einladungskarte im Chat. */
  const handleJoinByInviteCode = async (code: string) => {
    try {
      const joinedGroup = await joinGroupByInvite(code)
      toast.success(`Gruppe "${joinedGroup.name}" erfolgreich beigetreten!`)
      setActiveGroup(joinedGroup)
      setActiveContact(null)
      await loadData()
    } catch {
      toast.error('Der Einladungslink gilt nicht mehr.')
    }
  }

  // Combine Contacts
  const contactsList: ChatContact[] = useMemo(() => {
    const list: ChatContact[] = []
    const seenUserIds = new Set<number>()
    // Der Zähler gehört dieser Ansicht. Die Benutzer-Id tut es nicht, deshalb
    // trägt sie den Schlüssel nur lesbar mit, nicht seine Eindeutigkeit.
    let schluesselZaehler = 0
    const naechsterSchluessel = (uid: number) => `k${++schluesselZaehler}-${uid}`

    for (const f of friends) {
      if (f.status === 'accepted' || (f as any).friend_user_id) {
        const uid = f.user_id ?? (f as any).friend_user_id ?? f.id
        // Liegt eine Freundschaft in beiden Richtungen im Bestand, kommt sie
        // zweimal an. Ein Freund, ein Eintrag, wie bei den drei Quellen unten.
        if (seenUserIds.has(uid)) continue
        seenUserIds.add(uid)
        list.push({
          listKey: naechsterSchluessel(uid),
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
          listKey: naechsterSchluessel(member.user_id),
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
          listKey: naechsterSchluessel(dc.other_user_id),
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
          listKey: naechsterSchluessel(p.user_id),
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

  /**
   * Angeheftetes nach oben, Archiviertes heraus.
   *
   * Beides steht nur auf diesem Gerät: welche Gespräche jemandem wichtig sind,
   * ist ein Metadatum ersten Ranges und hat auf keinem Server etwas zu suchen.
   * Dieselbe Bauart wie das Stummschalten.
   */
  const ordne = useCallback(
    <T,>(eintraege: T[], midVon: (e: T) => string | undefined) => {
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
        if (pa === pb) return 0
        return (pa < 0 ? 99 : pa) - (pb < 0 ? 99 : pb)
      })
      return { sichtbar, imArchiv }
    },
    [pinnedChats, archivedChats],
  )

  const gruppenNachArchiv = useMemo(
    () => ordne(filteredGroups, (g) => groupMailboxMap[g.id]),
    [ordne, filteredGroups, groupMailboxMap],
  )
  const kontakteNachArchiv = useMemo(
    () => ordne(filteredContacts, (c) => contactMailboxMap[c.userId]),
    [ordne, filteredContacts, contactMailboxMap],
  )
  /** Wie viele Chats im Archiv liegen und wie viel dort ungelesen ist. */
  const archivZahl = gruppenNachArchiv.imArchiv.length + kontakteNachArchiv.imArchiv.length
  const archivUngelesen = useMemo(() => {
    let summe = 0
    for (const g of gruppenNachArchiv.imArchiv) summe += unreadCounts[groupMailboxMap[g.id]] || 0
    for (const c of kontakteNachArchiv.imArchiv) summe += unreadCounts[contactMailboxMap[c.userId]] || 0
    return summe
  }, [gruppenNachArchiv, kontakteNachArchiv, unreadCounts, groupMailboxMap, contactMailboxMap])

  /** Der Chat, für den gerade das Langdruck-Menü der Liste offen ist. */
  const [zeilenMenue, setZeilenMenue] = useState<{ mid: string; name: string } | null>(null)
  /** Ob die archivierten Chats gerade mit angezeigt werden. */
  const [archivOffen, setArchivOffen] = useState(false)
  /**
   * Der Rückweg nach einer Wischgeste.
   *
   * Eine Geste löst versehentlich aus. Ohne sichtbaren Rückweg wäre ein Chat
   * weg, ohne dass jemand wüsste wohin — der Streifen unten in der Liste ist
   * deshalb Teil der Funktion, nicht ihre Verzierung.
   */
  const [widerruf, setWiderruf] = useState<{ text: string; zurueck: () => void } | null>(null)

  // Der Streifen verschwindet von selbst; sonst stünde er bis zum nächsten Mal.
  useEffect(() => {
    if (!widerruf) return
    const uhr = window.setTimeout(() => setWiderruf(null), 6000)
    return () => window.clearTimeout(uhr)
  }, [widerruf])

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

  // Auto-select contact if userId query parameter or storage is present
  useEffect(() => {
    if (queryUserId && !activeGroup) {
      const targetId = Number(queryUserId)
      if (targetId) {
        const match = contactsList.find((c) => c.userId === targetId)
        if (match) {
          setActiveContact((prev) => {
            if (prev?.userId === match.userId && prev.username === match.username && prev.avatarUrl === match.avatarUrl) {
              return prev
            }
            return match
          })
          setActiveGroup(null)
        } else {
          setActiveContact((prev) => {
            if (prev?.userId === targetId) return prev
            return {
              // Steht in keiner Liste, braucht den Schlüssel aber als Kontakt.
              listKey: `q-${targetId}`,
              id: targetId,
              userId: targetId,
              username: `User #${targetId}`,
              avatarUrl: null,
              status: 'invisible',
              deviceType: null,
              activityLabel: null,
              isFriend: false,
              teamName: null,
            }
          })
          setActiveGroup(null)
        }
      }
    }
  }, [queryUserId, contactsList, activeGroup])

  // Auto-select group if groupId query parameter or storage is present
  useEffect(() => {
    if (queryGroupId && !activeContact && groups.length > 0) {
      const targetGroupId = Number(queryGroupId)
      if (targetGroupId) {
        const match = groups.find((g) => g.id === targetGroupId)
        if (match) {
          setActiveGroup((prev) => (prev?.id === match.id ? prev : match))
          setActiveContact(null)
        }
      }
    }
  }, [queryGroupId, groups, activeContact])

  // Synchronize active conversation with search params and sessionStorage
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

  // 4. When active contact or active group changes, derive mailbox ID
  /**
   * Das Gespräch, wie es die Krypto-Schicht sieht.
   *
   * Vorher stand hier ein Effekt mit 135 Zeilen, in dem dieselben dreißig
   * viermal standen: einmal je Kombination aus Gruppe/Kontakt und
   * Zwischenspeicher/Berechnung. Die Mailbox-Kennung und die Wahl des
   * Verfahrens liegen jetzt in `useKonversation`; hier bleibt, was die
   * Oberfläche davon merkt.
   */
  const gespraechsZiel = useMemo<GespraechsZiel>(() => {
    if (activeGroup) {
      return {
        art: 'gruppe',
        groupId: activeGroup.id,
        mitglieder: (activeGroup.members ?? []).map((m) => m.user_id),
      }
    }
    if (activeContact) return { art: 'direkt', peerId: activeContact.userId }
    return { art: 'keins' }
  }, [activeGroup?.id, activeGroup?.members, activeContact?.userId])

  /**
   * Schreibt die Systemzeile zum Sitzungsbruch in den Verlauf.
   *
   * Gedrosselt auf einmal je Gerät und Minute. Ohne die Drossel schaukeln sich
   * zwei Clients hoch, die beide gleichzeitig den Bruch bemerken, und der
   * Verlauf füllt sich mit Systemzeilen statt mit Nachrichten.
   */
  const sitzungsMeldungRef = useRef<Map<string, number>>(new Map())
  const sitzungNeuGemeldet = useCallback((geraet: string) => {
    const jetzt = Date.now()
    const zuletzt = sitzungsMeldungRef.current.get(geraet) || 0
    if (jetzt - zuletzt < 60_000) return
    sitzungsMeldungRef.current.set(geraet, jetzt)

    const zeile: ChatMessage = {
      id: jetzt,
      clientUuid: `sys-dr-${geraet}-${jetzt}`,
      senderId: 0,
      text: 'Die Sicherheitssitzung mit diesem Gerät wurde neu aufgebaut. Ältere Nachrichten dieses Geräts bleiben unlesbar.',
      createdAt: new Date().toISOString(),
      isSelf: false,
      isSystem: true,
    }
    setMessages((prev) => sortMessagesChronologically([...prev, zeile]))
  }, [])

  /**
   * Eine Systemzeile in den offenen Verlauf schreiben.
   *
   * Nur Anzeige und nur für diese Sitzung: sie wandert nicht in die lokale
   * Ablage und reist nirgendwohin. Die Gegenseite schreibt sich ihre eigene,
   * wenn sie den Umschlag sieht.
   */
  const zeigeSystemzeile = useCallback((text: string) => {
    const jetzt = Date.now()
    setMessages((prev) =>
      sortMessagesChronologically([
        ...prev,
        {
          id: jetzt,
          clientUuid: `sys-${jetzt}`,
          senderId: 0,
          text,
          createdAt: new Date().toISOString(),
          isSelf: false,
          isSystem: true,
        },
      ]),
    )
  }, [])

  const konversation = useKonversation({
    ziel: gespraechsZiel,
    eigeneId: currentUserId,
    identitaetRef: identityRef,
    meldeSitzungsbruch: sitzungNeuGemeldet,
  })
  const blindMailboxId = konversation.blindMailboxId

  /**
   * Der Wechsel in ein anderes Gespräch.
   *
   * Zählerstände zurücksetzen, den lokalen Verlauf zeigen, solange der Abruf
   * läuft, und die Benachrichtigungen umhängen. Alles davon ist Anzeige und
   * gehört deshalb hierher, nicht in den Hook.
   */
  useEffect(() => {
    let active = true

    if (activeMailboxIdRef.current === blindMailboxId) return
    activeMailboxIdRef.current = blindMailboxId
    highestIncomingIdAcknowledgedRef.current = 0
    highestIncomingIdDeliveredRef.current = 0
    maxPartnerReadIdRef.current = 0
    maxPartnerDeliveredIdRef.current = 0
    useMessengerNotificationStore.getState().setActiveMailboxId(blindMailboxId || null)

    // Was zum neuen Chat gehört und nicht zum alten.
    setAntwortAuf(null)
    setSucheOffen(false)
    setSuchTreffer([])
    setAngeheftet(null)
    setAuswahlModus(false)
    setGewaehlteUuids([])
    setInputText('')

    if (!blindMailboxId) {
      setMessages([])
      setLoadingMessages(Boolean(activeContact || activeGroup))
      return
    }

    // Frist und angefangener Text gehören zu diesem Chat, nicht zum vorigen.
    setVerfallSekunden(verfallsfrist(blindMailboxId))
    ladeEntwurf(blindMailboxId)
      .then((text) => {
        if (!active || activeMailboxIdRef.current !== blindMailboxId) return
        if (text) setInputText(text)
      })
      .catch(() => {})

    const cached = sessionChatCache.get(blindMailboxId)
    if (cached && cached.length > 0) {
      setMessages(cached)
      setLoadingMessages(false)
    } else {
      setMessages([])
      setLoadingMessages(true)
      loadLocalMessages(blindMailboxId)
        .then((localMsgs) => {
          if (active && activeMailboxIdRef.current === blindMailboxId && localMsgs.length > 0) {
            sessionChatCache.set(blindMailboxId, localMsgs)
            setMessages(localMsgs)
            setLoadingMessages(false)
          }
        })
        .catch(() => {})
    }

    return () => {
      active = false
    }
  }, [blindMailboxId, activeContact?.userId, activeGroup?.id])

  /**
   * Schickt einen Steuerumschlag (Quittung, Änderung, Löschung, Reaktion …).
   *
   * **Wirft.** Bis 09/2026 verschluckte diese Funktion jeden Fehler, und der
   * Aufrufer hielt ein gescheitertes Senden für erfolgreich. Bei einer Quittung
   * ist das verschmerzbar; bei einer Reaktion, die lokal steht und nie ankommt,
   * ist es eine Lüge. Jeder Aufrufer entscheidet selbst, ob er den Fehler zeigt
   * oder schluckt — hier wird er nur nicht mehr versteckt.
   */
  const sendE2eeControlMessage = async (payloadObj: Record<string, unknown>) => {
    if (!blindMailboxId || !currentUserId || (!activeContact && !activeGroup)) return
    const clientUuid =
      typeof crypto !== 'undefined' && crypto.randomUUID
        ? crypto.randomUUID()
        : 'ctrl-' + Date.now() + '-' + Math.random().toString(36).substring(2, 9)
    const payload = JSON.stringify({ ...payloadObj, client_uuid: clientUuid })
    const auftraege = await konversation.baueSteuerversand(
      payload,
      clientUuid,
      String(payloadObj.type || 'control'),
    )
    await Promise.all(auftraege.map((auftrag) => relayE2eeEnvelope(auftrag)))
  }

  // 5. Load and decrypt messages (non-flickering background sync + real-time)
  const loadMessages = async (isInitial = false) => {
    const currentMid = blindMailboxId
    if (!currentMid || !currentUserId) return
    if (activeMailboxIdRef.current !== currentMid) return
    // Erst entschlüsseln, wenn feststeht, welche Schlüssel dieses Gerät hat.
    // Ein Durchlauf während `loading` hätte keine, würde auf den Altpfad fallen
    // und dessen Ergebnis im Zwischenspeicher festschreiben — der richtige
    // Klartext käme danach nicht mehr durch.
    //
    // Das gilt seit der Umstellung auch für Gruppen: ihr Schlüssel kommt in
    // einem Hybridumschlag, der gegen den Geräteschlüssel versiegelt ist.
    const aktuelleIdentitaet = identityRef.current
    if (aktuelleIdentitaet.state === 'loading') return
    const seq = ++currentLoadSeqRef.current

    if (isInitial && messages.length === 0) {
      setLoadingMessages(true)
    }
    try {
      // Der ganze Entschlüsselungsteil steht in `useKonversation`: welche
      // Mailbox, welches Verfahren, was ein Umschlag bedeutet. Hier bleibt die
      // Anzeige — Quittungen, Häkchen, Bearbeiten und Löschen.
      const gelesen = await konversation.liesUmschlaege()
      if (gelesen === null) return
      if (activeMailboxIdRef.current !== currentMid || currentLoadSeqRef.current !== seq) return

      const decryptedList: ChatMessage[] = []
      const seenEnvelopeIds = new Set<number>()
      const seenClientUuids = new Set<string>()

      // Wirkungen, die aus Steuerumschlägen kommen. Jede Tafel kennt ihre
      // Nachricht über die Umschlagkennung **und** die logische Kennung; warum
      // beides nötig ist, steht in `nachrichtBezug.ts`.
      const aenderungen = neueBezugstafel<{ newText: string; editedAt: string }>()
      const loeschungen = neueBezugstafel<{ deletedAt: string }>()
      const reaktionen = neueSammeltafel<RohReaktion>()
      let maxPartnerReadId = 0
      let maxPartnerDeliveredId = 0
      let maxIncomingId = 0

      for (const lesung of gelesen) {
        const env = lesung.env
        if (seenEnvelopeIds.has(env.id)) continue
        seenEnvelopeIds.add(env.id)
        // 'still' — eine Kopie für ein anderes Gerät, die eigene
        // Ratchet-Nachricht oder eine Schlüsselzustellung. Nichts davon ist ein
        // Fehler, und nichts davon darf als „Verschlüsselte Nachricht" im
        // Verlauf stehen.
        if (lesung.art === 'still') continue
        const plain = lesung.art === 'klartext' ? lesung.text : ''

        if (!plain) {
          const isControl =
            Boolean((env as any).is_control) ||
            Boolean((env as any).control_type) ||
            Boolean(
              env.client_uuid &&
                (env.client_uuid.startsWith('receipt:') ||
                  env.client_uuid.startsWith('ctrl-') ||
                  env.client_uuid.startsWith('deliv-') ||
                  env.client_uuid.toLowerCase().includes('control') ||
                  env.client_uuid.toLowerCase().includes('receipt'))
            )

          if (isControl) {
            // Silence un-decryptable control envelopes; never render in chat timeline
            continue
          }

          const clientUuid = logischeUuid(env.client_uuid)
          if (clientUuid && seenClientUuids.has(clientUuid)) {
            continue
          }
          if (clientUuid) {
            seenClientUuids.add(clientUuid)
          }
          decryptedList.push({
            id: env.id,
            clientUuid,
            senderId: activeContact ? activeContact.userId : 0,
            text: 'Verschlüsselte Nachricht',
            createdAt: env.created_at,
            isSelf: false,
          })
          if (env.id > maxIncomingId) {
            maxIncomingId = env.id
          }
          continue
        }

        try {
          const parsed = JSON.parse(plain)
          if (typeof parsed === 'object' && parsed !== null) {
            // 1. Read receipt control packet
            if (parsed.type === 'read_receipt') {
              const readUpTo = Number(parsed.read_up_to_id || 0)
              const readerId = Number(parsed.reader_id || 0)
              if (Number(readerId) !== Number(currentUserId)) {
                if (readUpTo > maxPartnerReadId) {
                  maxPartnerReadId = readUpTo
                  maxPartnerReadIdRef.current = Math.max(maxPartnerReadIdRef.current, readUpTo)
                }
                if (readUpTo > maxPartnerDeliveredId) {
                  maxPartnerDeliveredId = readUpTo
                  maxPartnerDeliveredIdRef.current = Math.max(maxPartnerDeliveredIdRef.current, readUpTo)
                }
              } else {
                // Multi-Device: Vom aktuellen Benutzer auf anderem Gerät gelesen
                markAsRead(currentMid)
              }
              continue
            }

            // 1b. Delivery receipt control packet
            if (parsed.type === 'delivery_receipt') {
              const deliveredUpTo = Number(parsed.delivered_up_to_id || 0)
              const receiverId = Number(parsed.receiver_id || 0)
              if (Number(receiverId) !== Number(currentUserId) && deliveredUpTo > maxPartnerDeliveredId) {
                maxPartnerDeliveredId = deliveredUpTo
                maxPartnerDeliveredIdRef.current = Math.max(maxPartnerDeliveredIdRef.current, deliveredUpTo)
              }
              continue
            }

            // 2. Edit message control packet
            if (parsed.type === 'edit_message') {
              if (parsed.new_text) {
                aenderungen.merke(parsed, {
                  newText: String(parsed.new_text),
                  editedAt: String(parsed.edited_at || env.created_at),
                })
              }
              continue
            }

            // 3. Delete message control packet
            if (parsed.type === 'delete_message') {
              loeschungen.merke(parsed, {
                deletedAt: String(parsed.deleted_at || env.created_at),
              })
              continue
            }

            // 4. Reaktion auf eine Nachricht
            if (parsed.type === 'reaction') {
              const zeichen = String(parsed.emoji || '')
              const wer = Number(parsed.actor_id || 0)
              if (zeichen && wer) {
                reaktionen.ergaenze(parsed, {
                  emoji: zeichen,
                  actorId: wer,
                  nehmen: parsed.aktion === 'nehmen',
                  zeitpunkt: String(parsed.zeitpunkt || env.created_at),
                })
              }
              continue
            }

            // Normal Chat Message
            // Die Kennung aus dem Umschlag trägt einen Gerätezusatz je Kopie;
            // für den Verlauf zählt die logische darunter.
            const clientUuid = (parsed.client_uuid as string) || logischeUuid(env.client_uuid)
            if (clientUuid && seenClientUuids.has(clientUuid)) {
              continue
            }
            if (clientUuid) {
              seenClientUuids.add(clientUuid)
            }

            let senderId = parsed.sender_id || (activeContact ? activeContact.userId : 0)
            let senderName = parsed.sender_name || parsed.sender_username
            let isSelf = Number(senderId) === Number(currentUserId)

            if (!isSelf && env.id > maxIncomingId) {
              maxIncomingId = env.id
            }

            decryptedList.push({
              id: env.id,
              clientUuid,
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
              videoNoteAttachment: parsed.video_note_attachment,
              antwortAuf: parsed.antwort_auf,
              weitergeleitet: Boolean(parsed.weitergeleitet) || undefined,
              erwaehnungen: Array.isArray(parsed.erwaehnungen) ? parsed.erwaehnungen : undefined,
              // Ob daraus eine Erwähnung wird, entscheidet nicht dieses Feld,
              // sondern die Rechtelage des Absenders — geprüft beim Anzeigen.
              erwaehntAlle: Boolean(parsed.erwaehnt_alle) || undefined,
              verfaelltAm: typeof parsed.verfaellt_am === 'string' ? parsed.verfaellt_am : undefined,
            })
            continue
          }
        } catch {
          // Legacy / simple text fallback
          const clientUuid = logischeUuid(env.client_uuid)
          if (clientUuid && seenClientUuids.has(clientUuid)) {
            continue
          }
          if (clientUuid) {
            seenClientUuids.add(clientUuid)
          }

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
            clientUuid,
            senderId,
            text,
            createdAt: env.created_at,
            isSelf,
          })
        }
      }

      const findeAenderung = (m: Bezugsziel) => aenderungen.finde(m)
      const findeLoeschung = (m: Bezugsziel) => loeschungen.finde(m)

      // Apply Edits, Deletions, and Read Status
      // Gelöscht = gelöscht. Kein Originaltext wird aufbewahrt (Zero Knowledge).
      const processedList: ChatMessage[] = decryptedList.map((msg) => {
        let text = msg.text
        let isEdited = false
        let editedAt: string | undefined = undefined
        let isDeleted = false
        let deletedAt: string | undefined = undefined
        let originalText: string | undefined = undefined

        const aenderung = findeAenderung(msg)
        if (aenderung) {
          originalText = text
          text = aenderung.newText
          isEdited = true
          editedAt = aenderung.editedAt
        }

        const loeschung = findeLoeschung(msg)
        if (loeschung) {
          isDeleted = true
          deletedAt = loeschung.deletedAt
          // Kein originalText bei Löschung — gelöscht ist gelöscht.
          originalText = undefined
        }

        // Dynamisches Häkchen-System (WhatsApp-Style):
        // 1. Gelesen: Gesprächspartner hat die Nachricht quittiert (maxPartnerReadId >= msg.id)
        // 2. Zugestellt: Gesprächspartner hat die Nachricht empfangen (maxPartnerDeliveredId >= msg.id oder bereits gelesen)
        const isRead = msg.isSelf && maxPartnerReadId >= msg.id
        const isDelivered = msg.isSelf && (isRead || maxPartnerDeliveredId >= msg.id)
        const status: 'queued' | 'sent' | 'delivered' | 'read' = isRead
          ? 'read'
          : isDelivered
            ? 'delivered'
            : msg.id > 0
              ? 'sent'
              : 'queued'

        const fertig = {
          ...msg,
          text,
          isEdited,
          editedAt,
          isDeleted,
          deletedAt,
          originalText,
          isDelivered,
          isRead,
          status,
          // Reaktionen aus diesem Fenster in die Zeile schreiben. Der Stand aus
          // der Ablage kommt gleich beim Mischen dazu; `wendeReaktionenAn`
          // trägt hier nur die neu gesehenen Meldungen nach.
          reaktionen: wendeReaktionenAn(msg.reaktionen, reaktionen.finde(msg)),
        }
        // Ausblenden reicht nicht: was hier stehen bleibt, schreibt
        // `saveLocalMessages` gleich wieder auf die Platte — Text und Anhang
        // einer gelöschten Nachricht eingeschlossen.
        return isDeleted && deletedAt ? tilgeInhalt(fertig, deletedAt) : fertig
      })

      // Abort if the user has navigated to another chat in the meantime or a newer load completed
      if (activeMailboxIdRef.current !== currentMid || currentLoadSeqRef.current !== seq) return

      /**
       * Der eigene Gesprächsanteil steht nur hier: eine Ratchet-Nachricht kann
       * ihr Absender nicht öffnen. Ein Ersetzen statt Zusammenführen würde
       * alles selbst Geschriebene bei jedem Abruf wegwischen.
       *
       * **Seit 09/2026 auch für Gruppen.** Dort ließ sich die eigene Nachricht
       * zwar immer schon lesen (Sender Keys sind symmetrisch), weshalb der
       * lokale Verlauf verzichtbar schien. Verzichtbar war er aber nur für den
       * Text: Markierungen, die Suche über alle Chats und die Übersicht „an
       * mich" lesen alle aus dieser Ablage. Ohne sie endeten Gruppen in jeder
       * dieser Ansichten als leere Stelle.
       */
      const rohesLokal = await loadLocalMessages(currentMid)
      if (activeMailboxIdRef.current !== currentMid || currentLoadSeqRef.current !== seq) return

      /**
       * Eine Löschung, die hier ankommt, gilt auch für das, was schon auf der
       * Platte liegt.
       *
       * Der Steuerumschlag nennt eine Nachricht, die dieses Gerät längst hat.
       * Ohne diesen Durchgang blieben zwei Fassungen zurück: die Zeile im
       * eigenen Verlauf mit Text und Anhang, und der abgelegte
       * Umschlag-Klartext. Beide würden beim nächsten Öffnen des Gesprächs
       * wieder gelesen — die Nachricht wäre „für alle gelöscht" und stünde
       * trotzdem da.
       */
      const lokalerVerlauf = rohesLokal.map((m) => {
        const loeschung = findeLoeschung(m)
        if (loeschung && !m.isDeleted) return tilgeInhalt(m, loeschung.deletedAt)
        /**
         * Reaktionen auf die **eigenen** Nachrichten stehen nur hier.
         *
         * Der eigene Gesprächsanteil kommt aus der Ablage, nicht aus der
         * Mailbox — eine Ratchet-Nachricht kann ihr Absender nicht öffnen. Die
         * Reaktion darauf liegt aber als Umschlag in der Mailbox. Ohne diesen
         * Durchgang träfe sie also nie auf ihre Nachricht.
         */
        const neue = wendeReaktionenAn(m.reaktionen, reaktionen.finde(m))
        return neue === m.reaktionen ? m : { ...m, reaktionen: neue }
      })

      const nochZuTilgen = new Map<string, { msg: ChatMessage; geloeschtAm: string }>()
      for (const m of [...rohesLokal, ...processedList] as ChatMessage[]) {
        if (m.isDeleted) continue
        const loeschung = findeLoeschung(m)
        if (!loeschung) continue
        const schluessel = m.clientUuid || `#${m.id}`
        if (!nochZuTilgen.has(schluessel)) {
          nochZuTilgen.set(schluessel, { msg: m, geloeschtAm: loeschung.deletedAt })
        }
      }
      for (const { msg, geloeschtAm } of nochZuTilgen.values()) {
        void tilgeNachrichtLokal(currentMid, msg, geloeschtAm).catch(() => {})
      }

      setMessages((prev) => {
        const processedClientUuids = new Set<string>()
        for (const m of processedList) {
          if (m.clientUuid) processedClientUuids.add(m.clientUuid)
        }
        const pendingOptimistic = prev
          .filter((m) => m.isSelf && m.clientUuid && !processedClientUuids.has(m.clientUuid))
          .map((m) => {
            const isRead = m.isSelf && maxPartnerReadId >= m.id
            const isDelivered = m.isSelf && (isRead || maxPartnerDeliveredId >= m.id)
            return {
              ...m,
              isRead,
              isDelivered,
              status: isRead ? ('read' as const) : isDelivered ? ('delivered' as const) : m.status || ('queued' as const),
            }
          })
        const systemzeilen = prev.filter((m) => m.isSystem)
        const frisch = [...processedList, ...pendingOptimistic, ...systemzeilen]
        const combined = lokalerVerlauf.length
          ? (mischeVerlauf(lokalerVerlauf, frisch) as ChatMessage[])
          : sortMessagesChronologically(frisch)
        sessionChatCache.set(currentMid, combined.slice(-80))
        void saveLocalMessages(currentMid, combined.slice(-200))
        return combined
      })
      // Ungelesen-Zähler zurücksetzen
      markAsRead(currentMid)

      // Prüfen, ob der Ziel-Kontakt blockiert ist: Wenn blockiert, werden keinerlei
      // Zustell- oder Lesequittungen (delivery_receipt, read_receipt) an die Mailbox gesendet!
      // Dadurch verbleibt die Nachricht beim blockierten Absender dauerhaft auf genau 1 grauem Häkchen (✓).
      const isTargetBlocked = activeContact ? isBlocked(activeContact.userId) : false
      const isDocVisible = typeof document === 'undefined' || document.visibilityState === 'visible'

      // Sende Zustellbestätigung (delivery_receipt), sobald neue Nachrichten empfangen wurden
      const needsDelivery =
        maxIncomingId > 0 &&
        maxIncomingId > highestIncomingIdDeliveredRef.current &&
        !isTargetBlocked

      if (needsDelivery) {
        const idToDeliver = maxIncomingId
        sendE2eeControlMessage({
          type: 'delivery_receipt',
          delivered_up_to_id: idToDeliver,
          receiver_id: currentUserId,
          timestamp: new Date().toISOString(),
        })
          .then(() => {
            highestIncomingIdDeliveredRef.current = Math.max(highestIncomingIdDeliveredRef.current, idToDeliver)
          })
          .catch(() => {})
      }

      // Send read receipt if there are new incoming unacknowledged messages and document is visible
      const needsRead =
        maxIncomingId > 0 &&
        maxIncomingId > highestIncomingIdAcknowledgedRef.current &&
        readReceiptsEnabled &&
        isDocVisible &&
        !isTargetBlocked

      if (needsRead) {
        const idToAck = maxIncomingId
        const dispatchReadReceipt = () => {
          sendE2eeControlMessage({
            type: 'read_receipt',
            read_up_to_id: idToAck,
            reader_id: currentUserId,
            timestamp: new Date().toISOString(),
          })
            .then(() => {
              highestIncomingIdAcknowledgedRef.current = Math.max(highestIncomingIdAcknowledgedRef.current, idToAck)
            })
            .catch(() => {})
        }

        if (needsDelivery) {
          setTimeout(dispatchReadReceipt, 350)
        } else {
          dispatchReadReceipt()
        }
      }
    } catch {
      // Offline fallback
    } finally {
      if (isInitial && activeMailboxIdRef.current === currentMid) {
        setLoadingMessages(false)
      }
    }
  }

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
      toast.error('Die Reaktion konnte nicht gesendet werden.')
    }
  }

  /** Antworten: den Zitatkopf über die Eingabe setzen und dorthin springen. */
  const handleAntworten = (msg: ChatMessage) => {
    if (!msg.clientUuid) {
      // Ohne logische Kennung gäbe es nichts, worauf das Zitat zeigen könnte.
      toast.error('Auf diese Nachricht lässt sich nicht antworten.')
      return
    }
    setAntwortAuf({
      clientUuid: msg.clientUuid,
      absenderId: msg.senderId,
      absenderName: msg.isSelf ? user?.username : msg.senderName || activeContact?.username,
      auszug: auszugFuerZitat(msg),
    })
    chatInputRef.current?.focus()
  }

  /** Markieren — bleibt auf diesem Gerät, geht nie über den Server. */
  const handleMarkieren = (msg: ChatMessage) => {
    if (!blindMailboxId) return
    const neu = !msg.istMarkiert
    setMessages((prev) => {
      const geaendert = prev.map((m) => (m.id === msg.id ? { ...m, istMarkiert: neu } : m))
      sessionChatCache.set(blindMailboxId, geaendert.slice(-80))
      return geaendert
    })
    void updateMessageInLocalStore(blindMailboxId, msg.id, { istMarkiert: neu }).catch(() => {})
    vergissMailbox(blindMailboxId)
    toast.success(neu ? 'Markiert.' : 'Markierung entfernt.')
  }

  /** Text in die Zwischenablage. */
  const handleKopieren = async (msg: ChatMessage) => {
    if (!msg.text) return
    try {
      await navigator.clipboard.writeText(msg.text)
      toast.success('Text kopiert.')
    } catch {
      toast.error('Kopieren wurde vom Browser abgelehnt.')
    }
  }

  /**
   * Zu einer Nachricht springen.
   *
   * Nicht animiert: durch tausend Zeilen zu scrollen dauert und bringt nichts.
   * Direkt setzen, dann kurz aufleuchten lassen — das Aufleuchten ist die
   * Antwort auf „wo bin ich jetzt".
   */
  const springeZu = (clientUuid: string) => {
    const ziel = document.querySelector<HTMLElement>(`[data-nachricht="${CSS.escape(clientUuid)}"]`)
    if (!ziel) {
      toast.error('Diese Nachricht liegt nicht mehr auf diesem Gerät.')
      return
    }
    ziel.scrollIntoView({ block: 'center' })
    setHervorgehoben(clientUuid)
    window.setTimeout(() => setHervorgehoben((v) => (v === clientUuid ? null : v)), 1600)
  }

  /** Auswahlmodus: ein Haken je Zeile, die Aktionen unten. */
  const handleAuswahlUmschalten = (msg: ChatMessage) => {
    const schluessel = msg.clientUuid || `#${msg.id}`
    setGewaehlteUuids((v) =>
      v.includes(schluessel) ? v.filter((x) => x !== schluessel) : [...v, schluessel],
    )
  }

  const beendeAuswahl = () => {
    setAuswahlModus(false)
    setGewaehlteUuids([])
  }

  const gewaehlteNachrichten = () =>
    messages.filter((m) => gewaehlteUuids.includes(m.clientUuid || `#${m.id}`))

  /** Mehrere Texte am Stück in die Zwischenablage, in Reihenfolge des Verlaufs. */
  const handleAuswahlKopieren = async () => {
    const text = gewaehlteNachrichten()
      .filter((m) => m.text && !m.isDeleted)
      .map((m) => m.text)
      .join('\n')
    if (!text) {
      toast.error('In der Auswahl steht kein Text.')
      return
    }
    try {
      await navigator.clipboard.writeText(text)
      toast.success('Text kopiert.')
      beendeAuswahl()
    } catch {
      toast.error('Kopieren wurde vom Browser abgelehnt.')
    }
  }

  /**
   * Mehrere löschen.
   *
   * Der Reihe nach über denselben Weg wie eine einzelne Nachricht: erst die
   * Gegenseite, dann Server, dann dieses Gerät. Nebenläufig ginge schneller und
   * würde beim Netzabbruch einen halb geräumten Zustand hinterlassen.
   */
  const handleAuswahlLoeschen = async () => {
    const eigene = gewaehlteNachrichten().filter((m) => m.isSelf && !m.isDeleted)
    if (!eigene.length) return
    beendeAuswahl()
    for (const m of eigene) await handleDeleteMessage(m)
  }

  /** Alle Chats, in die sich weiterleiten lässt — zuletzt genutzte zuerst. */
  const weiterleitungsZiele = useMemo<
    (Weiterleitungsziel & { avatarUrl?: string | null; istGruppe?: boolean })[]
  >(() => {
    const ziele: (Weiterleitungsziel & { avatarUrl?: string | null; istGruppe?: boolean })[] = []
    for (const g of groups) {
      const mid = groupMailboxMap[g.id]
      if (mid) ziele.push({ blindMailboxId: mid, groupId: g.id, name: g.name, istGruppe: true })
    }
    for (const c of contactsList) {
      const mid = contactMailboxMap[c.userId]
      if (!mid || mid === blindMailboxId) continue
      ziele.push({
        blindMailboxId: mid,
        recipientId: c.userId,
        name: c.username,
        avatarUrl: c.avatarUrl,
      })
    }
    // Angeheftete zuerst — das sind die Chats, die jemand selbst als wichtig
    // markiert hat, und meistens leitet man an dieselben zwei Leute weiter.
    return ziele.sort((a, b) => {
      const pa = pinnedChats.indexOf(a.blindMailboxId)
      const pb = pinnedChats.indexOf(b.blindMailboxId)
      if (pa !== pb) return (pa < 0 ? 99 : pa) - (pb < 0 ? 99 : pb)
      return a.name.localeCompare(b.name)
    })
  }, [groups, contactsList, groupMailboxMap, contactMailboxMap, blindMailboxId, pinnedChats])

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
    if (gescheitert) toast.error(`${gescheitert} Nachricht(en) konnten nicht weitergeleitet werden.`)
    else toast.success(ziele.length === 1 ? 'Weitergeleitet.' : `An ${ziele.length} Chats weitergeleitet.`)
  }

  /** Ob ich in dieser Gruppe anheften darf — vom Server entschieden. */
  const darfAnheften = Boolean(activeGroup?.can_pin_messages)

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
      toast.error('Dafür fehlt dir das Recht in dieser Gruppe.')
      return
    }
    const loesen = angeheftet?.clientUuid === msg.clientUuid
    setAngeheftet(loesen ? null : msg)
    try {
      await sendE2eeControlMessage({
        type: 'pin_message',
        ...bezugFelder(msg),
        aktion: loesen ? 'loesen' : 'anheften',
        actor_id: currentUserId,
        zeitpunkt: new Date().toISOString(),
      })
      toast.success(loesen ? 'Nicht mehr angeheftet.' : 'Angeheftet.')
    } catch {
      setAngeheftet(loesen ? msg : null)
      toast.error('Das Anheften konnte nicht gesendet werden.')
    }
  }

  /**
   * Die Verfallsfrist dieses Chats umstellen.
   *
   * Keine heimliche Änderung: der Umschlag geht an die Gegenseite, beide Seiten
   * bekommen dieselbe Systemzeile in den Verlauf. Scheitert das Senden, bleibt
   * die alte Frist stehen — eine Frist, die nur hier gilt, wäre eine Lüge über
   * das, was beim Gegenüber passiert.
   */
  const handleVerfallWaehlen = async (sekunden: number) => {
    if (!blindMailboxId || sekunden === verfallSekunden) {
      setVerfallOffen(false)
      return
    }
    const vorher = verfallSekunden
    setVerfallSekunden(sekunden)
    setzeVerfallsfrist(blindMailboxId, sekunden)
    setVerfallOffen(false)
    try {
      await sendE2eeControlMessage({
        type: 'retention',
        dauer: sekunden,
        actor_id: currentUserId,
        zeitpunkt: new Date().toISOString(),
      })
      zeigeSystemzeile(
        sekunden > 0
          ? `Du hast eingestellt: Nachrichten verschwinden nach ${stufenLabel(sekunden)}.`
          : 'Du hast verschwindende Nachrichten ausgeschaltet.',
      )
    } catch {
      setVerfallSekunden(vorher)
      setzeVerfallsfrist(blindMailboxId, vorher)
      toast.error('Die Umstellung konnte nicht gesendet werden.')
    }
  }

  /** Sucht im offenen Chat, entprellt durch die Suchleiste. */
  const handleSuchen = useCallback(
    (frage: string) => {
      if (!blindMailboxId) return
      if (!frage.trim()) {
        setSuchTreffer([])
        setSuchIndex(0)
        setSucheGesperrt(false)
        return
      }
      void sucheImChat(blindMailboxId, frage).then(({ treffer, gesperrt }) => {
        setSuchTreffer(treffer)
        setSuchIndex(0)
        setSucheGesperrt(gesperrt)
        if (treffer[0]?.clientUuid) springeZu(treffer[0].clientUuid)
      })
    },
    [blindMailboxId],
  )

  const blaettereTreffer = (richtung: 1 | -1) => {
    if (!suchTreffer.length) return
    const naechster = (suchIndex + richtung + suchTreffer.length) % suchTreffer.length
    setSuchIndex(naechster)
    const ziel = suchTreffer[naechster]
    if (ziel.clientUuid) springeZu(ziel.clientUuid)
  }

  /** Öffnet einen Treffer aus der Ansicht über alle Chats. */
  const oeffneTreffer = async (treffer: Treffer) => {
    setUeberall('aus')
    const meta = mailboxDirectory[treffer.blindMailboxId]
    if (meta?.isGroup && meta.groupId) {
      const gruppe = groups.find((g) => g.id === meta.groupId)
      if (gruppe) {
        setActiveGroup(gruppe)
        setActiveContact(null)
      }
    } else if (meta?.userId) {
      const kontakt = contactsList.find((c) => c.userId === meta.userId)
      if (kontakt) {
        setActiveContact(kontakt)
        setActiveGroup(null)
      }
    }
    // Der Verlauf muss erst stehen, bevor der Anker im DOM liegt.
    if (treffer.clientUuid) {
      const uuid = treffer.clientUuid
      window.setTimeout(() => springeZu(uuid), 400)
    }
  }

  /**
   * Die Ansicht über alle Chats öffnen — Suche, Markiertes oder „an mich".
   *
   * Dieselbe Durchsicht, drei Fragen. Bei gesetztem PIN kostet das Entsiegeln
   * Rechenzeit, deshalb läuft es asynchron mit sichtbarem „wird durchgesehen".
   */
  const oeffneUeberall = async (welche: 'suche' | 'markiert' | 'anMich', frage = '') => {
    setUeberall(welche)
    setUeberallFrage(frage)
    setUeberallChats([])
    setUeberallLaeuft(true)
    setUeberallGesperrt(false)
    try {
      const ergebnis =
        welche === 'suche'
          ? await sucheUeberall(frage)
          : welche === 'markiert'
            ? await sammleMarkierte()
            : await sammleAnMich(currentUserId, (m, mid) => {
                // Eine Antwort auf meine Nachricht zählt genauso wie eine
                // Erwähnung: beides heißt „hier werde ich gebraucht".
                const bezug = m.antwortAuf as { absenderId?: number } | undefined
                if (bezug?.absenderId && Number(bezug.absenderId) === Number(currentUserId)) return true
                // Dieselbe Empfängerprüfung wie im Verlauf: ob aus `@everyone`
                // eine Erwähnung wird, entscheidet das Recht des Absenders.
                const meta = mailboxDirectory[mid]
                const gruppe = meta?.groupId ? groups.find((g) => g.id === meta.groupId) : null
                return binIchGemeint(m, currentUserId, gruppe ?? null)
              })
      setUeberallChats(ergebnis.chats)
      setUeberallGesperrt(ergebnis.gesperrt)
    } finally {
      setUeberallLaeuft(false)
    }
  }

  // Action: Edit existing message
  const handleEditMessage = async (msg: ChatMessage, newText: string) => {
    const cleanText = newText.trim()
    if (!cleanText || cleanText === msg.text) {
      setEditingMessage(null)
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
    // Eine Nachricht, die noch in der Warteschlange steht, gibt es nur hier.
    // Sie „für alle" zu löschen ging ins Leere: der Steuerumschlag nannte eine
    // Kennung, die kein anderes Gerät je gesehen hat, und die Zeile blieb
    // stehen. Am laufenden System waren das die Nachrichten mit der Uhr, an
    // die niemand mehr herankam. Also lokal entfernen, aus Ansicht,
    // Warteschlange und Verlauf.
    if (msg.status === 'queued') {
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
      toast.success('Ausstehende Nachricht verworfen.')
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
      await tilgeNachrichtBeimServer(blindMailboxId, msg)

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

      toast.success('Nachricht für alle gelöscht.')
      await loadMessages(false)
    } catch {
      toast.error('Fehler beim Löschen der Nachricht.')
    }
  }

  /**
   * Abgelaufene Zeilen wegräumen.
   *
   * Jede Seite tilgt bei sich lokal; beim Server räumt nur ab, wer selbst
   * gesendet hat — niemand sonst darf das. Läuft still: ein Hinweis pro
   * verschwundener Nachricht wäre genau das Gegenteil von „verschwunden".
   */
  useEffect(() => {
    if (!blindMailboxId) return
    let aktiv = true

    const raeumeAuf = async () => {
      const faellig = faelligeZeilen(messages)
      if (!faellig.length || !aktiv) return
      const jetzt = new Date().toISOString()
      for (const msg of faellig) {
        try {
          if (msg.isSelf) await tilgeNachrichtBeimServer(blindMailboxId, msg)
          await tilgeNachrichtLokal(blindMailboxId, msg, jetzt)
        } catch {
          // Was jetzt nicht wegging, geht beim nächsten Durchgang.
        }
      }
      if (!aktiv) return
      const weg = new Set(faellig.map((m) => m.id))
      setMessages((prev) => {
        const uebrig = prev.filter((m) => !weg.has(m.id))
        sessionChatCache.set(blindMailboxId, uebrig.slice(-80))
        return uebrig
      })
      vergissMailbox(blindMailboxId)
    }

    void raeumeAuf()
    const takt = window.setInterval(() => void raeumeAuf(), 60_000)
    return () => {
      aktiv = false
      window.clearInterval(takt)
    }
  }, [blindMailboxId, messages])

  // Real-time SSE event listener for zero-latency incoming messages & typing signals
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
    window.addEventListener('msm:message-confirmed', handleMessagesUpdated)
    if (typeof document !== 'undefined') {
      document.addEventListener('visibilitychange', handleVisibilityChange)
    }
    return () => {
      window.removeEventListener('msm:sync-event', handleSync)
      window.removeEventListener('msm:messages-updated', handleMessagesUpdated)
      window.removeEventListener('msm:message-confirmed', handleMessagesUpdated)
      if (typeof document !== 'undefined') {
        document.removeEventListener('visibilitychange', handleVisibilityChange)
      }
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
    highestIncomingIdAcknowledgedRef.current = 0
    highestIncomingIdDeliveredRef.current = 0
    maxPartnerReadIdRef.current = 0
    maxPartnerDeliveredIdRef.current = 0
  }, [blindMailboxId])

  /**
   * Fasst die Warteschlange nach.
   *
   * `replayOutbox` hatte bis 09/2026 genau einen Auslöser in der Anwendung:
   * `getNotesOffline`. Eine Nachricht, deren Versand scheiterte — ein
   * Ratenlimit reicht —, lag danach in `msm_offline_outbox` und wurde erst
   * wieder angefasst, wenn der Benutzer zufällig die Notizen öffnete. Am
   * laufenden System standen so 31 Aufträge mit `retryCount: 0` und rührten
   * sich nicht, während im Verlauf Nachrichten mit dem Uhr-Symbol hingen. Der
   * Chat ist der Ort, an dem man diese Uhr sieht, also fasst er auch nach.
   */
  const fasseWarteschlangeNach = useCallback(() => {
    if (getOutbox().length === 0) return
    void replayOutbox().catch(() => {})
  }, [])

  useEffect(() => {
    if (blindMailboxId && (activeContact || activeGroup)) {
      void loadMessages(true)
      fasseWarteschlangeNach()
      const interval = setInterval(() => {
        void loadMessages(false)
        fasseWarteschlangeNach()
      }, 5000)
      const beiNetz = () => fasseWarteschlangeNach()
      window.addEventListener('online', beiNetz)
      return () => {
        clearInterval(interval)
        window.removeEventListener('online', beiNetz)
      }
    }
    // `identity.state` gehört in die Abhängigkeiten: nach dem Entsperren muss
    // derselbe Chat noch einmal durchlaufen, sonst bleibt der eben lesbar
    // gewordene Verlauf bis zum nächsten Wechsel stumm.
  }, [blindMailboxId, activeContact?.userId, activeGroup?.id, identity.state])

  // Listen for offline queue background confirmations from offlineSync
  useEffect(() => {
    const handleMessageConfirmed = (e: Event) => {
      const ce = e as CustomEvent<{ client_uuid: string; envelope_id: number; blind_mailbox_id: string }>
      const detail = ce.detail
      if (!detail?.client_uuid || !detail?.envelope_id) return
      const { envelope_id, blind_mailbox_id: confirmedMid } = detail
      /**
       * Die Warteschlange meldet die Kennung **mit** Gerätesuffix — ein
       * Auftrag steht je Zielgerät darin, und `#<geraet>` hält sie
       * auseinander. Die Zeile im Verlauf trägt die logische Kennung ohne
       * Suffix. Der Vergleich traf deshalb nie zu: der Umschlag ging raus, die
       * Zeile behielt ihre Uhr, und weil `updateMessageInLocalStore` dieselbe
       * Kennung benutzte, überlebte sie auch jedes Neuladen. Am laufenden
       * System waren das die Nachrichten, die zugestellt waren und trotzdem
       * für immer „in der Warteschlange" standen.
       */
      const client_uuid = logischeUuid(detail.client_uuid) ?? detail.client_uuid

      if (confirmedMid === blindMailboxId) {
        setMessages((prev) =>
          sortMessagesChronologically(
            prev.map((m) => {
              if (m.clientUuid === client_uuid) {
                const isRead = maxPartnerReadIdRef.current >= envelope_id
                const isDelivered = isRead || maxPartnerDeliveredIdRef.current >= envelope_id
                const status: 'queued' | 'sent' | 'delivered' | 'read' = isRead ? 'read' : isDelivered ? 'delivered' : 'sent'
                return {
                  ...m,
                  id: envelope_id,
                  status,
                  isRead: m.isRead || isRead,
                  isDelivered: m.isDelivered || isDelivered,
                }
              }
              return m
            })
          )
        )
      }
      if (confirmedMid) {
        void updateMessageInLocalStore(confirmedMid, client_uuid, {
          id: envelope_id,
          status: 'sent',
        })
      }
    }
    window.addEventListener('msm:message-confirmed', handleMessageConfirmed)
    return () => {
      window.removeEventListener('msm:message-confirmed', handleMessageConfirmed)
    }
  }, [blindMailboxId])

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

  /**
   * Der Knopf „nach unten".
   *
   * Wer weiter oben liest, verliert sonst den Anschluss an das, was gerade
   * hereinkommt. Beim Springen zu einem Suchtreffer ist er der Rückweg.
   */
  const [weitOben, setWeitOben] = useState(false)

  /**
   * Die Trennlinie „Neue Nachrichten".
   *
   * Der Zähler steht nur fest, solange der Chat noch nicht offen ist — mit dem
   * Öffnen wird gelesen. Er wird deshalb beim Antippen der Zeile festgehalten
   * und hier auf die erste noch ungelesene Nachricht umgerechnet.
   */
  const [trennerId, setTrennerId] = useState<number | null>(null)
  const ungelesenBeimOeffnen = useRef(0)
  const trennerGesetztFuer = useRef<string | null>(null)

  useEffect(() => {
    if (!blindMailboxId || !messages.length) return
    if (trennerGesetztFuer.current === blindMailboxId) return
    trennerGesetztFuer.current = blindMailboxId
    const offen = ungelesenBeimOeffnen.current
    ungelesenBeimOeffnen.current = 0
    if (!offen) {
      setTrennerId(null)
      return
    }
    const fremde = messages.filter((m) => !m.isSelf && !m.isSystem)
    const erste = fremde[Math.max(0, fremde.length - offen)]
    setTrennerId(erste ? erste.id : null)
  }, [blindMailboxId, messages])
  useEffect(() => {
    const container = scrollContainerRef.current
    if (!container) return
    const pruefe = () => {
      setWeitOben(container.scrollHeight - container.scrollTop - container.clientHeight > 400)
    }
    pruefe()
    container.addEventListener('scroll', pruefe, { passive: true })
    return () => container.removeEventListener('scroll', pruefe)
  }, [blindMailboxId])

  // 6. Send message (text, note, cal, img, audio, file, sticker)
  const handleSendMessage = async (auftrag: SendeAuftrag = {}) => {
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

    // If currently editing a message, redirect to edit handler
    if (editingMessage) {
      const textToSave = customText !== undefined ? customText : inputText
      await handleEditMessage(editingMessage, textToSave)
      return
    }

    const rawText = customText !== undefined ? customText : inputText.trim()
    if (
      (!rawText && !note && !cal && !img && !audio && !file && !sticker && !storyReply && !videoNote) ||
      (!activeContact && !activeGroup) ||
      !blindMailboxId ||
      !currentUserId
    ) {
      return
    }

    const targetBlindMailboxId = blindMailboxId
    const targetUserId = activeContact?.userId
    const currentGroupId = activeGroup?.id

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
      isDelivered: false,
      isRead: false,
      status: 'queued',
    }

    setMessages((prev) => {
      const updated = [...prev, optimisticMessage]
      sessionChatCache.set(targetBlindMailboxId, updated.slice(-80))
      void saveLocalMessages(targetBlindMailboxId, updated.slice(-200))
      return updated
    })
    setInputText('')
    setSelectedImage(null)
    setStagedFile(null)
    setAntwortAuf(null)
    setErwaehnungsVorschlaege([])
    // Der Entwurf ist verschickt, also keiner mehr. Die wartende Entprellung
    // muss mit weg, sonst schreibt sie den gerade gesendeten Text zurück.
    if (entwurfUhr.current) clearTimeout(entwurfUhr.current)
    entwurfOffen.current = null
    void speichereEntwurf(targetBlindMailboxId, '').catch(() => {})
    setEntwuerfe((v) => {
      if (!v[targetBlindMailboxId]) return v
      const neu = { ...v }
      delete neu[targetBlindMailboxId]
      return neu
    })
    justSentRef.current = true
    lastTypingSentRef.current = 0

    if (targetBlindMailboxId) {
      void sendTypingSignal({
        blind_mailbox_id: targetBlindMailboxId,
        status: 'idle',
        recipient_id: targetUserId ?? null,
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
      const payloadObj: Record<string, unknown> = {
        client_uuid: clientUuid,
        sender_id: currentUserId,
        sender_name: user?.username || 'Ich',
        text: rawText,
        timestamp: new Date().toISOString(),
        // Nur setzen, was es gibt: ein Umschlag voller `undefined` kostet
        // Bytes, und jedes Byte reist verschlüsselt mit.
        ...(bezug ? { antwort_auf: bezug } : {}),
        ...(weitergeleitet ? { weitergeleitet: true } : {}),
        ...(erwaehnt.erwaehnungen?.length ? { erwaehnungen: erwaehnt.erwaehnungen } : {}),
        ...(erwaehnt.erwaehntAlle ? { erwaehnt_alle: true } : {}),
        ...(optimisticMessage.verfaelltAm ? { verfaellt_am: optimisticMessage.verfaelltAm } : {}),
      }

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
          recipientId: targetUserId,
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
        void updateMessageInLocalStore(targetBlindMailboxId, clientUuid, nachtrag)
      }

      if (note) payloadObj.note_attachment = note
      if (cal) payloadObj.calendar_attachment = cal
      if (finalImg) payloadObj.image_attachment = finalImg
      if (finalAudio) payloadObj.audio_attachment = finalAudio
      if (finalFile) payloadObj.file_attachment = finalFile
      if (sticker) payloadObj.sticker_attachment = sticker
      if (storyReply) payloadObj.story_reply = storyReply
      if (finalVideoNote) payloadObj.video_note_attachment = finalVideoNote

      const payload = JSON.stringify(payloadObj)

      // Welche Umschläge daraus werden, entscheidet `useKonversation`: einer
      // für die Gruppe, oder je Empfängergerät und eigenem Zweitgerät einer aus
      // dem Double Ratchet, dem sein Sitzungsaufbau vorausgeht. Der
      // Gruppenschlüssel rotiert dabei, falls sich die Mitgliedschaft geändert
      // hat. Das steht bewusst vor der Offline-Abzweigung: ohne Netz gibt es
      // weder einen frischen Schlüssel noch einen Weg, ihn zu verteilen.
      if (!currentGroupId && !aktiveIdentitaet.sendPair) {
        throw new Error('Der Schlüssel dieses Geräts ist noch nicht bereit.')
      }
      const auftraege = await konversation.baueVersand(payload, clientUuid)
      if (auftraege.length === 0) {
        // Früher fiel der Sendepfad hier auf einen Schlüssel zurück, den das
        // Backend aus den beiden Benutzerkennungen selbst bilden kann. Lieber
        // nicht senden und es sagen.
        if (currentGroupId) throw new Error('Die Gruppe ist noch nicht bereit.')
        throw new E2eeRecipientKeyMissingError(targetUserId ?? 0)
      }

      /**
       * Die niedrigste Umschlagkennung der Auffächerung gilt als Kennung dieser
       * Nachricht. Quittungen der Gegenstelle nennen die Kennung der Kopie, die
       * *sie* gesehen hat — also eine aus derselben Auffächerung und damit nie
       * kleinere. Der Vergleich `quittiert >= meine` trägt deshalb weiter.
       */
      let niedrigsteId = 0
      let verbindungsfehler = false
      for (const auftrag of auftraege) {
        // Reihenfolge ist bindend: ohne den Sitzungsaufbau findet die
        // Gegenstelle keine Sitzung und läuft in den Sitzungsbruch.
        //
        // Gesendet wird ohne Vorabfrage. Hier stand bis 09/2026
        // `!navigator.onLine` davor und legte jeden Auftrag ungeprüft in die
        // Warteschlange. Diese Auskunft des Systems ist keine Aussage über die
        // Erreichbarkeit des Backends: im Tauri-Fenster unter Windows meldet
        // sie schon dann „offline", wenn ein virtueller Netzadapter dazwischen
        // liegt, und der Benutzer sah Nachrichten, die nie losgingen, obwohl
        // sein Netz stand. Ob es geht, weiss nur der Versuch.
        try {
          const r = await relayE2eeEnvelope(auftrag)
          if (!auftrag.is_control && r && typeof r.id === 'number') {
            if (niedrigsteId === 0 || r.id < niedrigsteId) niedrigsteId = r.id
          }
        } catch {
          enqueueMessageMutation(auftrag)
          verbindungsfehler = true
        }
      }

      if (verbindungsfehler && niedrigsteId === 0) {
        toast.info('Nachricht offline in Warteschlange eingereiht (Verbindungsfehler).')
      }

      if (niedrigsteId > 0) {
        const serverId = niedrigsteId
        const isRead = maxPartnerReadIdRef.current >= serverId
        const isDelivered = isRead || maxPartnerDeliveredIdRef.current >= serverId
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
              }
            : m
        setMessages((prev) => sortMessagesChronologically(prev.map(nachziehen)))
        const cached = sessionChatCache.get(targetBlindMailboxId)
        if (cached) {
          sessionChatCache.set(targetBlindMailboxId, sortMessagesChronologically(cached.map(nachziehen)))
        }
        void updateMessageInLocalStore(targetBlindMailboxId, clientUuid, {
          id: serverId,
          status,
          isRead,
          isDelivered,
        })
      }

      await loadMessages()
    } catch (err: unknown) {
      const istSchluesselProblem =
        err instanceof E2eeRecipientKeyMissingError ||
        err instanceof E2eeIdentityLockedError ||
        err instanceof DrZustellungFehlgeschlagenError

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

        if (err instanceof E2eeIdentityLockedError) {
          // Der Schlüssel dieses Geräts war noch nicht fertig angelegt. Beim
          // nächsten Versuch steht er — es gibt nichts, was der Benutzer dafür
          // tun müsste.
          toast.error('Der Schlüssel dieses Geräts ist noch nicht bereit. Bitte kurz erneut versuchen.')
        } else if (err instanceof DrZustellungFehlgeschlagenError) {
          // Die Gegenstelle ist angemeldet, das Verschlüsseln hat versagt. Der
          // nächste Versuch setzt die Sitzung neu auf, deshalb der Hinweis auf
          // das Wiederholen statt einer Aussage über den Kontakt.
          toast.error('Die Nachricht liess sich nicht verschlüsseln und wurde nicht gesendet. Bitte erneut versuchen.')
        } else {
          toast.error(
            `${activeContact?.username ?? 'Dieser Kontakt'} ist mit keinem Gerät angemeldet. Die Nachricht wurde nicht gesendet.`
          )
        }
      } else {
        const msg = err instanceof Error ? err.message : 'Fehler beim Senden'
        toast.error(msg)
      }
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
              handleSendMessage({
                text: '',
                audio: {
                  dataUrl,
                  durationSeconds: Math.max(1, duration),
                  mimeType: mime,
                },
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

  /** Die eigene Aufnahme, solange sie lokal liegt; sonst aus dem Medienspeicher. */
  const tonQuelle = async (
    anhang: AudioAttachment,
    bindung: MedienBindungsKontext
  ): Promise<string | null> => anhang.dataUrl || (await holeAnhangUrl(anhang, bindung))

  // Voice playback handler
  const togglePlayAudio = async (
    messageId: number,
    anhang: AudioAttachment,
    bindung: MedienBindungsKontext
  ) => {
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
      const quelle = await tonQuelle(anhang, bindung)
      if (!quelle) {
        toast.error('Sprachnachricht konnte nicht geladen werden.')
        return
      }

      const audio = new Audio(quelle)
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
  const handleWaveformSeek = async (
    messageId: number,
    anhang: AudioAttachment,
    bindung: MedienBindungsKontext,
    e: React.MouseEvent<HTMLDivElement>
  ) => {
    e.stopPropagation()
    // Die Maße des Elements müssen vor jedem `await` feststehen: React gibt das
    // Ereignis danach frei und `currentTarget` ist null.
    const rect = e.currentTarget.getBoundingClientRect()
    const durationSeconds = anhang.durationSeconds
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
      const quelle = await tonQuelle(anhang, bindung)
      if (!quelle) {
        toast.error('Sprachnachricht konnte nicht geladen werden.')
        return
      }

      const audio = new Audio(quelle)
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
  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    if (!file.type.startsWith('image/')) {
      toast.error('Bitte ein gültiges Bild auswählen.')
      return
    }

    try {
      const compressed = await compressImageFile(file)
      setSelectedImage({ dataUrl: compressed.dataUrl, name: compressed.name })
    } catch {
      const reader = new FileReader()
      reader.onload = (event) => {
        const dataUrl = event.target?.result as string
        if (dataUrl) {
          setSelectedImage({ dataUrl, name: file.name })
        }
      }
      reader.readAsDataURL(file)
    }
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
      // Wer draußen ist, braucht die Schlüssel nicht mehr — und soll sie auch
      // nicht behalten. Der Verlauf dieser Gruppe wird damit unlesbar, was
      // genau die Zusage ist, die ein Austritt geben soll.
      await verwirfGruppenSchluessel(group.id).catch(() => {})
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
      await verwirfGruppenSchluessel(groupToDelete.id).catch(() => {})
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
    //
    // Die Obergrenze rechnet sich aus dem Deckel des Backends zurück: die Datei
    // wird als data-URL gelesen (ein Drittel mehr) und dann verschlüsselt
    // verpackt. Hier standen früher feste 25 MB — genau der Deckel, den der
    // fertige Blob nicht überschreiten darf. Eine 20-MB-Datei lief damit durch
    // die ganze Verschlüsselung und scheiterte erst am Upload.
    const MAX_FILE_BYTES = Math.floor(maxKlartextBytes() * 0.75)
    const MAX_IMAGE_BYTES = Math.min(8 * 1024 * 1024, MAX_FILE_BYTES)

    const isImage = file.type.startsWith('image/')
    const limit = isImage ? MAX_IMAGE_BYTES : MAX_FILE_BYTES
    if (file.size > limit) {
      toast.error(`Datei ist zu groß (maximal ${Math.floor(limit / (1024 * 1024))} MB erlaubt).`)
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
      compressImageFile(file)
        .then((compressed) => {
          setSelectedImage({ dataUrl: compressed.dataUrl, name: compressed.name })
        })
        .catch(() => {
          const reader = new FileReader()
          reader.onload = (event) => {
            const dataUrl = event.target?.result as string
            if (dataUrl) {
              setSelectedImage({ dataUrl, name: file.name })
            }
          }
          reader.readAsDataURL(file)
        })
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

  /**
   * Was in dieser Gruppe erlaubt ist, sagt das Backend.
   *
   * Vorher stand hier eine zweite Regel, die jedem Mitglied das Beitreten
   * zusprach; das Backend verlangt dafür `join_group_calls` und antwortete
   * danach mit 403. Ein Knopf, der sicher scheitert, ist schlimmer als keiner.
   * Moderation und Freigabe hängen an der Gruppenrolle, nicht am Anrufrecht.
   */
  const groupCallPermissions = useMemo(() => {
    if (!activeGroup) {
      return {
        canStart: false,
        canJoin: false,
        canShare: false,
        canModerate: false,
        canMute: false,
        canKick: false,
      }
    }
    return {
      canStart: activeGroup.can_start_call === true,
      canJoin: activeGroup.can_join_call === true,
      canShare: activeGroup.can_share_screen === true,
      // „Moderieren" heisst hier nur: den Raum für alle schliessen dürfen. Das
      // hängt am Startrecht, weil genau das der Endpunkt prüft.
      canModerate: activeGroup.can_start_call === true,
      canMute: activeGroup.can_mute_others === true,
      canKick: activeGroup.can_kick_from_call === true,
    }
  }, [activeGroup])

  const handleStartGroupCall = async (joinExisting = false) => {
    if (!activeGroup) return
    if (joinExisting ? !groupCallPermissions.canJoin : !groupCallPermissions.canStart) {
      toast.error(
        joinExisting
          ? 'Du hast in dieser Gruppe keine Berechtigung, einem Gruppenanruf beizutreten.'
          : 'Du hast in dieser Gruppe keine Berechtigung, einen Gruppenanruf zu starten.'
      )
      return
    }

    // Beim Start bekommt jedes Mitglied den Raumschlüssel zugestellt. Wer
    // später dazukommt, bekommt ihn im Raum nachgereicht.
    const mitgliederIds = (activeGroup.members ?? []).map((member) => member.user_id)

    let roomToken: string
    try {
      if (joinExisting) {
        const existingToken = (activeGroup as ChatGroupItem & { room_token?: string }).room_token
        if (!existingToken) {
          toast.error('Für diesen Gruppenanruf ist kein Raum verfügbar.')
          return
        }
        roomToken = existingToken
      } else {
        const room = await starteGruppenanruf(activeGroup.id)
        roomToken = room.room_token
      }
    } catch {
      toast.error(
        joinExisting
          ? 'Der Gruppenanruf konnte nicht geöffnet werden.'
          : 'Der Gruppenanruf konnte nicht gestartet werden.'
      )
      return
    }

    await useCallStore.getState().joinGroupCall(
      {
        id: activeGroup.id,
        name: activeGroup.name,
        avatarUrl: activeGroup.avatar_url ?? null,
        canShare: groupCallPermissions.canShare,
        canModerate: groupCallPermissions.canModerate,
        canMute: groupCallPermissions.canMute,
        canKick: groupCallPermissions.canKick,
      },
      roomToken,
      // Nur der Startende verteilt; ein Beitretender hat den Schlüssel noch
      // nicht und hätte nichts zu verteilen.
      joinExisting ? undefined : mitgliederIds
    )
  }

  const isChatOpen = Boolean(activeContact || activeGroup)

  /**
   * Anheften und Archivieren — beides bleibt auf diesem Gerät.
   *
   * Der Hinweis mit „Widerrufen" ist kein Schmuck: eine Wischgeste löst
   * versehentlich aus, und ohne Rückweg wäre der Chat weg, ohne dass jemand
   * wüsste wohin.
   */
  const handleAnheftenChat = (mid: string) => {
    const war = pinnedChats.includes(mid)
    const ergebnis = schalteAnheften(mid)
    if (!ergebnis.ok) {
      toast.error(`Höchstens ${PINS_MAX} Chats lassen sich anheften. Löse zuerst einen.`)
      return
    }
    setWiderruf({
      text: war ? 'Nicht mehr angeheftet.' : 'Angeheftet.',
      zurueck: () => {
        schalteAnheften(mid)
      },
    })
  }

  const handleArchivieren = (mid: string) => {
    const war = archivedChats.includes(mid)
    schalteArchiv(mid)
    setWiderruf({
      text: war ? 'Aus dem Archiv geholt.' : 'Archiviert.',
      zurueck: () => {
        schalteArchiv(mid)
      },
    })
  }

  /** Die Abzeichen rechts an einer Chatzeile — für Gruppen und Kontakte gleich. */
  const zeilenAbzeichen = (mid: string | undefined) => {
    if (!mid) return null
    const unread = unreadCounts[mid] || 0
    return (
      <div className="flex items-center gap-1.5 shrink-0 ml-2">
        {pinnedChats.includes(mid) && <Pin className="w-3.5 h-3.5 text-primary/70" />}
        {archivedChats.includes(mid) && <Archive className="w-3.5 h-3.5 text-on-surface-variant/50" />}
        {isChatMuted(mid) && <BellOff className="w-3.5 h-3.5 text-on-surface-variant/50" />}
        {mentionedChats.includes(mid) && (
          <span
            title="Du wurdest erwähnt"
            className="inline-flex items-center justify-center w-[18px] h-[18px] rounded-full bg-primary/20 text-primary"
          >
            <AtSign className="w-3 h-3" />
          </span>
        )}
        {unread > 0 && (
          <span className="inline-flex items-center justify-center px-1.5 py-0.5 text-[10px] font-bold rounded-full bg-primary text-on-primary min-w-[18px]">
            {unread > 99 ? '99+' : unread}
          </span>
        )}
      </div>
    )
  }

  /** Die zweite Zeile: ein angefangener Entwurf schlägt jede Beschreibung. */
  const zeilenVorschau = (mid: string | undefined, sonst: React.ReactNode) => {
    const entwurf = mid ? entwuerfe[mid] : ''
    if (!entwurf) return sonst
    return (
      <p className="text-[11px] truncate">
        <span className="text-status-warning font-semibold">Entwurf: </span>
        <span className="text-on-surface-variant/80">{entwurf}</span>
      </p>
    )
  }

  /** Eine Gruppenzeile, eingefasst in Wischgeste und Langdruckmenü. */
  const zeichneGruppe = (g: ChatGroupItem) => {
    const isSelected = activeGroup?.id === g.id
    const gmid = groupMailboxMap[g.id]
    const imArchiv = gmid ? archivedChats.includes(gmid) : false
    return (
      <ChatZeilenGeste
        key={`g-${g.id}`}
        angeheftet={gmid ? pinnedChats.includes(gmid) : false}
        archiviert={imArchiv}
        onAnheften={() => gmid && handleAnheftenChat(gmid)}
        onArchivieren={() => gmid && handleArchivieren(gmid)}
        onMenue={() => gmid && setZeilenMenue({ mid: gmid, name: g.name })}
      >
        <button
          type="button"
          onClick={() => {
            setActiveGroup(g)
            setActiveContact(null)
            if (gmid) {
              ungelesenBeimOeffnen.current = unreadCounts[gmid] || 0
              trennerGesetztFuer.current = null
              markAsRead(gmid)
            }
          }}
          className={`w-full flex items-center justify-between p-2.5 rounded-xl text-left transition-all ${
            isSelected
              ? 'bg-primary/15 border border-primary/30 shadow-xs'
              : 'hover:bg-surface-container-high/60 border border-transparent'
          } ${imArchiv ? 'opacity-60' : ''}`}
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
                <span className="text-xs font-semibold text-primary truncate">{g.name}</span>
                <span className="text-[10px] text-on-surface-variant/60 shrink-0">{g.member_count} M.</span>
              </div>
              {zeilenVorschau(
                gmid,
                <p className="text-[11px] text-on-surface-variant/80 truncate">
                  {g.description || 'Verschlüsselte Gruppe'}
                </p>,
              )}
            </div>
          </div>
          {zeilenAbzeichen(gmid)}
        </button>
      </ChatZeilenGeste>
    )
  }

  /** Eine Kontaktzeile. */
  const zeichneKontakt = (c: (typeof contactsList)[number]) => {
    const isSelected = activeContact?.userId === c.userId
    const cmid = contactMailboxMap[c.userId]
    const isUserBlocked = isBlocked(c.userId)
    const imArchiv = cmid ? archivedChats.includes(cmid) : false
    return (
      <ChatZeilenGeste
        key={c.listKey}
        angeheftet={cmid ? pinnedChats.includes(cmid) : false}
        archiviert={imArchiv}
        onAnheften={() => cmid && handleAnheftenChat(cmid)}
        onArchivieren={() => cmid && handleArchivieren(cmid)}
        onMenue={() => cmid && setZeilenMenue({ mid: cmid, name: c.username })}
      >
        <button
          type="button"
          onClick={() => {
            setActiveContact(c)
            setActiveGroup(null)
            if (cmid) {
              ungelesenBeimOeffnen.current = unreadCounts[cmid] || 0
              trennerGesetztFuer.current = null
              markAsRead(cmid)
            }
          }}
          className={`w-full flex items-center justify-between p-2.5 rounded-xl text-left transition-all ${
            isSelected
              ? 'bg-primary/15 border border-primary/30 shadow-xs'
              : 'hover:bg-surface-container-high/60 border border-transparent'
          } ${imArchiv ? 'opacity-60' : ''}`}
        >
          <div className="flex items-center gap-3 min-w-0 flex-1">
            <div className="relative shrink-0">
              <Avatar src={c.avatarUrl} name={c.username} size="sm" />
              <StatusDot status={c.status} size="sm" className="absolute bottom-0 right-0" />
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-1.5">
                <span className="text-xs font-semibold text-primary truncate">{c.username}</span>
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
              {zeilenVorschau(
                cmid,
                <>
                  {c.teamName && (
                    <p className="text-[10px] text-tertiary truncate flex items-center gap-1">
                      <UsersRound className="w-2.5 h-2.5" />
                      <span>{c.teamName}</span>
                    </p>
                  )}
                  {c.isPublicUser && !c.isFriend && !c.teamName && (
                    <p className="text-[10px] text-on-surface-variant/70 truncate">E2EE Chat bereit</p>
                  )}
                  {c.activityLabel && !c.teamName && (
                    <p className="text-[10px] text-on-surface-variant/80 truncate">{c.activityLabel}</p>
                  )}
                </>,
              )}
            </div>
          </div>
          {zeilenAbzeichen(cmid)}
        </button>
      </ChatZeilenGeste>
    )
  }

  // Gesperrt wird der Verlauf nicht überdeckt, sondern gar nicht erst gebaut.
  // Er stünde auch nicht zur Verfügung: die lokalen Ablagen geben ohne
  // Schlüssel nichts heraus (siehe `services/lokaleVersiegelung`).
  if (messengerGesperrt) {
    return (
      <div className="flex h-full w-full min-h-0 flex-1 flex-col overflow-hidden bg-surface">
        <MessengerSperrschirm />
      </div>
    )
  }

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
              size="sm"
              onClick={() => handleStartGroupCall(true)}
              disabled={!groupCallPermissions.canJoin}
              className="h-8 gap-1.5 bg-surface-container-high/85 px-2.5 text-xs text-cyan-200 shadow-xs hover:bg-surface-container-high disabled:cursor-not-allowed disabled:opacity-60"
              title={
                groupCallPermissions.canJoin
                  ? 'Laufendem Gruppenanruf beitreten'
                  : 'Du hast in dieser Gruppe keine Berechtigung, einem Gruppenanruf beizutreten.'
              }
              aria-label="Laufendem Gruppenanruf beitreten"
            >
              <Phone className="w-3.5 h-3.5" />
              <span className="hidden sm:inline">Beitreten</span>
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

          {/* Der Rückweg nach einer Wischgeste. Unten, weil dort der Daumen ist. */}
          {widerruf && (
            <div className="shrink-0 mx-2 mb-1 px-3 py-2 rounded-xl bg-surface-container-high border border-outline-variant/30 flex items-center justify-between gap-2">
              <span className="text-xs text-on-surface truncate">{widerruf.text}</span>
              <button
                type="button"
                onClick={() => {
                  widerruf.zurueck()
                  setWiderruf(null)
                }}
                className="min-h-11 px-3 text-xs font-semibold text-primary hover:bg-surface-container-highest rounded-lg transition-colors shrink-0"
              >
                Widerrufen
              </button>
            </div>
          )}

          {/* List Scroll Area */}
          <div className="flex-1 overflow-y-auto p-2 space-y-1">
            {/* View 1: Standard Chats Mode */}
            {mobileNavTab === 'chats' && (
              <>
                {/* Das Archiv. Ein weggeräumter Chat bleibt weggeräumt, auch
                    wenn neue Nachrichten kommen; sein Ungelesen-Zähler steht
                    deshalb hier und nicht in der Hauptliste. */}
                {archivZahl > 0 && (
                  <button
                    type="button"
                    onClick={() => setArchivOffen((offen) => !offen)}
                    className="w-full min-h-11 px-2.5 py-2 mb-1 flex items-center gap-2.5 rounded-xl text-left hover:bg-surface-container-high/60 transition-colors"
                    aria-expanded={archivOffen}
                  >
                    <span className="w-9 h-9 rounded-full bg-surface-container-high text-on-surface-variant flex items-center justify-center shrink-0">
                      <Archive className="w-4 h-4" />
                    </span>
                    <span className="min-w-0 flex-1 text-xs font-semibold text-on-surface-variant">
                      Archiviert ({archivZahl})
                    </span>
                    {archivUngelesen > 0 && (
                      <span className="inline-flex items-center justify-center px-1.5 py-0.5 text-[10px] font-bold rounded-full bg-on-surface-variant/20 text-on-surface-variant min-w-[18px]">
                        {archivUngelesen > 99 ? '99+' : archivUngelesen}
                      </span>
                    )}
                    <ChevronDown
                      className={`w-4 h-4 text-on-surface-variant/70 shrink-0 transition-transform ${
                        archivOffen ? 'rotate-180' : ''
                      }`}
                    />
                  </button>
                )}

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
                    {(archivOffen
                      ? [...gruppenNachArchiv.sichtbar, ...gruppenNachArchiv.imArchiv]
                      : gruppenNachArchiv.sichtbar
                    ).map((g) => zeichneGruppe(g))}
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
                    {(archivOffen
                      ? [...kontakteNachArchiv.sichtbar, ...kontakteNachArchiv.imArchiv]
                      : kontakteNachArchiv.sichtbar
                    ).map((c) => zeichneKontakt(c))}
                  </div>
                )}

                {filteredContacts.length === 0 && filteredGroups.length === 0 && (
                  <p className="py-12 text-center text-xs text-on-surface-variant/70">
                    Keine Kontakte oder Gruppen gefunden.
                  </p>
                )}

                {/* Über alle Chats hinweg suchen, Markiertes und „an mich". */}
                <div className="pt-2 mt-1 border-t border-outline-variant/20 space-y-0.5">
                  <button
                    type="button"
                    onClick={() => void oeffneUeberall('markiert')}
                    className="w-full min-h-11 px-2.5 flex items-center gap-2.5 rounded-xl text-left text-xs text-on-surface-variant hover:bg-surface-container-high/60 transition-colors"
                  >
                    <Star className="w-4 h-4 text-amber-400 shrink-0" />
                    <span>Markierte Nachrichten</span>
                  </button>
                  <button
                    type="button"
                    onClick={() => void oeffneUeberall('anMich')}
                    className="w-full min-h-11 px-2.5 flex items-center gap-2.5 rounded-xl text-left text-xs text-on-surface-variant hover:bg-surface-container-high/60 transition-colors"
                  >
                    <AtSign className="w-4 h-4 text-primary shrink-0" />
                    <span>@ und Antworten an mich</span>
                  </button>
                  {searchQuery.trim().length > 1 && (
                    <button
                      type="button"
                      onClick={() => void oeffneUeberall('suche', searchQuery)}
                      className="w-full min-h-11 px-2.5 flex items-center gap-2.5 rounded-xl text-left text-xs text-primary hover:bg-surface-container-high/60 transition-colors"
                    >
                      <Search className="w-4 h-4 shrink-0" />
                      <span className="truncate">Nachrichten nach „{searchQuery.trim()}" durchsuchen</span>
                    </button>
                  )}
                </div>
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
                        key={c.listKey}
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
          {/* Höhe plus sichere Fläche, siehe die Eingabeleiste weiter unten. */}
          {!isChatOpen && (
            <nav className="md:hidden shrink-0 h-14 box-content pb-[env(safe-area-inset-bottom)] border-t border-outline-variant/20 bg-surface-container/95 backdrop-blur flex items-center justify-around px-2 z-10">
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
              {/* Die Auswahlleiste ersetzt die schwebenden Bedienelemente.
                  Zähler und Abbrechen oben, die Aktionen unten in
                  Daumenreichweite — am Telefon ist der obere Rand außer
                  Reichweite, sobald man einhändig hält. */}
              {auswahlModus && (
                <>
                  <div className="absolute top-2.5 left-3 right-3 z-40 flex items-center justify-between gap-2 px-3 py-2 rounded-full bg-surface-container-high/95 backdrop-blur-md border border-outline-variant/30 shadow-sm">
                    <span className="text-xs font-semibold text-on-surface tabular-nums">
                      {gewaehlteUuids.length} ausgewählt
                    </span>
                    <button
                      type="button"
                      onClick={beendeAuswahl}
                      className="w-9 h-9 -mr-1.5 rounded-full flex items-center justify-center text-on-surface-variant hover:bg-surface-container-highest transition-colors"
                      aria-label="Auswahl beenden"
                    >
                      <X className="w-4 h-4" />
                    </button>
                  </div>

                  <div className="absolute bottom-0 left-0 right-0 z-40 px-3 py-2 pb-[calc(0.5rem+env(safe-area-inset-bottom))] border-t border-outline-variant/30 bg-surface-container-low flex items-center justify-around">
                    <button
                      type="button"
                      disabled={gewaehlteUuids.length === 0}
                      onClick={() => {
                        const auswahl = gewaehlteNachrichten().filter(istWeiterleitbar)
                        if (!auswahl.length) {
                          toast.error('An diesen Nachrichten ist nichts weiterzuleiten.')
                          return
                        }
                        setWeiterzuleiten(auswahl)
                      }}
                      className="min-w-16 min-h-12 flex flex-col items-center justify-center gap-0.5 rounded-xl text-[10px] text-on-surface-variant hover:bg-surface-container-high disabled:opacity-40 transition-colors"
                    >
                      <Forward className="w-5 h-5" />
                      <span>Weiterleiten</span>
                    </button>
                    <button
                      type="button"
                      disabled={gewaehlteUuids.length === 0}
                      onClick={() => void handleAuswahlKopieren()}
                      className="min-w-16 min-h-12 flex flex-col items-center justify-center gap-0.5 rounded-xl text-[10px] text-on-surface-variant hover:bg-surface-container-high disabled:opacity-40 transition-colors"
                    >
                      <Copy className="w-5 h-5" />
                      <span>Kopieren</span>
                    </button>
                    <button
                      type="button"
                      disabled={gewaehlteUuids.length === 0}
                      onClick={() => {
                        for (const m of gewaehlteNachrichten()) handleMarkieren(m)
                        beendeAuswahl()
                      }}
                      className="min-w-16 min-h-12 flex flex-col items-center justify-center gap-0.5 rounded-xl text-[10px] text-on-surface-variant hover:bg-surface-container-high disabled:opacity-40 transition-colors"
                    >
                      <Star className="w-5 h-5" />
                      <span>Markieren</span>
                    </button>
                    <button
                      type="button"
                      disabled={!gewaehlteNachrichten().some((m) => m.isSelf && !m.isDeleted)}
                      onClick={() => void handleAuswahlLoeschen()}
                      className="min-w-16 min-h-12 flex flex-col items-center justify-center gap-0.5 rounded-xl text-[10px] text-destructive hover:bg-destructive/10 disabled:opacity-40 transition-colors"
                    >
                      <Trash2 className="w-5 h-5" />
                      <span>Löschen</span>
                    </button>
                  </div>
                </>
              )}

              {/* Floating Chat Controls (Header-free, maximal chat space) */}
              <div
                className={`absolute top-2.5 left-3 right-3 z-30 flex items-center justify-between pointer-events-none ${
                  auswahlModus || sucheOffen ? 'hidden' : ''
                }`}
              >
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
                    {activeGroup?.avatar_url && (
                      <img
                        src={apiUrl(activeGroup.avatar_url)}
                        alt=""
                        className="-ml-1.5 h-5 w-5 shrink-0 rounded-full object-cover"
                      />
                    )}
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

                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => handleStartGroupCall(false)}
                        disabled={!groupCallPermissions.canStart}
                        className="h-8 gap-1.5 bg-surface-container-high/85 px-2.5 text-xs text-primary shadow-xs hover:bg-surface-container-high disabled:cursor-not-allowed disabled:opacity-60"
                        title={
                          groupCallPermissions.canStart
                            ? 'Gruppenanruf starten'
                            : 'Du hast in dieser Gruppe keine Berechtigung, einen Gruppenanruf zu starten.'
                        }
                        aria-label="Gruppenanruf starten"
                      >
                        <UsersRound className="w-3.5 h-3.5" />
                        <span className="hidden sm:inline">Anruf</span>
                      </Button>

                      {(activeGroup.owner_user_id === currentUserId || activeGroup.role === 'admin') && (
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => gruppenLogoInputRef.current?.click()}
                          disabled={logoLaedt}
                          className="h-8 w-8 bg-surface-container-high/85 hover:bg-surface-container-high backdrop-blur-md border border-outline-variant/30 text-on-surface-variant hover:text-primary shadow-xs"
                          title="Gruppenlogo ändern"
                          aria-label="Gruppenlogo ändern"
                        >
                          <ImagePlus className="w-4 h-4" />
                        </Button>
                      )}

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

                  {activeContact && activeContact.isFriend && (
                    <>
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={async () => {
                          try {
                            await useCallStore.getState().initiateCall(
                              {
                                userId: activeContact.userId,
                                username: activeContact.username,
                                avatarUrl: activeContact.avatarUrl,
                              },
                              'audio',
                            )
                          } catch (err: any) {
                            toast.error(err?.message || 'Anruf konnte nicht gestartet werden.')
                          }
                        }}
                        className="h-8 w-8 bg-surface-container-high/85 hover:bg-surface-container-high backdrop-blur-md border border-outline-variant/30 text-primary hover:text-primary shadow-xs"
                        title="Sprachanruf starten"
                        aria-label="Sprachanruf starten"
                      >
                        <Phone className="w-4 h-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={async () => {
                          try {
                            await useCallStore.getState().initiateCall(
                              {
                                userId: activeContact.userId,
                                username: activeContact.username,
                                avatarUrl: activeContact.avatarUrl,
                              },
                              'video',
                            )
                          } catch (err: any) {
                            toast.error(err?.message || 'Anruf konnte nicht gestartet werden.')
                          }
                        }}
                        className="h-8 w-8 bg-surface-container-high/85 hover:bg-surface-container-high backdrop-blur-md border border-outline-variant/30 text-primary hover:text-primary shadow-xs"
                        title="Videoanruf starten"
                        aria-label="Videoanruf starten"
                      >
                        <Video className="w-4 h-4" />
                      </Button>
                    </>
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
                    onClick={() => setSucheOffen(true)}
                    className="h-8 w-8 bg-surface-container-high/85 hover:bg-surface-container-high backdrop-blur-md border border-outline-variant/30 text-on-surface-variant hover:text-primary shadow-xs"
                    title="Im Chatverlauf suchen"
                    aria-label="Im Chatverlauf suchen"
                  >
                    <Search className="w-4 h-4" />
                  </Button>

                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => setVerfallOffen(true)}
                    className={`h-8 w-8 bg-surface-container-high/85 hover:bg-surface-container-high backdrop-blur-md border border-outline-variant/30 shadow-xs ${
                      verfallSekunden > 0 ? 'text-primary' : 'text-on-surface-variant hover:text-primary'
                    }`}
                    title={
                      verfallSekunden > 0
                        ? `Nachrichten verschwinden nach ${stufenLabel(verfallSekunden)}`
                        : 'Verschwindende Nachrichten'
                    }
                    aria-label="Verschwindende Nachrichten"
                  >
                    <Timer className="w-4 h-4" />
                  </Button>

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

              {/* Die angeheftete Nachricht der Gruppe.
                  Ob sie erscheint, entscheidet das Recht des **Anheftenden** —
                  geprüft beim Empfänger, weil der Server den Inhalt nicht lesen
                  und die Regel deshalb nicht durchsetzen kann. */}
              {angeheftet && !auswahlModus && !sucheOffen && (
                <button
                  type="button"
                  onClick={() => angeheftet.clientUuid && springeZu(angeheftet.clientUuid)}
                  className="absolute top-14 left-3 right-3 z-20 min-h-11 px-3 py-2 flex items-center gap-2.5 rounded-xl bg-surface-container-high/90 backdrop-blur-md border border-outline-variant/30 shadow-xs text-left"
                >
                  <Pin className="w-3.5 h-3.5 text-primary shrink-0" />
                  <span className="min-w-0 flex-1">
                    <span className="block text-[10px] font-semibold text-primary">Angeheftet</span>
                    <span className="block text-[11px] text-on-surface-variant truncate">
                      {angeheftet.text || auszugFuerZitat(angeheftet)}
                    </span>
                  </span>
                  {darfAnheften && (
                    <span
                      role="button"
                      tabIndex={0}
                      onClick={(e) => {
                        e.stopPropagation()
                        void handleAnheften(angeheftet)
                      }}
                      onKeyDown={(e) => {
                        if (e.key !== 'Enter' && e.key !== ' ') return
                        e.preventDefault()
                        e.stopPropagation()
                        void handleAnheften(angeheftet)
                      }}
                      className="w-11 h-11 -mr-2 rounded-full flex items-center justify-center text-on-surface-variant hover:bg-surface-container-highest transition-colors shrink-0"
                      aria-label="Nicht mehr anheften"
                    >
                      <X className="w-4 h-4" />
                    </span>
                  )}
                </button>
              )}

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
                  // Eine Systemzeile ist keine Nachricht: sie hat keinen
                  // Absender, keine Quittung und kein Kontextmenü. In der
                  // Sprechblase gerendert sah sie aus, als hätte das Gegenüber
                  // sie geschrieben — bei einer Meldung über die Sicherheit
                  // dieses Gesprächs die denkbar schlechteste Verwechslung.
                  if (msg.isSystem) {
                    return (
                      <div key={msg.id} className="py-1 text-center">
                        <div className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-surface-container-high/60 border border-outline-variant/30 text-[11px] text-on-surface-variant shadow-2xs">
                          <Shield className="w-3 h-3 text-amber-400 shrink-0" />
                          <span>{msg.text}</span>
                        </div>
                      </div>
                    )
                  }

                  const currentDateBadge = formatChatDateBadge(msg.createdAt)
                  const prevDateBadge = idx > 0 ? formatChatDateBadge(messages[idx - 1].createdAt) : null
                  const showDateSeparator = Boolean(currentDateBadge && currentDateBadge !== prevDateBadge)

                  return (
                    <React.Fragment key={msg.id}>
                      {showDateSeparator && (
                        <div className="flex justify-center my-3 pointer-events-none">
                          <span className="px-3.5 py-1 rounded-full text-[11px] font-semibold bg-surface-container/90 text-on-surface-variant backdrop-blur-md border border-outline-variant/30 shadow-xs">
                            {currentDateBadge}
                          </span>
                        </div>
                      )}

                      {trennerId !== null && msg.id === trennerId && (
                        <div className="flex items-center gap-2 my-3">
                          <span className="h-px flex-1 bg-primary/30" />
                          <span className="text-[10px] font-semibold text-primary uppercase tracking-wide">
                            Neue Nachrichten
                          </span>
                          <span className="h-px flex-1 bg-primary/30" />
                        </div>
                      )}

                      <ChatMessageBubble
                        msg={msg}
                        kontext={{
                          activeGroup,
                          activeContact,
                          eigeneId: currentUserId,
                          eigenerName: user?.username || 'Ich',
                          eigenesBild: user?.avatar_url,
                          readReceiptsEnabled,
                          importedAttachmentIds,
                          // Die Rechteprüfung für `@everyone` steht hier, beim
                          // Empfänger: der Server kann den Inhalt nicht lesen
                          // und die Regel deshalb nicht durchsetzen.
                          michGemeint: binIchGemeint(msg, currentUserId, activeGroup),
                          hervorgehoben: Boolean(
                            msg.clientUuid && hervorgehoben === msg.clientUuid,
                          ),
                        }}
                        ton={{
                          playingAudioId,
                          audioCurrentTime,
                          audioPlaybackRate,
                          onTogglePlay: (id, anhang, bindung) =>
                            void togglePlayAudio(id, anhang, bindung),
                          onCycleRate: cycleAudioPlaybackRate,
                          onSeek: (id, anhang, bindung, e) =>
                            void handleWaveformSeek(id, anhang, bindung, e),
                        }}
                        aktionen={{
                          onViewImage: setViewingImage,
                          onEdit: (m) => {
                            setEditingMessage(m)
                            setInputText(m.text)
                          },
                          onDelete: (m) => void handleDeleteMessage(m),
                          onImportNote: (note, schluessel) => void handleImportNote(note, schluessel),
                          onImportCalendar: (cal, schluessel) =>
                            void handleImportCalendar(cal, schluessel),
                          onJoinByInviteCode: handleJoinByInviteCode,
                          onMenue: setMenueNachricht,
                          onAntworten: handleAntworten,
                          onReaktion: (m, emoji) => void handleReaktion(m, emoji),
                          onSpringeZu: springeZu,
                        }}
                        auswahl={{
                          aktiv: auswahlModus,
                          gewaehlt: gewaehlteUuids.includes(msg.clientUuid || `#${msg.id}`),
                          onUmschalten: handleAuswahlUmschalten,
                        }}
                        medienBindung={medienBindung}
                      />
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

              {/* Nach unten. Schwebt über der Eingabe, nicht darunter, und weicht
                  dem Zitatkopf aus, wenn beide gleichzeitig da sind. */}
              {weitOben && !sucheOffen && (
                <div className="relative z-10">
                  <button
                    type="button"
                    onClick={() => messagesEndRef.current?.scrollIntoView?.({ behavior: 'smooth' })}
                    className="absolute -top-14 right-4 w-11 h-11 rounded-full bg-surface-container-high/95 backdrop-blur-md border border-outline-variant/30 shadow-lg flex items-center justify-center text-on-surface-variant hover:text-primary transition-colors"
                    aria-label="Zum Ende springen"
                  >
                    <ArrowDown className="w-4 h-4" />
                  </button>
                </div>
              )}

              {/* Die Suche im offenen Chat. Sie sitzt über der Eingabe und
                  damit über der Tastatur: Feld, Zähler und Pfeile liegen alle
                  im Daumenbereich. Oben wären die Pfeile bei offener Tastatur
                  außer Reichweite. */}
              {sucheOffen && (
                <VerlaufSuchleiste
                  onSchliessen={() => {
                    setSucheOffen(false)
                    setSuchTreffer([])
                    setSuchIndex(0)
                  }}
                  onSuchen={handleSuchen}
                  trefferAnzahl={suchTreffer.length}
                  aktuellerTreffer={suchIndex}
                  onVor={() => blaettereTreffer(1)}
                  onZurueck={() => blaettereTreffer(-1)}
                  gesperrt={sucheGesperrt}
                />
              )}

              {/* Footer Input Area */}
              {/* Die untere Polsterung wächst um die sichere Fläche des Geräts.
                  `viewport-fit=cover` steht in der index.html, also reicht der
                  Inhalt bis an den Rand — auf einem iPhone lag die Eingabeleiste
                  damit unter dem Home-Balken, und jeder Griff dorthin wischte
                  die App weg, statt zu tippen. */}
              <div className="p-2.5 pb-[calc(0.625rem+env(safe-area-inset-bottom))] border-t border-outline-variant/20 bg-surface-container-low relative z-1">
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
                                  void handleSendMessage({ sticker: stk })
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
                        handleSendMessage({
                          text: inputText,
                          img: selectedImage || undefined,
                          file: stagedFile || undefined,
                        })
                      }}
                    >
                      {/* Hidden Image Input */}
                      <input
                        ref={fileInputRef}
                        type="file"
                        accept="image/*"
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
                        ref={chatInputRef}
                        value={inputText}
                        onChange={handleInputChange}
                        topSlot={
                          <>
                            {/* Die Vorschlagsliste beim Tippen von `@`.
                                Liegt unmittelbar über dem Feld und damit über
                                der Tastatur; jede Zeile ist 44 px hoch. */}
                            {erwaehnungsVorschlaege.length > 0 && (
                              <div className="border-b border-outline-variant/20 max-h-56 overflow-y-auto">
                                {erwaehnungsVorschlaege.map((v) => (
                                  <button
                                    key={v.userId ?? 'alle'}
                                    type="button"
                                    onMouseDown={(e) => e.preventDefault()}
                                    onClick={() => waehleErwaehnung(v)}
                                    className="w-full min-h-11 px-3 py-1.5 flex items-center gap-2.5 text-left hover:bg-surface-container-high transition-colors"
                                  >
                                    {v.istAlle ? (
                                      <span className="w-7 h-7 rounded-full bg-primary/20 flex items-center justify-center shrink-0">
                                        <Bell className="w-3.5 h-3.5 text-primary" />
                                      </span>
                                    ) : (
                                      <Avatar
                                        src={v.avatarUrl ?? null}
                                        name={v.name}
                                        size="sm"
                                        className="w-7 h-7 shrink-0"
                                      />
                                    )}
                                    <span className="min-w-0 flex-1">
                                      <span className="block text-xs text-on-surface truncate">@{v.name}</span>
                                      {v.istAlle && (
                                        <span className="block text-[10px] text-on-surface-variant">
                                          Benachrichtigt alle in dieser Gruppe
                                        </span>
                                      )}
                                    </span>
                                  </button>
                                ))}
                              </div>
                            )}

                            {/* Der Zitatkopf beim Antworten. */}
                            {antwortAuf && (
                              <div className="px-2.5 py-2 border-b border-outline-variant/20 flex items-center gap-2">
                                <span className="w-0.5 self-stretch rounded-full bg-primary shrink-0" />
                                <span className="min-w-0 flex-1">
                                  <span className="block text-[10px] font-semibold text-primary truncate">
                                    Antwort an {antwortAuf.absenderName || 'Nachricht'}
                                  </span>
                                  <span className="block text-[11px] text-on-surface-variant line-clamp-1">
                                    {antwortAuf.auszug}
                                  </span>
                                </span>
                                <button
                                  type="button"
                                  onClick={() => setAntwortAuf(null)}
                                  className="w-11 h-11 -mr-1 rounded-full flex items-center justify-center text-on-surface-variant hover:bg-surface-container-high transition-colors shrink-0"
                                  aria-label="Antwort verwerfen"
                                >
                                  <X className="w-4 h-4" />
                                </button>
                              </div>
                            )}
                          </>
                        }
                        onSubmit={() => {
                          handleSendMessage({
                            text: inputText,
                            img: selectedImage || undefined,
                            file: stagedFile || undefined,
                          })
                        }}
                        // Gesperrt heißt gesperrt: unter einer Identität, die
                        // dieses Gerät nicht öffnen kann, wird nicht gesendet.
                        // Während `loading` bleibt die Leiste offen, sonst
                        // flackerte sie bei jedem Öffnen kurz tot.
                        // Gruppenchats laufen über den Gruppenschlüssel weiter.
                        disabled={istSchreibenGesperrt}
                        placeholder={
                          istSchreibenGesperrt
                            ? // Hier stand „zuerst den Schlüssel entsperren".
                              // Das stammte aus der Zeit, als der
                              // Identitätsschlüssel eine eigene Passphrase
                              // hatte — die gibt es nicht mehr, und die
                              // Aufforderung schickte den Benutzer nach
                              // nirgendwo. Was bleibt, ist ein kurzer Moment.
                              'Schlüssel wird vorbereitet …'
                            : editingMessage
                              ? 'Nachricht bearbeiten …'
                              : 'Nachricht schreiben …'
                        }
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
                                      fileInputRef.current?.click()
                                    }}
                                    className="w-full flex items-center gap-2.5 px-3 py-2 rounded-xl text-left hover:bg-surface-container-highest/80 transition-colors group"
                                    aria-label="Foto & Bild auswählen"
                                  >
                                    <div className="w-7 h-7 rounded-lg bg-purple-500/15 text-purple-400 flex items-center justify-center shrink-0 group-hover:scale-105 transition-transform">
                                      <ImageIcon className="w-4 h-4" />
                                    </div>
                                    <div className="min-w-0 flex-1">
                                      <div className="text-xs font-semibold text-primary">Foto & Bild</div>
                                      <div className="text-[10px] text-on-surface-variant/70">Aus Galerie / Dateien</div>
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
                            <div className="flex items-center gap-1">
                              <Button
                                type="button"
                                variant="ghost"
                                size="icon"
                                onClick={() => setIsVideoNoteRecording(true)}
                                className="h-8 w-8 p-0 text-on-surface-variant hover:text-emerald-400 hover:bg-emerald-500/10 rounded-full"
                                title="Videonotiz aufnehmen (Halten/Swipe)"
                                aria-label="Videonotiz aufnehmen"
                              >
                                <Video className="w-4 h-4" />
                              </Button>
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
                            </div>
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

      {/* Das Menü zu einer einzelnen Nachricht. Portal an `document.body`,
          weil `Shell.tsx` jedes `z-50` im Inhaltsbereich auf 10 kappt. */}
      <NachrichtenMenue
        msg={menueNachricht}
        onSchliessen={() => setMenueNachricht(null)}
        onReaktion={(m, emoji) => void handleReaktion(m, emoji)}
        onAntworten={handleAntworten}
        onWeiterleiten={(m) => {
          if (!istWeiterleitbar(m)) {
            toast.error('An dieser Nachricht ist nichts weiterzuleiten.')
            return
          }
          setWeiterzuleiten([m])
        }}
        onKopieren={(m) => void handleKopieren(m)}
        onMarkieren={handleMarkieren}
        onAuswaehlen={(m) => {
          setAuswahlModus(true)
          setGewaehlteUuids([m.clientUuid || `#${m.id}`])
        }}
        onBearbeiten={(m) => {
          setEditingMessage(m)
          setInputText(m.text)
        }}
        onLoeschen={(m) => void handleDeleteMessage(m)}
        onAnheften={activeGroup ? (m) => void handleAnheften(m) : undefined}
        darfAnheften={darfAnheften}
        istAngeheftet={Boolean(angeheftet && angeheftet.clientUuid === menueNachricht?.clientUuid)}
      />

      {/* Weiterleiten und die Trefferansicht sind eigene Ansichten über der
          ganzen Seite, kein Kästchen, das bei offener Tastatur verschwindet.
          Sie stehen hier und nicht im Chat-Ast: aus der Chatliste heraus
          aufgerufen gibt es noch keinen offenen Chat, und dort hängend
          rendern sie dann gar nicht. Beide gehen per Portal an den Body. */}
      <WeiterleitenAnsicht
        offen={Boolean(weiterzuleiten)}
        onSchliessen={() => {
          setWeiterzuleiten(null)
          setWlFortschritt(null)
        }}
        ziele={weiterleitungsZiele}
        anzahlNachrichten={weiterzuleiten?.length || 0}
        fortschritt={wlFortschritt}
        onSenden={handleWeiterleiten}
      />

      <TrefferListe
        offen={ueberall !== 'aus'}
        titel={
          ueberall === 'markiert'
            ? 'Markierte Nachrichten'
            : ueberall === 'anMich'
              ? '@ und Antworten an mich'
              : `Suche: ${ueberallFrage}`
        }
        leerText={
          ueberall === 'markiert'
            ? 'Noch nichts markiert. Über das Menü einer Nachricht legst du ein Sternchen an.'
            : ueberall === 'anMich'
              ? 'Niemand hat dich erwähnt oder auf dich geantwortet.'
              : 'Kein Chat auf diesem Gerät enthält diesen Text.'
        }
        chats={ueberallChats}
        verzeichnis={mailboxDirectory}
        gesperrt={ueberallGesperrt}
        laeuft={ueberallLaeuft}
        onSchliessen={() => setUeberall('aus')}
        onTreffer={(treffer) => void oeffneTreffer(treffer)}
      />

      {/* Das Menü einer Chatzeile — derselbe Aufruf wie die Wischgeste, nur
          auffindbar. Eine Geste allein findet niemand. */}
      <Blattmenue
        offen={Boolean(zeilenMenue)}
        onSchliessen={() => setZeilenMenue(null)}
        titel={zeilenMenue?.name || 'Chat'}
      >
        <div className="px-4 pt-2 pb-1 text-xs font-semibold text-on-surface-variant truncate">
          {zeilenMenue?.name}
        </div>
        <div className="pb-2">
          <Blatteintrag
            icon={zeilenMenue && pinnedChats.includes(zeilenMenue.mid) ? <PinOff className="w-4 h-4" /> : <Pin className="w-4 h-4" />}
            label={zeilenMenue && pinnedChats.includes(zeilenMenue.mid) ? 'Nicht mehr anheften' : 'Anheften'}
            hinweis={`Bleibt auf diesem Gerät. Höchstens ${PINS_MAX} Chats.`}
            onClick={() => {
              if (zeilenMenue) handleAnheftenChat(zeilenMenue.mid)
              setZeilenMenue(null)
            }}
          />
          <Blatteintrag
            icon={zeilenMenue && archivedChats.includes(zeilenMenue.mid) ? <ArchiveRestore className="w-4 h-4" /> : <Archive className="w-4 h-4" />}
            label={zeilenMenue && archivedChats.includes(zeilenMenue.mid) ? 'Aus dem Archiv holen' : 'Archivieren'}
            onClick={() => {
              if (zeilenMenue) handleArchivieren(zeilenMenue.mid)
              setZeilenMenue(null)
            }}
          />
          <Blatteintrag
            icon={zeilenMenue && isChatMuted(zeilenMenue.mid) ? <Bell className="w-4 h-4" /> : <BellOff className="w-4 h-4" />}
            label={zeilenMenue && isChatMuted(zeilenMenue.mid) ? 'Stummschaltung aufheben' : 'Stummschalten'}
            onClick={() => {
              if (!zeilenMenue) return
              if (isChatMuted(zeilenMenue.mid)) void unmuteChat(zeilenMenue.mid)
              else void muteChat(zeilenMenue.mid)
              setZeilenMenue(null)
            }}
          />
        </div>
      </Blattmenue>

      {/* Verschwindende Nachrichten. Die Grenze steht in der Auswahl selbst,
          nicht in einer Fußnote: beim Server löschen kann nur, wer hochgeladen
          hat. */}
      <Blattmenue
        offen={verfallOffen}
        onSchliessen={() => setVerfallOffen(false)}
        titel="Verschwindende Nachrichten"
      >
        <div className="px-4 pt-2 pb-3 space-y-1">
          <p className="text-sm font-semibold text-on-surface">Verschwindende Nachrichten</p>
          <p className="text-[11px] text-on-surface-variant leading-relaxed">
            Neue Nachrichten werden nach der gewählten Zeit auf beiden Geräten gelöscht. Beide Seiten
            sehen die Umstellung als Hinweis im Verlauf. Beim Server räumt nur das Gerät auf, das die
            Nachricht gesendet hat: bleibt es dauerhaft offline, liegt der verschlüsselte Umschlag
            dort weiter, auch wenn die Nachricht auf allen Geräten verschwunden ist.
          </p>
        </div>
        <div className="pb-2">
          {VERFALL_STUFEN.map((stufe) => (
            <Blatteintrag
              key={stufe.sekunden}
              icon={
                verfallSekunden === stufe.sekunden ? (
                  <UserCheck className="w-4 h-4 text-primary" />
                ) : (
                  <Timer className="w-4 h-4" />
                )
              }
              label={stufe.label}
              onClick={() => void handleVerfallWaehlen(stufe.sekunden)}
            />
          ))}
        </div>
      </Blattmenue>

      {/* Die Wache liest Gruppen mit, die gerade nicht offen sind — sonst
          erschiene ein @-Abzeichen erst, wenn man die Gruppe ohnehin öffnet. */}
      <ErwaehnungsWache
        gruppen={groups}
        aktiveMailboxId={blindMailboxId || null}
        eigeneId={currentUserId}
        identitaetRef={identityRef}
        onErwaehnung={merkeErwaehnung}
        aktiv={!messengerGesperrt && !!currentUserId}
      />

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
                      handleSendMessage({
                        text: '',
                        note: {
                          title: n.title,
                          content: n.content,
                          color: n.color,
                          category: n.category,
                        },
                      })
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
                      handleSendMessage({
                        text: '',
                        cal: {
                          title: ev.title,
                          start: ev.start,
                          end: ev.end,
                          description: ev.description,
                          location: ev.location,
                        },
                      })
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
                key={c.listKey}
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
            void handleSendMessage({ text, storyReply: storyContext })
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

      {/* Dateiauswahl für das Gruppenlogo (der sichtbare Knopf steht im Kopf) */}
      <input
        ref={gruppenLogoInputRef}
        type="file"
        accept="image/jpeg,image/png,image/webp,image/gif"
        className="hidden"
        onChange={(e) => {
          void handleGruppenLogo(e.target.files?.[0])
          e.target.value = ''
        }}
      />

      {/* Circular Video Note Recorder (R2) */}
      {isVideoNoteRecording && (
        <CircularVideoNoteRecorder
          onCancel={() => setIsVideoNoteRecording(false)}
          onComplete={async (aufnahme: VideoNoteAufnahme) => {
            setIsVideoNoteRecording(false)
            await handleSendMessage({
              videoNote: aufnahme,
              videoUrl: URL.createObjectURL(aufnahme.blob),
            })
          }}
        />
      )}
    </div>
  )
}
