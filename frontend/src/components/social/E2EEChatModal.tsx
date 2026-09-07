import React, { useState, useEffect, useRef } from 'react'
import {
  Button,
  Input,
  Badge,
  Dialog,
  DialogContent,
} from '@/Singra/UI'
import { ShieldCheck, Send, RefreshCw, Lock } from 'lucide-react'
import { DeviceBadge } from './DeviceBadge'
import { StatusDot } from './StatusIndicator'
import {
  type FriendItem,
  relayE2eeEnvelope,
  fetchE2eeEnvelopes,
  getE2eePublicKey,
  setE2eePublicKey,
} from '@/api/social'
import {
  deriveBlindMailboxId,
  encryptE2eeMessage,
  decryptE2eeMessage,
  encryptE2eeHybrid,
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

interface DecryptedMessage {
  id: number
  text: string
  createdAt: string
  isSelf: boolean
}

export function E2EEChatModal({ open, onOpenChange, currentUserId, friend }: E2EEChatModalProps) {
  const [messages, setMessages] = useState<DecryptedMessage[]>([])
  const [inputText, setInputText] = useState('')
  const [loading, setLoading] = useState(false)
  const [sending, setSending] = useState(false)
  const [blindMailboxId, setBlindMailboxId] = useState<string>('')
  const [localKeyPair, setLocalKeyPair] = useState<LocalE2eeKeyPair | null>(null)
  const [recipientPublicKeyJwk, setRecipientPublicKeyJwk] = useState<string | null>(null)

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
        await setE2eePublicKey(kp.publicKeyJwk)
      } catch {
        // Non-blocking key registration
      }
    }).catch(() => {})

    return () => {
      active = false
    }
  }, [open, currentUserId])

  // Fetch target user's public key for hybrid asymmetric encryption
  useEffect(() => {
    if (!open || !targetUserId) return
    let active = true

    getE2eePublicKey(targetUserId).then((res) => {
      if (active && res?.public_key) {
        setRecipientPublicKeyJwk(res.public_key)
      }
    }).catch(() => {})

    return () => {
      active = false
    }
  }, [open, targetUserId])

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

          try {
            const parsed = JSON.parse(plain)
            if (typeof parsed === 'object' && parsed !== null && 'text' in parsed) {
              text = parsed.text
              isSelf = parsed.sender_id === currentUserId
            }
          } catch {
            // Backward-compatibility: legacy tagged messages
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
          })
        } catch {
          // If decryption fails, maintain zero-knowledge and render protected placeholder
          decryptedList.push({
            id: env.id,
            text: '🔒 [Verschlüsselte Nachricht]',
            createdAt: env.created_at,
            isSelf: false,
          })
        }
      }

      setMessages(decryptedList)
    } catch {
      // Offline / network error
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (open && blindMailboxId) {
      loadMessages()
      const interval = setInterval(loadMessages, 5000)
      return () => clearInterval(interval)
    }
  }, [open, blindMailboxId, localKeyPair])

  // Autoscroll: only scroll down if just sent or already scrolled near bottom (<100px)
  useEffect(() => {
    const container = scrollContainerRef.current
    if (!container) return

    const isNearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 100
    if (justSentRef.current || isNearBottom) {
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
      justSentRef.current = false
    }
  }, [messages])

  const handleSend = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    if (!inputText.trim() || !friend || !blindMailboxId || !currentUserId || !targetUserId || sending) return

    const rawMessage = inputText.trim()
    setSending(true)
    try {
      const payload = JSON.stringify({
        sender_id: currentUserId,
        text: rawMessage,
        timestamp: new Date().toISOString(),
      })

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
      justSentRef.current = true
      await loadMessages()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Fehler beim Senden der Nachricht'
      toast.error(msg)
    } finally {
      setSending(false)
    }
  }

  if (!friend) return null

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl h-[600px] p-0 flex flex-col" showCloseButton>
        {/* Header */}
        <div className="p-4 border-b border-outline-variant/30 bg-surface-container flex items-center justify-between pr-12">
          <div className="flex items-center gap-3">
            <div className="relative">
              <div className="w-10 h-10 rounded-full bg-primary/15 flex items-center justify-center font-bold text-primary">
                {friend.username.slice(0, 2).toUpperCase()}
              </div>
              <StatusDot
                status={friend.presence?.status || 'invisible'}
                size="sm"
                className="absolute bottom-0 right-0"
              />
            </div>
            <div>
              <div className="font-headline text-body-md font-bold text-primary flex items-center gap-2">
                <span>{friend.username}</span>
                <DeviceBadge deviceType={friend.presence?.device_type} />
              </div>
              <div className="font-body text-xs text-on-surface-variant flex items-center gap-1.5 mt-0.5">
                <Lock className="w-3 h-3 text-emerald-400" />
                <span>Zero-Knowledge E2EE (@msdis/shield)</span>
              </div>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <Badge variant="success" className="text-[10px] uppercase tracking-wider gap-1 hidden sm:flex">
              <ShieldCheck className="w-3 h-3" />
              DIS Verifiziert
            </Badge>
            <Button
              variant="ghost"
              size="icon"
              onClick={() => void loadMessages()}
              disabled={loading}
              aria-label="Nachrichten aktualisieren"
              className="p-1.5 h-8 w-8"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            </Button>
          </div>
        </div>

        {/* Message Thread */}
        <div
          ref={scrollContainerRef}
          className="flex-1 overflow-y-auto p-4 space-y-3 bg-surface-container-lowest/50"
        >
          <div className="p-2.5 rounded-lg bg-surface-container-high/40 border border-outline-variant/20 text-center">
            <p className="text-[11px] text-on-surface-variant/90 leading-relaxed">
              🛡️ <strong>Ende-zu-Ende verschlüsselt:</strong> Nachrichten werden auf Ihrem Gerät mit modernster
              DIS-Kryptographie versiegelt. Der MSM-Server fungiert als blinder Relais und hat keinen Zugriff auf Klartexte oder private Schlüssel.
            </p>
          </div>

          {messages.length === 0 && !loading && (
            <div className="py-16 text-center text-on-surface-variant/70 text-xs">
              Keine Nachrichten im Tresor. Schreiben Sie die erste verschlüsselte Nachricht!
            </div>
          )}

          {messages.map((msg) => (
            <div
              key={msg.id}
              className={`flex flex-col ${msg.isSelf ? 'items-end' : 'items-start'}`}
            >
              <div
                className={`max-w-[75%] px-3.5 py-2.5 rounded-2xl text-xs break-words shadow-sm ${
                  msg.isSelf
                    ? 'bg-primary text-on-primary rounded-br-xs'
                    : 'bg-surface-container-high text-on-surface rounded-bl-xs border border-outline-variant/20'
                }`}
              >
                {msg.text}
              </div>
              <span className="text-[10px] text-on-surface-variant/50 mt-1 px-1">
                {new Date(msg.createdAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
              </span>
            </div>
          ))}
          <div ref={messagesEndRef} />
        </div>

        {/* Footer Input */}
        <form onSubmit={handleSend} className="p-3 border-t border-outline-variant/30 bg-surface-container-low flex items-center gap-2">
          <Input
            value={inputText}
            onChange={(e: React.ChangeEvent<HTMLInputElement>) => setInputText(e.target.value)}
            placeholder="Verschlüsselte Nachricht senden …"
            className="flex-1 text-xs"
            disabled={sending}
          />
          <Button
            type="submit"
            disabled={!inputText.trim() || sending}
            size="sm"
            className="gap-1.5 px-3.5"
          >
            <Send className="w-3.5 h-3.5" />
            <span>Senden</span>
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  )
}
