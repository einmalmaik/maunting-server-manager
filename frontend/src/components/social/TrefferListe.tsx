/**
 * Treffer über alle Chats hinweg — dieselbe Liste für drei Fragen.
 *
 * Die Suche, die markierten Nachrichten und „@ und Antworten an mich" liefern
 * dasselbe: nach Chat gruppierte Zeilen mit einem Ausschnitt. Eine Ansicht
 * dafür statt dreier, die nach dem zweiten Monat verschieden aussehen.
 *
 * Am Telefon ist das eine eigene Ansicht über dem Chat, kein Aufklapper: eine
 * Liste, die sich über mehrere Gespräche zieht, braucht die volle Höhe. *
 * **Per Portal an `document.body`.** Bis dahin hing die Ansicht im Chat-Ast
 * von `Messenger.tsx` und war `absolute inset-0` im Chat-Container. Ohne
 * offenen Chat rendert dieser Ast gar nicht: der Aufruf aus der Chatliste
 * wirkte folgenlos, und wer danach einen Chat öffnete, fand ihn sofort
 * überdeckt. Dazu kappt `Shell.tsx` jedes `z-50` im Inhaltsbereich auf 10.
 * Beides löst dieselbe Maßnahme.
 */

import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import { ArrowLeft, Lock, MessageSquare, Users } from 'lucide-react'

import { Avatar } from '@/Singra/UI'
import type { ChatTreffer, Treffer } from '@/services/verlaufSuche'
import type { MailboxMeta } from '@/stores/messengerNotificationStore'

export interface TrefferListeProps {
  offen: boolean
  titel: string
  /** Was steht, wenn nichts gefunden wurde. */
  leerText: string
  chats: readonly ChatTreffer[]
  verzeichnis: Record<string, MailboxMeta>
  gesperrt?: boolean
  laeuft?: boolean
  onSchliessen: () => void
  onTreffer: (t: Treffer) => void
}

function Ausschnitt({ t }: { t: Treffer }) {
  // Ohne Fundstelle (Markierte, Erwähnungen) bleibt es beim reinen Text.
  if (t.bis <= t.von) return <span className="line-clamp-2">{t.auszug}</span>
  return (
    <span className="line-clamp-2">
      {t.auszug.slice(0, t.von)}
      <mark className="bg-primary/30 text-on-surface rounded px-0.5">{t.auszug.slice(t.von, t.bis)}</mark>
      {t.auszug.slice(t.bis)}
    </span>
  )
}

export function TrefferListe({
  offen,
  titel,
  leerText,
  chats,
  verzeichnis,
  gesperrt,
  laeuft,
  onSchliessen,
  onTreffer,
}: TrefferListeProps) {
  const { t } = useTranslation()
  if (!offen || typeof document === 'undefined') return null

  const gesamt = chats.reduce((s, c) => s + c.treffer.length, 0)

  return createPortal(
    <div className="fixed inset-0 z-[70] flex flex-col bg-surface">
      <div className="shrink-0 px-3 py-2.5 border-b border-outline-variant/20 flex items-center gap-2">
        <button
          type="button"
          onClick={onSchliessen}
          className="w-11 h-11 -ml-1 rounded-full flex items-center justify-center text-on-surface-variant hover:bg-surface-container-high transition-colors"
          aria-label={t('common.back')}
        >
          <ArrowLeft className="w-5 h-5" />
        </button>
        <div className="min-w-0">
          <div className="text-sm font-semibold text-on-surface truncate">{titel}</div>
          {!gesperrt && !laeuft && (
            <div className="text-label-sm text-on-surface-variant tabular-nums">
              {gesamt === 0
                ? t('messenger.searchNothingFound')
                : t('messenger.searchHitsInChats', { hits: gesamt, count: chats.length })}
            </div>
          )}
        </div>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto pb-[env(safe-area-inset-bottom)]">
        {gesperrt ? (
          <div className="py-16 px-6 text-center space-y-2">
            <Lock className="w-6 h-6 mx-auto text-on-surface-variant/60" />
            <p className="text-xs text-on-surface-variant">
              {t('messenger.searchLockedLong')}
            </p>
          </div>
        ) : laeuft ? (
          <p className="py-16 text-center text-xs text-on-surface-variant/70">{t('messenger.searching')}</p>
        ) : chats.length === 0 ? (
          <p className="py-16 px-6 text-center text-xs text-on-surface-variant/70">{leerText}</p>
        ) : (
          chats.map((chat) => {
            const meta = verzeichnis[chat.blindMailboxId]
            return (
              <div key={chat.blindMailboxId} className="py-1">
                <div className="px-3 py-1.5 flex items-center gap-2 text-label-sm font-semibold text-on-surface-variant uppercase tracking-wide">
                  {meta?.isGroup ? (
                    <Users className="w-3.5 h-3.5 text-tertiary shrink-0" />
                  ) : (
                    <MessageSquare className="w-3.5 h-3.5 text-primary shrink-0" />
                  )}
                  <span className="truncate">{meta?.name || t('messenger.unknownChat')}</span>
                </div>
                {chat.treffer.map((t) => (
                  <button
                    key={`${t.blindMailboxId}-${t.id}-${t.von}`}
                    type="button"
                    onClick={() => onTreffer(t)}
                    className="w-full min-h-14 px-3 py-2 flex items-start gap-3 text-left hover:bg-surface-container-high transition-colors"
                  >
                    <Avatar
                      src={t.isSelf ? null : meta?.avatarUrl ?? null}
                      name={t.isSelf ? 'Ich' : t.senderName || meta?.name || 'Kontakt'}
                      size="sm"
                      className="w-8 h-8 shrink-0 mt-0.5"
                    />
                    <span className="min-w-0 flex-1 space-y-0.5">
                      <span className="flex items-baseline justify-between gap-2">
                        <span className="text-label-sm font-semibold text-on-surface truncate">
                          {t.isSelf ? 'Ich' : t.senderName || meta?.name || 'Kontakt'}
                        </span>
                        <span className="text-label-sm text-on-surface-variant/70 shrink-0 tabular-nums">
                          {new Date(t.createdAt).toLocaleDateString([], { day: '2-digit', month: '2-digit' })}
                        </span>
                      </span>
                      <span className="block text-xs text-on-surface-variant leading-snug">
                        <Ausschnitt t={t} />
                      </span>
                    </span>
                  </button>
                ))}
              </div>
            )
          })
        )}
      </div>
    </div>,
    document.body,
  )
}
