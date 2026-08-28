import { useEffect, useState } from 'react'
import { Map, Save, Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { aiApi } from '@/api/ai'
import { SanitizedApiError } from '@/api/client'
import { Button } from '@/Singra/UI'
import { confirm } from '@/stores/confirmStore'
import { toast } from '@/stores/toastStore'

/** Optionaler Browser-Key. Ohne ihn bleibt die Sentinel-Ansicht unveraendert. */
export function AiMapTilerSettings({ canWrite }: { canWrite: boolean }) {
  const { t } = useTranslation()
  const [configured, setConfigured] = useState(false)
  const [apiKey, setApiKey] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  useEffect(() => { let active = true; aiApi.getMapTilerStatus().then((s) => { if (active) setConfigured(s.configured) }).catch(() => { if (active) toast.error(t('ai.maptiler.errors.load')) }).finally(() => { if (active) setLoading(false) }); return () => { active = false } }, [t])
  const save = async (event: React.FormEvent) => { event.preventDefault(); if (!canWrite || busy || !apiKey.trim()) return; setBusy(true); try { const status = await aiApi.setMapTilerKey(apiKey.trim()); setConfigured(status.configured); setApiKey(''); toast.success(t('ai.maptiler.saved')) } catch (error: unknown) { toast.error(error instanceof SanitizedApiError ? error.message : t('ai.maptiler.errors.save')) } finally { setBusy(false) } }
  const remove = async () => { if (!canWrite || busy || !await confirm({ message: t('ai.maptiler.removeConfirm'), confirmText: t('common.delete'), danger: true })) return; setBusy(true); try { const status = await aiApi.setMapTilerKey(''); setConfigured(status.configured); toast.success(t('ai.maptiler.removed')) } catch (error: unknown) { toast.error(error instanceof SanitizedApiError ? error.message : t('ai.maptiler.errors.save')) } finally { setBusy(false) } }
  if (loading) return null
  return <section className="msm-card space-y-4 p-6" aria-labelledby="ai-maptiler-title"><div className="flex items-center gap-2"><Map className="h-5 w-5 text-secondary" aria-hidden="true" /><h3 id="ai-maptiler-title" className="font-headline text-lg font-semibold text-on-surface">{t('ai.maptiler.title')}</h3></div><p className="max-w-3xl text-sm text-on-surface-variant">{t('ai.maptiler.description')}</p><form className="space-y-3" onSubmit={save}><label className="block max-w-2xl space-y-1.5"><span className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">{t('ai.maptiler.apiKey')}</span><input className="msm-input" type="password" autoComplete="new-password" maxLength={512} value={apiKey} disabled={!canWrite || busy} placeholder={configured ? t('ai.maptiler.configured') : t('ai.maptiler.placeholder')} onChange={(event) => setApiKey(event.target.value)} aria-label={t('ai.maptiler.apiKey')} /></label>{canWrite && <div className="flex items-center gap-2 pt-1"><Button type="submit" disabled={busy || !apiKey.trim()}><Save className="h-4 w-4" aria-hidden="true" />{t('settings.save')}</Button>{configured && <Button type="button" variant="destructive" disabled={busy} onClick={() => void remove()}><Trash2 className="h-4 w-4" aria-hidden="true" />{t('common.delete')}</Button>}</div>}</form><p className="text-xs text-on-surface-variant">{t('ai.maptiler.hint')}</p></section>
}
