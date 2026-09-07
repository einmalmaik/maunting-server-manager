import { useState, useMemo, useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { useAuthStore } from '@/stores/authStore'
import { Mail, AlertTriangle, Clock, Globe, MapPin, Save, ShieldCheck, Trash2, Camera, Loader2, Shield } from 'lucide-react'
import { Avatar, Button, Dropdown, type DropdownOption } from '@/Singra/UI'
import { api } from '@/api/client'
import { updatePrivacy } from '@/api/social'
import { toast } from '@/stores/toastStore'

import { getAvailableTimezones } from '@/utils/timeFormat'

/**
 * Tab: Account-Info (Username, E-Mail, Verify-Status, Profilbild) & Zeitzonen-Einstellung.
 */
export function AccountTab() {
  const { t } = useTranslation()
  const { user, updateUser } = useAuthStore()

  const fileInputRef = useRef<HTMLInputElement>(null)
  const [uploadingAvatar, setUploadingAvatar] = useState(false)

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
      const res = await api<any>('/auth/me/avatar', {
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
      await api<any>('/auth/me/avatar', { method: 'DELETE' })
      updateUser({ avatar_url: null })
      toast.success(t('profile.avatarRemoved', 'Profilbild wurde entfernt.'))
    } catch (err: any) {
      toast.error(err?.detail || t('profile.avatarRemoveFailed', 'Profilbild konnte nicht entfernt werden.'))
    } finally {
      setUploadingAvatar(false)
    }
  }

  const browserZone = typeof Intl !== 'undefined' && typeof Intl.DateTimeFormat === 'function'
    ? Intl.DateTimeFormat().resolvedOptions().timeZone
    : null

  const [selectedZone, setSelectedZone] = useState<string>(
    user?.time_zone || browserZone || 'UTC',
  )
  const [saving, setSaving] = useState(false)
  const [dismissedBrowserHint, setDismissedBrowserHint] = useState(false)
  const [savingLocationSharing, setSavingLocationSharing] = useState(false)
  const [locationSharingError, setLocationSharingError] = useState<string | null>(null)

  const [privacyLevel, setPrivacyLevel] = useState<'public' | 'friends' | 'private'>(
    (user?.social_privacy as 'public' | 'friends' | 'private') || 'friends'
  )
  const [savingPrivacy, setSavingPrivacy] = useState(false)

  useEffect(() => {
    if (user?.social_privacy) {
      setPrivacyLevel(user.social_privacy as 'public' | 'friends' | 'private')
    }
  }, [user?.social_privacy])

  const handleSavePrivacy = async (levelToSave?: 'public' | 'friends' | 'private') => {
    const level = levelToSave || privacyLevel
    setSavingPrivacy(true)
    try {
      const res = await updatePrivacy({ privacy: level })
      const validPrivacy = (res.social_privacy === 'public' || res.social_privacy === 'friends' || res.social_privacy === 'private')
        ? res.social_privacy
        : level
      updateUser({ social_privacy: validPrivacy })
      setPrivacyLevel(validPrivacy)
      toast.success(t('profile.privacySaved', 'Privatsphäre-Einstellungen gespeichert.'))
    } catch {
      toast.error(t('profile.privacySaveFailed', 'Einstellungen konnten nicht gespeichert werden.'))
    } finally {
      setSavingPrivacy(false)
    }
  }

  const privacyOptions: DropdownOption[] = [
    { value: 'friends', label: 'Nur Freunde (Status für Kontakte)' },
    { value: 'public', label: 'Öffentlich (Status für alle Panel-Benutzer)' },
    { value: 'private', label: 'Privat (Unsichtbar / Status verborgen)' },
  ]

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
    setSaving(true)
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
      setSaving(false)
    }
  }

  const requestBrowserLocationPermission = () => new Promise<void>((resolve, reject) => {
    if (!navigator.geolocation) {
      reject(new Error('UNSUPPORTED'))
      return
    }

    navigator.geolocation.getCurrentPosition(
      // Die Position wird bewusst nicht entgegengenommen: Die Konto-Einstellung
      // speichert ausschließlich die Einwilligung. Eine konkrete Position gehört
      // nur in den jeweiligen KI-Lauf und darf nie in diesem Profilzustand landen.
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
          t('profile.locationSharingPermissionError', 'Der Standortzugriff wurde nicht freigegeben. Du kannst ihn in den Browser- oder App-Einstellungen erlauben.'),
        )
      } else {
        setLocationSharingError(t('profile.locationSharingSaveError', 'Die Standortfreigabe konnte nicht gespeichert werden.'))
      }
    } finally {
      setSavingLocationSharing(false)
    }
  }

  return (
    <div className="space-y-6">
      {/* Account Info & Profilbild */}
      <div className="msm-card p-6">
        <div className="flex items-center gap-2 mb-6">
          <Mail className="h-5 w-5 text-secondary" aria-hidden="true" />
          <h2 className="font-headline text-lg font-semibold text-on-surface">{t('auth.email', 'Konto & Profilbild')}</h2>
        </div>

        <div className="flex flex-col sm:flex-row items-start sm:items-center gap-6">
          {/* Avatar with Upload Hover / Button */}
          <div className="relative group">
            <Avatar
              src={user?.avatar_url}
              name={user?.username}
              size="xl"
            />
            {uploadingAvatar && (
              <div className="absolute inset-0 rounded-full bg-black/60 flex items-center justify-center">
                <Loader2 className="w-6 h-6 text-primary animate-spin" />
              </div>
            )}
          </div>

          <div className="space-y-3 flex-1">
            <div>
              <p className="font-label-md text-base text-on-surface font-semibold">{user?.username}</p>
              <p className="font-body-md text-sm text-on-surface-variant">{user?.email}</p>
              {user?.email_verified === false && (
                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs bg-status-error/10 text-status-error border border-status-error/30 mt-1.5">
                  <AlertTriangle className="w-3 h-3" />
                  {t('profile.notVerified', 'Nicht verifiziert')}
                </span>
              )}
            </div>

            {/* Avatar Action Buttons */}
            <div className="flex flex-wrap items-center gap-2.5 pt-1">
              <input
                ref={fileInputRef}
                type="file"
                accept="image/png,image/jpeg,image/webp,image/gif"
                className="sr-only"
                disabled={uploadingAvatar}
                onChange={(e) => void handleAvatarChange(e.target.files?.[0])}
              />
              <Button
                type="button"
                variant="secondary"
                size="sm"
                disabled={uploadingAvatar}
                onClick={() => fileInputRef.current?.click()}
                className="flex items-center gap-1.5"
              >
                <Camera className="w-4 h-4 text-primary" aria-hidden="true" />
                {user?.avatar_url ? t('profile.changeAvatar', 'Profilbild ändern') : t('profile.uploadAvatar', 'Profilbild hochladen')}
              </Button>

              {user?.avatar_url && (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  disabled={uploadingAvatar}
                  onClick={() => void handleDeleteAvatar()}
                  className="flex items-center gap-1.5 text-status-error hover:text-status-error hover:bg-error-container/20"
                >
                  <Trash2 className="w-4 h-4" aria-hidden="true" />
                  {t('profile.removeAvatar', 'Entfernen')}
                </Button>
              )}
            </div>
            <p className="text-xs text-on-surface-variant/70">
              {t('profile.avatarHint', 'PNG, JPG, WebP oder GIF (max. 5 MB).')}
            </p>
          </div>
        </div>
      </div>

      {/* Zeitzonen-Einstellung */}
      <div className="msm-card p-6">
        <div className="flex items-center gap-2 mb-6">
          <Clock className="h-5 w-5 text-secondary" aria-hidden="true" />
          <h2 className="font-headline text-lg font-semibold text-on-surface">
            {t('profile.timezoneTitle', 'Zeitzone')}
          </h2>
        </div>

        {showBrowserHint && browserZone && (
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-primary/30 bg-primary/10 p-3 text-sm text-on-surface">
            <div className="flex items-center gap-2">
              <Globe className="h-4 w-4 text-primary shrink-0" aria-hidden="true" />
              <span>
                {t('profile.timezoneBrowserHint', 'Dein Browser nutzt {{zone}}, im Profil ist jedoch {{current}} gespeichert.', {
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
                disabled={saving}
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

        <div className="max-w-md space-y-4">
          <div>
            <label
              htmlFor="profile-timezone"
              className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider"
            >
              {t('profile.timezoneLabel', 'Zeitzone')}
            </label>
            <Dropdown
              id="profile-timezone"
              value={selectedZone}
              onChange={setSelectedZone}
              options={timezoneOptions}
              searchable={true}
              searchPlaceholder={t('profile.timezoneSearch', 'Zeitzone suchen …')}
              placeholder={t('profile.timezonePlaceholder', 'Zeitzone auswählen')}
              aria-label={t('profile.timezoneLabel', 'Zeitzone')}
            />
          </div>

          <div>
            <Button
              type="button"
              variant="primary"
              disabled={saving || (selectedZone === user?.time_zone && Boolean(user?.time_zone))}
              onClick={() => void handleSaveTimezone()}
            >
              <Save className="mr-1.5 h-4 w-4" aria-hidden="true" />
              {saving ? t('common.saving', 'Speichern …') : t('profile.timezoneSave', 'Zeitzone speichern')}
            </Button>
          </div>
        </div>
      </div>

      <section className="msm-card p-6" aria-labelledby="location-sharing-title">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="flex min-w-0 items-start gap-3">
            <div className={`mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border ${
              user?.location_sharing_enabled
                ? 'border-primary/30 bg-primary/10 text-primary'
                : 'border-outline-variant bg-surface-container text-on-surface-variant'
            }`}>
              <MapPin className="h-4 w-4" aria-hidden="true" />
            </div>
            <div>
              <h2 id="location-sharing-title" className="font-headline text-lg font-semibold text-on-surface">
                {t('profile.locationSharingTitle', 'Standort für KI-Anfragen')}
              </h2>
              <p className="mt-1 max-w-2xl font-body-md text-sm leading-6 text-on-surface-variant">
                {t('profile.locationSharingDescription', 'Wird nur bei ortsbezogenen KI-Anfragen verwendet.')}
              </p>
            </div>
          </div>
          <span className={`inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs font-medium ${
            user?.location_sharing_enabled
              ? 'border-status-success/30 bg-status-success/10 text-status-success'
              : 'border-outline-variant bg-surface-container text-on-surface-variant'
          }`}>
            <ShieldCheck className="h-3.5 w-3.5" aria-hidden="true" />
            {user?.location_sharing_enabled
              ? t('profile.locationSharingEnabled', 'Freigegeben')
              : t('profile.locationSharingDisabled', 'Nicht freigegeben')}
          </span>
        </div>

        <div className="mt-4 border-l-2 border-primary/40 pl-3">
          <p className="font-body-md text-xs leading-5 text-on-surface-variant">
            {t('profile.locationSharingPrivacy', 'Keine dauerhafte Speicherung von Koordinaten im Konto.')}
          </p>
        </div>

        {locationSharingError && (
          <p className="mt-4 text-sm text-status-error" role="alert">
            {locationSharingError}
          </p>
        )}

        <div className="mt-5">
          <Button
            type="button"
            variant={user?.location_sharing_enabled ? 'secondary' : 'primary'}
            disabled={savingLocationSharing}
            onClick={() => void handleLocationSharingChange(!user?.location_sharing_enabled)}
          >
            {savingLocationSharing
              ? t('common.saving', 'Speichern …')
              : user?.location_sharing_enabled
                ? t('profile.locationSharingDisable', 'Standortfreigabe deaktivieren')
                : t('profile.locationSharingEnable', 'Standortfreigabe aktivieren')}
          </Button>
        </div>
      </section>

      {/* Privatsphäre & Sichtbarkeit */}
      <section className="msm-card p-6" aria-labelledby="privacy-settings-title">
        <div className="flex items-center gap-2 mb-4">
          <Shield className="h-5 w-5 text-secondary" aria-hidden="true" />
          <h2 id="privacy-settings-title" className="font-headline text-lg font-semibold text-on-surface">
            {t('profile.privacyTitle', 'Privatsphäre & Sichtbarkeit')}
          </h2>
        </div>
        <p className="max-w-2xl font-body-md text-sm leading-6 text-on-surface-variant mb-4">
          {t('profile.privacyDescription', 'Bestimme, wer deinen Online-Status und deine Aktivitäten im System sehen kann.')}
        </p>

        <div className="max-w-md space-y-4">
          <div>
            <label
              htmlFor="profile-privacy-dropdown"
              className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider"
            >
              {t('profile.privacyVisibility', 'Profil-Sichtbarkeit')}
            </label>
            <Dropdown
              id="profile-privacy-dropdown"
              value={privacyLevel}
              onChange={(val: string) => {
                const next = val as 'public' | 'friends' | 'private'
                setPrivacyLevel(next)
                void handleSavePrivacy(next)
              }}
              options={privacyOptions}
              disabled={savingPrivacy}
              aria-label={t('profile.privacyVisibility', 'Profil-Sichtbarkeit')}
            />
          </div>
          <p className="text-xs text-on-surface-variant/70">
            {privacyLevel === 'private'
              ? 'Im privaten Modus wird dein Status für andere als offline angezeigt.'
              : privacyLevel === 'friends'
              ? 'Nur bestätigte Freunde sehen dein Gerät und deinen aktuellen Status.'
              : 'Jedes Mitglied im Panel kann deinen Status sehen.'}
          </p>
        </div>
      </section>
    </div>
  )
}
