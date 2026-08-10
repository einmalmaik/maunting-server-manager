import { Brain, BrainCircuit, Pencil, Plus, Save, Trash2, X } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { aiApi, type AiMemoryEntry } from '@/api/ai'
import { SanitizedApiError } from '@/api/client'
import { useHasPermission } from '@/hooks/useHasPermission'
import { Button, Switch } from '@/Singra/UI'
import { confirm } from '@/stores/confirmStore'
import { toast } from '@/stores/toastStore'

import { AiKnowledgeShell } from './AiKnowledgeShell'
import { type AiKnowledgeScope, scopeCanManage, scopeTeamId } from './knowledgeScope'

type Herkunft = 'all' | 'user' | 'ai'

interface Props {
  /** Ohne Angabe: das eigene Gedächtnis. */
  scope?: AiKnowledgeScope
}

/**
 * Erinnerungen einsehen und pflegen — eigene oder die eines Teams.
 *
 * Vorher war das eine flache Liste mit einem Formular darunter. Das trägt, bis
 * die KI zwanzig Einträge abgeleitet hat; danach findet man nichts mehr und
 * kann nur noch einzeln löschen. Deshalb: Suche, Herkunftsfilter, Bearbeiten an
 * Ort und Stelle und ein „alles löschen" für den Fall, dass man wirklich
 * aufräumen will.
 *
 * Die Herkunft ist der wichtigste Filter, nicht ein hübsches Extra: „was hat
 * sich die KI über mich gemerkt?" ist eine andere Frage als „was habe ich ihr
 * gesagt?", und nur die erste beantwortet niemand sonst.
 */
export function AiMemoryManager({ scope = { kind: 'user' } }: Props) {
  const { t } = useTranslation()
  const allowed = useHasPermission('ai.memory.use')
  const darfAendern = scopeCanManage(scope)
  const teamId = scopeTeamId(scope)

  const [entries, setEntries] = useState<AiMemoryEntry[]>([])
  const [enabled, setEnabled] = useState(false)
  const [suche, setSuche] = useState('')
  const [herkunft, setHerkunft] = useState<Herkunft>('all')
  const [key, setKey] = useState('')
  const [value, setValue] = useState('')
  const [bearbeitet, setBearbeitet] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const laden = async () => {
    const rows = teamId === undefined
      ? await aiApi.listMemory('user')
      : await aiApi.listMemory('team', undefined, teamId)
    setEntries(rows)
  }

  useEffect(() => {
    if (!allowed) return
    let active = true
    setSuche(''); setHerkunft('all'); setKey(''); setValue(''); setBearbeitet(null)
    Promise.all([
      teamId === undefined ? aiApi.listMemory('user') : aiApi.listMemory('team', undefined, teamId),
      // Der Schalter ist eine Einstellung des Benutzers, kein Merkmal des
      // Bereichs — er wird auch in der Teamansicht geladen, aber nur in der
      // persönlichen angezeigt.
      aiApi.getMemoryPreference(),
    ])
      .then(([rows, preference]) => {
        if (!active) return
        setEntries(rows)
        setEnabled(preference.enabled)
      })
      .catch(() => { if (active) toast.error(t('ai.memory.errors.load')) })
    return () => { active = false }
  }, [allowed, teamId, t])

  const sichtbar = useMemo(() => {
    const nadel = suche.trim().toLowerCase()
    return entries
      .filter((entry) => herkunft === 'all' || entry.origin === herkunft)
      .filter((entry) => !nadel
        || entry.key.toLowerCase().includes(nadel)
        || entry.value.toLowerCase().includes(nadel))
      // Zuletzt genutzt zuerst: was die KI gerade heranzieht, ist das, was man
      // prüfen will. Nie genutzte Einträge stehen hinten, nach Schlüssel sortiert.
      .sort((a, b) => {
        const links = a.last_used_at ? Date.parse(a.last_used_at) : 0
        const rechts = b.last_used_at ? Date.parse(b.last_used_at) : 0
        return rechts - links || a.key.localeCompare(b.key)
      })
  }, [entries, herkunft, suche])

  if (!allowed) return null

  const speichern = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!key.trim() || !value.trim() || busy) return
    setBusy(true)
    try {
      await aiApi.saveMemory(teamId === undefined
        ? { scope: 'user', key: key.trim(), value: value.trim() }
        : { scope: 'team', team_id: teamId, key: key.trim(), value: value.trim() })
      setKey(''); setValue(''); setBearbeitet(null)
      await laden()
      toast.success(t('ai.memory.saved'))
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('ai.memory.errors.save'))
    } finally { setBusy(false) }
  }

  const entfernen = async (entry: AiMemoryEntry) => {
    if (!await confirm({
      message: t('ai.memory.deleteConfirm', { key: entry.key }),
      confirmText: t('common.delete'), danger: true,
    })) return
    setBusy(true)
    try {
      await aiApi.deleteMemory(entry.id)
      if (bearbeitet === entry.id) { setKey(''); setValue(''); setBearbeitet(null) }
      await laden()
    } catch { toast.error(t('ai.memory.errors.delete')) } finally { setBusy(false) }
  }

  const allesEntfernen = async () => {
    // Die Anzahl steht in der Frage: „alles löschen" ist bei drei Einträgen
    // etwas anderes als bei achtzig, und nur der Benutzer weiß, welches er
    // gerade meint.
    if (!await confirm({
      title: t('ai.memory.clearTitle'),
      message: t('ai.memory.clearConfirm', { count: entries.length }),
      confirmText: t('common.delete'), danger: true,
    })) return
    setBusy(true)
    try {
      const { removed } = await aiApi.clearMemory(teamId === undefined ? 'user' : 'team', teamId)
      setKey(''); setValue(''); setBearbeitet(null)
      await laden()
      toast.success(t('ai.memory.cleared', { count: removed }))
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('ai.memory.errors.delete'))
    } finally { setBusy(false) }
  }

  const bearbeiten = (entry: AiMemoryEntry) => {
    setKey(entry.key); setValue(entry.value); setBearbeitet(entry.id)
  }

  const herkunftsFilter = (
    <div className="flex flex-wrap gap-1.5" role="group" aria-label={t('ai.memory.filterOrigin')}>
      {(['all', 'user', 'ai'] as const).map((wert) => (
        <button
          key={wert}
          type="button"
          aria-pressed={herkunft === wert}
          onClick={() => setHerkunft(wert)}
          className={`rounded-full border px-3 py-1.5 text-xs transition-colors ${
            herkunft === wert
              ? 'border-primary/60 bg-primary/15 text-primary'
              : 'border-outline-variant/50 text-on-surface-variant hover:text-on-surface'
          }`}
        >
          {t(`ai.memory.origins.${wert}`)}
        </button>
      ))}
    </div>
  )

  return (
    <AiKnowledgeShell
      icon={Brain}
      // Die Überschrift folgt dem Bereich. Sie lautete auch über dem Wissen
      // eines Teams „Persönliches KI-Memory" — genau die Verwechslung, die
      // persönlich und geteilt zusammenrühren lässt.
      title={scope.kind === 'team' ? t('ai.memory.teamTitle') : t('ai.memory.title')}
      description={scope.kind === 'team' ? t('ai.memory.teamDescription') : t('ai.memory.description')}
      headerAction={scope.kind === 'user' ? (
        <label className="flex min-h-10 items-center gap-3 text-sm text-on-surface-variant">
          <span>{t('ai.memory.enabled')}</span>
          <Switch
            checked={enabled}
            disabled={busy}
            onCheckedChange={(next) => {
              setBusy(true)
              void aiApi.setMemoryPreference(next)
                .then(() => setEnabled(next))
                .catch(() => toast.error(t('ai.memory.errors.save')))
                .finally(() => setBusy(false))
            }}
            aria-label={t('ai.memory.enabled')}
          />
        </label>
      ) : undefined}
      search={entries.length > 3
        ? { value: suche, onChange: setSuche, label: t('ai.memory.search') }
        : undefined}
      filters={(
        <>
          {entries.length > 3 && herkunftsFilter}
          {darfAendern && entries.length > 0 && (
            <Button type="button" variant="ghost" size="sm" disabled={busy} onClick={() => void allesEntfernen()}>
              <Trash2 className="h-4 w-4" aria-hidden="true" />
              {t('ai.memory.clearAll')}
            </Button>
          )}
        </>
      )}
      count={entries.length > 3
        ? t('ai.memory.count', { shown: sichtbar.length, total: entries.length })
        : undefined}
    >
      <div className="space-y-2">
        {sichtbar.map((entry) => (
          <div
            key={entry.id}
            className={`flex items-start gap-3 rounded-xl border p-3 transition-colors ${
              bearbeitet === entry.id
                ? 'border-primary/50 bg-primary/5'
                : 'border-outline-variant/40 bg-surface-container-low/35'
            }`}
          >
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <p className="font-mono text-xs font-semibold text-primary">{entry.key}</p>
                {/* Herkunft sichtbar machen: niemand soll raten müssen, ob er
                    das selbst hinterlegt hat oder ob die KI es abgeleitet hat. */}
                {entry.origin === 'ai' && (
                  <span className="inline-flex items-center gap-1 rounded-full border border-outline-variant/50 px-2 py-0.5 text-[10px] text-on-surface-variant">
                    <BrainCircuit className="h-3 w-3" aria-hidden="true" />
                    {t('ai.memory.originAi')}
                  </span>
                )}
                {entry.use_count > 0 && (
                  <span className="text-[10px] text-on-surface-variant/70">
                    {t('ai.memory.usedCount', { count: entry.use_count })}
                  </span>
                )}
              </div>
              <p className="mt-1 whitespace-pre-wrap break-words text-sm text-on-surface-variant">{entry.value}</p>
            </div>
            {darfAendern && (
              <div className="flex shrink-0 gap-1">
                <Button
                  type="button" size="sm" variant="ghost" disabled={busy}
                  onClick={() => bearbeiten(entry)}
                  aria-label={`${t('ai.memory.edit')}: ${entry.key}`}
                >
                  <Pencil className="h-4 w-4" aria-hidden="true" />
                </Button>
                <Button
                  type="button" size="sm" variant="ghost" disabled={busy}
                  onClick={() => void entfernen(entry)}
                  aria-label={`${t('ai.memory.delete')}: ${entry.key}`}
                >
                  <Trash2 className="h-4 w-4" aria-hidden="true" />
                </Button>
              </div>
            )}
          </div>
        ))}
        {sichtbar.length === 0 && (
          <p className="rounded-xl border border-dashed border-outline-variant/50 px-4 py-5 text-sm text-on-surface-variant">
            {entries.length === 0 ? t('ai.memory.empty') : t('ai.memory.noMatches')}
          </p>
        )}
      </div>

      {darfAendern && (
        <form className="space-y-3 rounded-xl border border-outline-variant/40 bg-surface-container-low/35 p-4" onSubmit={speichern}>
          <div className="grid gap-3 md:grid-cols-[14rem_minmax(0,1fr)]">
            <label className="space-y-1.5">
              <span className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
                {t('ai.memory.key')}
              </span>
              <input
                className="msm-input" pattern="[A-Za-z0-9_.-]+" maxLength={64}
                value={key} disabled={busy || bearbeitet !== null}
                onChange={(event) => setKey(event.target.value)}
                placeholder="ram-praeferenz"
                aria-label={t('ai.memory.key')}
              />
            </label>
            <label className="space-y-1.5">
              <span className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
                {t('ai.memory.value')}
              </span>
              <input
                className="msm-input" maxLength={2000} value={value} disabled={busy}
                onChange={(event) => setValue(event.target.value)}
                aria-label={t('ai.memory.value')}
              />
            </label>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button type="submit" disabled={busy || !key.trim() || !value.trim()}>
              {bearbeitet === null
                ? <Plus className="h-4 w-4" aria-hidden="true" />
                : <Save className="h-4 w-4" aria-hidden="true" />}
              {bearbeitet === null ? t('ai.memory.add') : t('settings.save')}
            </Button>
            {bearbeitet !== null && (
              <Button
                type="button" variant="secondary" disabled={busy}
                onClick={() => { setKey(''); setValue(''); setBearbeitet(null) }}
              >
                <X className="h-4 w-4" aria-hidden="true" />
                {t('common.cancel')}
              </Button>
            )}
            <span className="text-xs text-on-surface-variant">{t('ai.memory.secretHint')}</span>
          </div>
        </form>
      )}
    </AiKnowledgeShell>
  )
}
