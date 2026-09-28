import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { AlertTriangle, Search, Settings2 } from 'lucide-react'
import { Badge, Button, Dropdown, Input } from '@/Singra/UI'
import { FormDialog } from './FormDialog'
import { useStudio } from './StudioContext'
import { ErrorBox, Loading, Section, useLoad } from './shared'
import type { StudioParameter } from './studioApi'

/** postgresql.conf der eigenen Instanz: ALTER SYSTEM + pg_reload_conf(). */
export function InstanceTab() {
  const { t } = useTranslation()
  const { api, revision, runOperation } = useStudio()
  const parameters = useLoad(() => api.parameters(), [api, revision])
  const [search, setSearch] = useState('')
  const [category, setCategory] = useState('all')
  const [onlyChanged, setOnlyChanged] = useState(false)
  const [edit, setEdit] = useState<StudioParameter | null>(null)

  const categories = useMemo(() => [...new Set((parameters.data || []).map((p) => p.category))].sort(), [parameters.data])
  const list = (parameters.data || []).filter(
    (p) =>
      (category === 'all' || p.category === category) &&
      (!onlyChanged || p.source === 'configuration file' || p.setting !== p.boot_val) &&
      (p.name.includes(search.toLowerCase()) || p.description.toLowerCase().includes(search.toLowerCase())),
  )
  const pendingRestart = (parameters.data || []).filter((p) => p.pending_restart)

  return (
    <div className="space-y-4">
      {pendingRestart.length > 0 && (
        <p className="flex items-start gap-2 rounded-lg border border-status-warning/40 bg-status-warning/10 p-3 text-sm text-status-warning" data-testid="restart-needed">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          {t('postgresStudio.instance.restartNeeded', { names: pendingRestart.map((p) => p.name).join(', ') })}
        </p>
      )}
      <Section
        title={<span className="inline-flex items-center gap-2"><Settings2 className="h-4 w-4" />{t('postgresStudio.instance.title')}</span>}
        actions={
          <>
            <Input prefix={<Search className="h-3.5 w-3.5" />} value={search} onChange={(e) => setSearch(e.target.value)} placeholder={t('postgresStudio.instance.search')} aria-label={t('postgresStudio.instance.search')} />
            <Dropdown value={category} onChange={setCategory} options={[{ value: 'all', label: t('postgresStudio.instance.allCategories') }, ...categories.map((c) => ({ value: c, label: c }))]} searchable className="w-64" aria-label={t('postgresStudio.instance.category')} />
            <Button size="sm" variant={onlyChanged ? 'primary' : 'ghost'} onClick={() => setOnlyChanged((v) => !v)}>
              {t('postgresStudio.instance.onlyChanged')}
            </Button>
          </>
        }
      >
        <p className="mb-3 text-xs text-on-surface-variant">{t('postgresStudio.instance.hint')}</p>
        <ErrorBox message={parameters.error} />
        {parameters.loading && !parameters.data && <Loading />}
        <div className="max-h-[65vh] overflow-auto">
          <table className="w-full text-left text-xs" data-testid="parameters">
            <thead className="sticky top-0 bg-surface-container text-on-surface-variant">
              <tr className="border-b border-outline-variant">
                <th className="py-2 pr-3 font-medium">{t('postgresStudio.instance.name')}</th>
                <th className="py-2 pr-3 font-medium">{t('postgresStudio.instance.value')}</th>
                <th className="py-2 pr-3 font-medium">{t('postgresStudio.instance.description')}</th>
                <th className="py-2" />
              </tr>
            </thead>
            <tbody>
              {list.map((parameter) => (
                <tr key={parameter.name} className="border-b border-outline-variant/50 align-top">
                  <td className="py-1.5 pr-3">
                    <span className="font-mono text-on-surface">{parameter.name}</span>
                    <span className="mt-0.5 flex flex-wrap gap-1">
                      {parameter.context === 'postmaster' && <Badge variant="warning">{t('postgresStudio.instance.needsRestart')}</Badge>}
                      {parameter.source === 'configuration file' && <Badge variant="info">{t('postgresStudio.instance.changed')}</Badge>}
                      {parameter.pending_restart && <Badge variant="destructive">{t('postgresStudio.instance.pending')}</Badge>}
                    </span>
                  </td>
                  <td className="py-1.5 pr-3 font-mono text-secondary">
                    {parameter.setting}
                    {parameter.unit ? ` ${parameter.unit}` : ''}
                  </td>
                  <td className="py-1.5 pr-3 text-on-surface-variant">{parameter.description}</td>
                  <td className="whitespace-nowrap py-1.5 text-right">
                    {parameter.editable && !['internal', 'backend', 'superuser-backend'].includes(parameter.context) ? (
                      <>
                        <Button size="sm" variant="ghost" onClick={() => setEdit(parameter)}>{t('common.edit')}</Button>
                        {parameter.source === 'configuration file' && (
                          <Button size="sm" variant="ghost" onClick={() => void runOperation({ op: 'reset_parameter', name: parameter.name }, { title: t('postgresStudio.instance.reset', { name: parameter.name }) })}>
                            {t('common.reset')}
                          </Button>
                        )}
                      </>
                    ) : (
                      !parameter.editable && <span className="text-label-sm text-on-surface-variant">{t('postgresStudio.instance.locked')}</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>
      {edit && <ParameterDialog parameter={edit} onClose={() => setEdit(null)} />}
    </div>
  )
}

function ParameterDialog({ parameter, onClose }: { parameter: StudioParameter; onClose: () => void }) {
  const { t } = useTranslation()
  const { runOperation } = useStudio()
  const [value, setValue] = useState(() => withUnit(parameter))
  const title = t('postgresStudio.instance.edit', { name: parameter.name })
  const range = parameter.min_val != null && parameter.max_val != null ? `${parameter.min_val} … ${parameter.max_val}${parameter.unit ? ` (${parameter.unit})` : ''}` : null
  return (
    <FormDialog
      open
      title={title}
      description={parameter.description}
      onClose={onClose}
      onSubmit={() => void runOperation({ op: 'set_parameter', name: parameter.name, value }, { title }).then((ok) => ok && onClose())}
      testId="parameter-dialog"
    >
      {parameter.enumvals ? (
        <Dropdown value={value} onChange={setValue} options={parameter.enumvals.map((v) => ({ value: v, label: v }))} aria-label={parameter.name} />
      ) : parameter.vartype === 'bool' ? (
        <Dropdown value={value} onChange={setValue} options={['on', 'off'].map((v) => ({ value: v, label: v }))} aria-label={parameter.name} />
      ) : (
        <Input value={value} onChange={(e) => setValue(e.target.value)} className="font-mono" aria-label={parameter.name} />
      )}
      {range && <p className="text-xs text-on-surface-variant">{t('postgresStudio.instance.range', { range })}</p>}
      <p className="text-xs text-on-surface-variant">{t('postgresStudio.instance.default', { value: parameter.boot_val ?? '–' })}</p>
      {parameter.context === 'postmaster' && <p className="text-xs text-status-warning">{t('postgresStudio.instance.restartHint')}</p>}
    </FormDialog>
  )
}

/**
 * Wert mit Einheit, so wie ALTER SYSTEM ihn versteht. Manche Einheiten tragen
 * einen Faktor (shared_buffers: „8kB"): 16384 × 8kB sind 131072kB, nicht 16384kB.
 */
export function withUnit(parameter: Pick<StudioParameter, 'setting' | 'unit'>): string {
  const { setting, unit } = parameter
  if (!unit || !/^-?\d+$/.test(setting)) return setting
  const match = unit.match(/^(\d*)(\D+)$/)
  if (!match) return setting
  const factor = match[1] ? Number(match[1]) : 1
  return `${Number(setting) * factor}${match[2]}`
}
