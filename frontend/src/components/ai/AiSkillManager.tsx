import { ArrowDown, ArrowUp, Plus, Save, ShieldCheck, Trash2, Workflow } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { aiApi, latestAiSkillVersions, type AiSkill, type AiSkillStep } from '@/api/ai'
import { SanitizedApiError } from '@/api/client'
import { useHasPermission } from '@/hooks/useHasPermission'
import { Button, Dropdown, Switch } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'

type ToolName =
  | 'read_server_status'
  | 'read_server_capacity'
  | 'read_server_logs'
  | 'read_config'
  | 'propose_server_lifecycle'
  | 'propose_backup'

interface SkillDraft {
  skill_key: string
  name: string
  description: string
  steps: AiSkillStep[]
  enabled: boolean
}

const EMPTY_DRAFT: SkillDraft = {
  skill_key: '',
  name: '',
  description: '',
  steps: [{ tool_name: 'read_server_status', arguments: {} }],
  enabled: true,
}

const TOOL_NAMES: ToolName[] = [
  'read_server_status',
  'read_server_capacity',
  'read_server_logs',
  'read_config',
  'propose_server_lifecycle',
  'propose_backup',
]

function defaultArguments(toolName: ToolName): Record<string, unknown> {
  if (toolName === 'read_server_logs') return { lines: 100 }
  if (toolName === 'read_config') return { path: '' }
  if (toolName === 'propose_server_lifecycle') return { operation: 'restart' }
  return {}
}

function toDraft(skill: AiSkill): SkillDraft {
  return {
    skill_key: skill.skill_key,
    name: skill.name,
    description: skill.description,
    steps: skill.steps.map((step) => ({ ...step, arguments: { ...step.arguments } })),
    enabled: skill.enabled,
  }
}

/**
 * Verwaltet ausschließlich versionierte, fest erlaubte MSM-Schritte. Freie
 * Skripte oder beliebige Argument-Schemata werden bewusst nicht angeboten.
 */
export function AiSkillManager() {
  const { t } = useTranslation()
  const allowed = useHasPermission('ai.skills.manage')
  const [skills, setSkills] = useState<AiSkill[]>([])
  const [selectedKey, setSelectedKey] = useState<string | null>(null)
  const [draft, setDraft] = useState<SkillDraft>(EMPTY_DRAFT)
  const [loading, setLoading] = useState(allowed)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!allowed) return
    let active = true
    aiApi.listManagedSkills()
      .then((rows) => {
        if (!active) return
        const latestSkills = latestAiSkillVersions(rows)
        setSkills(latestSkills)
        if (latestSkills[0]) {
          setSelectedKey(latestSkills[0].skill_key)
          setDraft(toDraft(latestSkills[0]))
        }
      })
      .catch(() => { if (active) toast.error(t('ai.skills.errors.load')) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [allowed, t])

  if (!allowed) return null

  const selectSkill = (skillKey: string) => {
    const selected = skills.find((skill) => skill.skill_key === skillKey)
    if (!selected) return
    setSelectedKey(skillKey)
    setDraft(toDraft(selected))
  }

  const startNew = () => {
    setSelectedKey(null)
    setDraft({ ...EMPTY_DRAFT, steps: [{ tool_name: 'read_server_status', arguments: {} }] })
  }

  const updateStep = (index: number, step: AiSkillStep) => {
    setDraft((current) => ({
      ...current,
      steps: current.steps.map((item, itemIndex) => itemIndex === index ? step : item),
    }))
  }

  const moveStep = (index: number, direction: -1 | 1) => {
    const target = index + direction
    if (target < 0 || target >= draft.steps.length) return
    setDraft((current) => {
      const steps = [...current.steps]
      ;[steps[index], steps[target]] = [steps[target], steps[index]]
      return { ...current, steps }
    })
  }

  const valid = Boolean(
    /^[a-z0-9][a-z0-9_.-]*$/.test(draft.skill_key)
    && draft.name.trim()
    && draft.description.trim()
    && draft.steps.length > 0
    && draft.steps.every((step) => {
      if (step.tool_name === 'read_config') return Boolean(String(step.arguments.path ?? '').trim())
      if (step.tool_name === 'read_server_logs') {
        const lines = step.arguments.lines
        return typeof lines === 'number' && Number.isInteger(lines) && lines >= 1 && lines <= 200
      }
      return true
    }),
  )

  const save = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!valid || busy) return
    setBusy(true)
    const payload = {
      skill_key: draft.skill_key,
      name: draft.name.trim(),
      description: draft.description.trim(),
      steps: draft.steps,
      enabled: draft.enabled,
    }
    try {
      const saved = selectedKey
        ? await aiApi.updateSkill(selectedKey, payload)
        : await aiApi.createSkill(payload)
      const rows = latestAiSkillVersions(await aiApi.listManagedSkills())
      setSkills(rows)
      setSelectedKey(saved.skill_key)
      setDraft(toDraft(saved))
      toast.success(t(selectedKey ? 'ai.skills.updated' : 'ai.skills.created', { version: saved.version }))
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('ai.skills.errors.save'))
    } finally {
      setBusy(false)
    }
  }

  if (loading) {
    return <div className="flex h-32 items-center justify-center" aria-label={t('common.loading')}><div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" /></div>
  }

  return (
    <section className="space-y-4" aria-labelledby="ai-skills-title">
      <div className="msm-card flex flex-wrap items-start justify-between gap-4 p-6">
        <div className="max-w-3xl">
          <div className="flex items-center gap-2">
            <Workflow className="h-5 w-5 text-secondary" aria-hidden="true" />
            <h2 id="ai-skills-title" className="font-headline text-lg font-semibold text-on-surface">{t('ai.skills.title')}</h2>
          </div>
          <p className="mt-2 text-sm text-on-surface-variant">{t('ai.skills.description')}</p>
          <p className="mt-2 flex items-start gap-2 text-xs text-on-surface-variant"><ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-status-success" aria-hidden="true" />{t('ai.skills.safetyHint')}</p>
        </div>
        <Button type="button" variant="secondary" disabled={busy || selectedKey === null} onClick={startNew}><Plus className="h-4 w-4" aria-hidden="true" />{t('ai.skills.add')}</Button>
      </div>

      {skills.length === 0 && selectedKey === null && (
        <div className="msm-card p-6 text-sm text-on-surface-variant">{t('ai.skills.empty')}</div>
      )}

      <form className="msm-card space-y-5 p-6" onSubmit={save}>
        {skills.length > 0 && (
          <label className="block max-w-md space-y-1.5">
            <span className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">{t('ai.skills.existing')}</span>
            <Dropdown
              value={selectedKey}
              onChange={selectSkill}
              options={skills.map((skill) => ({ value: skill.skill_key, label: skill.name, hint: `v${skill.version}${skill.enabled ? '' : ` · ${t('ai.skills.disabled')}`}` }))}
              placeholder={t('ai.skills.newSkill')}
              disabled={busy}
              aria-label={t('ai.skills.existing')}
            />
          </label>
        )}

        <fieldset disabled={busy} className="grid grid-cols-1 gap-4 border-0 p-0 md:grid-cols-2">
          <SkillInput label={t('ai.skills.key')} value={draft.skill_key} disabled={selectedKey !== null} maxLength={64} pattern="[a-z0-9][a-z0-9_.-]*" onChange={(skill_key) => setDraft((current) => ({ ...current, skill_key }))} />
          <SkillInput label={t('ai.skills.name')} value={draft.name} maxLength={100} onChange={(name) => setDraft((current) => ({ ...current, name }))} />
          <SkillInput className="md:col-span-2" label={t('ai.skills.descriptionLabel')} value={draft.description} maxLength={500} onChange={(description) => setDraft((current) => ({ ...current, description }))} />
          <label className="flex min-h-10 items-center justify-between gap-4 text-sm text-on-surface md:col-span-2">
            <span><span className="block font-medium">{t('ai.skills.enabled')}</span><span className="block text-xs text-on-surface-variant">{t('ai.skills.enabledHint')}</span></span>
            <Switch checked={draft.enabled} onCheckedChange={(enabled) => setDraft((current) => ({ ...current, enabled }))} aria-label={t('ai.skills.enabled')} />
          </label>
        </fieldset>

        <div>
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <div><h3 className="font-headline text-base font-semibold text-on-surface">{t('ai.skills.steps')}</h3><p className="mt-1 text-xs text-on-surface-variant">{t('ai.skills.stepsHint')}</p></div>
            <Button type="button" size="sm" variant="secondary" disabled={busy || draft.steps.length >= 20} onClick={() => setDraft((current) => ({ ...current, steps: [...current.steps, { tool_name: 'read_server_status', arguments: {} }] }))}><Plus className="h-4 w-4" aria-hidden="true" />{t('ai.skills.addStep')}</Button>
          </div>
          <ol className="space-y-3">
            {draft.steps.map((step, index) => (
              <SkillStepEditor
                key={`${index}-${step.tool_name}`}
                index={index}
                step={step}
                count={draft.steps.length}
                disabled={busy}
                onChange={(next) => updateStep(index, next)}
                onMove={(direction) => moveStep(index, direction)}
                onRemove={() => setDraft((current) => ({ ...current, steps: current.steps.filter((_, itemIndex) => itemIndex !== index) }))}
              />
            ))}
          </ol>
        </div>

        <div className="flex justify-end">
          <Button type="submit" disabled={busy || !valid}><Save className="h-4 w-4" aria-hidden="true" />{busy ? t('common.loading') : t(selectedKey ? 'ai.skills.saveVersion' : 'ai.skills.create')}</Button>
        </div>
      </form>
    </section>
  )
}

function SkillInput({ label, value, onChange, className = '', ...props }: {
  label: string
  value: string
  onChange: (value: string) => void
  className?: string
} & Omit<React.InputHTMLAttributes<HTMLInputElement>, 'value' | 'onChange'>) {
  return <label className={`space-y-1.5 ${className}`}><span className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">{label}</span><input className="msm-input" value={value} onChange={(event) => onChange(event.target.value)} {...props} /></label>
}

function SkillStepEditor({ index, step, count, disabled, onChange, onMove, onRemove }: {
  index: number
  step: AiSkillStep
  count: number
  disabled: boolean
  onChange: (step: AiSkillStep) => void
  onMove: (direction: -1 | 1) => void
  onRemove: () => void
}) {
  const { t } = useTranslation()
  const toolName = step.tool_name as ToolName
  return (
    <li className="rounded-xl border border-outline-variant/40 bg-surface-container-low/35 p-4">
      <div className="flex flex-wrap items-start gap-3">
        <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-primary/10 font-mono text-xs font-semibold text-primary" aria-hidden="true">{index + 1}</span>
        <div className="min-w-[14rem] flex-1">
          <Dropdown
            value={toolName}
            onChange={(value) => onChange({ tool_name: value, arguments: defaultArguments(value as ToolName) })}
            options={TOOL_NAMES.map((name) => ({ value: name, label: t(`ai.skills.tools.${name}`), hint: name.startsWith('propose_') ? t('ai.skills.proposal') : t('ai.skills.readOnly') }))}
            disabled={disabled}
            aria-label={t('ai.skills.stepTool', { number: index + 1 })}
          />
        </div>
        <div className="flex gap-1">
          <Button type="button" size="sm" variant="ghost" disabled={disabled || index === 0} onClick={() => onMove(-1)} aria-label={t('ai.skills.moveUp', { number: index + 1 })}><ArrowUp className="h-4 w-4" /></Button>
          <Button type="button" size="sm" variant="ghost" disabled={disabled || index === count - 1} onClick={() => onMove(1)} aria-label={t('ai.skills.moveDown', { number: index + 1 })}><ArrowDown className="h-4 w-4" /></Button>
          <Button type="button" size="sm" variant="ghost" disabled={disabled || count === 1} onClick={onRemove} aria-label={t('ai.skills.removeStep', { number: index + 1 })}><Trash2 className="h-4 w-4" /></Button>
        </div>
      </div>
      {toolName === 'read_server_logs' && (
        <label className="mt-3 block max-w-xs space-y-1.5"><span className="block text-xs font-semibold text-on-surface-variant">{t('ai.skills.logLines')}</span><input type="number" className="msm-input" min={1} max={200} value={Number(step.arguments.lines ?? 100)} disabled={disabled} onChange={(event) => onChange({ ...step, arguments: { lines: Number(event.target.value) } })} /></label>
      )}
      {toolName === 'read_config' && (
        <label className="mt-3 block space-y-1.5"><span className="block text-xs font-semibold text-on-surface-variant">{t('ai.skills.configPath')}</span><input className="msm-input" maxLength={256} value={String(step.arguments.path ?? '')} disabled={disabled} placeholder={t('ai.skills.configPathPlaceholder')} onChange={(event) => onChange({ ...step, arguments: { path: event.target.value } })} /></label>
      )}
      {toolName === 'propose_server_lifecycle' && (
        <label className="mt-3 block max-w-xs space-y-1.5"><span className="block text-xs font-semibold text-on-surface-variant">{t('ai.skills.operation')}</span><Dropdown value={String(step.arguments.operation ?? 'restart')} onChange={(operation) => onChange({ ...step, arguments: { operation } })} options={['start', 'stop', 'restart'].map((operation) => ({ value: operation, label: t(`ai.skills.operations.${operation}`) }))} disabled={disabled} aria-label={t('ai.skills.operation')} /></label>
      )}
    </li>
  )
}
