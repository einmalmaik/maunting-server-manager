import React, { useState, useEffect, useRef, useMemo, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
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
  Blattknopf,
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
import { maxAnhangBytes } from '@/services/medienKrypto'
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
import { baueVersandFuer, useKonversation, type GespraechsZiel } from '@/hooks/useKonversation'
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
import { geraeteVon, kontoNutztSignaturen, onNeuesGeraet } from '@/services/e2eeGeraet'
import { pruefeNutzlast, signiereNutzlast } from '@/services/nutzlastSignatur'
import { verwirfGruppenSchluessel } from '@/services/gruppenSchluessel'
import { ladeGruppenzustand, type Gruppenzustand } from '@/services/gruppenKonfig'
import { wirksameGruppenrechte } from '@/services/gruppenRollen'
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
  tilgeFremdeNachrichtBeimServer,
  tilgeNachrichtLokal,
} from '@/services/nachrichtLoeschen'
import {
  bezugFelder,
  istSteuerpaket,
  neueBezugstafel,
  neueSammeltafel,
} from '@/services/nachrichtBezug'
import {
  anheftung,
  durfteAnheften,
  setzeAnheftung,
  uebernehmeAnheftung,
} from '@/services/nachrichtAnheftung'
import { merkeQuittung, quittungsstand } from '@/services/quittungsstand'
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
  istBekannteStufe,
  raeumeAlleChats,
  setzeVerfallsfrist,
  stufenDativ,
  stufenLabel,
  uebernehmeVerfall,
  VERFALL_STUFEN,
  verfaelltAm as berechneVerfall,
  verfallsfrist,
  verfallStand,
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
import { ChatHintergrund, ChatHintergrundDialog } from '@/features/chatHintergrund'
import { useAuthStore } from '@/stores/authStore'
import { toast } from '@/stores/toastStore'
import { useMessengerNotificationStore, PINS_MAX } from '@/stores/messengerNotificationStore'
import { sanitizeSvg, getSafeAttachmentUrl } from '@/lib/sanitizeSvg'

function formatChatDateBadge(
  isoDateString: string,
  t: (schluessel: string) => string,
  sprache: string,
): string {
  try {
    const d = new Date(isoDateString)
    if (isNaN(d.getTime())) return ''
    const now = new Date()

    const isToday =
      d.getDate() === now.getDate() &&
      d.getMonth() === now.getMonth() &&
      d.getFullYear() === now.getFullYear()
    if (isToday) return t('messenger.today')

    const yesterday = new Date(now)
    yesterday.setDate(now.getDate() - 1)
    const isYesterday =
      d.getDate() === yesterday.getDate() &&
      d.getMonth() === yesterday.getMonth() &&
      d.getFullYear() === yesterday.getFullYear()
    if (isYesterday) return t('messenger.yesterday')

    const isSameYear = d.getFullYear() === now.getFullYear()
    // Die Sprache kommt von i18next, nicht fest aus dem Code: sonst stünde im
    // englischen Messenger ein deutsches Datum.
    return d.toLocaleDateString(sprache, {
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
  const { t, i18n } = useTranslation()

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
  /**
   * Die eigenen Rollen der offenen Gruppe, entschlüsselt.
   *
   * `null` heisst „nicht belegbar": entweder hat die Gruppe noch keinen Block,
   * oder diesem Gerät fehlt der Schlüssel, oder er war nicht beglaubigt. In
   * allen drei Fällen zählen nur die Rechte aus der Mitgliederzeile — das ist
   * der ehrliche Stand, und nicht etwa „keine Rechte".
   */
  const [gruppenRollenZustand, setGruppenRollenZustand] = useState<Gruppenzustand | null>(null)
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

  // Der Chat-Hintergrund liegt im gemeinsamen Modul (`features/chatHintergrund`):
  // die Schicht liest ihre Wahl selbst und hört auf Änderungen, hier steht nur
  // noch, ob das Einstellungsfenster offen ist.
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
  /** Das Menü hinter den drei Punkten in der Chat-Kopfzeile. */
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

  /** Ob gerade ein Versand läuft. Siehe `handleSendMessage`. */
  const sendeLaeuft = useRef(false)

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
    toast.success(t('messenger.storyPublished'))
  }

  const handleStoryDeleted = (storyId: number) => {
    setStories((prev) => prev.filter((s) => s.id !== storyId))
    toast.success(t('messenger.storyDeleted'))
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
        toast.success(t('messenger.groupJoined', { name: joinedGroup.name }))
        setActiveGroup(joinedGroup)
        setActiveContact(null)
        loadData()
        navigate('/chat', { replace: true })
      })
      .catch(() => {
        if (!active) return
        toast.error(t('messenger.inviteInvalid'))
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
      toast.error(t('messenger.logoBadType'))
      return
    }
    if (datei.size > 5 * 1024 * 1024) {
      toast.error(t('messenger.logoTooLarge'))
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
      toast.success(t('messenger.logoUpdated'))
    } catch {
      toast.error(t('messenger.logoFailed'))
    } finally {
      setLogoLaedt(false)
    }
  }

  /** Beitritt über die Einladungskarte im Chat. */
  const handleJoinByInviteCode = async (code: string) => {
    try {
      const joinedGroup = await joinGroupByInvite(code)
      toast.success(t('messenger.groupJoined', { name: joinedGroup.name }))
      setActiveGroup(joinedGroup)
      setActiveContact(null)
      await loadData()
    } catch {
      toast.error(t('messenger.inviteExpired'))
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
              username: t('social.contacts.unknownUser', { id: targetId }),
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

  /**
   * Ein anderer Chat schliesst die Vollbildansicht.
   *
   * „Markierte Nachrichten", „@ und Antworten an mich" und die Suche über alle
   * Chats liegen als `fixed inset-0` über dem Gesprächsbereich — aber **nicht**
   * über der Seitenleiste: die Hülle kappt den Stapelkontext im Inhaltsbereich
   * (siehe `Shell.tsx`). Ein Klick auf einen Chat dort wechselte also die
   * Adresse, öffnete das Gespräch und liess die Trefferliste darüber stehen.
   * Man konnte tippen und senden, ohne etwas davon zu sehen; der einzige Weg
   * zurück war der Zurück-Knopf der Ansicht. Ein Treffer selbst schliesst sie
   * schon länger (`oeffneTreffer`), der Weg über die Seitenleiste nicht.
   */
  useEffect(() => {
    setUeberall('aus')
  }, [activeContact?.userId, activeGroup?.id])

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
      text: t('messenger.sessionRebuilt'),
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

  // M-10: Überwachung des Geräteverzeichnisses — warnt bei neuen Geräten eines Gesprächspartners
  useEffect(() => {
    if (!activeContact?.userId) return
    geraeteVon(activeContact.userId).catch(() => {})
  }, [activeContact?.userId])

  useEffect(() => {
    const abbestellen = onNeuesGeraet((peerId, neue) => {
      if (neue.length === 0) return
      if (activeContact && activeContact.userId === peerId) {
        const name = activeContact.username || t('messenger.thisContact')
        zeigeSystemzeile(
          t('messenger.newDeviceDetected', {
            name,
            defaultValue: `${name} hat ein neues Gerät angemeldet.`,
          }),
        )
      } else if (activeGroup) {
        const member = (activeGroup.members ?? []).find((m) => Number(m.user_id) === peerId)
        if (member) {
          const name = member.username || t('messenger.thisContact')
          zeigeSystemzeile(
            t('messenger.newDeviceDetected', {
              name,
              defaultValue: `${name} hat ein neues Gerät angemeldet.`,
            }),
          )
        }
      }
    })
    return abbestellen
  }, [activeContact, activeGroup, t, zeigeSystemzeile])

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
    // Der eigene Quittungsstand kommt aus der Ablage, nicht von null. Sonst
    // quittiert jeder Chatwechsel dieselbe letzte Nachricht erneut. Was die
    // Gegenseite quittiert hat, wird dagegen bei jedem Abruf neu aus den
    // Umschlägen gelesen und darf hier zurückfallen.
    const stand = quittungsstand(blindMailboxId)
    highestIncomingIdAcknowledgedRef.current = stand.gelesen
    highestIncomingIdDeliveredRef.current = stand.zugestellt
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
    // Steuerpakete laufen im Direktchat über den Hybridumschlag und in der
    // Gruppe über den geteilten Schlüssel — beides ohne Absenderkopf. Ohne den
    // Beleg hier konnte die Gegenseite `actor_id` auf eine fremde Kennung
    // setzen und damit fremde Nachrichten umschreiben oder löschen.
    const payload = JSON.stringify(
      await signiereNutzlast(blindMailboxId, currentUserId, {
        actor_id: currentUserId,
        ...payloadObj,
        client_uuid: clientUuid,
      }),
    )
    const auftraege = await konversation.baueSteuerversand(
      payload,
      clientUuid,
      String(payloadObj.type || 'control'),
    )
    await Promise.all(auftraege.map((auftrag) => relayE2eeEnvelope(auftrag)))
  }

  // 5. Load and decrypt messages (non-flickering background sync + real-time)
  /**
   * Was ein Konto in der offenen Gruppe darf.
   *
   * Zwei Quellen: die Mitgliederzeile vom Server und der verschlüsselte
   * Rollenblock (`gruppenRollenZustand`). Zusammengeführt in
   * `wirksameGruppenrechte` — das ist die einzige Stelle, die diese Frage
   * beantwortet, und der Rechte-Dialog benutzt dieselbe.
   */
  const gruppenrechteVon = useCallback(
    (konto: number) => {
      if (!activeGroup) return new Set<string>()
      const mitglied = activeGroup.members?.find((m) => Number(m.user_id) === Number(konto))
      return wirksameGruppenrechte({
        konto,
        systemRolle: mitglied?.role,
        istEigentuemer: Number(activeGroup.owner_user_id) === Number(konto),
        eigeneRechte: mitglied?.permissions,
        standardrechte: activeGroup.default_permissions,
        zustand: gruppenRollenZustand,
      })
    },
    [activeGroup, gruppenRollenZustand],
  )

  /**
   * Ob ich fremde Nachrichten in dieser Gruppe entfernen darf.
   *
   * `delete_messages` stand seit je im Rechtevokabular und hatte bis 09/2026
   * keinen Konsumenten: der Menüeintrag hing an `msg.isSelf`, ein Moderator
   * konnte also nichts entfernen, egal was im Dialog gesetzt war.
   */
  const darfFremdeLoeschen =
    Boolean(activeGroup) && gruppenrechteVon(currentUserId).has('delete_messages')

  /**
   * Ob ich in dieser Gruppe schreiben und anhängen darf.
   *
   * Im Direktchat immer — dort gibt es keine Rollen. `send_messages` und
   * `attach_media` standen seit je im Vokabular, ließen sich setzen und hatten
   * keinen Konsumenten: das Eingabefeld fragte nie.
   *
   * Durchgesetzt wird das **beim Empfänger** (siehe `loadMessages`), nicht am
   * Server. Der Server kann den Inhalt nicht lesen und weiß nach Stufe 6 auch
   * nicht mehr, wer Mitglied ist; eine Schranke dort wäre eine, die wir bald
   * wieder herausreißen. Hier zu sperren ist die Höflichkeit, dort zu
   * verwerfen die Wirkung.
   */
  const darfSchreiben = !activeGroup || gruppenrechteVon(currentUserId).has('send_messages')
  const darfAnhaengen = !activeGroup || gruppenrechteVon(currentUserId).has('attach_media')

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
            text: t('messenger.encryptedMessage'),
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
            /*
             * Wer das hier geschrieben hat — einmal beantwortet, für alles was
             * folgt.
             *
             * Zwei Belege können vorliegen: die Ratchet-Sitzung (nur im
             * Direktchat, nur für Nachrichten) und die Nutzlastsignatur
             * (überall, auch für Steuerpakete). Widersprechen sie einander,
             * hat jemand an einer von beiden gedreht.
             *
             * `belegterUrheber` bleibt `undefined`, wenn keiner der beiden
             * greift. Das ist kein Freibrief: jede Auswertung unten prüft
             * zusätzlich, ob das behauptete Konto beglaubigen *könnte* — wer
             * es kann, muss es auch.
             */
            const beleg = await pruefeNutzlast(currentMid, parsed as Record<string, unknown>)
            if (beleg.art === 'gefaelscht') {
              console.warn(
                '[Messenger] Dropping payload with invalid sender signature, claimed:',
                beleg.behauptet,
              )
              continue
            }
            const ratchetUrheber = lesung.art === 'klartext' ? lesung.vonKonto : undefined
            if (
              beleg.art === 'geprueft' &&
              ratchetUrheber !== undefined &&
              Number(ratchetUrheber) !== beleg.vonKonto
            ) {
              console.warn(
                '[Messenger] Dropping payload: signature and ratchet disagree on sender',
              )
              continue
            }
            const belegterUrheber =
              beleg.art === 'geprueft' ? beleg.vonKonto : ratchetUrheber

            /**
             * Der Urheber, gegen eine Behauptung aus der Nutzlast geprüft.
             *
             * `null` heißt verwerfen: entweder widerspricht die Behauptung dem
             * Beleg, oder sie nennt ein Konto, das beglaubigen könnte und es
             * hier nicht tut — das wäre der Weg, die Prüfung einfach
             * wegzulassen.
             */
            const urheberVon = async (
              behauptetRoh: unknown,
            ): Promise<number | undefined | null> => {
              const behauptet =
                behauptetRoh === undefined || behauptetRoh === null
                  ? undefined
                  : Number(behauptetRoh)
              if (belegterUrheber !== undefined) {
                if (behauptet !== undefined && behauptet !== Number(belegterUrheber)) return null
                return Number(belegterUrheber)
              }
              if (behauptet !== undefined && behauptet > 0) {
                if (await kontoNutztSignaturen(behauptet)) return null
              }
              return behauptet
            }

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
                const urheber = await urheberVon(parsed.actor_id ?? parsed.sender_id)
                if (urheber === null) {
                  console.warn('[Messenger] Dropping edit packet with forged actor_id:', parsed.actor_id)
                  continue
                }
                aenderungen.merke(
                  parsed,
                  {
                    newText: String(parsed.new_text),
                    editedAt: String(parsed.edited_at || env.created_at),
                  },
                  urheber,
                )
              }
              continue
            }

            // 3. Delete message control packet
            if (parsed.type === 'delete_message') {
              const urheber = await urheberVon(parsed.actor_id ?? parsed.sender_id)
              if (urheber === null) {
                console.warn('[Messenger] Dropping delete packet with forged actor_id:', parsed.actor_id)
                continue
              }
              loeschungen.merke(
                parsed,
                {
                  deletedAt: String(parsed.deleted_at || env.created_at),
                },
                urheber,
              )
              continue
            }

            // 4. Reaktion auf eine Nachricht
            if (parsed.type === 'reaction') {
              const urheber = await urheberVon(parsed.actor_id)
              if (urheber === null) {
                console.warn('[Messenger] Dropping reaction with forged actor_id:', parsed.actor_id)
                continue
              }
              const zeichen = String(parsed.emoji || '')
              const wer = Number(urheber || 0)
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

            // 5. Verfallsfrist — gilt für beide Seiten, nicht nur für den,
            //    der sie eingestellt hat. Ab hier hängen auch die eigenen
            //    Nachrichten ihr `verfaellt_am` an. Wer zuletzt umstellt,
            //    gewinnt; das entscheidet `uebernehmeVerfall` am Zeitpunkt.
            //
            //    Angewandt wird sofort und nicht am Ende des Durchlaufs: unten
            //    stehen zwei Abbruchwächter für den Fall, dass inzwischen ein
            //    zweiter Abruf läuft. Der Eintrag wäre dann schon geschrieben,
            //    die Meldung darüber aber verschluckt — und `uebernehmeVerfall`
            //    meldet dieselbe Umstellung kein zweites Mal.
            if (parsed.type === 'retention') {
              const dauer = Number(parsed.dauer || 0)
              const wann = String(parsed.zeitpunkt || env.created_at)
              if (istBekannteStufe(dauer) && uebernehmeVerfall(currentMid, dauer, wann)) {
                const wer = Number(parsed.actor_id || 0)
                const selbst = wer === Number(currentUserId)
                const name = selbst
                  ? t('messenger.retentionYou')
                  : (activeGroup?.members ?? []).find((m) => Number(m.user_id) === wer)?.username ||
                    activeContact?.username ||
                    t('messenger.retentionOther')
                setVerfallSekunden(dauer)
                zeigeSystemzeile(
                  dauer > 0
                    ? t(selbst ? 'messenger.retentionSetSelf' : 'messenger.retentionSetOther', {
                        name,
                        frist: stufenDativ(dauer, t),
                      })
                    : t(selbst ? 'messenger.retentionOffSelf' : 'messenger.retentionOffOther', { name }),
                )
              }
              continue
            }

            // 6. Angeheftete Nachricht der Gruppe.
            //
            //    Die Schranke sitzt hier, beim Empfänger: der Server kann den
            //    Inhalt nicht lesen und deshalb nicht prüfen, wer anheften
            //    durfte. Ohne das Recht bleibt der Umschlag folgenlos.
            if (parsed.type === 'pin_message') {
              const urheber = await urheberVon(parsed.actor_id)
              if (urheber === null) {
                console.warn('[Messenger] Dropping pin packet with forged actor_id:', parsed.actor_id)
                continue
              }
              const wer = Number(urheber || 0)
              const ziel = String(parsed.target_client_uuid || '')
              const wann = String(parsed.zeitpunkt || env.created_at)
              const geloest = parsed.aktion === 'loesen'
              if (wer && durfteAnheften(activeGroup, wer)) {
                uebernehmeAnheftung(currentMid, geloest ? '' : ziel, wann)
              }
              continue
            }

            /**
             * Ein Steuerpaket, für das dieser Stand keinen Zweig hat.
             *
             * Etwa von einem neueren Client. Ohne diese Schranke fiele es in
             * den gewöhnlichen Weg und stünde als roher JSON-Text im Verlauf —
             * und weil der Verlauf gespeichert wird, für immer. Genau so eine
             * Zeile lag nach der ersten Laufzeitprobe im Testchat.
             */
            if (istSteuerpaket(parsed.type)) continue

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

            let senderId: number
            let senderName: string
            let isSelf: boolean
            /** Im Direktchat immer; in der Gruppe entscheidet `attach_media`. */
            let anhaengeErlaubt = true

            if (activeContact) {
              /*
               * Direktchat: die Kennung kommt aus dem Beleg — dem Ratchet oder
               * der Nutzlastsignatur —, nie aus `parsed.sender_id`.
               *
               * Fehlt jeder Beleg, ist die Gegenseite die einzig mögliche
               * Antwort: in dieser Mailbox sitzen genau zwei Menschen, und der
               * eigene Gesprächsanteil kommt aus dem lokalen Speicher, nicht
               * von hier. Die Behauptung aus der Nutzlast gewinnt also in
               * keinem der Fälle.
               */
              senderId = Number(belegterUrheber ?? activeContact.userId)
              if (parsed.sender_id !== undefined && Number(parsed.sender_id) !== senderId) {
                console.warn(
                  '[Messenger] Dropping message with forged sender_id in direct chat:',
                  parsed.sender_id,
                  'expected:',
                  senderId,
                )
                continue
              }
              isSelf = Number(senderId) === Number(currentUserId)
              senderName = isSelf ? t('messenger.you') : activeContact.username
            } else if (activeGroup) {
              /*
               * Gruppe: der Absender steht in der Nutzlastsignatur.
               *
               * Ein Gruppenschlüssel ist geteilt — jedes Mitglied kann jede
               * Nachricht der Gruppe erzeugen. `parsed.sender_id` war deshalb
               * nie eine Auskunft, sondern eine Behauptung. Anders als im
               * Direktchat gibt es hier auch keinen Rückfall: „aus dieser
               * Gruppe" sagt nichts darüber, von wem.
               *
               * Bleibt der Urheber unbelegt, weil das sendende Gerät die
               * Signatur noch nicht kennt, gilt die Zeile weiterhin — aber nur
               * solange das behauptete Konto nirgends einen Signaturschlüssel
               * führt. Diese Prüfung steckt in `urheberVon`.
               */
              const urheber = await urheberVon(parsed.sender_id)
              if (urheber === null) {
                console.warn(
                  '[Messenger] Dropping group message with forged sender_id:',
                  parsed.sender_id,
                )
                continue
              }
              senderId = Number(urheber ?? 0)

              isSelf = Number(senderId) === Number(currentUserId)

              /**
               * Durfte dieses Konto hier überhaupt schreiben?
               *
               * Hier sitzt die Durchsetzung von `send_messages` und
               * `attach_media` — nicht am Eingabefeld. Ein verändertes Programm
               * schickt trotzdem; dass es niemand **anzeigt**, ist die
               * Wirkung. Dieselbe Bauart wie bei `@everyone` und beim
               * Anheften.
               *
               * Zwei Feinheiten, die leicht verloren gehen:
               *
               * - Geprüft wird nur bei **aktuellen** Mitgliedern. Wer die
               *   Gruppe verlassen hat oder hinausgeworfen wurde, steht in
               *   keiner Rolle mehr; seine alten Nachrichten deshalb
               *   nachträglich verschwinden zu lassen, wäre Geschichtsfälschung
               *   — er durfte, als er schrieb.
               * - Verworfen wird beim **ersten Sehen**. Eine Nachricht, die
               *   schon in der Ablage steht, bleibt: `mischeVerlauf` behält
               *   lokale Zeilen. Sonst löschte das Stummschalten rückwirkend
               *   alles, was noch im Hundert-Umschläge-Fenster liegt.
               */
              const istMitglied = Boolean(
                activeGroup.members?.some((m) => Number(m.user_id) === Number(senderId)),
              )
              const senderrechte = gruppenrechteVon(senderId)
              if (!isSelf && istMitglied && !senderrechte.has('send_messages')) {
                console.warn(
                  '[Messenger] Gruppennachricht verworfen, Absender darf nicht schreiben:',
                  senderId,
                )
                continue
              }
              // Ein Anhang ohne das Recht dazu fällt weg, der Text bleibt: die
              // Nachricht ganz zu verwerfen nähme jemandem seine Worte wegen
              // eines Bildes.
              anhaengeErlaubt = isSelf || !istMitglied || senderrechte.has('attach_media')
              // Der Anzeigename kommt aus der Mitgliederliste, nie aus der
              // Nutzlast: sonst stünde unter der richtigen Kennung ein
              // fremder Name.
              const groupMember = activeGroup.members?.find((m) => Number(m.user_id) === Number(senderId))
              senderName = isSelf
                ? t('messenger.you')
                : (groupMember?.username || parsed.sender_name || parsed.sender_username || '')
            } else {
              senderId = Number(parsed.sender_id || 0)
              isSelf = Number(senderId) === Number(currentUserId)
              senderName = parsed.sender_name || parsed.sender_username || ''
            }

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
              noteAttachment: anhaengeErlaubt ? parsed.note_attachment : undefined,
              calendarAttachment: anhaengeErlaubt ? parsed.calendar_attachment : undefined,
              imageAttachment: anhaengeErlaubt ? parsed.image_attachment : undefined,
              audioAttachment: anhaengeErlaubt ? parsed.audio_attachment : undefined,
              fileAttachment: anhaengeErlaubt ? parsed.file_attachment : undefined,
              stickerAttachment: anhaengeErlaubt ? parsed.sticker_attachment : undefined,
              storyReply: anhaengeErlaubt ? parsed.story_reply : undefined,
              videoNoteAttachment: anhaengeErlaubt ? parsed.video_note_attachment : undefined,
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

      const findeAenderung = (m: ChatMessage) =>
        aenderungen.finde(m, (urheber) => urheber !== undefined && Number(urheber) === Number(m.senderId))

      /**
       * Wessen Löschbefehl befolgt wird.
       *
       * Der eigene Absender immer. In einer Gruppe zusätzlich, wer
       * `delete_messages` trägt — und das ist seit 09/2026 auch wirklich dieses
       * Recht. Vorher wurde hier `can_pin_messages` geprüft, also das Recht,
       * eine Nachricht **anzuheften**: ein Moderator mit Löschrecht und ohne
       * Heftrecht wurde ignoriert, einer mit Heftrecht und ohne Löschrecht kam
       * durch. Die Rechtelage kommt jetzt aus derselben Stelle wie im
       * Rechte-Dialog, inklusive der Rollen aus dem verschlüsselten Block.
       *
       * Die ehrliche Grenze steht hier und nicht im Werbetext: Moderation unter
       * Ende-zu-Ende-Verschlüsselung ist eine Bitte, die Clients befolgen —
       * keine Tatsache, die der Server durchsetzt. Wer die Nachricht schon
       * gelesen hat, behält sie. Das gilt für Signal und WhatsApp genauso.
       */
      const findeLoeschung = (m: ChatMessage) =>
        loeschungen.finde(m, (urheber) => {
          if (urheber === undefined) return false
          if (Number(urheber) === Number(m.senderId)) return true
          if (!activeGroup) return false
          return gruppenrechteVon(Number(urheber)).has('delete_messages')
        })

      /**
       * Die Wirkungen dieses Durchlaufs auf eine Zeile, die es schon gibt.
       *
       * Gebraucht an **zwei** Stellen, und genau darin lag ein Fehler: der
       * lokale Verlauf wendete Löschung und Reaktionen an, die noch
       * unbestätigten Zeilen aus dem React-Zustand dagegen gar nichts.
       * `mischeVerlauf` lässt bei zwei optimistischen Fassungen die aus dem
       * Zustand gewinnen — die gerade berechnete Reaktion wurde damit wieder
       * überschrieben und so weggespeichert. Am laufenden System: eine
       * Nachricht, die keine Reaktion mehr annahm, für immer.
       *
       * Bearbeitungen fehlten hier ganz. Sie standen allein im Zweig für frisch
       * entschlüsselte Umschläge, und der kennt nur, was noch im
       * Hundert-Umschläge-Fenster liegt. Alles Ältere blieb beim Empfänger
       * unverändert stehen, auch nach dem Neuladen.
       */
      const wendeWirkungenAn = (m: ChatMessage): ChatMessage => {
        const loeschung = findeLoeschung(m)
        if (loeschung && !m.isDeleted) return tilgeInhalt(m, loeschung.deletedAt) as ChatMessage

        let zeile = m
        const aenderung = findeAenderung(zeile)
        if (aenderung && zeile.text !== aenderung.newText) {
          zeile = {
            ...zeile,
            originalText: zeile.text,
            text: aenderung.newText,
            isEdited: true,
            editedAt: aenderung.editedAt,
          }
        }

        const neue = wendeReaktionenAn(zeile.reaktionen, reaktionen.finde(zeile))
        return neue === zeile.reaktionen ? zeile : { ...zeile, reaktionen: neue }
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
      const lokalerVerlauf = rohesLokal.map(wendeWirkungenAn)

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
            // Dieselben Wirkungen wie im lokalen Verlauf. Ohne sie brächte
            // diese Fassung eine Reaktion oder Bearbeitung weniger mit und
            // würde die berechnete beim Mischen wieder verdrängen.
            const zeile = wendeWirkungenAn(m)
            const isRead = zeile.isSelf && maxPartnerReadId >= zeile.id
            const isDelivered = zeile.isSelf && (isRead || maxPartnerDeliveredId >= zeile.id)
            return {
              ...zeile,
              isRead,
              isDelivered,
              status: isRead ? ('read' as const) : isDelivered ? ('delivered' as const) : zeile.status || ('queued' as const),
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

      /**
       * Die Leiste wird aus der Ablage abgeglichen, nicht aus dem Umschlag.
       *
       * Die Kennung überlebt das Hundert-Umschläge-Fenster, der Anheft-Umschlag
       * nicht. Deshalb ist die Ablage die Wahrheit und die Leiste nur ihre
       * Anzeige — das deckt beides ab: eine frisch übernommene Anheftung und
       * eine, die schon vor dem Öffnen des Chats galt. Nach dem `setMessages`,
       * denn im Updater wäre es ein Seiteneffekt mitten in der Berechnung.
       */
      const gemerkt = anheftung(currentMid).clientUuid
      setAngeheftet((bisher) => {
        if ((bisher?.clientUuid || '') === gemerkt) return bisher
        if (!gemerkt) return null
        // Noch nicht im Verlauf: keine Leiste. Der nächste Durchgang holt sie
        // nach, sobald die Zeile da ist — eine falsche wäre schlimmer als keine.
        return [...processedList, ...lokalerVerlauf].find((m) => m.clientUuid === gemerkt) || null
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
        /*
         * Der Stand steigt **vor** dem Versand, nicht danach.
         *
         * Bis 09/2026 wanderte er erst im `.then()` hoch. Jeder gescheiterte
         * Versand ließ ihn stehen, und der nächste Abruf ein paar Sekunden
         * später schickte dieselbe Quittung noch einmal. Bremste der Server
         * mit 429, hielt sich das von selbst am Leben: gemessen waren 64 % der
         * Umschläge in der Mailbox Zustellquittungen, und das Fenster fasst
         * hundert — verdrängt wurden Bearbeitungen und Reaktionen.
         *
         * Ein Verlust kostet nichts: `delivered_up_to_id` nennt eine
         * Obergrenze. Die nächste Nachricht bringt eine höhere Kennung und
         * damit eine Quittung, die den Bereich mit abdeckt.
         */
        highestIncomingIdDeliveredRef.current = Math.max(
          highestIncomingIdDeliveredRef.current,
          idToDeliver,
        )
        merkeQuittung(currentMid, 'zugestellt', idToDeliver)
        sendE2eeControlMessage({
          type: 'delivery_receipt',
          delivered_up_to_id: idToDeliver,
          receiver_id: currentUserId,
          timestamp: new Date().toISOString(),
        }).catch(() => {})
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
        // Derselbe Grund wie bei der Zustellquittung: kumulativ, also lieber
        // einmal zu wenig als in jeder Runde erneut.
        highestIncomingIdAcknowledgedRef.current = Math.max(
          highestIncomingIdAcknowledgedRef.current,
          idToAck,
        )
        merkeQuittung(currentMid, 'gelesen', idToAck)
        const dispatchReadReceipt = () => {
          sendE2eeControlMessage({
            type: 'read_receipt',
            read_up_to_id: idToAck,
            reader_id: currentUserId,
            timestamp: new Date().toISOString(),
          }).catch(() => {})
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
      toast.error(t('messenger.reactionFailed'))
    }
  }

  /** Antworten: den Zitatkopf über die Eingabe setzen und dorthin springen. */
  const handleAntworten = (msg: ChatMessage) => {
    if (!msg.clientUuid) {
      // Ohne logische Kennung gäbe es nichts, worauf das Zitat zeigen könnte.
      toast.error(t('messenger.replyImpossible'))
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
      toast.success(t('messenger.textCopied'))
    } catch {
      toast.error(t('messenger.copyRefused'))
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
      toast.error(t('messenger.messageGoneLocally'))
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
      toast.error(t('messenger.selectionHasNoText'))
      return
    }
    try {
      await navigator.clipboard.writeText(text)
      toast.success(t('messenger.textCopied'))
      beendeAuswahl()
    } catch {
      toast.error(t('messenger.copyRefused'))
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
    if (gescheitert) toast.error(t('messenger.forwardFailed', { count: gescheitert }))
    else toast.success(t('messenger.forwarded', { count: ziele.length }))
  }

  /**
   * Den verschlüsselten Rollenblock der offenen Gruppe holen.
   *
   * Er entscheidet mit, ob ein fremder Löschbefehl befolgt wird — also muss er
   * hier liegen und nicht nur im Rechte-Dialog. Wer ihn schreiben durfte, wird
   * gegen die **Mitgliederzeile** geprüft und nicht gegen den Block selbst:
   * ein Block, der seine eigene Befugnis bescheinigt, bescheinigt nichts.
   */
  useEffect(() => {
    if (!activeGroup || !blindMailboxId) {
      setGruppenRollenZustand(null)
      return
    }
    let abgebrochen = false
    const mitglieder = activeGroup.members ?? []
    const darfSchreiben = (konto: number) => {
      if (Number(activeGroup.owner_user_id) === Number(konto)) return true
      const m = mitglieder.find((x) => Number(x.user_id) === Number(konto))
      if (!m) return false
      if (m.role === 'owner' || m.role === 'admin') return true
      return (m.permissions || '')
        .split(',')
        .map((p) => p.trim())
        .includes('manage_roles')
    }

    void ladeGruppenzustand(
      {
        groupId: activeGroup.id,
        blindMailboxId,
        eigeneId: currentUserId,
        mitglieder: mitglieder.map((m) => m.user_id),
      },
      darfSchreiben,
    )
      .then((lesung) => {
        if (abgebrochen) return
        setGruppenRollenZustand(lesung.art === 'zustand' ? lesung.zustand : null)
      })
      .catch(() => {
        if (!abgebrochen) setGruppenRollenZustand(null)
      })

    return () => {
      abgebrochen = true
    }
  }, [activeGroup?.id, activeGroup?.members, blindMailboxId, currentUserId])

  /**
   * Die offene Gruppe frisch halten.
   *
   * `activeGroup` war eine Momentaufnahme vom Öffnen des Chats und wurde nie
   * wieder angefasst — die Liste daneben aktualisierte sich im Takt, dieser
   * eine Eintrag nicht. Solange daran nur der Name hing, fiel es niemandem
   * auf. Seit die **Rechte** daran hängen, ist es eine Sicherheitsfrage: ein
   * entzogenes Schreibrecht wirkte erst, wenn der Betroffene den Chat von
   * Hand neu öffnete, und ein frisch vergebenes ebenso wenig.
   *
   * Verglichen wird nur, was Rechte trägt. Raumzeichen und Anrufzustand
   * ändern sich im Sekundentakt; darauf zu reagieren hiesse, die Ansicht
   * ständig neu zu setzen, ohne dass sich etwas geändert hätte.
   */
  useEffect(() => {
    if (!activeGroup) return
    const frisch = groups.find((g) => g.id === activeGroup.id)
    if (!frisch) return
    const rechtekennung = (g: ChatGroupItem) =>
      JSON.stringify([
        g.default_permissions ?? null,
        g.invite_code ?? null,
        g.role ?? null,
        g.owner_user_id,
        g.can_pin_messages ?? null,
        g.can_mention_everyone ?? null,
        (g.members ?? []).map((m) => [m.user_id, m.role, m.permissions ?? null]),
      ])
    if (rechtekennung(frisch) !== rechtekennung(activeGroup)) setActiveGroup(frisch)
  }, [groups, activeGroup])

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
   * Die Verfallsfrist dieses Chats umstellen.
   *
   * Keine heimliche Änderung: der Umschlag geht an die Gegenseite, sie übernimmt
   * die Frist und bekommt dieselbe Systemzeile. Scheitert das Senden, bleibt die
   * alte Frist stehen — eine Frist, die nur hier gilt, wäre eine Lüge über das,
   * was beim Gegenüber passiert.
   *
   * Lokal und im Umschlag steht **derselbe** Zeitpunkt. Mit zwei knapp
   * verschiedenen käme der eigene Umschlag beim nächsten Abruf als „neuer" zurück
   * und schriebe eine zweite Systemzeile.
   */
  const handleVerfallWaehlen = async (sekunden: number) => {
    if (!blindMailboxId || sekunden === verfallSekunden) {
      setVerfallOffen(false)
      return
    }
    const vorher = verfallStand(blindMailboxId)
    const jetzt = new Date().toISOString()
    setVerfallSekunden(sekunden)
    setzeVerfallsfrist(blindMailboxId, sekunden, jetzt)
    setVerfallOffen(false)
    try {
      await sendE2eeControlMessage({
        type: 'retention',
        dauer: sekunden,
        actor_id: currentUserId,
        zeitpunkt: jetzt,
      })
      zeigeSystemzeile(
        sekunden > 0
          ? t('messenger.retentionSetSelf', {
              name: t('messenger.retentionYou'),
              frist: stufenDativ(sekunden, t),
            })
          : t('messenger.retentionOffSelf', { name: t('messenger.retentionYou') }),
      )
    } catch {
      setVerfallSekunden(vorher.sekunden)
      setzeVerfallsfrist(blindMailboxId, vorher.sekunden, vorher.stand || jetzt)
      toast.error(t('messenger.retentionChangeFailed'))
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
      if (entwurfUhr.current) clearTimeout(entwurfUhr.current)
      entwurfOffen.current = null
      void speichereEntwurf(blindMailboxId, '').catch(() => {})
      setEntwuerfe((v) => {
        if (!v[blindMailboxId]) return v
        const neu = { ...v }
        delete neu[blindMailboxId]
        return neu
      })
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
      if (entwurfUhr.current) clearTimeout(entwurfUhr.current)
      entwurfOffen.current = null
      void speichereEntwurf(blindMailboxId, '').catch(() => {})
      setEntwuerfe((v) => {
        if (!v[blindMailboxId]) return v
        const neu = { ...v }
        delete neu[blindMailboxId]
        return neu
      })
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

  /**
   * Derselbe Durchgang über die Chats, die gerade nicht offen sind.
   *
   * Der Takt oben sieht nur den offenen Chat. Einen Chat, den man nie wieder
   * öffnet, würde er nie aufräumen — die Nachricht wäre „nach 24 Stunden weg"
   * und läge weiter auf der Platte.
   */
  useEffect(() => {
    if (messengerGesperrt) return
    void raeumeAlleChats().catch(() => {})
    const takt = window.setInterval(() => void raeumeAlleChats().catch(() => {}), 300_000)
    return () => window.clearInterval(takt)
  }, [messengerGesperrt])

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
    const stand = quittungsstand(blindMailboxId)
    highestIncomingIdAcknowledgedRef.current = stand.gelesen
    highestIncomingIdDeliveredRef.current = stand.zugestellt
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

  /**
   * Beim Öffnen eines Chats steht man unten, bei der letzten Nachricht.
   *
   * Das klang einfacher, als es war. Ein weiches `scrollIntoView` beim Wechsel
   * zielt auf ein Ende, das sich noch verschiebt: erst steht der lokale
   * Verlauf, dann kommen die Umschläge vom Server, dann laden die Bilder und
   * machen die Blasen höher. Die Animation läuft ins Leere und bleibt irgendwo
   * in der Mitte stehen.
   *
   * Deshalb wird nach einem Wechsel eine knappe Sekunde lang bei jedem Bild
   * hart ans Ende gesetzt — ohne Animation, weil ein Sprung über tausend
   * Nachrichten niemandem hilft. Rührt der Nutzer das Rad oder den Finger an,
   * hört das sofort auf: ab da scrollt nur noch er.
   */
  const ansEndeRef = useRef(false)

  useEffect(() => {
    const container = scrollContainerRef.current
    if (!container || !blindMailboxId) return

    ansEndeRef.current = true
    let abbruch = false
    const bis = Date.now() + 1200
    const ziehen = () => {
      if (abbruch || !ansEndeRef.current || !scrollContainerRef.current) return
      const c = scrollContainerRef.current
      c.scrollTop = c.scrollHeight
      if (Date.now() < bis) requestAnimationFrame(ziehen)
      else ansEndeRef.current = false
    }
    requestAnimationFrame(ziehen)

    // Eine echte Nutzergeste beendet das Nachziehen. Auf das `scroll`-Ereignis
    // zu hören ginge nicht: das löst das Nachziehen selbst aus.
    const losIassen = () => {
      ansEndeRef.current = false
    }
    container.addEventListener('wheel', losIassen, { passive: true })
    container.addEventListener('touchstart', losIassen, { passive: true })

    return () => {
      abbruch = true
      container.removeEventListener('wheel', losIassen)
      container.removeEventListener('touchstart', losIassen)
    }
  }, [blindMailboxId])

  // Autoscroll bei neuen Nachrichten im offenen Chat.
  useEffect(() => {
    const container = scrollContainerRef.current
    if (!container) return
    if (ansEndeRef.current) {
      container.scrollTop = container.scrollHeight
      return
    }
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

    // Die Rechtelage der Gruppe, bevor irgendetwas verschlüsselt wird. Das
    // Eingabefeld ist bereits gesperrt; dies fängt die anderen Wege ab —
    // Weiterleiten, Sprachnachricht, Videonotiz, Sticker.
    if (!darfSchreiben) {
      toast.error(t('messenger.sendNoRight'))
      return
    }
    if (
      !darfAnhaengen &&
      (note || cal || img || audio || file || sticker || storyReply || videoNote)
    ) {
      toast.error(t('messenger.attachNoRight'))
      return
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
      return
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
    if (sendeLaeuft.current) return
    sendeLaeuft.current = true

    /**
     * Wohin diese Nachricht geht.
     *
     * Beim Weiterleiten ist das ein **anderes** Gespräch als das offene. Bis
     * 09/2026 stand hier stur `blindMailboxId`, und jede Weiterleitung landete
     * still im gerade geöffneten Chat statt beim gewählten Empfänger.
     */
    const fremdesZiel = auftrag.ziel ?? null
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
      isDelivered: false,
      isRead: false,
      status: 'queued',
    }

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
    }
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
        await ablageBereit
        await updateMessageInLocalStore(targetBlindMailboxId, clientUuid, nachtrag).catch(() => {})
      }

      if (note) payloadObj.note_attachment = note
      if (cal) payloadObj.calendar_attachment = cal
      if (finalImg) payloadObj.image_attachment = finalImg
      if (finalAudio) payloadObj.audio_attachment = finalAudio
      if (finalFile) payloadObj.file_attachment = finalFile
      if (sticker) payloadObj.sticker_attachment = sticker
      if (storyReply) payloadObj.story_reply = storyReply
      if (finalVideoNote) payloadObj.video_note_attachment = finalVideoNote

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

      if (verbindungsfehler && niedrigsteId === 0) {
        toast.info(t('messenger.queuedOffline'))
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
        // Beides gewartet, und in dieser Reihenfolge: die Zeile muss liegen,
        // bevor sie nachgezogen wird, und nachgezogen sein, bevor
        // `loadMessages` sie liest und wieder wegschreibt.
        await ablageBereit
        await updateMessageInLocalStore(targetBlindMailboxId, clientUuid, {
          id: serverId,
          status,
          isRead,
          isDelivered,
        }).catch(() => {})
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
          toast.error(t('messenger.deviceKeyNotReadyRetry'))
        } else if (err instanceof DrZustellungFehlgeschlagenError) {
          // Die Gegenstelle ist angemeldet, das Verschlüsseln hat versagt. Der
          // nächste Versuch setzt die Sitzung neu auf, deshalb der Hinweis auf
          // das Wiederholen statt einer Aussage über den Kontakt.
          toast.error(t('messenger.encryptFailed'))
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
  }

  // Voice recording handlers
  const startRecording = async () => {
    if (typeof navigator === 'undefined' || !navigator.mediaDevices?.getUserMedia) {
      toast.error(t('messenger.micUnavailable'))
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
      toast.error(t('messenger.micDenied'))
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
        toast.error(t('messenger.voiceLoadFailed'))
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
        toast.error(t('messenger.voicePlayFailed'))
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
        toast.error(t('messenger.voiceLoadFailed'))
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
        toast.error(t('messenger.voicePlayFailed'))
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
      toast.error(t('messenger.pickValidImage'))
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
      toast.error(t('messenger.notesLoadFailed'))
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
      toast.error(t('messenger.calendarLoadFailed'))
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
      toast.success(t('messenger.groupCreated', { name: newGroup.name }))
      setIsCreateGroupOpen(false)
      setGroupName('')
      setGroupDesc('')
      await loadData()
      setActiveGroup(newGroup)
      setActiveContact(null)
    } catch {
      toast.error(t('messenger.groupCreateFailed'))
    } finally {
      setCreatingGroup(false)
    }
  }

  /**
   * Den Einladungslink kopieren — wenn es einen gibt.
   *
   * `invite_code` ist `null`, sobald das Backend dieses Mitglied nicht als
   * einladungsberechtigt ansieht. Das ist die eigentliche Durchsetzung von
   * `invite_members`: wer den Code nicht bekommt, kann ihn nicht weitergeben.
   * Der Knopf erscheint dann gar nicht erst; diese Zeile fängt den Fall ab,
   * dass eine Liste noch aus einem älteren Abruf stammt.
   */
  const handleCopyInviteLink = (group: ChatGroupItem) => {
    if (!group.invite_code) {
      toast.error(t('messenger.inviteNoRight'))
      return
    }
    const url = `${window.location.origin}/chat/join/${group.invite_code}`
    navigator.clipboard.writeText(url)
    toast.success(t('messenger.inviteCopied'))
  }

  // Leave Group
  const handleLeaveGroup = async (group: ChatGroupItem) => {
    try {
      await leaveGroup(group.id)
      // Wer draußen ist, braucht die Schlüssel nicht mehr — und soll sie auch
      // nicht behalten. Der Verlauf dieser Gruppe wird damit unlesbar, was
      // genau die Zusage ist, die ein Austritt geben soll.
      await verwirfGruppenSchluessel(group.id).catch(() => {})
      toast.success(t('messenger.groupLeft', { name: group.name }))
      setActiveGroup(null)
      await loadData()
    } catch {
      toast.error(t('messenger.groupLeaveFailed'))
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
      toast.success(t('messenger.groupDeleted', { name: groupToDelete.name }))
      setActiveGroup(null)
      setGroupToDelete(null)
      await loadData()
    } catch {
      toast.error(t('messenger.groupDeleteFailed'))
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
    const MAX_FILE_BYTES = maxAnhangBytes()
    const MAX_IMAGE_BYTES = Math.min(8 * 1024 * 1024, MAX_FILE_BYTES)

    const isImage = file.type.startsWith('image/')
    const limit = isImage ? MAX_IMAGE_BYTES : MAX_FILE_BYTES
    if (file.size > limit) {
      toast.error(t('messenger.fileTooLarge', { limit: Math.floor(limit / (1024 * 1024)) }))
      return
    }

    // 2. Blockiere ausfuehrbare Dateien clientseitig vorab
    const lowerName = file.name.toLowerCase()
    const blockedExtensions = ['.exe', '.dll', '.bat', '.cmd', '.sh', '.msi', '.vbs', '.ps1', '.elf', '.com', '.scr', '.pif']
    if (blockedExtensions.some((ext) => lowerName.endsWith(ext))) {
      toast.error(t('messenger.executableBlocked'))
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
      toast.success(t('messenger.noteAlreadyTaken'))
      return
    }
    try {
      await saveNoteOffline({
        title: note.title || t('messenger.sharedNote'),
        content: note.content || '',
        category: note.category || 'personal',
        color: note.color || 'primary',
        is_pinned: false,
        note_type: 'personal',
        team_id: null,
      })
      setImportedAttachmentIds((prev) => new Set([...prev, key]))
      toast.success(t('messenger.noteSaved', { title: note.title || t('messenger.sharedNote') }))
    } catch {
      toast.error(t('messenger.noteSaveFailed'))
    }
  }

  const handleImportCalendar = async (cal: CalendarAttachment, itemKey?: string) => {
    const key = itemKey || `${cal.title}_${cal.start}`
    if (importedAttachmentIds.has(key)) {
      toast.success(t('messenger.eventAlreadyTaken'))
      return
    }
    try {
      await saveCalendarEventOffline({
        title: cal.title || t('messenger.sharedEvent'),
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
      toast.success(t('messenger.eventSaved', { title: cal.title || t('messenger.sharedEvent') }))
    } catch {
      toast.error(t('messenger.eventSaveFailed'))
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
          ? t('messenger.noJoinCallRight')
          : t('messenger.noStartCallRight')
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
          toast.error(t('messenger.noCallRoom'))
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
          ? t('messenger.callOpenFailed')
          : t('messenger.callStartFailed')
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
      toast.error(t('messenger.pinLimit', { count: PINS_MAX }))
      return
    }
    setWiderruf({
      text: war ? t('messenger.unpinned') : t('messenger.pinned'),
      zurueck: () => {
        schalteAnheften(mid)
      },
    })
  }

  const handleArchivieren = (mid: string) => {
    const war = archivedChats.includes(mid)
    schalteArchiv(mid)
    setWiderruf({
      text: war ? t('messenger.unarchived') : t('messenger.archived'),
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
            title={t('messenger.youWereMentioned')}
            className="inline-flex items-center justify-center w-[18px] h-[18px] rounded-full bg-primary/20 text-primary"
          >
            <AtSign className="w-3 h-3" />
          </span>
        )}
        {unread > 0 && (
          <span className="inline-flex items-center justify-center px-1.5 py-0.5 text-label-sm font-bold rounded-full bg-primary text-on-primary min-w-[18px]">
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
      <p className="text-label-sm truncate">
        <span className="text-status-warning font-semibold">{t('messenger.draftPrefix')} </span>
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
              ? 'bg-primary/15 border border-primary/30 shadow-sm'
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
                <span className="text-label-sm text-on-surface-variant/60 shrink-0">{g.member_count} M.</span>
              </div>
              {zeilenVorschau(
                gmid,
                <p className="text-label-sm text-on-surface-variant/80 truncate">
                  {g.description || t('messenger.encryptedGroup')}
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
              ? 'bg-primary/15 border border-primary/30 shadow-sm'
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
                  <span className="text-label-sm px-1 rounded bg-status-destructive/15 text-status-destructive font-medium">
                    Blockiert
                  </span>
                )}
                {c.isPublicUser && !c.isFriend && !c.teamName && !isUserBlocked && (
                  <span className="text-label-sm px-1.5 py-0.2 rounded-md bg-primary/10 text-primary font-medium flex items-center gap-0.5">
                    <Globe className="w-2.5 h-2.5" />
                    <span>{t('messenger.public')}</span>
                  </span>
                )}
                <DeviceBadge deviceType={c.deviceType} />
              </div>
              {zeilenVorschau(
                cmid,
                <>
                  {c.teamName && (
                    <p className="text-label-sm text-tertiary truncate flex items-center gap-1">
                      <UsersRound className="w-2.5 h-2.5" />
                      <span>{c.teamName}</span>
                    </p>
                  )}
                  {c.isPublicUser && !c.isFriend && !c.teamName && (
                    <p className="text-label-sm text-on-surface-variant/70 truncate">{t('messenger.e2eeReady')}</p>
                  )}
                  {c.activityLabel && !c.teamName && (
                    <p className="text-label-sm text-on-surface-variant/80 truncate">{c.activityLabel}</p>
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
            <span className="text-label-sm text-on-surface-variant/60 hidden sm:inline">{t('messenger.headerSubtitle')}</span>
          </div>

          <div className="flex items-center gap-1">
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setIsCameraModalOpen(true)}
              className="h-8 w-8 text-on-surface-variant hover:text-primary"
              title={t('social.camera.take')}
              aria-label={t('social.camera.take')}
            >
              <Camera className="w-4 h-4" />
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => handleStartGroupCall(true)}
              disabled={!groupCallPermissions.canJoin}
              className="h-8 gap-1.5 bg-surface-container-high/85 px-2.5 text-xs text-primary shadow-sm hover:bg-surface-container-high disabled:cursor-not-allowed disabled:opacity-60"
              title={
                groupCallPermissions.canJoin
                  ? t('messenger.joinOngoingCall')
                  : t('messenger.noJoinCallRight')
              }
              aria-label={t('messenger.joinOngoingCall')}
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
                  ? 'bg-primary text-on-primary shadow-sm font-semibold'
                  : 'text-on-surface-variant hover:text-on-surface hover:bg-surface-container-high/60'
              }`}
              aria-label={t('messenger.nav.chats')}
            >
              <MessageSquare className="w-3.5 h-3.5" />
              <span>{t('messenger.nav.chats')}</span>
            </button>
            <button
              type="button"
              onClick={() => setMobileNavTab('updates')}
              className={`flex-1 py-1.5 px-2 rounded-lg text-xs font-medium flex items-center justify-center gap-1.5 transition-colors relative ${
                mobileNavTab === 'updates'
                  ? 'bg-primary text-on-primary shadow-sm font-semibold'
                  : 'text-on-surface-variant hover:text-on-surface hover:bg-surface-container-high/60'
              }`}
              aria-label={t('messenger.nav.updates')}
            >
              <Sparkles className="w-3.5 h-3.5" />
              <span>{t('messenger.nav.updates')}</span>
              {stories.length > 0 && (
                <span className={`w-2 h-2 rounded-full ${mobileNavTab === 'updates' ? 'bg-white' : 'bg-status-success animate-pulse'}`} />
              )}
            </button>
            <button
              type="button"
              onClick={() => setMobileNavTab('community')}
              className={`flex-1 py-1.5 px-2 rounded-lg text-xs font-medium flex items-center justify-center gap-1.5 transition-colors ${
                mobileNavTab === 'community'
                  ? 'bg-primary text-on-primary shadow-sm font-semibold'
                  : 'text-on-surface-variant hover:text-on-surface hover:bg-surface-container-high/60'
              }`}
              aria-label={t('messenger.nav.community')}
            >
              <UsersRound className="w-3.5 h-3.5" />
              <span>{t('messenger.nav.community')}</span>
            </button>
          </div>

          {/* Top Search & Category Tabs */}
          <div className="p-2.5 border-b border-outline-variant/15 space-y-2 bg-surface-container/40">
            <div className="relative">
              <Search className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-on-surface-variant/60" />
              <Input
                value={searchQuery}
                onChange={(e: React.ChangeEvent<HTMLInputElement>) => setSearchQuery(e.target.value)}
                placeholder={t('messenger.searchPlaceholder')}
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
                      ? 'bg-primary text-on-primary shadow-sm'
                      : 'text-on-surface-variant hover:text-on-surface hover:bg-surface-container-high/70'
                  }`}
                  title={t('messenger.filterAll')}
                  aria-label={t('messenger.filterAll')}
                >
                  <LayoutGrid className="w-3.5 h-3.5 shrink-0" />
                  <span className="text-label-sm leading-none hidden xs:inline">Alle</span>
                </button>

                <button
                  type="button"
                  onClick={() => setFilterTab('groups')}
                  className={`h-7 rounded-lg flex items-center justify-center gap-0.5 sm:gap-1 transition-all text-xs font-semibold ${
                    filterTab === 'groups'
                      ? 'bg-primary text-on-primary shadow-sm'
                      : 'text-on-surface-variant hover:text-on-surface hover:bg-surface-container-high/70'
                  }`}
                  title={t('messenger.filterGroups', { count: groups.length })}
                  aria-label={t('messenger.filterGroups', { count: groups.length })}
                >
                  <UsersRound className="w-3.5 h-3.5 shrink-0" />
                  {groups.length > 0 && (
                    <span
                      className={`text-label-sm px-1 py-0.2 rounded-full font-bold leading-none ${
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
                      ? 'bg-primary text-on-primary shadow-sm'
                      : 'text-on-surface-variant hover:text-on-surface hover:bg-surface-container-high/70'
                  }`}
                  title={t('messenger.filterFriends', { count: contactsList.filter((c) => c.isFriend).length })}
                  aria-label={t('messenger.filterFriends', { count: contactsList.filter((c) => c.isFriend).length })}
                >
                  <UserCheck className="w-3.5 h-3.5 shrink-0" />
                  {contactsList.some((c) => c.isFriend) && (
                    <span
                      className={`text-label-sm px-1 py-0.2 rounded-full font-bold leading-none ${
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
                      ? 'bg-primary text-on-primary shadow-sm'
                      : 'text-on-surface-variant hover:text-on-surface hover:bg-surface-container-high/70'
                  }`}
                  title={t('messenger.filterTeams', { count: contactsList.filter((c) => c.teamName).length })}
                  aria-label={t('messenger.filterTeams', { count: contactsList.filter((c) => c.teamName).length })}
                >
                  <Briefcase className="w-3.5 h-3.5 shrink-0" />
                  {contactsList.some((c) => c.teamName) && (
                    <span
                      className={`text-label-sm px-1 py-0.2 rounded-full font-bold leading-none ${
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
                      ? 'bg-primary text-on-primary shadow-sm'
                      : 'text-on-surface-variant hover:text-on-surface hover:bg-surface-container-high/70'
                  }`}
                  title={t('messenger.filterPublic', { count: contactsList.filter((c) => c.isPublicUser).length })}
                  aria-label={t('messenger.filterPublic', { count: contactsList.filter((c) => c.isPublicUser).length })}
                >
                  <Globe className="w-3.5 h-3.5 shrink-0" />
                  <span className="text-label-sm leading-none hidden xs:inline">Entdecken</span>
                  {contactsList.some((c) => c.isPublicUser) && (
                    <span
                      className={`text-label-sm px-1 py-0.2 rounded-full font-bold leading-none ${
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
                      <div className="absolute -bottom-0.5 -right-0.5 w-4 h-4 rounded-full bg-primary text-on-primary flex items-center justify-center text-label-sm shadow-sm border-2 border-surface">
                        <Plus className="w-2.5 h-2.5" />
                      </div>
                    ) : (
                      <span className="absolute -top-0.5 -right-0.5 w-4 h-4 rounded-full bg-status-success text-white text-label-sm font-bold flex items-center justify-center border-2 border-surface">
                        {myStories.length}
                      </span>
                    )}
                  </div>
                  <span className="text-label-sm text-on-surface-variant truncate w-full text-center">
                    {myStories.length > 0 ? t('messenger.yourStatus') : 'Neu'}
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
                        <span className="absolute -top-0.5 -right-0.5 w-4 h-4 rounded-full bg-primary text-on-primary text-label-sm font-bold flex items-center justify-center border-2 border-surface">
                          {group.stories.length}
                        </span>
                      )}
                    </div>
                    <span
                      className={`text-label-sm truncate w-full text-center ${
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
                      <span className="inline-flex items-center justify-center px-1.5 py-0.5 text-label-sm font-bold rounded-full bg-on-surface-variant/20 text-on-surface-variant min-w-[18px]">
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
                    <div className="px-2 py-1 text-label-sm font-semibold text-on-surface-variant/70 uppercase tracking-wider flex items-center justify-between">
                      <span>{t('messenger.sectionGroups')}</span>
                      <div className="flex items-center gap-1">
                        <span className="text-label-sm">{filteredGroups.length}</span>
                        <button
                          type="button"
                          onClick={() => setIsCreateGroupOpen(true)}
                          className="p-0.5 rounded text-on-surface-variant hover:text-primary transition-colors"
                          aria-label={t('messenger.newGroup')}
                          title={t('messenger.newGroup')}
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
                      <div className="px-2 py-1 text-label-sm font-semibold text-on-surface-variant/70 uppercase tracking-wider flex items-center justify-between">
                        <span>Direktnachrichten</span>
                        <span className="text-label-sm">{filteredContacts.length}</span>
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
                    {t('messenger.noContactsOrGroups')}
                  </p>
                )}

                {/* Über alle Chats hinweg suchen, Markiertes und „an mich". */}
                <div className="pt-2 mt-1 border-t border-outline-variant/20 space-y-0.5">
                  <button
                    type="button"
                    onClick={() => void oeffneUeberall('markiert')}
                    className="w-full min-h-11 px-2.5 flex items-center gap-2.5 rounded-xl text-left text-xs text-on-surface-variant hover:bg-surface-container-high/60 transition-colors"
                  >
                    <Star className="w-4 h-4 text-status-warning shrink-0" />
                    <span>{t('messenger.markedMessages')}</span>
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
                      <span className="truncate">„{searchQuery.trim()}" in Nachrichten</span>
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
                    title={t('messenger.addStatus')}
                  >
                    <Plus className="w-3.5 h-3.5" />
                    <span>{t('common.add')}</span>
                  </Button>
                </div>

                {/* My Status Card with crisp contrast and clear visual identity */}
                <div className="p-3.5 rounded-2xl bg-surface-container/70 border border-outline-variant/35 shadow-sm transition-colors hover:bg-surface-container/90">
                  <div className="flex items-center justify-between mb-3">
                    <span className="text-xs font-headline font-bold text-on-surface">{t('messenger.myStatus')}</span>
                    <span className="text-label-sm text-on-surface-variant font-medium">{t('social.story.badge24h')}</span>
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
                        {myStories.length > 0 ? t('messenger.viewStatus') : t('social.story.share')}
                      </div>
                      <p className="text-label-sm text-on-surface-variant truncate">
                        {myStories.length > 0
                          ? t('messenger.activeStories', { count: myStories.length })
                          : t('messenger.statusHint')}
                      </p>
                    </div>
                  </div>
                </div>

                {/* Friends' Stories Section */}
                <div className="space-y-2">
                  <div className="px-1 text-label-sm font-semibold text-on-surface-variant/80 uppercase tracking-wider flex items-center justify-between">
                    <span>{t('messenger.recentUpdates')}</span>
                    <span className="text-label-sm px-1.5 py-0.5 rounded-full bg-surface-container font-mono text-on-surface-variant">
                      {friendsStoriesGrouped.length}
                    </span>
                  </div>

                  {friendsStoriesGrouped.length === 0 ? (
                    <div className="p-4 rounded-xl bg-surface-container/40 border border-outline-variant/25 text-center text-xs text-on-surface-variant">
                      {t('messenger.noStatusUpdates')}
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
                              <span className="absolute -top-1 -right-1 w-4 h-4 rounded-full bg-primary text-on-primary text-label-sm font-bold flex items-center justify-center border border-surface">
                                {grp.stories.length}
                              </span>
                            )}
                          </div>
                          <div className="min-w-0 flex-1">
                            <div className="text-xs font-semibold text-on-surface truncate">
                              {grp.username}
                            </div>
                            <div className="text-label-sm text-on-surface-variant flex items-center gap-1">
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
                  <div className="px-1 text-label-sm font-semibold text-on-surface-variant/70 uppercase tracking-wider">
                    {t('messenger.contactActivity')}
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
                            <div className="text-label-sm text-on-surface-variant/80 truncate">
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
                      <span>{t('messenger.communitiesTitle')}</span>
                      <span className="text-label-sm text-on-surface-variant/70">({groups.length})</span>
                    </div>
                    <p className="text-label-sm text-on-surface-variant/80">
                      {t('messenger.communitySubtitle')}
                    </p>
                  </div>
                  <Button
                    type="button"
                    variant="primary"
                    size="sm"
                    onClick={() => setIsCreateGroupOpen(true)}
                    className="h-7 text-xs gap-1 px-2.5 rounded-full"
                    aria-label={t('messenger.newGroup')}
                    title={t('messenger.newGroup')}
                  >
                    <Plus className="w-3.5 h-3.5" />
                    <span>{t('messenger.createGroup')}</span>
                  </Button>
                </div>

                <div className="space-y-1.5 pt-1">
                  {groups.length === 0 ? (
                    <p className="py-6 text-center text-xs text-on-surface-variant/70">
                      {t('messenger.noGroupsJoined')}
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
                            <div className="text-label-sm text-on-surface-variant/70">{g.member_count} Mitglieder</div>
                          </div>
                        </div>

                        {g.invite_code && (
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            onClick={() => handleCopyInviteLink(g)}
                            className="h-7 px-2 text-xs gap-1 text-primary"
                            title={t('messenger.copyInvite')}
                          >
                            <Share2 className="w-3.5 h-3.5" />
                            <span>Link</span>
                          </Button>
                        )}
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
                aria-label={t('messenger.nav.chats')}
              >
                <div className={`p-1 rounded-full ${mobileNavTab === 'chats' ? 'bg-primary/15' : ''}`}>
                  <MessageSquare className="w-4 h-4" />
                </div>
                <span className="text-label-sm mt-0.5">{t('messenger.nav.chats')}</span>
              </button>

              <button
                type="button"
                onClick={() => setMobileNavTab('updates')}
                className={`flex flex-col items-center justify-center flex-1 py-1 transition-colors ${
                  mobileNavTab === 'updates' ? 'text-primary font-semibold' : 'text-on-surface-variant/70 hover:text-on-surface'
                }`}
                aria-label={t('messenger.nav.updates')}
              >
                <div className={`p-1 rounded-full ${mobileNavTab === 'updates' ? 'bg-primary/15' : ''}`}>
                  <Sparkles className="w-4 h-4" />
                </div>
                <span className="text-label-sm mt-0.5">{t('messenger.nav.updates')}</span>
              </button>

              <button
                type="button"
                onClick={() => setMobileNavTab('community')}
                className={`flex flex-col items-center justify-center flex-1 py-1 transition-colors ${
                  mobileNavTab === 'community' ? 'text-primary font-semibold' : 'text-on-surface-variant/70 hover:text-on-surface'
                }`}
                aria-label={t('messenger.nav.community')}
              >
                <div className={`p-1 rounded-full ${mobileNavTab === 'community' ? 'bg-primary/15' : ''}`}>
                  <UsersRound className="w-4 h-4" />
                </div>
                <span className="text-label-sm mt-0.5">{t('messenger.nav.community')}</span>
              </button>
            </nav>
          )}
        </div>

        {/* Right Column: Chat Thread & Input Area */}
        <div
          // `min-w-0` ist hier nicht kosmetisch: ein Flex-Kind hat von Haus
          // aus `min-width: auto` und kann damit nicht unter die Breite
          // seines Inhalts schrumpfen. Der Chatbereich wuchs so auf 402 px
          // in einem 375 px breiten Fenster und schob sich 11 px nach links
          // aus dem Bild — daher die verrutschten Texte am Telefon.
          className={`flex-1 min-w-0 flex flex-col min-h-0 bg-surface-container-lowest/30 relative ${
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
            <div className="absolute inset-0 z-40 bg-surface/85 backdrop-blur-sm border-2 border-dashed border-primary flex flex-col items-center justify-center p-6 text-center pointer-events-none">
              <Upload className="w-12 h-12 text-primary animate-bounce mb-2" />
              <p className="font-headline font-bold text-sm text-primary">{t('messenger.dropFile')}</p>
              <p className="text-xs text-on-surface-variant">{t('messenger.dropHint')}</p>
            </div>
          )}

          {/* Der Chat-Hintergrund — dieselbe Schicht wie im KI-Bereich. */}
          <ChatHintergrund bereich="messenger" />

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
                      aria-label={t('messenger.endSelection')}
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
                          toast.error(t('messenger.nothingToForwardPlural'))
                          return
                        }
                        setWeiterzuleiten(auswahl)
                      }}
                      className="min-w-16 min-h-12 flex flex-col items-center justify-center gap-0.5 rounded-xl text-label-sm text-on-surface-variant hover:bg-surface-container-high disabled:opacity-40 transition-colors"
                    >
                      <Forward className="w-5 h-5" />
                      <span>Weiterleiten</span>
                    </button>
                    <button
                      type="button"
                      disabled={gewaehlteUuids.length === 0}
                      onClick={() => void handleAuswahlKopieren()}
                      className="min-w-16 min-h-12 flex flex-col items-center justify-center gap-0.5 rounded-xl text-label-sm text-on-surface-variant hover:bg-surface-container-high disabled:opacity-40 transition-colors"
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
                      className="min-w-16 min-h-12 flex flex-col items-center justify-center gap-0.5 rounded-xl text-label-sm text-on-surface-variant hover:bg-surface-container-high disabled:opacity-40 transition-colors"
                    >
                      <Star className="w-5 h-5" />
                      <span>Markieren</span>
                    </button>
                    <button
                      type="button"
                      disabled={!gewaehlteNachrichten().some((m) => m.isSelf && !m.isDeleted)}
                      onClick={() => void handleAuswahlLoeschen()}
                      className="min-w-16 min-h-12 flex flex-col items-center justify-center gap-0.5 rounded-xl text-label-sm text-status-destructive hover:bg-status-destructive/10 disabled:opacity-40 transition-colors"
                    >
                      <Trash2 className="w-5 h-5" />
                      <span>{t('common.delete')}</span>
                    </button>
                  </div>
                </>
              )}

              {/* Floating Chat Controls (Header-free, maximal chat space) */}
              <div
                className={`absolute top-2.5 left-3 right-3 z-30 flex items-center justify-between gap-2 pointer-events-none ${
                  auswahlModus || sucheOffen ? 'hidden' : ''
                }`}
              >
                <div className="flex items-center gap-2 min-w-0 pointer-events-auto">
                  {/* Mobile Back Button */}
                  <button
                    type="button"
                    onClick={() => {
                      setActiveContact(null)
                      setActiveGroup(null)
                    }}
                    className="md:hidden w-11 h-11 flex items-center justify-center rounded-full bg-surface-container-high/85 hover:bg-surface-container-high backdrop-blur-md border border-outline-variant/30 text-on-surface-variant shadow-sm transition-colors"
                    aria-label={t('messenger.backToContacts')}
                    title={t('messenger.backToContacts')}
                  >
                    <ChevronLeft className="w-4 h-4" />
                  </button>

                  {/* Header Title Badge with Mute & Block Indicators */}
                  <div className="flex items-center gap-2 min-w-0 px-3 py-1.5 rounded-full bg-surface-container-high/85 backdrop-blur-md border border-outline-variant/30 shadow-sm">
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
                      <span className="px-1.5 py-0.2 rounded-md bg-status-destructive/15 text-status-destructive text-label-sm font-semibold">
                        Blockiert
                      </span>
                    )}
                  </div>
                </div>

                {/* Was oft gebraucht wird, steht hier. Alles Übrige liegt
                    im Menü: acht Knöpfe passten bei 375 px nicht nebeneinander,
                    die Gruppe lief 41 px über den rechten Rand hinaus und
                    drängte den Namen auf null Abstand.

                    Schlichte <button> statt der Button-Komponente: deren
                    size="icon" setzt h-8 w-8 fest, und weil die Klassen nur
                    aneinandergehängt werden, gewinnt im CSS die feste Größe
                    gegen jede mitgegebene. Am Telefon braucht es 44 px. */}
                <div className="flex items-center gap-1.5 shrink-0 pointer-events-auto">
                  {activeGroup && (
                    <button
                      type="button"
                      onClick={() => handleStartGroupCall(false)}
                      disabled={!groupCallPermissions.canStart}
                      className="flex items-center justify-center rounded-md h-11 w-11 sm:h-8 sm:w-8 bg-surface-container-high/85 hover:bg-surface-container-high backdrop-blur-md border border-outline-variant/30 text-primary shadow-sm disabled:opacity-60"
                      title={
                        groupCallPermissions.canStart
                          ? t('messenger.startGroupCall')
                          : t('messenger.noStartCallRight')
                      }
                      aria-label={t('messenger.startGroupCall')}
                    >
                      <UsersRound className="w-4 h-4" />
                    </button>
                  )}

                  {activeContact && activeContact.isFriend && (
                    <button
                        type="button"
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
                            toast.error(err?.message || t('messenger.callStartFailedSingle'))
                          }
                        }}
                        className="flex items-center justify-center rounded-md h-11 w-11 sm:h-8 sm:w-8 bg-surface-container-high/85 hover:bg-surface-container-high backdrop-blur-md border border-outline-variant/30 text-primary shadow-sm"
                        title={t('messenger.startVoiceCall')}
                        aria-label={t('messenger.startVoiceCall')}
                      >
                      <Phone className="w-4 h-4" />
                    </button>
                  )}

                  {activeContact && !activeContact.isFriend && (
                    <button
                      type="button"
                      onClick={async () => {
                        try {
                          await sendFriendRequest(activeContact.username)
                          toast.success(t('messenger.friendRequestSent', { name: activeContact.username }))
                        } catch (err: any) {
                          toast.error(err?.message || t('messenger.friendRequestFailed'))
                        }
                      }}
                      className="flex items-center justify-center rounded-md h-11 w-11 sm:h-8 sm:w-8 bg-surface-container-high/85 hover:bg-surface-container-high backdrop-blur-md border border-outline-variant/30 text-primary shadow-sm"
                      title={t('messenger.sendFriendRequest')}
                      aria-label={t('messenger.sendFriendRequest')}
                    >
                      <UserPlus className="w-4 h-4" />
                    </button>
                  )}

                  <button
                    type="button"
                    onClick={() => setSucheOffen(true)}
                    className="flex items-center justify-center rounded-md h-11 w-11 sm:h-8 sm:w-8 bg-surface-container-high/85 hover:bg-surface-container-high backdrop-blur-md border border-outline-variant/30 text-on-surface-variant hover:text-primary shadow-sm"
                    title={t('messenger.searchInChat')}
                    aria-label={t('messenger.searchInChat')}
                  >
                    <Search className="w-4 h-4" />
                  </button>

                  <Blattknopf
                    variante="schwebend"
                    label={t('messenger.moreChatSettings')}
                    titel={activeGroup ? activeGroup.name : activeContact?.username || t('messenger.chat')}
                    ueberschrift={activeGroup ? activeGroup.name : activeContact?.username}
                  >
                    {(schliessen) => (
                      <>
                      {blindMailboxId && (
                        <Blatteintrag
                          icon={isChatMuted(blindMailboxId) ? <Bell className="w-4 h-4" /> : <BellOff className="w-4 h-4" />}
                          label={isChatMuted(blindMailboxId) ? t('messenger.unmute') : t('messenger.mute')}
                          onClick={() => {
                            schliessen()
                            setIsMuteModalOpen(true)
                          }}
                        />
                      )}
                      <Blatteintrag
                        icon={<Timer className="w-4 h-4" />}
                        label={t('messenger.disappearingMessages')}
                        hinweis={stufenLabel(verfallSekunden > 0 ? verfallSekunden : 0, t)}
                        onClick={() => {
                          schliessen()
                          setVerfallOffen(true)
                        }}
                      />
                      {activeContact && activeContact.isFriend && (
                        <Blatteintrag
                          icon={<Video className="w-4 h-4" />}
                          label={t('messenger.videoCall')}
                          onClick={async () => {
                            schliessen()
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
                              toast.error(err?.message || t('messenger.callStartFailedSingle'))
                            }
                          }}
                        />
                      )}
                      <Blatteintrag
                        icon={<ImageIcon className="w-4 h-4" />}
                        label={t('social.wallpaper.title')}
                        onClick={() => {
                          schliessen()
                          setIsWallpaperModalOpen(true)
                        }}
                      />

                      {activeGroup && (
                        <>
                          {activeGroup.invite_code && (
                            <Blatteintrag
                              icon={<Share2 className="w-4 h-4" />}
                              label={t('messenger.copyInvite')}
                              onClick={() => {
                                schliessen()
                                handleCopyInviteLink(activeGroup)
                              }}
                            />
                          )}
                          {(activeGroup.owner_user_id === currentUserId || activeGroup.role === 'admin') && (
                            <>
                              <Blatteintrag
                                icon={<ImagePlus className="w-4 h-4" />}
                                label={t('messenger.changeGroupLogo')}
                                disabled={logoLaedt}
                                onClick={() => {
                                  schliessen()
                                  gruppenLogoInputRef.current?.click()
                                }}
                              />
                              <Blatteintrag
                                icon={<Shield className="w-4 h-4" />}
                                label={t('messenger.manageGroupRoles')}
                                onClick={() => {
                                  schliessen()
                                  setIsGroupPermissionsOpen(true)
                                }}
                              />
                            </>
                          )}
                          {activeGroup.owner_user_id === currentUserId ? (
                            <Blatteintrag
                              icon={<Trash2 className="w-4 h-4" />}
                              label={t('messenger.deleteGroup')}
                              gefahr
                              onClick={() => {
                                schliessen()
                                handleDeleteGroup(activeGroup)
                              }}
                            />
                          ) : (
                            <Blatteintrag
                              icon={<LogOut className="w-4 h-4" />}
                              label={t('messenger.leaveGroup')}
                              gefahr
                              onClick={() => {
                                schliessen()
                                handleLeaveGroup(activeGroup)
                              }}
                            />
                          )}
                        </>
                      )}

                      {activeContact && (
                        <Blatteintrag
                          icon={<Ban className="w-4 h-4" />}
                          label={
                            isBlocked(activeContact.userId)
                              ? t('messenger.unblockContact')
                              : t('messenger.blockContact')
                          }
                          gefahr={!isBlocked(activeContact.userId)}
                          onClick={() => {
                            schliessen()
                            setIsBlockConfirmOpen(true)
                          }}
                        />
                      )}
                      </>
                    )}
                  </Blattknopf>
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
                  className="absolute top-[4.25rem] sm:top-14 left-3 right-3 z-20 min-h-11 px-3 py-2 flex items-center gap-2.5 rounded-xl bg-surface-container-high/90 backdrop-blur-md border border-outline-variant/30 shadow-sm text-left"
                >
                  <Pin className="w-3.5 h-3.5 text-primary shrink-0" />
                  <span className="min-w-0 flex-1">
                    <span className="block text-label-sm font-semibold text-primary">Angeheftet</span>
                    <span className="block text-label-sm text-on-surface-variant truncate">
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
                      aria-label={t('messenger.unpin')}
                    >
                      <X className="w-4 h-4" />
                    </span>
                  )}
                </button>
              )}

              {/* Message Thread Scroll Area */}
              <div
                ref={scrollContainerRef}
                // Das obere Polster muss die schwebende Kopfzeile freihalten.
                // Am Telefon ist sie 44 px hoch (Trefflaeche), am Zeigergeraet
                // 32 px; mit einem festen pt-12 verdeckte sie dort die ersten
                // Zeilen des Verlaufs.
                className="flex-1 overflow-y-auto p-4 pt-16 sm:pt-12 space-y-3 relative z-1"
              >
                {/* WhatsApp-style encryption notice banner */}
                <div className="py-1 text-center">
                  <div className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-surface-container-high/60 border border-outline-variant/30 text-label-sm text-on-surface-variant shadow-2xs">
                    <Lock className="w-3 h-3 text-status-success" />
                    <span>{t('messenger.e2eeBanner')}</span>
                  </div>
                </div>


                {messages.length === 0 && !loadingMessages && (
                  <div className="py-16 text-center text-xs text-on-surface-variant/70">
                    {t('messenger.noMessagesYet')}
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
                        <div className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-surface-container-high/60 border border-outline-variant/30 text-label-sm text-on-surface-variant shadow-2xs">
                          <Shield className="w-3 h-3 text-status-warning shrink-0" />
                          <span>{msg.text}</span>
                        </div>
                      </div>
                    )
                  }

                  const currentDateBadge = formatChatDateBadge(msg.createdAt, t, i18n.language)
                  const prevDateBadge = idx > 0 ? formatChatDateBadge(messages[idx - 1].createdAt, t, i18n.language) : null
                  const showDateSeparator = Boolean(currentDateBadge && currentDateBadge !== prevDateBadge)

                  return (
                    <React.Fragment key={msg.id}>
                      {showDateSeparator && (
                        <div className="flex justify-center my-3 pointer-events-none">
                          <span className="px-3.5 py-1 rounded-full text-label-sm font-semibold bg-surface-container/90 text-on-surface-variant backdrop-blur-md border border-outline-variant/30 shadow-sm">
                            {currentDateBadge}
                          </span>
                        </div>
                      )}

                      {trennerId !== null && msg.id === trennerId && (
                        <div className="flex items-center gap-2 my-3">
                          <span className="h-px flex-1 bg-primary/30" />
                          <span className="text-label-sm font-semibold text-primary uppercase tracking-wide">
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
                  <div className="flex items-center gap-2 text-xs py-1.5 px-3 rounded-full bg-surface-container-high/90 border border-outline-variant/30 text-on-surface w-fit shadow-sm animate-slide-up">
                    {partnerActivity.status === 'recording' ? (
                      <>
                        <Mic className="w-3.5 h-3.5 text-status-destructive animate-pulse" />
                        <span className="text-label-sm text-status-destructive font-medium">
                          {activeGroup
                            ? t('messenger.someoneRecording', {
                                name: partnerActivity.username || t('messenger.someone'),
                              })
                            : t('messenger.recordingVoice')}
                        </span>
                      </>
                    ) : (
                      <>
                        <span className="flex gap-1 items-center px-0.5">
                          <span className="w-1.5 h-1.5 rounded-full bg-primary animate-bounce [animation-delay:-0.3s]" />
                          <span className="w-1.5 h-1.5 rounded-full bg-primary animate-bounce [animation-delay:-0.15s]" />
                          <span className="w-1.5 h-1.5 rounded-full bg-primary animate-bounce" />
                        </span>
                        <span className="text-label-sm text-primary font-medium">
                          {activeGroup ? `${partnerActivity.username || 'Jemand'} schreibt …` : t('messenger.typing')}
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
                      aria-label={t('messenger.removeImage')}
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
                      <p className="text-label-sm text-on-surface-variant">{formatFileSize(stagedFile.sizeBytes)}</p>
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => setStagedFile(null)}
                    className="p-1 rounded-full hover:bg-surface-container-highest text-on-surface-variant"
                    aria-label={t('messenger.removeFile')}
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
                      <p className="text-xs font-semibold text-primary">{t('messenger.editMessage')}</p>
                      <p className="text-label-sm text-on-surface-variant truncate max-w-md">
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
                    aria-label={t('messenger.cancelEdit')}
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
                    aria-label={t('messenger.jumpToEnd')}
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
                  <div className="flex flex-col sm:flex-row items-center justify-between gap-3 p-3 rounded-xl bg-status-destructive/10 border border-status-destructive/30 text-xs text-status-destructive">
                    <div className="flex items-center gap-2">
                      <Ban className="w-4 h-4 shrink-0" />
                      <span>{t('messenger.contactBlocked')}</span>
                    </div>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => void unblockUser(activeContact.userId)}
                      className="h-7 text-xs px-3 border border-status-destructive/30 hover:bg-status-destructive/20 text-status-destructive font-medium"
                    >
                      {t('messenger.unblock')}
                    </Button>
                  </div>
                ) : isRecording ? (
                  <VoiceRecordingBar
                    durationSeconds={recordingDuration}
                    statusLabel={t('messenger.voiceRecording')}
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
                      <div className="mb-2 p-2.5 rounded-xl bg-surface-container border border-outline-variant/30 shadow-lg animate-slide-up">
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
                            aria-label={t('common.close')}
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
                                <span className="text-label-sm text-on-surface-variant/80 truncate w-full text-center mt-1 font-medium">
                                  {stk.label}
                                </span>
                              </button>
                            ))}
                          </div>
                        ) : (
                          <div className="space-y-3 max-h-52 overflow-y-auto p-1.5">
                            {CATEGORIZED_EMOJIS.map((cat) => (
                              <div key={cat.category} className="space-y-1">
                                <div className="text-label-sm font-bold text-on-surface-variant/70 uppercase tracking-wider px-1">
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
                                        <span className="block text-label-sm text-on-surface-variant">
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
                                  <span className="block text-label-sm font-semibold text-primary truncate">
                                    Antwort an {antwortAuf.absenderName || 'Nachricht'}
                                  </span>
                                  <span className="block text-label-sm text-on-surface-variant line-clamp-1">
                                    {antwortAuf.auszug}
                                  </span>
                                </span>
                                <button
                                  type="button"
                                  onClick={() => setAntwortAuf(null)}
                                  className="w-11 h-11 -mr-1 rounded-full flex items-center justify-center text-on-surface-variant hover:bg-surface-container-high transition-colors shrink-0"
                                  aria-label={t('messenger.discardReply')}
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
                        disabled={istSchreibenGesperrt || !darfSchreiben}
                        placeholder={
                          !darfSchreiben
                            ? // Kein Schlüsselproblem, sondern eine
                              // Rechtelage: in dieser Gruppe darf dieses Konto
                              // nicht schreiben. Das gehört gesagt, nicht
                              // durch ein totes Feld angedeutet.
                              t('messenger.sendNoRight')
                            : istSchreibenGesperrt
                            ? // Hier stand „zuerst den Schlüssel entsperren".
                              // Das stammte aus der Zeit, als der
                              // Identitätsschlüssel eine eigene Passphrase
                              // hatte — die gibt es nicht mehr, und die
                              // Aufforderung schickte den Benutzer nach
                              // nirgendwo. Was bleibt, ist ein kurzer Moment.
                              t('messenger.keyPreparing')
                            : editingMessage
                              ? t('messenger.editPlaceholder')
                              : t('messenger.writePlaceholder')
                        }
                        leftActions={
                          <>
                            <button
                              type="button"
                              onClick={() => setIsStickerPickerOpen((prev) => !prev)}
                              className={`w-11 h-11 sm:w-8 sm:h-8 shrink-0 flex items-center justify-center rounded-full transition-colors ${
                                isStickerPickerOpen
                                  ? 'bg-surface-container-highest text-status-warning'
                                  : 'text-on-surface-variant hover:text-status-warning'
                              }`}
                              title={t('messenger.stickers')}
                              aria-label={t('messenger.pickSticker')}
                            >
                              <Smile className="w-4 h-4" />
                            </button>

                            {/* Unified Attachment Button with sleek Popover */}
                            <div className="relative shrink-0" ref={attachMenuRef}>
                              <button
                                type="button"
                                disabled={!darfAnhaengen}
                                onClick={() => setIsAttachMenuOpen((prev) => !prev)}
                                className={`w-11 h-11 sm:w-8 sm:h-8 shrink-0 flex items-center justify-center rounded-full transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${
                                  isAttachMenuOpen
                                    ? 'bg-surface-container-highest text-primary'
                                    : 'text-on-surface-variant hover:text-primary'
                                }`}
                                title={darfAnhaengen ? t('messenger.addAttachment') : t('messenger.attachNoRight')}
                                aria-label={darfAnhaengen ? t('messenger.addAttachment') : t('messenger.attachNoRight')}
                              >
                                <Plus className={`w-4 h-4 transition-transform duration-200 ${isAttachMenuOpen ? 'rotate-45 text-primary' : ''}`} />
                              </button>

                              {/* Attachment Popover Menu */}
                              {isAttachMenuOpen && (
                                <div className="absolute bottom-10 left-0 z-30 min-w-[210px] p-1.5 rounded-2xl bg-surface-container-high/95 backdrop-blur-md border border-outline-variant/30 shadow-xl space-y-1 animate-slide-up">
                                  <button
                                    type="button"
                                    onClick={() => {
                                      setIsAttachMenuOpen(false)
                                      setIsCameraModalOpen(true)
                                    }}
                                    className="w-full flex items-center gap-2.5 px-3 py-2 rounded-xl text-left hover:bg-surface-container-highest/80 transition-colors group"
                                    aria-label={t('messenger.attachPhoto')}
                                  >
                                    <div className="w-7 h-7 rounded-lg bg-primary/15 text-primary flex items-center justify-center shrink-0 group-hover:scale-105 transition-transform">
                                      <Camera className="w-4 h-4" />
                                    </div>
                                    <div className="min-w-0 flex-1">
                                      <div className="text-xs font-semibold text-primary">{t('social.camera.take')}</div>
                                      <div className="text-label-sm text-on-surface-variant/70">{t('messenger.cameraSnapshot')}</div>
                                    </div>
                                  </button>

                                  <button
                                    type="button"
                                    onClick={() => {
                                      setIsAttachMenuOpen(false)
                                      fileInputRef.current?.click()
                                    }}
                                    className="w-full flex items-center gap-2.5 px-3 py-2 rounded-xl text-left hover:bg-surface-container-highest/80 transition-colors group"
                                    aria-label={t('messenger.pickPhoto')}
                                  >
                                    <div className="w-7 h-7 rounded-lg bg-primary/15 text-primary flex items-center justify-center shrink-0 group-hover:scale-105 transition-transform">
                                      <ImageIcon className="w-4 h-4" />
                                    </div>
                                    <div className="min-w-0 flex-1">
                                      <div className="text-xs font-semibold text-primary">{t('messenger.photo')}</div>
                                      <div className="text-label-sm text-on-surface-variant/70">{t('messenger.fromGallery')}</div>
                                    </div>
                                  </button>

                                  <button
                                    type="button"
                                    onClick={() => {
                                      setIsAttachMenuOpen(false)
                                      docInputRef.current?.click()
                                    }}
                                    className="w-full flex items-center gap-2.5 px-3 py-2 rounded-xl text-left hover:bg-surface-container-highest/80 transition-colors group"
                                    aria-label={t('messenger.attachFile')}
                                  >
                                    <div className="w-7 h-7 rounded-lg bg-primary/15 text-primary flex items-center justify-center shrink-0 group-hover:scale-105 transition-transform">
                                      <Paperclip className="w-4 h-4" />
                                    </div>
                                    <div className="min-w-0 flex-1">
                                      <div className="text-xs font-semibold text-primary">{t('messenger.document')}</div>
                                      <div className="text-label-sm text-on-surface-variant/70">{t('messenger.sendEncrypted')}</div>
                                    </div>
                                  </button>

                                  <button
                                    type="button"
                                    onClick={() => {
                                      setIsAttachMenuOpen(false)
                                      handleOpenNotePicker()
                                    }}
                                    className="w-full flex items-center gap-2.5 px-3 py-2 rounded-xl text-left hover:bg-surface-container-highest/80 transition-colors group"
                                    aria-label={t('messenger.shareNote')}
                                  >
                                    <div className="w-7 h-7 rounded-lg bg-primary/15 text-primary flex items-center justify-center shrink-0 group-hover:scale-105 transition-transform">
                                      <StickyNote className="w-4 h-4" />
                                    </div>
                                    <div className="min-w-0 flex-1">
                                      <div className="text-xs font-semibold text-primary">{t('messenger.attachNote')}</div>
                                      <div className="text-label-sm text-on-surface-variant/70">{t('messenger.fromNotes')}</div>
                                    </div>
                                  </button>

                                  <button
                                    type="button"
                                    onClick={() => {
                                      setIsAttachMenuOpen(false)
                                      handleOpenCalendarPicker()
                                    }}
                                    className="w-full flex items-center gap-2.5 px-3 py-2 rounded-xl text-left hover:bg-surface-container-highest/80 transition-colors group"
                                    aria-label={t('messenger.shareEvent')}
                                  >
                                    <div className="w-7 h-7 rounded-lg bg-primary/15 text-primary flex items-center justify-center shrink-0 group-hover:scale-105 transition-transform">
                                      <CalendarIcon className="w-4 h-4" />
                                    </div>
                                    <div className="min-w-0 flex-1">
                                      <div className="text-xs font-semibold text-primary">{t('messenger.attachEvent')}</div>
                                      <div className="text-label-sm text-on-surface-variant/70">{t('messenger.fromCalendar')}</div>
                                    </div>
                                  </button>
                                </div>
                              )}
                            </div>
                          </>
                        }
                        rightActions={
                          inputText.trim() || selectedImage || stagedFile ? (
                            /*
                             * Ein einfacher Knopf, kein `<Button>` — wie seine
                             * Nachbarn in dieser Leiste.
                             *
                             * `<Button size="sm">` bringt `h-8` mit, und das
                             * gewinnt gegen ein `h-11` aus `className`: über
                             * die Höhe entscheidet die Reihenfolge im
                             * Stylesheet, nicht die im Attribut. Gemessen war
                             * der Knopf am Telefon deshalb 44 × 32 statt
                             * 44 × 44 — zu flach für einen Daumen, und das
                             * bei der einen Handlung, für die es keinen
                             * zweiten Weg gibt. `msm-btn-primary` bringt nur
                             * die Farben mit und kollidiert mit nichts.
                             */
                            <button
                              type="submit"
                              disabled={sending}
                              className="msm-btn-primary w-11 h-11 sm:w-8 sm:h-8 shrink-0 rounded-full flex items-center justify-center"
                              title="Senden"
                              aria-label="Senden"
                            >
                              <Send className="w-3.5 h-3.5" />
                            </button>
                          ) : (
                            <div className="flex items-center gap-1">
                              <button
                                type="button"
                                onClick={() => setIsVideoNoteRecording(true)}
                                className="w-11 h-11 sm:w-8 sm:h-8 shrink-0 flex items-center justify-center text-on-surface-variant hover:text-status-success hover:bg-status-success/10 rounded-full transition-colors"
                                title={t('messenger.recordVideoNoteHint')}
                                aria-label={t('messenger.recordVideoNote')}
                              >
                                <Video className="w-4 h-4" />
                              </button>
                              <button
                                type="button"
                                onClick={startRecording}
                                className="w-11 h-11 sm:w-8 sm:h-8 shrink-0 flex items-center justify-center text-on-surface-variant hover:text-primary hover:bg-primary/10 rounded-full transition-colors"
                                title={t('messenger.recordVoice')}
                                aria-label={t('messenger.recordVoice')}
                              >
                                <Mic className="w-4 h-4" />
                              </button>
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
                {t('messenger.pickChatHint')}
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
            toast.error(t('messenger.nothingToForward'))
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
        darfFremdeLoeschen={darfFremdeLoeschen}
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
            ? t('messenger.markedMessages')
            : ueberall === 'anMich'
              ? t('messenger.mentionsAndReplies')
              : t('messenger.searchFor', { frage: ueberallFrage })
        }
        leerText={
          ueberall === 'markiert'
            ? t('messenger.nothingMarkedYet')
            : ueberall === 'anMich'
              ? t('messenger.nothingMentioned')
              : t('messenger.noChatHasThisText')
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
            label={
              zeilenMenue && pinnedChats.includes(zeilenMenue.mid)
                ? t('messenger.unpin')
                : t('messenger.pin')
            }
            hinweis={t('messenger.pinHint', { count: PINS_MAX })}
            onClick={() => {
              if (zeilenMenue) handleAnheftenChat(zeilenMenue.mid)
              setZeilenMenue(null)
            }}
          />
          <Blatteintrag
            icon={zeilenMenue && archivedChats.includes(zeilenMenue.mid) ? <ArchiveRestore className="w-4 h-4" /> : <Archive className="w-4 h-4" />}
            label={
              zeilenMenue && archivedChats.includes(zeilenMenue.mid)
                ? t('messenger.unarchive')
                : t('messenger.archive')
            }
            onClick={() => {
              if (zeilenMenue) handleArchivieren(zeilenMenue.mid)
              setZeilenMenue(null)
            }}
          />
          <Blatteintrag
            icon={zeilenMenue && isChatMuted(zeilenMenue.mid) ? <Bell className="w-4 h-4" /> : <BellOff className="w-4 h-4" />}
            label={
              zeilenMenue && isChatMuted(zeilenMenue.mid)
                ? t('messenger.unmute')
                : t('messenger.mute')
            }
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
        titel={t('messenger.disappearingMessages')}
      >
        <div className="px-4 pt-1 pb-3">
          <p className="text-label-sm text-on-surface-variant leading-relaxed">
            {t('messenger.disappearingHint')}
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
              label={t(stufe.labelKey)}
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
              <span className="font-headline text-body-md font-bold text-primary">{t('messenger.newGroup')}</span>
            </div>
          </div>

          <form onSubmit={handleCreateGroup} className="space-y-4 pt-3">
            <div className="space-y-1.5">
              <label className="text-xs font-semibold text-on-surface">{t('messenger.groupNameLabel')}</label>
              <Input
                value={groupName}
                onChange={(e: React.ChangeEvent<HTMLInputElement>) => setGroupName(e.target.value)}
                placeholder={t('messenger.groupNamePlaceholder')}
                required
                className="text-xs h-9"
              />
            </div>

            <div className="space-y-1.5">
              <label className="text-xs font-semibold text-on-surface">{t('messenger.groupDescLabel')}</label>
              <Input
                value={groupDesc}
                onChange={(e: React.ChangeEvent<HTMLInputElement>) => setGroupDesc(e.target.value)}
                placeholder={t('messenger.groupDescPlaceholder')}
                className="text-xs h-9"
              />
            </div>

            <div className="p-3 rounded-xl bg-surface-container-high/60 border border-outline-variant/30 text-xs text-on-surface-variant space-y-1">
              <div className="flex items-center gap-1.5 font-semibold text-primary">
                <Sparkles className="w-3.5 h-3.5" />
                <span>{t('messenger.groupE2eeLabel')}</span>
              </div>
              <p className="text-label-sm">
                {t('messenger.groupInviteHint')}
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
                {creatingGroup ? t('messenger.creating') : t('messenger.createGroup')}
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
              <StickyNote className="w-4 h-4 text-status-warning" />
              <span className="font-headline text-body-sm font-bold text-primary">{t('messenger.shareNote')}</span>
            </div>
          </div>

          <div className="py-2">
            <Input
              value={noteSearch}
              onChange={(e: React.ChangeEvent<HTMLInputElement>) => setNoteSearch(e.target.value)}
              placeholder={t('messenger.searchNote')}
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
                    <p className="text-label-sm text-on-surface-variant line-clamp-2 mt-0.5">
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
              <CalendarIcon className="w-4 h-4 text-primary" />
              <span className="font-headline text-body-sm font-bold text-primary">{t('messenger.shareEventTitle')}</span>
            </div>
          </div>

          <div className="py-2">
            <Input
              value={calendarSearch}
              onChange={(e: React.ChangeEvent<HTMLInputElement>) => setCalendarSearch(e.target.value)}
              placeholder={t('messenger.searchEvent')}
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
                    <div className="text-label-sm text-on-surface-variant flex items-center gap-1 mt-0.5">
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
          className="msm-modal-overlay bg-black/80"
          onClick={() => setViewingImage(null)}
        >
          <div className="relative max-w-4xl max-h-[90vh]">
            <img
              src={viewingImage}
              alt={t('messenger.fullView')}
              className="max-h-[85vh] max-w-full rounded-xl object-contain shadow-2xl"
            />
            <button
              type="button"
              onClick={() => setViewingImage(null)}
              className="absolute -top-3 -right-3 p-1.5 rounded-full bg-surface-container-highest text-on-surface shadow-md"
              aria-label={t('common.close')}
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
              <span className="font-headline text-body-sm font-bold text-primary">{t('messenger.sendPhotoTo')}</span>
            </div>
          </div>

          <div className="flex-1 overflow-y-auto space-y-1 py-2">
            <div className="text-label-sm font-semibold text-on-surface-variant/70 uppercase tracking-wider px-2 py-1">
              {t('messenger.pickChat')}
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
                  <div className="text-label-sm text-on-surface-variant/70">Gruppe ({g.member_count} Mitglieder)</div>
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
                  <div className="text-label-sm text-on-surface-variant/70">{c.teamName || (c.isFriend ? 'Freund' : 'Kontakt')}</div>
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
            toast.error(t('messenger.replyContactMissing'))
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
              <span>{t('messenger.deleteGroupTitle')}</span>
            </DialogTitle>
            <DialogDescription>
              {t('messenger.deleteGroupMessage', { name: groupToDelete?.name ?? '' })}
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
              <span>{isDeletingGroup ? t('messenger.deleting') : t('messenger.deleteForGood')}</span>
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      {/* Das Hintergrundfenster — dasselbe, das der KI-Chat öffnet. */}
      <ChatHintergrundDialog
        bereich="messenger"
        offen={isWallpaperModalOpen}
        onOffenChange={setIsWallpaperModalOpen}
      />

      {/* Design-DNA Mute Dialog */}
      <Dialog open={isMuteModalOpen} onOpenChange={setIsMuteModalOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <BellOff className="w-5 h-5 text-primary" />
              <span>{t('messenger.muteNotifications')}</span>
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
                  toast.success(t('messenger.muted8h'))
                }
                setIsMuteModalOpen(false)
              }}
            >
              <Clock className="w-4 h-4 mr-2.5 text-on-surface-variant" />
              <div>
                <div className="font-semibold">{t('messenger.mute8h')}</div>
                <div className="text-label-sm text-on-surface-variant/70">{t('messenger.mute8hHint')}</div>
              </div>
            </Button>

            <Button
              variant="secondary"
              className="w-full justify-start text-left text-xs py-2.5 h-auto"
              onClick={() => {
                if (blindMailboxId) {
                  muteChat(blindMailboxId, 10080)
                  toast.success(t('messenger.muted1w'))
                }
                setIsMuteModalOpen(false)
              }}
            >
              <Clock className="w-4 h-4 mr-2.5 text-on-surface-variant" />
              <div>
                <div className="font-semibold">{t('messenger.mute1w')}</div>
                <div className="text-label-sm text-on-surface-variant/70">{t('messenger.mute1wHint')}</div>
              </div>
            </Button>

            <Button
              variant="secondary"
              className="w-full justify-start text-left text-xs py-2.5 h-auto"
              onClick={() => {
                if (blindMailboxId) {
                  muteChat(blindMailboxId, 0)
                  toast.success(t('messenger.mutedForever'))
                }
                setIsMuteModalOpen(false)
              }}
            >
              <BellOff className="w-4 h-4 mr-2.5 text-on-surface-variant" />
              <div>
                <div className="font-semibold">Immer</div>
                <div className="text-label-sm text-on-surface-variant/70">{t('messenger.muteForeverHint')}</div>
              </div>
            </Button>

            {blindMailboxId && isChatMuted(blindMailboxId) && (
              <Button
                variant="ghost"
                className="w-full justify-start text-left text-xs py-2.5 h-auto text-primary hover:bg-primary/10 mt-1 border border-primary/20"
                onClick={() => {
                  unmuteChat(blindMailboxId)
                  toast.success(t('social.contacts.unmuted'))
                  setIsMuteModalOpen(false)
                }}
              >
                <Bell className="w-4 h-4 mr-2.5 text-primary" />
                <div>
                  <div className="font-semibold">{t('messenger.unmute')}</div>
                  <div className="text-label-sm text-on-surface-variant/70">{t('messenger.unmuteHint')}</div>
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
            <DialogTitle className={`flex items-center gap-2 ${activeContact && isBlocked(activeContact.userId) ? 'text-primary' : 'text-status-destructive'}`}>
              <Ban className="w-5 h-5" />
              <span>
                {activeContact && isBlocked(activeContact.userId)
                  ? t('messenger.unblockTitle')
                  : t('messenger.blockTitle')}
              </span>
            </DialogTitle>
            <DialogDescription>
              {activeContact && isBlocked(activeContact.userId) ? (
                t('messenger.unblockMessage', { name: activeContact.username })
              ) : (
                t('messenger.blockMessage', { name: activeContact?.username ?? '' })
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
                  toast.success(t('social.contacts.unblocked', { name: activeContact.username }))
                  setIsBlockConfirmOpen(false)
                }}
              >
                {t('messenger.unblock')}
              </Button>
            ) : (
              <Button
                variant="destructive"
                size="sm"
                onClick={async () => {
                  if (activeContact) {
                    await blockUser(activeContact.userId, activeContact.username, activeContact.avatarUrl)
                    toast.success(t('messenger.contactBlockedToast', { name: activeContact.username }))
                  }
                  setIsBlockConfirmOpen(false)
                }}
              >
                {t('messenger.block')}
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
