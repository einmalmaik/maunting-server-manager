/**
 * Was sich mit einer einzelnen Nachricht anstellen lässt.
 *
 * Öffnet sich durch langes Drücken auf die Blase — am Telefon der einzige Weg
 * dorthin, weil es dort kein Hover gibt. Am Rechner führt zusätzlich die kleine
 * Leiste unter der eigenen Blase zu Bearbeiten und Löschen.
 *
 * Oben die sechs Schnellreaktionen, weil das die häufigste Absicht ist und der
 * Daumen sie am oberen Rand eines Blattes von unten bequem erreicht. Darunter
 * die Aktionen in der Reihenfolge, in der sie gebraucht werden.
 */

import { useTranslation } from 'react-i18next'
import {
  Copy,
  Forward,
  ListChecks,
  Pencil,
  Pin,
  Reply,
  Star,
  StarOff,
  Trash2,
} from 'lucide-react'

import { Blattmenue, Blatteintrag } from '@/Singra/UI'
import { SCHNELLREAKTIONEN } from '@/services/reaktionen'
import type { ChatMessage } from './ChatMessageBubble'

export interface NachrichtenMenueProps {
  msg: ChatMessage | null
  onSchliessen: () => void
  onReaktion: (msg: ChatMessage, emoji: string) => void
  onAntworten: (msg: ChatMessage) => void
  onWeiterleiten: (msg: ChatMessage) => void
  onKopieren: (msg: ChatMessage) => void
  onMarkieren: (msg: ChatMessage) => void
  onAuswaehlen: (msg: ChatMessage) => void
  onBearbeiten: (msg: ChatMessage) => void
  onLoeschen: (msg: ChatMessage) => void
  onAnheften?: (msg: ChatMessage) => void
  /** Ob in dieser Gruppe angeheftet werden darf — vom Server entschieden. */
  darfAnheften?: boolean
  /** Ob diese Nachricht gerade oben angeheftet ist. */
  istAngeheftet?: boolean
  /**
   * Ob **fremde** Nachrichten in dieser Gruppe entfernt werden dürfen.
   *
   * Bis 09/2026 gab es diesen Eintrag nur für eigene Nachrichten. Das Recht
   * `delete_messages` stand im Rechte-Dialog, ließ sich setzen — und hatte
   * nirgends einen Konsumenten: wer es hatte, konnte trotzdem nichts entfernen.
   */
  darfFremdeLoeschen?: boolean
}

export function NachrichtenMenue({
  msg,
  onSchliessen,
  onReaktion,
  onAntworten,
  onWeiterleiten,
  onKopieren,
  onMarkieren,
  onAuswaehlen,
  onBearbeiten,
  onLoeschen,
  onAnheften,
  darfAnheften,
  istAngeheftet,
  darfFremdeLoeschen,
}: NachrichtenMenueProps) {
  const { t } = useTranslation()
  if (!msg) return null

  const schliesseUnd = (tun: (m: ChatMessage) => void) => () => {
    onSchliessen()
    tun(msg)
  }

  return (
    <Blattmenue offen onSchliessen={onSchliessen} titel={t('messenger.messageActions')}>
      <div className="px-3 pt-2 pb-3 flex items-center justify-between gap-1 border-b border-outline-variant/20">
        {SCHNELLREAKTIONEN.map((emoji) => (
          <button
            key={emoji}
            type="button"
            onClick={schliesseUnd((m) => onReaktion(m, emoji))}
            // 44 px, damit der Daumen sicher trifft; das Zeichen selbst darf
            // kleiner aussehen als seine Trefferfläche.
            className={`w-11 h-11 rounded-full text-xl flex items-center justify-center transition-transform active:scale-90 ${
              msg.reaktionen?.[emoji]?.length ? 'bg-primary/20' : 'hover:bg-surface-container-high'
            }`}
            aria-label={t('messenger.reactWith', { emoji })}
          >
            <span aria-hidden="true">{emoji}</span>
          </button>
        ))}
      </div>

      <div className="py-1">
        <Blatteintrag icon={<Reply className="w-4 h-4" />} label={t('messenger.reply')} onClick={schliesseUnd(onAntworten)} />
        <Blatteintrag
          icon={<Forward className="w-4 h-4" />}
          label={t('messenger.forward')}
          onClick={schliesseUnd(onWeiterleiten)}
        />
        {msg.text && (
          <Blatteintrag icon={<Copy className="w-4 h-4" />} label={t('messenger.copyText')} onClick={schliesseUnd(onKopieren)} />
        )}
        <Blatteintrag
          icon={msg.istMarkiert ? <StarOff className="w-4 h-4" /> : <Star className="w-4 h-4" />}
          label={msg.istMarkiert ? t('messenger.unmark') : t('messenger.mark')}
          hinweis={t('messenger.markHint')}
          onClick={schliesseUnd(onMarkieren)}
        />
        {onAnheften && (
          <Blatteintrag
            icon={<Pin className="w-4 h-4" />}
            label={istAngeheftet ? t('messenger.unpin') : t('messenger.pinInGroup')}
            hinweis={darfAnheften ? undefined : t('messenger.pinNoRight')}
            disabled={!darfAnheften}
            onClick={schliesseUnd(onAnheften)}
          />
        )}
        <Blatteintrag
          icon={<ListChecks className="w-4 h-4" />}
          label={t('messenger.selectSeveral')}
          onClick={schliesseUnd(onAuswaehlen)}
        />
        {msg.isSelf && msg.text && (
          <Blatteintrag icon={<Pencil className="w-4 h-4" />} label={t('common.edit')} onClick={schliesseUnd(onBearbeiten)} />
        )}
        {(msg.isSelf || darfFremdeLoeschen) && (
          <Blatteintrag
            icon={<Trash2 className="w-4 h-4" />}
            label={
              msg.isSelf ? t('messenger.deleteForAllShort') : t('messenger.deleteForeignShort')
            }
            hinweis={
              msg.isSelf ? t('messenger.deleteForAllHint') : t('messenger.deleteForeignHint')
            }
            gefahr
            onClick={schliesseUnd(onLoeschen)}
          />
        )}
      </div>
    </Blattmenue>
  )
}
