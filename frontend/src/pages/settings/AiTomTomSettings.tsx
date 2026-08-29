import { useEffect, useState, type FormEvent } from 'react'
import { Car, Save, Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { aiApi } from '@/api/ai'
import { SanitizedApiError } from '@/api/client'
import { Button } from '@/Singra/UI'
import { confirm } from '@/stores/confirmStore'
import { toast } from '@/stores/toastStore'

/** Der TomTom-Schlüssel bleibt verschlüsselt im Backend und wird nie zurückgelesen. */
export function AiTomTomSettings({ canWrite }: { canWrite: boolean }) {
  const { t } = useTranslation()
  const [configured, setConfigured] = useState(false)
  const [apiKey, setApiKey] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let active = true
    aiApi.getTomTomStatus()
      .then((status) => {
        if (active) setConfigured(status.configured)
      })
      .catch(() => {
        if (active) toast.error(t('ai.tomtom.errors.load'))
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [t])

  const save = async (event: FormEvent) => {
    event.preventDefault()
    if (!canWrite || busy || !apiKey.trim()) return
    setBusy(true)
    try {
      const status = await aiApi.setTomTomKey(apiKey.trim())
      setConfigured(status.configured)
      setApiKey('')
      toast.success(t('ai.tomtom.saved'))
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('ai.tomtom.errors.save'))
    } finally {
      setBusy(false)
    }
  }

  const remove = async () => {
    if (
      !canWrite
      || busy
      || !await confirm({
        message: t('ai.tomtom.removeConfirm'),
        confirmText: t('common.delete'),
        danger: true,
      })
    ) return
    setBusy(true)
    try {
      const status = await aiApi.setTomTomKey('')
      setConfigured(status.configured)
      toast.success(t('ai.tomtom.removed'))
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('ai.tomtom.errors.save'))
    } finally {
      setBusy(false)
    }
  }

  if (loading) return null

  return (
    <section className="msm-card space-y-4 p-6" aria-labelledby="ai-tomtom-title">
      <div className="flex items-center gap-2">
        <Car className="h-5 w-5 text-secondary" aria-hidden="true" />
        <h3 id="ai-tomtom-title" className="font-headline text-lg font-semibold text-on-surface">
          {t('ai.tomtom.title')}
        </h3>
      </div>
      <p className="max-w-3xl text-sm text-on-surface-variant">{t('ai.tomtom.description')}</p>
      <form className="space-y-3" onSubmit={save}>
        <label className="block max-w-2xl space-y-1.5">
          <span className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
            {t('ai.tomtom.apiKey')}
          </span>
          <input
            className="msm-input"
            type="password"
            autoComplete="new-password"
            maxLength={512}
            value={apiKey}
            disabled={!canWrite || busy}
            placeholder={configured ? t('ai.tomtom.configured') : t('ai.tomtom.placeholder')}
            onChange={(event) => setApiKey(event.target.value)}
            aria-label={t('ai.tomtom.apiKey')}
          />
        </label>
        {canWrite && (
          <div className="flex items-center gap-2 pt-1">
            <Button type="submit" disabled={busy || !apiKey.trim()}>
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
      <p className="text-xs text-on-surface-variant">{t('ai.tomtom.hint')}</p>
    </section>
  )
}
