import React, { useState, useEffect, useRef, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  Button,
  Input,
  Badge,
  Dialog,
  DialogContent,
  Avatar,
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
} from 'lucide-react'
import { DeviceBadge } from '@/components/social/DeviceBadge'
import { StatusDot, type PresenceStatus } from '@/components/social/StatusIndicator'
import {
  type FriendItem,
  getFriends,
  relayE2eeEnvelope,
  fetchE2eeEnvelopes,
  getE2eePublicKey,
  setE2eePublicKey,
} from '@/api/social'
import { teamsApi, type TeamMember } from '@/api/teams'
import { loadNotesOfflineFirst, loadCalendarEventsOfflineFirst } from '@/lib/offlineSync'
import type { NoteItem } from '@/pages/Notes'
import type { CalendarEventItem } from '@/pages/Calendar'
import {
  deriveBlindMailboxId,
  encryptE2eeMessage,
  decryptE2eeMessage,
  encryptE2eeHybrid,
  decryptE2eeHybrid,
  getOrGenerateLocalKeyPair,
  type LocalE2eeKeyPair,
} from '@/services/e2eeCrypto'
import { useAuthStore } from '@/stores/authStore'
import { toast } from '@/stores/toastStore'

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
}

export interface ChatMessage {
  id: number
  senderId: number
  text: string
  createdAt: string
  isSelf: boolean
  noteAttachment?: NoteAttachment
  calendarAttachment?: CalendarAttachment
  imageAttachment?: ImageAttachment
}

export function Messenger() {
  const { user } = useAuthStore()
  const [searchParams] = useSearchParams()
  const queryUserId = searchParams.get('userId')
  const [friends, setFriends] = useState<FriendItem[]>([])
  const [teamMembers, setTeamMembers] = useState<Array<{ member: TeamMember; teamName: string }>>([])
  const [activeContact, setActiveContact] = useState<ChatContact | null>(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [filterTab, setFilterTab] = useState<'all' | 'friends' | 'teams'>('all')

  // Conversation state
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [inputText, setInputText] = useState('')
  const [loadingMessages, setLoadingMessages] = useState(false)
  const [sending, setSending] = useState(false)
  const [blindMailboxId, setBlindMailboxId] = useState<string>('')
  const [localKeyPair, setLocalKeyPair] = useState<LocalE2eeKeyPair | null>(null)
  const [recipientPublicKeyJwk, setRecipientPublicKeyJwk] = useState<string | null>(null)

  // Modals for Attachments
  const [isNotePickerOpen, setIsNotePickerOpen] = useState(false)
  const [userNotes, setUserNotes] = useState<NoteItem[]>([])
  const [noteSearch, setNoteSearch] = useState('')

  const [isCalendarPickerOpen, setIsCalendarPickerOpen] = useState(false)
  const [userEvents, setUserEvents] = useState<CalendarEventItem[]>([])
  const [calendarSearch, setCalendarSearch] = useState('')

  const [selectedImage, setSelectedImage] = useState<ImageAttachment | null>(null)
  const [viewingImage, setViewingImage] = useState<string | null>(null)

  const fileInputRef = useRef<HTMLInputElement>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const scrollContainerRef = useRef<HTMLDivElement>(null)
  const justSentRef = useRef<boolean>(false)

  const currentUserId = user?.id || 0

  // 1. Initialize local key pair
  useEffect(() => {
    if (!currentUserId) return
    let active = true

    getOrGenerateLocalKeyPair(currentUserId).then(async (kp) => {
      if (!active) return
      setLocalKeyPair(kp)
      try {
        await setE2eePublicKey(kp.publicKeyJwk)
      } catch {
        // Non-blocking
      }
    }).catch(() => {})

    return () => {
      active = false
    }
  }, [currentUserId])

  // 2. Load Friends and Team Members
  const loadContacts = async () => {
    try {
      const [friendsData, teamsData] = await Promise.all([
        getFriends().catch(() => []),
        teamsApi.list().catch(() => []),
      ])
      setFriends(friendsData)

      // Fetch team members for user's teams
      const membersList: Array<{ member: TeamMember; teamName: string }> = []
      for (const t of teamsData) {
        try {
          const detail = await teamsApi.get(t.id)
          if (detail && detail.members) {
            for (const m of detail.members) {
              if (m.user_id !== currentUserId) {
                membersList.push({ member: m, teamName: t.name })
              }
            }
          }
        } catch {
          // Ignore
        }
      }
      setTeamMembers(membersList)
    } catch {
      // Offline fallback
    }
  }

  useEffect(() => {
    loadContacts()
    const interval = setInterval(loadContacts, 15000)
    return () => clearInterval(interval)
  }, [currentUserId])

  // Combine Contacts
  const contactsList: ChatContact[] = useMemo(() => {
    const list: ChatContact[] = []
    const seenUserIds = new Set<number>()

    // Friends first
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

    // Team members (with or without prior friendship)
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

    return list.sort((a, b) => {
      const statusOrder: Record<string, number> = { online: 0, away: 1, invisible: 2 }
      const diff = (statusOrder[a.status] ?? 3) - (statusOrder[b.status] ?? 3)
      if (diff !== 0) return diff
      return a.username.localeCompare(b.username)
    })
  }, [friends, teamMembers])

  const filteredContacts = useMemo(() => {
    return contactsList.filter((c) => {
      if (filterTab === 'friends' && !c.isFriend) return false
      if (filterTab === 'teams' && !c.teamName) return false
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

  // Auto-select contact if userId query parameter is present
  useEffect(() => {
    if (queryUserId && contactsList.length > 0) {
      const match = contactsList.find((c) => c.userId === Number(queryUserId))
      if (match && activeContact?.userId !== match.userId) {
        setActiveContact(match)
      }
    }
  }, [queryUserId, contactsList, activeContact])

  // 3. When active contact changes, fetch their public key & derive mailbox
  useEffect(() => {
    if (!activeContact || !currentUserId) {
      setBlindMailboxId('')
      setRecipientPublicKeyJwk(null)
      setMessages([])
      return
    }

    let active = true
    const targetUserId = activeContact.userId

    deriveBlindMailboxId(currentUserId, targetUserId).then((mid) => {
      if (active) setBlindMailboxId(mid)
    })

    getE2eePublicKey(targetUserId).then((res) => {
      if (active && res?.public_key) {
        setRecipientPublicKeyJwk(res.public_key)
      }
    }).catch(() => {})

    return () => {
      active = false
    }
  }, [activeContact, currentUserId])

  // 4. Load messages
  const loadMessages = async () => {
    if (!activeContact || !blindMailboxId || !currentUserId) return
    setLoadingMessages(true)
    try {
      const envelopes = await fetchE2eeEnvelopes(blindMailboxId)
      const decryptedList: ChatMessage[] = []
      const targetUserId = activeContact.userId

      for (const env of envelopes) {
        try {
          let plain = ''
          if (env.ciphertext_envelope.startsWith('sv-e2ee-hybrid-v1:')) {
            if (localKeyPair) {
              plain = await decryptE2eeHybrid(env.ciphertext_envelope, localKeyPair.privateKeyJwk)
            } else {
              throw new Error('Local key not ready')
            }
          } else {
            plain = await decryptE2eeMessage(env.ciphertext_envelope, currentUserId, targetUserId)
          }

          let text = plain
          let isSelf = false
          let noteAttachment: NoteAttachment | undefined = undefined
          let calendarAttachment: CalendarAttachment | undefined = undefined
          let imageAttachment: ImageAttachment | undefined = undefined

          try {
            const parsed = JSON.parse(plain)
            if (typeof parsed === 'object' && parsed !== null) {
              text = parsed.text || ''
              isSelf = parsed.sender_id === currentUserId
              if (parsed.note_attachment) noteAttachment = parsed.note_attachment
              if (parsed.calendar_attachment) calendarAttachment = parsed.calendar_attachment
              if (parsed.image_attachment) imageAttachment = parsed.image_attachment
            }
          } catch {
            if (plain.startsWith('[ME]:')) {
              text = plain.replace('[ME]:', '')
              isSelf = true
            }
          }

          decryptedList.push({
            id: env.id,
            senderId: isSelf ? currentUserId : targetUserId,
            text,
            createdAt: env.created_at,
            isSelf,
            noteAttachment,
            calendarAttachment,
            imageAttachment,
          })
        } catch {
          decryptedList.push({
            id: env.id,
            senderId: targetUserId,
            text: 'Verschlüsselte Nachricht',
            createdAt: env.created_at,
            isSelf: false,
          })
        }
      }

      setMessages(decryptedList)
    } catch {
      // Offline fallback
    } finally {
      setLoadingMessages(false)
    }
  }

  useEffect(() => {
    if (blindMailboxId && activeContact) {
      loadMessages()
      const interval = setInterval(loadMessages, 4000)
      return () => clearInterval(interval)
    }
  }, [blindMailboxId, activeContact, localKeyPair])

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

  // 5. Send message (with optional attachments)
  const handleSendMessage = async (
    customText?: string,
    note?: NoteAttachment,
    cal?: CalendarAttachment,
    img?: ImageAttachment
  ) => {
    const rawText = customText !== undefined ? customText : inputText.trim()
    const targetUserId = activeContact?.userId

    if (
      (!rawText && !note && !cal && !img) ||
      !activeContact ||
      !blindMailboxId ||
      !currentUserId ||
      !targetUserId ||
      sending
    ) {
      return
    }

    setSending(true)
    try {
      const payloadObj: Record<string, unknown> = {
        sender_id: currentUserId,
        text: rawText,
        timestamp: new Date().toISOString(),
      }

      if (note) payloadObj.note_attachment = note
      if (cal) payloadObj.calendar_attachment = cal
      if (img) payloadObj.image_attachment = img

      const payload = JSON.stringify(payloadObj)

      let ciphertext: string
      if (recipientPublicKeyJwk && localKeyPair) {
        ciphertext = await encryptE2eeHybrid(payload, recipientPublicKeyJwk, localKeyPair.publicKeyJwk)
      } else {
        ciphertext = await encryptE2eeMessage(payload, currentUserId, targetUserId)
      }

      await relayE2eeEnvelope({
        blind_mailbox_id: blindMailboxId,
        ciphertext_envelope: ciphertext,
        recipient_user_id: targetUserId,
      })

      setInputText('')
      setSelectedImage(null)
      justSentRef.current = true
      await loadMessages()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Fehler beim Senden'
      toast.error(msg)
    } finally {
      setSending(false)
    }
  }

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

  return (
    <div className="msm-page h-[calc(100dvh-5.5rem)] max-h-[calc(100dvh-5.5rem)] flex flex-col min-h-0 overflow-hidden">
      <div className="flex-1 grid grid-cols-1 md:grid-cols-12 gap-3 min-h-0 overflow-hidden">
        {/* Left Column: Contact List */}
        <div
          className={`md:col-span-4 lg:col-span-4 flex flex-col min-h-0 bg-surface-container-low/95 border border-outline-variant/30 rounded-2xl overflow-hidden shadow-md ${
            activeContact ? 'hidden md:flex' : 'flex'
          }`}
        >
          {/* Header & Search */}
          <div className="p-3 border-b border-outline-variant/20 bg-surface-container space-y-2.5">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <MessageSquare className="w-4 h-4 text-primary" />
                <span className="font-headline text-body-md font-bold text-primary">Messenger</span>
              </div>
              <Badge variant="info" className="text-[10px] px-1.5 py-0">
                {contactsList.length} Kontakte
              </Badge>
            </div>

            <div className="relative">
              <Search className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-on-surface-variant/60" />
              <Input
                value={searchQuery}
                onChange={(e: React.ChangeEvent<HTMLInputElement>) => setSearchQuery(e.target.value)}
                placeholder="Freunde oder Teammitglieder suchen …"
                className="text-xs pl-8 h-8"
              />
            </div>

            {/* Filter Tabs */}
            <div className="flex items-center gap-1">
              <Button
                variant={filterTab === 'all' ? 'primary' : 'ghost'}
                size="sm"
                onClick={() => setFilterTab('all')}
                className="text-xs h-6 px-2.5"
              >
                Alle
              </Button>
              <Button
                variant={filterTab === 'friends' ? 'primary' : 'ghost'}
                size="sm"
                onClick={() => setFilterTab('friends')}
                className="text-xs h-6 px-2.5"
              >
                Freunde ({contactsList.filter((c) => c.isFriend).length})
              </Button>
              <Button
                variant={filterTab === 'teams' ? 'primary' : 'ghost'}
                size="sm"
                onClick={() => setFilterTab('teams')}
                className="text-xs h-6 px-2.5"
              >
                Teams ({contactsList.filter((c) => c.teamName).length})
              </Button>
            </div>
          </div>

          {/* Contact List Scroll */}
          <div className="flex-1 overflow-y-auto p-2 space-y-1">
            {filteredContacts.length === 0 ? (
              <p className="py-12 text-center text-xs text-on-surface-variant/70">
                Keine Kontakte gefunden.
              </p>
            ) : (
              filteredContacts.map((c) => {
                const isSelected = activeContact?.userId === c.userId
                return (
                  <button
                    key={`${c.isFriend ? 'f' : 't'}-${c.userId}`}
                    type="button"
                    onClick={() => setActiveContact(c)}
                    className={`w-full flex items-center justify-between p-2.5 rounded-xl text-left transition-all ${
                      isSelected
                        ? 'bg-primary/15 border border-primary/30 shadow-sm'
                        : 'hover:bg-surface-container-high/60 border border-transparent'
                    }`}
                  >
                    <div className="flex items-center gap-3 min-w-0">
                      <div className="relative shrink-0">
                        <Avatar src={c.avatarUrl} name={c.username} size="sm" />
                        <StatusDot status={c.status} size="sm" className="absolute bottom-0 right-0" />
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-1.5">
                          <span className="text-xs font-semibold text-primary truncate">
                            {c.username}
                          </span>
                          <DeviceBadge deviceType={c.deviceType} />
                        </div>
                        {c.teamName && (
                          <p className="text-[10px] text-tertiary truncate flex items-center gap-1">
                            <UsersRound className="w-2.5 h-2.5" />
                            <span>{c.teamName}</span>
                          </p>
                        )}
                        {c.activityLabel && !c.teamName && (
                          <p className="text-[10px] text-on-surface-variant/80 truncate">
                            {c.activityLabel}
                          </p>
                        )}
                      </div>
                    </div>
                  </button>
                )
              })
            )}
          </div>
        </div>

        {/* Right Column: Chat Thread */}
        <div
          className={`md:col-span-8 lg:col-span-8 flex flex-col min-h-0 bg-surface-container-low/95 border border-outline-variant/30 rounded-2xl overflow-hidden shadow-md ${
            !activeContact ? 'hidden md:flex' : 'flex'
          }`}
        >
          {activeContact ? (
            <>
              {/* Active Contact Header */}
              <div className="p-3 border-b border-outline-variant/20 bg-surface-container flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <button
                    type="button"
                    onClick={() => setActiveContact(null)}
                    className="md:hidden p-1 rounded-lg hover:bg-surface-container-high text-on-surface-variant"
                    aria-label="Zurück zur Kontaktliste"
                  >
                    <ChevronLeft className="w-5 h-5" />
                  </button>

                  <div className="relative">
                    <Avatar src={activeContact.avatarUrl} name={activeContact.username} size="sm" />
                    <StatusDot
                      status={activeContact.status}
                      size="sm"
                      className="absolute bottom-0 right-0"
                    />
                  </div>

                  <div>
                    <div className="flex items-center gap-2">
                      <span className="font-headline text-body-sm font-bold text-primary truncate">
                        {activeContact.username}
                      </span>
                      {activeContact.teamName && (
                        <Badge variant="info" className="text-[9px] px-1.5 py-0">
                          {activeContact.teamName}
                        </Badge>
                      )}
                      <DeviceBadge deviceType={activeContact.deviceType} />
                    </div>
                    <div className="font-body text-[11px] text-on-surface-variant flex items-center gap-1 mt-0.5">
                      <Lock className="w-3 h-3 text-emerald-400" />
                      <span>Ende-zu-Ende verschlüsselt</span>
                    </div>
                  </div>
                </div>

                <div className="flex items-center gap-1">
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => void loadMessages()}
                    disabled={loadingMessages}
                    className="h-7 w-7 p-0"
                    aria-label="Nachrichten aktualisieren"
                  >
                    <RefreshCw className={`w-3.5 h-3.5 ${loadingMessages ? 'animate-spin' : ''}`} />
                  </Button>
                </div>
              </div>

              {/* Message Thread Scroll Area */}
              <div
                ref={scrollContainerRef}
                className="flex-1 overflow-y-auto p-4 space-y-3 bg-surface-container-lowest/40"
              >
                {/* Discreet WhatsApp-style encryption notice */}
                <div className="py-2 text-center">
                  <div className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-surface-container-high/60 border border-outline-variant/30 text-[11px] text-on-surface-variant">
                    <Lock className="w-3 h-3 text-emerald-400" />
                    <span>Nachrichten in diesem Chat sind Ende-zu-Ende verschlüsselt.</span>
                  </div>
                </div>

                {messages.length === 0 && !loadingMessages && (
                  <div className="py-16 text-center text-xs text-on-surface-variant/70">
                    Noch keine Nachrichten. Schreibe die erste Nachricht!
                  </div>
                )}

                {messages.map((msg) => (
                  <div
                    key={msg.id}
                    className={`flex flex-col ${msg.isSelf ? 'items-end' : 'items-start'}`}
                  >
                    <div
                      className={`max-w-[85%] md:max-w-[70%] px-3.5 py-2.5 rounded-2xl text-xs break-words shadow-sm space-y-2 ${
                        msg.isSelf
                          ? 'bg-primary text-on-primary rounded-br-xs'
                          : 'bg-surface-container-high text-on-surface rounded-bl-xs border border-outline-variant/20'
                      }`}
                    >
                      {/* Image Attachment */}
                      {msg.imageAttachment && (
                        <div className="rounded-xl overflow-hidden border border-black/10 my-1 cursor-pointer">
                          <img
                            src={msg.imageAttachment.dataUrl}
                            alt="Chat Anhang"
                            onClick={() => setViewingImage(msg.imageAttachment?.dataUrl || null)}
                            className="max-h-60 w-auto object-cover rounded-lg hover:opacity-95 transition-opacity"
                          />
                        </div>
                      )}

                      {/* Note Attachment Card */}
                      {msg.noteAttachment && (
                        <div
                          className={`p-2.5 rounded-xl border text-xs ${
                            msg.isSelf
                              ? 'bg-white/10 border-white/20 text-white'
                              : 'bg-surface-container-low border-outline-variant/30 text-on-surface'
                          }`}
                        >
                          <div className="flex items-center gap-1.5 font-bold mb-1 text-[11px]">
                            <StickyNote className="w-3.5 h-3.5 text-amber-400" />
                            <span>{msg.noteAttachment.title || 'Notiz'}</span>
                          </div>
                          <p className="whitespace-pre-wrap text-[11px] opacity-90 line-clamp-4">
                            {msg.noteAttachment.content}
                          </p>
                        </div>
                      )}

                      {/* Calendar Attachment Card */}
                      {msg.calendarAttachment && (
                        <div
                          className={`p-2.5 rounded-xl border text-xs ${
                            msg.isSelf
                              ? 'bg-white/10 border-white/20 text-white'
                              : 'bg-surface-container-low border-outline-variant/30 text-on-surface'
                          }`}
                        >
                          <div className="flex items-center gap-1.5 font-bold mb-1 text-[11px]">
                            <CalendarIcon className="w-3.5 h-3.5 text-cyan-400" />
                            <span>{msg.calendarAttachment.title || 'Termin'}</span>
                          </div>
                          <div className="text-[10px] opacity-80 flex items-center gap-1">
                            <Clock className="w-3 h-3" />
                            <span>
                              {new Date(msg.calendarAttachment.start).toLocaleString([], {
                                dateStyle: 'short',
                                timeStyle: 'short',
                              })}
                            </span>
                          </div>
                          {msg.calendarAttachment.location && (
                            <div className="text-[10px] opacity-80 flex items-center gap-1 mt-0.5">
                              <MapPin className="w-3 h-3" />
                              <span>{msg.calendarAttachment.location}</span>
                            </div>
                          )}
                          {msg.calendarAttachment.description && (
                            <p className="whitespace-pre-wrap text-[11px] opacity-90 mt-1 line-clamp-3">
                              {msg.calendarAttachment.description}
                            </p>
                          )}
                        </div>
                      )}

                      {/* Text content */}
                      {msg.text && <p className="leading-relaxed">{msg.text}</p>}
                    </div>

                    <span className="text-[10px] text-on-surface-variant/50 mt-1 px-1">
                      {new Date(msg.createdAt).toLocaleTimeString([], {
                        hour: '2-digit',
                        minute: '2-digit',
                      })}
                    </span>
                  </div>
                ))}
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

              {/* Footer Input Area */}
              <form
                onSubmit={(e) => {
                  e.preventDefault()
                  handleSendMessage(inputText, undefined, undefined, selectedImage || undefined)
                }}
                className="p-3 border-t border-outline-variant/20 bg-surface-container-low flex items-center gap-2"
              >
                {/* File Input for Camera/Photo */}
                <input
                  ref={fileInputRef}
                  type="file"
                  accept="image/*"
                  capture="environment"
                  className="hidden"
                  onChange={handleFileChange}
                />

                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  onClick={() => fileInputRef.current?.click()}
                  className="h-8 w-8 p-0 text-on-surface-variant hover:text-primary"
                  title="Foto aufnehmen oder Bild hochladen"
                  aria-label="Foto anhängen"
                >
                  <Camera className="w-4 h-4" />
                </Button>

                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  onClick={handleOpenNotePicker}
                  className="h-8 w-8 p-0 text-on-surface-variant hover:text-amber-400"
                  title="Notiz teilen (ohne Synchronisation)"
                  aria-label="Notiz teilen"
                >
                  <StickyNote className="w-4 h-4" />
                </Button>

                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  onClick={handleOpenCalendarPicker}
                  className="h-8 w-8 p-0 text-on-surface-variant hover:text-cyan-400"
                  title="Kalendereintrag teilen (ohne Synchronisation)"
                  aria-label="Kalendereintrag teilen"
                >
                  <CalendarIcon className="w-4 h-4" />
                </Button>

                <Input
                  value={inputText}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) => setInputText(e.target.value)}
                  placeholder="Nachricht schreiben …"
                  className="flex-1 text-xs h-8"
                  disabled={sending}
                />

                <Button
                  type="submit"
                  disabled={(!inputText.trim() && !selectedImage) || sending}
                  size="sm"
                  className="gap-1.5 px-3 h-8 text-xs"
                >
                  <Send className="w-3.5 h-3.5" />
                  <span>Senden</span>
                </Button>
              </form>
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
                Wähle einen Kontakt oder ein Teammitglied aus, um einen direkten, Ende-zu-Ende verschlüsselten Chat zu starten.
              </p>
            </div>
          )}
        </div>
      </div>

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
    </div>
  )
}
