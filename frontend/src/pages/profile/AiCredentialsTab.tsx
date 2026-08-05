import { useEffect, useState } from 'react'
import { KeyRound, Save, Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { aiApi, type AiProviderAvailable } from '@/api/ai'
import { SanitizedApiError } from '@/api/client'
import { Button } from '@/Singra/UI'
import { confirm } from '@/stores/confirmStore'
import { toast } from '@/stores/toastStore'
import { AiMemoryManager } from '@/components/ai/AiMemoryManager'

export function AiCredentialsTab() {
  const { t } = useTranslation()
  const [providers, setProviders] = useState<AiProviderAvailable[]>([])
  const [keys, setKeys] = useState<Record<number, string>>({})
  const [busyId, setBusyId] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)

  const load = () => aiApi.listProviders().then(setProviders)
  useEffect(() => {
    let active = true
    aiApi.listProviders()
      .then((rows) => { if (active) setProviders(rows) })
      .catch((error: unknown) => {
        if (active) toast.error(error instanceof SanitizedApiError ? error.message : t('ai.credentials.errors.load'))
      })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [t])

  const save = async (provider: AiProviderAvailable) => {
    const value = keys[provider.id]?.trim()
    if (!value || busyId !== null) return
    setBusyId(provider.id)
    try {
      await aiApi.setCredential(provider.id, value)
      setKeys((current) => ({ ...current, [provider.id]: '' }))
      await load()
      toast.success(t('ai.credentials.saved'))
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('ai.credentials.errors.save'))
    } finally {
      setBusyId(null)
    }
  }

  const remove = async (provider: AiProviderAvailable) => {
    if (busyId !== null) return
    const accepted = await confirm({
      title: t('ai.credentials.deleteTitle'),
      message: t('ai.credentials.deleteConfirm', { name: provider.name }),
      confirmText: t('common.delete'),
      danger: true,
    })
    if (!accepted) return
    setBusyId(provider.id)
    try {
      await aiApi.deleteCredential(provider.id)
      await load()
      toast.success(t('ai.credentials.deleted'))
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('ai.credentials.errors.delete'))
    } finally {
      setBusyId(null)
    }
  }

  if (loading) return <div className="flex h-48 items-center justify-center"><div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" /></div>

  return (
    <section className="space-y-4" aria-labelledby="ai-credentials-title">
      <div className="msm-card p-6">
        <div className="flex items-center gap-2"><KeyRound className="h-5 w-5 text-secondary" /><h2 id="ai-credentials-title" className="font-headline text-lg font-semibold text-on-surface">{t('ai.credentials.title')}</h2></div>
        <p className="mt-2 max-w-3xl text-sm text-on-surface-variant">{t('ai.credentials.description')}</p>
      </div>
      {providers.length === 0 && <div className="msm-card p-6 text-sm text-on-surface-variant">{t('ai.credentials.empty')}</div>}
      {providers.map((provider) => (
        <form key={provider.id} className="msm-card space-y-4 p-6" onSubmit={(event) => { event.preventDefault(); void save(provider) }}>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div><h3 className="font-headline font-semibold text-on-surface">{provider.name}</h3><p className="mt-1 font-mono text-xs text-on-surface-variant">{provider.default_model}</p></div>
            <span className={provider.available ? 'msm-badge-success' : 'msm-badge-warning'}>{provider.available ? t('ai.credentials.available') : t('ai.credentials.keyRequired')}</span>
          </div>
          <label className="block space-y-1.5"><span className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">{t('ai.credentials.apiKey')}</span><input type="password" autoComplete="new-password" className="msm-input" value={keys[provider.id] ?? ''} onChange={(event) => setKeys((current) => ({ ...current, [provider.id]: event.target.value }))} placeholder={provider.user_key_configured ? t('ai.credentials.configured') : t('ai.credentials.placeholder')} /></label>
          <div className="flex flex-wrap justify-end gap-2">
            {provider.user_key_configured && <Button type="button" variant="destructive" disabled={busyId !== null} onClick={() => void remove(provider)}><Trash2 className="h-4 w-4" />{t('ai.credentials.remove')}</Button>}
            <Button type="submit" disabled={busyId !== null || !(keys[provider.id]?.trim())}><Save className="h-4 w-4" />{busyId === provider.id ? t('common.loading') : t('settings.save')}</Button>
          </div>
        </form>
      ))}
      <AiMemoryManager />
    </section>
  )
}
