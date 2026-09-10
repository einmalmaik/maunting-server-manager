import React, { useState, useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Button,
  Input,
  Dialog,
  DialogContent,
  Avatar,
} from '@/Singra/UI'
import {
  Send,
  RefreshCw,
  Lock,
  Camera,
  StickyNote,
  Calendar as CalendarIcon,
  ExternalLink,
  X,
  Clock,
  MapPin,
} from 'lucide-react'
import { DeviceBadge } from './DeviceBadge'
import { StatusDot } from './StatusIndicator'
import {
  type FriendItem,
  relayE2eeEnvelope,
  fetchE2eeEnvelopes,
  getE2eePublicKey,
  setE2eePublicKey,
} from '@/api/social'
import { loadNotesOfflineFirst, loadCalendarEventsOfflineFirst } from '@/lib/offlineSync'
import type { NoteItem } from '@/pages/Notes'
import type { CalendarEventItem } from '@/pages/Calendar'
import {
  deriveBlindMailboxId,
  encryptE2eeMessage,
  decryptE2eeMessage,
  decryptE2eeHybrid,
  getOrGenerateLocalKeyPair,
  type LocalE2eeKeyPair,
} from '@/services/e2eeCrypto'
import { toast } from '@/stores/toastStore'

interface E2EEChatModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  currentUserId: number
  friend: FriendItem | null
}

interface NoteAttachment {
  title: string
  content: string
  color?: string
  category?: string
}

interface CalendarAttachment {
  title: string
  start: string
  end: string
  description?: string
  location?: string
}

interface ImageAttachment {
  dataUrl: string
  name?: string
}

interface DecryptedMessage {
  id: number
  text: string
  createdAt: string
  isSelf: boolean
  noteAttachment?: NoteAttachment
  calendarAttachment?: CalendarAttachment
  imageAttachment?: ImageAttachment
}

export function E2EEChatModal({ open, onOpenChange, currentUserId, friend }: E2EEChatModalProps) {
  const navigate = useNavigate()
  const [messages, setMessages] = useState<DecryptedMessage[]>([])
  const [inputText, setInputText] = useState('')
  const [loading, setLoading] = useState(false)
  const [sending, setSending] = useState(false)
  const [blindMailboxId, setBlindMailboxId] = useState<string>('')
  const [localKeyPair, setLocalKeyPair] = useState<LocalE2eeKeyPair | null>(null)

  // Attachments
  const [isNotePickerOpen, setIsNotePickerOpen] = useState(false)
  const [userNotes, setUserNotes] = useState<NoteItem[]>([])
  const [isCalendarPickerOpen, setIsCalendarPickerOpen] = useState(false)
  const [userEvents, setUserEvents] = useState<CalendarEventItem[]>([])
  const [selectedImage, setSelectedImage] = useState<ImageAttachment | null>(null)

  const fileInputRef = useRef<HTMLInputElement>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const scrollContainerRef = useRef<HTMLDivElement>(null)
  const justSentRef = useRef<boolean>(false)

  const targetUserId = friend?.user_id ?? friend?.id ?? 0

  // Initialize local key pair and upload public key to server
  useEffect(() => {
    if (!open || !currentUserId) return
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
        // Non-blocking key registration
      }
    }).catch(() => {})

    return () => {
      active = false
    }
  }, [open, currentUserId])


  // Derive deterministic blind mailbox ID when friend changes
  useEffect(() => {
    if (!open || !friend || !currentUserId || !targetUserId) return
    let active = true

    deriveBlindMailboxId(currentUserId, targetUserId).then((mailboxId) => {
      if (active) {
        setBlindMailboxId(mailboxId)
      }
    })

    return () => {
      active = false
    }
  }, [open, friend, currentUserId, targetUserId])

  // Load and decrypt messages from blind mailbox
  const loadMessages = async () => {
    if (!friend || !blindMailboxId || !currentUserId || !targetUserId) return
    setLoading(true)
    try {
      const envelopes = await fetchE2eeEnvelopes(blindMailboxId)
      const decryptedList: DecryptedMessage[] = []

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
            text: '[Verschlüsselte Nachricht]',
            createdAt: env.created_at,
            isSelf: false,
          })
        }
      }

      setMessages(decryptedList)
    } catch {
      // Offline fallback
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (open && blindMailboxId) {
      loadMessages()
      const interval = setInterval(loadMessages, 4000)
      return () => clearInterval(interval)
    }
  }, [open, blindMailboxId, localKeyPair])

  // Autoscroll
  useEffect(() => {
    const container = scrollContainerRef.current
    if (!container) return

    const isNearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 100
    if (justSentRef.current || isNearBottom) {
      messagesEndRef.current?.scrollIntoView?.({ behavior: 'smooth' })
      justSentRef.current = false
    }
  }, [messages])

  const handleSend = async (
    customText?: string,
    note?: NoteAttachment,
    cal?: CalendarAttachment,
    img?: ImageAttachment
  ) => {
    const rawMessage = customText !== undefined ? customText : inputText.trim()
    if (
      (!rawMessage && !note && !cal && !img) ||
      !friend ||
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
        text: rawMessage,
        timestamp: new Date().toISOString(),
      }

      if (note) payloadObj.note_attachment = note
      if (cal) payloadObj.calendar_attachment = cal
      if (img) payloadObj.image_attachment = img

      const payload = JSON.stringify(payloadObj)

      const ciphertext = await encryptE2eeMessage(payload, currentUserId, targetUserId)

      await relayE2eeEnvelope({
        blind_mailbox_id: blindMailboxId,
        ciphertext_envelope: ciphertext,
        recipient_id: targetUserId,
      })

      setInputText('')
      setSelectedImage(null)
      justSentRef.current = true
      await loadMessages()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Fehler beim Senden der Nachricht'
      toast.error(msg)
    } finally {
      setSending(false)
    }
  }

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    if (!file.type.startsWith('image/')) {
      toast.error('Bitte ein Bild auswählen.')
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

  const handleOpenNotes = async () => {
    try {
      const res = await loadNotesOfflineFirst()
      setUserNotes(res.notes.filter((n) => !n.is_archived))
      setIsNotePickerOpen(true)
    } catch {
      toast.error('Notizen konnten nicht geladen werden.')
    }
  }

  const handleOpenCalendar = async () => {
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

  if (!friend) return null

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl h-[600px] p-0 flex flex-col" showCloseButton>
        {/* Header */}
        <div className="p-3.5 border-b border-outline-variant/30 bg-surface-container flex items-center justify-between pr-12">
          <div className="flex items-center gap-3 min-w-0">
            <div className="relative shrink-0">
              <Avatar src={friend.avatar_url} name={friend.username} size="sm" />
              <StatusDot
                status={friend.presence?.status || 'invisible'}
                size="sm"
                className="absolute bottom-0 right-0"
              />
            </div>
            <div className="min-w-0">
              <div className="font-headline text-body-sm font-bold text-primary flex items-center gap-2 truncate">
                <span>{friend.username}</span>
                <DeviceBadge deviceType={friend.presence?.device_type} />
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
              onClick={() => {
                onOpenChange(false)
                navigate(`/chat?userId=${targetUserId}`)
              }}
              title="Im großen Chatraum öffnen"
              aria-label="Im großen Chatraum öffnen"
              className="p-1 h-7 w-7 text-on-surface-variant hover:text-primary"
            >
              <ExternalLink className="w-3.5 h-3.5" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              onClick={() => void loadMessages()}
              disabled={loading}
              aria-label="Nachrichten aktualisieren"
              className="p-1 h-7 w-7"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            </Button>
          </div>
        </div>

        {/* Message Thread */}
        <div
          ref={scrollContainerRef}
          className="flex-1 overflow-y-auto p-4 space-y-3 bg-surface-container-lowest/40"
        >
          {/* Subtle WhatsApp-style encryption indicator */}
          <div className="py-1 text-center">
            <div className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-surface-container-high/60 border border-outline-variant/30 text-[11px] text-on-surface-variant">
              <Lock className="w-3 h-3 text-emerald-400" />
              <span>Nachrichten in diesem Chat sind Ende-zu-Ende verschlüsselt.</span>
            </div>
          </div>

          {messages.length === 0 && !loading && (
            <div className="py-16 text-center text-on-surface-variant/70 text-xs">
              Keine Nachrichten. Schreibe die erste Nachricht!
            </div>
          )}

          {messages.map((msg) => (
            <div
              key={msg.id}
              className={`flex flex-col ${msg.isSelf ? 'items-end' : 'items-start'}`}
            >
              <div
                className={`max-w-[75%] px-3.5 py-2.5 rounded-2xl text-xs break-words shadow-sm space-y-1.5 ${
                  msg.isSelf
                    ? 'bg-primary text-on-primary rounded-br-xs'
                    : 'bg-surface-container-high text-on-surface rounded-bl-xs border border-outline-variant/20'
                }`}
              >
                {/* Image */}
                {msg.imageAttachment && (
                  <img
                    src={msg.imageAttachment.dataUrl}
                    alt="Anhang"
                    className="max-h-48 w-auto object-cover rounded-lg my-1"
                  />
                )}

                {/* Note Attachment */}
                {msg.noteAttachment && (
                  <div
                    className={`p-2.5 rounded-xl border text-xs shadow-sm space-y-1.5 ${
                      msg.isSelf
                        ? 'bg-slate-950/80 border-white/20 text-white'
                        : 'bg-surface-container-lowest border-outline-variant/50 text-on-surface'
                    }`}
                  >
                    <div className="flex items-center gap-1.5 font-semibold text-xs text-white">
                      <StickyNote className="w-3.5 h-3.5 text-amber-400" />
                      <span>{msg.noteAttachment.title}</span>
                    </div>
                    <p className="whitespace-pre-wrap text-[11px] text-white/90 line-clamp-3 leading-relaxed">
                      {msg.noteAttachment.content}
                    </p>
                  </div>
                )}

                {/* Calendar Attachment */}
                {msg.calendarAttachment && (
                  <div
                    className={`p-2.5 rounded-xl border text-xs shadow-sm space-y-1.5 ${
                      msg.isSelf
                        ? 'bg-slate-950/80 border-white/20 text-white'
                        : 'bg-surface-container-lowest border-outline-variant/50 text-on-surface'
                    }`}
                  >
                    <div className="flex items-center gap-1.5 font-semibold text-xs text-white">
                      <div className="w-4 h-4 rounded-md bg-cyan-500/20 flex items-center justify-center shrink-0">
                        <CalendarIcon className="w-3 h-3 text-cyan-300" />
                      </div>
                      <span>{msg.calendarAttachment.title}</span>
                    </div>
                    <div className="text-[11px] text-white/90 flex items-center gap-1.5 font-medium">
                      <Clock className="w-3 h-3 text-cyan-400" />
                      <span>
                        {new Date(msg.calendarAttachment.start).toLocaleString([], {
                          dateStyle: 'short',
                          timeStyle: 'short',
                        })}
                      </span>
                    </div>
                    {msg.calendarAttachment.location && (
                      <div className="text-[11px] text-white/80 flex items-center gap-1.5">
                        <MapPin className="w-3 h-3 text-rose-400" />
                        <span>{msg.calendarAttachment.location}</span>
                      </div>
                    )}
                  </div>
                )}

                {msg.text && <p className="leading-relaxed">{msg.text}</p>}
              </div>

              <span className="text-[10px] text-on-surface-variant/50 mt-1 px-1">
                {new Date(msg.createdAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
              </span>
            </div>
          ))}
          <div ref={messagesEndRef} />
        </div>

        {/* Staged Image */}
        {selectedImage && (
          <div className="px-3 py-1.5 border-t border-outline-variant/20 bg-surface-container flex items-center gap-2">
            <img src={selectedImage.dataUrl} alt="Vorschau" className="w-8 h-8 object-cover rounded" />
            <span className="text-xs text-on-surface-variant truncate flex-1">Foto angehängt</span>
            <button
              type="button"
              onClick={() => setSelectedImage(null)}
              className="p-1 text-on-surface-variant hover:text-on-surface"
              aria-label="Entfernen"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        )}

        {/* Footer Input */}
        <form
          onSubmit={(e) => {
            e.preventDefault()
            handleSend(inputText, undefined, undefined, selectedImage || undefined)
          }}
          className="p-2.5 border-t border-outline-variant/30 bg-surface-container-low flex items-center gap-1.5"
        >
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
            className="h-7 w-7 p-0 text-on-surface-variant hover:text-primary"
            title="Foto / Kamera"
            aria-label="Foto / Kamera"
          >
            <Camera className="w-4 h-4" />
          </Button>

          <Button
            type="button"
            variant="ghost"
            size="icon"
            onClick={handleOpenNotes}
            className="h-7 w-7 p-0 text-on-surface-variant hover:text-amber-400"
            title="Notiz teilen"
            aria-label="Notiz teilen"
          >
            <StickyNote className="w-4 h-4" />
          </Button>

          <Button
            type="button"
            variant="ghost"
            size="icon"
            onClick={handleOpenCalendar}
            className="h-7 w-7 p-0 text-on-surface-variant hover:text-cyan-400"
            title="Termin teilen"
            aria-label="Termin teilen"
          >
            <CalendarIcon className="w-4 h-4" />
          </Button>

          <Input
            value={inputText}
            onChange={(e: React.ChangeEvent<HTMLInputElement>) => setInputText(e.target.value)}
            placeholder="Nachricht schreiben …"
            className="flex-1 text-xs h-7.5"
            disabled={sending}
          />

          <Button
            type="submit"
            disabled={(!inputText.trim() && !selectedImage) || sending}
            size="sm"
            className="gap-1 px-3 h-7.5 text-xs"
          >
            <Send className="w-3.5 h-3.5" />
            <span>Senden</span>
          </Button>
        </form>
      </DialogContent>

      {/* Note Picker */}
      <Dialog open={isNotePickerOpen} onOpenChange={setIsNotePickerOpen}>
        <DialogContent className="max-w-md max-h-[60vh] flex flex-col p-4">
          <div className="font-headline text-body-sm font-bold text-primary mb-2">
            Notiz auswählen
          </div>
          <div className="flex-1 overflow-y-auto space-y-2">
            {userNotes.length === 0 ? (
              <p className="text-xs text-on-surface-variant/70 py-4 text-center">Keine Notizen.</p>
            ) : (
              userNotes.map((n) => (
                <div
                  key={n.id}
                  onClick={() => {
                    setIsNotePickerOpen(false)
                    handleSend('', { title: n.title, content: n.content, color: n.color, category: n.category })
                  }}
                  className="p-2.5 rounded-lg border border-outline-variant/30 hover:border-primary/50 cursor-pointer text-xs"
                >
                  <div className="font-semibold text-primary">{n.title}</div>
                  <p className="text-[11px] text-on-surface-variant line-clamp-2 mt-0.5">{n.content}</p>
                </div>
              ))
            )}
          </div>
        </DialogContent>
      </Dialog>

      {/* Calendar Picker */}
      <Dialog open={isCalendarPickerOpen} onOpenChange={setIsCalendarPickerOpen}>
        <DialogContent className="max-w-md max-h-[60vh] flex flex-col p-4">
          <div className="font-headline text-body-sm font-bold text-primary mb-2">
            Termin auswählen
          </div>
          <div className="flex-1 overflow-y-auto space-y-2">
            {userEvents.length === 0 ? (
              <p className="text-xs text-on-surface-variant/70 py-4 text-center">Keine Termine.</p>
            ) : (
              userEvents.map((ev) => (
                <div
                  key={ev.event_id || ev.id}
                  onClick={() => {
                    setIsCalendarPickerOpen(false)
                    handleSend('', undefined, {
                      title: ev.title,
                      start: ev.start,
                      end: ev.end,
                      description: ev.description,
                      location: ev.location,
                    })
                  }}
                  className="p-2.5 rounded-lg border border-outline-variant/30 hover:border-primary/50 cursor-pointer text-xs"
                >
                  <div className="font-semibold text-primary">{ev.title}</div>
                  <div className="text-[10px] text-on-surface-variant mt-0.5">
                    {new Date(ev.start).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' })}
                  </div>
                </div>
              ))
            )}
          </div>
        </DialogContent>
      </Dialog>
    </Dialog>
  )
}
