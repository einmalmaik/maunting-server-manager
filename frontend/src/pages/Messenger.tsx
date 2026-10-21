import React, { useState, useEffect, useRef, useMemo, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams, useNavigate } from 'react-router-dom'
import {
  Button,
  Input,
  ChatInputBar,
  VoiceRecordingBar,
  Blattmenue,
  Blatteintrag,
  Kurzinfo,
  Ablageflaeche,
  useMehrfachauswahl,
  useZurueckSchliesst,
  type ChatInputBarRef,
} from '@/Singra/UI'
import {
  MessageSquare,
  Send,
  Search,
  X,
  RefreshCw,
  Trash2,
  Plus,
  Smile,
  UserCheck,
  Bell,
  BellOff,
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
import { useCallStore } from '@/stores/useCallStore'
import { apiUrl } from '@/config/api'
import {
  CircularVideoNoteRecorder,
  type VideoNoteAufnahme,
} from '@/components/social/CircularVideoNoteRecorder'
import {
  ChatMessageBubble,
  type AntwortBezug,
  type BlasenAktionen,
  type CalendarAttachment,
  type ChatMessage,
  type TonZustand,
  type NoteAttachment,
} from '@/components/social/ChatMessageBubble'
import {
  type ChatGroupItem,
  type ChatStoryItem,
  joinGroupByInvite,
  sendFriendRequest,
  sendTypingSignal,
  getGroupInviteInfo,
} from '@/api/social'
import { deriveBlindMailboxId } from '@/services/e2eeCrypto'
import { maxAnhangBytes } from '@/services/medienKrypto'
import {
  type FileAttachment,
  type ImageAttachment,
} from '@/components/social/ChatMediaAttachments'
import { useSprachaufnahme } from '@/hooks/useSprachaufnahme'
import { useSprachwiedergabe } from '@/hooks/useSprachwiedergabe'
import { useChatSuche } from '@/hooks/useChatSuche'
import { useEntwuerfe } from '@/hooks/useEntwuerfe'
import { useMessengerKontakte } from '@/pages/messenger/useMessengerKontakte'
import { useMessengerAuswahl } from '@/pages/messenger/useMessengerAuswahl'
import { useGruppenAktionen } from '@/hooks/useGruppenAktionen'
import { useMessengerKern } from '@/hooks/useMessengerKern'
import { useMessengerSenden } from '@/hooks/useMessengerSenden'
import { useMessengerInteraktionen } from '@/hooks/useMessengerInteraktionen'
import { useMessengerSync } from '@/hooks/useMessengerSync'
import { useSicherheitsnummern } from '@/hooks/useSicherheitsnummern'
import { useUeberallAnsicht } from '@/hooks/useUeberallAnsicht'
import {
  loadNotesOfflineFirst,
  loadCalendarEventsOfflineFirst,
  saveNoteOffline,
  saveCalendarEventOffline,
  getOutbox,
  setOutbox,
  replayOutbox,
} from '@/lib/offlineSync'
import type { NoteItem } from '@/pages/Notes'
import type { CalendarEventItem } from '@/pages/Calendar'
import {
  logischeUuid,
} from '@/services/ratchetSitzung'
import { abonniereBekannteGespraeche } from '@/services/gruppenSchluessel'
import {
  lieseEinladungsKarte,
  schluesselAusLink,
  type EinladungsInhalt,
} from '@/services/einladungsKarte'
import { merkeGruppenName } from '@/services/gruppenName'
import {
  gespraechsPartner,
  vergissGespraech,
} from '@/services/gespraechsListe'
import { ladeGruppenzustand, type Gruppenzustand } from '@/services/gruppenKonfig'
import { wirksameGruppenrechte } from '@/services/gruppenRollen'
import {
  entferneLokaleNachricht,
  leereMailboxLokal,
  updateMessageInLocalStore,
  sortMessagesChronologically,
} from '@/services/messengerLocalStore'
import {
  tilgeNachrichtBeimServer,
  tilgeNachrichtLokal,
} from '@/services/nachrichtLoeschen'
import { quittungsstand } from '@/services/quittungsstand'
import {
  binIchGemeint,
  offeneErwaehnung,
  setzeVorschlagEin,
  sucheVorschlaege,
  type Erwaehnungsvorschlag,
} from '@/services/erwaehnungen'
import {
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
  verfallStand,
} from '@/services/nachrichtVerfall'
import { sessionChatCache } from '@/services/klartextSpeicher'
import { ErwaehnungsWache } from '@/components/social/ErwaehnungsWache'
import { FunkenWache } from '@/components/social/FunkenWache'
import { FunkenAbzeichen } from '@/components/social/FunkenBadge'
import { rettungMoeglich } from '@/services/funkenService'
import { useFunkenStore } from '@/stores/funkenStore'
import { NachrichtenMenue } from '@/components/social/NachrichtenMenue'
import { WeiterleitenAnsicht } from '@/components/social/WeiterleitenAnsicht'
import { VerlaufSuchleiste } from '@/components/social/VerlaufSuchleiste'
import { TrefferListe } from '@/components/social/TrefferListe'

import { compressImageFile } from '@/lib/imageCompression'
import { CreateStoryModal } from '@/components/social/CreateStoryModal'
import { MessengerSperrschirm } from '@/components/social/MessengerSperrschirm'
import { siegelAktiv } from '@/services/lokaleVersiegelung'
import { useMessengerSperre } from '@/services/messengerSperre'
import { StoryViewerModal, type StoryReplyContext } from '@/components/social/StoryViewerModal'
import { NotePickerDialog } from '@/components/social/modals/NotePickerDialog'
import { CalendarPickerDialog } from '@/components/social/modals/CalendarPickerDialog'
import { ChatEntfernenDialog } from '@/components/social/modals/ChatEntfernenDialog'
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
import { ChatHintergrund } from '@/features/chatHintergrund'
import { MessengerDialoge } from '@/components/social/modals/MessengerDialoge'
import { useAuthStore } from '@/stores/authStore'
import { toast } from '@/stores/toastStore'
import { useMessengerNotificationStore, PINS_MAX } from '@/stores/messengerNotificationStore'
import { MessengerBetaBanner } from '@/components/social/MessengerBetaBanner'
import { DisBadge } from '@/components/DisBadge'

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
/*
 * `siegelAktiv()` steht neben dem Store, weil der seinen Stand erst nach
 * `initialisiere()` kennt. Ohne diesen zweiten Blick zeigte der erste Durchlauf
 * nach jedem Neuladen einen kurz aufblitzenden, leeren Messenger, bevor der
 * Sperrschirm ihn ablöst. Gelesen hätte er nichts — die Ablagen geben ohne
 * Schlüssel nichts heraus —, aber es sähe kaputt aus.
 */
/** Kennung einer Nachricht in der Mehrfachauswahl; noch nicht gesendete haben nur ihre `clientUuid`. */
const auswahlSchluessel = (m: ChatMessage) => m.clientUuid || `#${m.id}`

const istGesperrt = (s: { entsperrt: boolean; eingerichtet: boolean }) =>
  !s.entsperrt && (s.eingerichtet || siegelAktiv())

/**
 * Der Messenger hinter seinem Schloss.
 *
 * Gesperrt wird die Seite **ausgehängt**, nicht überdeckt. Bis 09/2026 stand
 * der Sperrschirm als `return` in der Seite selbst: sie blieb eingehängt, und
 * mit ihr im React-Zustand der entschlüsselte Verlauf des offenen Chats, das
 * Eingabefeld, Antwort- und Bearbeitungsziel, die Suchtreffer. Nach dem
 * Entsperren stand alles sofort wieder da, ohne dass etwas neu entsiegelt
 * wurde.
 *
 * Ausgehängt ist das alles weg. Nach dem Entsperren baut sich die Seite neu
 * auf und lädt den Verlauf aus der versiegelten Ablage. Welcher Chat offen war,
 * steht in der Adresse und im `sessionStorage`, als Kennung, nicht als Inhalt.
 */
export function Messenger() {
  const gesperrt = useMessengerSperre(istGesperrt)
  if (gesperrt) {
    return (
      <div className="flex h-full w-full min-h-0 flex-1 flex-col overflow-hidden bg-surface">
        <MessengerSperrschirm />
      </div>
    )
  }
  return <MessengerSeite />
}

function MessengerSeite() {
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
  const currentUserId = user?.id || 0
  // Die Hülle hängt diese Seite gesperrt gar nicht erst ein. Die Schranken
  // unten bleiben trotzdem stehen: sie kosten nichts, und keiner der Effekte
  // soll davon abhängen, von wem die Seite gerendert wird.
  const messengerGesperrt = useMessengerSperre(istGesperrt)
  const { inviteCode } = useParams<{ inviteCode?: string }>()
  const navigate = useNavigate()
  const {
    friends,
    groups,
    setGroups,
    directChats,
    stories,
    setStories,
    loadData,
    kontakteGeladen,
    contactsList,
    filteredContacts,
    filteredGroups,
    gruppenNachArchiv,
    kontakteNachArchiv,
    archivZahl,
    archivUngelesen,
    contactMailboxMap,
    groupMailboxMap,
    storyAnsicht,
    searchQuery,
    setSearchQuery,
    filterTab,
    setFilterTab,
    mobileNavTab,
    setMobileNavTab,
    archivOffen,
    setArchivOffen,
  } = useMessengerKontakte({ currentUserId, messengerGesperrt, gruppenTitel })

  const {
    activeContact,
    setActiveContact,
    activeGroup,
    setActiveGroup,
    searchParams,
    setSearchParams,
    zeilenMenue,
    setZeilenMenue,
    widerruf,
    setWiderruf,
    handleZurueckZurListe,
    handleAnheftenChat,
    handleArchivieren,
  } = useMessengerAuswahl({ kontakte: contactsList, gruppen: groups, kontakteGeladen, t })

  const {
    isCreateGroupOpen,
    setIsCreateGroupOpen,
    groupToDelete,
    setGroupToDelete,
    isDeletingGroup,
    logoLaedt,
    gruppenLogoInputRef,
    groupCallPermissions,
    handleStartGroupCall,
    handleGruppenLogo,
    handleCreateGroup,
    handleCopyInviteLink,
    handleLeaveGroup,
    handleDeleteGroup,
    handleConfirmDeleteGroup,
  } = useGruppenAktionen({
    t,
    currentUserId,
    activeGroup,
    setActiveGroup,
    setActiveContact,
    setGroups,
    loadData,
    gruppenTitel,
    handleZurueckZurListe,
  })

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
  const merkeErwaehnung = useMessengerNotificationStore((s) => s.merkeErwaehnung)

  // Mute & Block modals
  const [isMuteModalOpen, setIsMuteModalOpen] = useState(false)
  const [isBlockConfirmOpen, setIsBlockConfirmOpen] = useState(false)

  // Pre-computed mailbox IDs
  
  // Selection
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

  // Group Permissions & Delete Modal State
  const [isGroupPermissionsOpen, setIsGroupPermissionsOpen] = useState(false)
  const [chatEntfernenOffen, setChatEntfernenOffen] = useState(false)
  /**
   * Die `?userId=`, für die schon geprüft wurde.
   *
   * Die Kontaktliste kommt alle 15 Sekunden neu; ohne diesen Merker fragte die
   * Seite für einen unbekannten Kontakt bei jedem Takt erneut nach dem Profil.
   */

  // Camera & Attachments
  const [isCameraModalOpen, setIsCameraModalOpen] = useState(false)
  /** Die Kamera nimmt einen Augenblick auf: Auslösen heißt Senden. */
  const [augenblickModus, setAugenblickModus] = useState(false)
  /** Die offene Rückfrage vor einer Wiederherstellung des Funkens. */
  const [funkenRettung, setFunkenRettung] = useState<{ partnerId: number; name: string; anzahl: number } | null>(
    null,
  )
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
  const [inputText, setInputText] = useState('')
  // Ein Gerät legt seinen Schlüssel beim ersten Öffnen selbst an. Es gibt
  // nichts einzurichten, also auch keinen Zustand, in dem das Schreiben auf
  // Dauer gesperrt wäre — nur die kurze Spanne bis `ready`. Ist der Messenger
  // per PIN zu, steht ohnehin der Sperrschirm statt dieser Leiste.

  // Attachments
  const [isNotePickerOpen, setIsNotePickerOpen] = useState(false)
  const [userNotes, setUserNotes] = useState<NoteItem[]>([])

  const [isCalendarPickerOpen, setIsCalendarPickerOpen] = useState(false)
  const [userEvents, setUserEvents] = useState<CalendarEventItem[]>([])

  const [selectedImage, setSelectedImage] = useState<ImageAttachment | null>(null)
  const [viewingImage, setViewingImage] = useState<string | null>(null)
  useZurueckSchliesst(Boolean(viewingImage), () => setViewingImage(null))

  // Stickers / Emojis
  const [isStickerPickerOpen, setIsStickerPickerOpen] = useState(false)
  const [stickerTab, setStickerTab] = useState<PickerReiter>('stickers')

  // Group creation modal

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
  /**
   * Mehrfachauswahl: aus der Kopfzeile wird eine Aktionsleiste. Zurück und
   * Escape beenden sie, wie überall, wo `useMehrfachauswahl` gilt.
   */
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

  const gruppenrechteVonRef = useRef<(konto: number) => Set<string>>(() => new Set())
  const wahlLeerenRef = useRef<() => void>(() => {})
  const kern = useMessengerKern({
    t,
    currentUserId,
    friends,
    activeContact,
    activeGroup,
    messengerGesperrt,
    gruppenrechteVon: (konto) => gruppenrechteVonRef.current(konto),
    setAngeheftet,
    setVerfallSekunden,
    markAsRead,
    isBlocked,
    readReceiptsEnabled,
    onGespraechWechsel: () => {
      setAntwortAuf(null)
      chatSuche.schliesse()
      setAngeheftet(null)
      wahlLeerenRef.current()
      setInputText('')
    },
    onEntwurf: setInputText,
  })
  const {
    messages, setMessages, loadingMessages, sending, setSending,
    identity, setIdentity, identityRef, geraetWartet, blindMailboxId, konversation,
    sendE2eeControlMessage, loadMessages, zeigeSystemzeile,
    eingabeBeruehrtRef, highestIncomingIdAcknowledgedRef, highestIncomingIdDeliveredRef,
    maxPartnerReadIdRef, maxPartnerDeliveredIdRef,
  } = kern
  const auswahlReihe = useMemo(() => messages.map(auswahlSchluessel), [messages])
  const wahl = useMehrfachauswahl(auswahlReihe)
  wahlLeerenRef.current = wahl.leeren
  const auswahlModus = wahl.auswahl !== null
  const istSchreibenGesperrt = Boolean(activeContact) && identity.state === 'loading'


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
    eingabeBeruehrtRef.current = true
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
  gruppenrechteVonRef.current = gruppenrechteVon

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
  const handleAuswahlUmschalten = (msg: ChatMessage) => wahl.umschalten(auswahlSchluessel(msg))

  const beendeAuswahl = wahl.leeren

  const gewaehlteNachrichten = () => messages.filter((m) => wahl.auswahl?.has(auswahlSchluessel(m)))

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
      return (a.name || '').localeCompare(b.name || '')
    })
  }, [groups, contactsList, groupMailboxMap, contactMailboxMap, blindMailboxId, pinnedChats])

  const { handleSendMessage, handleDeleteMessage } = useMessengerSenden({
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
  })

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

  const { handleReaktion, handleAnheften, handleWeiterleiten, medienBindung } =
    useMessengerInteraktionen({
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
    })

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

  // Echtzeitstrom: Anrufe, eintreffende Nachrichten, Tippen.
  useMessengerSync({
    blindMailboxId,
    currentUserId,
    geraeteMeldungen: user?.device_notifications,
    loadMessages,
    setGroups,
    setActiveGroup,
    setPartnerActivity,
    partnerActivityTimeoutRef,
  })


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
        // Im Hintergrund nicht: jeder Durchgang entschlüsselt und versiegelt
        // den ganzen Verlauf neu. Neue Nachrichten holt dann das Sync-Ereignis
        // oben, und `visibilitychange` lädt beim Zurückkommen sofort.
        if (typeof document === 'undefined' || document.visibilityState === 'visible') {
          void loadMessages(false)
        }
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
   * Eine Nachricht, die die Warteschlange endgültig aufgegeben hat.
   *
   * Vorher erfuhr die Oberfläche davon nichts: der Auftrag verschwand, die Uhr
   * drehte weiter. Jetzt steht dort ein rotes Zeichen, und das Menü bietet
   * „Erneut senden". Der Text bleibt erhalten — das ist der Unterschied
   * zwischen „nicht angekommen" und „verloren".
   */
  useEffect(() => {
    const handleMessageFailed = (e: Event) => {
      const detail = (
        e as CustomEvent<{ client_uuid: string; blind_mailbox_id: string }>
      ).detail
      if (!detail?.client_uuid || detail.blind_mailbox_id !== blindMailboxId) return
      const clientUuid = logischeUuid(detail.client_uuid) ?? detail.client_uuid
      setMessages((prev) =>
        prev.map((m) =>
          m.clientUuid === clientUuid
            ? { ...m, status: 'failed', isDelivered: false, isRead: false }
            : m,
        ),
      )
    }
    window.addEventListener('msm:message-failed', handleMessageFailed)
    return () => {
      window.removeEventListener('msm:message-failed', handleMessageFailed)
    }
  }, [blindMailboxId])

  /**
   * Wenn dieses Gerät sich nicht beim Konto melden kann, sieht es niemand.
   *
   * Der Fehler wird in `e2eeIdentity.resolveIdentity` gedrosselt gemeldet; hier
   * wird daraus ein Satz statt eines stummen Verschwindens.
   */
  useEffect(() => {
    const handlePublishFailed = () => {
      toast.error(t('messenger.devicePublishFailed'))
    }
    window.addEventListener('msm:e2ee-publish-failed', handlePublishFailed)
    return () => {
      window.removeEventListener('msm:e2ee-publish-failed', handlePublishFailed)
    }
  }, [t])

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

  /**
   * Eine endgültig gescheiterte Nachricht noch einmal schicken.
   *
   * Der Umschlag von damals ist weg (der Server hat ihn abgewiesen), also wird
   * die Nutzlast aus der lokalen Zeile neu gebaut. Anhänge mit `mediaId`
   * werden wiederverwendet; eine Videonotiz lässt sich nicht aus ihrem Zeiger
   * wiederherstellen, und ohne Text gäbe es dann nichts zu senden — das sagt
   * die Meldung, statt eine leere Blase zu schicken.
   */
  const handleErneutSenden = async (msg: ChatMessage) => {
    const hatInhalt = Boolean(
      msg.text ||
        msg.imageAttachment ||
        msg.fileAttachment ||
        msg.audioAttachment ||
        msg.stickerAttachment ||
        msg.noteAttachment ||
        msg.calendarAttachment ||
        msg.storyReply,
    )
    if (!hatInhalt) {
      toast.error(t('messenger.retryNotPossible'))
      return
    }

    if (msg.clientUuid) {
      setMessages((prev) => prev.filter((m) => m.clientUuid !== msg.clientUuid))
      await entferneLokaleNachricht(blindMailboxId, {
        clientUuid: msg.clientUuid,
        id: msg.id,
      }).catch(() => {})
    }

    await handleSendMessage({
      text: msg.text,
      antwortAuf: msg.antwortAuf ?? null,
      img: msg.imageAttachment,
      file: msg.fileAttachment,
      audio: msg.audioAttachment,
      sticker: msg.stickerAttachment,
      note: msg.noteAttachment,
      cal: msg.calendarAttachment,
      storyReply: msg.storyReply,
      weitergeleitet: msg.weitergeleitet,
    })
  }

  /**
   * Nimmt ein 1:1-Gespräch aus der Liste — samt örtlichem Verlauf.
   *
   * Der Gegenpol zu den Geister-Kontakten aus früheren Fassungen: was nie in
   * der Liste stehen sollte, lässt sich jetzt auch wieder entfernen. Die
   * Freundschaft bleibt bestehen; nur das Gespräch und sein Verlauf gehen.
   */
  const handleChatAusListe = async () => {
    if (!activeContact) return
    const userId = activeContact.userId
    const mid = blindMailboxId

    await vergissGespraech(userId).catch(() => {})
    if (mid) {
      await leereMailboxLokal(mid).catch(() => {})
      sessionChatCache.delete(mid)
      vergissMailbox(mid)
      useMessengerNotificationStore.getState().markAsRead(mid)
    }

    handleZurueckZurListe()
    await loadData().catch(() => {})
    toast.success(t('messenger.chatRemoved'))
  }

  // Die fertige Sprachnachricht geht denselben Weg wie jede andere Nachricht.
  const aufnahme = useSprachaufnahme({
    blindMailboxId,
    onFertig: (audio) => void handleSendMessage({ text: '', audio }),
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

  const isChatOpen = Boolean(activeContact || activeGroup)

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

  /** Fragt nach, bevor der Funke mit diesem Kontakt wiederhergestellt wird. */
  const oeffneFunkenRettung = async (kontakt: ChatContact) => {
    await useFunkenStore.getState().lade()
    const jetzt = Date.now()
    const kern = useFunkenStore.getState().kernVon(kontakt.userId, jetzt)
    if (!kern || !rettungMoeglich(kern, jetzt)) {
      toast.error(t('messenger.streak.restoreUnavailable'))
      return
    }
    setFunkenRettung({ partnerId: kontakt.userId, name: kontakt.username, anzahl: kern.verloren })
  }

  // Aus der Freundesliste im Profil: `/chat?userId=<id>&funke=retten` öffnet
  // den Chat und gleich die Rückfrage. Der Zusatz fällt danach weg, damit ein
  // Neuladen nicht noch einmal fragt.
  useEffect(() => {
    if (searchParams.get('funke') !== 'retten' || !activeContact?.isFriend) return
    if (String(activeContact.userId) !== (searchParams.get('userId') || searchParams.get('contact'))) return
    const next = new URLSearchParams(searchParams)
    next.delete('funke')
    setSearchParams(next, { replace: true })
    void oeffneFunkenRettung(activeContact)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams, activeContact])

  /** Öffnet die Kamera für einen Augenblick an den offenen Kontakt. */
  const oeffneAugenblick = () => {
    setAugenblickModus(true)
    setIsCameraModalOpen(true)
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

  /*
   * Der Verlauf zeichnet nur neu, wenn sich etwas ändert, das er zeigt.
   *
   * Bis 27.09.2026 zeichnete jeder Tastendruck im Eingabefeld alle
   * Nachrichtenblasen neu: `inputText` liegt hier in der Seite, und die Blasen
   * bekamen bei jedem Durchlauf neue Handler. Die Handler laufen jetzt über
   * `blasenHandler`, das in jedem Durchlauf die neueste Fassung bekommt. Der
   * gemerkte Verlauf ruft also nie einen Handler mit altem Zustand auf.
   *
   * Wer der Blase etwas Neues zeigt, trägt es in die Liste unter `useMemo`
   * ein. Sonst bleibt die Blase beim alten Stand stehen.
   */
  const blasenHandler = useRef<{ aktionen: BlasenAktionen; ton: TonZustand; umschalten: (m: ChatMessage) => void }>(null!)
  blasenHandler.current = {
    aktionen: {
      onViewImage: setViewingImage,
      onEdit: (m) => {
        setEditingMessage(m)
        setInputText(m.text)
      },
      onDelete: (m) => void handleDeleteMessage(m),
      onImportNote: (note, schluessel) => void handleImportNote(note, schluessel),
      onImportCalendar: (cal, schluessel) => void handleImportCalendar(cal, schluessel),
      onJoinByInviteCode: handleJoinByInviteCode,
      onMenue: setMenueNachricht,
      onAntworten: handleAntworten,
      onReaktion: (m, emoji) => void handleReaktion(m, emoji),
      onSpringeZu: springeZu,
      onErneutSenden: (m) => void handleErneutSenden(m),
    },
    ton: tonWiedergabe,
    umschalten: handleAuswahlUmschalten,
  }
  const blasenAktionen = useMemo<BlasenAktionen>(
    () => ({
      onViewImage: (url) => blasenHandler.current.aktionen.onViewImage(url),
      onEdit: (m) => blasenHandler.current.aktionen.onEdit(m),
      onDelete: (m) => blasenHandler.current.aktionen.onDelete(m),
      onImportNote: (note, schluessel) => blasenHandler.current.aktionen.onImportNote(note, schluessel),
      onImportCalendar: (cal, schluessel) => blasenHandler.current.aktionen.onImportCalendar(cal, schluessel),
      onJoinByInviteCode: (code) => blasenHandler.current.aktionen.onJoinByInviteCode(code),
      onMenue: (m) => blasenHandler.current.aktionen.onMenue(m),
      onAntworten: (m) => blasenHandler.current.aktionen.onAntworten(m),
      onReaktion: (m, emoji) => blasenHandler.current.aktionen.onReaktion(m, emoji),
      onSpringeZu: (uuid) => blasenHandler.current.aktionen.onSpringeZu(uuid),
      onErneutSenden: (m) => blasenHandler.current.aktionen.onErneutSenden?.(m),
    }),
    [],
  )
  const { playingAudioId, audioCurrentTime, audioPlaybackRate } = tonWiedergabe
  const verlauf = useMemo(() => {
    const ton: TonZustand = {
      playingAudioId,
      audioCurrentTime,
      audioPlaybackRate,
      onTogglePlay: (id, anhang, bindung) => blasenHandler.current.ton.onTogglePlay(id, anhang, bindung),
      onCycleRate: (e) => blasenHandler.current.ton.onCycleRate(e),
      onSeek: (id, anhang, bindung, e) => blasenHandler.current.ton.onSeek(id, anhang, bindung, e),
    }
    const umschalten = (m: ChatMessage) => blasenHandler.current.umschalten(m)
    return (
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
              hervorgehoben: Boolean(msg.clientUuid && hervorgehoben === msg.clientUuid),
            }}
            ton={ton}
            aktionen={blasenAktionen}
            auswahl={{
              aktiv: auswahlModus,
              gewaehlt: Boolean(wahl.auswahl?.has(auswahlSchluessel(msg))),
              onUmschalten: umschalten,
            }}
            medienBindung={medienBindung}
          />
        )}
      />
    )
  }, [
    messages,
    loadingMessages,
    trennerId,
    partnerActivity,
    activeGroup,
    activeContact,
    currentUserId,
    user?.username,
    user?.avatar_url,
    readReceiptsEnabled,
    importedAttachmentIds,
    hervorgehoben,
    playingAudioId,
    audioCurrentTime,
    audioPlaybackRate,
    auswahlModus,
    wahl.auswahl,
    // `medienBindung` liest nur diese beiden.
    blindMailboxId,
    blasenAktionen,
  ])

  return (
    <div className="flex h-full w-full min-h-0 flex-1 flex-col overflow-hidden bg-surface">
      <MessengerBetaBanner />
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
            <DisBadge size={14} className="py-0.5 px-0.5 sm:px-2" textClassName="hidden sm:inline" />
            <span className="text-label-sm text-on-surface-variant/60 hidden md:inline">{t('messenger.headerSubtitle')}</span>
          </div>

          <div className="flex items-center gap-1">
            <Kurzinfo text={t('common.refresh')} seite="ende">
              <Button
                variant="ghost"
                size="icon"
                onClick={() => loadData()}
                className="h-8 w-8 text-on-surface-variant"
                aria-label={t('common.refresh')}
              >
                <RefreshCw className="w-3.5 h-3.5" />
              </Button>
            </Kurzinfo>
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
                        <Kurzinfo text={t('messenger.newGroup')} seite="ende" className="normal-case tracking-normal">
                          <button
                            type="button"
                            onClick={() => setIsCreateGroupOpen(true)}
                            className="p-0.5 rounded text-on-surface-variant hover:text-primary transition-colors"
                            aria-label={t('messenger.newGroup')}
                          >
                            <Plus className="w-3 h-3" />
                          </button>
                        </Kurzinfo>
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
                      <span className="truncate">{t('messenger.searchInMessages', { query: searchQuery.trim() })}</span>
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
          {isDragOver && <Ablageflaeche className="z-40" text={t('messenger.dropFile')} hinweis={t('messenger.dropHint')} />}

          {/* Der Chat-Hintergrund — dieselbe Schicht wie im KI-Bereich. */}
          <ChatHintergrund bereich="messenger" />

          {isChatOpen ? (
            <>
              {auswahlModus && (
                <ChatSelectionBar
                  anzahl={wahl.auswahl?.size ?? 0}
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
                onZurueck={handleZurueckZurListe}
                gruppenanruf={
                  activeGroup
                    ? { erlaubt: groupCallPermissions.canStart, onStarten: () => handleStartGroupCall(false) }
                    : undefined
                }
                onSprachanruf={activeContact?.isFriend ? () => void starteAnruf('audio') : undefined}
                funke={
                  activeContact?.isFriend ? (
                    <FunkenAbzeichen
                      partnerId={activeContact.userId}
                      name={activeContact.username}
                      interaktiv
                      onWiederherstellen={() => void oeffneFunkenRettung(activeContact)}
                    />
                  ) : null
                }
                onAugenblick={
                  activeContact?.isFriend && !isBlocked(activeContact.userId) ? oeffneAugenblick : undefined
                }
                onFreundschaftsanfrage={
                  activeContact && !activeContact.isFriend ? () => void sendeFreundschaftsanfrage() : undefined
                }
                onProfil={activeContact ? () => navigate(`/user/${activeContact.userId}`) : undefined}
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
                    onProfil={() => activeContact && navigate(`/user/${activeContact.userId}`)}
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
                    onChatEntfernen={() => setChatEntfernenOffen(true)}
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

              {verlauf}

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
              <div className="p-2.5 pb-[calc(0.625rem+var(--msm-unten-sicher))] border-t border-outline-variant/20 bg-surface-container-low relative z-1">
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
                            {/* Bei offener Auswahl läge die Kurzinfo über ihr. */}
                            <Kurzinfo
                              text={t('messenger.stickers')}
                              seite="anfang"
                              lage="oben"
                              aussen="shrink-0"
                              className={isStickerPickerOpen ? '!hidden' : ''}
                            >
                              <button
                                type="button"
                                onClick={() => setIsStickerPickerOpen((prev) => !prev)}
                                className={`w-11 h-11 sm:w-8 sm:h-8 flex items-center justify-center rounded-full transition-colors ${
                                  isStickerPickerOpen
                                    ? 'bg-surface-container-highest text-status-warning'
                                    : 'text-on-surface-variant hover:text-status-warning'
                                }`}
                                aria-label={t('messenger.pickSticker')}
                              >
                                <Smile className="w-4 h-4" />
                              </button>
                            </Kurzinfo>

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
        onAuswaehlen={(m) => wahl.umschalten(auswahlSchluessel(m))}
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

      {/* Die Funkenwache liest Direktchats mit laufendem Funken mit, damit das
          Abzeichen springt, ohne dass man den Chat öffnet. */}
      <FunkenWache
        aktiverPartner={activeContact?.userId ?? null}
        eigeneId={currentUserId}
        identitaetRef={identityRef}
        aktiv={!messengerGesperrt && !!currentUserId}
      />


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
          role="dialog"
          aria-modal="true"
          aria-label={t('messenger.fullView')}
          className="msm-modal-overlay bg-black/80"
          onClick={() => setViewingImage(null)}
        >
          <div className="relative max-w-4xl max-h-[90vh]" onClick={(e) => e.stopPropagation()}>
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
        onReply={async (targetUserId, _targetUsername, text, storyContext: StoryReplyContext) => {
          const contact = contactsList.find((c) => c.userId === targetUserId)
          if (!contact || !currentUserId) {
            toast.error(t('messenger.replyContactMissing'))
            return
          }
          storyAnsicht.betrachter.setOffen(false)
          // Das Ziel steht ausdrücklich im Auftrag. Bis 26.09.2026 wurde erst
          // der Chat umgeschaltet und im selben Zug gesendet; der Versand sah
          // noch das offene Gespräch, und die private Antwort samt Storyfoto
          // ging an die gerade offene Gruppe.
          const mailbox =
            contactMailboxMap[contact.userId] ?? (await deriveBlindMailboxId(currentUserId, contact.userId))
          await handleSendMessage({
            text,
            storyReply: storyContext,
            antwortAuf: null,
            ziel: { blindMailboxId: mailbox, recipientId: contact.userId, groupId: null },
          })
          setActiveContact(contact)
          setActiveGroup(null)
        }}
      />

      {/* Live Camera Snapshot Modal */}

      {/* Group Permissions & Roles Management Modal */}


      <ChatEntfernenDialog
        open={chatEntfernenOffen}
        name={activeContact?.username ?? ''}
        onCancel={() => setChatEntfernenOffen(false)}
        onConfirm={() => {
          setChatEntfernenOffen(false)
          void handleChatAusListe()
        }}
      />
      {/* Das Hintergrundfenster — dasselbe, das der KI-Chat öffnet. */}




      <MessengerDialoge
        isCreateGroupOpen={isCreateGroupOpen}
        setIsCreateGroupOpen={setIsCreateGroupOpen}
        onCreateGroup={handleCreateGroup}
        funkenRettung={funkenRettung}
        setFunkenRettung={setFunkenRettung}
        onFunkenRettungBestaetigen={async () => {
          const ziel = funkenRettung
          setFunkenRettung(null)
          if (!ziel || activeContact?.userId !== ziel.partnerId) return
          await handleSendMessage({ text: '', funkenRettung: true, antwortAuf: null })
        }}
        activeContact={activeContact}
        isCameraModalOpen={isCameraModalOpen}
        setIsCameraModalOpen={setIsCameraModalOpen}
        augenblickModus={augenblickModus}
        setAugenblickModus={setAugenblickModus}
        onCameraCapture={(dataUrl) => {
          if (augenblickModus && activeContact) {
            void handleSendMessage({
              text: '',
              img: { dataUrl, name: 'augenblick.jpg' },
              augenblick: true,
              antwortAuf: null,
            })
            return
          }
          setSelectedImage({ dataUrl, name: 'kamera-aufnahme.jpg' })
        }}
        isGroupPermissionsOpen={isGroupPermissionsOpen}
        setIsGroupPermissionsOpen={setIsGroupPermissionsOpen}
        activeGroup={activeGroup}
        currentUserId={currentUserId}
        onGroupUpdated={(updatedGroup) => {
          setActiveGroup(updatedGroup)
          setGroups((prev) => prev.map((g) => (g.id === updatedGroup.id ? updatedGroup : g)))
        }}
        groupToDelete={groupToDelete}
        setGroupToDelete={setGroupToDelete}
        isDeletingGroup={isDeletingGroup}
        onConfirmDeleteGroup={handleConfirmDeleteGroup}
        isWallpaperModalOpen={isWallpaperModalOpen}
        setIsWallpaperModalOpen={setIsWallpaperModalOpen}
        isMuteModalOpen={isMuteModalOpen}
        setIsMuteModalOpen={setIsMuteModalOpen}
        blindMailboxId={blindMailboxId}
        chatName={activeGroup ? gruppenTitel(activeGroup) : activeContact?.username ?? null}
        sicherheitsnummern={sicherheitsnummern}
        isBlockConfirmOpen={isBlockConfirmOpen}
        setIsBlockConfirmOpen={setIsBlockConfirmOpen}
        blocked={Boolean(activeContact && isBlocked(activeContact.userId))}
        onBlockConfirm={handleBlockConfirm}
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
