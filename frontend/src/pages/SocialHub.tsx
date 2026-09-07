import React, { useState, useEffect } from 'react'
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
  Button,
  Input,
  Badge,
  Dropdown,
  type DropdownOption,
} from '@/Singra/UI'
import {
  Users,
  Trophy,
  ShieldCheck,
  Clock,
  UserPlus,
  MessageSquare,
  Check,
  X,
  UserMinus,
  Sparkles,
  Award,
  Flame,
  Radio,
  Eye,
  Lock,
} from 'lucide-react'
import { DeviceBadge } from '@/components/social/DeviceBadge'
import { StatusDot, StatusSwitcher, type PresenceStatus } from '@/components/social/StatusIndicator'
import { E2EEChatModal } from '@/components/social/E2EEChatModal'
import {
  type FriendItem,
  type AchievementsOverview,
  type UserStatsResponse,
  getFriends,
  sendFriendRequest,
  acceptFriendRequest,
  declineFriendRequest,
  removeFriend,
  updatePresence,
  getAchievements,
  getStats,
  updatePrivacy,
} from '@/api/social'
import { useAuthStore } from '@/stores/authStore'
import { toast } from '@/stores/toastStore'

export function SocialHub() {
  const { user } = useAuthStore()
  const [activeTab, setActiveTab] = useState<'overview' | 'friends' | 'achievements' | 'privacy'>('overview')

  // Friends state
  const [friends, setFriends] = useState<FriendItem[]>([])
  const [addUsername, setAddUsername] = useState('')
  const [loadingAction, setLoadingAction] = useState(false)
  const [myStatus, setMyStatus] = useState<PresenceStatus>('online')

  // Chat modal state
  const [chatFriend, setChatFriend] = useState<FriendItem | null>(null)
  const [isChatOpen, setIsChatOpen] = useState(false)

  // Achievements state
  const [overview, setOverview] = useState<AchievementsOverview | null>(null)
  const [stats, setStats] = useState<UserStatsResponse | null>(null)
  const [achFilter, setAchFilter] = useState<'all' | 'unlocked' | 'locked'>('all')

  // Privacy state
  const [privacyLevel, setPrivacyLevel] = useState<'private' | 'friends' | 'public'>('friends')
  const [savingPrivacy, setSavingPrivacy] = useState(false)

  const loadData = async () => {
    try {
      const [fData, achData, stData] = await Promise.all([
        getFriends(),
        getAchievements(),
        getStats(),
      ])
      setFriends(fData)
      setOverview(achData)
      setStats(stData)
    } catch {
      // Non-blocking
    }
  }

  useEffect(() => {
    loadData()
    const interval = setInterval(loadData, 10000)
    return () => clearInterval(interval)
  }, [])

  const handleStatusChange = async (newStatus: PresenceStatus) => {
    setMyStatus(newStatus)
    try {
      await updatePresence({
        status: newStatus,
        device_type: 'web',
      })
      toast.success(`Status auf "${newStatus}" gesetzt`)
    } catch {
      // Ignore
    }
  }

  const handlePrivacyChange = async (newPrivacy: 'private' | 'friends' | 'public') => {
    setPrivacyLevel(newPrivacy)
    setSavingPrivacy(true)
    try {
      await updatePrivacy({ social_privacy: newPrivacy })
      toast.success('Privatsphäre-Einstellung aktualisiert')
    } catch {
      toast.error('Fehler beim Speichern der Privatsphäre')
    } finally {
      setSavingPrivacy(false)
    }
  }

  const handleSendFriendRequest = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    if (!addUsername.trim()) return
    setLoadingAction(true)
    try {
      const res = await sendFriendRequest(addUsername.trim())
      toast.success(res.message || 'Anfrage gesendet')
      setAddUsername('')
      await loadData()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Fehler beim Senden'
      toast.error(msg)
    } finally {
      setLoadingAction(false)
    }
  }

  const handleAcceptRequest = async (id: number) => {
    try {
      await acceptFriendRequest(id)
      toast.success('Freundschaftsanfrage angenommen')
      await loadData()
    } catch {
      toast.error('Fehler beim Annehmen')
    }
  }

  const handleDeclineRequest = async (id: number) => {
    try {
      await declineFriendRequest(id)
      toast.success('Freundschaftsanfrage abgelehnt')
      await loadData()
    } catch {
      toast.error('Fehler beim Ablehnen')
    }
  }

  const handleRemoveFriend = async (id: number) => {
    try {
      await removeFriend(id)
      toast.success('Freund entfernt')
      await loadData()
    } catch {
      toast.error('Fehler beim Entfernen')
    }
  }

  const formatHours = (seconds: number) => {
    const hrs = Math.floor(seconds / 3600)
    const mins = Math.floor((seconds % 3600) / 60)
    if (hrs === 0) return `${mins}m`
    return `${hrs}h ${mins}m`
  }

  const acceptedFriends = friends.filter((f) => f.status === 'accepted')
  const incomingRequests = friends.filter((f) => f.status === 'pending' && !f.is_requester)

  const filteredAchievements = (overview?.achievements || []).filter((item) => {
    if (achFilter === 'unlocked') return item.unlocked
    if (achFilter === 'locked') return !item.unlocked
    return true
  })

  const progressPercent = overview
    ? Math.round((overview.total_unlocked / Math.max(overview.total_available, 1)) * 100)
    : 0

  const privacyOptions: DropdownOption[] = [
    {
      value: 'private',
      label: 'Privat',
      hint: 'Profil und Errungenschaften sind nur für dich sichtbar',
    },
    {
      value: 'friends',
      label: 'Nur Freunde (Empfohlen)',
      hint: 'Nur bestätigte Freunde sehen Profil und Errungenschaften',
    },
    {
      value: 'public',
      label: 'Öffentlich',
      hint: 'Jeder im Netzwerk kann dein Profil und deine Erfolge sehen',
    },
  ]

  return (
    <div className="space-y-6 max-w-7xl mx-auto px-4 py-6">
      {/* Header Banner */}
      <div className="relative overflow-hidden rounded-2xl border border-outline-variant/30 bg-gradient-to-r from-surface-container-low via-surface-container to-surface-container-low p-6 sm:p-8 shadow-xl">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-6">
          <div className="flex items-center gap-4">
            <div className="p-4 rounded-2xl bg-primary/15 border border-primary/30 text-primary shadow-[0_0_20px_rgba(59,130,246,0.2)]">
              <Users className="w-8 h-8" />
            </div>
            <div>
              <div className="flex items-center gap-2.5 flex-wrap">
                <h1 className="font-headline text-title-lg sm:text-headline-sm font-black text-primary tracking-tight">
                  Social Hub & Community
                </h1>
                <Badge variant="success" className="text-[11px] gap-1 uppercase tracking-wider font-bold">
                  <ShieldCheck className="w-3.5 h-3.5" />
                  DIS E2EE Aktiv
                </Badge>
              </div>
              <p className="font-body text-xs sm:text-body-sm text-on-surface-variant mt-1 max-w-2xl leading-relaxed">
                Steam-Prestige Errungenschaften mit dynamischer Seltenheit, Rich Presence und kompromissloser
                Ende-zu-Ende-Verschlüsselung für Direktnachrichten.
              </p>
            </div>
          </div>

          {/* Quick status & prestige indicator */}
          <div className="flex items-center gap-3 self-start md:self-auto bg-surface-container-high/40 p-3 rounded-xl border border-outline-variant/20">
            <div>
              <div className="text-[11px] text-on-surface-variant font-medium">Mein Status</div>
              <StatusSwitcher currentStatus={myStatus} onChange={handleStatusChange} className="w-36 mt-1" />
            </div>
            <div className="border-l border-outline-variant/30 pl-3">
              <div className="text-[11px] text-on-surface-variant font-medium">Prestige</div>
              <div className="text-sm font-extrabold text-amber-400 font-mono mt-1">
                {overview?.prestige_score || 0} Pkt
              </div>
            </div>
          </div>
        </div>

        {/* Navigation Tabs */}
        <div className="mt-8 pt-4 border-t border-outline-variant/20 flex items-center gap-2 overflow-x-auto">
          <Button
            variant={activeTab === 'overview' ? 'primary' : 'ghost'}
            size="sm"
            onClick={() => setActiveTab('overview')}
            className="text-xs h-8"
          >
            Übersicht
          </Button>
          <Button
            variant={activeTab === 'friends' ? 'primary' : 'ghost'}
            size="sm"
            onClick={() => setActiveTab('friends')}
            className="text-xs h-8 relative"
          >
            Freundesliste ({acceptedFriends.length})
            {incomingRequests.length > 0 && (
              <span className="ml-1.5 px-1.5 py-0.2 rounded-full bg-amber-500 text-[10px] text-white font-bold">
                {incomingRequests.length}
              </span>
            )}
          </Button>
          <Button
            variant={activeTab === 'achievements' ? 'primary' : 'ghost'}
            size="sm"
            onClick={() => setActiveTab('achievements')}
            className="text-xs h-8"
          >
            Errungenschaften ({overview?.total_unlocked || 0}/{overview?.total_available || 0})
          </Button>
          <Button
            variant={activeTab === 'privacy' ? 'primary' : 'ghost'}
            size="sm"
            onClick={() => setActiveTab('privacy')}
            className="text-xs h-8"
          >
            Privatsphäre & Sicherheit
          </Button>
        </div>
      </div>

      {/* Tab 1: Overview */}
      {activeTab === 'overview' && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          {/* Card: Active Usage / Spielzeit */}
          <Card className="border border-outline-variant/30 bg-surface-container-low shadow-md">
            <CardHeader className="p-4 border-b border-outline-variant/20">
              <div className="flex items-center gap-2">
                <Clock className="w-4 h-4 text-primary" />
                <CardTitle className="font-headline text-body-md font-bold text-primary">
                  Aktive Spiel- & Nutzungszeit
                </CardTitle>
              </div>
            </CardHeader>
            <CardContent className="p-4 space-y-3">
              <div className="text-center py-2">
                <div className="text-3xl font-black text-primary font-mono">
                  {formatHours(stats?.total_activity_seconds || 0)}
                </div>
                <div className="text-xs text-on-surface-variant mt-1">Interaktive Gesamtzeit</div>
              </div>

              <div className="space-y-2 pt-2 border-t border-outline-variant/20">
                <div className="flex items-center justify-between text-xs">
                  <span className="flex items-center gap-1.5 text-on-surface-variant">
                    <Sparkles className="w-3.5 h-3.5 text-cyan-400" />
                    KI-Dialoge
                  </span>
                  <span className="font-mono font-bold text-primary">
                    {formatHours(stats?.categories?.ai_chat || 0)}
                  </span>
                </div>
                <div className="flex items-center justify-between text-xs">
                  <span className="flex items-center gap-1.5 text-on-surface-variant">
                    <Award className="w-3.5 h-3.5 text-amber-400" />
                    Server-Administration
                  </span>
                  <span className="font-mono font-bold text-primary">
                    {formatHours(stats?.categories?.server_admin || 0)}
                  </span>
                </div>
                <div className="flex items-center justify-between text-xs">
                  <span className="flex items-center gap-1.5 text-on-surface-variant">
                    <Flame className="w-3.5 h-3.5 text-rose-400" />
                    Befehlsausführungen
                  </span>
                  <span className="font-mono font-bold text-primary">
                    {formatHours(stats?.categories?.command_exec || 0)}
                  </span>
                </div>
              </div>
            </CardContent>
          </Card>

          {/* Card: Achievement Progress */}
          <Card className="border border-outline-variant/30 bg-surface-container-low shadow-md">
            <CardHeader className="p-4 border-b border-outline-variant/20 flex flex-row items-center justify-between">
              <div className="flex items-center gap-2">
                <Trophy className="w-4 h-4 text-amber-400" />
                <CardTitle className="font-headline text-body-md font-bold text-primary">
                  Steam-Prestige Status
                </CardTitle>
              </div>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setActiveTab('achievements')}
                className="text-xs h-7 px-2 text-primary"
              >
                Alle ansehen →
              </Button>
            </CardHeader>
            <CardContent className="p-4 space-y-4">
              <div className="flex items-center justify-between">
                <div>
                  <div className="text-2xl font-black text-amber-400 font-mono">
                    {overview?.prestige_score || 0}
                  </div>
                  <div className="text-xs text-on-surface-variant">Prestige Punkte</div>
                </div>
                <div className="text-right">
                  <div className="text-2xl font-black text-primary font-mono">
                    {overview?.total_unlocked || 0} / {overview?.total_available || 0}
                  </div>
                  <div className="text-xs text-on-surface-variant">{progressPercent}% abgeschlossen</div>
                </div>
              </div>

              <div className="w-full h-2.5 bg-surface-container-high rounded-full overflow-hidden border border-outline-variant/30">
                <div
                  className="h-full bg-gradient-to-r from-amber-500 to-amber-300 rounded-full"
                  style={{ width: `${progressPercent}%` }}
                />
              </div>

              <div className="space-y-1.5 pt-2">
                <div className="text-xs font-bold text-primary">Kürzlich freigeschaltet:</div>
                {(overview?.achievements || [])
                  .filter((a) => a.unlocked)
                  .slice(0, 2)
                  .map((a) => (
                    <div
                      key={a.id}
                      className="p-2 rounded-lg bg-surface-container-high/40 border border-outline-variant/20 flex items-center gap-2.5"
                    >
                      <span className="text-lg">{a.icon || '🏆'}</span>
                      <div className="min-w-0 flex-1">
                        <div className="text-xs font-bold text-primary truncate">{a.title}</div>
                        <div className="text-[10px] text-on-surface-variant truncate">{a.rarity_text}</div>
                      </div>
                    </div>
                  ))}
              </div>
            </CardContent>
          </Card>

          {/* Card: Online Friends */}
          <Card className="border border-outline-variant/30 bg-surface-container-low shadow-md">
            <CardHeader className="p-4 border-b border-outline-variant/20 flex flex-row items-center justify-between">
              <div className="flex items-center gap-2">
                <Users className="w-4 h-4 text-primary" />
                <CardTitle className="font-headline text-body-md font-bold text-primary">
                  Freunde Online
                </CardTitle>
              </div>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setActiveTab('friends')}
                className="text-xs h-7 px-2 text-primary"
              >
                Verwalten →
              </Button>
            </CardHeader>
            <CardContent className="p-4 space-y-2">
              {acceptedFriends.length === 0 ? (
                <div className="py-8 text-center text-xs text-on-surface-variant/70">
                  Keine Freunde verbunden.
                </div>
              ) : (
                acceptedFriends.slice(0, 4).map((f) => (
                  <div
                    key={f.id}
                    className="flex items-center justify-between p-2 rounded-lg hover:bg-surface-container-high/50 transition-colors"
                  >
                    <div className="flex items-center gap-2 min-w-0">
                      <div className="relative shrink-0">
                        <div className="w-7 h-7 rounded-full bg-primary/15 flex items-center justify-center text-xs font-bold text-primary">
                          {f.username.slice(0, 2).toUpperCase()}
                        </div>
                        <StatusDot
                          status={f.presence?.status || 'invisible'}
                          size="sm"
                          className="absolute bottom-0 right-0"
                        />
                      </div>
                      <div className="min-w-0">
                        <div className="text-xs font-semibold text-primary truncate">{f.username}</div>
                        <DeviceBadge deviceType={f.presence?.device_type} />
                      </div>
                    </div>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => {
                        setChatFriend(f)
                        setIsChatOpen(true)
                      }}
                      className="h-7 w-7 p-0 text-primary"
                      title="E2EE Chat"
                    >
                      <MessageSquare className="w-3.5 h-3.5" />
                    </Button>
                  </div>
                ))
              )}
            </CardContent>
          </Card>
        </div>
      )}

      {/* Tab 2: Friends */}
      {activeTab === 'friends' && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          <div className="md:col-span-2 space-y-4">
            <Card className="border border-outline-variant/30 bg-surface-container-low">
              <CardHeader className="p-4 border-b border-outline-variant/20 flex flex-row items-center justify-between">
                <CardTitle className="font-headline text-body-md font-bold text-primary">
                  Meine Freunde ({acceptedFriends.length})
                </CardTitle>
                <Badge variant="default" className="text-xs">
                  {acceptedFriends.filter((f) => f.presence?.status === 'online').length} Online
                </Badge>
              </CardHeader>
              <CardContent className="p-4 space-y-2">
                {acceptedFriends.length === 0 ? (
                  <p className="py-12 text-center text-xs text-on-surface-variant/70">
                    Noch keine Freunde in der Liste. Füge andere Nutzer über das Formular rechts hinzu.
                  </p>
                ) : (
                  acceptedFriends.map((f) => (
                    <div
                      key={f.id}
                      className="flex items-center justify-between p-3 rounded-xl border border-outline-variant/20 bg-surface-container/60 hover:bg-surface-container transition-colors"
                    >
                      <div className="flex items-center gap-3 min-w-0">
                        <div className="relative shrink-0">
                          <div className="w-10 h-10 rounded-full bg-primary/15 flex items-center justify-center font-bold text-primary">
                            {f.username.slice(0, 2).toUpperCase()}
                          </div>
                          <StatusDot
                            status={f.presence?.status || 'invisible'}
                            size="md"
                            className="absolute bottom-0 right-0"
                          />
                        </div>
                        <div className="min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="font-headline text-body-sm font-bold text-primary truncate">
                              {f.username}
                            </span>
                            <DeviceBadge deviceType={f.presence?.device_type} showLabel />
                          </div>
                          {f.presence?.activity_label && (
                            <p className="text-xs text-on-surface-variant/80 mt-0.5 flex items-center gap-1.5">
                              <Radio className="w-3 h-3 text-emerald-400 animate-pulse" />
                              <span>{f.presence.activity_label}</span>
                            </p>
                          )}
                        </div>
                      </div>

                      <div className="flex items-center gap-2">
                        <Button
                          variant="primary"
                          size="sm"
                          onClick={() => {
                            setChatFriend(f)
                            setIsChatOpen(true)
                          }}
                          className="gap-1.5 text-xs"
                        >
                          <MessageSquare className="w-3.5 h-3.5" />
                          <span>E2EE Chat</span>
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => void handleRemoveFriend(f.user_id ?? f.id)}
                          className="text-on-surface-variant hover:text-rose-400 hover:bg-rose-500/10 h-8 w-8 p-0"
                          title="Freund entfernen"
                        >
                          <UserMinus className="w-4 h-4" />
                        </Button>
                      </div>
                    </div>
                  ))
                )}
              </CardContent>
            </Card>

            {/* Incoming Requests */}
            {incomingRequests.length > 0 && (
              <Card className="border border-amber-500/30 bg-surface-container-low">
                <CardHeader className="p-4 border-b border-outline-variant/20">
                  <CardTitle className="font-headline text-body-md font-bold text-primary flex items-center gap-2">
                    <UserPlus className="w-4 h-4 text-amber-400" />
                    Ausstehende Freundschaftsanfragen ({incomingRequests.length})
                  </CardTitle>
                </CardHeader>
                <CardContent className="p-4 space-y-2">
                  {incomingRequests.map((req) => (
                    <div
                      key={req.id}
                      className="flex items-center justify-between p-3 rounded-lg bg-surface-container border border-outline-variant/20"
                    >
                      <span className="font-semibold text-xs text-primary">{req.username}</span>
                      <div className="flex items-center gap-2">
                        <Button
                          variant="primary"
                          size="sm"
                          onClick={() => void handleAcceptRequest(req.id)}
                          className="h-7 px-3 text-xs gap-1"
                        >
                          <Check className="w-3.5 h-3.5" />
                          <span>Annehmen</span>
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => void handleDeclineRequest(req.id)}
                          className="h-7 px-3 text-xs text-rose-400 hover:bg-rose-500/10 gap-1"
                        >
                          <X className="w-3.5 h-3.5" />
                          <span>Ablehnen</span>
                        </Button>
                      </div>
                    </div>
                  ))}
                </CardContent>
              </Card>
            )}
          </div>

          {/* Add Friend Form */}
          <div>
            <Card className="border border-outline-variant/30 bg-surface-container-low shadow-md">
              <CardHeader className="p-4 border-b border-outline-variant/20">
                <CardTitle className="font-headline text-body-md font-bold text-primary flex items-center gap-2">
                  <UserPlus className="w-4 h-4 text-primary" />
                  Freund hinzufügen
                </CardTitle>
                <CardDescription className="text-xs text-on-surface-variant">
                  Geben Sie den Benutzernamen eines anderen Panel- oder Desktop-Nutzers ein.
                </CardDescription>
              </CardHeader>
              <CardContent className="p-4">
                <form onSubmit={handleSendFriendRequest} className="space-y-3">
                  <div>
                    <label className="block text-xs font-semibold text-primary mb-1">
                      Benutzername
                    </label>
                    <Input
                      value={addUsername}
                      onChange={(e: React.ChangeEvent<HTMLInputElement>) => setAddUsername(e.target.value)}
                      placeholder="z. B. alexander"
                      disabled={loadingAction}
                    />
                  </div>
                  <Button
                    type="submit"
                    disabled={!addUsername.trim() || loadingAction}
                    className="w-full gap-1.5"
                  >
                    <UserPlus className="w-4 h-4" />
                    <span>Anfrage abschicken</span>
                  </Button>
                </form>
              </CardContent>
            </Card>
          </div>
        </div>
      )}

      {/* Tab 3: Achievements */}
      {activeTab === 'achievements' && (
        <div className="space-y-4">
          <div className="flex items-center justify-between flex-wrap gap-3">
            <div className="flex items-center gap-2">
              <Button
                variant={achFilter === 'all' ? 'primary' : 'ghost'}
                size="sm"
                onClick={() => setAchFilter('all')}
                className="text-xs h-7"
              >
                Alle ({overview?.achievements.length || 0})
              </Button>
              <Button
                variant={achFilter === 'unlocked' ? 'primary' : 'ghost'}
                size="sm"
                onClick={() => setAchFilter('unlocked')}
                className="text-xs h-7"
              >
                Freigeschaltet ({overview?.total_unlocked || 0})
              </Button>
              <Button
                variant={achFilter === 'locked' ? 'primary' : 'ghost'}
                size="sm"
                onClick={() => setAchFilter('locked')}
                className="text-xs h-7"
              >
                Gesperrt ({(overview?.total_available || 0) - (overview?.total_unlocked || 0)})
              </Button>
            </div>

            <div className="text-xs text-on-surface-variant font-mono">
              Fortschritt: <strong className="text-primary">{overview?.total_unlocked || 0}</strong> von{' '}
              {overview?.total_available || 0}
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {filteredAchievements.map((a) => {
              const rarity = a.rarity_percent ?? a.global_unlocked_percentage ?? 0
              const isRare = rarity > 0 && rarity <= 10
              return (
                <div
                  key={a.id}
                  className={`flex items-start gap-4 p-4 rounded-xl border transition-all ${
                    a.unlocked
                      ? 'bg-surface-container-low border-outline-variant/40 shadow-sm'
                      : 'bg-surface-container-lowest/50 border-outline-variant/20 opacity-50'
                  }`}
                >
                  <div
                    className={`w-12 h-12 rounded-xl flex items-center justify-center text-2xl shrink-0 border ${
                      a.unlocked
                        ? isRare
                          ? 'bg-amber-500/20 border-amber-500/40 text-amber-300 shadow-[0_0_12px_rgba(245,158,11,0.25)]'
                          : 'bg-primary/15 border-primary/30 text-primary'
                        : 'bg-surface-container-high/50 border-outline-variant/20 text-on-surface-variant/40'
                    }`}
                  >
                    {a.unlocked ? a.icon || '🏆' : <Lock className="w-5 h-5" />}
                  </div>

                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <h4 className="font-headline text-body-sm font-bold text-primary truncate">
                        {a.title}
                      </h4>
                      <span className="text-xs font-mono text-amber-400 font-semibold">
                        +{a.points} Pkt
                      </span>
                      {isRare && (
                        <Badge variant="warning" className="text-[10px] py-0 px-1 font-bold">
                          💎 Selten
                        </Badge>
                      )}
                    </div>
                    <p className="font-body text-xs text-on-surface-variant mt-0.5 leading-relaxed">
                      {a.description}
                    </p>
                    <div className="flex items-center gap-3 mt-2 text-[11px] text-on-surface-variant/70">
                      <span className={`font-medium ${isRare ? 'text-amber-400' : ''}`}>
                        {a.rarity_text}
                      </span>
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Tab 4: Privacy */}
      {activeTab === 'privacy' && (
        <div className="max-w-2xl mx-auto">
          <Card className="border border-outline-variant/30 bg-surface-container-low shadow-md">
            <CardHeader className="p-6 border-b border-outline-variant/20">
              <div className="flex items-center gap-3">
                <div className="p-3 rounded-xl bg-primary/15 text-primary">
                  <Eye className="w-5 h-5" />
                </div>
                <div>
                  <CardTitle className="font-headline text-body-lg font-bold text-primary">
                    3-Stufen Privatsphäre-Einstellungen
                  </CardTitle>
                  <CardDescription className="text-xs text-on-surface-variant mt-0.5">
                    Bestimmen Sie granular, wer Ihre Errungenschaften, Spielzeit und Anwesenheit sehen darf.
                  </CardDescription>
                </div>
              </div>
            </CardHeader>
            <CardContent className="p-6 space-y-6">
              <div>
                <label className="block text-xs font-bold uppercase tracking-wider text-on-surface-variant mb-2">
                  Profil-Sichtbarkeit
                </label>
                <Dropdown
                  value={privacyLevel}
                  onChange={(val: string) => handlePrivacyChange(val as 'private' | 'friends' | 'public')}
                  options={privacyOptions}
                  disabled={savingPrivacy}
                />
              </div>

              <div className="p-4 rounded-xl bg-surface-container-high/30 border border-outline-variant/20 space-y-2">
                <div className="flex items-center gap-2 text-xs font-bold text-primary">
                  <ShieldCheck className="w-4 h-4 text-emerald-400" />
                  <span>Kryptographische Sicherheitsgarantie</span>
                </div>
                <p className="text-xs text-on-surface-variant/80 leading-relaxed">
                  Ihre Direktnachrichten werden auf Ihrem Endgerät per AES-256-GCM über die verifizierte
                  Kryptographie-Bibliothek <strong>@msdis/shield</strong> versiegelt. Der Server speichert keine
                  Benutzerverknüpfungen und leitet Nachrichten ausschließlich über anonyme, blinde Mailbox-Hashes weiter.
                </p>
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {/* E2EE Chat Modal */}
      {user && (
        <E2EEChatModal
          open={isChatOpen}
          onOpenChange={setIsChatOpen}
          currentUserId={user.id}
          friend={chatFriend}
        />
      )}
    </div>
  )
}
