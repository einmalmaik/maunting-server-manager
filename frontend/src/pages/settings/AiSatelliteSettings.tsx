import { useEffect, useState } from 'react'
import { Globe2, Save, Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { aiApi } from '@/api/ai'
import { SanitizedApiError } from '@/api/client'
import { Button } from '@/Singra/UI'
import { confirm } from '@/stores/confirmStore'
import { toast } from '@/stores/toastStore'

/**
 * Zugangsdaten für das Copernicus Data Space Ecosystem (CDSE / Sentinel).
 *
 * Die Zugangsdaten verlassen das Backend nie — die API meldet ausschließlich,
 * ob sie hinterlegt sind.
 */
export function AiSatelliteSettings({ canWrite }: { canWrite: boolean }) {
  const { t } = useTranslation()
  const [configured, setConfigured] = useState(false)
  const [clientId, setClientId] = useState('')
  const [clientSecret, setClientSecret] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let active = true
    aiApi.getSatelliteStatus()
      .then((status) => { if (active) setConfigured(status.configured) })
      .catch(() => { if (active) toast.error(t('ai.satellite.errors.load')) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [t])

  const save = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!canWrite || busy || !clientId.trim() || !clientSecret.trim()) return
    setBusy(true)
    try {
      const status = await aiApi.setSatelliteCredentials(clientId.trim(), clientSecret.trim())
      setConfigured(status.configured)
      setClientId('')
      setClientSecret('')
      toast.success(t('ai.satellite.saved'))
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('ai.satellite.errors.save'))
    } finally {
      setBusy(false)
    }
  }

  const remove = async () => {
    if (!canWrite || busy) return
    if (!await confirm({
      message: t('ai.satellite.removeConfirm'),
      confirmText: t('common.delete'),
      danger: true,
    })) return
    setBusy(true)
    try {
      const status = await aiApi.setSatelliteCredentials('', '')
      setConfigured(status.configured)
      toast.success(t('ai.satellite.removed'))
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('ai.satellite.errors.save'))
    } finally {
      setBusy(false)
    }
  }

  if (loading) return null

  return (
    <section className="msm-card space-y-4 p-6" aria-labelledby="ai-satellite-title">
      <div className="flex items-center gap-2">
        <Globe2 className="h-5 w-5 text-secondary" aria-hidden="true" />
        <h3 id="ai-satellite-title" className="font-headline text-lg font-semibold text-on-surface">
          {t('ai.satellite.title')}
        </h3>
      </div>
      <p className="max-w-3xl text-sm text-on-surface-variant">
        {t('ai.satellite.description')}
      </p>

      <form className="space-y-3" onSubmit={save}>
        <div className="flex flex-wrap gap-3">
          <label className="min-w-[14rem] flex-1 space-y-1.5">
            <span className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
              {t('ai.satellite.clientId')}
            </span>
            <input
              className="msm-input"
              type="text"
              autoComplete="off"
              maxLength={256}
              value={clientId}
              disabled={!canWrite || busy}
              placeholder={configured ? t('ai.satellite.configured') : t('ai.satellite.clientIdPlaceholder')}
              onChange={(event) => setClientId(event.target.value)}
              aria-label={t('ai.satellite.clientId')}
            />
          </label>

          <label className="min-w-[14rem] flex-1 space-y-1.5">
            <span className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
              {t('ai.satellite.clientSecret')}
            </span>
            <input
              className="msm-input"
              type="password"
              autoComplete="new-password"
              maxLength={512}
              value={clientSecret}
              disabled={!canWrite || busy}
              placeholder={configured ? t('ai.satellite.configured') : t('ai.satellite.clientSecretPlaceholder')}
              onChange={(event) => setClientSecret(event.target.value)}
              aria-label={t('ai.satellite.clientSecret')}
            />
          </label>
        </div>

        {canWrite && (
          <div className="flex items-center gap-2 pt-1">
            <Button type="submit" disabled={busy || !clientId.trim() || !clientSecret.trim()}>
              <Save className="h-4 w-4" aria-hidden="true" />
              {t('settings.save')}
            </Button>
            {configured && (
              <Button type="button" variant="destructive" disabled={busy} onClick={() => void remove()}>
                <Trash2 className="h-4 w-4" aria-hidden="true" />
                {t('common.delete')}
              </Button>
            )}
          </div>
        )}
      </form>

      <p className="text-xs text-on-surface-variant">{t('ai.satellite.hint')}</p>
    </section>
  )
}
