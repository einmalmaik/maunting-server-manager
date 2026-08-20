import { useState, useMemo, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { useAuthStore } from '@/stores/authStore'
import { Mail, AlertTriangle, Clock, Globe, Save } from 'lucide-react'
import { Button, Dropdown, type DropdownOption } from '@/Singra/UI'
import { api } from '@/api/client'
import { toast } from '@/stores/toastStore'

import { getAvailableTimezones } from '@/utils/timeFormat'

/**
 * Tab: Account-Info (Username, E-Mail, Verify-Status) & Zeitzonen-Einstellung.
 */
export function AccountTab() {
  const { t } = useTranslation()
  const { user, updateUser } = useAuthStore()

  const browserZone = typeof Intl !== 'undefined' && typeof Intl.DateTimeFormat === 'function'
    ? Intl.DateTimeFormat().resolvedOptions().timeZone
    : null

  const [selectedZone, setSelectedZone] = useState<string>(
    user?.time_zone || browserZone || 'UTC',
  )
  const [saving, setSaving] = useState(false)
  const [dismissedBrowserHint, setDismissedBrowserHint] = useState(false)

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
    return allZones.map((tz) => ({ value: tz, label: tz }))
  }, [user?.time_zone])

  const showBrowserHint =
    !dismissedBrowserHint &&
    Boolean(browserZone) &&
    Boolean(user?.time_zone) &&
    user?.time_zone !== browserZone

  const handleSaveTimezone = async (zoneToSave?: string) => {
    const tz = zoneToSave || selectedZone
    setSaving(true)
    try {
      await api('/auth/me/timezone', {
        method: 'PATCH',
        body: JSON.stringify({ time_zone: tz }),
      })
      updateUser({ time_zone: tz })
      setSelectedZone(tz)
      setDismissedBrowserHint(true)
      toast.success(t('profile.timezoneSaved', 'Zeitzone gespeichert'))
    } catch (err: any) {
      toast.error(err.message || t('common.error'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-6">
      {/* Account Info */}
      <div className="msm-card p-6">
        <div className="flex items-center gap-2 mb-6">
          <Mail className="h-5 w-5 text-secondary" aria-hidden="true" />
          <h2 className="font-headline text-lg font-semibold text-on-surface">{t('auth.email')}</h2>
        </div>
        <div className="flex items-center gap-3">
          <div className="w-12 h-12 rounded-full bg-primary/10 flex items-center justify-center text-lg font-bold text-primary border border-outline-variant">
            {user?.username.charAt(0).toUpperCase()}
          </div>
          <div>
            <p className="font-label-md text-sm text-on-surface font-medium">{user?.username}</p>
            <p className="font-body-md text-sm text-on-surface-variant">{user?.email}</p>
            {user?.email_verified === false && (
              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs bg-status-error/10 text-status-error border border-status-error/30 mt-1">
                <AlertTriangle className="w-3 h-3" />
                {t('profile.notVerified', 'Nicht verifiziert')}
              </span>
            )}
          </div>
        </div>
      </div>

      {/* Zeitzonen-Einstellung */}
      <div className="msm-card p-6">
        <div className="flex items-center gap-2 mb-2">
          <Clock className="h-5 w-5 text-secondary" aria-hidden="true" />
          <h2 className="font-headline text-lg font-semibold text-on-surface">
            {t('profile.timezoneTitle', 'Zeitzone')}
          </h2>
        </div>
        <p className="font-body-md text-sm text-on-surface-variant mb-6">
          {t('profile.timezoneSubtitle', 'Kanonische Zeitzone für die KI, den Lagebericht und alle zeitgesteuerten Aufgaben.')}
        </p>

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
              {t('profile.timezoneLabel', 'Zeitzone (IANA)')}
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
            <p className="msm-field-help mt-1.5">
              {t('profile.timezoneHelp', 'Wähle deine regionale IANA-Zeitzone für exakte Uhrzeiten.')}
            </p>
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
    </div>
  )
}

