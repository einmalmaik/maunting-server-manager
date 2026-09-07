import React, { useState, useEffect, useRef } from 'react'
import { Button, Input, Badge } from '@/Singra/UI'
import { ShieldCheck, Send, RefreshCw, Lock, X } from 'lucide-react'
import { DeviceBadge } from './DeviceBadge'
import { StatusDot } from './StatusIndicator'
import { type FriendItem, relayE2eeEnvelope, fetchE2eeEnvelopes } from '@/api/social'
import { deriveBlindMailboxId, encryptE2eeMessage, decryptE2eeMessage } from '@/services/e2eeCrypto'

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
  const messagesEndRef = useRef<HTMLDivElement>(null)

  const targetUserId = friend?.user_id ?? friend?.id ?? 0

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
          const plain = await decryptE2eeMessage(env.ciphertext_envelope, currentUserId, targetUserId)
          const isSelf = plain.startsWith('[ME]:')
          const display = isSelf ? plain.replace('[ME]:', '') : plain
          decryptedList.push({
            id: env.id,
            text: display,
            createdAt: env.created_at,
            isSelf,
          })
        } catch {
          // If decryption fails, keep zero-knowledge and don't crash
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
  }, [open, blindMailboxId])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const handleSend = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    if (!inputText.trim() || !friend || !blindMailboxId || !currentUserId || !targetUserId || sending) return

    const rawMessage = inputText.trim()
    setSending(true)
    try {
      const tagged = `[ME]:${rawMessage}`
      const ciphertext = await encryptE2eeMessage(tagged, currentUserId, targetUserId)

      await relayE2eeEnvelope({
        blind_mailbox_id: blindMailboxId,
        ciphertext_envelope: ciphertext,
      })

      setInputText('')
      await loadMessages()
    } catch {
      // Error handling
    } finally {
      setSending(false)
    }
  }

  if (!open || !friend) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm animate-[fadeIn_.15s_ease-out]">
      <div
        className="w-full max-w-xl h-[600px] bg-surface-container-low border border-outline-variant/30 rounded-2xl shadow-2xl overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="p-4 border-b border-outline-variant/30 bg-surface-container flex items-center justify-between">
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
              size="sm"
              onClick={() => void loadMessages()}
              disabled={loading}
              title="Nachrichten aktualisieren"
              className="p-1.5 h-8 w-8"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            </Button>
            <button
              type="button"
              onClick={() => onOpenChange(false)}
              className="p-1.5 text-on-surface-variant hover:text-on-surface rounded-xl hover:bg-surface-container-high transition-colors"
              title="Schließen"
            >
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>

        {/* Message Thread */}
        <div className="flex-1 overflow-y-auto p-4 space-y-3 bg-surface-container-lowest/50">
          <div className="p-2.5 rounded-lg bg-surface-container-high/40 border border-outline-variant/20 text-center">
            <p className="text-[11px] text-on-surface-variant/90 leading-relaxed">
              🛡️ <strong>Ende-zu-Ende verschlüsselt:</strong> Nachrichten werden auf Ihrem Gerät mit AES-256-GCM
              versiegelt. Der MSM-Server fungiert als blinder Relais und hat keinen Zugriff auf Klartexte oder Schlüssel.
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
      </div>
    </div>
  )
}
