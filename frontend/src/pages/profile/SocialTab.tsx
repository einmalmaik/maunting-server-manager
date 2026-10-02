import React, { useState, useEffect, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import {
  Avatar,
  Button,
  Input,
  Kurzinfo,
} from '@/Singra/UI'
import {
  Users,
  UserPlus,
  Check,
  X,
  UserMinus,
  Search,
  Lock,
  Ban,
  BellOff,
  Clock,
} from 'lucide-react'
import { DeviceBadge } from '@/components/social/DeviceBadge'
import { FunkenAbzeichen } from '@/components/social/FunkenBadge'
import { StatusDot } from '@/components/social/StatusIndicator'
import { Meilensteine } from '@/components/social/Meilensteine'
import { Nutzungszeit } from '@/components/social/Nutzungszeit'
import {
  type FriendItem,
  type AchievementsOverview,
  type UserStatsResponse,
  getFriends,
  getFriendRequests,
  sendFriendRequest,
  acceptFriendRequest,
  declineFriendRequest,
  removeFriend,
  getAchievements,
  getStats,
} from '@/api/social'
import { toast } from '@/stores/toastStore'
import { useMessengerNotificationStore } from '@/stores/messengerNotificationStore'
import { useFunkenStore } from '@/stores/funkenStore'
import { meldeErrungenschaft } from '@/lib/errungenschaft'

export function SocialTab() {
  const { t } = useTranslation()
  const navigate = useNavigate()

  // Friends state
  const [friends, setFriends] = useState<FriendItem[]>([])
  const [incomingRequests, setIncomingRequests] = useState<FriendItem[]>([])
  const [addUsername, setAddUsername] = useState('')
  const [searchFriend, setSearchFriend] = useState('')
  const [sendingRequest, setSendingRequest] = useState(false)
  const [contactSubTab, setContactSubTab] = useState<'friends' | 'blocked' | 'muted'>('friends')
  const [visibleFriendsCount, setVisibleFriendsCount] = useState(12)

  // Milestones state
  const [overview, setOverview] = useState<AchievementsOverview | null>(null)
  const [stats, setStats] = useState<UserStatsResponse | null>(null)

  // Notification Store for Mute & Block
  const blockedUserIds = useMessengerNotificationStore((s) => s.blockedUserIds)
  const blockedProfiles = useMessengerNotificationStore((s) => s.blockedProfiles)
  const unblockUser = useMessengerNotificationStore((s) => s.unblockUser)
  const mutedChats = useMessengerNotificationStore((s) => s.mutedChats)
  const unmuteChat = useMessengerNotificationStore((s) => s.unmuteChat)
  const mailboxDirectory = useMessengerNotificationStore((s) => s.mailboxDirectory)
  const syncBlockedFromBackend = useMessengerNotificationStore((s) => s.syncBlockedFromBackend)

  // Die Funken zeigen sich nur bei Freunden, ab dem Beginn der Freundschaft.
  useEffect(() => {
    useFunkenStore.getState().setzeFreunde(
      friends
        .filter((f) => f.status === 'accepted')
        .map((f) => ({ userId: Number(f.user_id ?? f.id), seit: f.created_at })),
    )
  }, [friends])

  const loadFriendsData = async () => {
    try {
      const [friendsData, reqsData] = await Promise.all([
        getFriends().catch(() => []),
        getFriendRequests().catch(() => ({ incoming: [], outgoing: [] })),
      ])
      setFriends(friendsData)
      setIncomingRequests(reqsData.incoming)
    } catch {
      // Non-blocking
    }
  }

  const loadMilestonesData = async () => {
    try {
      const [ovData, statsData] = await Promise.all([
        getAchievements().catch(() => null),
        getStats().catch(() => null),
      ])
      setOverview(ovData)
      setStats(statsData)
    } catch {
      // Non-blocking
    }
  }

  useEffect(() => {
    void loadFriendsData()
    void loadMilestonesData()
    void syncBlockedFromBackend()
  }, [])

  const handleSendFriendRequest = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    const target = addUsername.trim()
    if (!target) return

    setSendingRequest(true)
    try {
      const res = await sendFriendRequest(target)
      toast.success(res.message || t('social.contacts.requestSent'))
      setAddUsername('')
      await loadFriendsData()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : t('social.contacts.requestSendFailed')
      toast.error(msg)
    } finally {
      setSendingRequest(false)
    }
  }

  const handleAcceptRequest = async (reqId: number) => {
    try {
      await acceptFriendRequest(reqId)
      toast.success(t('social.contacts.requestAccepted'))
      await loadFriendsData()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : t('social.contacts.requestAcceptFailed')
      toast.error(msg)
    }
  }

  const handleDeclineRequest = async (reqId: number) => {
    try {
      await declineFriendRequest(reqId)
      toast.success(t('social.contacts.requestDeclined'))
      await loadFriendsData()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : t('social.contacts.requestDeclineFailed')
      toast.error(msg)
    }
  }

  const handleRemoveFriend = async (friendId: number) => {
    try {
      await removeFriend(friendId)
      // Mit der Freundschaft endet der Funke, sofort und unwiderruflich.
      void useFunkenStore.getState().vergiss(friendId)
      toast.success(t('social.contacts.removed'))
      await loadFriendsData()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : t('social.contacts.removeFailed')
      toast.error(msg)
    }
  }

  const acceptedFriends = useMemo(() => {
    return friends.filter((f) => f.status === 'accepted')
  }, [friends])

  const filteredFriends = useMemo(() => {
    const q = searchFriend.toLowerCase().trim()
    return acceptedFriends.filter((f) => !q || f.username.toLowerCase().includes(q))
  }, [acceptedFriends, searchFriend])

  const blockedList = useMemo(() => {
    return blockedUserIds.map((uid) => {
      const profile = blockedProfiles[uid]
      const fromFriends = friends.find((f) => (f.user_id ?? f.id) === uid)
      return {
        userId: uid,
        username: profile?.username || fromFriends?.username || t('social.contacts.unknownUser', { id: uid }),
        avatarUrl: profile?.avatarUrl || fromFriends?.avatar_url || null,
      }
    })
  }, [blockedUserIds, blockedProfiles, friends])

  const mutedList = useMemo(() => {
    const now = Date.now()
    const list: Array<{
      mailboxId: string
      name: string
      avatarUrl?: string | null
      expiry: number
      remainingLabel: string
    }> = []
    for (const [mid, expiry] of Object.entries(mutedChats)) {
      if (expiry === 0 || expiry > now) {
        const meta = mailboxDirectory[mid]
        let remainingLabel = t('social.contacts.mutePermanent')
        if (expiry > 0) {
          const diffMin = Math.round((expiry - now) / (60 * 1000))
          if (diffMin < 60) {
            remainingLabel = t('social.contacts.muteMinutesLeft', { count: diffMin })
          } else if (diffMin < 24 * 60) {
            remainingLabel = t('social.contacts.muteHoursLeft', { count: Math.round(diffMin / 60) })
          } else {
            remainingLabel = t('social.contacts.muteDaysLeft', { count: Math.round(diffMin / (24 * 60)) })
          }
        }
        list.push({
          mailboxId: mid,
          name: meta?.name || t('social.contacts.unnamedChat', { id: mid.slice(0, 8) }),
          avatarUrl: meta?.avatarUrl,
          expiry,
          remainingLabel,
        })
      }
    }
    return list
  }, [mutedChats, mailboxDirectory])

  const [readReceiptsEnabled, setReadReceiptsEnabled] = useState(() => {
    try {
      return localStorage.getItem('msm_read_receipts_enabled') !== 'false'
    } catch {
      return true
    }
  })

  const handleToggleReadReceipts = () => {
    const nextVal = !readReceiptsEnabled
    setReadReceiptsEnabled(nextVal)
    // Gewählt ist gewählt — an oder aus. Der Standard ist an, und wer ihn
    // nie anfasst, hat nichts gewählt.
    meldeErrungenschaft('social_read_receipts')
    try {
      localStorage.setItem('msm_read_receipts_enabled', String(nextVal))
      toast.success(nextVal ? t('social.privacy.receiptsOn') : t('social.privacy.receiptsOff'))
    } catch {
      // Non-blocking
    }
  }

  return (
    <div className="space-y-6">
      {/* 1. Freunde & Kontakte (Ganz oben) */}
      <section className="msm-card p-6" aria-labelledby="social-contacts-title">
        <div className="flex items-center gap-2 mb-2">
          <Users className="h-5 w-5 text-secondary" aria-hidden="true" />
          <h2 id="social-contacts-title" className="font-headline text-title-lg font-semibold text-on-surface">
            {t('social.contacts.title')}
          </h2>
        </div>
        <p className="max-w-2xl font-body-md text-sm leading-6 text-on-surface-variant mb-5">
          {t('social.contacts.description')}
        </p>

        {/* Freund hinzufügen Formular */}
        <div className="max-w-md mb-6 p-4 rounded-xl bg-surface-container-high/40 border border-outline-variant/30">
          <h3 className="text-xs font-bold uppercase tracking-wider text-on-surface-variant mb-2.5">
            {t('social.contacts.addFriend')}
          </h3>
          <form onSubmit={handleSendFriendRequest} className="flex gap-2">
            <Input
              value={addUsername}
              onChange={(e: React.ChangeEvent<HTMLInputElement>) => setAddUsername(e.target.value)}
              placeholder={t('social.contacts.usernamePlaceholder')}
              className="text-xs h-8 flex-1"
              disabled={sendingRequest}
            />
            <Button
              type="submit"
              size="sm"
              disabled={!addUsername.trim() || sendingRequest}
              className="gap-1.5 h-8 text-xs shrink-0"
            >
              <UserPlus className="w-3.5 h-3.5" />
              <span>{t('social.contacts.sendRequest')}</span>
            </Button>
          </form>
        </div>

        {/* Ausstehende Anfragen */}
        {incomingRequests.length > 0 && (
          <div className="mb-6 space-y-2 max-w-xl">
            <h3 className="text-xs font-bold uppercase tracking-wider text-status-warning flex items-center gap-1.5">
              <span>{t('social.contacts.pendingRequests', { count: incomingRequests.length })}</span>
            </h3>
            <div className="space-y-2">
              {incomingRequests.map((req) => (
                <div
                  key={req.id}
                  className="flex items-center justify-between p-3 rounded-xl bg-surface-container-high/50 border border-status-warning/30 shadow-sm"
                >
                  <div className="flex items-center gap-2.5">
                    <Avatar src={req.avatar_url} name={req.username} size="sm" />
                    <span className="text-xs font-semibold text-primary">{req.username}</span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <Button
                      variant="primary"
                      size="sm"
                      onClick={() => void handleAcceptRequest(req.id)}
                      className="gap-1 h-7 text-xs px-2.5"
                    >
                      <Check className="w-3.5 h-3.5" />
                      <span>{t('social.contacts.accept')}</span>
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => void handleDeclineRequest(req.id)}
                      className="gap-1 h-7 text-xs px-2.5 text-status-destructive hover:bg-status-destructive/10"
                    >
                      <X className="w-3.5 h-3.5" />
                      <span>{t('social.contacts.decline')}</span>
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Sub-Navigation: Deine Freunde, Blockierte, Stummgeschaltete */}
        <div className="flex items-center gap-2 mb-4 border-b border-outline-variant/30 pb-3 flex-wrap">
          <Button
            variant={contactSubTab === 'friends' ? 'primary' : 'ghost'}
            size="sm"
            onClick={() => setContactSubTab('friends')}
            className="text-xs h-7 px-3 gap-1.5"
          >
            <Users className="w-3.5 h-3.5" />
            <span>{t('social.contacts.tabFriends', { count: acceptedFriends.length })}</span>
          </Button>

          <Button
            variant={contactSubTab === 'blocked' ? 'primary' : 'ghost'}
            size="sm"
            onClick={() => setContactSubTab('blocked')}
            className="text-xs h-7 px-3 gap-1.5"
          >
            <Ban className="w-3.5 h-3.5" />
            <span>{t('social.contacts.tabBlocked', { count: blockedList.length })}</span>
          </Button>

          <Button
            variant={contactSubTab === 'muted' ? 'primary' : 'ghost'}
            size="sm"
            onClick={() => setContactSubTab('muted')}
            className="text-xs h-7 px-3 gap-1.5"
          >
            <BellOff className="w-3.5 h-3.5" />
            <span>{t('social.contacts.tabMuted', { count: mutedList.length })}</span>
          </Button>
        </div>

        {/* 1A. Tab: Deine Freunde */}
        {contactSubTab === 'friends' && (
          <div className="space-y-3">
            <div className="flex items-center justify-between gap-3 max-w-xl">
              <h3 className="text-xs font-bold uppercase tracking-wider text-on-surface-variant">
                {t('social.contacts.myContacts', { count: acceptedFriends.length })}
              </h3>
              {acceptedFriends.length > 3 && (
                <div className="relative w-48">
                  <Search className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-on-surface-variant/60" />
                  <Input
                    value={searchFriend}
                    onChange={(e: React.ChangeEvent<HTMLInputElement>) => {
                      setSearchFriend(e.target.value)
                      setVisibleFriendsCount(12)
                    }}
                    placeholder={t('common.search')}
                    className="text-xs pl-8 h-7"
                  />
                </div>
              )}
            </div>

            {filteredFriends.length === 0 ? (
              <p className="text-xs text-on-surface-variant/70 py-6">
                {searchFriend
                  ? t('social.friends.noMatches')
                  : t('social.contacts.none')}
              </p>
            ) : (
              <>
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                  {filteredFriends.slice(0, visibleFriendsCount).map((f) => (
                    <div
                      key={f.id}
                      className="flex items-center justify-between p-3 rounded-xl bg-surface-container-low border border-outline-variant/30 hover:border-outline-variant/60 transition-colors"
                    >
                      <div className="flex items-center gap-3 min-w-0">
                        <Kurzinfo text={t('social.profile.open', { name: f.username })} seite="anfang" aussen="shrink-0">
                          <button
                            type="button"
                            onClick={() => navigate(`/user/${f.user_id ?? f.id}`)}
                            className="relative rounded-full"
                            aria-label={t('social.profile.open', { name: f.username })}
                          >
                            <Avatar src={f.avatar_url} name={f.username} size="sm" />
                            <StatusDot
                              status={f.presence?.status || 'invisible'}
                              size="sm"
                              className="absolute bottom-0 right-0"
                            />
                          </button>
                        </Kurzinfo>
                        <div className="min-w-0">
                          <div className="flex items-center gap-1.5">
                            <button
                              type="button"
                              onClick={() => navigate(`/user/${f.user_id ?? f.id}`)}
                              className="text-xs font-semibold text-primary truncate hover:underline"
                            >
                              {f.username}
                            </button>
                            {/* Wiederhergestellt wird im Chat: nur dort lässt sich
                                die Nachricht an beide Seiten verschlüsseln. */}
                            <FunkenAbzeichen
                              partnerId={f.user_id ?? f.id}
                              name={f.username}
                              interaktiv
                              onWiederherstellen={() =>
                                navigate(`/chat?userId=${f.user_id ?? f.id}&funke=retten`)
                              }
                            />
                            <DeviceBadge deviceType={f.presence?.device_type} />
                          </div>
                          {f.presence?.activity_label && (
                            <p className="text-label-sm text-on-surface-variant/80 truncate">
                              {f.presence.activity_label}
                            </p>
                          )}
                        </div>
                      </div>

                      <Kurzinfo text={t('social.contacts.remove')} seite="ende" aussen="shrink-0 ml-2">
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => void handleRemoveFriend(f.user_id ?? f.id)}
                          className="h-7 w-7 p-0 text-on-surface-variant hover:text-status-destructive hover:bg-status-destructive/10"
                          aria-label={t('social.contacts.remove')}
                        >
                          <UserMinus className="w-3.5 h-3.5" />
                        </Button>
                      </Kurzinfo>
                    </div>
                  ))}
                </div>

                {/* Lazy Load Button für Freunde */}
                {filteredFriends.length > visibleFriendsCount && (
                  <div className="pt-2 flex justify-center">
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => setVisibleFriendsCount((prev) => prev + 12)}
                      className="text-xs gap-1.5 px-4"
                    >
                      <span>{t('social.contacts.loadMore', { count: filteredFriends.length - visibleFriendsCount })}</span>
                    </Button>
                  </div>
                )}
              </>
            )}
          </div>
        )}

        {/* 1B. Tab: Blockierte Kontakte */}
        {contactSubTab === 'blocked' && (
          <div className="space-y-3">
            <h3 className="text-xs font-bold uppercase tracking-wider text-on-surface-variant">
              {t('social.contacts.blockedHeading', { count: blockedList.length })}
            </h3>
            {blockedList.length === 0 ? (
              <p className="text-xs text-on-surface-variant/70 py-6">
                {t('social.contacts.noBlocked')}
              </p>
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                {blockedList.map((b) => (
                  <div
                    key={b.userId}
                    className="flex items-center justify-between p-3 rounded-xl bg-surface-container-low border border-status-destructive/30"
                  >
                    <div className="flex items-center gap-3 min-w-0">
                      <Avatar src={b.avatarUrl} name={b.username} size="sm" />
                      <div className="min-w-0">
                        <span className="text-xs font-semibold text-primary truncate block">
                          {b.username}
                        </span>
                        <span className="text-label-sm text-status-destructive font-medium">
                          {t('social.contacts.blocked')}
                        </span>
                      </div>
                    </div>

                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={async () => {
                        await unblockUser(b.userId)
                        toast.success(t('social.contacts.unblocked', { name: b.username }))
                      }}
                      className="h-7 text-xs px-2.5 border border-status-destructive/30 text-status-destructive hover:bg-status-destructive/15 shrink-0"
                    >
                      {t('social.contacts.unblock')}
                    </Button>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* 1C. Tab: Stummgeschaltete Chats */}
        {contactSubTab === 'muted' && (
          <div className="space-y-3">
            <h3 className="text-xs font-bold uppercase tracking-wider text-on-surface-variant">
              {t('social.contacts.mutedHeading', { count: mutedList.length })}
            </h3>
            {mutedList.length === 0 ? (
              <p className="text-xs text-on-surface-variant/70 py-6">
                {t('social.contacts.noMuted')}
              </p>
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                {mutedList.map((m) => (
                  <div
                    key={m.mailboxId}
                    className="flex items-center justify-between p-3 rounded-xl bg-surface-container-low border border-outline-variant/30"
                  >
                    <div className="flex items-center gap-3 min-w-0">
                      <div className="w-8 h-8 rounded-full bg-surface-container-highest flex items-center justify-center text-status-warning shrink-0">
                        <BellOff className="w-4 h-4" />
                      </div>
                      <div className="min-w-0">
                        <span className="text-xs font-semibold text-primary truncate block">
                          {m.name}
                        </span>
                        <span className="text-label-sm text-on-surface-variant/80 flex items-center gap-1">
                          <Clock className="w-3 h-3 text-status-warning" />
                          <span>{m.remainingLabel}</span>
                        </span>
                      </div>
                    </div>

                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => {
                        unmuteChat(m.mailboxId)
                        toast.success(t('social.contacts.unmuted'))
                      }}
                      className="h-7 text-xs px-2.5 text-on-surface-variant hover:text-primary shrink-0"
                    >
                      {t('social.contacts.unmute')}
                    </Button>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </section>

      {/* 2. Chat-Privatsphäre & Lesebestätigungen */}
      <section className="msm-card p-6" aria-labelledby="chat-privacy-title">
        <div className="flex items-center gap-2 mb-2">
          <Lock className="h-5 w-5 text-secondary" aria-hidden="true" />
          <h2 id="chat-privacy-title" className="font-headline text-title-lg font-semibold text-on-surface">
            {t('social.privacy.title')}
          </h2>
        </div>
        <p className="max-w-2xl font-body-md text-sm leading-6 text-on-surface-variant mb-4">
          {t('social.privacy.description')}
        </p>

        <div className="flex items-center justify-between p-4 rounded-xl bg-surface-container-high/40 border border-outline-variant/30">
          <div className="space-y-0.5 max-w-md">
            <span className="text-xs font-bold text-on-surface">
              {t('social.privacy.receipts')}
            </span>
            <p className="text-label-sm text-on-surface-variant">
              {t('social.privacy.receiptsHint')}
            </p>
          </div>

          <button
            type="button"
            role="switch"
            aria-checked={readReceiptsEnabled}
            onClick={handleToggleReadReceipts}
            className={`relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none ${
              readReceiptsEnabled ? 'bg-primary' : 'bg-surface-container-highest'
            }`}
          >
            <span
              className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow-lg ring-0 transition duration-200 ease-in-out ${
                readReceiptsEnabled ? 'translate-x-5' : 'translate-x-0'
              }`}
            />
          </button>
        </div>
      </section>

      {/* 3. Nutzungszeit — stand bis 09/2026 nur in der App. */}
      <section className="msm-card p-6" aria-labelledby="activity-time-title">
        <div className="flex items-center gap-2 mb-4">
          <Clock className="h-5 w-5 text-secondary" aria-hidden="true" />
          <h2 id="activity-time-title" className="font-headline text-title-lg font-semibold text-on-surface">
            {t('social.profile.activityTitle')}
          </h2>
        </div>
        <Nutzungszeit stats={stats} />
      </section>

      {/* 4. Meilensteine & Fortschritt */}
      <Meilensteine
        achievements={overview?.achievements ?? []}
        freigeschaltet={overview?.total_unlocked ?? 0}
        gesamt={overview?.total_available ?? 0}
        punkte={overview?.prestige_score ?? 0}
        titel={t('social.milestones.title')}
        beschreibung={t('social.milestones.description')}
      />
    </div>
  )
}
