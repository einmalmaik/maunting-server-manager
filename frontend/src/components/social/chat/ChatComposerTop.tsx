import { useTranslation } from 'react-i18next'
import { Bell, X } from 'lucide-react'
import { Avatar } from '@/Singra/UI'
import type { AntwortBezug } from '@/components/social/ChatMessageBubble'
import type { Erwaehnungsvorschlag } from '@/services/erwaehnungen'

/**
 * Die Vorschlagsliste beim Tippen von `@`. Liegt unmittelbar über dem Feld
 * und damit über der Tastatur; jede Zeile ist 44 px hoch.
 */
export function MentionSuggestions({
  vorschlaege,
  onWaehlen,
}: {
  vorschlaege: Erwaehnungsvorschlag[]
  onWaehlen: (vorschlag: Erwaehnungsvorschlag) => void
}) {
  if (vorschlaege.length === 0) return null
  return (
    <div className="border-b border-outline-variant/20 max-h-56 overflow-y-auto">
      {vorschlaege.map((v) => (
        <button
          key={v.userId ?? 'alle'}
          type="button"
          // Das Feld behält den Fokus, sonst klappte die Tastatur zu.
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => onWaehlen(v)}
          className="w-full min-h-11 px-3 py-1.5 flex items-center gap-2.5 text-left hover:bg-surface-container-high transition-colors"
        >
          {v.istAlle ? (
            <span className="w-7 h-7 rounded-full bg-primary/20 flex items-center justify-center shrink-0">
              <Bell className="w-3.5 h-3.5 text-primary" />
            </span>
          ) : (
            <Avatar src={v.avatarUrl ?? null} name={v.name} size="sm" className="w-7 h-7 shrink-0" />
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
  )
}

/** Der Zitatkopf beim Antworten. */
export function ChatReplyBar({ antwort, onVerwerfen }: { antwort: AntwortBezug; onVerwerfen: () => void }) {
  const { t } = useTranslation()
  return (
    <div className="px-2.5 py-2 border-b border-outline-variant/20 flex items-center gap-2">
      <span className="w-0.5 self-stretch rounded-full bg-primary shrink-0" />
      <span className="min-w-0 flex-1">
        <span className="block text-label-sm font-semibold text-primary truncate">
          Antwort an {antwort.absenderName || 'Nachricht'}
        </span>
        <span className="block text-label-sm text-on-surface-variant line-clamp-1">{antwort.auszug}</span>
      </span>
      <button
        type="button"
        onClick={onVerwerfen}
        className="w-11 h-11 -mr-1 rounded-full flex items-center justify-center text-on-surface-variant hover:bg-surface-container-high transition-colors shrink-0"
        aria-label={t('messenger.discardReply')}
      >
        <X className="w-4 h-4" />
      </button>
    </div>
  )
}
