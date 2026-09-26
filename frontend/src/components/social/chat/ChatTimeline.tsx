import React from 'react'
import { useTranslation } from 'react-i18next'
import { Lock, Mic, Shield, Sparkles } from 'lucide-react'
import type { ChatMessage } from '@/components/social/ChatMessageBubble'

export interface PartnerAktivitaet {
  status: 'typing' | 'recording'
  username?: string
}

interface ChatTimelineProps {
  scrollRef: React.RefObject<HTMLDivElement>
  endeRef: React.RefObject<HTMLDivElement>
  nachrichten: ChatMessage[]
  laedt: boolean
  /** Vor dieser Nachricht steht „Neue Nachrichten". */
  trennerId: number | null
  aktivitaet: PartnerAktivitaet | null
  istGruppe: boolean
  /** Die Sprechblase selbst; ihre vielen Angaben kennt nur die Seite. */
  zeichneNachricht: (msg: ChatMessage) => React.ReactNode
}

/** „Heute", „Gestern" oder das Datum in der Sprache der Oberfläche. */
export function formatChatDateBadge(
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

/** Der Nachrichtenverlauf des offenen Chats. */
export function ChatTimeline({
  scrollRef,
  endeRef,
  nachrichten,
  laedt,
  trennerId,
  aktivitaet,
  istGruppe,
  zeichneNachricht,
}: ChatTimelineProps) {
  const { t, i18n } = useTranslation()

  return (
    <div
      ref={scrollRef}
      // Das obere Polster muss die schwebende Kopfzeile freihalten. Am
      // Telefon ist sie 44 px hoch (Trefffläche), am Zeigergerät 32 px; mit
      // einem festen pt-12 verdeckte sie dort die ersten Zeilen des Verlaufs.
      className="flex-1 overflow-y-auto p-4 pt-16 sm:pt-12 space-y-3 relative z-1"
    >
      <div className="py-1 text-center">
        <div className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-surface-container-high/60 border border-outline-variant/30 text-label-sm text-on-surface-variant shadow-2xs">
          <Lock className="w-3 h-3 text-status-success" />
          <span>{t('messenger.e2eeBanner')}</span>
        </div>
      </div>

      {nachrichten.length === 0 && !laedt && (
        <div className="py-16 text-center text-xs text-on-surface-variant/70">
          {t('messenger.noMessagesYet')}
        </div>
      )}

      {nachrichten.map((msg, idx) => {
        // Eine Systemzeile ist keine Nachricht: sie hat keinen Absender, keine
        // Quittung und kein Kontextmenü. In der Sprechblase gerendert sah sie
        // aus, als hätte das Gegenüber sie geschrieben — bei einer Meldung über
        // die Sicherheit dieses Gesprächs die denkbar schlechteste Verwechslung.
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

        // Eine Wiederherstellung des Funkens reist als Nachricht, damit sie
        // alle Geräte beider Seiten erreicht. Im Verlauf ist sie eine Meldung
        // über den Funken, keine Sprechblase.
        if (msg.funkenRettung) {
          const count = msg.funkenRettung.verloren
          return (
            <div key={msg.id} className="py-1 text-center" data-funken-rettung="">
              <div className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-status-warning/10 border border-status-warning/30 text-label-sm text-on-surface-variant shadow-2xs">
                <Sparkles className="w-3 h-3 text-status-warning shrink-0" />
                <span>
                  {msg.isSelf
                    ? t('messenger.streak.restoredSelf', { count })
                    : t('messenger.streak.restoredOther', { count, name: msg.senderName || '' })}
                </span>
              </div>
            </div>
          )
        }

        const datum = formatChatDateBadge(msg.createdAt, t, i18n.language)
        const datumDavor = idx > 0 ? formatChatDateBadge(nachrichten[idx - 1].createdAt, t, i18n.language) : null

        return (
          <React.Fragment key={msg.id}>
            {datum && datum !== datumDavor && (
              <div className="flex justify-center my-3 pointer-events-none">
                <span className="px-3.5 py-1 rounded-full text-label-sm font-semibold bg-surface-container/90 text-on-surface-variant backdrop-blur-md border border-outline-variant/30 shadow-sm">
                  {datum}
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

            {zeichneNachricht(msg)}
          </React.Fragment>
        )
      })}

      {aktivitaet && (
        <div className="flex items-center gap-2 text-xs py-1.5 px-3 rounded-full bg-surface-container-high/90 border border-outline-variant/30 text-on-surface w-fit shadow-sm animate-slide-up">
          {aktivitaet.status === 'recording' ? (
            <>
              <Mic className="w-3.5 h-3.5 text-status-destructive animate-pulse" />
              <span className="text-label-sm text-status-destructive font-medium">
                {istGruppe
                  ? t('messenger.someoneRecording', { name: aktivitaet.username || t('messenger.someone') })
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
                {istGruppe ? `${aktivitaet.username || 'Jemand'} schreibt …` : t('messenger.typing')}
              </span>
            </>
          )}
        </div>
      )}
      <div ref={endeRef} />
    </div>
  )
}
