import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Check,
  CheckCircle2,
  Clock,
  Lock,
  Phone,
  Trophy,
  UserMinus,
  UserPlus,
  Users,
  Ban,
  BellOff,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { useMessengerNotificationStore } from '@/stores/messengerNotificationStore'
import {
  getAchievements,
  getFriends,
  getFriendRequests,
  getStats,
  sendFriendRequest,
  acceptFriendRequest,
  declineFriendRequest,
  removeFriend,
  updatePrivacy,
  type AchievementsOverview,
  type FriendItem,
  type UserStatsResponse,
} from '@/api/social'
import { renderAchievementIcon } from '@/components/social/achievementIcons'
import { StatusDot } from '@/components/social/StatusIndicator'
import { formatActivityCategory } from '@/hooks/usePresenceAndActivity'
import { Avatar, Badge, Button, Dropdown, type DropdownOption, Input, Switch } from '@/Singra/UI'
import { useAuthStore } from '@/stores/authStore'
import { toast } from '@/stores/toastStore'

export function SocialEinstellungen() {
  const { t } = useTranslation()
  const user = useAuthStore((s) => s.user)
  const updateUser = useAuthStore((s) => s.updateUser)

  // 1. {t('mss.social.privatsphaereTitel')}
  const [privacyLevel, setPrivacyLevel] = useState<'public' | 'friends' | 'private'>(
    (user?.social_privacy as 'public' | 'friends' | 'private') || 'friends',
  )
  const [savingPrivacy, setSavingPrivacy] = useState(false)

  const [readReceiptsEnabled, setReadReceiptsEnabled] = useState(() => {
    try {
      return localStorage.getItem('msm_read_receipts_enabled') !== 'false'
    } catch {
      return true
    }
  })

  useEffect(() => {
    if (user?.social_privacy) {
      setPrivacyLevel(user.social_privacy as 'public' | 'friends' | 'private')
    }
  }, [user?.social_privacy])

  const handleSavePrivacy = async (level: 'public' | 'friends' | 'private') => {
    setSavingPrivacy(true)
    try {
      const res = await updatePrivacy({ privacy: level })
      const valid = res.social_privacy === 'public' || res.social_privacy === 'friends' || res.social_privacy === 'private'
        ? res.social_privacy
        : level
      updateUser({ social_privacy: valid })
      setPrivacyLevel(valid)
      toast.success(t('profile.privacySaved'))
    } catch {
      toast.error(t('profile.privacySaveFailed'))
    } finally {
      setSavingPrivacy(false)
    }
  }

  const handleToggleReadReceipts = () => {
    const nextVal = !readReceiptsEnabled
    setReadReceiptsEnabled(nextVal)
    try {
      localStorage.setItem('msm_read_receipts_enabled', String(nextVal))
      toast.success(nextVal ? 'Lesebestätigungen aktiv' : 'Lesebestätigungen aus')
    } catch {}
  }

  const privacyOptions: DropdownOption[] = [
    { value: 'friends', label: 'Freunde (Status sichtbar für Kontakte)' },
    { value: 'public', label: 'Öffentlich (Für alle sichtbar)' },
    { value: 'private', label: 'Privat (Unsichtbar / verborgen)' },
  ]

  // 2. Spielzeit & Aktivität
  const [stats, setStats] = useState<UserStatsResponse | null>(null)
      {/* 3. Meilensteine */}
  const [overview, setOverview] = useState<AchievementsOverview | null>(null)
  const [milestoneFilter, setMilestoneFilter] = useState<'all' | 'unlocked' | 'locked'>('all')

  // 4. Freunde & Kontakte State
  const [friends, setFriends] = useState<FriendItem[]>([])
  const [incomingRequests, setIncomingRequests] = useState<FriendItem[]>([])
  const [addUsername, setAddUsername] = useState('')
  const [searchFriend, setSearchFriend] = useState('')
  const [sendingRequest, setSendingRequest] = useState(false)
  const [contactSubTab, setContactSubTab] = useState<'friends' | 'blocked' | 'muted'>('friends')
  const [visibleFriendsCount, setVisibleFriendsCount] = useState(12)
  const [visibleMilestonesCount, setVisibleMilestonesCount] = useState(12)

  // Notification Store for Mute & Block
  const blockedUserIds = useMessengerNotificationStore((s) => s.blockedUserIds)
  const blockedProfiles = useMessengerNotificationStore((s) => s.blockedProfiles)
  const unblockUser = useMessengerNotificationStore((s) => s.unblockUser)
  const mutedChats = useMessengerNotificationStore((s) => s.mutedChats)
  const unmuteChat = useMessengerNotificationStore((s) => s.unmuteChat)
  const mailboxDirectory = useMessengerNotificationStore((s) => s.mailboxDirectory)
  const syncBlockedFromBackend = useMessengerNotificationStore((s) => s.syncBlockedFromBackend)

  const loadData = useCallback(async () => {
    try {
      const [statsData, achData, friendsData, reqsData] = await Promise.all([
        getStats().catch(() => null),
        getAchievements().catch(() => null),
        getFriends().catch(() => []),
        getFriendRequests().catch(() => ({ incoming: [], outgoing: [] })),
      ])
      setStats(statsData)
      setOverview(achData)
      setFriends(friendsData)
      setIncomingRequests(reqsData.incoming)
    } catch {
      // Best-effort load
    }
  }, [])

  useEffect(() => {
    void loadData()
    void syncBlockedFromBackend()
  }, [loadData, syncBlockedFromBackend])

  const handleSendFriendRequest = async (e: React.FormEvent) => {
    e.preventDefault()
    const target = addUsername.trim()
    if (!target) return
    setSendingRequest(true)
    try {
      const res = await sendFriendRequest(target)
      toast.success(res.message || 'Anfrage gesendet.')
      setAddUsername('')
      const [fData, rData] = await Promise.all([getFriends(), getFriendRequests()])
      setFriends(fData)
      setIncomingRequests(rData.incoming)
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : t('profile.friends.sendError'))
    } finally {
      setSendingRequest(false)
    }
  }

  const handleAcceptRequest = async (reqId: number) => {
    try {
      await acceptFriendRequest(reqId)
      toast.success(t('profile.friends.accepted'))
      const [fData, rData] = await Promise.all([getFriends(), getFriendRequests()])
      setFriends(fData)
      setIncomingRequests(rData.incoming)
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : t('profile.friends.acceptError'))
    }
  }

  const handleDeclineRequest = async (reqId: number) => {
    try {
      await declineFriendRequest(reqId)
      toast.success(t('profile.friends.rejected'))
      const rData = await getFriendRequests()
      setIncomingRequests(rData.incoming)
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : t('profile.friends.rejectError'))
    }
  }

  const handleRemoveFriend = async (friendId: number) => {
    try {
      await removeFriend(friendId)
      toast.success(t('profile.friends.removed'))
      const fData = await getFriends()
      setFriends(fData)
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : t('profile.friends.removeError'))
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
        username: profile?.username || fromFriends?.username || `Benutzer #${uid}`,
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
        let remainingLabel = 'Dauerhaft'
        if (expiry > 0) {
          const diffMin = Math.round((expiry - now) / (60 * 1000))
          if (diffMin < 60) {
            remainingLabel = `Noch ${diffMin} Min.`
          } else if (diffMin < 24 * 60) {
            remainingLabel = `Noch ${Math.round(diffMin / 60)} Std.`
          } else {
            remainingLabel = `Noch ${Math.round(diffMin / (24 * 60))} Tage`
          }
        }
        list.push({
          mailboxId: mid,
          name: meta?.name || `Chat (${mid.slice(0, 8)})`,
          avatarUrl: meta?.avatarUrl,
          expiry,
          remainingLabel,
        })
      }
    }
    return list
  }, [mutedChats, mailboxDirectory])

  const filteredMilestones = useMemo(() => {
    const list = overview?.achievements || []
    if (milestoneFilter === 'unlocked') return list.filter((m) => m.unlocked)
    if (milestoneFilter === 'locked') return list.filter((m) => !m.unlocked)
    return list
  }, [overview?.achievements, milestoneFilter])

  const progressPercent = overview
    ? Math.round((overview.total_unlocked / Math.max(overview.total_available, 1)) * 100)
    : 0

  const formatHours = (seconds?: number) => {
    if (!seconds || seconds <= 0) return '0 Std.'
    const h = Math.floor(seconds / 3600)
    const m = Math.floor((seconds % 3600) / 60)
    if (h === 0) return `${m} Min.`
    return `${h} Std. ${m} Min.`
  }

  return (
    <div className="flex flex-col gap-6">
      {/* 1. Freunde & Kontakte (Direkt ganz oben!) */}
      <section className="msm-card p-5 space-y-4" aria-labelledby="social-friends-title">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary/10 text-primary">
            <Users className="h-5 w-5" />
          </div>
          <div>
            <h2 id="social-friends-title" className="text-sm font-semibold text-on-surface">
              Freunde & Kontakte
            </h2>
            <p className="text-label-sm text-on-surface-variant mt-0.5">
              {t('mss.social.kontakteHinweis')}
            </p>
          </div>
        </div>

        {/* Add Friend */}
        <form onSubmit={handleSendFriendRequest} className="flex gap-2 pt-2 border-t border-outline-variant/30">
          <Input
            value={addUsername}
            onChange={(e) => setAddUsername(e.target.value)}
            placeholder={t('mss.social.freundPlatzhalter')}
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
            <span>Anfrage</span>
          </Button>
        </form>

        {/* Incoming Requests */}
        {incomingRequests.length > 0 && (
          <div className="space-y-2">
            <span className="text-xs font-bold text-status-warning block">
              Ausstehende Anfragen ({incomingRequests.length})
            </span>
            <div className="space-y-1.5">
              {incomingRequests.map((req) => (
                <div
                  key={req.id}
                  className="flex items-center justify-between p-2.5 rounded-xl bg-surface-container-high/50 border border-status-warning/30"
                >
                  <div className="flex items-center gap-2">
                    <Avatar src={req.avatar_url} name={req.username} size="sm" />
                    <span className="text-xs font-semibold text-primary">{req.username}</span>
                  </div>
                  <div className="flex items-center gap-1">
                    <Button
                      size="sm"
                      variant="primary"
                      onClick={() => void handleAcceptRequest(req.id)}
                      className="h-7 px-2 text-xs gap-1"
                    >
                      <Check className="w-3 h-3" />
                      <span>Annehmen</span>
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => void handleDeclineRequest(req.id)}
                      className="h-7 px-2 text-xs text-on-surface-variant hover:text-status-destructive"
                    >
                      Ablehnen
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Sub-Navigation: Deine Freunde, Blockierte, Stummgeschaltete */}
        <div className="flex items-center gap-2 border-b border-outline-variant/30 pb-2.5 flex-wrap">
          <Button
            variant={contactSubTab === 'friends' ? 'primary' : 'ghost'}
            size="sm"
            onClick={() => setContactSubTab('friends')}
            className="text-xs h-7 px-3 gap-1.5"
          >
            <Users className="w-3.5 h-3.5" />
            <span>Deine Freunde ({acceptedFriends.length})</span>
          </Button>

          <Button
            variant={contactSubTab === 'blocked' ? 'primary' : 'ghost'}
            size="sm"
            onClick={() => setContactSubTab('blocked')}
            className="text-xs h-7 px-3 gap-1.5"
          >
            <Ban className="w-3.5 h-3.5" />
            <span>Blockiert ({blockedList.length})</span>
          </Button>

          <Button
            variant={contactSubTab === 'muted' ? 'primary' : 'ghost'}
            size="sm"
            onClick={() => setContactSubTab('muted')}
            className="text-xs h-7 px-3 gap-1.5"
          >
            <BellOff className="w-3.5 h-3.5" />
            <span>Stummgeschaltet ({mutedList.length})</span>
          </Button>
        </div>

        {/* Tab 1: Deine Freunde */}
        {contactSubTab === 'friends' && (
          <div className="space-y-2">
            {acceptedFriends.length > 3 && (
              <Input
                value={searchFriend}
                onChange={(e) => {
                  setSearchFriend(e.target.value)
                  setVisibleFriendsCount(12)
                }}
                placeholder="Kontakte filtern …"
                className="text-xs h-7"
              />
            )}

            {filteredFriends.length === 0 ? (
              <p className="text-xs text-on-surface-variant py-2">
                {searchFriend ? 'Keine Treffer.' : 'Noch keine Kontakte hinzugefügt.'}
              </p>
            ) : (
              <>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                  {filteredFriends.slice(0, visibleFriendsCount).map((f) => (
                    <div
                      key={f.id}
                      className="flex items-center justify-between p-2.5 rounded-xl bg-surface-container-low border border-outline-variant/30"
                    >
                      <div className="flex items-center gap-2.5 min-w-0">
                        <div className="relative shrink-0">
                          <Avatar src={f.avatar_url} name={f.username} size="sm" />
                          <StatusDot
                            status={f.presence?.status || 'invisible'}
                            size="sm"
                            className="absolute bottom-0 right-0"
                          />
                        </div>
                        <div className="min-w-0">
                          <span className="text-xs font-semibold text-primary truncate block">
                            {f.username}
                          </span>
                          {f.presence?.activity_label && (
                            <p className="text-label-sm text-on-surface-variant truncate">
                              {f.presence.activity_label}
                            </p>
                          )}
                        </div>
                      </div>
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => void handleRemoveFriend(f.user_id ?? f.id)}
                        className="h-7 w-7 p-0 text-on-surface-variant hover:text-status-destructive shrink-0"
                        title="Kontakt entfernen"
                        aria-label="Kontakt entfernen"
                      >
                        <UserMinus className="w-3.5 h-3.5" />
                      </Button>
                    </div>
                  ))}
                </div>

                {/* Lazy Load Button */}
                {filteredFriends.length > visibleFriendsCount && (
                  <div className="pt-2 flex justify-center">
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => setVisibleFriendsCount((prev) => prev + 12)}
                      className="text-xs gap-1.5 px-4"
                    >
                      <span>Weitere Kontakte laden ({filteredFriends.length - visibleFriendsCount} verbleibend)</span>
                    </Button>
                  </div>
                )}
              </>
            )}
          </div>
        )}

        {/* Tab 2: Blockierte Kontakte */}
        {contactSubTab === 'blocked' && (
          <div className="space-y-2">
            {blockedList.length === 0 ? (
              <p className="text-xs text-on-surface-variant py-2">
                Keine blockierten Kontakte vorhanden.
              </p>
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                {blockedList.map((b) => (
                  <div
                    key={b.userId}
                    className="flex items-center justify-between p-2.5 rounded-xl bg-surface-container-low border border-status-destructive/30"
                  >
                    <div className="flex items-center gap-2.5 min-w-0">
                      <Avatar src={b.avatarUrl} name={b.username} size="sm" />
                      <div className="min-w-0">
                        <span className="text-xs font-semibold text-primary truncate block">
                          {b.username}
                        </span>
                        <span className="text-label-sm text-status-destructive font-medium">
                          {t('profile.friends.blockedStatus')}
                        </span>
                      </div>
                    </div>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={async () => {
                        await unblockUser(b.userId)
                        toast.success(t('profile.friends.unblocked', { username: b.username }))
                      }}
                      className="h-7 text-xs px-2.5 border border-status-destructive/30 text-status-destructive hover:bg-status-destructive/15 shrink-0"
                    >
                      {t('profile.friends.unblock')}
                    </Button>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Tab 3: Stummgeschaltete Chats */}
        {contactSubTab === 'muted' && (
          <div className="space-y-2">
            {mutedList.length === 0 ? (
              <p className="text-xs text-on-surface-variant py-2">
                {t('profile.friends.noMutedChats')}
              </p>
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                {mutedList.map((m) => (
                  <div
                    key={m.mailboxId}
                    className="flex items-center justify-between p-2.5 rounded-xl bg-surface-container-low border border-outline-variant/30"
                  >
                    <div className="flex items-center gap-2.5 min-w-0">
                      <div className="w-7 h-7 rounded-full bg-surface-container-highest flex items-center justify-center text-status-warning shrink-0">
                        <BellOff className="w-3.5 h-3.5" />
                      </div>
                      <div className="min-w-0">
                        <span className="text-xs font-semibold text-primary truncate block">
                          {m.name}
                        </span>
                        <span className="text-label-sm text-on-surface-variant flex items-center gap-1">
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
                        toast.success(t('profile.friends.unmuted'))
                      }}
                      className="h-7 text-xs px-2.5 text-on-surface-variant hover:text-primary shrink-0"
                    >
                      {t('profile.friends.unmute')}
                    </Button>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </section>

      {/* 2. {t('mss.social.privatsphaereTitel')} */}
      <section className="msm-card p-5 space-y-4" aria-labelledby="social-privacy-title">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary/10 text-primary">
            <Lock className="h-5 w-5" />
          </div>
          <div>
            <h2 id="social-privacy-title" className="text-sm font-semibold text-on-surface">
              {t('mss.social.privatsphaereTitel')}
            </h2>
          </div>
        </div>

        <div className="space-y-4 pt-2 border-t border-outline-variant/30">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
            <div>
              <span className="text-xs font-medium text-on-surface">Profil-Sichtbarkeit & Status</span>
              <p className="text-label-sm text-on-surface-variant">{t('mss.social.sichtbarkeitFrage')}</p>
            </div>
            <div className="w-full sm:w-64">
              <Dropdown
                options={privacyOptions}
                value={privacyLevel}
                disabled={savingPrivacy}
                onChange={(val) => void handleSavePrivacy(val as 'public' | 'friends' | 'private')}
                aria-label="Profil-Sichtbarkeit"
              />
            </div>
          </div>

          {/* Dieselbe Zusage wie im Panel: die Sichtbarkeit öffnet den Status,
              nie das Klingeln. */}
          <p className="flex items-start gap-2 rounded-lg bg-surface-container-high/60 px-3 py-2 text-label-sm text-on-surface-variant">
            <Phone className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" aria-hidden="true" />
            <span>{t('profile.privacyCallsFriendsOnly')}</span>
          </p>

          <div className="flex items-center justify-between gap-3 pt-2 border-t border-outline-variant/20">
            <div>
              <span className="text-xs font-medium text-on-surface">{t('mss.social.lesebestaetigungenLang')}</span>
              <p className="text-label-sm text-on-surface-variant">Zeigt Kontakten, sobald Nachrichten gelesen wurden.</p>
            </div>
            <Switch
              checked={readReceiptsEnabled}
              onCheckedChange={handleToggleReadReceipts}
              aria-label={t('mss.social.lesebestaetigungen')}
            />
          </div>
        </div>
      </section>

      {/* 3. Spielzeit / Nutzungszeit */}
      <section className="msm-card p-5 space-y-4" aria-labelledby="social-time-title">
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary/10 text-primary">
              <Clock className="h-5 w-5" />
            </div>
            <div>
              <h2 id="social-time-title" className="text-sm font-semibold text-on-surface">
                Nutzungs- & Spielzeit
              </h2>
            </div>
          </div>
          <div className="text-right">
            <span className="text-xs font-bold text-primary font-mono block">
              {formatHours(stats?.active_time_seconds ?? stats?.total_activity_seconds)}
            </span>
            <span className="text-label-sm text-on-surface-variant">{t('mss.social.gesamtaktivitaet')}</span>
          </div>
        </div>

        {stats?.active_time_by_category && Object.keys(stats.active_time_by_category).length > 0 && (
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-2.5 pt-2 border-t border-outline-variant/30">
            {Object.entries(stats.active_time_by_category).map(([cat, secs]) => (
              <div key={cat} className="p-2.5 rounded-xl bg-surface-container-low border border-outline-variant/30">
                <span className="text-label-sm font-bold text-on-surface-variant tracking-wider block truncate">
                  {formatActivityCategory(cat)}
                </span>
                <span className="text-xs font-semibold text-on-surface font-mono">
                  {formatHours(secs)}
                </span>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* 4. Meilensteine & Erfolge (Mit Lazy-Load) */}
      <section className="msm-card p-5 space-y-4" aria-labelledby="social-milestones-title">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary/10 text-primary">
              <Trophy className="h-5 w-5" />
            </div>
            <div>
              <h2 id="social-milestones-title" className="text-sm font-semibold text-on-surface">
                Meilensteine & Erfolge
              </h2>
            </div>
          </div>

          <div className="flex items-center gap-3 self-start sm:self-auto">
            <div className="text-right">
              <span className="text-xs font-bold text-primary font-mono block">
                {overview?.total_unlocked || 0} / {overview?.total_available || 0}
              </span>
              <span className="text-label-sm text-on-surface-variant font-mono">
                {overview?.prestige_score || 0} Pkt
              </span>
            </div>
            <div className="w-20 h-2 bg-surface-container-high rounded-full overflow-hidden border border-outline-variant/30">
              <div
                className="h-full bg-primary transition-all duration-500 rounded-full"
                style={{ width: `${progressPercent}%` }}
              />
            </div>
          </div>
        </div>

        {/* Filter Pills */}
        <div className="flex items-center gap-1.5 pt-2 border-t border-outline-variant/20">
          <Button
            variant={milestoneFilter === 'all' ? 'primary' : 'ghost'}
            size="sm"
            onClick={() => {
              setMilestoneFilter('all')
              setVisibleMilestonesCount(12)
            }}
            className="text-xs h-7 px-2.5"
          >
            Alle ({overview?.achievements.length || 0})
          </Button>
          <Button
            variant={milestoneFilter === 'unlocked' ? 'primary' : 'ghost'}
            size="sm"
            onClick={() => {
              setMilestoneFilter('unlocked')
              setVisibleMilestonesCount(12)
            }}
            className="text-xs h-7 px-2.5"
          >
            Freigeschaltet ({overview?.total_unlocked || 0})
          </Button>
          <Button
            variant={milestoneFilter === 'locked' ? 'primary' : 'ghost'}
            size="sm"
            onClick={() => {
              setMilestoneFilter('locked')
              setVisibleMilestonesCount(12)
            }}
            className="text-xs h-7 px-2.5"
          >
            Gesperrt ({(overview?.total_available || 0) - (overview?.total_unlocked || 0)})
          </Button>
        </div>

        {/* Milestones Grid mit Lazy-Load */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
          {filteredMilestones.slice(0, visibleMilestonesCount).map((m) => {
            const rarity = m.rarity_percent ?? m.global_unlocked_percentage ?? 0
            const isRare = rarity > 0 && rarity <= 10
            return (
              <div
                key={m.id}
                className={`flex items-start gap-3 p-3 rounded-xl border transition-all ${
                  m.unlocked
                    ? 'bg-surface-container-low border-outline-variant/40 shadow-sm'
                    : 'bg-surface-container-lowest/40 border-outline-variant/20 opacity-55'
                }`}
              >
                <div
                  className={`w-9 h-9 rounded-xl flex items-center justify-center shrink-0 border ${
                    m.unlocked
                      ? isRare
                        ? 'bg-status-warning/20 border-status-warning/40 text-status-warning'
                        : 'bg-primary/15 border-primary/30 text-primary'
                      : 'bg-surface-container-high/50 border-outline-variant/20 text-on-surface-variant/40'
                  }`}
                >
                  {m.unlocked ? renderAchievementIcon(m.icon, 'w-4 h-4') : <Lock className="w-3.5 h-3.5" />}
                </div>

                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-1.5 flex-wrap">
                    <span className="text-xs font-bold text-on-surface truncate">{m.title}</span>
                    <span className="text-label-sm font-mono text-status-warning font-semibold">+{m.points}</span>
                    {isRare && (
                      <Badge variant="warning" className="text-label-sm px-1 py-0 uppercase font-bold">
                        Selten
                      </Badge>
                    )}
                  </div>
                  <p className="text-label-sm text-on-surface-variant mt-0.5 line-clamp-2">
                    {m.description}
                  </p>
                  {m.unlocked && m.unlocked_at && (
                    <span className="inline-flex items-center gap-1 text-label-sm text-status-success mt-1">
                      <CheckCircle2 className="w-3 h-3" />
                      <span>{new Date(m.unlocked_at).toLocaleDateString()}</span>
                    </span>
                  )}
                </div>
              </div>
            )
          })}
        </div>

        {/* Lazy Load Button für Erfolge */}
        {filteredMilestones.length > visibleMilestonesCount && (
          <div className="pt-2 flex justify-center">
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setVisibleMilestonesCount((prev) => prev + 12)}
              className="text-xs gap-1.5 px-4"
            >
              <span>Weitere Erfolge anzeigen ({filteredMilestones.length - visibleMilestonesCount} verbleibend)</span>
            </Button>
          </div>
        )}
      </section>
    </div>
  )
}
