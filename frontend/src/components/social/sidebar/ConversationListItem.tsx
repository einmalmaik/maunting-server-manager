import React from 'react'
import { useTranslation } from 'react-i18next'
import { Archive, AtSign, BellOff, Globe, Pin, UsersRound } from 'lucide-react'
import { Avatar } from '@/Singra/UI'
import type { ChatGroupItem } from '@/api/social'
import { ChatZeilenGeste } from '@/components/social/ChatZeilenGeste'
import { DeviceBadge } from '@/components/social/DeviceBadge'
import { StatusDot, type PresenceStatus } from '@/components/social/StatusIndicator'
import { useMessengerNotificationStore } from '@/stores/messengerNotificationStore'

/** Ein Kontakt, wie ihn die Chatliste zeigt. */
export interface ChatContact {
  /**
   * Schlüssel für die Kontaktlisten. Wird beim Zusammenführen vergeben und
   * stammt nicht aus der Server-Antwort: eine Benutzer-Id kann doppelt
   * ankommen, dieser Wert nicht.
   */
  listKey: string
  id: number
  userId: number
  username: string
  avatarUrl?: string | null
  status: PresenceStatus
  deviceType?: string | null
  activityLabel?: string | null
  isFriend: boolean
  teamName?: string | null
  isPublicUser?: boolean
}

/** Was Gruppen- und Kontaktzeile gemeinsam haben. */
interface ZeileProps {
  /** Das Postfach des Chats. Fehlt es noch, gibt es weder Abzeichen noch Gesten. */
  mid: string | undefined
  ausgewaehlt: boolean
  /** Ein angefangener Entwurf ersetzt die zweite Zeile. */
  entwurf: string
  onOeffnen: () => void
  onAnheften: (mid: string) => void
  onArchivieren: (mid: string) => void
  onMenue: (mid: string, name: string) => void
}

/** Anheften, Archiv, Stumm, Erwähnung und Ungelesen rechts an einer Zeile. */
function ZeilenAbzeichen({ mid }: { mid: string | undefined }) {
  const { t } = useTranslation()
  const unreadCounts = useMessengerNotificationStore((s) => s.unreadCounts)
  const pinnedChats = useMessengerNotificationStore((s) => s.pinnedChats)
  const archivedChats = useMessengerNotificationStore((s) => s.archivedChats)
  const mentionedChats = useMessengerNotificationStore((s) => s.mentionedChats)
  const isMuted = useMessengerNotificationStore((s) => s.isMuted)
  if (!mid) return null
  const unread = unreadCounts[mid] || 0
  return (
    <div className="flex items-center gap-1.5 shrink-0 ml-2">
      {pinnedChats.includes(mid) && <Pin className="w-3.5 h-3.5 text-primary/70" />}
      {archivedChats.includes(mid) && <Archive className="w-3.5 h-3.5 text-on-surface-variant/50" />}
      {isMuted(mid) && <BellOff className="w-3.5 h-3.5 text-on-surface-variant/50" />}
      {mentionedChats.includes(mid) && (
        <span
          title={t('messenger.youWereMentioned')}
          className="inline-flex items-center justify-center w-[18px] h-[18px] rounded-full bg-primary/20 text-primary"
        >
          <AtSign className="w-3 h-3" />
        </span>
      )}
      {unread > 0 && (
        <span className="inline-flex items-center justify-center px-1.5 py-0.5 text-label-sm font-bold rounded-full bg-primary text-on-primary min-w-[18px]">
          {unread > 99 ? '99+' : unread}
        </span>
      )}
    </div>
  )
}

function ZeilenVorschau({ entwurf, children }: { entwurf: string; children: React.ReactNode }) {
  const { t } = useTranslation()
  if (!entwurf) return <>{children}</>
  return (
    <p className="text-label-sm truncate">
      <span className="text-status-warning font-semibold">{t('messenger.draftPrefix')} </span>
      <span className="text-on-surface-variant/80">{entwurf}</span>
    </p>
  )
}

/** Die Hülle jeder Zeile: Wischgeste, Langdruckmenü und der Knopf selbst. */
function Zeile({
  mid,
  name,
  ausgewaehlt,
  onOeffnen,
  onAnheften,
  onArchivieren,
  onMenue,
  children,
}: Omit<ZeileProps, 'entwurf'> & { name: string; children: React.ReactNode }) {
  const pinnedChats = useMessengerNotificationStore((s) => s.pinnedChats)
  const archivedChats = useMessengerNotificationStore((s) => s.archivedChats)
  const imArchiv = mid ? archivedChats.includes(mid) : false
  return (
    <ChatZeilenGeste
      angeheftet={mid ? pinnedChats.includes(mid) : false}
      archiviert={imArchiv}
      onAnheften={() => mid && onAnheften(mid)}
      onArchivieren={() => mid && onArchivieren(mid)}
      onMenue={() => mid && onMenue(mid, name)}
    >
      <button
        type="button"
        onClick={onOeffnen}
        className={`w-full flex items-center justify-between p-2.5 rounded-xl text-left transition-all ${
          ausgewaehlt
            ? 'bg-primary/15 border border-primary/30 shadow-sm'
            : 'hover:bg-surface-container-high/60 border border-transparent'
        } ${imArchiv ? 'opacity-60' : ''}`}
      >
        <div className="flex items-center gap-3 min-w-0 flex-1">{children}</div>
        <ZeilenAbzeichen mid={mid} />
      </button>
    </ChatZeilenGeste>
  )
}

/** Eine Gruppe in der Chatliste. */
export function GroupListItem({ gruppe, titel, entwurf, ...zeile }: ZeileProps & { gruppe: ChatGroupItem; titel: string }) {
  const { t } = useTranslation()
  return (
    <Zeile name={titel} {...zeile}>
      <div className="w-9 h-9 rounded-full bg-primary/10 text-primary flex items-center justify-center font-bold text-xs shrink-0">
        {gruppe.avatar_url ? (
          <img src={gruppe.avatar_url} alt="" className="w-full h-full rounded-full object-cover" />
        ) : (
          <UsersRound className="w-4 h-4" />
        )}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-1">
          <span className="text-xs font-semibold text-primary truncate">{titel}</span>
          <span className="text-label-sm text-on-surface-variant/60 shrink-0">{gruppe.member_count} M.</span>
        </div>
        <ZeilenVorschau entwurf={entwurf}>
          <p className="text-label-sm text-on-surface-variant/80 truncate">
            {gruppe.description || t('messenger.encryptedGroup')}
          </p>
        </ZeilenVorschau>
      </div>
    </Zeile>
  )
}

/** Ein Kontakt in der Chatliste. */
export function ContactListItem({ kontakt: c, entwurf, ...zeile }: ZeileProps & { kontakt: ChatContact }) {
  const { t } = useTranslation()
  const isBlocked = useMessengerNotificationStore((s) => s.isBlocked)
  const blockiert = isBlocked(c.userId)
  return (
    <Zeile name={c.username} {...zeile}>
      <div className="relative shrink-0">
        <Avatar src={c.avatarUrl} name={c.username} size="sm" />
        <StatusDot status={c.status} size="sm" className="absolute bottom-0 right-0" />
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <span className="text-xs font-semibold text-primary truncate">{c.username}</span>
          {blockiert && (
            <span className="text-label-sm px-1 rounded bg-status-destructive/15 text-status-destructive font-medium">
              Blockiert
            </span>
          )}
          {c.isPublicUser && !c.isFriend && !c.teamName && !blockiert && (
            <span className="text-label-sm px-1.5 py-0.2 rounded-md bg-primary/10 text-primary font-medium flex items-center gap-0.5">
              <Globe className="w-2.5 h-2.5" />
              <span>{t('messenger.public')}</span>
            </span>
          )}
          <DeviceBadge deviceType={c.deviceType} />
        </div>
        <ZeilenVorschau entwurf={entwurf}>
          {c.teamName && (
            <p className="text-label-sm text-tertiary truncate flex items-center gap-1">
              <UsersRound className="w-2.5 h-2.5" />
              <span>{c.teamName}</span>
            </p>
          )}
          {c.isPublicUser && !c.isFriend && !c.teamName && (
            <p className="text-label-sm text-on-surface-variant/70 truncate">{t('messenger.e2eeReady')}</p>
          )}
          {c.activityLabel && !c.teamName && (
            <p className="text-label-sm text-on-surface-variant/80 truncate">{c.activityLabel}</p>
          )}
        </ZeilenVorschau>
      </div>
    </Zeile>
  )
}
