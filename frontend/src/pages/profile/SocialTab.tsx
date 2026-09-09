import React, { useState, useEffect, useMemo } from 'react'
import {
  Avatar,
  Button,
  Input,
  Badge,
} from '@/Singra/UI'
import {
  Users,
  UserPlus,
  Check,
  X,
  UserMinus,
  Trophy,
  Search,
  Lock,
  CheckCircle2,
  Ban,
  BellOff,
  Clock,
} from 'lucide-react'
import { DeviceBadge } from '@/components/social/DeviceBadge'
import { StatusDot } from '@/components/social/StatusIndicator'
import { renderAchievementIcon } from '@/components/social/achievementIcons'
import {
  type FriendItem,
  type AchievementsOverview,
  getFriends,
  getFriendRequests,
  sendFriendRequest,
  acceptFriendRequest,
  declineFriendRequest,
  removeFriend,
  getAchievements,
} from '@/api/social'
import { toast } from '@/stores/toastStore'
import { useMessengerNotificationStore } from '@/stores/messengerNotificationStore'

export function SocialTab() {
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
  const [milestoneFilter, setMilestoneFilter] = useState<'all' | 'unlocked' | 'locked'>('all')
  const [visibleMilestonesCount, setVisibleMilestonesCount] = useState(12)

  // Notification Store for Mute & Block
  const blockedUserIds = useMessengerNotificationStore((s) => s.blockedUserIds)
  const blockedProfiles = useMessengerNotificationStore((s) => s.blockedProfiles)
  const unblockUser = useMessengerNotificationStore((s) => s.unblockUser)
  const mutedChats = useMessengerNotificationStore((s) => s.mutedChats)
  const unmuteChat = useMessengerNotificationStore((s) => s.unmuteChat)
  const mailboxDirectory = useMessengerNotificationStore((s) => s.mailboxDirectory)
  const syncBlockedFromBackend = useMessengerNotificationStore((s) => s.syncBlockedFromBackend)

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
      const ovData = await getAchievements().catch(() => null)
      setOverview(ovData)
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
      toast.success(res.message || 'Freundschaftsanfrage gesendet.')
      setAddUsername('')
      await loadFriendsData()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Fehler beim Senden der Anfrage.'
      toast.error(msg)
    } finally {
      setSendingRequest(false)
    }
  }

  const handleAcceptRequest = async (reqId: number) => {
    try {
      await acceptFriendRequest(reqId)
      toast.success('Freundschaftsanfrage angenommen.')
      await loadFriendsData()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Fehler beim Annehmen der Anfrage.'
      toast.error(msg)
    }
  }

  const handleDeclineRequest = async (reqId: number) => {
    try {
      await declineFriendRequest(reqId)
      toast.success('Freundschaftsanfrage abgelehnt.')
      await loadFriendsData()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Fehler beim Ablehnen der Anfrage.'
      toast.error(msg)
    }
  }

  const handleRemoveFriend = async (friendId: number) => {
    try {
      await removeFriend(friendId)
      toast.success('Kontakt entfernt.')
      await loadFriendsData()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Fehler beim Entfernen des Kontakts.'
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
    try {
      localStorage.setItem('msm_read_receipts_enabled', String(nextVal))
      toast.success(nextVal ? 'Lesebestätigungen aktiviert' : 'Lesebestätigungen deaktiviert')
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
          <h2 id="social-contacts-title" className="font-headline text-lg font-semibold text-on-surface">
            Freunde & Kontakte
          </h2>
        </div>
        <p className="max-w-2xl font-body-md text-sm leading-6 text-on-surface-variant mb-5">
          Verwalte deine bestätigten Kontakte, blockierte Personen und stummgeschaltete Unterhaltungen.
        </p>

        {/* Freund hinzufügen Formular */}
        <div className="max-w-md mb-6 p-4 rounded-xl bg-surface-container-high/40 border border-outline-variant/30">
          <h3 className="text-xs font-bold uppercase tracking-wider text-on-surface-variant mb-2.5">
            Freund hinzufügen
          </h3>
          <form onSubmit={handleSendFriendRequest} className="flex gap-2">
            <Input
              value={addUsername}
              onChange={(e: React.ChangeEvent<HTMLInputElement>) => setAddUsername(e.target.value)}
              placeholder="Benutzername eingeben …"
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
              <span>Anfrage senden</span>
            </Button>
          </form>
        </div>

        {/* Ausstehende Anfragen */}
        {incomingRequests.length > 0 && (
          <div className="mb-6 space-y-2 max-w-xl">
            <h3 className="text-xs font-bold uppercase tracking-wider text-amber-400 flex items-center gap-1.5">
              <span>Ausstehende Anfragen ({incomingRequests.length})</span>
            </h3>
            <div className="space-y-2">
              {incomingRequests.map((req) => (
                <div
                  key={req.id}
                  className="flex items-center justify-between p-3 rounded-xl bg-surface-container-high/50 border border-amber-500/30 shadow-xs"
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
                      <span>Annehmen</span>
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => void handleDeclineRequest(req.id)}
                      className="gap-1 h-7 text-xs px-2.5 text-rose-400 hover:bg-rose-500/10"
                    >
                      <X className="w-3.5 h-3.5" />
                      <span>Ablehnen</span>
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

        {/* 1A. Tab: Deine Freunde */}
        {contactSubTab === 'friends' && (
          <div className="space-y-3">
            <div className="flex items-center justify-between gap-3 max-w-xl">
              <h3 className="text-xs font-bold uppercase tracking-wider text-on-surface-variant">
                Meine Kontakte ({acceptedFriends.length})
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
                    placeholder="Suchen …"
                    className="text-xs pl-8 h-7"
                  />
                </div>
              )}
            </div>

            {filteredFriends.length === 0 ? (
              <p className="text-xs text-on-surface-variant/70 py-6">
                {searchFriend
                  ? 'Keine Treffer für die Suche.'
                  : 'Noch keine Kontakte hinzugefügt. Sende oben eine Anfrage, um Kontakte zu verbinden.'}
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
                        <div className="relative shrink-0">
                          <Avatar src={f.avatar_url} name={f.username} size="sm" />
                          <StatusDot
                            status={f.presence?.status || 'invisible'}
                            size="sm"
                            className="absolute bottom-0 right-0"
                          />
                        </div>
                        <div className="min-w-0">
                          <div className="flex items-center gap-1.5">
                            <span className="text-xs font-semibold text-primary truncate">
                              {f.username}
                            </span>
                            <DeviceBadge deviceType={f.presence?.device_type} />
                          </div>
                          {f.presence?.activity_label && (
                            <p className="text-[10px] text-on-surface-variant/80 truncate">
                              {f.presence.activity_label}
                            </p>
                          )}
                        </div>
                      </div>

                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => void handleRemoveFriend(f.user_id ?? f.id)}
                        className="h-7 w-7 p-0 text-on-surface-variant hover:text-rose-400 hover:bg-rose-500/10 shrink-0 ml-2"
                        title="Kontakt entfernen"
                        aria-label="Kontakt entfernen"
                      >
                        <UserMinus className="w-3.5 h-3.5" />
                      </Button>
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
                      <span>Weitere Kontakte laden ({filteredFriends.length - visibleFriendsCount} verbleibend)</span>
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
              Blockierte Personen ({blockedList.length})
            </h3>
            {blockedList.length === 0 ? (
              <p className="text-xs text-on-surface-variant/70 py-6">
                Keine blockierten Kontakte vorhanden.
              </p>
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                {blockedList.map((b) => (
                  <div
                    key={b.userId}
                    className="flex items-center justify-between p-3 rounded-xl bg-surface-container-low border border-status-error/30"
                  >
                    <div className="flex items-center gap-3 min-w-0">
                      <Avatar src={b.avatarUrl} name={b.username} size="sm" />
                      <div className="min-w-0">
                        <span className="text-xs font-semibold text-primary truncate block">
                          {b.username}
                        </span>
                        <span className="text-[10px] text-status-error font-medium">
                          Blockiert
                        </span>
                      </div>
                    </div>

                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={async () => {
                        await unblockUser(b.userId)
                        toast.success(`Blockierung von ${b.username} aufgehoben`)
                      }}
                      className="h-7 text-xs px-2.5 border border-status-error/30 text-status-error hover:bg-status-error/15 shrink-0"
                    >
                      Entblocken
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
              Stummgeschaltete Unterhaltungen ({mutedList.length})
            </h3>
            {mutedList.length === 0 ? (
              <p className="text-xs text-on-surface-variant/70 py-6">
                Keine stummgeschalteten Chats vorhanden.
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
                        <span className="text-[10px] text-on-surface-variant/80 flex items-center gap-1">
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
                        toast.success('Stummschaltung aufgehoben')
                      }}
                      className="h-7 text-xs px-2.5 text-on-surface-variant hover:text-primary shrink-0"
                    >
                      Einschalten
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
          <h2 id="chat-privacy-title" className="font-headline text-lg font-semibold text-on-surface">
            Chat-Privatsphäre
          </h2>
        </div>
        <p className="max-w-2xl font-body-md text-sm leading-6 text-on-surface-variant mb-4">
          Steuere deine Privatsphäre im Messenger und bei Ende-zu-Ende verschlüsselten Konversationen.
        </p>

        <div className="flex items-center justify-between p-4 rounded-xl bg-surface-container-high/40 border border-outline-variant/30">
          <div className="space-y-0.5 max-w-md">
            <span className="text-xs font-bold text-on-surface">
              Lesebestätigungen (Gelesen-Häkchen ✓✓)
            </span>
            <p className="text-[11px] text-on-surface-variant">
              Wenn aktiviert, wird deinen Kontakten mit zwei blauen Häkchen signalisiert, sobald eine Nachricht gelesen wurde.
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

      {/* 3. Meilensteine & Fortschritt (Mit Lazy-Load) */}
      <section className="msm-card p-6" aria-labelledby="milestones-title">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-4">
          <div className="flex items-center gap-2">
            <Trophy className="h-5 w-5 text-secondary" aria-hidden="true" />
            <div>
              <h2 id="milestones-title" className="font-headline text-lg font-semibold text-on-surface">
                Meilensteine & Fortschritt
              </h2>
              <p className="font-body-md text-xs text-on-surface-variant mt-0.5">
                Dokumentiert deine Erfolge bei der Systemverwaltung und Aktivität.
              </p>
            </div>
          </div>

          <div className="flex items-center gap-3 self-start sm:self-auto">
            <div className="text-right">
              <span className="text-xs font-bold text-primary font-mono block">
                {overview?.total_unlocked || 0} / {overview?.total_available || 0}
              </span>
              <span className="text-[10px] text-on-surface-variant font-mono">
                {overview?.prestige_score || 0} Punkte
              </span>
            </div>
            <div className="w-24 h-2 bg-surface-container-high rounded-full overflow-hidden border border-outline-variant/30">
              <div
                className="h-full bg-primary transition-all duration-500 rounded-full"
                style={{ width: `${progressPercent}%` }}
              />
            </div>
          </div>
        </div>

        {/* Filter Buttons */}
        <div className="flex items-center gap-1.5 mb-4 pt-1 border-t border-outline-variant/20">
          <Button
            variant={milestoneFilter === 'all' ? 'primary' : 'ghost'}
            size="sm"
            onClick={() => {
              setMilestoneFilter('all')
              setVisibleMilestonesCount(12)
            }}
            className="text-xs h-7 px-3"
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
            className="text-xs h-7 px-3"
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
            className="text-xs h-7 px-3"
          >
            Gesperrt ({(overview?.total_available || 0) - (overview?.total_unlocked || 0)})
          </Button>
        </div>

        {/* Milestones Grid mit Lazy-Load */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {filteredMilestones.slice(0, visibleMilestonesCount).map((m) => {
            const rarity = m.rarity_percent ?? m.global_unlocked_percentage ?? 0
            const isRare = rarity > 0 && rarity <= 10

            return (
              <div
                key={m.id}
                className={`flex items-start gap-3.5 p-3.5 rounded-xl border transition-all ${
                  m.unlocked
                    ? 'bg-surface-container-low border-outline-variant/40 shadow-xs'
                    : 'bg-surface-container-lowest/40 border-outline-variant/20 opacity-55'
                }`}
              >
                <div
                  className={`w-10 h-10 rounded-xl flex items-center justify-center shrink-0 border ${
                    m.unlocked
                      ? isRare
                        ? 'bg-amber-500/20 border-amber-500/40 text-amber-300'
                        : 'bg-primary/15 border-primary/30 text-primary'
                      : 'bg-surface-container-high/50 border-outline-variant/20 text-on-surface-variant/40'
                  }`}
                >
                  {m.unlocked ? renderAchievementIcon(m.icon, 'w-5 h-5') : <Lock className="w-4 h-4" />}
                </div>

                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="font-headline text-xs font-bold text-primary truncate">
                      {m.title}
                    </span>
                    <span className="text-[10px] font-mono text-amber-400/90 font-semibold">
                      +{m.points} Pkt
                    </span>
                    {isRare && (
                      <Badge variant="warning" className="text-[9px] px-1 py-0 uppercase font-bold">
                        Selten
                      </Badge>
                    )}
                  </div>
                  <p className="font-body text-xs text-on-surface-variant mt-0.5 leading-relaxed">
                    {m.description}
                  </p>
                  <div className="flex items-center gap-3 mt-1.5 text-[10px] text-on-surface-variant/70 flex-wrap">
                    {m.rarity_text && <span>{m.rarity_text}</span>}
                    {m.unlocked && m.unlocked_at && (
                      <span className="inline-flex items-center gap-1 text-emerald-400">
                        <CheckCircle2 className="w-3 h-3" />
                        <span>Freigeschaltet am {new Date(m.unlocked_at).toLocaleDateString()}</span>
                      </span>
                    )}
                  </div>
                </div>
              </div>
            )
          })}
        </div>

        {/* Lazy-Load Button für Erfolge */}
        {filteredMilestones.length > visibleMilestonesCount && (
          <div className="pt-3 flex justify-center">
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
