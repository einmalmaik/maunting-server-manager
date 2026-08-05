import { Brain, Save, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { aiApi, type AiMemoryEntry } from '@/api/ai'
import { SanitizedApiError } from '@/api/client'
import { useHasPermission } from '@/hooks/useHasPermission'
import { Button, Switch } from '@/Singra/UI'
import { confirm } from '@/stores/confirmStore'
import { toast } from '@/stores/toastStore'

export function AiMemoryManager() {
  const { t } = useTranslation()
  const allowed = useHasPermission('ai.memory.use')
  const [entries, setEntries] = useState<AiMemoryEntry[]>([])
  const [enabled, setEnabled] = useState(true)
  const [key, setKey] = useState('')
  const [value, setValue] = useState('')
  const [busy, setBusy] = useState(false)

  const load = async () => {
    const [rows, preference] = await Promise.all([
      aiApi.listMemory('user'), aiApi.getMemoryPreference(),
    ])
    setEntries(rows)
    setEnabled(preference.enabled)
  }

  useEffect(() => {
    if (!allowed) return
    let active = true
    Promise.all([aiApi.listMemory('user'), aiApi.getMemoryPreference()])
      .then(([rows, preference]) => { if (active) { setEntries(rows); setEnabled(preference.enabled) } })
      .catch(() => { if (active) toast.error(t('ai.memory.errors.load')) })
    return () => { active = false }
  }, [allowed, t])

  if (!allowed) return null

  const save = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!key.trim() || !value.trim() || busy) return
    setBusy(true)
    try {
      await aiApi.saveMemory({ scope: 'user', key: key.trim(), value: value.trim() })
      setKey(''); setValue(''); await load()
      toast.success(t('ai.memory.saved'))
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('ai.memory.errors.save'))
    } finally { setBusy(false) }
  }

  const remove = async (entry: AiMemoryEntry) => {
    if (!await confirm({ message: t('ai.memory.deleteConfirm', { key: entry.key }), confirmText: t('common.delete'), danger: true })) return
    setBusy(true)
    try { await aiApi.deleteMemory(entry.id); await load() } catch { toast.error(t('ai.memory.errors.delete')) } finally { setBusy(false) }
  }

  return (
    <section className="msm-card space-y-5 p-6" aria-labelledby="ai-memory-title">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div><div className="flex items-center gap-2"><Brain className="h-5 w-5 text-secondary" /><h2 id="ai-memory-title" className="font-headline text-lg font-semibold text-on-surface">{t('ai.memory.title')}</h2></div><p className="mt-2 max-w-3xl text-sm text-on-surface-variant">{t('ai.memory.description')}</p></div>
        <label className="flex min-h-10 items-center gap-3 text-sm text-on-surface-variant"><span>{t('ai.memory.enabled')}</span><Switch checked={enabled} disabled={busy} onCheckedChange={(next) => { setBusy(true); void aiApi.setMemoryPreference(next).then(() => setEnabled(next)).catch(() => toast.error(t('ai.memory.errors.save'))).finally(() => setBusy(false)) }} aria-label={t('ai.memory.enabled')} /></label>
      </div>
      <div className="space-y-2">
        {entries.map((entry) => <div key={entry.id} className="flex items-start gap-3 rounded-xl border border-outline-variant/40 bg-surface-container-low/35 p-3"><div className="min-w-0 flex-1"><p className="font-mono text-xs font-semibold text-primary">{entry.key}</p><p className="mt-1 whitespace-pre-wrap break-words text-sm text-on-surface-variant">{entry.value}</p></div><Button type="button" size="sm" variant="ghost" disabled={busy} onClick={() => void remove(entry)} aria-label={t('ai.memory.delete')}><Trash2 className="h-4 w-4" /></Button></div>)}
        {entries.length === 0 && <p className="rounded-xl border border-dashed border-outline-variant/50 px-4 py-5 text-sm text-on-surface-variant">{t('ai.memory.empty')}</p>}
      </div>
      <form className="grid gap-3 md:grid-cols-[14rem_minmax(0,1fr)_auto]" onSubmit={save}><input className="msm-input" pattern="[A-Za-z0-9_.-]+" maxLength={64} value={key} onChange={(event) => setKey(event.target.value)} placeholder={t('ai.memory.key')} aria-label={t('ai.memory.key')} /><input className="msm-input" maxLength={2000} value={value} onChange={(event) => setValue(event.target.value)} placeholder={t('ai.memory.value')} aria-label={t('ai.memory.value')} /><Button type="submit" disabled={busy || !key.trim() || !value.trim()}><Save className="h-4 w-4" />{t('settings.save')}</Button></form>
      <p className="text-xs text-on-surface-variant">{t('ai.memory.secretHint')}</p>
    </section>
  )
}
