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
  Fingerprint,
  Globe,
  Lock,
  MapPin,
  Mic,
  MonitorCog,
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
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'

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
import { Avatar, Badge, Button, Dropdown, type DropdownOption, Input, ProgressBar, Slider, Switch } from '@/Singra/UI'
import { useAuthStore } from '@/stores/authStore'
import { toast } from '@/stores/toastStore'
import { getAvailableTimezones } from '@/utils/timeFormat'
import { Gefahrenzone } from './Gefahrenzone'
import { OVERLAY_ZUSTAND_TEST } from './sprachKoordination'
import { WakewordEinrichtung } from './WakewordEinrichtung'
import { useVaultStore } from './vault/vaultStore'
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

type EinstellungsTab = 'konto' | 'social' | 'desktop' | 'wakeword' | 'audio' | 'rechtliches' | 'gefahr'

const isAndroidClient = typeof navigator !== 'undefined' && /android/i.test(navigator.userAgent)

const TABS: TabDef<EinstellungsTab>[] = [
  { id: 'konto', labelKey: 'profile.tabs.account', icon: User },
  { id: 'social', labelKey: 'profile.tabs.social', icon: Users },
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

  // Tresor & Biometrie
  const {
    isInitialized,
    isUnlocked,
    autoLockMinutes,
    lockOnWindowBlur,
    isBiometricsSupported,
    isBiometricsEnabled,
    setAutoLockMinutes,
    setLockOnWindowBlur,
    enableBiometrics,
    disableBiometrics,
    checkBiometricsSupport,
  } = useVaultStore()

  useEffect(() => {
    void checkBiometricsSupport()
  }, [checkBiometricsSupport])

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
      toast.success(t('profile.timezoneSaved', 'Zeitzone gespeichert.'))
    } catch {
      toast.error(t('profile.timezoneSaveFailed', 'Zeitzone konnte nicht gespeichert werden.'))
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
          t('profile.locationSharingPermissionError', 'Der Standortzugriff wurde nicht freigegeben. Du kannst ihn in den Systemeinstellungen erlauben.'),
        )
      } else {
        setLocationSharingError(t('profile.locationSharingSaveError', 'Die Standortfreigabe konnte nicht gespeichert werden.'))
      }
    } finally {
      setSavingLocationSharing(false)
    }
  }

  const [biometricsModalOpen, setBiometricsModalOpen] = useState(false)
  const [masterPasswordInput, setMasterPasswordInput] = useState('')
  const [biometricsLoading, setBiometricsLoading] = useState(false)

  const handleAvatarChange = async (file?: File | null) => {
    if (!file) return
    if (file.size > 5 * 1024 * 1024) {
      toast.error(t('profile.avatarSizeLimit', 'Das Profilbild darf maximal 5 MB groß sein.'))
      return
    }
    const allowedTypes = ['image/jpeg', 'image/png', 'image/webp', 'image/gif']
    if (!allowedTypes.includes(file.type)) {
      toast.error(t('profile.avatarInvalidType', 'Erlaubte Formate sind JPEG, PNG, WebP und GIF.'))
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
      toast.success(t('profile.avatarUpdated', 'Profilbild erfolgreich aktualisiert.'))
    } catch (err: any) {
      toast.error(err?.detail || t('profile.avatarUpdateFailed', 'Profilbild konnte nicht hochgeladen werden.'))
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
      toast.success(t('profile.avatarRemoved', 'Profilbild wurde entfernt.'))
    } catch (err: any) {
      toast.error(err?.detail || t('profile.avatarRemoveFailed', 'Profilbild konnte nicht entfernt werden.'))
    } finally {
      setUploadingAvatar(false)
    }
  }

  const autoLockOptions: DropdownOption[] = [
    { value: '0', label: t('mss.vault.autolock.disabled', 'Sofort beim Verlassen') },
    { value: '5', label: t('mss.vault.autolock.5min', '5 Minuten Inaktivität') },
    { value: '10', label: t('mss.vault.autolock.10min', '10 Minuten Inaktivität') },
    { value: '15', label: t('mss.vault.autolock.15min', '15 Minuten Inaktivität') },
    { value: '30', label: t('mss.vault.autolock.30min', '30 Minuten Inaktivität') },
    { value: '60', label: t('mss.vault.autolock.60min', '1 Stunde Inaktivität') },
  ]

  const handleBiometricsToggle = async (checked: boolean) => {
    if (!checked) {
      await disableBiometrics()
      toast.success('Biometrischer Schnelleinstieg deaktiviert')
      return
    }
    setMasterPasswordInput('')
    setBiometricsModalOpen(true)
  }

  const handleConfirmBiometrics = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!masterPasswordInput) return
    setBiometricsLoading(true)
    try {
      const ok = await enableBiometrics(masterPasswordInput)
      if (ok) {
        toast.success('Biometrischer Schnelleinstieg aktiviert')
        setBiometricsModalOpen(false)
        setMasterPasswordInput('')
      } else {
        toast.error('Konnte Biometrie nicht aktivieren. Prüfe das Master-Passwort.')
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Konnte Biometrie nicht aktivieren. Prüfe das Master-Passwort.'
      toast.error(msg)
    } finally {
      setBiometricsLoading(false)
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
            <h2 className="text-sm font-semibold text-on-surface">{t('profile.tabs.account', 'Konto & Profilbild')}</h2>
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
                {user?.avatar_url ? t('profile.changeAvatar', 'Bild ändern') : t('profile.uploadAvatar', 'Bild hochladen')}
              </Button>

              {user?.avatar_url && (
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={uploadingAvatar}
                  onClick={() => void handleDeleteAvatar()}
                  className="text-status-error hover:bg-status-error/10"
                >
                  <Trash2 className="h-3.5 w-3.5 mr-1.5" />
                  {t('profile.removeAvatar', 'Entfernen')}
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
            <h2 className="text-sm font-semibold text-on-surface">{t('profile.timezoneTitle', 'Zeitzone')}</h2>
          </div>
        </div>

        {showBrowserHint && browserZone && (
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-primary/30 bg-primary/10 p-3 text-xs text-on-surface">
            <div className="flex items-center gap-2">
              <Globe className="h-4 w-4 text-primary shrink-0" aria-hidden="true" />
              <span>
                {t('profile.timezoneBrowserHint', 'System nutzt {{zone}}, im Konto ist {{current}} gespeichert.', {
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
                {t('profile.timezoneAdopt', 'Übernehmen')}
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => setDismissedBrowserHint(true)}
              >
                {t('profile.timezoneDismiss', 'Ausblenden')}
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
            searchPlaceholder={t('profile.timezoneSearch', 'Zeitzone suchen …')}
            placeholder={t('profile.timezonePlaceholder', 'Zeitzone auswählen')}
            aria-label={t('profile.timezoneLabel', 'Zeitzone')}
          />
          <Button
            type="button"
            variant="primary"
            size="sm"
            disabled={savingZone || (selectedZone === user?.time_zone && Boolean(user?.time_zone))}
            onClick={() => void handleSaveTimezone()}
          >
            <Save className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
            {savingZone ? t('common.saving', 'Speichern …') : t('profile.timezoneSave', 'Zeitzone speichern')}
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
                {t('profile.locationSharingTitle', 'Standort für KI-Anfragen')}
              </h2>
              <p className="text-xs text-on-surface-variant">
                {t('profile.locationSharingDescription', 'Wird nur bei ortsbezogenen KI-Anfragen verwendet.')}
              </p>
            </div>
          </div>

          <Switch
            checked={Boolean(user?.location_sharing_enabled)}
            disabled={savingLocationSharing}
            onCheckedChange={(checked) => void handleLocationSharingChange(checked)}
            aria-label={t('profile.locationSharingTitle', 'Standort für KI-Anfragen')}
          />
        </div>

        {locationSharingError && (
          <div className="rounded-xl border border-status-error/30 bg-status-error/10 p-3 text-xs text-status-error flex items-center gap-2">
            <AlertTriangle className="h-4 w-4 shrink-0" />
            <span>{locationSharingError}</span>
          </div>
        )}
      </div>

      {/* 4. Passwort-Manager & Automatische Sperre (Auto-Lock) */}
      <div className="msm-card p-5 space-y-4">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary/10 text-primary">
            <ShieldCheck className="h-5 w-5" />
          </div>
          <div>
            <h2 className="text-sm font-semibold text-on-surface">Tresor-Sicherheit & Auto-Lock</h2>
          </div>
        </div>

        <div className="space-y-4 pt-2 border-t border-outline-variant/30">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
            <div>
              <label className="text-xs font-medium text-on-surface">Automatische Sperre</label>
            </div>
            <div className="w-full sm:w-56">
              <Dropdown
                options={autoLockOptions}
                value={String(autoLockMinutes)}
                onChange={(val) => setAutoLockMinutes(Number(val))}
              />
            </div>
          </div>

          <div className="flex items-center justify-between gap-4 pt-2 border-t border-outline-variant/20">
            <div>
              <span className="text-xs font-medium text-on-surface">Beim Fensterwechsel / App-Verlassen sperren</span>
              <p className="text-[11px] text-on-surface-variant">
                Sperrt den Passwort-Manager sofort, sobald das Fenster verlassen oder die App in den Hintergrund gelegt wird.
              </p>
            </div>
            <Switch
              checked={lockOnWindowBlur}
              onCheckedChange={setLockOnWindowBlur}
            />
          </div>
        </div>
      </div>

      {/* 5. Biometrischer Schnelleinstieg */}
      <div className="msm-card p-5 space-y-4">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary/10 text-primary">
            <Fingerprint className="h-5 w-5" />
          </div>
          <div>
            <h2 className="text-sm font-semibold text-on-surface">Biometrischer Schnelleinstieg</h2>
          </div>
        </div>

        <div className="pt-2 border-t border-outline-variant/30 space-y-3">
          {!isInitialized ? (
            <div className="p-2.5 rounded-xl bg-surface-container-high border border-outline-variant/30 text-xs text-on-surface-variant">
              Passwort-Manager ist auf diesem Gerät noch nicht eingerichtet.
            </div>
          ) : !isUnlocked ? (
            <div className="p-2.5 rounded-xl bg-surface-container-high border border-outline-variant/30 text-xs text-on-surface-variant">
              Tresor ist gesperrt. Bitte zuerst entsperren.
            </div>
          ) : null}

          <div className="flex items-center justify-between gap-4">
            <div>
              <span className="text-xs font-medium text-on-surface">
                Biometrische Authentifizierung aktivieren
              </span>
              <p className="text-[11px] text-on-surface-variant">
                Windows Hello / nativer Hardware-Schlüsselspeicher. Schlüssel werden niemals im Browser oder ungesichert gespeichert.
              </p>
            </div>
            <Switch
              checked={isBiometricsEnabled && isBiometricsSupported}
              onCheckedChange={(checked: boolean) => void handleBiometricsToggle(checked)}
              disabled={!isBiometricsSupported || !isInitialized || !isUnlocked}
            />
          </div>

          {!isBiometricsSupported && (
            <div className="p-2.5 rounded-xl bg-surface-container-high border border-outline-variant/30 text-xs text-on-surface-variant">
              Kein biometrischer Sensor oder nativer Hardware-Tresor auf diesem Gerät verfügbar.
            </div>
          )}
        </div>
      </div>

      {/* Biometrie Aktivierungs-Modal */}
      {biometricsModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-3 bg-black/60 backdrop-blur-xs">
          <div className="w-full max-w-sm rounded-2xl bg-surface-container border border-outline-variant/30 p-5 shadow-2xl space-y-4">
            <div className="flex items-center gap-3">
              <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-primary/10 text-primary">
                <Fingerprint className="h-4 w-4" />
              </div>
              <h3 className="text-sm font-semibold text-on-surface">Biometrie einrichten</h3>
            </div>
            <p className="text-xs text-on-surface-variant">
              Master-Passwort zur Bestätigung eingeben.
            </p>
            <form onSubmit={handleConfirmBiometrics} className="space-y-3">
              <input
                type="password"
                value={masterPasswordInput}
                onChange={(e) => setMasterPasswordInput(e.target.value)}
                placeholder="Master-Passwort"
                className="w-full rounded-xl bg-surface-container-low border border-outline-variant/30 px-3 py-2 text-xs text-on-surface focus:outline-none focus:border-primary"
                autoFocus
                required
              />
              <div className="flex items-center justify-end gap-2 pt-2">
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() => setBiometricsModalOpen(false)}
                  disabled={biometricsLoading}
                >
                  Abbrechen
                </Button>
                <Button
                  type="submit"
                  disabled={biometricsLoading || !masterPasswordInput}
                >
                  {biometricsLoading ? 'Prüfe...' : 'Aktivieren'}
                </Button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  )
}

function SocialEinstellungen() {
  const { t } = useTranslation()
  const user = useAuthStore((s) => s.user)
  const updateUser = useAuthStore((s) => s.updateUser)

  // 1. Privatsphäre & Sichtbarkeit
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
      toast.success(t('profile.privacySaved', 'Privatsphäre gespeichert.'))
    } catch {
      toast.error(t('profile.privacySaveFailed', 'Fehler beim Speichern.'))
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

  // 3. Meilensteine
  const [overview, setOverview] = useState<AchievementsOverview | null>(null)
  const [milestoneFilter, setMilestoneFilter] = useState<'all' | 'unlocked' | 'locked'>('all')

  // 4. Freunde
  const [friends, setFriends] = useState<FriendItem[]>([])
  const [incomingRequests, setIncomingRequests] = useState<FriendItem[]>([])
  const [addUsername, setAddUsername] = useState('')
  const [searchFriend, setSearchFriend] = useState('')
  const [sendingRequest, setSendingRequest] = useState(false)

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
  }, [loadData])

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
      toast.error(err instanceof Error ? err.message : 'Fehler beim Senden.')
    } finally {
      setSendingRequest(false)
    }
  }

  const handleAcceptRequest = async (reqId: number) => {
    try {
      await acceptFriendRequest(reqId)
      toast.success('Anfrage angenommen.')
      const [fData, rData] = await Promise.all([getFriends(), getFriendRequests()])
      setFriends(fData)
      setIncomingRequests(rData.incoming)
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : 'Fehler beim Annehmen.')
    }
  }

  const handleDeclineRequest = async (reqId: number) => {
    try {
      await declineFriendRequest(reqId)
      toast.success('Anfrage abgelehnt.')
      const rData = await getFriendRequests()
      setIncomingRequests(rData.incoming)
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : 'Fehler beim Ablehnen.')
    }
  }

  const handleRemoveFriend = async (friendId: number) => {
    try {
      await removeFriend(friendId)
      toast.success('Kontakt entfernt.')
      const fData = await getFriends()
      setFriends(fData)
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : 'Fehler beim Entfernen.')
    }
  }

  const acceptedFriends = useMemo(() => {
    return friends.filter((f) => f.status === 'accepted')
  }, [friends])

  const filteredFriends = useMemo(() => {
    const q = searchFriend.toLowerCase().trim()
    return acceptedFriends.filter((f) => !q || f.username.toLowerCase().includes(q))
  }, [acceptedFriends, searchFriend])

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
      {/* 1. Privatsphäre & Sichtbarkeit */}
      <section className="msm-card p-5 space-y-4" aria-labelledby="social-privacy-title">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary/10 text-primary">
            <Lock className="h-5 w-5" />
          </div>
          <div>
            <h2 id="social-privacy-title" className="text-sm font-semibold text-on-surface">
              Privatsphäre & Sichtbarkeit
            </h2>
          </div>
        </div>

        <div className="space-y-4 pt-2 border-t border-outline-variant/30">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
            <div>
              <span className="text-xs font-medium text-on-surface">Profil-Sichtbarkeit & Status</span>
              <p className="text-[11px] text-on-surface-variant">Wer darf deine Präsenz und Aktivitäten sehen?</p>
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

          <div className="flex items-center justify-between gap-3 pt-2 border-t border-outline-variant/20">
            <div>
              <span className="text-xs font-medium text-on-surface">Lesebestätigungen (Gelesen-Häkchen)</span>
              <p className="text-[11px] text-on-surface-variant">Zeigt Kontakten, sobald Nachrichten gelesen wurden.</p>
            </div>
            <Switch
              checked={readReceiptsEnabled}
              onCheckedChange={handleToggleReadReceipts}
              aria-label="Lesebestätigungen"
            />
          </div>
        </div>
      </section>

      {/* 2. Spielzeit / Nutzungszeit */}
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
            <span className="text-[10px] text-on-surface-variant">Gesamtaktivität</span>
          </div>
        </div>

        {stats?.active_time_by_category && Object.keys(stats.active_time_by_category).length > 0 && (
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-2.5 pt-2 border-t border-outline-variant/30">
            {Object.entries(stats.active_time_by_category).map(([cat, secs]) => (
              <div key={cat} className="p-2.5 rounded-xl bg-surface-container-low border border-outline-variant/30">
                <span className="text-[10px] font-bold text-on-surface-variant tracking-wider block truncate">
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

      {/* 3. Meilensteine & Erfolge */}
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
              <span className="text-[10px] text-on-surface-variant font-mono">
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
            onClick={() => setMilestoneFilter('all')}
            className="text-xs h-7 px-2.5"
          >
            Alle ({overview?.achievements.length || 0})
          </Button>
          <Button
            variant={milestoneFilter === 'unlocked' ? 'primary' : 'ghost'}
            size="sm"
            onClick={() => setMilestoneFilter('unlocked')}
            className="text-xs h-7 px-2.5"
          >
            Freigeschaltet ({overview?.total_unlocked || 0})
          </Button>
          <Button
            variant={milestoneFilter === 'locked' ? 'primary' : 'ghost'}
            size="sm"
            onClick={() => setMilestoneFilter('locked')}
            className="text-xs h-7 px-2.5"
          >
            Gesperrt ({(overview?.total_available || 0) - (overview?.total_unlocked || 0)})
          </Button>
        </div>

        {/* Milestones Grid */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
          {filteredMilestones.map((m) => {
            const rarity = m.rarity_percent ?? m.global_unlocked_percentage ?? 0
            const isRare = rarity > 0 && rarity <= 10
            return (
              <div
                key={m.id}
                className={`flex items-start gap-3 p-3 rounded-xl border transition-all ${
                  m.unlocked
                    ? 'bg-surface-container-low border-outline-variant/40 shadow-xs'
                    : 'bg-surface-container-lowest/40 border-outline-variant/20 opacity-55'
                }`}
              >
                <div
                  className={`w-9 h-9 rounded-xl flex items-center justify-center shrink-0 border ${
                    m.unlocked
                      ? isRare
                        ? 'bg-amber-500/20 border-amber-500/40 text-amber-300'
                        : 'bg-primary/15 border-primary/30 text-primary'
                      : 'bg-surface-container-high/50 border-outline-variant/20 text-on-surface-variant/40'
                  }`}
                >
                  {m.unlocked ? renderAchievementIcon(m.icon, 'w-4 h-4') : <Lock className="w-3.5 h-3.5" />}
                </div>

                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-1.5 flex-wrap">
                    <span className="text-xs font-bold text-on-surface truncate">{m.title}</span>
                    <span className="text-[10px] font-mono text-amber-400 font-semibold">+{m.points}</span>
                    {isRare && (
                      <Badge variant="warning" className="text-[9px] px-1 py-0 uppercase font-bold">
                        Selten
                      </Badge>
                    )}
                  </div>
                  <p className="text-[11px] text-on-surface-variant mt-0.5 line-clamp-2">
                    {m.description}
                  </p>
                  {m.unlocked && m.unlocked_at && (
                    <span className="inline-flex items-center gap-1 text-[10px] text-emerald-400 mt-1">
                      <CheckCircle2 className="w-3 h-3" />
                      <span>{new Date(m.unlocked_at).toLocaleDateString()}</span>
                    </span>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      </section>

      {/* 4. Freunde & Kontakte */}
      <section className="msm-card p-5 space-y-4" aria-labelledby="social-friends-title">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary/10 text-primary">
            <Users className="h-5 w-5" />
          </div>
          <div>
            <h2 id="social-friends-title" className="text-sm font-semibold text-on-surface">
              Freunde & Kontakte
            </h2>
          </div>
        </div>

        {/* Add Friend */}
        <form onSubmit={handleSendFriendRequest} className="flex gap-2 pt-2 border-t border-outline-variant/30">
          <Input
            value={addUsername}
            onChange={(e) => setAddUsername(e.target.value)}
            placeholder="Benutzername für Freundschaftsanfrage …"
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
            <span className="text-xs font-bold text-amber-400 block">
              Ausstehende Anfragen ({incomingRequests.length})
            </span>
            <div className="space-y-1.5">
              {incomingRequests.map((req) => (
                <div
                  key={req.id}
                  className="flex items-center justify-between p-2.5 rounded-xl bg-surface-container-high/50 border border-amber-500/30"
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
                      className="h-7 px-2 text-xs text-on-surface-variant hover:text-status-error"
                    >
                      Ablehnen
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Friend List */}
        <div className="space-y-2">
          {acceptedFriends.length > 3 && (
            <Input
              value={searchFriend}
              onChange={(e) => setSearchFriend(e.target.value)}
              placeholder="Kontakte filtern …"
              className="text-xs h-7"
            />
          )}

          {filteredFriends.length === 0 ? (
            <p className="text-xs text-on-surface-variant py-2">
              {searchFriend ? 'Keine Treffer.' : 'Noch keine Kontakte hinzugefügt.'}
            </p>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              {filteredFriends.map((f) => (
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
                        <p className="text-[10px] text-on-surface-variant truncate">
                          {f.presence.activity_label}
                        </p>
                      )}
                    </div>
                  </div>
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => void handleRemoveFriend(f.user_id ?? f.id)}
                    className="h-7 w-7 p-0 text-on-surface-variant hover:text-status-error shrink-0"
                    title="Kontakt entfernen"
                    aria-label="Kontakt entfernen"
                  >
                    <UserMinus className="w-3.5 h-3.5" />
                  </Button>
                </div>
              ))}
            </div>
          )}
        </div>
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
    // echter Sitzungen, und die Blase im Schaufenster folgte dann der
    // fremden Sitzung statt der geklickten Diagnose-Farbe.
    await emit(OVERLAY_ZUSTAND_TEST, neu).catch(() => {})
  }

  return (
    <section className="msm-card flex flex-col gap-4 p-5">
      <h2 className="text-sm font-medium text-on-surface">
        {isAndroid ? t('mss.einstellungen.tab.app', 'App-Status') : t('mss.einstellungen.desktopIntegration')}
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
                    toast.success(`Version v${res.neue_version} ist verfügbar.`)
                  } else {
                    toast.success('Maunting Smart System ist auf dem neuesten Stand.')
                  }
                } catch {
                  toast.error('Konnte nicht nach Updates suchen.')
                } finally {
                  setPrueftUpdate(false)
                }
              })()
            }}
          >
            {prueftUpdate ? 'Prüft...' : 'Auf Updates prüfen'}
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
      toast.error(t('mss.einstellungen.artefakte.ordnerFehler', 'Ordner konnte nicht ausgewählt werden.'))
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
              <p className="text-sm text-on-surface">{t('mss.einstellungen.artefakte.titel', 'Artefakt-Installationen & Quarantäne')}</p>
              {konfig?.artifact_install_aktiv ? (
                <Badge variant="success">
                  {t('mss.einstellungen.artefakte.statusAktiv', 'Aktiviert')}
                </Badge>
              ) : (
                <Badge variant="default">
                  {t('mss.einstellungen.artefakte.statusDeaktiviert', 'Deaktiviert')}
                </Badge>
              )}
            </div>
          </div>
          <Switch
            checked={konfig?.artifact_install_aktiv === true}
            disabled={konfig === null}
            onCheckedChange={(an) => void toggle(an)}
            aria-label={t('mss.einstellungen.artefakte.titel', 'Artefakt-Installationen')}
          />
        </div>

        {konfig?.artifact_install_aktiv && (
          <div className="mt-2 flex flex-col gap-4 rounded-xl border border-outline-variant/30 bg-surface-container-low/30 p-4">
            {/* Windows Sandbox Status */}
            <div className="flex items-center justify-between gap-2">
              <p className="text-xs font-medium text-on-surface">{t('mss.einstellungen.artefakte.sandboxTitel', 'Windows Sandbox')}</p>
              <Badge variant={sandboxOk ? 'success' : 'warning'}>
                {sandboxOk ? t('mss.einstellungen.artefakte.sandboxBereit', 'Bereit') : t('mss.einstellungen.artefakte.sandboxFehlt', 'Nicht verfügbar')}
              </Badge>
            </div>

            {/* Download Limit */}
            <div className="flex flex-col gap-2">
              <div className="flex items-center justify-between text-xs">
                <span className="text-on-surface font-medium">{t('mss.einstellungen.artefakte.downloadLimitTitel', 'Download-Limit pro Datei')}</span>
                <span className="font-mono text-primary">{limitGiB} GiB</span>
              </div>
              <Slider
                min={1}
                max={100}
                step={1}
                value={limitGiB}
                onValueChange={(val) => void downloadLimitAendern(val)}
                ariaLabel={t('mss.einstellungen.artefakte.downloadLimitTitel', 'Download-Limit')}
              />
            </div>

            {/* Freigegebene Suchwurzeln */}
            <div className="flex flex-col gap-2">
              <div className="flex items-center justify-between">
                <p className="text-xs font-medium text-on-surface">{t('mss.einstellungen.artefakte.suchwurzelnTitel', 'Suchbereiche')}</p>
                <Button variant="secondary" size="sm" onClick={() => void suchwurzelHinzufuegen()}>
                  {t('mss.einstellungen.artefakte.suchwurzelHinzufuegen', '+ Ordner freigeben')}
                </Button>
              </div>
              {konfig.search_roots.length === 0 ? (
                <p className="text-xs italic text-on-surface-variant/70">
                  {t('mss.einstellungen.artefakte.keineSuchwurzeln', 'Keine Ordner hinterlegt. Standard-Pfade werden automatisch erkannt.')}
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
          aria-label={t('mss.einstellungen.artefakte.aktivierenTitel', 'Artefakt-Installationen aktivieren')}
        >
          <div className="msm-card flex w-full max-w-md flex-col gap-4 p-5">
            <div className="flex items-center gap-2 text-status-warning">
              <AlertTriangle className="h-5 w-5 shrink-0" aria-hidden="true" />
              <h2 className="text-base font-semibold text-on-surface">
                {t('mss.einstellungen.artefakte.aktivierenTitel', 'Artefakt-Installationen aktivieren')}
              </h2>
            </div>
            <p className="text-xs leading-relaxed text-on-surface-variant">
              {t(
                'mss.einstellungen.artefakte.aktivierenWarnung',
                'Downloads durchlaufen Quarantäne, Defender-Prüfung und Sandbox vor der Ausführung.',
              )}
            </p>
            <div className="flex items-center justify-end gap-2 pt-2">
              <Button variant="ghost" size="sm" onClick={() => setDialogOffen(false)}>
                {t('mss.einstellungen.artefakte.abbrechen', 'Abbrechen')}
              </Button>
              <Button autoFocus size="sm" onClick={() => void bestaetigenAktivieren()}>
                {t('mss.einstellungen.artefakte.aktivierenBestaetigen', 'Aktivieren')}
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
            <h2 id="audio-devices-heading" className="font-headline text-lg font-semibold text-on-surface">
              {t('profile.audioTitle', 'Mikrofon & Audio')}
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
            {istAmTesten ? t('profile.audioActive', 'Test aktiv') : t('profile.audioInactive', 'Bereit')}
          </span>
        </div>

        <p className="max-w-2xl font-body-md text-sm leading-6 text-on-surface-variant mb-6">
          {t(
            'profile.audioDescription',
            'Konfiguriere deine Audio-Geräte für Sprachnachrichten, den KI-Sprachmodus und das Wake-Word. Änderungen werden einheitlich im gesamten System angewendet.'
          )}
        </p>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 max-w-2xl">
          <div className="space-y-1.5">
            <label className="block font-label-md text-label-md text-on-surface-variant uppercase tracking-wider">
              {t('profile.audioDeviceLabel', 'Eingabegerät (Mikrofon)')}
            </label>
            {auswahl('audio_eingabe', geraete?.eingaenge ?? [], geraete?.standard_eingang ?? null)}
          </div>

          <div className="space-y-1.5">
            <label className="block font-label-md text-label-md text-on-surface-variant uppercase tracking-wider">
              {t('mss.audio.ausgabe', 'Ausgabegerät (Lautsprecher)')}
            </label>
            {auswahl('audio_ausgabe', geraete?.ausgaenge ?? [], geraete?.standard_ausgang ?? null)}
          </div>
        </div>

        {!isAndroidClient && (
          <div className="flex items-center justify-between gap-3 border-t border-outline-variant/30 pt-4 mt-6 max-w-2xl">
            <div>
              <span className="text-xs font-medium text-on-surface block">{t('mss.audio.ducking', 'Audio-Ducking')}</span>
              <span className="text-[11px] text-on-surface-variant">
                Senkt Hintergrundgeräusche und Musik ab, während die KI spricht.
              </span>
            </div>
            <Button variant="secondary" size="sm" onClick={() => void duckingTesten()} disabled={duckt}>
              {duckt
                ? t('mss.einstellungen.duckingLaeuft', 'Ducking aktiv …')
                : t('mss.einstellungen.duckingTesten', 'Ducking testen')}
            </Button>
          </div>
        )}
      </section>

      {/* 2. Signalverarbeitung & Filter */}
      <section className="msm-card p-6" aria-labelledby="audio-processing-heading">
        <div className="flex items-center gap-2 mb-4">
          <Sliders className="h-5 w-5 text-secondary" aria-hidden="true" />
          <h2 id="audio-processing-heading" className="font-headline text-lg font-semibold text-on-surface">
            {t('mss.audio.verarbeitung', 'Signalverarbeitung & Filter')}
          </h2>
        </div>
        <p className="max-w-2xl font-body-md text-sm leading-6 text-on-surface-variant mb-5">
          Chromiums integrierte WebRTC-Filterreihe zur Beseitigung von Störgeräuschen und Hall in Sprachräumen und Sprachaufnahmen.
        </p>

        <div className="max-w-xl space-y-4">
          <div className="flex items-center justify-between gap-3 p-3 rounded-xl border border-outline-variant/30 bg-surface-container-low/40">
            <div>
              <span className="text-sm font-medium text-on-surface block">
                {t('profile.audioNoiseSuppression', 'Rauschunterdrückung (Noise Suppression)')}
              </span>
              <span className="text-xs text-on-surface-variant">
                Filtert Hintergrundgeräusche wie Lüfter oder Tastaturanschläge heraus.
              </span>
            </div>
            <Switch
              checked={konfig?.audio_rauschen ?? true}
              disabled={konfig === null}
              onCheckedChange={(an) => void verarbeitungSetzen('audio_rauschen', an)}
              aria-label={t('profile.audioNoiseSuppression', 'Rauschunterdrückung')}
            />
          </div>

          <div className="flex items-center justify-between gap-3 p-3 rounded-xl border border-outline-variant/30 bg-surface-container-low/40">
            <div>
              <span className="text-sm font-medium text-on-surface block">
                {t('profile.audioEchoCancellation', 'Echounterdrückung (Echo Cancellation)')}
              </span>
              <span className="text-xs text-on-surface-variant">
                Verhindert akustische Rückkopplungen bei Lautsprechern ohne Kopfhörer.
              </span>
            </div>
            <Switch
              checked={konfig?.audio_echo ?? true}
              disabled={konfig === null}
              onCheckedChange={(an) => void verarbeitungSetzen('audio_echo', an)}
              aria-label={t('profile.audioEchoCancellation', 'Echounterdrückung')}
            />
          </div>

          <div className="flex items-center justify-between gap-3 p-3 rounded-xl border border-outline-variant/30 bg-surface-container-low/40">
            <div>
              <span className="text-sm font-medium text-on-surface block">
                {t('profile.audioAutoGain', 'Automatische Pegelanpassung (Auto Gain)')}
              </span>
              <span className="text-xs text-on-surface-variant">
                Gleicht leise und laute Sprachpassagen automatisch an ein gesundes Niveau an.
              </span>
            </div>
            <Switch
              checked={konfig?.audio_autogain ?? true}
              disabled={konfig === null}
              onCheckedChange={(an) => void verarbeitungSetzen('audio_autogain', an)}
              aria-label={t('profile.audioAutoGain', 'Automatische Pegelanpassung')}
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
              label={t('mss.audio.verstaerkung', 'Software-Eingangsverstärkung')}
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
      setFehler(t('mss.audio.testhoerenFehler', 'Mikrofon konnte für den Test nicht gestartet werden.'))
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
        <h2 id="audio-test-heading" className="font-headline text-lg font-semibold text-on-surface">
          {t('mss.audio.testhoeren', 'Testhören & Mikrofon-Pegel')}
        </h2>
      </div>
      <p className="max-w-2xl font-body-md text-sm leading-6 text-on-surface-variant mb-5">
        {t(
          'profile.audioTestDescription',
          'Höre deine Stimme live über den gewählten Lautsprecher ab, um Klangqualität und Pegel zu kontrollieren. Die Echounterdrückung ist im Testlauf deaktiviert, damit deine Stimme nicht ausgefiltert wird.'
        )}
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
            <span>{laeuft ? t('profile.audioTestStop', 'Test beenden') : t('profile.audioTestStart', 'Testhören starten')}</span>
          </Button>

          <ProgressBar
            value={laeuft ? Math.round(pegel * 100) : null}
            ariaLabel={t('mss.audio.testhoerenPegel', 'Mikrofonpegel')}
            className="flex-1"
          />
        </div>

        {laeuft && (
          <div className="flex items-center justify-between text-xs px-1 text-on-surface-variant">
            <span>Pegel: {Math.round(pegel * 100)}%</span>
            <span className={pegel > 0.05 ? 'text-emerald-400 font-semibold' : 'text-on-surface-variant/60'}>
              {pegel > 0.05 ? t('profile.audioSignalDetected', 'Signal erkannt') : 'Kein Signal'}
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
              {t('mss.einstellungen.rechtliches.slogan', 'Maunting Studios — Sicherheit braucht Vertrauen')}
            </h2>
            <p className="mt-1 text-xs text-on-surface-variant">
              {t('mss.einstellungen.rechtliches.beschreibung', 'Vollständige Transparenz, echte Datenhoheit und kein unbemerktes Handeln auf Ihren Systemen.')}
            </p>
          </div>
        </div>
      </section>

      {/* Datenschutzerklärung */}
      <section className="msm-card flex flex-col gap-4 p-5">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-medium text-on-surface">
              {t('mss.einstellungen.rechtliches.datenschutzTitel', 'Datenschutzerklärung')}
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
            {t('mss.einstellungen.rechtliches.datenschutzOeffnen', 'Datenschutzerklärung öffnen')}
          </Button>
        </div>
      </section>

      {/* Impressum */}
      <section className="msm-card flex flex-col gap-4 p-5">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <h3 className="text-sm font-medium text-on-surface">
                {t('mss.einstellungen.rechtliches.impressumTitel', 'Betreiber-Impressum')}
              </h3>
              <Badge
                variant={legal.imprint_enabled && legal.imprint_url ? 'success' : 'default'}
              >
                {legal.imprint_enabled && legal.imprint_url
                  ? t('mss.einstellungen.rechtliches.impressumAktiv', 'Aktiviert')
                  : t('mss.einstellungen.rechtliches.impressumInaktiv', 'Nicht konfiguriert')}
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
              {t('mss.einstellungen.rechtliches.impressumOeffnen', 'Impressum im Browser öffnen')}
            </Button>
          )}
        </div>
      </section>
    </div>
  )
}

