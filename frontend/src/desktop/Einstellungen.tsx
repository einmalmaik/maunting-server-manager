/**
 * Die Einstellungen der Desktop-App — dieselbe Formensprache wie die
 * Panel-Einstellungen: eine Reiterleiste oben (`TabBar`, dieselbe Komponente
 * wie `/settings` und `/profile` im Panel), darunter Karten. Eigene Inhalte:
 * was dieser **Rechner** tut, nicht was das Panel tut.
 *
 * Vier Reiter: Desktop-Integration (Autostart, Hotkeys, Diagnose), Wake-Word
 * (Kalibrierung, Aktiv-Schalter), Audio (Geräteauswahl, Ducking) und die
 * Gefahrenzone. `?tab=wakeword` wählt einen Reiter vor — der Weg des
 * Neukalibrierungs-Hinweises nach einer Umbenennung.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { emit } from '@tauri-apps/api/event'
import { disable, enable, isEnabled } from '@tauri-apps/plugin-autostart'
import { open as ordnerDialog } from '@tauri-apps/plugin-dialog'
import {
  AlertTriangle,
  Camera,
  Check,
  CheckCircle2,
  Clock,
  ExternalLink,
  FileSignature,
  Globe,
  Lock,
  MapPin,
  Mic,
  MonitorCog,
  Phone,
  Radio,
  Save,
  ShieldAlert,
  ShieldCheck,
  Sliders,
  Trash2,
  Trophy,
  User,
  UserMinus,
  UserPlus,
  Users,
  Volume2,
  Ban,
  BellOff,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import { useMessengerNotificationStore } from '@/stores/messengerNotificationStore'

import {
  aktuelleVerarbeitung,
  ausgabeGeraetId,
  eingabeGeraetId,
  registriereAudioGeraete,
  registriereAudioVerarbeitung,
  type AudioVerarbeitung,
} from '@/components/ai/voice/audioGeraete'
import { api } from '@/api/client'
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
import { usePublicLegalSettings } from '@/hooks/usePublicLegalSettings'
import { TabBar, type TabDef } from '@/components/ui/TabBar'
import { MessengerSicherheitTab } from '@/pages/profile/MessengerSicherheitTab'
import { TresorSicherheitTab } from './vault/TresorSicherheitTab'
import { Avatar, Badge, Button, Dropdown, type DropdownOption, Input, ProgressBar, Slider, Switch } from '@/Singra/UI'
import { useAuthStore } from '@/stores/authStore'
import { toast } from '@/stores/toastStore'
import { getAvailableTimezones } from '@/utils/timeFormat'
import { Gefahrenzone } from './Gefahrenzone'
import { OVERLAY_ZUSTAND_TEST } from './sprachKoordination'
import { WakewordEinrichtung } from './WakewordEinrichtung'
import {
  audioGeraete,
  duckingSetzen,
  hotkeysSetzen,
  konfigLaden,
  konfigSpeichern,
  oeffneBrowser,
  overlayTesten,
  sandboxVerfuegbar,
  setzeStatus,
  updatePruefen,
  wakewordLauschen,
  type AgentStatus,
  type AppKonfig,
  type AudioGeraete,
} from './tauri'

const STATUS_REIHE: AgentStatus[] = ['bereit', 'hoert', 'denkt', 'spricht']

/**
 * Wie lange nach der letzten Verarbeitungsänderung gewartet wird, bevor sie
 * in konfig.json landet — der Verstärkungsregler feuert je Tick. Registriert
 * (und damit hörbar) ist jede Änderung sofort, nur das Schreiben wartet.
 */
const VERARBEITUNG_SPEICHERN_MS = 400

type EinstellungsTab = 'konto' | 'social' | 'messenger' | 'tresor' | 'desktop' | 'wakeword' | 'audio' | 'rechtliches' | 'gefahr'

const isAndroidClient = typeof navigator !== 'undefined' && /android/i.test(navigator.userAgent)

const TABS: TabDef<EinstellungsTab>[] = [
  { id: 'konto', labelKey: 'profile.tabs.account', icon: User },
  { id: 'social', labelKey: 'profile.tabs.social', icon: Users },
  // Derselbe Reiter wie im Web-Panel, dieselbe Komponente. Zwei Fassungen
  // waeren zwei Staende.
  { id: 'messenger', labelKey: 'profile.tabs.messenger', icon: Lock },
  { id: 'tresor', labelKey: 'profile.tabs.vault', icon: ShieldCheck },
  {
    id: 'desktop',
    labelKey: isAndroidClient ? 'mss.einstellungen.tab.app' : 'mss.einstellungen.tab.desktop',
    icon: MonitorCog,
  },
  { id: 'wakeword', labelKey: 'mss.einstellungen.tab.wakeword', icon: Mic },
  { id: 'audio', labelKey: 'mss.einstellungen.tab.audio', icon: Volume2 },
  { id: 'rechtliches', labelKey: 'mss.einstellungen.tab.rechtliches', icon: FileSignature },
  { id: 'gefahr', labelKey: 'mss.einstellungen.tab.gefahr', icon: AlertTriangle, variant: 'danger' },
]

function tabAusSuche(suche: string): EinstellungsTab {
  const wunsch = new URLSearchParams(suche).get('tab')
  if (wunsch === 'profil' || wunsch === 'account') return 'konto'
  return TABS.some((tab) => tab.id === wunsch) ? (wunsch as EinstellungsTab) : 'desktop'
}

export function Einstellungen({ onKonfigAenderung }: { onKonfigAenderung?: () => void }) {
  const { t } = useTranslation()
  const ort = useLocation()
  const [tab, setTab] = useState<EinstellungsTab>(() => tabAusSuche(ort.search))

  useEffect(() => {
    setTab(tabAusSuche(ort.search))
  }, [ort.search])

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6">
      <TabBar
        tabs={TABS}
        active={tab}
        onChange={setTab}
        ariaLabel={t('mss.app.einstellungen')}
      />
      {tab === 'konto' && <KontoEinstellungen />}
      {tab === 'social' && <SocialEinstellungen />}
      {tab === 'messenger' && <MessengerSicherheitTab />}
      {tab === 'tresor' && <TresorSicherheitTab />}
      {tab === 'desktop' && <DesktopIntegration onKonfigAenderung={onKonfigAenderung} />}
      {tab === 'wakeword' && <WakewordEinrichtung />}
      {tab === 'audio' && <AudioEinstellungen />}
      {tab === 'rechtliches' && <RechtlichesEinstellungen />}
      {tab === 'gefahr' && <Gefahrenzone />}
    </div>
  )
}

function KontoEinstellungen() {
  const { t } = useTranslation()
  const user = useAuthStore((s) => s.user)
  const updateUser = useAuthStore((s) => s.updateUser)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [uploadingAvatar, setUploadingAvatar] = useState(false)

  // Zeitzone State
  const browserZone = typeof Intl !== 'undefined' && typeof Intl.DateTimeFormat === 'function'
    ? Intl.DateTimeFormat().resolvedOptions().timeZone
    : null
  const [selectedZone, setSelectedZone] = useState<string>(
    user?.time_zone || browserZone || 'UTC',
  )
  const [savingZone, setSavingZone] = useState(false)
  const [dismissedBrowserHint, setDismissedBrowserHint] = useState(false)

  // Standort für KI State
  const [savingLocationSharing, setSavingLocationSharing] = useState(false)
  const [locationSharingError, setLocationSharingError] = useState<string | null>(null)

  useEffect(() => {
    if (user?.time_zone) {
      setSelectedZone(user.time_zone)
    } else if (browserZone) {
      setSelectedZone(browserZone)
    }
  }, [user?.time_zone, browserZone])

  const timezoneOptions: DropdownOption[] = useMemo(() => {
    const zones = getAvailableTimezones()
    const allZones = [...new Set([...(user?.time_zone ? [user.time_zone] : []), ...zones])].sort()
    return allZones.map((z) => ({ value: z, label: z }))
  }, [user?.time_zone])

  const showBrowserHint = !dismissedBrowserHint
    && browserZone
    && user?.time_zone
    && user.time_zone !== browserZone

  const handleSaveTimezone = async (zoneToSave?: string) => {
    const zone = zoneToSave || selectedZone
    setSavingZone(true)
    try {
      const res = await api<{ time_zone: string | null }>('/auth/me/timezone', {
        method: 'PATCH',
        body: JSON.stringify({ time_zone: zone }),
      })
      updateUser({ time_zone: res.time_zone })
      setSelectedZone(res.time_zone || 'UTC')
      setDismissedBrowserHint(true)
      toast.success(t('profile.timezoneSaved'))
    } catch {
      toast.error(t('profile.timezoneSaveFailed'))
    } finally {
      setSavingZone(false)
    }
  }

  const requestBrowserLocationPermission = () => new Promise<void>((resolve, reject) => {
    if (!navigator.geolocation) {
      reject(new Error('UNSUPPORTED'))
      return
    }
    navigator.geolocation.getCurrentPosition(
      () => resolve(),
      (error) => reject(error),
      { enableHighAccuracy: false, maximumAge: 0, timeout: 10_000 },
    )
  })

  const handleLocationSharingChange = async (enabled: boolean) => {
    setLocationSharingError(null)
    setSavingLocationSharing(true)
    try {
      if (enabled) {
        await requestBrowserLocationPermission()
      }
      const res = await api<{ location_sharing_enabled: boolean }>('/auth/me/location-sharing', {
        method: 'PATCH',
        body: JSON.stringify({ enabled }),
      })
      updateUser({ location_sharing_enabled: res.location_sharing_enabled })
    } catch (error) {
      const geolocationErrorCode = (error as { code?: unknown } | null)?.code
      if (
        (typeof geolocationErrorCode === 'number' && geolocationErrorCode >= 1 && geolocationErrorCode <= 3) ||
        (error as Error)?.message === 'UNSUPPORTED'
      ) {
        setLocationSharingError(
          t('profile.locationSharingPermissionError'),
        )
      } else {
        setLocationSharingError(t('profile.locationSharingSaveError'))
      }
    } finally {
      setSavingLocationSharing(false)
    }
  }

  const handleAvatarChange = async (file?: File | null) => {
    if (!file) return
    if (file.size > 5 * 1024 * 1024) {
      toast.error(t('profile.avatarSizeLimit'))
      return
    }
    const allowedTypes = ['image/jpeg', 'image/png', 'image/webp', 'image/gif']
    if (!allowedTypes.includes(file.type)) {
      toast.error(t('profile.avatarInvalidType'))
      return
    }

    setUploadingAvatar(true)
    try {
      const formData = new FormData()
      formData.append('file', file)
      const res = await api<{ avatar_url: string }>('/auth/me/avatar', {
        method: 'POST',
        body: formData,
      })
      updateUser({ avatar_url: res.avatar_url })
      toast.success(t('profile.avatarUpdated'))
    } catch (err: any) {
      toast.error(err?.detail || t('profile.avatarUpdateFailed'))
    } finally {
      setUploadingAvatar(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  const handleDeleteAvatar = async () => {
    if (!user?.avatar_url) return
    setUploadingAvatar(true)
    try {
      await api('/auth/me/avatar', { method: 'DELETE' })
      updateUser({ avatar_url: null })
      toast.success(t('profile.avatarRemoved'))
    } catch (err: any) {
      toast.error(err?.detail || t('profile.avatarRemoveFailed'))
    } finally {
      setUploadingAvatar(false)
    }
  }

  return (
    <div className="flex flex-col gap-6">
      {/* 1. Konto & Profilbild */}
      <div className="msm-card p-5 space-y-4">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary/10 text-primary">
            <User className="h-5 w-5" />
          </div>
          <div>
            <h2 className="text-sm font-semibold text-on-surface">{t('profile.tabs.account')}</h2>
          </div>
        </div>

        <div className="flex flex-col sm:flex-row items-start sm:items-center gap-4 pt-2 border-t border-outline-variant/30">
          <Avatar
            src={user?.avatar_url}
            name={user?.username}
            size="lg"
          />

          <div className="space-y-1.5 flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <span className="font-semibold text-sm text-on-surface">{user?.username}</span>
              {user?.is_owner && (
                <Badge variant="default">Owner</Badge>
              )}
            </div>
            <p className="text-xs text-on-surface-variant truncate">{user?.email || '—'}</p>

            <div className="flex flex-wrap items-center gap-2 pt-1">
              <input
                ref={fileInputRef}
                type="file"
                accept="image/jpeg,image/png,image/webp,image/gif"
                className="hidden"
                onChange={(e) => void handleAvatarChange(e.target.files?.[0])}
              />
              <Button
                size="sm"
                variant="secondary"
                disabled={uploadingAvatar}
                onClick={() => fileInputRef.current?.click()}
              >
                <Camera className="h-3.5 w-3.5 mr-1.5" />
                {user?.avatar_url ? t('profile.changeAvatar') : t('profile.uploadAvatar')}
              </Button>

              {user?.avatar_url && (
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={uploadingAvatar}
                  onClick={() => void handleDeleteAvatar()}
                  className="text-status-destructive hover:bg-status-destructive/10"
                >
                  <Trash2 className="h-3.5 w-3.5 mr-1.5" />
                  {t('profile.removeAvatar')}
                </Button>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* 2. Zeitzone */}
      <div className="msm-card p-5 space-y-4">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary/10 text-primary">
            <Clock className="h-5 w-5" />
          </div>
          <div>
            <h2 className="text-sm font-semibold text-on-surface">{t('profile.timezoneTitle')}</h2>
          </div>
        </div>

        {showBrowserHint && browserZone && (
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-primary/30 bg-primary/10 p-3 text-xs text-on-surface">
            <div className="flex items-center gap-2">
              <Globe className="h-4 w-4 text-primary shrink-0" aria-hidden="true" />
              <span>
                {t('profile.timezoneBrowserHint', {
                  zone: browserZone,
                  current: user?.time_zone,
                })}
              </span>
            </div>
            <div className="flex items-center gap-2">
              <Button
                type="button"
                variant="primary"
                size="sm"
                disabled={savingZone}
                onClick={() => void handleSaveTimezone(browserZone)}
              >
                {t('common.apply')}
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => setDismissedBrowserHint(true)}
              >
                {t('profile.timezoneDismiss')}
              </Button>
            </div>
          </div>
        )}

        <div className="space-y-3 pt-2 border-t border-outline-variant/30 max-w-md">
          <Dropdown
            id="desktop-timezone"
            value={selectedZone}
            onChange={setSelectedZone}
            options={timezoneOptions}
            searchable={true}
            searchPlaceholder={t('profile.timezoneSearch')}
            placeholder={t('profile.timezonePlaceholder')}
            aria-label={t('profile.timezoneLabel')}
          />
          <Button
            type="button"
            variant="primary"
            size="sm"
            disabled={savingZone || (selectedZone === user?.time_zone && Boolean(user?.time_zone))}
            onClick={() => void handleSaveTimezone()}
          >
            <Save className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
            {savingZone ? t('common.saving') : t('profile.timezoneSave')}
          </Button>
        </div>
      </div>

      {/* 3. Standort für KI-Anfragen */}
      <div className="msm-card p-5 space-y-4">
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className={`flex h-9 w-9 items-center justify-center rounded-xl border ${
              user?.location_sharing_enabled
                ? 'border-primary/30 bg-primary/10 text-primary'
                : 'border-outline-variant bg-surface-container text-on-surface-variant'
            }`}>
              <MapPin className="h-5 w-5" />
            </div>
            <div>
              <h2 className="text-sm font-semibold text-on-surface">
                {t('profile.locationSharingTitle')}
              </h2>
              <p className="text-xs text-on-surface-variant">
                {t('profile.locationSharingDescription')}
              </p>
            </div>
          </div>

          <Switch
            checked={Boolean(user?.location_sharing_enabled)}
            disabled={savingLocationSharing}
            onCheckedChange={(checked) => void handleLocationSharingChange(checked)}
            aria-label={t('profile.locationSharingTitle')}
          />
        </div>

        {locationSharingError && (
          <div className="rounded-xl border border-status-destructive/30 bg-status-destructive/10 p-3 text-xs text-status-destructive flex items-center gap-2">
            <AlertTriangle className="h-4 w-4 shrink-0" />
            <span>{locationSharingError}</span>
          </div>
        )}
      </div>

    </div>
  )
}

function SocialEinstellungen() {
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

function DesktopIntegration({ onKonfigAenderung }: { onKonfigAenderung?: () => void }) {
  const { t } = useTranslation()
  const isAndroid = typeof navigator !== 'undefined' && /android/i.test(navigator.userAgent)
  const [autostart, setAutostart] = useState<boolean | null>(null)
  const [status, setStatus] = useState<AgentStatus>('bereit')
  const [prueftUpdate, setPrueftUpdate] = useState(false)

  useEffect(() => {
    if (isAndroid) {
      void konfigLaden()
        .then((cfg) => {
          setAutostart(cfg.autostart_aktiv ?? true)
        })
        .catch(() => setAutostart(true))
    } else {
      void isEnabled()
        .then(setAutostart)
        .catch(() => setAutostart(null))
    }
  }, [isAndroid])

  async function autostartUmschalten(an: boolean) {
    try {
      if (isAndroid) {
        const akt = await konfigLaden().catch(() => null)
        if (akt) {
          await konfigSpeichern({ ...akt, autostart_aktiv: an })
        }
        setAutostart(an)
        onKonfigAenderung?.()
        toast.success(
          an
            ? 'Hintergrundüberwachung bei Handystart aktiviert'
            : 'Hintergrundüberwachung bei Handystart deaktiviert'
        )
      } else {
        if (an) {
          await enable()
        } else {
          await disable()
        }
        const akt = await konfigLaden().catch(() => null)
        if (akt) {
          await konfigSpeichern({ ...akt, autostart_aktiv: an })
        }
        setAutostart(an)
        onKonfigAenderung?.()
      }
    } catch {
      toast.error(t('mss.einstellungen.autostartFehler'))
    }
  }

  async function statusWechseln(neu: AgentStatus) {
    setStatus(neu)
    await setzeStatus(neu).catch(() => {})
    // Das Schaufenster-Ereignis kommt von hier und nur von hier — nicht aus
    // `setze_status` in Rust: den Befehl ruft auch die Zustandsverdrahtung
    // echter Sitzungen, und das Schaufenster folgte dann der fremden
    // Sitzung statt der geklickten Diagnose-Form.
    await emit(OVERLAY_ZUSTAND_TEST, neu).catch(() => {})
  }

  return (
    <section className="msm-card flex flex-col gap-4 p-5">
      <h2 className="text-sm font-medium text-on-surface">
        {isAndroid ? t('mss.einstellungen.tab.app') : t('mss.einstellungen.desktopIntegration')}
      </h2>

      <div className="flex items-center justify-between gap-3">
        <p className="text-sm text-on-surface">
          {isAndroid ? 'Beim Handystart ausführen' : t('mss.einstellungen.autostart')}
        </p>
        <Switch
          checked={autostart === true}
          disabled={autostart === null}
          onCheckedChange={(an) => void autostartUmschalten(an)}
          aria-label={isAndroid ? 'Beim Handystart ausführen' : t('mss.einstellungen.autostart')}
        />
      </div>

      {!isAndroid && (
        <>
          <ArtefaktInstallationSektion onKonfigAenderung={onKonfigAenderung} />

          <Hotkeys />

          <Systembereich />
        </>
      )}

      <ComputerUseSektion onKonfigAenderung={onKonfigAenderung} />

      <div className={isAndroid ? '' : 'border-t border-outline-variant/40 pt-4'}>
        <p className="mb-3 text-sm text-on-surface">{t('mss.einstellungen.diagnose')}</p>
        <div className="flex flex-wrap gap-2">
          {STATUS_REIHE.map((s) => (
            <button
              key={s}
              onClick={() => void statusWechseln(s)}
              className={`rounded-lg border px-3.5 py-2 text-sm transition-colors ${
                status === s
                  ? 'border-primary/40 bg-primary/10 text-primary'
                  : 'border-outline-variant/40 bg-surface-container-low/40 text-on-surface-variant hover:text-on-surface'
              }`}
            >
              {t(`mss.einstellungen.status.${s}`)}
            </button>
          ))}
        </div>
        <div className="mt-3">
          <Button variant="secondary" onClick={() => void overlayTesten().catch(() => {})}>
            {t('mss.einstellungen.overlayTesten')}
          </Button>
        </div>
      </div>

      <div className="border-t border-outline-variant/40 pt-4">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div className="min-w-0">
            <p className="text-sm font-medium text-on-surface">System-Updates</p>
          </div>
          <Button
            variant="secondary"
            size="sm"
            disabled={prueftUpdate}
            onClick={() => {
              void (async () => {
                setPrueftUpdate(true)
                try {
                  const res = await updatePruefen()
                  if (res.verfuegbar) {
                    toast.success(t('mss.einstellungen.updateAvailableVersion', { version: res.neue_version }))
                  } else {
                    toast.success(t('mss.einstellungen.updateUpToDate'))
                  }
                } catch {
                  toast.error(t('mss.einstellungen.updateCheckError'))
                } finally {
                  setPrueftUpdate(false)
                }
              })()
            }}
          >
            {prueftUpdate ? t('mss.einstellungen.checkingUpdates') : t('mss.einstellungen.checkForUpdates')}
          </Button>
        </div>
      </div>
    </section>
  )
}

function ComputerUseSektion({ onKonfigAenderung }: { onKonfigAenderung?: () => void }) {
  const { t } = useTranslation()
  const [konfig, setKonfig] = useState<AppKonfig | null>(null)
  const [dialogOffen, setDialogOffen] = useState(false)

  useEffect(() => {
    void konfigLaden().then(setKonfig).catch(() => {})
  }, [])

  async function toggle(an: boolean) {
    if (!konfig) return
    if (an) {
      setDialogOffen(true)
    } else {
      const neu = { ...konfig, computer_use_aktiv: false }
      setKonfig(neu)
      await konfigSpeichern(neu).catch(() => {})
      onKonfigAenderung?.()
    }
  }

  async function bestaetigenAktivieren() {
    if (!konfig) return
    const neu = { ...konfig, computer_use_aktiv: true }
    setKonfig(neu)
    setDialogOffen(false)
    await konfigSpeichern(neu).catch(() => {})
    onKonfigAenderung?.()
  }

  const isAndroid = typeof navigator !== 'undefined' && /android/i.test(navigator.userAgent)

  return (
    <>
      <div className="flex items-center justify-between gap-3 border-t border-outline-variant/40 pt-4">
        <div>
          <div className="flex items-center gap-2">
            <p className="text-sm text-on-surface">{t('mss.einstellungen.computerUse.titel')}</p>
            {isAndroid ? (
              <Badge variant="default">
                {t('mss.einstellungen.computerUse.statusNichtVerfuegbar')}
              </Badge>
            ) : konfig?.computer_use_aktiv ? (
              <Badge variant="success">
                {t('mss.einstellungen.computerUse.statusAktiv')}
              </Badge>
            ) : (
              <Badge variant="default">
                {t('mss.einstellungen.computerUse.statusDeaktiviert')}
              </Badge>
            )}
          </div>
          {isAndroid && (
            <p className="text-xs text-on-surface-variant">
              {t('mss.einstellungen.computerUse.androidHinweis')}
            </p>
          )}
        </div>
        <Switch
          checked={!isAndroid && konfig?.computer_use_aktiv === true}
          disabled={isAndroid || konfig === null}
          onCheckedChange={(an) => void toggle(an)}
          aria-label={t('mss.einstellungen.computerUse.titel')}
        />
      </div>

      {dialogOffen && (
        <div
          className="msm-modal-overlay"
          role="dialog"
          aria-modal="true"
          aria-label={t('mss.einstellungen.computerUse.aktivierenTitel')}
        >
          <div className="msm-card flex w-full max-w-md flex-col gap-4 p-5">
            <div className="flex items-center gap-2 text-status-warning">
              <AlertTriangle className="h-5 w-5 shrink-0" aria-hidden="true" />
              <h2 className="text-base font-semibold text-on-surface">
                {t('mss.einstellungen.computerUse.aktivierenTitel')}
              </h2>
            </div>
            <p className="text-xs leading-relaxed text-on-surface-variant">
              {t('mss.einstellungen.computerUse.aktivierenWarnung')}
            </p>
            <div className="flex items-center justify-end gap-2 pt-2">
              <Button variant="ghost" size="sm" onClick={() => setDialogOffen(false)}>
                {t('mss.einstellungen.computerUse.abbrechen')}
              </Button>
              <Button autoFocus size="sm" onClick={() => void bestaetigenAktivieren()}>
                {t('mss.einstellungen.computerUse.aktivierenBestaetigen')}
              </Button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}

function ArtefaktInstallationSektion({ onKonfigAenderung }: { onKonfigAenderung?: () => void }) {
  const { t } = useTranslation()
  const [konfig, setKonfig] = useState<AppKonfig | null>(null)
  const [dialogOffen, setDialogOffen] = useState(false)
  const [sandboxOk, setSandboxOk] = useState<boolean | null>(null)

  useEffect(() => {
    void konfigLaden().then(setKonfig).catch(() => {})
    void sandboxVerfuegbar().then(setSandboxOk).catch(() => setSandboxOk(false))
  }, [])

  async function toggle(an: boolean) {
    if (!konfig) return
    if (an) {
      setDialogOffen(true)
    } else {
      const neu = { ...konfig, artifact_install_aktiv: false }
      setKonfig(neu)
      await konfigSpeichern(neu).catch(() => {})
      onKonfigAenderung?.()
    }
  }

  async function bestaetigenAktivieren() {
    if (!konfig) return
    const neu = { ...konfig, artifact_install_aktiv: true }
    setKonfig(neu)
    setDialogOffen(false)
    await konfigSpeichern(neu).catch(() => {})
    onKonfigAenderung?.()
  }

  async function downloadLimitAendern(gib: number) {
    if (!konfig) return
    const bytes = Math.max(1, Math.min(100, gib)) * 1024 * 1024 * 1024
    const neu = { ...konfig, max_download_bytes: bytes }
    setKonfig(neu)
    await konfigSpeichern(neu).catch(() => {})
    onKonfigAenderung?.()
  }

  async function suchwurzelHinzufuegen() {
    if (!konfig) return
    try {
      const gewaehlt = await ordnerDialog({ directory: true, multiple: false })
      if (typeof gewaehlt === 'string' && gewaehlt && !konfig.search_roots.includes(gewaehlt)) {
        const neu = { ...konfig, search_roots: [...konfig.search_roots, gewaehlt] }
        setKonfig(neu)
        await konfigSpeichern(neu).catch(() => {})
        onKonfigAenderung?.()
      }
    } catch {
      toast.error(t('mss.einstellungen.artefakte.ordnerFehler'))
    }
  }

  async function suchwurzelEntfernen(pfad: string) {
    if (!konfig) return
    const neu = { ...konfig, search_roots: konfig.search_roots.filter((w) => w !== pfad) }
    setKonfig(neu)
    await konfigSpeichern(neu).catch(() => {})
    onKonfigAenderung?.()
  }

  const limitGiB = Math.round((konfig?.max_download_bytes ?? 10 * 1024 * 1024 * 1024) / (1024 * 1024 * 1024))

  return (
    <>
      <div className="flex flex-col gap-3 border-t border-outline-variant/40 pt-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <p className="text-sm text-on-surface">{t('mss.einstellungen.artefakte.titel')}</p>
              {konfig?.artifact_install_aktiv ? (
                <Badge variant="success">
                  {t('mss.einstellungen.artefakte.statusAktiv')}
                </Badge>
              ) : (
                <Badge variant="default">
                  {t('mss.einstellungen.artefakte.statusDeaktiviert')}
                </Badge>
              )}
            </div>
          </div>
          <Switch
            checked={konfig?.artifact_install_aktiv === true}
            disabled={konfig === null}
            onCheckedChange={(an) => void toggle(an)}
            aria-label={t('mss.einstellungen.artefakte.titel')}
          />
        </div>

        {konfig?.artifact_install_aktiv && (
          <div className="mt-2 flex flex-col gap-4 rounded-xl border border-outline-variant/30 bg-surface-container-low/30 p-4">
            {/* Windows Sandbox Status */}
            <div className="flex items-center justify-between gap-2">
              <p className="text-xs font-medium text-on-surface">{t('mss.einstellungen.artefakte.sandboxTitel')}</p>
              <Badge variant={sandboxOk ? 'success' : 'warning'}>
                {sandboxOk ? t('mss.einstellungen.artefakte.sandboxBereit') : t('mss.einstellungen.artefakte.sandboxFehlt')}
              </Badge>
            </div>

            {/* Download Limit */}
            <div className="flex flex-col gap-2">
              <div className="flex items-center justify-between text-xs">
                <span className="text-on-surface font-medium">{t('mss.einstellungen.artefakte.downloadLimitTitel')}</span>
                <span className="font-mono text-primary">{limitGiB} GiB</span>
              </div>
              <Slider
                min={1}
                max={100}
                step={1}
                value={limitGiB}
                onValueChange={(val) => void downloadLimitAendern(val)}
                ariaLabel={t('mss.einstellungen.artefakte.downloadLimitTitel')}
              />
            </div>

            {/* Freigegebene Suchwurzeln */}
            <div className="flex flex-col gap-2">
              <div className="flex items-center justify-between">
                <p className="text-xs font-medium text-on-surface">{t('mss.einstellungen.artefakte.suchwurzelnTitel')}</p>
                <Button variant="secondary" size="sm" onClick={() => void suchwurzelHinzufuegen()}>
                  {t('mss.einstellungen.artefakte.suchwurzelHinzufuegen')}
                </Button>
              </div>
              {konfig.search_roots.length === 0 ? (
                <p className="text-xs italic text-on-surface-variant/70">
                  {t('mss.einstellungen.artefakte.keineSuchwurzeln')}
                </p>
              ) : (
                <ul className="flex flex-col gap-1">
                  {konfig.search_roots.map((wurzel) => (
                    <li key={wurzel} className="flex items-center justify-between gap-2 rounded-lg bg-surface-container px-3 py-1.5 text-xs">
                      <span className="break-all font-mono text-on-surface">{wurzel}</span>
                      <Button variant="ghost" size="sm" onClick={() => void suchwurzelEntfernen(wurzel)}>
                        ✕
                      </Button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        )}
      </div>

      {dialogOffen && (
        <div
          className="msm-modal-overlay"
          role="dialog"
          aria-modal="true"
          aria-label={t('mss.einstellungen.artefakte.aktivierenTitel')}
        >
          <div className="msm-card flex w-full max-w-md flex-col gap-4 p-5">
            <div className="flex items-center gap-2 text-status-warning">
              <AlertTriangle className="h-5 w-5 shrink-0" aria-hidden="true" />
              <h2 className="text-base font-semibold text-on-surface">
                {t('mss.einstellungen.artefakte.aktivierenTitel')}
              </h2>
            </div>
            <p className="text-xs leading-relaxed text-on-surface-variant">
              {t('mss.einstellungen.artefakte.aktivierenWarnung')}
            </p>
            <div className="flex items-center justify-end gap-2 pt-2">
              <Button variant="ghost" size="sm" onClick={() => setDialogOffen(false)}>
                {t('mss.einstellungen.artefakte.abbrechen')}
              </Button>
              <Button autoFocus size="sm" onClick={() => void bestaetigenAktivieren()}>
                {t('mss.einstellungen.artefakte.aktivierenBestaetigen')}
              </Button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}

/** Die drei Stufen, in der Reihenfolge, in der sie mehr erlauben. */
const SYSTEMBEREICHE = ['aus', 'lesen', 'schreiben'] as const
type Systembereichswert = (typeof SYSTEMBEREICHE)[number]

/**
 * Wie weit die KI in Windows selbst greifen darf.
 *
 * Der Wert liegt am **Konto**, nicht in `konfig.json`: das Panel muss ihn
 * kennen, wenn es einen Aufräumauftrag zusammenstellt, und ein Wert, den nur
 * dieser Rechner kennt, wäre für einen Auftrag aus dem Panel unsichtbar.
 * Deshalb hier eine Panelabfrage und kein Rust-Command.
 *
 * Ausserhalb dieses Bereichs — im eigenen Profil, auf Datenlaufwerken —
 * arbeitet die KI ohne diese Einstellung; die Bestätigungsfrage dort hängt
 * allein am autonomen Modus. Diese Stufen gelten nur für das, was Windows
 * selbst gehört.
 */
function Systembereich() {
  const { t } = useTranslation()
  const [wert, setWert] = useState<Systembereichswert | null>(null)
  const [sendet, setSendet] = useState(false)

  useEffect(() => {
    void api<{ systembereich: Systembereichswert }>('/ai/settings/desktop')
      .then((daten) => setWert(daten.systembereich))
      // Kein Recht, kein Panel, keine Anmeldung: dann gibt es hier nichts zu
      // entscheiden, und ein Fehlertoast wäre nur Lärm.
      .catch(() => setWert(null))
  }, [])

  async function waehlen(neu: Systembereichswert) {
    if (sendet || neu === wert) return
    const vorher = wert
    setWert(neu)
    setSendet(true)
    try {
      await api('/ai/settings/desktop', {
        method: 'PUT',
        body: JSON.stringify({ systembereich: neu }),
      })
    } catch {
      setWert(vorher)
      toast.error(t('mss.systembereich.fehler'))
    } finally {
      setSendet(false)
    }
  }

  if (wert === null) {
    return null
  }

  return (
    <div className="border-t border-outline-variant/40 pt-4">
      <p className="mb-3 text-sm text-on-surface">{t('mss.systembereich.titel')}</p>
      <div className="flex flex-wrap gap-2">
        {SYSTEMBEREICHE.map((stufe) => (
          <button
            key={stufe}
            disabled={sendet}
            onClick={() => void waehlen(stufe)}
            className={`rounded-lg border px-3.5 py-2 text-sm transition-colors ${
              wert === stufe
                ? 'border-primary/40 bg-primary/10 text-primary'
                : 'border-outline-variant/40 bg-surface-container-low/40 text-on-surface-variant hover:text-on-surface'
            }`}
          >
            {t(`mss.systembereich.stufe.${stufe}`)}
          </button>
        ))}
      </div>
    </div>
  )
}

/**
 * Der Audio-Reiter: welches Mikrofon hört, welcher Lautsprecher spricht —
 * unabhängig vom Windows-Standard. Gespeichert wird nur der Gerätename
 * (konfig.json); „Windows-Standard" heißt: dem System folgen, auch wenn es
 * sich ändert. Ein gewähltes Gerät, das gerade fehlt, fällt still auf den
 * Standard zurück — ein abgezogenes USB-Mikrofon legt nichts lahm.
 */
function AudioEinstellungen() {
  const { t } = useTranslation()
  const [geraete, setGeraete] = useState<AudioGeraete | null>(null)
  const [konfig, setKonfig] = useState<AppKonfig | null>(null)
  const [duckt, setDuckt] = useState(false)
  const [istAmTesten, setIstAmTesten] = useState(false)
  // Bündelt das Speichern der Verarbeitung. Beim Unmount bewusst NICHT
  // geräumt: die Timeout-Schließung ist in sich geschlossen (frisches Laden,
  // Speichern, kein React-State) — räumen hieße, die letzte Änderung des
  // Benutzers wegzuwerfen.
  const speicherTimer = useRef<number | null>(null)

  useEffect(() => {
    void audioGeraete()
      .then(setGeraete)
      .catch(() => setGeraete(null))
    void konfigLaden()
      .then(setKonfig)
      .catch(() => setKonfig(null))
  }, [])

  async function waehlen(feld: 'audio_eingabe' | 'audio_ausgabe', wert: string) {
    if (!konfig) return
    const neu: AppKonfig = { ...konfig, [feld]: wert === '' ? null : wert }
    setKonfig(neu)
    try {
      await konfigSpeichern(neu)
      // Sofort wirksam für Sitzungen in diesem Fenster; das Overlay lädt die
      // Wahl bei jedem Sitzungsstart frisch, Rust (Wake-Word) liest sie je
      // Aufnahme selbst.
      registriereAudioGeraete(neu.audio_eingabe, neu.audio_ausgabe)
      // Der Lausch-Thread hält sein Mikrofon offen, bis er endet — läuft er,
      // einmal durchstarten, damit das neue Gerät auch wirklich hört.
      if (feld === 'audio_eingabe' && neu.wakeword_aktiv) {
        await wakewordLauschen(false)
        await wakewordLauschen(true)
      }
    } catch (fehler) {
      toast.error(String(fehler))
    }
  }

  const auswahl = (
    feld: 'audio_eingabe' | 'audio_ausgabe',
    liste: string[],
    standard: string | null,
  ) => {
    const wert = konfig?.[feld] ?? ''
    const isAndroid = typeof navigator !== 'undefined' && /android/i.test(navigator.userAgent)
    const standardLabel = isAndroid
      ? standard
        ? t('mss.audio.standardSystemMit', { name: standard })
        : t('mss.audio.standardSystem')
      : standard
        ? t('mss.audio.standardMit', { name: standard })
        : t('mss.audio.standard')

    const options: DropdownOption[] = [
      { value: '', label: standardLabel },
      ...(wert !== '' && !liste.includes(wert)
        ? [{ value: wert, label: t('mss.audio.fehlt', { name: wert }) }]
        : []),
      ...liste.map((name) => ({ value: name, label: name })),
    ]

    return (
      <div className="w-full">
        <Dropdown
          value={wert}
          onChange={(val) => void waehlen(feld, val)}
          options={options}
          disabled={konfig === null}
          aria-label={t(`mss.audio.${feld === 'audio_eingabe' ? 'eingabe' : 'ausgabe'}`)}
        />
      </div>
    )
  }

  async function duckingTesten() {
    setDuckt(true)
    try {
      await duckingSetzen(true)
      await new Promise((fertig) => setTimeout(fertig, 3000))
      await duckingSetzen(false)
    } finally {
      setDuckt(false)
    }
  }

  function verarbeitungSetzen(
    feld: 'audio_echo' | 'audio_rauschen' | 'audio_autogain' | 'audio_verstaerkung',
    wert: boolean | number,
  ) {
    if (!konfig) return
    const neu: AppKonfig = { ...konfig, [feld]: wert }
    setKonfig(neu)
    registriereAudioVerarbeitung({
      echo: neu.audio_echo,
      rauschen: neu.audio_rauschen,
      autogain: neu.audio_autogain,
      verstaerkung: neu.audio_verstaerkung,
    })
    if (speicherTimer.current !== null) window.clearTimeout(speicherTimer.current)
    speicherTimer.current = window.setTimeout(() => {
      speicherTimer.current = null
      void (async () => {
        try {
          const aktuell = await konfigLaden()
          await konfigSpeichern({
            ...aktuell,
            audio_echo: neu.audio_echo,
            audio_rauschen: neu.audio_rauschen,
            audio_autogain: neu.audio_autogain,
            audio_verstaerkung: neu.audio_verstaerkung,
          })
        } catch (fehler) {
          toast.error(String(fehler))
        }
      })()
    }, VERARBEITUNG_SPEICHERN_MS)
  }

  const gainPercent = Math.round((konfig?.audio_verstaerkung ?? 1) * 100)

  return (
    <div className="space-y-6">
      {/* 1. Geräte-Auswahl Karte */}
      <section className="msm-card p-6" aria-labelledby="audio-devices-heading">
        <div className="flex items-center justify-between gap-4 mb-4">
          <div className="flex items-center gap-2">
            <Mic className="h-5 w-5 text-secondary" aria-hidden="true" />
            <h2 id="audio-devices-heading" className="font-headline text-title-lg font-semibold text-on-surface">
              {t('profile.audioTitle')}
            </h2>
          </div>
          <span
            className={`inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs font-medium ${
              istAmTesten
                ? 'border-status-success/30 bg-status-success/10 text-status-success animate-pulse'
                : 'border-outline-variant bg-surface-container text-on-surface-variant'
            }`}
          >
            <Radio className="h-3.5 w-3.5" aria-hidden="true" />
            {istAmTesten ? t('profile.audioActive') : t('profile.audioInactive')}
          </span>
        </div>

        <p className="max-w-2xl font-body-md text-sm leading-6 text-on-surface-variant mb-6">
          {t('profile.audioDescription')}
        </p>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 max-w-2xl">
          <div className="space-y-1.5">
            <label className="block font-label-md text-label-md text-on-surface-variant uppercase tracking-wider">
              {t('profile.audioDeviceLabel')}
            </label>
            {auswahl('audio_eingabe', geraete?.eingaenge ?? [], geraete?.standard_eingang ?? null)}
          </div>

          <div className="space-y-1.5">
            <label className="block font-label-md text-label-md text-on-surface-variant uppercase tracking-wider">
              {t('mss.audio.ausgabe')}
            </label>
            {auswahl('audio_ausgabe', geraete?.ausgaenge ?? [], geraete?.standard_ausgang ?? null)}
          </div>
        </div>

        {!isAndroidClient && (
          <div className="flex items-center justify-between gap-3 border-t border-outline-variant/30 pt-4 mt-6 max-w-2xl">
            <div>
              <span className="text-xs font-medium text-on-surface block">{t('mss.audio.ducking')}</span>
              <span className="text-label-sm text-on-surface-variant">
                {t('mss.audio.duckingHinweis')}
              </span>
            </div>
            <Button variant="secondary" size="sm" onClick={() => void duckingTesten()} disabled={duckt}>
              {duckt
                ? t('mss.einstellungen.duckingLaeuft')
                : t('mss.einstellungen.duckingTesten')}
            </Button>
          </div>
        )}
      </section>

      {/* 2. Signalverarbeitung & Filter */}
      <section className="msm-card p-6" aria-labelledby="audio-processing-heading">
        <div className="flex items-center gap-2 mb-4">
          <Sliders className="h-5 w-5 text-secondary" aria-hidden="true" />
          <h2 id="audio-processing-heading" className="font-headline text-title-lg font-semibold text-on-surface">
            {t('mss.audio.verarbeitung')}
          </h2>
        </div>
        <p className="max-w-2xl font-body-md text-sm leading-6 text-on-surface-variant mb-5">
          {t('mss.audio.verarbeitungHinweis')}
        </p>

        <div className="max-w-xl space-y-4">
          <div className="flex items-center justify-between gap-3 p-3 rounded-xl border border-outline-variant/30 bg-surface-container-low/40">
            <div>
              <span className="text-sm font-medium text-on-surface block">
                {t('profile.audioNoiseSuppression')}
              </span>
              <span className="text-xs text-on-surface-variant">
                {t('mss.audio.rauschenHinweis')}
              </span>
            </div>
            <Switch
              checked={konfig?.audio_rauschen ?? true}
              disabled={konfig === null}
              onCheckedChange={(an) => void verarbeitungSetzen('audio_rauschen', an)}
              aria-label={t('profile.audioNoiseSuppression')}
            />
          </div>

          <div className="flex items-center justify-between gap-3 p-3 rounded-xl border border-outline-variant/30 bg-surface-container-low/40">
            <div>
              <span className="text-sm font-medium text-on-surface block">
                {t('profile.audioEchoCancellation')}
              </span>
              <span className="text-xs text-on-surface-variant">
                {t('mss.audio.echoHinweis')}
              </span>
            </div>
            <Switch
              checked={konfig?.audio_echo ?? true}
              disabled={konfig === null}
              onCheckedChange={(an) => void verarbeitungSetzen('audio_echo', an)}
              aria-label={t('profile.audioEchoCancellation')}
            />
          </div>

          <div className="flex items-center justify-between gap-3 p-3 rounded-xl border border-outline-variant/30 bg-surface-container-low/40">
            <div>
              <span className="text-sm font-medium text-on-surface block">
                {t('profile.audioAutoGain')}
              </span>
              <span className="text-xs text-on-surface-variant">
                Gleicht leise und laute Sprachpassagen automatisch an ein gesundes Niveau an.
              </span>
            </div>
            <Switch
              checked={konfig?.audio_autogain ?? true}
              disabled={konfig === null}
              onCheckedChange={(an) => void verarbeitungSetzen('audio_autogain', an)}
              aria-label={t('profile.audioAutoGain')}
            />
          </div>

          <div className="pt-2">
            <Slider
              value={gainPercent}
              min={25}
              max={400}
              step={5}
              disabled={konfig === null}
              onValueChange={(prozent) => void verarbeitungSetzen('audio_verstaerkung', prozent / 100)}
              label={t('mss.audio.verstaerkung')}
              hint={`${gainPercent} %`}
            />
          </div>
        </div>
      </section>

      {/* 3. Testhören & Mikrofon-Pegel */}
      <Testhoeren
        verarbeitung={{
          echo: konfig?.audio_echo ?? true,
          rauschen: konfig?.audio_rauschen ?? true,
          autogain: konfig?.audio_autogain ?? true,
          verstaerkung: konfig?.audio_verstaerkung ?? 1,
        }}
        onTestZustand={setIstAmTesten}
      />
    </div>
  )
}

/**
 * Testhören wie in Discord: das eigene Mikrofon auf den gewählten Lautsprecher
 * legen und dabei den Pegel sehen — mit genau der Verarbeitung, die auch die
 * Sprachsitzung nähme. Einzige Abweichung: die Echounterdrückung ist im Test
 * aus, weil sie sonst die eigene Wiedergabe als „Echo" erkennt und wegfiltert —
 * man hörte sich leiser werden, je länger man spricht. Alles bleibt im
 * Chromium-Prozess, nichts davon geht ins Netz.
 */
function Testhoeren({
  verarbeitung,
  onTestZustand,
}: {
  verarbeitung: AudioVerarbeitung
  onTestZustand?: (aktiv: boolean) => void
}) {
  const { t } = useTranslation()
  const [laeuft, setLaeuft] = useState(false)
  const [pegel, setPegel] = useState(0)
  const [fehler, setFehler] = useState<string | null>(null)
  const aufraeumen = useRef<(() => void) | null>(null)
  const gainKnoten = useRef<GainNode | null>(null)
  const verlassen = useRef(false)
  const startNummer = useRef(0)

  const stoppen = useCallback(() => {
    startNummer.current += 1
    aufraeumen.current?.()
    aufraeumen.current = null
    gainKnoten.current = null
    setLaeuft(false)
    setPegel(0)
    onTestZustand?.(false)
  }, [onTestZustand])

  const starten = useCallback(async () => {
    const nummer = startNummer.current + 1
    startNummer.current = nummer
    aufraeumen.current?.()
    aufraeumen.current = null
    setFehler(null)
    try {
      const geraet = await eingabeGeraetId().catch(() => null)
      const strom = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: false,
          noiseSuppression: verarbeitung.rauschen,
          autoGainControl: verarbeitung.autogain,
          ...(geraet ? { deviceId: { ideal: geraet } } : {}),
        },
      })
      if (verlassen.current || nummer !== startNummer.current) {
        strom.getTracks().forEach((spur) => spur.stop())
        return
      }
      const kontext = new AudioContext()
      if (kontext.state === 'suspended') {
        await kontext.resume().catch(() => {})
      }
      void ausgabeGeraetId()
        .then((sink) => {
          const mitSink = kontext as AudioContext & {
            setSinkId?: (id: string) => Promise<void>
          }
          if (sink && mitSink.setSinkId) return mitSink.setSinkId(sink)
        })
        .catch(() => undefined)
      const quelle = kontext.createMediaStreamSource(strom)
      const gain = kontext.createGain()
      gain.gain.value = aktuelleVerarbeitung().verstaerkung
      gainKnoten.current = gain
      const analyser = kontext.createAnalyser()
      quelle.connect(gain)
      gain.connect(analyser)
      gain.connect(kontext.destination)
      const puffer = new Float32Array(analyser.fftSize)
      const takt = window.setInterval(() => {
        analyser.getFloatTimeDomainData(puffer)
        let summe = 0
        for (let i = 0; i < puffer.length; i += 1) summe += puffer[i] * puffer[i]
        setPegel(Math.min(1, Math.sqrt(summe / puffer.length) * 4))
      }, 100)
      aufraeumen.current = () => {
        window.clearInterval(takt)
        quelle.disconnect()
        gain.disconnect()
        analyser.disconnect()
        strom.getTracks().forEach((spur) => spur.stop())
        void kontext.close().catch(() => undefined)
      }
      setLaeuft(true)
      onTestZustand?.(true)
    } catch {
      setFehler(t('mss.audio.testhoerenFehler'))
      setLaeuft(false)
      onTestZustand?.(false)
    }
  }, [verarbeitung.rauschen, verarbeitung.autogain, t, onTestZustand])

  useEffect(() => {
    if (gainKnoten.current) gainKnoten.current.gain.value = aktuelleVerarbeitung().verstaerkung
  }, [verarbeitung.verstaerkung])

  useEffect(() => {
    if (aufraeumen.current) void starten()
  }, [starten])

  useEffect(() => () => {
    verlassen.current = true
    aufraeumen.current?.()
  }, [])

  return (
    <section className="msm-card p-6" aria-labelledby="audio-test-heading">
      <div className="flex items-center gap-2 mb-4">
        <Volume2 className="h-5 w-5 text-secondary" aria-hidden="true" />
        <h2 id="audio-test-heading" className="font-headline text-title-lg font-semibold text-on-surface">
          {t('mss.audio.testhoeren')}
        </h2>
      </div>
      <p className="max-w-2xl font-body-md text-sm leading-6 text-on-surface-variant mb-5">
        {t('mss.audio.testhoerenHinweis')}
      </p>

      <div className="max-w-xl space-y-4">
        <div className="flex items-center gap-3">
          <Button
            type="button"
            variant={laeuft ? 'secondary' : 'primary'}
            onClick={() => (laeuft ? stoppen() : void starten())}
            className="gap-2 shrink-0"
          >
            <Mic className="w-4 h-4" />
            <span>{laeuft ? t('profile.audioTestStop') : t('profile.audioTestStart')}</span>
          </Button>

          <ProgressBar
            value={laeuft ? Math.round(pegel * 100) : null}
            ariaLabel={t('mss.audio.testhoerenPegel')}
            className="flex-1"
          />
        </div>

        {laeuft && (
          <div className="flex items-center justify-between text-xs px-1 text-on-surface-variant">
            <span>Pegel: {Math.round(pegel * 100)}%</span>
            <span className={pegel > 0.05 ? 'text-status-success font-semibold' : 'text-on-surface-variant/60'}>
              {pegel > 0.05 ? t('profile.audioSignalDetected') : 'Kein Signal'}
            </span>
          </div>
        )}

        {fehler && (
          <div className="p-3 rounded-xl bg-error/10 border border-error/30 text-error text-xs flex items-center gap-2">
            <ShieldAlert className="w-4 h-4 shrink-0" />
            <span>{fehler}</span>
          </div>
        )}
      </div>
    </section>
  )
}

/**
 * Baut aus einem Tastendruck die Kombination im Format der Registrierung
 * („Ctrl+Shift+K"). `null` heißt: nur Modifier gedrückt — weiter warten.
 * Der eigentliche Prüfer sitzt in Rust (`hotkey_pruefen`); was er ablehnt,
 * kommt als Fehlermeldung zurück, und der alte Hotkey bleibt.
 */
function komboAusEreignis(ereignis: KeyboardEvent): string | null {
  const code = ereignis.code
  if (!code || /^(Control|Alt|Shift|Meta)/.test(code)) return null
  const teile: string[] = []
  if (ereignis.ctrlKey) teile.push('Ctrl')
  if (ereignis.altKey) teile.push('Alt')
  if (ereignis.shiftKey) teile.push('Shift')
  if (ereignis.metaKey) teile.push('Super')
  const taste = code.startsWith('Key')
    ? code.slice(3)
    : code.startsWith('Digit')
      ? code.slice(5)
      : code
  return [...teile, taste].join('+')
}

/** Die Vorgaben — dieselben Werte wie `konfig::Default` in Rust. */
const HOTKEY_VORGABEN = { fenster: 'Alt+Space', sprache: 'Alt+Shift+Space' } as const

type HotkeyArt = 'fenster' | 'sprache'

/**
 * Zwei Hotkeys, je einzeln abschaltbar: einer fürs Hauptfenster, einer für
 * die Sprachsitzung im Overlay. Geändert wird per Aufnahme (nächster
 * Tastendruck), registriert und gespeichert in Rust (`hotkeys_setzen`) —
 * eine belegte Kombination kommt als Fehler zurück, nichts stellt sich um.
 */
function Hotkeys() {
  const { t } = useTranslation()
  const [werte, setWerte] = useState<Record<HotkeyArt, string | null> | null>(null)
  const [aufnahme, setAufnahme] = useState<HotkeyArt | null>(null)
  // Die letzte Kombination je Seite: der Aktiv-Schalter stellt sie wieder
  // her — nicht die Werksvorgabe, die der Benutzer längst ersetzt hat.
  const zuletzt = useRef({ ...HOTKEY_VORGABEN } as Record<HotkeyArt, string>)

  useEffect(() => {
    void konfigLaden()
      .then((konfig) => {
        const geladen = { fenster: konfig.hotkey_fenster, sprache: konfig.hotkey_sprache }
        if (geladen.fenster) zuletzt.current.fenster = geladen.fenster
        if (geladen.sprache) zuletzt.current.sprache = geladen.sprache
        setWerte(geladen)
      })
      .catch(() => setWerte(null))
  }, [])

  async function anwenden(neu: Record<HotkeyArt, string | null>) {
    try {
      await hotkeysSetzen(neu.fenster, neu.sprache)
      if (neu.fenster) zuletzt.current.fenster = neu.fenster
      if (neu.sprache) zuletzt.current.sprache = neu.sprache
      setWerte(neu)
    } catch (fehler) {
      toast.error(String(fehler))
    }
  }

  useEffect(() => {
    if (!aufnahme || !werte) return
    const taste = (ereignis: KeyboardEvent) => {
      ereignis.preventDefault()
      ereignis.stopPropagation()
      if (ereignis.key === 'Escape') {
        setAufnahme(null)
        return
      }
      const kombi = komboAusEreignis(ereignis)
      if (!kombi) return
      setAufnahme(null)
      void anwenden({ ...werte, [aufnahme]: kombi })
    }
    window.addEventListener('keydown', taste, true)
    return () => window.removeEventListener('keydown', taste, true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [aufnahme, werte])

  const zeile = (art: HotkeyArt) => {
    const wert = werte?.[art] ?? null
    return (
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm text-on-surface">{t(`mss.einstellungen.hotkey.${art}`)}</p>
          <p className="text-xs text-on-surface-variant">
            {wert ? <kbd className="font-mono">{wert}</kbd> : t('mss.einstellungen.hotkey.aus')}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Button
            variant="secondary"
            size="sm"
            disabled={!werte || wert === null}
            onClick={() => setAufnahme(aufnahme === art ? null : art)}
          >
            {aufnahme === art
              ? t('mss.einstellungen.hotkey.druecken')
              : t('mss.einstellungen.hotkey.aendern')}
          </Button>
          <Switch
            checked={wert !== null}
            disabled={!werte}
            onCheckedChange={(an) => {
              if (!werte) return
              setAufnahme(null)
              void anwenden({ ...werte, [art]: an ? zuletzt.current[art] : null })
            }}
            aria-label={t(`mss.einstellungen.hotkey.${art}`)}
          />
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-3 border-t border-outline-variant/40 pt-4">
      <div>
        <p className="text-sm text-on-surface">{t('mss.einstellungen.hotkey.titel')}</p>
      </div>
      {zeile('fenster')}
      {zeile('sprache')}
    </div>
  )
}

function RechtlichesEinstellungen() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const legal = usePublicLegalSettings()

  async function impressumOeffnen(url: string) {
    try {
      await oeffneBrowser(url)
    } catch {
      window.open(url, '_blank', 'noopener,noreferrer')
    }
  }

  return (
    <div className="flex flex-col gap-6">
      {/* Slogan & Philosophie */}
      <section className="msm-card bg-surface-container-low/40 p-5">
        <div className="flex items-center gap-3">
          <div className="rounded-lg bg-primary/10 p-2.5 text-primary">
            <ShieldCheck className="h-5 w-5" aria-hidden="true" />
          </div>
          <div>
            <h2 className="font-headline text-base font-semibold text-on-surface">
              {t('mss.einstellungen.rechtliches.slogan')}
            </h2>
            <p className="mt-1 text-xs text-on-surface-variant">
              {t('mss.einstellungen.rechtliches.beschreibung')}
            </p>
          </div>
        </div>
      </section>

      {/* Datenschutzerklärung */}
      <section className="msm-card flex flex-col gap-4 p-5">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-medium text-on-surface">
              {t('mss.einstellungen.rechtliches.datenschutzTitel')}
            </h3>
            <Badge variant="default">
              {t('mss.einstellungen.rechtliches.datenschutzVersion', { version: 'v2.7' })}
            </Badge>
          </div>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => navigate('/privacy')}
            className="shrink-0"
          >
            {t('mss.einstellungen.rechtliches.datenschutzOeffnen')}
          </Button>
        </div>
      </section>

      {/* Impressum */}
      <section className="msm-card flex flex-col gap-4 p-5">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <h3 className="text-sm font-medium text-on-surface">
                {t('mss.einstellungen.rechtliches.impressumTitel')}
              </h3>
              <Badge
                variant={legal.imprint_enabled && legal.imprint_url ? 'success' : 'default'}
              >
                {legal.imprint_enabled && legal.imprint_url
                  ? t('mss.einstellungen.rechtliches.impressumAktiv')
                  : t('mss.einstellungen.rechtliches.impressumInaktiv')}
              </Badge>
            </div>
            {legal.imprint_enabled && legal.imprint_url && (
              <p className="mt-2 text-xs font-mono text-primary truncate max-w-md">
                {legal.imprint_url}
              </p>
            )}
          </div>
          {legal.imprint_enabled && legal.imprint_url && (
            <Button
              variant="primary"
              size="sm"
              onClick={() => void impressumOeffnen(legal.imprint_url)}
              className="shrink-0"
            >
              <ExternalLink className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
              {t('mss.einstellungen.rechtliches.impressumOeffnen')}
            </Button>
          )}
        </div>
      </section>
    </div>
  )
}

