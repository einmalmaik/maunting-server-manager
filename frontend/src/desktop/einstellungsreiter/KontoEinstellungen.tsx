import { useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, Camera, Clock, Globe, MapPin, Save, Trash2, User } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { api } from '@/api/client'
import { Avatar, Badge, Button, Dropdown, type DropdownOption, Switch } from '@/Singra/UI'
import { useAuthStore } from '@/stores/authStore'
import { toast } from '@/stores/toastStore'
import { getAvailableTimezones } from '@/utils/timeFormat'

export function KontoEinstellungen() {
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
