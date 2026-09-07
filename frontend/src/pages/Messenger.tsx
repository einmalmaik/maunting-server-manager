import React, { useState, useEffect, useRef, useMemo } from 'react'
import { useSearchParams, useParams, useNavigate } from 'react-router-dom'
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
  Mic,
  Trash2,
  Play,
  Pause,
  Share2,
  Plus,
  LogOut,
  Sparkles,
  Smile,
} from 'lucide-react'
import { DeviceBadge } from '@/components/social/DeviceBadge'
import { StatusDot, type PresenceStatus } from '@/components/social/StatusIndicator'
import {
  type FriendItem,
  type ChatGroupItem,
  getFriends,
  getGroups,
  createGroup,
  joinGroupByInvite,
  leaveGroup,
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
  deriveGroupBlindMailboxId,
  encryptE2eeMessage,
  decryptE2eeMessage,
  encryptE2eeHybrid,
  decryptE2eeHybrid,
  encryptGroupE2eeMessage,
  decryptGroupE2eeMessage,
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

export interface AudioAttachment {
  dataUrl: string
  durationSeconds: number
  mimeType: string
}

export interface ChatMessage {
  id: number
  senderId: number
  senderName?: string
  text: string
  createdAt: string
  isSelf: boolean
  noteAttachment?: NoteAttachment
  calendarAttachment?: CalendarAttachment
  imageAttachment?: ImageAttachment
  audioAttachment?: AudioAttachment
}

function formatDuration(sec: number): string {
  const m = Math.floor(sec / 60)
  const s = Math.floor(sec % 60)
  return `${m}:${s < 10 ? '0' : ''}${s}`
}

function getStoredAudioConstraints(): MediaTrackConstraints {
  try {
    const micId = localStorage.getItem('msm_preferred_mic_id')
    const noiseSuppression = localStorage.getItem('msm_audio_noise_suppression') !== 'false'
    const echoCancellation = localStorage.getItem('msm_audio_echo_cancellation') !== 'false'
    const autoGainControl = localStorage.getItem('msm_audio_auto_gain') !== 'false'
    return {
      ...(micId ? { deviceId: { exact: micId } } : {}),
      echoCancellation,
      noiseSuppression,
      autoGainControl,
    }
  } catch {
    return {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    }
  }
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

const STICKERS = [
  { emoji: '🛡️', label: 'Singra Shield' },
  { emoji: '🔐', label: 'Vault E2EE' },
  { emoji: '🔑', label: 'Master Key' },
  { emoji: '⚡', label: 'Turbo' },
  { emoji: '🔥', label: 'Feuer' },
  { emoji: '🚀', label: 'Rocket' },
  { emoji: '🎉', label: 'Party' },
  { emoji: '❤️', label: 'Liebe' },
  { emoji: '👍', label: 'Daumen hoch' },
  { emoji: '💯', label: '100%' },
  { emoji: '☕', label: 'Kaffee' },
  { emoji: '🎮', label: 'Gaming' },
  { emoji: '🤖', label: 'KI-Assistent' },
  { emoji: '⭐', label: 'Stern' },
  { emoji: '👏', label: 'Applaus' },
  { emoji: '🥳', label: 'Feier' },
]

const QUICK_EMOJIS = [
  '😀', '😂', '😍', '😎', '🤔', '😴', '🥳', '😇',
  '👍', '👎', '👏', '🙌', '🤝', '❤️', '🔥', '🚀',
  '💡', '🛡️', '🔒', '🔑', '🎮', '☕', '✨', '💯',
]

export function Messenger() {
  const { user } = useAuthStore()
  const [searchParams] = useSearchParams()
  const { inviteCode } = useParams<{ inviteCode?: string }>()
  const navigate = useNavigate()
  const queryUserId = searchParams.get('userId')

  const [friends, setFriends] = useState<FriendItem[]>([])
  const [groups, setGroups] = useState<ChatGroupItem[]>([])
  const [teamMembers, setTeamMembers] = useState<Array<{ member: TeamMember; teamName: string }>>([])
  
  // Selection
  const [activeContact, setActiveContact] = useState<ChatContact | null>(null)
  const [activeGroup, setActiveGroup] = useState<ChatGroupItem | null>(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [filterTab, setFilterTab] = useState<'all' | 'groups' | 'friends' | 'teams'>('all')
  const [mobileNavTab, setMobileNavTab] = useState<'chats' | 'updates' | 'community' | 'calls'>('chats')

  // Conversation state
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [inputText, setInputText] = useState('')
  const [loadingMessages, setLoadingMessages] = useState(false)
  const [sending, setSending] = useState(false)
  const [blindMailboxId, setBlindMailboxId] = useState<string>('')
  const [localKeyPair, setLocalKeyPair] = useState<LocalE2eeKeyPair | null>(null)
  const [recipientPublicKeyJwk, setRecipientPublicKeyJwk] = useState<string | null>(null)

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
  const audioInstanceRef = useRef<HTMLAudioElement | null>(null)

  const fileInputRef = useRef<HTMLInputElement>(null)
  const headerFileInputRef = useRef<HTMLInputElement>(null)
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

  // 2. Load Friends, Groups, and Team Members
  const loadData = async () => {
    try {
      const [friendsData, groupsData, teamsData] = await Promise.all([
        getFriends().catch(() => []),
        getGroups().catch(() => []),
        teamsApi.list().catch(() => []),
      ])
      setFriends(friendsData)
      setGroups(groupsData)

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

    return list.sort((a, b) => {
      const statusOrder: Record<string, number> = { online: 0, away: 1, invisible: 2 }
      const diff = (statusOrder[a.status] ?? 3) - (statusOrder[b.status] ?? 3)
      if (diff !== 0) return diff
      return a.username.localeCompare(b.username)
    })
  }, [friends, teamMembers])

  const filteredContacts = useMemo(() => {
    return contactsList.filter((c) => {
      if (filterTab === 'groups') return false
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

  const filteredGroups = useMemo(() => {
    if (filterTab === 'friends' || filterTab === 'teams') return []
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
      setRecipientPublicKeyJwk(null)
      setMessages([])
      deriveGroupBlindMailboxId(activeGroup.id).then((mid) => {
        if (active) setBlindMailboxId(mid)
      })
    } else if (activeContact && currentUserId) {
      const targetUserId = activeContact.userId
      setRecipientPublicKeyJwk(null)
      setMessages([])

      deriveBlindMailboxId(currentUserId, targetUserId).then((mid) => {
        if (active) setBlindMailboxId(mid)
      })

      getE2eePublicKey(targetUserId).then((res) => {
        if (active && res?.public_key) {
          setRecipientPublicKeyJwk(res.public_key)
        }
      }).catch(() => {})
    } else {
      setBlindMailboxId('')
      setRecipientPublicKeyJwk(null)
      setMessages([])
    }

    return () => {
      active = false
    }
  }, [activeContact, activeGroup, currentUserId])

  // 5. Load and decrypt messages
  const loadMessages = async () => {
    if (!blindMailboxId || !currentUserId) return
    setLoadingMessages(true)
    try {
      const envelopes = await fetchE2eeEnvelopes(blindMailboxId)
      const decryptedList: ChatMessage[] = []

      for (const env of envelopes) {
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

          let text = plain
          let senderId = activeContact?.userId ?? 0
          let senderName: string | undefined = undefined
          let isSelf = false
          let noteAttachment: NoteAttachment | undefined = undefined
          let calendarAttachment: CalendarAttachment | undefined = undefined
          let imageAttachment: ImageAttachment | undefined = undefined
          let audioAttachment: AudioAttachment | undefined = undefined

          try {
            const parsed = JSON.parse(plain)
            if (typeof parsed === 'object' && parsed !== null) {
              text = parsed.text || ''
              senderId = parsed.sender_id || senderId
              senderName = parsed.sender_name
              isSelf = senderId === currentUserId
              if (parsed.note_attachment) noteAttachment = parsed.note_attachment
              if (parsed.calendar_attachment) calendarAttachment = parsed.calendar_attachment
              if (parsed.image_attachment) imageAttachment = parsed.image_attachment
              if (parsed.audio_attachment) audioAttachment = parsed.audio_attachment
            }
          } catch {
            if (plain.startsWith('[ME]:')) {
              text = plain.replace('[ME]:', '')
              isSelf = true
              senderId = currentUserId
            }
          }

          decryptedList.push({
            id: env.id,
            senderId,
            senderName,
            text,
            createdAt: env.created_at,
            isSelf,
            noteAttachment,
            calendarAttachment,
            imageAttachment,
            audioAttachment,
          })
        } catch {
          decryptedList.push({
            id: env.id,
            senderId: activeContact?.userId ?? 0,
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
    if (blindMailboxId && (activeContact || activeGroup)) {
      loadMessages()
      const interval = setInterval(loadMessages, 4000)
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

  // 6. Send message (text, note, cal, img, audio)
  const handleSendMessage = async (
    customText?: string,
    note?: NoteAttachment,
    cal?: CalendarAttachment,
    img?: ImageAttachment,
    audio?: AudioAttachment
  ) => {
    const rawText = customText !== undefined ? customText : inputText.trim()
    if (
      (!rawText && !note && !cal && !img && !audio) ||
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

      if (note) payloadObj.note_attachment = note
      if (cal) payloadObj.calendar_attachment = cal
      if (img) payloadObj.image_attachment = img
      if (audio) payloadObj.audio_attachment = audio

      const payload = JSON.stringify(payloadObj)
      let ciphertext: string

      if (activeGroup) {
        ciphertext = await encryptGroupE2eeMessage(payload, activeGroup.id)
        await relayE2eeEnvelope({
          blind_mailbox_id: blindMailboxId,
          ciphertext_envelope: ciphertext,
          group_id: activeGroup.id,
        })
      } else if (activeContact) {
        const targetUserId = activeContact.userId
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
      }

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

  // Voice recording handlers
  const startRecording = async () => {
    if (typeof navigator === 'undefined' || !navigator.mediaDevices?.getUserMedia) {
      toast.error('Mikrofon in dieser Browser-Umgebung nicht verfügbar.')
      return
    }

    try {
      const constraints = getStoredAudioConstraints()
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

      timerIntervalRef.current = setInterval(() => {
        setRecordingDuration((prev) => prev + 1)
      }, 1000)
    } catch {
      toast.error('Mikrofonzugriff verweigert oder nicht verfügbar.')
    }
  }

  const stopRecording = (shouldSend: boolean) => {
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

  // Header quick photo capture
  const handleHeaderFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
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
        const img: ImageAttachment = { dataUrl, name: file.name }
        if (activeContact || activeGroup) {
          setSelectedImage(img)
        } else {
          setPendingPhotoToSend(img)
          setIsSendPhotoOpen(true)
        }
      }
    }
    reader.readAsDataURL(file)
    if (headerFileInputRef.current) headerFileInputRef.current.value = ''
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

  const isChatOpen = Boolean(activeContact || activeGroup)

  return (
    <div className="flex h-full w-full min-h-0 flex-1 flex-col overflow-hidden bg-surface">
      {/* Slim, Compact Header (Matches AiChat header height) */}
      <header className="h-12 shrink-0 border-b border-outline-variant/20 bg-surface-container/70 backdrop-blur px-3 sm:px-4 flex items-center justify-between z-10">
        <div className="flex items-center gap-2.5 min-w-0">
          {/* Mobile Back Button when inside a chat */}
          {isChatOpen && (
            <button
              type="button"
              onClick={() => {
                setActiveContact(null)
                setActiveGroup(null)
              }}
              className="md:hidden p-1.5 -ml-1.5 rounded-lg hover:bg-surface-container-high text-on-surface-variant"
              aria-label="Zurück zur Kontaktliste"
            >
              <ChevronLeft className="w-5 h-5" />
            </button>
          )}

          {isChatOpen ? (
            <div className="flex items-center gap-2.5 min-w-0">
              {activeContact ? (
                <>
                  <div className="relative shrink-0">
                    <Avatar src={activeContact.avatarUrl} name={activeContact.username} size="sm" />
                    <StatusDot
                      status={activeContact.status}
                      size="sm"
                      className="absolute bottom-0 right-0"
                    />
                  </div>
                  <div className="min-w-0">
                    <div className="flex items-center gap-1.5">
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
                    <div className="text-[10px] text-on-surface-variant/80 flex items-center gap-1">
                      <Lock className="w-2.5 h-2.5 text-emerald-400 shrink-0" />
                      <span className="truncate">Ende-zu-Ende verschlüsselt</span>
                    </div>
                  </div>
                </>
              ) : activeGroup ? (
                <>
                  <div className="w-8 h-8 rounded-full bg-primary/15 text-primary flex items-center justify-center font-bold text-xs shrink-0">
                    {activeGroup.avatar_url ? (
                      <img src={activeGroup.avatar_url} alt="" className="w-full h-full rounded-full object-cover" />
                    ) : (
                      <UsersRound className="w-4 h-4" />
                    )}
                  </div>
                  <div className="min-w-0">
                    <div className="flex items-center gap-1.5">
                      <span className="font-headline text-body-sm font-bold text-primary truncate">
                        {activeGroup.name}
                      </span>
                      <Badge variant="default" className="text-[9px] px-1.5 py-0">
                        {activeGroup.member_count} {activeGroup.member_count === 1 ? 'Mitglied' : 'Mitglieder'}
                      </Badge>
                    </div>
                    <div className="text-[10px] text-on-surface-variant/80 flex items-center gap-1">
                      <Lock className="w-2.5 h-2.5 text-emerald-400 shrink-0" />
                      <span className="truncate">Gruppen-E2EE verschlüsselt</span>
                    </div>
                  </div>
                </>
              ) : null}
            </div>
          ) : (
            <div className="flex items-center gap-2">
              <div className="w-7 h-7 rounded-lg bg-primary/10 flex items-center justify-center text-primary">
                <MessageSquare className="w-4 h-4" />
              </div>
              <span className="font-headline text-body-md font-bold text-primary">Messenger</span>
              <span className="text-[11px] text-on-surface-variant/60 hidden sm:inline">• Chats & Gruppen</span>
            </div>
          )}
        </div>

        {/* Header Right Actions */}
        <div className="flex items-center gap-1">
          {activeGroup && (
            <>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => handleCopyInviteLink(activeGroup)}
                className="h-8 gap-1.5 text-xs px-2.5 text-primary hover:bg-primary/10"
                title="Einladungslink kopieren"
              >
                <Share2 className="w-3.5 h-3.5" />
                <span className="hidden sm:inline">Einladen</span>
              </Button>

              {activeGroup.owner_user_id !== currentUserId && (
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => handleLeaveGroup(activeGroup)}
                  className="h-8 w-8 text-on-surface-variant hover:text-error"
                  title="Gruppe verlassen"
                >
                  <LogOut className="w-4 h-4" />
                </Button>
              )}
            </>
          )}

          {/* Quick Camera Button in Header (WhatsApp Style) */}
          <Button
            variant="ghost"
            size="icon"
            onClick={() => headerFileInputRef.current?.click()}
            className="h-8 w-8 text-on-surface-variant hover:text-primary"
            title="Foto aufnehmen"
            aria-label="Foto aufnehmen"
          >
            <Camera className="w-4 h-4" />
          </Button>
          <input
            ref={headerFileInputRef}
            type="file"
            accept="image/*"
            capture="environment"
            className="hidden"
            onChange={handleHeaderFileChange}
          />

          {!isChatOpen && (
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setIsCreateGroupOpen(true)}
              className="h-7 text-xs gap-1 px-2.5 border-outline-variant/40 hover:border-primary"
              aria-label="Neue Gruppe erstellen"
            >
              <Plus className="w-3.5 h-3.5" />
              <span>Gruppe</span>
            </Button>
          )}

          <Button
            variant="ghost"
            size="icon"
            onClick={() => {
              loadData()
              if (isChatOpen) void loadMessages()
            }}
            disabled={loadingMessages}
            className="h-8 w-8 text-on-surface-variant"
            aria-label="Aktualisieren"
            title="Aktualisieren"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loadingMessages ? 'animate-spin' : ''}`} />
          </Button>
        </div>
      </header>

      {/* Main Split Layout: Left Contact/Group List, Right Chat Area */}
      <div className="flex-1 flex min-h-0 overflow-hidden">
        {/* Left Column: WhatsApp-style Contacts & Groups List */}
        <div
          className={`w-full md:w-80 lg:w-96 shrink-0 flex flex-col min-h-0 border-r border-outline-variant/20 bg-surface-container-low/60 ${
            isChatOpen ? 'hidden md:flex' : 'flex'
          }`}
        >
          {/* Top Search & Category Tabs */}
          <div className="p-2.5 border-b border-outline-variant/15 space-y-2 bg-surface-container/40">
            <div className="relative">
              <Search className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-on-surface-variant/60" />
              <Input
                value={searchQuery}
                onChange={(e: React.ChangeEvent<HTMLInputElement>) => setSearchQuery(e.target.value)}
                placeholder="Freunde oder Teammitglieder suchen …"
                className="text-xs pl-8 h-8 bg-surface-container-lowest/80 border-outline-variant/30"
              />
            </div>

            {/* WhatsApp Filter Tabs (Chats Mode) */}
            {mobileNavTab === 'chats' && (
              <div className="flex items-center gap-1 overflow-x-auto no-scrollbar py-0.5">
                <Button
                  variant={filterTab === 'all' ? 'primary' : 'ghost'}
                  size="sm"
                  onClick={() => setFilterTab('all')}
                  className="text-xs h-6 px-2.5 rounded-full"
                >
                  Alle
                </Button>
                <Button
                  variant={filterTab === 'groups' ? 'primary' : 'ghost'}
                  size="sm"
                  onClick={() => setFilterTab('groups')}
                  className="text-xs h-6 px-2.5 rounded-full"
                >
                  Gruppen ({groups.length})
                </Button>
                <Button
                  variant={filterTab === 'friends' ? 'primary' : 'ghost'}
                  size="sm"
                  onClick={() => setFilterTab('friends')}
                  className="text-xs h-6 px-2.5 rounded-full"
                >
                  Freunde ({contactsList.filter((c) => c.isFriend).length})
                </Button>
                <Button
                  variant={filterTab === 'teams' ? 'primary' : 'ghost'}
                  size="sm"
                  onClick={() => setFilterTab('teams')}
                  className="text-xs h-6 px-2.5 rounded-full"
                >
                  Teams ({contactsList.filter((c) => c.teamName).length})
                </Button>
              </div>
            )}
          </div>

          {/* List Scroll Area */}
          <div className="flex-1 overflow-y-auto p-2 space-y-1">
            {/* View 1: Standard Chats Mode */}
            {mobileNavTab === 'chats' && (
              <>
                {/* Groups Section */}
                {filteredGroups.length > 0 && (
                  <div className="space-y-1 mb-2">
                    <div className="px-2 py-1 text-[11px] font-semibold text-on-surface-variant/70 uppercase tracking-wider flex items-center justify-between">
                      <span>Gruppen</span>
                      <span className="text-[10px]">{filteredGroups.length}</span>
                    </div>
                    {filteredGroups.map((g) => {
                      const isSelected = activeGroup?.id === g.id
                      return (
                        <button
                          key={`g-${g.id}`}
                          type="button"
                          onClick={() => {
                            setActiveGroup(g)
                            setActiveContact(null)
                          }}
                          className={`w-full flex items-center justify-between p-2.5 rounded-xl text-left transition-all ${
                            isSelected
                              ? 'bg-primary/15 border border-primary/30 shadow-xs'
                              : 'hover:bg-surface-container-high/60 border border-transparent'
                          }`}
                        >
                          <div className="flex items-center gap-3 min-w-0">
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
                      return (
                        <button
                          key={`${c.isFriend ? 'f' : 't'}-${c.userId}`}
                          type="button"
                          onClick={() => {
                            setActiveContact(c)
                            setActiveGroup(null)
                          }}
                          className={`w-full flex items-center justify-between p-2.5 rounded-xl text-left transition-all ${
                            isSelected
                              ? 'bg-primary/15 border border-primary/30 shadow-xs'
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

            {/* View 2: Aktuelles (Status / Presence of contacts) */}
            {mobileNavTab === 'updates' && (
              <div className="space-y-3 p-1">
                <div className="p-3 rounded-xl bg-surface-container/60 border border-outline-variant/30 text-xs">
                  <div className="flex items-center gap-2 font-semibold text-primary mb-1">
                    <Sparkles className="w-4 h-4" />
                    <span>Aktuelles & Status deiner Kontakte</span>
                  </div>
                  <p className="text-[11px] text-on-surface-variant">
                    Hier siehst du, wer gerade im Panel, in Gameservern oder auf Desktop/Mobile aktiv ist.
                  </p>
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
            )}

            {/* View 3: Community (Groups & public invite links) */}
            {mobileNavTab === 'community' && (
              <div className="space-y-3 p-1">
                <div className="p-3.5 rounded-xl bg-primary/10 border border-primary/20 space-y-2">
                  <div className="flex items-center gap-2 font-semibold text-xs text-primary">
                    <UsersRound className="w-4 h-4" />
                    <span>Communities & Gruppen</span>
                  </div>
                  <p className="text-[11px] text-on-surface-variant">
                    Erstelle Gruppen mit öffentlichen Einladungslinks und teile sie mit Freunden oder Teams.
                  </p>
                  <Button
                    type="button"
                    variant="primary"
                    size="sm"
                    onClick={() => setIsCreateGroupOpen(true)}
                    className="w-full text-xs h-8 gap-1.5"
                  >
                    <Plus className="w-3.5 h-3.5" />
                    <span>Neue Gruppe erstellen</span>
                  </Button>
                </div>

                <div className="space-y-1.5">
                  <div className="px-1 text-[11px] font-semibold text-on-surface-variant/70 uppercase tracking-wider">
                    Deine Gruppen ({groups.length})
                  </div>
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

            {/* View 4: Audio / Calls Info */}
            {mobileNavTab === 'calls' && (
              <div className="space-y-3 p-1">
                <div className="p-3.5 rounded-xl bg-surface-container/60 border border-outline-variant/30 space-y-2 text-xs">
                  <div className="flex items-center gap-2 font-semibold text-primary">
                    <Mic className="w-4 h-4" />
                    <span>Sprachnachrichten & Audio</span>
                  </div>
                  <p className="text-[11px] text-on-surface-variant">
                    Sprachnachrichten werden in DIS AES-256-GCM verschlüsselt übertragen. Mit Noise Cancelling und Echounterdrückung für glasklare Audioqualität.
                  </p>
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    onClick={() => navigate('/profile')}
                    className="w-full text-xs h-8 gap-1.5 mt-1 border-outline-variant/40 hover:border-primary"
                  >
                    <Mic className="w-3.5 h-3.5" />
                    <span>Mikrofon-Test im Profil öffnen</span>
                  </Button>
                </div>
              </div>
            )}
          </div>

          {/* Floating Action Button for Mobile: Positioned cleanly above bottom bar */}
          {!isChatOpen && (
            <button
              type="button"
              onClick={() => setIsCreateGroupOpen(true)}
              className="fixed bottom-20 right-5 md:hidden z-20 w-14 h-14 rounded-full bg-primary text-on-primary shadow-xl flex items-center justify-center hover:scale-105 active:scale-95 transition-transform"
              aria-label="Neue Gruppe erstellen"
              title="Neue Gruppe erstellen"
            >
              <UsersRound className="w-6 h-6" />
            </button>
          )}

          {/* Mobile WhatsApp-Style Bottom Navigation Bar */}
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

              <button
                type="button"
                onClick={() => setMobileNavTab('calls')}
                className={`flex flex-col items-center justify-center flex-1 py-1 transition-colors ${
                  mobileNavTab === 'calls' ? 'text-primary font-semibold' : 'text-on-surface-variant/70 hover:text-on-surface'
                }`}
                aria-label="Audio"
              >
                <div className={`p-1 rounded-full ${mobileNavTab === 'calls' ? 'bg-primary/15' : ''}`}>
                  <Mic className="w-4 h-4" />
                </div>
                <span className="text-[10px] mt-0.5">Audio</span>
              </button>
            </nav>
          )}
        </div>

        {/* Right Column: Chat Thread & Input Area */}
        <div
          className={`flex-1 flex flex-col min-h-0 bg-surface-container-lowest/30 ${
            !isChatOpen ? 'hidden md:flex' : 'flex'
          }`}
        >
          {isChatOpen ? (
            <>
              {/* Message Thread Scroll Area */}
              <div
                ref={scrollContainerRef}
                className="flex-1 overflow-y-auto p-4 space-y-3"
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

                {messages.map((msg) => (
                  <div
                    key={msg.id}
                    className={`flex flex-col ${msg.isSelf ? 'items-end' : 'items-start'}`}
                  >
                    <div
                      className={`max-w-[85%] md:max-w-[70%] px-3.5 py-2 rounded-2xl text-xs break-words shadow-xs space-y-2 ${
                        msg.isSelf
                          ? 'bg-primary text-on-primary rounded-br-xs'
                          : 'bg-surface-container-high text-on-surface rounded-bl-xs border border-outline-variant/20'
                      }`}
                    >
                      {/* Group sender name if in group and not self */}
                      {activeGroup && !msg.isSelf && (
                        <div className="text-[10px] font-bold text-tertiary">
                          {msg.senderName || `Benutzer #${msg.senderId}`}
                        </div>
                      )}

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

                      {/* Audio / Voice Message Attachment */}
                      {msg.audioAttachment && (
                        <div className="flex items-center gap-3 py-1 min-w-[200px] max-w-[280px]">
                          <button
                            type="button"
                            onClick={() => togglePlayAudio(msg.id, msg.audioAttachment!.dataUrl)}
                            className={`p-2.5 rounded-full shrink-0 shadow-xs transition-all ${
                              msg.isSelf
                                ? 'bg-white text-primary hover:bg-white/90'
                                : 'bg-primary text-on-primary hover:opacity-90'
                            }`}
                            aria-label={playingAudioId === msg.id ? 'Pause' : 'Abspielen'}
                          >
                            {playingAudioId === msg.id ? (
                              <Pause className="w-4 h-4" />
                            ) : (
                              <Play className="w-4 h-4 translate-x-0.5" />
                            )}
                          </button>
                          <div className="flex-1 min-w-0 space-y-1">
                            <div className="relative h-2 w-full bg-black/10 rounded-full overflow-hidden">
                              <div
                                className={`h-full transition-all ${msg.isSelf ? 'bg-white' : 'bg-primary'}`}
                                style={{
                                  width:
                                    playingAudioId === msg.id && msg.audioAttachment.durationSeconds > 0
                                      ? `${Math.min(100, (audioCurrentTime / msg.audioAttachment.durationSeconds) * 100)}%`
                                      : '0%',
                                }}
                              />
                            </div>
                            <div className="flex justify-between items-center text-[10px] opacity-80">
                              <span className="flex items-center gap-1">
                                <Mic className="w-2.5 h-2.5" />
                                <span>Sprachnachricht</span>
                              </span>
                              <span>
                                {playingAudioId === msg.id
                                  ? formatDuration(audioCurrentTime)
                                  : formatDuration(msg.audioAttachment.durationSeconds)}
                              </span>
                            </div>
                          </div>
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
              <div className="p-2.5 border-t border-outline-variant/20 bg-surface-container-low">
                {isRecording ? (
                  /* WhatsApp-style Voice Recording Banner */
                  <div className="flex items-center justify-between gap-3 px-3 py-1.5 bg-error/10 border border-error/30 rounded-xl">
                    <div className="flex items-center gap-2">
                      <span className="relative flex h-3 w-3">
                        <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-error opacity-75"></span>
                        <span className="relative inline-flex rounded-full h-3 w-3 bg-error"></span>
                      </span>
                      <span className="text-xs font-semibold text-error">
                        {formatDuration(recordingDuration)}
                      </span>
                      <span className="text-xs text-on-surface-variant ml-2 hidden sm:inline">
                        Sprachaufnahme läuft …
                      </span>
                    </div>

                    <div className="flex items-center gap-2">
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        onClick={() => stopRecording(false)}
                        className="h-7 px-2 text-xs text-error hover:bg-error/20 gap-1"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                        <span>Abbrechen</span>
                      </Button>
                      <Button
                        type="button"
                        variant="primary"
                        size="sm"
                        onClick={() => stopRecording(true)}
                        className="h-7 px-3 text-xs gap-1"
                      >
                        <Send className="w-3.5 h-3.5" />
                        <span>Senden</span>
                      </Button>
                    </div>
                  </div>
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
                      <div className="grid grid-cols-4 sm:grid-cols-8 gap-1.5 max-h-36 overflow-y-auto p-1">
                        {STICKERS.map((stk) => (
                          <button
                            key={stk.label}
                            type="button"
                            onClick={() => {
                              void handleSendMessage(stk.emoji)
                              setIsStickerPickerOpen(false)
                            }}
                            className="flex flex-col items-center justify-center p-1.5 rounded-lg hover:bg-surface-container-high transition-transform hover:scale-110"
                            title={stk.label}
                          >
                            <span className="text-2xl">{stk.emoji}</span>
                            <span className="text-[9px] text-on-surface-variant/70 truncate w-full text-center mt-0.5">{stk.label}</span>
                          </button>
                        ))}
                      </div>
                    ) : (
                      <div className="grid grid-cols-8 sm:grid-cols-12 gap-1 max-h-36 overflow-y-auto p-1">
                        {QUICK_EMOJIS.map((emoji) => (
                          <button
                            key={emoji}
                            type="button"
                            onClick={() => {
                              setInputText((prev) => prev + emoji)
                            }}
                            className="p-1.5 text-lg rounded-lg hover:bg-surface-container-high transition-transform hover:scale-125 flex items-center justify-center"
                          >
                            {emoji}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                )}

                <form
                  onSubmit={(e) => {
                    e.preventDefault()
                    handleSendMessage(inputText, undefined, undefined, selectedImage || undefined)
                  }}
                  className="flex items-center gap-1.5 sm:gap-2"
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

                  <Button
                    type="button"
                    variant={isStickerPickerOpen ? 'secondary' : 'ghost'}
                    size="icon"
                    onClick={() => setIsStickerPickerOpen((prev) => !prev)}
                    className="h-8 w-8 p-0 text-on-surface-variant hover:text-amber-400"
                    title="Sticker & Emojis"
                    aria-label="Sticker auswählen"
                  >
                    <Smile className="w-4 h-4" />
                  </Button>

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
                    className="flex-1 text-xs h-8 bg-surface-container-lowest/80 border-outline-variant/30"
                    disabled={sending}
                  />

                    {/* WhatsApp-style dynamic Mic / Send button */}
                    {inputText.trim() || selectedImage ? (
                      <Button
                        type="submit"
                        disabled={sending}
                        size="sm"
                        className="gap-1.5 px-3 h-8 text-xs"
                      >
                        <Send className="w-3.5 h-3.5" />
                        <span>Senden</span>
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
                    )}
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
    </div>
  )
}
