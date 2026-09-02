import { useEffect, useState } from 'react'
import { Shield } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { aiApi } from '@/api/ai'
import { SanitizedApiError } from '@/api/client'
import { Switch } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'

/**
 * Modulare Verknüpfung zwischen KI und der Guardian Engine.
 *
 * Maunting Studios Grundsatz: „Sicherheit braucht Vertrauen“ / Datensparsamkeit.
 *
 * Ist die Integration deaktiviert (Standard), arbeitet die Guardian Engine vollkommen
 * isoliert und autark ohne KI — es werden keine KI-Token verbraucht, keine automatischen
 * Heilungsläufe gestartet und im Chat stehen keine Guardian-Werkzeuge bereit.
 */
export function AiGuardianSettings({ canWrite }: { canWrite: boolean }) {
  const { t } = useTranslation()
  const [enabled, setEnabled] = useState(false)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let active = true
    aiApi
      .getGuardianPolicy()
      .then((status) => {
        if (active) setEnabled(status.enabled)
      })
      .catch(() => {
        if (active) toast.error(t('aiSettings.guardian.errors.load'))
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [t])

  const toggle = async (next: boolean) => {
    if (!canWrite || busy) return
    setBusy(true)
    try {
      const updated = await aiApi.setGuardianPolicy(next)
      setEnabled(updated.enabled)
      toast.success(t('aiSettings.guardian.saved'))
    } catch (error: unknown) {
      toast.error(
        error instanceof SanitizedApiError ? error.message : t('aiSettings.guardian.errors.save'),
      )
    } finally {
      setBusy(false)
    }
  }

  if (loading) return null

  return (
    <section className="msm-card space-y-4 p-6" aria-labelledby="ai-guardian-settings-title">
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-2">
          <Shield className="h-5 w-5 text-primary" aria-hidden="true" />
          <h3
            id="ai-guardian-settings-title"
            className="font-headline text-lg font-semibold text-on-surface"
          >
            {t('aiSettings.guardian.title')}
          </h3>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-xs font-medium text-on-surface-variant">
            {enabled
              ? t('aiSettings.guardian.statusEnabled')
              : t('aiSettings.guardian.statusDisabled')}
          </span>
          <Switch
            checked={enabled}
            disabled={!canWrite || busy}
            onCheckedChange={toggle}
            aria-label={t('aiSettings.guardian.title')}
          />
        </div>
      </div>
      <p className="max-w-3xl text-sm leading-relaxed text-on-surface-variant">
        {t('aiSettings.guardian.description')}
      </p>
    </section>
  )
}
