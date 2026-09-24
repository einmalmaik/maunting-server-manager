import React, { useState, useEffect, useRef, useMemo, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { useSearchParams, useParams, useNavigate } from 'react-router-dom'
import {
  Button,
  Input,
  ChatInputBar,
  VoiceRecordingBar,
  Blattmenue,
  Blatteintrag,
  type ChatInputBarRef,
} from '@/Singra/UI'
import {
  MessageSquare,
  Send,
  Camera,
  Search,
  X,
  RefreshCw,
  Trash2,
  Plus,
  Smile,
  Upload,
  UserCheck,
  Bell,
  BellOff,
  Phone,
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
import type { PresenceStatus } from '@/components/social/StatusIndicator'
import {
  type ChatGroupItem,
  type ChatStoryItem,
  createGroup,
  joinGroupByInvite,
  sendFriendRequest,
  leaveGroup,
  deleteGroup,
  relayE2eeEnvelope,
  sendTypingSignal,
  ladeAnhangHoch,
  getGroupInviteInfo,
  setzeEinladungsKarte,
} from '@/api/social'
import { maxAnhangBytes } from '@/services/medienKrypto'
import {
  type AudioAttachment,
  type FileAttachment,
  type ImageAttachment,
  type MedienBindungsKontext,
  type VideoNoteAttachment,
} from '@/components/social/ChatMediaAttachments'
import { baueVersandFuer, useKonversation, type GespraechsZiel } from '@/hooks/useKonversation'
import { useSprachaufnahme } from '@/hooks/useSprachaufnahme'
import { useSprachwiedergabe } from '@/hooks/useSprachwiedergabe'
import { useChatSuche } from '@/hooks/useChatSuche'
import { useEntwuerfe } from '@/hooks/useEntwuerfe'
import { useKontaktdaten } from '@/hooks/useKontaktdaten'
import { useSicherheitsnummern } from '@/hooks/useSicherheitsnummern'
import { useStoryAnsicht } from '@/hooks/useStoryAnsicht'
import { useUeberallAnsicht } from '@/hooks/useUeberallAnsicht'
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
import {
  logischeUuid,
  DrGeraetNichtEingetragenError,
  DrZustellungFehlgeschlagenError,
} from '@/services/ratchetSitzung'
import {
  geraeteVon,
  eigenesGeraetFreigegeben,
  onEigeneFreigabe,
  onSchluesselWarnung,
} from '@/services/e2eeGeraet'
import { signiereNutzlast } from '@/services/nutzlastSignatur'
import {
  abonniereBekannteGespraeche,
  gruppenGeheimnis,
  verwirfGruppenSchluessel,
} from '@/services/gruppenSchluessel'
import {
  baueEinladungsKarte,
  einladungsschluesselAus,
  lieseEinladungsKarte,
  logoAlsDatenUrl,
  mitSchluessel,
  schluesselAusLink,
  type EinladungsInhalt,
} from '@/services/einladungsKarte'
import {
  merkeGruppenName,
  sichereGruppenAnsicht,
  vergissGruppenName,
} from '@/services/gruppenName'
import {
  fuelleNamenNach,
  gespraechsPartner,
  merkeGespraech,
  vergissGespraech,
} from '@/services/gespraechsListe'
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
import { bezugFelder } from '@/services/nachrichtBezug'
import { anheftung, setzeAnheftung } from '@/services/nachrichtAnheftung'
import { merkeQuittung, quittungsstand } from '@/services/quittungsstand'
import { schalteReaktion, wendeReaktionenAn } from '@/services/reaktionen'
import { werteUmschlaegeAus } from '@/services/umschlagAuswertung'
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
import { vergissMailbox, type Treffer } from '@/services/verlaufSuche'
import {
  faelligeZeilen,
  raeumeAlleChats,
  setzeVerfallsfrist,
  stufenDativ,
  stufenLabel,
  VERFALL_STUFEN,
  verfaelltAm as berechneVerfall,
  verfallsfrist,
  verfallStand,
} from '@/services/nachrichtVerfall'
import { ladeEntwurf } from '@/services/messengerLocalStore'
import { chatMediaBlobCache, sessionChatCache } from '@/services/klartextSpeicher'
import { ErwaehnungsWache } from '@/components/social/ErwaehnungsWache'
import { NachrichtenMenue } from '@/components/social/NachrichtenMenue'
import { WeiterleitenAnsicht } from '@/components/social/WeiterleitenAnsicht'
import { VerlaufSuchleiste } from '@/components/social/VerlaufSuchleiste'
import { TrefferListe } from '@/components/social/TrefferListe'

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
import { CameraSnapshotModal } from '@/components/social/CameraSnapshotModal'
import { CreateStoryModal } from '@/components/social/CreateStoryModal'
import { MessengerSperrschirm } from '@/components/social/MessengerSperrschirm'
import { siegelAktiv } from '@/services/lokaleVersiegelung'
import { useMessengerSperre } from '@/services/messengerSperre'
import { StoryViewerModal, type StoryReplyContext } from '@/components/social/StoryViewerModal'
import { GroupPermissionsModal } from '@/components/social/GroupPermissionsModal'
import { CreateGroupDialog } from '@/components/social/modals/CreateGroupDialog'
import { NotePickerDialog } from '@/components/social/modals/NotePickerDialog'
import { CalendarPickerDialog } from '@/components/social/modals/CalendarPickerDialog'
import { SendPhotoDialog } from '@/components/social/modals/SendPhotoDialog'
import { DeleteGroupDialog } from '@/components/social/modals/DeleteGroupDialog'
import { ChatMuteDialog } from '@/components/social/modals/ChatMuteDialog'
import { SafetyNumberDialog } from '@/components/social/modals/SafetyNumberDialog'
import { BlockConfirmDialog } from '@/components/social/modals/BlockConfirmDialog'
import { MessengerModeNav, MessengerBottomNav } from '@/components/social/sidebar/MessengerModeNav'
import { ContactFilterTabs } from '@/components/social/sidebar/ContactFilterTabs'
import { StoriesCarouselBar } from '@/components/social/sidebar/StoriesCarouselBar'
import { StatusUpdatesView } from '@/components/social/sidebar/StatusUpdatesView'
import { CommunityView } from '@/components/social/sidebar/CommunityView'
import { GroupListItem, ContactListItem, type ChatContact } from '@/components/social/sidebar/ConversationListItem'
import { ChatSelectionBar } from '@/components/social/chat/ChatSelectionBar'
import { ChatHeader } from '@/components/social/chat/ChatHeader'
import { ChatActionsMenu } from '@/components/social/chat/ChatActionsMenu'
import { ChatPinnedBar } from '@/components/social/chat/ChatPinnedBar'
import { ChatTimeline } from '@/components/social/chat/ChatTimeline'
import { StagedImageBar, StagedFileBar, EditingBanner, BlockedNotice } from '@/components/social/chat/ChatComposerBars'
import { StickerEmojiPicker, type PickerReiter } from '@/components/social/chat/StickerEmojiPicker'
import { AttachMenu } from '@/components/social/chat/AttachMenu'
import { MentionSuggestions, ChatReplyBar } from '@/components/social/chat/ChatComposerTop'
import { ComposerSendActions } from '@/components/social/chat/ComposerSendActions'
import { ChatHintergrund, ChatHintergrundDialog } from '@/features/chatHintergrund'
import { useAuthStore } from '@/stores/authStore'
import { toast } from '@/stores/toastStore'
import { useMessengerNotificationStore, PINS_MAX } from '@/stores/messengerNotificationStore'

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
interface SendeAuftrag {
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

/** Macht aus einer Aufnahme die Zeichenkette, die `medienKrypto` verschlüsselt. */
function blobAlsDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const leser = new FileReader()
    leser.onload = () => resolve(String(leser.result || ''))
    leser.onerror = () => reject(leser.error ?? new Error('Aufnahme nicht lesbar'))
    leser.readAsDataURL(blob)
  })
}

export function Messenger() {
  const { t } = useTranslation()

  /**
   * Wie eine Gruppe in der Oberfläche heisst.
   *
   * Seit Stufe 6 liefert der Server für `name` überall `null` — er kennt den
   * Namen nicht mehr. `benenneGruppen` setzt ihn aus dem versiegelten örtlichen
   * Speicher und aus dem verschlüsselten Gruppenblock wieder ein; bleibt er
   * trotzdem leer, ist der Schlüssel noch nicht da (frisches Gerät, verschlossener
   * Messenger). Dann steht hier eine ehrliche Überschrift statt eines leeren
   * Platzes — dieselbe Linie wie `social.invite.sealed` bei der Einladungskarte.
   */
  const gruppenTitel = useCallback(
    (g: { name?: string | null } | null | undefined): string =>
      g?.name?.trim() || t('messenger.groupSealed'),
    [t],
  )

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

  const {
    friends,
    groups,
    setGroups,
    teamMembers,
    publicUsers,
    directChats,
    stories,
    setStories,
    laden: loadData,
  } = useKontaktdaten(user?.id || 0, messengerGesperrt)

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
  const sicherheitsnummern = useSicherheitsnummern(activeContact?.userId)
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

  // Group Permissions & Delete Modal State
  const [isGroupPermissionsOpen, setIsGroupPermissionsOpen] = useState(false)
  const [groupToDelete, setGroupToDelete] = useState<ChatGroupItem | null>(null)
  const [isDeletingGroup, setIsDeletingGroup] = useState(false)

  // Camera & Attachments
  const [isCameraModalOpen, setIsCameraModalOpen] = useState(false)
  const [isDragOver, setIsDragOver] = useState(false)
  const [stagedFile, setStagedFile] = useState<FileAttachment | null>(null)
  const docInputRef = useRef<HTMLInputElement>(null)

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

  const [isCalendarPickerOpen, setIsCalendarPickerOpen] = useState(false)
  const [userEvents, setUserEvents] = useState<CalendarEventItem[]>([])

  const [selectedImage, setSelectedImage] = useState<ImageAttachment | null>(null)
  const [viewingImage, setViewingImage] = useState<string | null>(null)
  const [isSendPhotoOpen, setIsSendPhotoOpen] = useState(false)
  const [pendingPhotoToSend, setPendingPhotoToSend] = useState<ImageAttachment | null>(null)

  // Stickers / Emojis
  const [isStickerPickerOpen, setIsStickerPickerOpen] = useState(false)
  const [stickerTab, setStickerTab] = useState<PickerReiter>('stickers')

  // Group creation modal
  const [isCreateGroupOpen, setIsCreateGroupOpen] = useState(false)

  const [isVideoNoteRecording, setIsVideoNoteRecording] = useState(false)
  const tonWiedergabe = useSprachwiedergabe()

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
  /** Kurz aufleuchtende Zielzeile nach einem Sprung. */
  const [hervorgehoben, setHervorgehoben] = useState<string | null>(null)
  /** Die Nachricht, die in dieser Gruppe oben klebt. */
  const [angeheftet, setAngeheftet] = useState<ChatMessage | null>(null)
  /** Verfallsfrist dieses Chats in Sekunden, 0 = aus. */
  const [verfallSekunden, setVerfallSekunden] = useState(0)
  const [verfallOffen, setVerfallOffen] = useState(false)

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

  const entwuerfe = useEntwuerfe()

  /** Ob gerade ein Versand läuft. Siehe `handleSendMessage`. */
  const sendeLaeuft = useRef(false)

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
        }).catch(() => {})
      }
    } else {
      if (lastTypingSentRef.current > 0) {
        lastTypingSentRef.current = 0
        void sendTypingSignal({
          blind_mailbox_id: blindMailboxId,
          status: 'idle',
        }).catch(() => {})
      }
    }

    entwuerfe.merke(blindMailboxId, text)
  }

  const currentUserId = user?.id || 0

  /*
   * Jede bekannte Mailbox beim Strom anmelden, nicht nur die offene.
   *
   * Der Server schlägt seit Stufe 4 nicht mehr nach, wer zu einer Gruppe
   * gehört — er stellt an die Abonnenten einer Mailbox zu und sonst an
   * niemanden. Ohne diese Stelle erführe man von einer Gruppennachricht erst
   * beim Öffnen genau dieses Gesprächs, und auf dem geschlossenen Tab nie.
   *
   * Der Abdruck statt der Listen selbst: `groups` und `directChats` sind bei
   * jedem Abruf neue Felder, auch wenn sich nichts geändert hat. An ihnen zu
   * hängen hiesse, bei jedem Abruf erneut über alle Gespräche zu laufen und
   * die Ablage zu lesen.
   */
  const gespraechsAbdruck = useMemo(
    () =>
      [...groups.map((g) => `g${g.id}`), ...directChats.map((c) => `d${c.other_user_id}`)]
        .sort()
        .join(','),
    [groups, directChats],
  )

  useEffect(() => {
    if (!currentUserId || !gespraechsAbdruck) return
    void (async () => {
      /*
       * Die Gegenstellen kommen aus `gespraechsPartner()` und nicht aus
       * `directChats`. Der Unterschied sind die noch **namenlosen** Einträge:
       * ein Chatgeheimnis bringt eine Konto-Id mit, den Namen holt die
       * Kontaktliste später nach. Bis dahin steht das Gespräch nicht in
       * `directChats` — abonniert werden muss es trotzdem, sonst kommt die
       * erste Nachricht des neuen Gegenübers nirgends an.
       */
      const partner = await gespraechsPartner().catch(() => [] as number[])
      await abonniereBekannteGespraeche(
        currentUserId,
        groups.map((g) => g.id),
        partner,
      )
    })()
    // `groups`/`directChats` bewusst nicht in der Liste: der Abdruck ist ihr
    // Inhalt, und die Felder selbst wechseln bei jedem Abruf die Identität.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentUserId, gespraechsAbdruck])

  // Identität des Kontos auflösen und Klartextreste aus der Altzeit entfernen.
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

  const handleStoryCreated = (story: ChatStoryItem) => {
    setStories((prev) => [story, ...prev])
    toast.success(t('messenger.storyPublished'))
  }

  const handleStoryDeleted = (storyId: number) => {
    setStories((prev) => prev.filter((s) => s.id !== storyId))
    toast.success(t('messenger.storyDeleted'))
  }

  // Handle public invite link join if inviteCode param is present
  useEffect(() => {
    if (!inviteCode || !currentUserId) return
    let active = true

    /*
     * Der Schlüssel steht in `location.hash` und wird hier gelesen, bevor das
     * `navigate` weiter unten ihn wegräumt. Er kam nie beim Server an — der
     * Browser schickt nichts hinter der Raute —, und genau deshalb ist er die
     * einzige Stelle, an der der Name dieser Gruppe zu holen ist.
     */
    const schluessel = schluesselAusLink(window.location.hash)

    joinGroupByInvite(inviteCode)
      .then(async (joinedGroup) => {
        if (!active) return
        let karte: EinladungsInhalt | null = null
        if (schluessel) {
          karte = await getGroupInviteInfo(inviteCode)
            .then((daten) => lieseEinladungsKarte(daten.invite_card, schluessel, inviteCode))
            .catch(() => null)
        }
        const benannt = await uebernimmEinladung(joinedGroup, karte)
        if (!active) return
        toast.success(t('messenger.groupJoined', { name: gruppenTitel(benannt) }))
        setActiveGroup(benannt)
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

  /**
   * Übernimmt Name, Beschreibung und Logo einer Einladungskarte in den
   * versiegelten örtlichen Speicher und gibt die so benannte Gruppe zurück.
   *
   * Ohne Karte passiert nichts weiter — die Gruppe heisst dann bis zur ersten
   * Nachricht „Verschlüsselte Gruppe", und das ist ehrlicher als ein Name, den
   * der Server geraten hätte.
   *
   * Nicht in den Gruppenblock geschrieben: wer gerade beitritt, hat das
   * Gruppengeheimnis noch nicht, und ein neu gebauter Block überschriebe den
   * bestehenden samt Rollen. Der Block bleibt Sache der Mitglieder.
   */
  const uebernimmEinladung = async (
    gruppe: ChatGroupItem,
    karte?: EinladungsInhalt | null,
  ): Promise<ChatGroupItem> => {
    if (!karte) return gruppe
    await merkeGruppenName(gruppe.id, {
      name: karte.name,
      beschreibung: karte.beschreibung,
      logo: karte.logo,
    }).catch(() => {})
    return {
      ...gruppe,
      name: gruppe.name ?? karte.name ?? null,
      description: gruppe.description ?? karte.beschreibung ?? null,
      avatar_url: gruppe.avatar_url ?? karte.logo ?? null,
    }
  }

  /** Gruppenlogo: Auswahl, Prüfung, Ablage im verschlüsselten Gruppenblock. */
  const gruppenLogoInputRef = useRef<HTMLInputElement | null>(null)
  const [logoLaedt, setLogoLaedt] = useState(false)

  /*
   * Das Logo geht nicht mehr auf die Platte des Servers.
   *
   * Bis Stufe 6 lud `POST /social/groups/{id}/avatar` die Datei hoch und der
   * Server lieferte sie unter einer rate-URL an jeden aus, der sie kannte —
   * ohne Anmeldung. Ein Bild sagt über eine Gruppe oft mehr als ihr Name.
   *
   * Jetzt schrumpft `logoAlsDatenUrl` das Bild auf 128 Pixel und macht eine
   * Data-URL daraus; die landet im verschlüsselten Gruppenblock und im
   * versiegelten örtlichen Speicher. Der Grössenriegel darunter ist damit kein
   * Upload-Limit mehr, sondern der Schutz davor, ein 5-MB-Bild überhaupt erst
   * zu dekodieren.
   */
  const handleGruppenLogo = async (datei: File | undefined) => {
    if (!datei || !activeGroup || !currentUserId) return
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
      const logo = await logoAlsDatenUrl(datei)
      if (!logo) {
        toast.error(t('messenger.logoFailed'))
        return
      }
      const gruppenId = activeGroup.id
      const mailbox = await deriveGroupBlindMailboxId(gruppenId)
      const gespeichert = await sichereGruppenAnsicht(
        {
          groupId: gruppenId,
          blindMailboxId: mailbox,
          eigeneId: currentUserId,
          mitglieder: (activeGroup.members ?? []).map((m) => m.user_id),
          istEigentuemer: activeGroup.role === 'owner',
        },
        { logo },
      )
      // Anzeigen auch dann, wenn der Block nicht zu schreiben war: der
      // örtliche Speicher hat das Logo (`sichereGruppenAnsicht` merkt es
      // immer), und dieses Gerät zeigt es ab jetzt.
      setActiveGroup((aktuell) =>
        aktuell?.id === gruppenId ? { ...aktuell, avatar_url: logo } : aktuell,
      )
      setGroups((vorher) =>
        vorher.map((g) => (g.id === gruppenId ? { ...g, avatar_url: logo } : g)),
      )
      toast.success(gespeichert ? t('messenger.logoUpdated') : t('messenger.logoLocalOnly'))
    } catch {
      toast.error(t('messenger.logoFailed'))
    } finally {
      setLogoLaedt(false)
    }
  }

  /**
   * Beitritt über die Einladungskarte im Chat.
   *
   * `karte` ist der bereits geöffnete Inhalt der Vorschau. Er ist die einzige
   * Quelle für den Namen: der Server kennt ihn nicht, und den verschlüsselten
   * Gruppenblock kann dieses Gerät erst lesen, wenn es das Gruppengeheimnis
   * hat — das kommt mit der ersten Nachricht, nicht mit dem Beitritt.
   */
  const handleJoinByInviteCode = async (code: string, karte?: EinladungsInhalt | null) => {
    try {
      const joinedGroup = await joinGroupByInvite(code)
      const benannt = await uebernimmEinladung(joinedGroup, karte)
      toast.success(t('messenger.groupJoined', { name: gruppenTitel(benannt) }))
      setActiveGroup(benannt)
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

  /*
   * Namenlose Gespräche benennen.
   *
   * Ein zugestelltes Chatgeheimnis bringt eine Konto-Id und keinen Namen — ein
   * Anzeigename im Steuerumschlag wäre ein Feld, das der Absender frei wählt,
   * und damit der Weg, sich in einer fremden Kontaktliste als jemand anderes
   * auszugeben. Der Name kommt deshalb aus der Kontaktliste dieses Geräts, und
   * zwar sobald sie geladen ist.
   *
   * `loadData()` danach: erst damit wandert der frisch gefundene Name auch in
   * `directChats` und wird sichtbar.
   */
  useEffect(() => {
    if (contactsList.length === 0) return
    void fuelleNamenNach(
      contactsList.map((c) => ({
        userId: c.userId,
        username: c.username,
        avatarUrl: c.avatarUrl,
      })),
    )
      .then((geaendert) => {
        if (geaendert) void loadData()
      })
      .catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [contactsList.length])

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
          gruppenTitel(g).toLowerCase().includes(q) ||
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
          name: gruppenTitel(g),
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

  const storyAnsicht = useStoryAnsicht(stories, currentUserId, contactsList)

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

  // When active contact or active group changes, derive mailbox ID
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
        istEigentuemer: activeGroup.role === 'owner',
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
   * Schreibt die Systemzeile zu einem abgewiesenen Sitzungsaufbau.
   *
   * Jeder Aufbau meldet sich nur einmal — er bleibt gemerkt. Gedrosselt wird
   * trotzdem, je Gerät und Minute: wer Fälschungen in Serie schickt, soll den
   * Verlauf nicht mit Warnungen zuschütten können.
   */
  const aufbauMeldungRef = useRef<Map<string, number>>(new Map())
  const aufbauAbgelehnt = useCallback((geraet: string) => {
    const jetzt = Date.now()
    const zuletzt = aufbauMeldungRef.current.get(geraet) || 0
    if (jetzt - zuletzt < 60_000) return
    aufbauMeldungRef.current.set(geraet, jetzt)

    const zeile: ChatMessage = {
      id: jetzt,
      clientUuid: `sys-dr-abgewiesen-${geraet}-${jetzt}`,
      senderId: 0,
      text: t('messenger.sessionSetupRejected'),
      createdAt: new Date().toISOString(),
      isSelf: false,
      isSystem: true,
    }
    setMessages((prev) => sortMessagesChronologically([...prev, zeile]))
  }, [t])

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

  // Geräteverzeichnis: wer die Liste holt, prüft sie (`vertrauteGeraete`) und
  // meldet, was ihm auffällt. Das eigene Konto gehört dazu — ein Gerät, das
  // jemand mit deinem Passwort einträgt, sollst zuerst du sehen.
  useEffect(() => {
    if (!activeContact?.userId) return
    geraeteVon(activeContact.userId).catch(() => {})
  }, [activeContact?.userId])

  useEffect(() => {
    if (!currentUserId) return
    geraeteVon(currentUserId).catch(() => {})
  }, [currentUserId])

  // Ein wartendes Gerät bekommt nichts. Ohne Hinweis sähe das aus wie ein
  // kaputter Messenger — also steht oben, was zu tun ist.
  const [geraetWartet, setGeraetWartet] = useState(eigenesGeraetFreigegeben() === false)
  useEffect(() => onEigeneFreigabe((frei) => setGeraetWartet(frei === false)), [])

  useEffect(() => {
    const abbestellen = onSchluesselWarnung((ev) => {
      if (currentUserId === ev.userId) {
        const text = {
          neues_geraet: t('messenger.ownNewDevice'),
          unbestaetigt: t('messenger.ownUnverifiedDeviceWarning'),
          konto_neustart: t('messenger.ownAccountResetWarning'),
        }[ev.typ]
        zeigeSystemzeile(text)
        return
      }
      let name = ''
      if (activeContact && activeContact.userId === ev.userId) {
        name = activeContact.username || t('messenger.thisContact')
      } else if (activeGroup) {
        const member = (activeGroup.members ?? []).find((m) => Number(m.user_id) === ev.userId)
        if (member) name = member.username || t('messenger.thisContact')
      }
      if (!name) return
      const text = {
        neues_geraet: t('messenger.newDeviceDetected', { name }),
        unbestaetigt: t('messenger.unverifiedDeviceWarning', { name }),
        konto_neustart: t('messenger.accountResetWarning', { name }),
      }[ev.typ]
      zeigeSystemzeile(text)
    })
    return abbestellen
  }, [activeContact, activeGroup, currentUserId, t, zeigeSystemzeile])

  const konversation = useKonversation({
    ziel: gespraechsZiel,
    eigeneId: currentUserId,
    identitaetRef: identityRef,
    meldeSitzungsbruch: sitzungNeuGemeldet,
    meldeAufbauAbgelehnt: aufbauAbgelehnt,
  })
  const blindMailboxId = konversation.blindMailboxId

  /*
   * Das offene Gespräch in den versiegelten örtlichen Speicher.
   *
   * Seit Stufe 6b weiss der Server nicht mehr, mit wem dieses Konto schreibt —
   * `direct_chats` nennt keine Menschen, und `GET /social/direct-chats` ist
   * entfernt. Diese Zeile ist der Ersatz: wer ein Gespräch öffnet, merkt es
   * sich selbst.
   *
   * Nur Kontakte, die nicht ohnehin in der Kontaktliste stehen, brauchen das
   * eigentlich — gemerkt wird trotzdem jeder. Ein Freund, der später keiner
   * mehr ist, verschwände sonst samt seinem Chatverlauf aus der Liste, und ein
   * Verlauf ohne Zeile ist ein Verlauf, den niemand mehr findet.
   */
  useEffect(() => {
    if (!activeContact) return
    void merkeGespraech(activeContact.userId, {
      username: activeContact.username,
      avatarUrl: activeContact.avatarUrl,
      blindMailboxId: blindMailboxId,
    }).catch(() => {})
  }, [activeContact?.userId, activeContact?.username, blindMailboxId])

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
    chatSuche.schliesse()
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

  // Load and decrypt messages (non-flickering background sync + real-time)
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
   * Durchgesetzt wird das **beim Empfänger** (siehe `umschlagAuswertung.ts`), nicht am
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

      const {
        decryptedList,
        aenderungen,
        loeschungen,
        reaktionen,
        maxPartnerReadId,
        maxPartnerDeliveredId,
        maxIncomingId,
      } = await werteUmschlaegeAus(gelesen, {
        currentMid,
        currentUserId,
        activeContact,
        activeGroup,
        gruppenrechteVon,
        t,
        quittiertGelesen: (bis) => {
          maxPartnerReadIdRef.current = Math.max(maxPartnerReadIdRef.current, bis)
        },
        quittiertZugestellt: (bis) => {
          maxPartnerDeliveredIdRef.current = Math.max(maxPartnerDeliveredIdRef.current, bis)
        },
        markAsRead,
        setVerfallSekunden,
        zeigeSystemzeile,
      })

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
      if (mid) ziele.push({ blindMailboxId: mid, groupId: g.id, name: gruppenTitel(g), istGruppe: true })
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
        istEigentuemer: activeGroup.role === 'owner',
      },
      darfSchreiben,
    )
      .then((lesung) => {
        if (abgebrochen) return
        setGruppenRollenZustand(lesung.art === 'zustand' ? lesung.zustand : null)
        /*
         * Derselbe Block trägt den Namen. Er hier mitzunehmen kostet keinen
         * zweiten Abruf — und das ist der Weg, auf dem ein frisch
         * eingerichtetes Gerät überhaupt erfährt, wie die Gruppe heisst: sein
         * örtlicher Speicher ist leer, und der Server weiss es nicht mehr.
         *
         * Das Ergebnis wandert bewusst nicht direkt in `setGroups`: die Liste
         * holt es beim nächsten `loadData` über `benenneGruppen`, und damit
         * gibt es weiterhin genau eine Stelle, an der Gruppen benannt werden.
         */
        if (lesung.art === 'zustand') {
          const ansicht = {
            name: lesung.zustand.name,
            beschreibung: lesung.zustand.beschreibung,
            logo: lesung.zustand.logo,
          }
          // `loadData` nur, wenn der Block etwas weiss, das die Liste noch
          // nicht zeigt. Sonst löste jedes Öffnen einer Gruppe einen vollen
          // Abruf aus, und zwar genau auf den Geräten, die ihn nicht brauchen.
          const neu = Boolean(ansicht.name) && ansicht.name !== (activeGroup.name ?? null)
          void merkeGruppenName(activeGroup.id, ansicht)
            .then(() => {
              if (!abgebrochen && neu) void loadData()
            })
            .catch(() => {})
        }
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
   * Verglichen wird, was Rechte trägt — und seit Stufe 6 auch Name,
   * Beschreibung und Logo. Die kommen jetzt aus dem verschlüsselten
   * Gruppenblock und treffen daher **nach** dem Öffnen ein: auf einem frisch
   * eingerichteten Gerät stünde sonst für immer „Verschlüsselte Gruppe" über
   * einem Chat, dessen Name längst in der Liste daneben steht. Sie ändern sich
   * selten genug, um das Vergleichen nicht teuer zu machen.
   *
   * Raumzeichen und Anrufzustand bleiben draussen; die ändern sich im
   * Sekundentakt, und darauf zu reagieren hiesse, die Ansicht ständig neu zu
   * setzen, ohne dass sich etwas geändert hätte.
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
        g.can_set_disappearing_messages ?? null,
        g.name ?? null,
        g.description ?? null,
        g.avatar_url ?? null,
        (g.members ?? []).map((m) => [m.user_id, m.role, m.permissions ?? null]),
      ])
    if (rechtekennung(frisch) !== rechtekennung(activeGroup)) setActiveGroup(frisch)
  }, [groups, activeGroup])

  /** Ob ich in dieser Gruppe anheften darf — vom Server entschieden. */
  const darfAnheften = Boolean(activeGroup?.can_pin_messages)

  /**
   * Ob ich die Verfallsfrist dieses Chats umstellen darf.
   *
   * Im Direktchat immer, dort gibt es keine Rollen. In der Gruppe entscheidet
   * der Server — dieselbe Rechnung, deren Marke die anderen Geräte am Mitglied
   * prüfen. Nicht `gruppenrechteVon`: der verschlüsselte Rollenblock gibt
   * dieses Recht beim Empfänger nicht, und eine Auswahl, die bei allen anderen
   * folgenlos verpufft, wäre eine Irreführung. Hier sperren ist die Höflichkeit,
   * dort verwerfen die Wirkung.
   */
  const darfVerfallStellen = !activeGroup || Boolean(activeGroup.can_set_disappearing_messages)

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
    // Ohne das Recht ist die Auswahl gar nicht erst zu öffnen. Das hier fängt
    // den Fall ab, dass es entzogen wurde, während sie offen stand: gesendet
    // würde eine Umstellung, die jedes andere Gerät verwirft.
    if (!darfVerfallStellen) {
      setVerfallOffen(false)
      toast.error(t('messenger.retentionNoRight'))
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

  const chatSuche = useChatSuche(blindMailboxId, springeZu)
  const ueberall = useUeberallAnsicht(currentUserId, groups)

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
    ueberall.schliesse()
  }, [activeContact?.userId, activeGroup?.id])

  /** Öffnet einen Treffer aus der Ansicht über alle Chats. */
  const oeffneTreffer = async (treffer: Treffer) => {
    ueberall.schliesse()
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

  // Send message (text, note, cal, img, audio, file, sticker)
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
        err instanceof DrZustellungFehlgeschlagenError ||
        err instanceof DrGeraetNichtEingetragenError

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
  }

  // Die fertige Sprachnachricht geht denselben Weg wie jede andere Nachricht.
  const aufnahme = useSprachaufnahme({
    blindMailboxId,
    onFertig: (audio) => void handleSendMessage({ text: '', audio }),
  })

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

  // Handle Create Group. `true` heisst angelegt; dann leert der Dialog sein Formular.
  const handleCreateGroup = async (name: string, beschreibung: string | null): Promise<boolean> => {
    try {
      // Der Server bekommt nur die Bitte, eine Gruppe anzulegen. Name und
      // Beschreibung gehen ihn nichts an.
      const roh = await createGroup()

      /*
       * Der Name geht zwei Wege, und beide braucht es.
       *
       * `merkeGruppenName` schreibt ihn sofort in den versiegelten örtlichen
       * Speicher — damit steht er in der Liste, bevor irgendein Netzaufruf
       * zurück ist. `sichereGruppenAnsicht` legt ihn zusätzlich in den
       * verschlüsselten Gruppenblock, damit ihn auch das nächste Gerät
       * bekommt.
       *
       * Der Block kann hier noch scheitern: eine frisch angelegte Gruppe hat
       * oft noch kein Geheimnis, das entsteht erst beim ersten Senden. Das ist
       * kein Grund, das Anlegen abzubrechen — der örtliche Speicher trägt den
       * Namen, und `sichereGruppenAnsicht` läuft beim nächsten Umbenennen
       * erneut.
       */
      await merkeGruppenName(roh.id, { name, beschreibung }).catch(() => {})
      const mailbox = await deriveGroupBlindMailboxId(roh.id).catch(() => null)
      if (mailbox && currentUserId) {
        void sichereGruppenAnsicht(
          {
            groupId: roh.id,
            blindMailboxId: mailbox,
            eigeneId: currentUserId,
            mitglieder: [currentUserId],
            istEigentuemer: true,
          },
          { name, beschreibung },
        ).catch(() => false)
      }

      const newGroup: ChatGroupItem = { ...roh, name, description: beschreibung }
      toast.success(t('messenger.groupCreated', { name }))
      setIsCreateGroupOpen(false)
      await loadData()
      setActiveGroup(newGroup)
      setActiveContact(null)
      return true
    } catch {
      toast.error(t('messenger.groupCreateFailed'))
      return false
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
  const handleCopyInviteLink = async (group: ChatGroupItem) => {
    if (!group.invite_code) {
      toast.error(t('messenger.inviteNoRight'))
      return
    }
    const url = `${window.location.origin}/chat/join/${group.invite_code}`

    /*
     * Die Vorschaukarte entsteht hier — beim Teilen, nicht beim Anlegen.
     *
     * Zwei Gründe. Erstens trägt sie dann genau den Stand, den der Absender
     * gerade sieht; eine beim Anlegen erzeugte Karte zeigte den Namen von
     * vorgestern. Zweitens hat die Gruppe beim Anlegen oft noch gar kein
     * Geheimnis: das entsteht, wenn der Eigentümer zum ersten Mal sendet.
     *
     * Ohne Geheimnis bleibt der Link, was er war — ohne Raute und ohne
     * Vorschau. Seit Stufe 6 gibt es keinen Klartext mehr, auf den er dafür
     * zurückfallen könnte; der Eingeladene sieht dann „Verschlüsselte
     * Einladung" und die Mitgliederzahl, sonst nichts.
     */
    let fertig = url
    try {
      const geheimnis = await gruppenGeheimnis(group.id)
      if (geheimnis) {
        const karte = await baueEinladungsKarte(geheimnis, group.invite_code, {
          name: group.name,
          beschreibung: group.description ?? null,
          // `avatar_url` trägt seit Stufe 6 bereits die Data-URL aus dem
          // Gruppenblock — nichts mehr nachzuladen. Der Prüfausdruck fängt
          // Altbestände ab, die noch eine Serveradresse enthalten: die wäre
          // für den Eingeladenen ohnehin nicht abrufbar.
          logo:
            group.avatar_url && group.avatar_url.startsWith('data:image/')
              ? group.avatar_url
              : null,
        })
        await setzeEinladungsKarte(group.id, karte)
        fertig = mitSchluessel(url, await einladungsschluesselAus(geheimnis))
      }
    } catch {
      // Eine Karte, die nicht zustande kommt, darf das Einladen nicht
      // aufhalten. Der Link ohne Raute funktioniert; nur die Vorschau fehlt.
    }

    navigator.clipboard.writeText(fertig)
    toast.success(t('messenger.inviteCopied'))
  }

  // Leave Group
  const handleLeaveGroup = async (group: ChatGroupItem) => {
    try {
      const name = gruppenTitel(group)
      await leaveGroup(group.id)
      // Wer draußen ist, braucht die Schlüssel nicht mehr — und soll sie auch
      // nicht behalten. Der Verlauf dieser Gruppe wird damit unlesbar, was
      // genau die Zusage ist, die ein Austritt geben soll.
      await verwirfGruppenSchluessel(group.id).catch(() => {})
      // Und den Namen dazu: er lag versiegelt auf diesem Gerät, aber er lag
      // da. Ein Austritt, der die Überschrift stehen lässt, ist keiner.
      await vergissGruppenName(group.id).catch(() => {})
      toast.success(t('messenger.groupLeft', { name }))
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
      const name = gruppenTitel(groupToDelete)
      await deleteGroup(groupToDelete.id)
      await verwirfGruppenSchluessel(groupToDelete.id).catch(() => {})
      await vergissGruppenName(groupToDelete.id).catch(() => {})
      toast.success(t('messenger.groupDeleted', { name }))
      setActiveGroup(null)
      setGroupToDelete(null)
      await loadData()
    } catch {
      toast.error(t('messenger.groupDeleteFailed'))
    } finally {
      setIsDeletingGroup(false)
    }
  }

  // Blockieren oder Aufheben aus dem Bestätigungsdialog, je nach heutigem Stand.
  const handleBlockConfirm = async () => {
    if (activeContact && isBlocked(activeContact.userId)) {
      await unblockUser(activeContact.userId)
      toast.success(t('social.contacts.unblocked', { name: activeContact.username }))
    } else if (activeContact) {
      await blockUser(activeContact.userId, activeContact.username, activeContact.avatarUrl)
      // Und aus der örtlichen Gesprächsliste. Seit Stufe 6b führt
      // sie dieses Gerät; bliebe die Zeile stehen, tauchte der
      // Blockierte weiter in der Kontaktliste auf und sein
      // Gespräch bliebe abonniert.
      await vergissGespraech(activeContact.userId).catch(() => {})
      toast.success(t('messenger.contactBlockedToast', { name: activeContact.username }))
    }
    setIsBlockConfirmOpen(false)
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
        name: gruppenTitel(activeGroup),
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

  /** Ein Chat aus der Liste geht auf: gelesen, und der Trenner merkt sich, wo man stand. */
  const oeffneAusListe = (mid: string | undefined) => {
    if (!mid) return
    ungelesenBeimOeffnen.current = unreadCounts[mid] || 0
    trennerGesetztFuer.current = null
    markAsRead(mid)
  }

  const zeichneGruppe = (g: ChatGroupItem) => {
    const gmid = groupMailboxMap[g.id]
    return (
      <GroupListItem
        key={`g-${g.id}`}
        gruppe={g}
        titel={gruppenTitel(g)}
        mid={gmid}
        ausgewaehlt={activeGroup?.id === g.id}
        entwurf={gmid ? entwuerfe.vorschau[gmid] || '' : ''}
        onOeffnen={() => {
          setActiveGroup(g)
          setActiveContact(null)
          oeffneAusListe(gmid)
        }}
        onAnheften={handleAnheftenChat}
        onArchivieren={handleArchivieren}
        onMenue={(mid, name) => setZeilenMenue({ mid, name })}
      />
    )
  }

  const zeichneKontakt = (c: ChatContact) => {
    const cmid = contactMailboxMap[c.userId]
    return (
      <ContactListItem
        key={c.listKey}
        kontakt={c}
        mid={cmid}
        ausgewaehlt={activeContact?.userId === c.userId}
        entwurf={cmid ? entwuerfe.vorschau[cmid] || '' : ''}
        onOeffnen={() => {
          setActiveContact(c)
          setActiveGroup(null)
          oeffneAusListe(cmid)
        }}
        onAnheften={handleAnheftenChat}
        onArchivieren={handleArchivieren}
        onMenue={(mid, name) => setZeilenMenue({ mid, name })}
      />
    )
  }

  const storyIch = { avatarUrl: user?.avatar_url, username: user?.username }

  /** Ein Anruf an den offenen Kontakt; nur unter Freunden angeboten. */
  const starteAnruf = async (art: 'audio' | 'video') => {
    if (!activeContact) return
    try {
      await useCallStore.getState().initiateCall(
        {
          userId: activeContact.userId,
          username: activeContact.username,
          avatarUrl: activeContact.avatarUrl,
        },
        art,
      )
    } catch (err: any) {
      toast.error(err?.message || t('messenger.callStartFailedSingle'))
    }
  }

  const sendeFreundschaftsanfrage = async () => {
    if (!activeContact) return
    try {
      await sendFriendRequest(activeContact.username)
      toast.success(t('messenger.friendRequestSent', { name: activeContact.username }))
    } catch (err: any) {
      toast.error(err?.message || t('messenger.friendRequestFailed'))
    }
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
      {geraetWartet && (
        <button
          type="button"
          onClick={() => navigate('/profile?tab=devices')}
          className="shrink-0 border-b border-status-warning/30 bg-status-warning/10 px-4 py-2 text-left text-sm text-on-surface"
        >
          {t('messenger.thisDevicePending')}
        </button>
      )}
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
          <MessengerModeNav modus={mobileNavTab} onModus={setMobileNavTab} hatStories={stories.length > 0} />

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

            {mobileNavTab === 'chats' && (
              <ContactFilterTabs
                filter={filterTab}
                onFilter={setFilterTab}
                gruppenAnzahl={groups.length}
                kontakte={contactsList}
              />
            )}
          </div>

          {mobileNavTab === 'chats' && !searchQuery.trim() && (
            <StoriesCarouselBar
              ich={storyIch}
              eigeneStories={storyAnsicht.eigene}
              freunde={storyAnsicht.freunde}
              onOeffnen={(s) => storyAnsicht.betrachter.oeffne(s)}
              onErstellen={storyAnsicht.erstellung.mitText}
            />
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
                    onClick={() => void ueberall.oeffne('markiert')}
                    className="w-full min-h-11 px-2.5 flex items-center gap-2.5 rounded-xl text-left text-xs text-on-surface-variant hover:bg-surface-container-high/60 transition-colors"
                  >
                    <Star className="w-4 h-4 text-status-warning shrink-0" />
                    <span>{t('messenger.markedMessages')}</span>
                  </button>
                  <button
                    type="button"
                    onClick={() => void ueberall.oeffne('anMich')}
                    className="w-full min-h-11 px-2.5 flex items-center gap-2.5 rounded-xl text-left text-xs text-on-surface-variant hover:bg-surface-container-high/60 transition-colors"
                  >
                    <AtSign className="w-4 h-4 text-primary shrink-0" />
                    <span>@ und Antworten an mich</span>
                  </button>
                  {searchQuery.trim().length > 1 && (
                    <button
                      type="button"
                      onClick={() => void ueberall.oeffne('suche', searchQuery)}
                      className="w-full min-h-11 px-2.5 flex items-center gap-2.5 rounded-xl text-left text-xs text-primary hover:bg-surface-container-high/60 transition-colors"
                    >
                      <Search className="w-4 h-4 shrink-0" />
                      <span className="truncate">„{searchQuery.trim()}" in Nachrichten</span>
                    </button>
                  )}
                </div>
              </>
            )}

            {mobileNavTab === 'updates' && (
              <StatusUpdatesView
                ich={storyIch}
                eigeneStories={storyAnsicht.eigene}
                freunde={storyAnsicht.freunde}
                kontakte={contactsList}
                onOeffnen={(s) => storyAnsicht.betrachter.oeffne(s)}
                onErstellen={storyAnsicht.erstellung.mitText}
                onChat={(c) => {
                  setActiveContact(c)
                  setActiveGroup(null)
                }}
              />
            )}

            {mobileNavTab === 'community' && (
              <CommunityView
                gruppen={groups}
                onGruppe={(g) => {
                  setActiveGroup(g)
                  setActiveContact(null)
                }}
                onNeueGruppe={() => setIsCreateGroupOpen(true)}
                onEinladungKopieren={handleCopyInviteLink}
              />
            )}
          </div>

          {!isChatOpen && <MessengerBottomNav modus={mobileNavTab} onModus={setMobileNavTab} />}
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
              {auswahlModus && (
                <ChatSelectionBar
                  anzahl={gewaehlteUuids.length}
                  loeschenMoeglich={gewaehlteNachrichten().some((m) => m.isSelf && !m.isDeleted)}
                  onBeenden={beendeAuswahl}
                  onWeiterleiten={() => {
                    const auswahl = gewaehlteNachrichten().filter(istWeiterleitbar)
                    if (!auswahl.length) {
                      toast.error(t('messenger.nothingToForwardPlural'))
                      return
                    }
                    setWeiterzuleiten(auswahl)
                  }}
                  onKopieren={() => void handleAuswahlKopieren()}
                  onMarkieren={() => {
                    for (const m of gewaehlteNachrichten()) handleMarkieren(m)
                    beendeAuswahl()
                  }}
                  onLoeschen={() => void handleAuswahlLoeschen()}
                />
              )}

              <ChatHeader
                verborgen={auswahlModus || chatSuche.offen}
                titel={(activeGroup ? gruppenTitel(activeGroup) : activeContact?.username) ?? ''}
                gruppenBild={activeGroup?.avatar_url ? apiUrl(activeGroup.avatar_url) : null}
                stumm={Boolean(blindMailboxId && isChatMuted(blindMailboxId))}
                blockiert={Boolean(activeContact && isBlocked(activeContact.userId))}
                onZurueck={() => {
                  setActiveContact(null)
                  setActiveGroup(null)
                }}
                gruppenanruf={
                  activeGroup
                    ? { erlaubt: groupCallPermissions.canStart, onStarten: () => handleStartGroupCall(false) }
                    : undefined
                }
                onSprachanruf={activeContact?.isFriend ? () => void starteAnruf('audio') : undefined}
                onFreundschaftsanfrage={
                  activeContact && !activeContact.isFriend ? () => void sendeFreundschaftsanfrage() : undefined
                }
                onSuche={chatSuche.oeffne}
                menue={(schliessen) => (
                  <ChatActionsMenu
                    schliessen={schliessen}
                    stumm={blindMailboxId ? isChatMuted(blindMailboxId) : null}
                    kontakt={
                      activeContact
                        ? { istFreund: activeContact.isFriend, blockiert: isBlocked(activeContact.userId) }
                        : null
                    }
                    gruppe={
                      activeGroup
                        ? {
                            hatEinladung: Boolean(activeGroup.invite_code),
                            istEigentuemer: activeGroup.owner_user_id === currentUserId,
                            istAdmin: activeGroup.role === 'admin',
                            logoLaedt,
                          }
                        : null
                    }
                    verfallStufe={stufenLabel(verfallSekunden > 0 ? verfallSekunden : 0, t)}
                    verfallErlaubt={darfVerfallStellen}
                    onStumm={() => setIsMuteModalOpen(true)}
                    onSicherheitsnummer={() => sicherheitsnummern.setOffen(true)}
                    onVerfall={() => setVerfallOffen(true)}
                    onVideoanruf={() => void starteAnruf('video')}
                    onHintergrund={() => setIsWallpaperModalOpen(true)}
                    onEinladung={() => activeGroup && handleCopyInviteLink(activeGroup)}
                    onLogo={() => gruppenLogoInputRef.current?.click()}
                    onRollen={() => setIsGroupPermissionsOpen(true)}
                    onLoeschen={() => activeGroup && handleDeleteGroup(activeGroup)}
                    onVerlassen={() => activeGroup && handleLeaveGroup(activeGroup)}
                    onBlockieren={() => setIsBlockConfirmOpen(true)}
                  />
                )}
              />

              {angeheftet && !auswahlModus && !chatSuche.offen && (
                <ChatPinnedBar
                  text={angeheftet.text || auszugFuerZitat(angeheftet)}
                  onOeffnen={() => angeheftet.clientUuid && springeZu(angeheftet.clientUuid)}
                  onLoesen={darfAnheften ? () => void handleAnheften(angeheftet) : undefined}
                />
              )}

              <ChatTimeline
                scrollRef={scrollContainerRef}
                endeRef={messagesEndRef}
                nachrichten={messages}
                laedt={loadingMessages}
                trennerId={trennerId}
                aktivitaet={partnerActivity}
                istGruppe={Boolean(activeGroup)}
                zeichneNachricht={(msg) => (
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
                    ton={tonWiedergabe}
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
                )}
              />

              {selectedImage && <StagedImageBar bild={selectedImage} onEntfernen={() => setSelectedImage(null)} />}
              {stagedFile && <StagedFileBar datei={stagedFile} onEntfernen={() => setStagedFile(null)} />}
              {editingMessage && (
                <EditingBanner
                  text={editingMessage.text}
                  onAbbrechen={() => {
                    setEditingMessage(null)
                    setInputText('')
                  }}
                />
              )}

              {/* Nach unten. Schwebt über der Eingabe, nicht darunter, und weicht
                  dem Zitatkopf aus, wenn beide gleichzeitig da sind. */}
              {weitOben && !chatSuche.offen && (
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
              {chatSuche.offen && (
                <VerlaufSuchleiste
                  onSchliessen={chatSuche.schliesse}
                  onSuchen={chatSuche.suchen}
                  trefferAnzahl={chatSuche.treffer.length}
                  aktuellerTreffer={chatSuche.index}
                  onVor={() => chatSuche.blaettere(1)}
                  onZurueck={() => chatSuche.blaettere(-1)}
                  gesperrt={chatSuche.gesperrt}
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
                  <BlockedNotice onAufheben={() => void unblockUser(activeContact.userId)} />
                ) : aufnahme.laeuft ? (
                  <VoiceRecordingBar
                    durationSeconds={aufnahme.dauer}
                    statusLabel={t('messenger.voiceRecording')}
                    stream={aufnahme.stream}
                    variant="danger"
                    onCancel={() => aufnahme.beende(false)}
                    onConfirm={() => aufnahme.beende(true)}
                    cancelLabel="Abbrechen"
                    confirmLabel="Senden"
                    cancelIcon={<Trash2 className="w-3.5 h-3.5" />}
                    confirmIcon={<Send className="w-3.5 h-3.5" />}
                  />
                ) : (
                  <>
                    {isStickerPickerOpen && (
                      <StickerEmojiPicker
                        reiter={stickerTab}
                        onReiter={setStickerTab}
                        onSticker={(stk) => {
                          void handleSendMessage({ sticker: stk })
                          setIsStickerPickerOpen(false)
                        }}
                        onEmoji={(emoji) => setInputText((prev) => prev + emoji)}
                        onSchliessen={() => setIsStickerPickerOpen(false)}
                      />
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
                            <MentionSuggestions vorschlaege={erwaehnungsVorschlaege} onWaehlen={waehleErwaehnung} />
                            {antwortAuf && <ChatReplyBar antwort={antwortAuf} onVerwerfen={() => setAntwortAuf(null)} />}
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

                            <AttachMenu
                              erlaubt={darfAnhaengen}
                              onKamera={() => setIsCameraModalOpen(true)}
                              onFoto={() => fileInputRef.current?.click()}
                              onDokument={() => docInputRef.current?.click()}
                              onNotiz={() => void handleOpenNotePicker()}
                              onTermin={() => void handleOpenCalendarPicker()}
                            />
                          </>
                        }
                        rightActions={
                          <ComposerSendActions
                            hatInhalt={Boolean(inputText.trim() || selectedImage || stagedFile)}
                            sendet={sending}
                            onVideonotiz={() => setIsVideoNoteRecording(true)}
                            onSprachnachricht={aufnahme.starte}
                          />
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
        offen={ueberall.art !== 'aus'}
        titel={
          ueberall.art === 'markiert'
            ? t('messenger.markedMessages')
            : ueberall.art === 'anMich'
              ? t('messenger.mentionsAndReplies')
              : t('messenger.searchFor', { frage: ueberall.frage })
        }
        leerText={
          ueberall.art === 'markiert'
            ? t('messenger.nothingMarkedYet')
            : ueberall.art === 'anMich'
              ? t('messenger.nothingMentioned')
              : t('messenger.noChatHasThisText')
        }
        chats={ueberall.chats}
        verzeichnis={mailboxDirectory}
        gesperrt={ueberall.gesperrt}
        laeuft={ueberall.laeuft}
        onSchliessen={ueberall.schliesse}
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

      <CreateGroupDialog
        open={isCreateGroupOpen}
        onOpenChange={setIsCreateGroupOpen}
        onCreate={handleCreateGroup}
      />

      <NotePickerDialog
        open={isNotePickerOpen}
        onOpenChange={setIsNotePickerOpen}
        notes={userNotes}
        onPick={(n) => {
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
      />

      <CalendarPickerDialog
        open={isCalendarPickerOpen}
        onOpenChange={setIsCalendarPickerOpen}
        events={userEvents}
        onPick={(ev) => {
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
      />

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

      <SendPhotoDialog
        open={isSendPhotoOpen}
        onOpenChange={setIsSendPhotoOpen}
        groups={filteredGroups}
        contacts={filteredContacts}
        onPickGroup={(g) => {
          setActiveGroup(g)
          setActiveContact(null)
          if (pendingPhotoToSend) setSelectedImage(pendingPhotoToSend)
          setPendingPhotoToSend(null)
          setIsSendPhotoOpen(false)
        }}
        onPickContact={(c) => {
          setActiveContact(c)
          setActiveGroup(null)
          if (pendingPhotoToSend) setSelectedImage(pendingPhotoToSend)
          setPendingPhotoToSend(null)
          setIsSendPhotoOpen(false)
        }}
      />

      {/* Create Story Modal */}
      <CreateStoryModal
        open={storyAnsicht.erstellung.offen}
        onOpenChange={storyAnsicht.erstellung.setOffen}
        onCreated={handleStoryCreated}
        initialMode={storyAnsicht.erstellung.modus}
        initialPhotoUrl={storyAnsicht.erstellung.fotoUrl}
      />

      {/* Story Viewer Modal */}
      <StoryViewerModal
        open={storyAnsicht.betrachter.offen}
        onOpenChange={storyAnsicht.betrachter.setOffen}
        stories={storyAnsicht.betrachter.stories}
        initialIndex={storyAnsicht.betrachter.index}
        onDeleted={handleStoryDeleted}
        onReply={(targetUserId, _targetUsername, text, storyContext: StoryReplyContext) => {
          const contact = contactsList.find((c) => c.userId === targetUserId)
          if (contact) {
            setActiveContact(contact)
            setActiveGroup(null)
            storyAnsicht.betrachter.setOffen(false)
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
            storyAnsicht.erstellung.mitFoto(dataUrl)
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

      <DeleteGroupDialog
        group={groupToDelete}
        deleting={isDeletingGroup}
        onCancel={() => setGroupToDelete(null)}
        onConfirm={handleConfirmDeleteGroup}
      />
      {/* Das Hintergrundfenster — dasselbe, das der KI-Chat öffnet. */}
      <ChatHintergrundDialog
        bereich="messenger"
        offen={isWallpaperModalOpen}
        onOffenChange={setIsWallpaperModalOpen}
      />

      <ChatMuteDialog
        open={isMuteModalOpen}
        onOpenChange={setIsMuteModalOpen}
        mailboxId={blindMailboxId || null}
        chatName={activeGroup ? gruppenTitel(activeGroup) : activeContact?.username ?? null}
      />

      <SafetyNumberDialog
        open={sicherheitsnummern.offen}
        onOpenChange={sicherheitsnummern.setOffen}
        contactName={activeContact?.username || ''}
        devices={sicherheitsnummern.geraete}
        loading={sicherheitsnummern.laedt}
      />

      <BlockConfirmDialog
        open={isBlockConfirmOpen}
        onOpenChange={setIsBlockConfirmOpen}
        contactName={activeContact?.username ?? ''}
        blocked={Boolean(activeContact && isBlocked(activeContact.userId))}
        onConfirm={handleBlockConfirm}
      />

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
