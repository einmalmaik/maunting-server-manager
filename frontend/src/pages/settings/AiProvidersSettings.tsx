import { useEffect, useState } from 'react'
import { KeyRound, Plus, Save, Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { aiApi, type AiProviderAdmin, type AiProviderWrite } from '@/api/ai'
import { SanitizedApiError } from '@/api/client'
import { Button, Switch } from '@/Singra/UI'
import { confirm } from '@/stores/confirmStore'
import { toast } from '@/stores/toastStore'

interface ProviderDraft extends AiProviderWrite {
  id?: number
  operator_key_configured?: boolean
  operator_key_hint?: string | null
}

const EMPTY_PROVIDER: ProviderDraft = {
  name: '',
  base_url: '',
  default_model: '',
  enabled: true,
  requires_api_key: true,
  allow_private_network: false,
  operator_api_key: '',
}

function toDraft(provider: AiProviderAdmin): ProviderDraft {
  return {
    id: provider.id,
    name: provider.name,
    base_url: provider.base_url,
    default_model: provider.default_model,
    enabled: provider.enabled,
    requires_api_key: provider.requires_api_key,
    allow_private_network: provider.allow_private_network,
    operator_api_key: '',
    operator_key_configured: provider.operator_key_configured,
    operator_key_hint: provider.operator_key_hint,
  }
}

export function AiProvidersSettings({ canWrite }: { canWrite: boolean }) {
  const { t } = useTranslation()
  const [providers, setProviders] = useState<ProviderDraft[]>([])
  const [creating, setCreating] = useState(false)
  const [loading, setLoading] = useState(true)
  const [busyId, setBusyId] = useState<number | 'new' | null>(null)

  useEffect(() => {
    let active = true
    aiApi.listProviderSettings()
      .then((rows) => { if (active) setProviders(rows.map(toDraft)) })
      .catch((error: unknown) => {
        if (active) toast.error(error instanceof SanitizedApiError ? error.message : t('ai.providers.errors.load'))
      })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [t])

  const update = (index: number, patch: Partial<ProviderDraft>) => {
    setProviders((current) => current.map((item, itemIndex) => (
      itemIndex === index ? { ...item, ...patch } : item
    )))
  }

  const save = async (draft: ProviderDraft, index?: number) => {
    const target = draft.id ?? 'new'
    if (!canWrite || busyId !== null) return
    setBusyId(target)
    const payload: AiProviderWrite = {
      name: draft.name.trim(),
      base_url: draft.base_url.trim(),
      default_model: draft.default_model.trim(),
      enabled: draft.enabled,
      requires_api_key: draft.requires_api_key,
      allow_private_network: draft.allow_private_network,
      ...(draft.operator_api_key ? { operator_api_key: draft.operator_api_key } : {}),
      ...(draft.clear_operator_api_key ? { clear_operator_api_key: true } : {}),
    }
    try {
      const saved = draft.id
        ? await aiApi.updateProvider(draft.id, payload)
        : await aiApi.createProvider(payload)
      if (index === undefined) {
        setProviders((current) => [...current, toDraft(saved)])
        setCreating(false)
      } else {
        update(index, toDraft(saved))
      }
      toast.success(t('ai.providers.saved'))
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('ai.providers.errors.save'))
    } finally {
      setBusyId(null)
    }
  }

  const remove = async (provider: ProviderDraft) => {
    if (!provider.id || !canWrite || busyId !== null) return
    const accepted = await confirm({
      title: t('ai.providers.deleteTitle'),
      message: t('ai.providers.deleteConfirm', { name: provider.name }),
      confirmText: t('common.delete'),
      danger: true,
    })
    if (!accepted) return
    setBusyId(provider.id)
    try {
      await aiApi.deleteProvider(provider.id)
      setProviders((current) => current.filter((item) => item.id !== provider.id))
      toast.success(t('ai.providers.deleted'))
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('ai.providers.errors.delete'))
    } finally {
      setBusyId(null)
    }
  }

  if (loading) {
    return <div className="flex h-32 items-center justify-center"><div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" /></div>
  }

  return (
    <section className="space-y-4" aria-labelledby="ai-provider-title">
      <div className="msm-card flex flex-wrap items-start justify-between gap-4 p-6">
        <div className="max-w-3xl">
          <div className="flex items-center gap-2">
            <KeyRound className="h-5 w-5 text-secondary" aria-hidden="true" />
            <h3 id="ai-provider-title" className="font-headline text-lg font-semibold text-on-surface">{t('ai.providers.title')}</h3>
          </div>
          <p className="mt-2 text-sm text-on-surface-variant">{t('ai.providers.description')}</p>
        </div>
        {canWrite && !creating && (
          <Button type="button" variant="secondary" onClick={() => setCreating(true)}>
            <Plus className="h-4 w-4" aria-hidden="true" />{t('ai.providers.add')}
          </Button>
        )}
      </div>

      {providers.map((provider, index) => (
        <ProviderForm
          key={provider.id}
          draft={provider}
          disabled={!canWrite || busyId !== null}
          saving={busyId === provider.id}
          onChange={(patch) => update(index, patch)}
          onSave={() => void save(provider, index)}
          onDelete={() => void remove(provider)}
        />
      ))}
      {providers.length === 0 && !creating && (
        <div className="msm-card p-6 text-sm text-on-surface-variant">{t('ai.providers.empty')}</div>
      )}
      {creating && (
        <ProviderForm
          draft={EMPTY_PROVIDER}
          disabled={busyId !== null}
          saving={busyId === 'new'}
          onChange={() => undefined}
          localDraft
          onSaveDraft={(draft) => void save(draft)}
          onCancel={() => setCreating(false)}
        />
      )}
    </section>
  )
}

function ProviderForm({
  draft: initialDraft,
  disabled,
  saving,
  localDraft = false,
  onChange,
  onSave,
  onSaveDraft,
  onDelete,
  onCancel,
}: {
  draft: ProviderDraft
  disabled: boolean
  saving: boolean
  localDraft?: boolean
  onChange: (patch: Partial<ProviderDraft>) => void
  onSave?: () => void
  onSaveDraft?: (draft: ProviderDraft) => void
  onDelete?: () => void
  onCancel?: () => void
}) {
  const { t } = useTranslation()
  const [local, setLocal] = useState<ProviderDraft>({ ...initialDraft })
  const draft = localDraft ? local : initialDraft
  const change = (patch: Partial<ProviderDraft>) => {
    if (localDraft) setLocal((current) => ({ ...current, ...patch }))
    else onChange(patch)
  }
  const valid = Boolean(draft.name.trim() && draft.base_url.trim() && draft.default_model.trim())

  return (
    <form className="msm-card space-y-5 p-6" onSubmit={(event) => {
      event.preventDefault()
      if (localDraft) onSaveDraft?.(draft)
      else onSave?.()
    }}>
      <fieldset disabled={disabled} className="grid grid-cols-1 gap-4 border-0 p-0 md:grid-cols-2">
        <ProviderInput label={t('ai.providers.name')} value={draft.name} onChange={(name) => change({ name })} />
        <ProviderInput label={t('ai.providers.model')} value={draft.default_model} onChange={(default_model) => change({ default_model })} />
        <ProviderInput className="md:col-span-2" type="url" label={t('ai.providers.baseUrl')} value={draft.base_url} onChange={(base_url) => change({ base_url })} />
        <ProviderInput
          className="md:col-span-2"
          type="password"
          autoComplete="new-password"
          label={t('ai.providers.operatorKey')}
          value={draft.operator_api_key ?? ''}
          placeholder={draft.operator_key_configured ? t('ai.providers.keyConfigured', { hint: draft.operator_key_hint ?? '••••' }) : t('ai.providers.keyOptional')}
          disabled={draft.clear_operator_api_key}
          onChange={(operator_api_key) => change({ operator_api_key, clear_operator_api_key: false })}
        />
        {draft.operator_key_configured && (
          <div className="md:col-span-2">
            <Toggle label={t('ai.providers.clearOperatorKey')} checked={Boolean(draft.clear_operator_api_key)} onChange={(clear_operator_api_key) => change({ clear_operator_api_key, operator_api_key: '' })} />
          </div>
        )}
        <Toggle label={t('ai.providers.enabled')} checked={draft.enabled} onChange={(enabled) => change({ enabled })} />
        <Toggle label={t('ai.providers.requiresKey')} checked={draft.requires_api_key} onChange={(requires_api_key) => change({ requires_api_key })} />
        <div className="md:col-span-2 rounded-xl border border-outline-variant/40 bg-surface-container-low/35 p-4">
          <Toggle label={t('ai.providers.privateNetwork')} checked={draft.allow_private_network} onChange={(allow_private_network) => change({ allow_private_network })} />
          <p className="mt-2 text-xs text-on-surface-variant">{t('ai.providers.privateNetworkHint')}</p>
        </div>
      </fieldset>
      <div className="flex flex-wrap justify-end gap-2">
        {onDelete && <Button type="button" variant="destructive" disabled={disabled} onClick={onDelete}><Trash2 className="h-4 w-4" />{t('common.delete')}</Button>}
        {onCancel && <Button type="button" variant="ghost" disabled={disabled} onClick={onCancel}>{t('common.cancel')}</Button>}
        <Button type="submit" disabled={disabled || !valid}><Save className="h-4 w-4" />{saving ? t('common.loading') : t('settings.save')}</Button>
      </div>
    </form>
  )
}

function ProviderInput({ label, value, onChange, className = '', ...props }: {
  label: string
  value: string
  onChange: (value: string) => void
  className?: string
} & Omit<React.InputHTMLAttributes<HTMLInputElement>, 'value' | 'onChange'>) {
  return <label className={`space-y-1.5 ${className}`}><span className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">{label}</span><input className="msm-input" value={value} onChange={(event) => onChange(event.target.value)} {...props} /></label>
}

function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (value: boolean) => void }) {
  return <label className="flex min-h-10 items-center justify-between gap-4 text-sm text-on-surface"><span>{label}</span><Switch checked={checked} onCheckedChange={onChange} /></label>
}
