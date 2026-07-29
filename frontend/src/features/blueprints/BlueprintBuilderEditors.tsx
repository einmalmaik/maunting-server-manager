import { cloneElement, useMemo, useRef, useState, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'
import { Plus, Trash2 } from 'lucide-react'
import { Button, Dropdown } from '@/Singra/UI'
import type { BlueprintDraft, BlueprintValidationIssue, GuardianRecoveryAction } from './contract'

interface AccessibleControlProps {
  id?: string
  'aria-describedby'?: string
  'aria-invalid'?: boolean
}

export function Field({
  id,
  label,
  help,
  error,
  children,
}: {
  id: string
  label: string
  help: string
  error?: string
  children: ReactElement<AccessibleControlProps>
}) {
  const helpId = `${id}-help`
  return (
    <div className="min-w-0 max-w-full">
      <label htmlFor={id} className="mb-1.5 block break-words font-label-md text-sm font-semibold text-on-surface">
        {label}
      </label>
      {cloneElement(children, {
        id,
        'aria-describedby': helpId,
        'aria-invalid': Boolean(error),
      })}
      <p id={helpId} className={`${error ? 'msm-field-error' : 'msm-field-help'} break-words`}>
        {error ?? help}
      </p>
    </div>
  )
}

export function LinesField({
  id,
  label,
  help,
  value,
  onChange,
  error,
}: {
  id: string
  label: string
  help: string
  value: string[]
  onChange: (value: string[]) => void
  error?: string
}) {
  const normalize = () => onChange(value.map(line => line.trim()).filter(Boolean))
  return (
    <Field id={id} label={label} help={help} error={error}>
      <textarea
        className="msm-input min-h-24 font-mono text-xs"
        value={value.join('\n')}
        onChange={event => onChange(event.target.value.split('\n'))}
        onBlur={normalize}
      />
    </Field>
  )
}

interface EnvironmentRow {
  id: number
  key: string
  value: string
}

function environmentRowIssues(rows: EnvironmentRow[]): Map<number, string> {
  const counts = new Map<string, number>()
  rows.forEach(row => {
    const key = row.key.trim()
    if (key) counts.set(key, (counts.get(key) ?? 0) + 1)
  })
  const issues = new Map<number, string>()
  rows.forEach(row => {
    const key = row.key.trim()
    if (!key) issues.set(row.id, 'blueprintBuilder.validation.envEmpty')
    else if ((counts.get(key) ?? 0) > 1) issues.set(row.id, 'blueprintBuilder.validation.envDuplicate')
    else if (!/^[A-Z][A-Z0-9_]*$/.test(key)) issues.set(row.id, 'blueprintBuilder.validation.envName')
  })
  return issues
}

export function EnvironmentEditor({
  value,
  onChange,
  onIssuesChange,
}: {
  value: Record<string, string>
  onChange: (value: Record<string, string>) => void
  onIssuesChange: (issues: BlueprintValidationIssue[]) => void
}) {
  const { t } = useTranslation()
  const nextId = useRef(1)
  const [rows, setRows] = useState<EnvironmentRow[]>(() =>
    Object.entries(value).map(([key, current]) => ({ id: nextId.current++, key, value: current })),
  )
  const rowIssues = useMemo(() => environmentRowIssues(rows), [rows])

  const commitRows = (next: EnvironmentRow[]) => {
    setRows(next)
    const nextIssues = environmentRowIssues(next)
    onIssuesChange(
      [...nextIssues.entries()].map(([id, key]) => ({
        path: 'runtime.env',
        key,
        values: { row: next.findIndex(item => item.id === id) + 1 },
      })),
    )
    if (nextIssues.size === 0) {
      onChange(Object.fromEntries(next.map(row => [row.key.trim(), row.value])))
    }
  }

  const addRow = () => commitRows([...rows, { id: nextId.current++, key: '', value: '' }])

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-3">
        <h4 className="font-semibold">{t('blueprintBuilder.environment.title')}</h4>
        <Button variant="secondary" onClick={addRow}>
          <Plus className="h-4 w-4" aria-hidden="true" />
          {t('blueprintBuilder.environment.add')}
        </Button>
      </div>
      <p className="msm-field-help">{t('blueprintBuilder.environment.help')}</p>
      {rows.map((row, index) => {
        const issueKey = rowIssues.get(row.id)
        const helpId = `bp-env-${row.id}-help`
        return (
          <div key={row.id} className="grid gap-2 sm:grid-cols-[1fr_1.5fr_auto]">
            <input
              aria-label={t('blueprintBuilder.environment.nameLabel', { index: index + 1 })}
              aria-describedby={issueKey ? helpId : undefined}
              aria-invalid={Boolean(issueKey)}
              className="msm-input font-mono"
              value={row.key}
              onChange={event => commitRows(rows.map(item => item.id === row.id ? { ...item, key: event.target.value } : item))}
            />
            <input
              aria-label={t('blueprintBuilder.environment.valueLabel', { index: index + 1 })}
              className="msm-input font-mono"
              value={row.value}
              onChange={event => commitRows(rows.map(item => item.id === row.id ? { ...item, value: event.target.value } : item))}
            />
            <Button
              variant="ghost"
              aria-label={t('blueprintBuilder.environment.removeLabel', { index: index + 1 })}
              onClick={() => commitRows(rows.filter(item => item.id !== row.id))}
            >
              <Trash2 className="h-4 w-4" aria-hidden="true" />
            </Button>
            {issueKey && (
              <p id={helpId} className="msm-field-error sm:col-span-3">
                {t(issueKey, { row: index + 1, name: row.key })}
              </p>
            )}
          </div>
        )
      })}
    </div>
  )
}

export function SetupCommandsEditor({
  value,
  onChange,
}: {
  value: string[][]
  onChange: (value: string[][]) => void
}) {
  const { t } = useTranslation()
  return (
    <div className="space-y-3 md:col-span-2">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h4 className="font-semibold">{t('blueprintBuilder.setup.title')}</h4>
          <p className="msm-field-help">{t('blueprintBuilder.setup.help')}</p>
        </div>
        <Button
          variant="secondary"
          disabled={value.length >= 8}
          onClick={() => onChange([...value, ['']])}
        >
          <Plus className="h-4 w-4" aria-hidden="true" />
          {t('blueprintBuilder.setup.addCommand')}
        </Button>
      </div>
      {value.map((command, commandIndex) => (
        <fieldset key={commandIndex} className="space-y-2 rounded-xl border border-outline-variant/50 p-3">
          <legend className="px-1 text-xs font-semibold text-on-surface-variant">
            {t('blueprintBuilder.setup.command', { index: commandIndex + 1 })}
          </legend>
          {command.map((argument, argumentIndex) => (
            <div key={argumentIndex} className="grid gap-2 sm:grid-cols-[1fr_auto]">
              <input
                aria-label={t('blueprintBuilder.setup.argumentLabel', {
                  command: commandIndex + 1,
                  argument: argumentIndex + 1,
                })}
                className="msm-input font-mono"
                value={argument}
                onChange={event => onChange(value.map((item, index) => index === commandIndex
                  ? item.map((current, argIndex) => argIndex === argumentIndex ? event.target.value : current)
                  : item))}
              />
              <Button
                variant="ghost"
                aria-label={t('blueprintBuilder.setup.removeArgument', { index: argumentIndex + 1 })}
                onClick={() => onChange(value.map((item, index) => index === commandIndex
                  ? item.filter((_, argIndex) => argIndex !== argumentIndex)
                  : item))}
              >
                <Trash2 className="h-4 w-4" aria-hidden="true" />
              </Button>
            </div>
          ))}
          <div className="flex flex-wrap gap-2">
            <Button
              variant="secondary"
              disabled={command.length >= 32}
              onClick={() => onChange(value.map((item, index) => index === commandIndex ? [...item, ''] : item))}
            >
              <Plus className="h-4 w-4" aria-hidden="true" />
              {t('blueprintBuilder.setup.addArgument')}
            </Button>
            <Button
              variant="destructive"
              onClick={() => onChange(value.filter((_, index) => index !== commandIndex))}
            >
              <Trash2 className="h-4 w-4" aria-hidden="true" />
              {t('blueprintBuilder.setup.removeCommand')}
            </Button>
          </div>
        </fieldset>
      ))}
      <p className="msm-alert-info">{t('blueprintBuilder.setup.notice')}</p>
    </div>
  )
}

export function StartupProfilesEditor({
  value,
  onChange,
}: {
  value: BlueprintDraft['runtime']['startupProfiles']
  onChange: (value: BlueprintDraft['runtime']['startupProfiles']) => void
}) {
  const { t } = useTranslation()
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h4 className="font-semibold">{t('blueprintBuilder.profiles.title')}</h4>
          <p className="msm-field-help">{t('blueprintBuilder.profiles.help')}</p>
        </div>
        <Button variant="secondary" disabled={value.length >= 8} onClick={() => onChange([...value, { whenFile: '', startup: '' }])}>
          <Plus className="h-4 w-4" aria-hidden="true" />
          {t('blueprintBuilder.profiles.add')}
        </Button>
      </div>
      {value.map((row, index) => (
        <div key={index} className="grid gap-2 rounded-xl border border-outline-variant/50 p-3 sm:grid-cols-[1fr_1.5fr_auto]">
          <input aria-label={t('blueprintBuilder.profiles.markerLabel', { index: index + 1 })} placeholder="server.marker" className="msm-input font-mono" value={row.whenFile} onChange={event => onChange(value.map((item, itemIndex) => itemIndex === index ? { ...item, whenFile: event.target.value } : item))} />
          <input aria-label={t('blueprintBuilder.profiles.startupLabel', { index: index + 1 })} placeholder="./start-alt" className="msm-input font-mono" value={row.startup} onChange={event => onChange(value.map((item, itemIndex) => itemIndex === index ? { ...item, startup: event.target.value } : item))} />
          <Button variant="ghost" aria-label={t('blueprintBuilder.profiles.removeLabel', { index: index + 1 })} onClick={() => onChange(value.filter((_, itemIndex) => itemIndex !== index))}>
            <Trash2 className="h-4 w-4" aria-hidden="true" />
          </Button>
        </div>
      ))}
    </div>
  )
}

export function SeedFileEditor({
  value,
  onChange,
}: {
  value: BlueprintDraft['runtime']['seedFiles']
  onChange: (value: BlueprintDraft['runtime']['seedFiles']) => void
}) {
  const { t } = useTranslation()
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h4 className="font-semibold">{t('blueprintBuilder.seedFiles.title')}</h4>
          <p className="msm-field-help">{t('blueprintBuilder.seedFiles.help')}</p>
        </div>
        <Button variant="secondary" disabled={value.length >= 16} onClick={() => onChange([...value, { file: '', content: '' }])}>
          <Plus className="h-4 w-4" aria-hidden="true" />
          {t('blueprintBuilder.seedFiles.add')}
        </Button>
      </div>
      {value.map((row, index) => (
        <div key={index} className="grid gap-2 rounded-xl border border-outline-variant/50 p-3">
          <input
            aria-label={t('blueprintBuilder.seedFiles.fileLabel', { index: index + 1 })}
            placeholder="server.json"
            className="msm-input font-mono"
            value={row.file}
            onChange={event => onChange(value.map((item, itemIndex) => itemIndex === index ? { ...item, file: event.target.value } : item))}
          />
          <textarea
            aria-label={t('blueprintBuilder.seedFiles.contentLabel', { index: index + 1 })}
            placeholder={'{\n  "queryPort": {QUERY_PORT}\n}\n'}
            className="msm-input min-h-28 font-mono text-xs"
            value={row.content}
            onChange={event => onChange(value.map((item, itemIndex) => itemIndex === index ? { ...item, content: event.target.value } : item))}
          />
          <Button variant="destructive" onClick={() => onChange(value.filter((_, itemIndex) => itemIndex !== index))}>
            <Trash2 className="h-4 w-4" aria-hidden="true" />
            {t('common.remove')}
          </Button>
        </div>
      ))}
    </div>
  )
}

export function ConfigPatchEditor({
  value,
  onChange,
}: {
  value: BlueprintDraft['runtime']['configPatches']
  onChange: (value: BlueprintDraft['runtime']['configPatches']) => void
}) {
  const { t } = useTranslation()
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h4 className="font-semibold">{t('blueprintBuilder.patches.title')}</h4>
          <p className="msm-field-help">{t('blueprintBuilder.patches.help')}</p>
        </div>
        <Button variant="secondary" disabled={value.length >= 32} onClick={() => onChange([...value, { type: 'ini', file: '', section: '', key: '', value: '' }])}>
          <Plus className="h-4 w-4" aria-hidden="true" />
          {t('blueprintBuilder.patches.add')}
        </Button>
      </div>
      {value.map((row, index) => (
        <div key={index} className="grid gap-2 rounded-xl border border-outline-variant/50 p-3 md:grid-cols-2">
          <Dropdown aria-label={t('blueprintBuilder.patches.typeLabel', { index: index + 1 })} value={row.type} options={[{ value: 'ini', label: 'INI' }, { value: 'regex', label: 'Regex' }]} onChange={next => onChange(value.map((item, itemIndex) => itemIndex === index ? next === 'ini' ? { type: 'ini', file: item.file, section: '', key: '', value: item.value } : { type: 'regex', file: item.file, regex: '', value: item.value } : item))} />
          <input aria-label={t('blueprintBuilder.patches.fileLabel', { index: index + 1 })} placeholder="config/server.ini" className="msm-input font-mono" value={row.file} onChange={event => onChange(value.map((item, itemIndex) => itemIndex === index ? { ...item, file: event.target.value } : item))} />
          {row.type === 'ini' ? (
            <>
              <input aria-label={t('blueprintBuilder.patches.sectionLabel', { index: index + 1 })} placeholder="Server" className="msm-input" value={row.section ?? ''} onChange={event => onChange(value.map((item, itemIndex) => itemIndex === index ? { ...item, section: event.target.value } : item))} />
              <input aria-label={t('blueprintBuilder.patches.keyLabel', { index: index + 1 })} placeholder="Port" className="msm-input" value={row.key ?? ''} onChange={event => onChange(value.map((item, itemIndex) => itemIndex === index ? { ...item, key: event.target.value } : item))} />
            </>
          ) : (
            <input aria-label={t('blueprintBuilder.patches.regexLabel', { index: index + 1 })} placeholder="(port=)\\d+" className="msm-input font-mono md:col-span-2" value={row.regex ?? ''} onChange={event => onChange(value.map((item, itemIndex) => itemIndex === index ? { ...item, regex: event.target.value } : item))} />
          )}
          <input aria-label={t('blueprintBuilder.patches.valueLabel', { index: index + 1 })} placeholder="{GAME_PORT}" className="msm-input font-mono" value={row.value} onChange={event => onChange(value.map((item, itemIndex) => itemIndex === index ? { ...item, value: event.target.value } : item))} />
          <Button variant="destructive" onClick={() => onChange(value.filter((_, itemIndex) => itemIndex !== index))}>
            <Trash2 className="h-4 w-4" aria-hidden="true" />
            {t('common.remove')}
          </Button>
        </div>
      ))}
    </div>
  )
}

export function PostInstallEditor({
  value,
  onChange,
}: {
  value: NonNullable<BlueprintDraft['mods']>['postInstall']
  onChange: (value: NonNullable<BlueprintDraft['mods']>['postInstall']) => void
}) {
  const { t } = useTranslation()
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-3">
        <h4 className="font-semibold">{t('blueprintBuilder.postInstall.title')}</h4>
        <Button variant="secondary" disabled={value.length >= 32} onClick={() => onChange([...value, { operation: 'copy', source: '', target: '', required: false }])}>
          <Plus className="h-4 w-4" aria-hidden="true" />
          {t('blueprintBuilder.postInstall.add')}
        </Button>
      </div>
      {value.map((row, index) => (
        <div key={index} className="grid gap-2 rounded-xl border border-outline-variant/50 p-3 md:grid-cols-[.7fr_1fr_1fr_auto]">
          <Dropdown aria-label={t('blueprintBuilder.postInstall.operationLabel', { index: index + 1 })} value={row.operation} options={[{ value: 'copy', label: t('blueprintBuilder.postInstall.copy') }, { value: 'symlink', label: t('blueprintBuilder.postInstall.symlink') }]} onChange={next => onChange(value.map((item, itemIndex) => itemIndex === index ? { ...item, operation: next as 'copy' | 'symlink' } : item))} />
          <input aria-label={t('blueprintBuilder.postInstall.sourceLabel', { index: index + 1 })} placeholder="workshop/{MOD_ID}/*" className="msm-input font-mono" value={row.source} onChange={event => onChange(value.map((item, itemIndex) => itemIndex === index ? { ...item, source: event.target.value } : item))} />
          <input aria-label={t('blueprintBuilder.postInstall.targetLabel', { index: index + 1 })} placeholder="mods/{BASENAME}" className="msm-input font-mono" value={row.target} onChange={event => onChange(value.map((item, itemIndex) => itemIndex === index ? { ...item, target: event.target.value } : item))} />
          <Button variant="ghost" aria-label={t('blueprintBuilder.postInstall.removeLabel', { index: index + 1 })} onClick={() => onChange(value.filter((_, itemIndex) => itemIndex !== index))}>
            <Trash2 className="h-4 w-4" aria-hidden="true" />
          </Button>
          <label className="flex items-center gap-2 text-sm md:col-span-4">
            <input type="checkbox" checked={row.required} onChange={event => onChange(value.map((item, itemIndex) => itemIndex === index ? { ...item, required: event.target.checked } : item))} />
            {t('blueprintBuilder.postInstall.required')}
          </label>
        </div>
      ))}
    </div>
  )
}
export function RecoveryPoliciesEditor({
  value,
  onChange,
}: {
  value: Array<{ match: string; action: GuardianRecoveryAction | '' }>
  onChange: (value: Array<{ match: string; action: GuardianRecoveryAction | '' }>) => void
}) {
  const { t } = useTranslation()

  const commonMatches = [
    { value: 'process_not_running', label: t('blueprintBuilder.recovery.matches.process_not_running') },
    { value: 'tcp_connect_failed', label: t('blueprintBuilder.recovery.matches.tcp_connect_failed') },
    { value: 'udp_mapping_missing', label: t('blueprintBuilder.recovery.matches.udp_mapping_missing') },
    { value: 'http_redirect_rejected', label: t('blueprintBuilder.recovery.matches.http_redirect_rejected') },
    { value: 'http_response_too_large', label: t('blueprintBuilder.recovery.matches.http_response_too_large') },
    { value: 'http_unexpected_status', label: t('blueprintBuilder.recovery.matches.http_unexpected_status') },
    { value: 'http_request_failed', label: t('blueprintBuilder.recovery.matches.http_request_failed') },
    { value: 'minecraft_query_failed', label: t('blueprintBuilder.recovery.matches.minecraft_query_failed') },
    { value: 'minecraft_status_failed', label: t('blueprintBuilder.recovery.matches.minecraft_status_failed') },
    { value: 'source_query_failed', label: t('blueprintBuilder.recovery.matches.source_query_failed') },
    { value: 'port-conflict', label: t('blueprintBuilder.recovery.matches.port-conflict') },
    { value: 'linux-oom', label: t('blueprintBuilder.recovery.matches.linux-oom') },
    { value: 'java-stacktrace', label: t('blueprintBuilder.recovery.matches.java-stacktrace') },
    { value: 'nodejs-stacktrace', label: t('blueprintBuilder.recovery.matches.nodejs-stacktrace') },
    { value: 'missing-runtime', label: t('blueprintBuilder.recovery.matches.missing-runtime') },
    { value: 'corrupted-config', label: t('blueprintBuilder.recovery.matches.corrupted-config') },
    { value: 'startup-pattern', label: t('blueprintBuilder.recovery.matches.startup-pattern') },
    { value: 'probe_failed', label: t('blueprintBuilder.recovery.matches.probe_failed') },
  ]

  const commonActions = [
    { value: 'restart', label: t('blueprintBuilder.recovery.actions.restart') },
    { value: 'graceful_restart', label: t('blueprintBuilder.recovery.actions.graceful_restart') },
    { value: 'clear_declared_lock_files', label: t('blueprintBuilder.recovery.actions.clear_declared_lock_files') },
    { value: 'quarantine', label: t('blueprintBuilder.recovery.actions.quarantine') },
  ]

  return (
    <div className="min-w-0 max-w-full space-y-4">
      <div className="flex min-w-0 flex-col items-start gap-3 sm:flex-row sm:items-center sm:justify-between">
        <h4 className="min-w-0 break-words font-semibold text-sm text-on-surface-variant">{t('blueprintBuilder.recovery.policiesTitle')}</h4>
        <Button
          variant="secondary"
          className="h-auto min-h-10 max-w-full whitespace-normal text-left"
          disabled={value.length >= 16}
          onClick={() => onChange([...value, { match: 'port-conflict', action: 'restart' }])}
        >
          <Plus className="h-4 w-4" aria-hidden="true" />
          {t('blueprintBuilder.recovery.addPolicy')}
        </Button>
      </div>
      {value.map((row, index) => {
        return (
          <div key={index} className="grid min-w-0 max-w-full gap-3 rounded-xl border border-outline-variant/50 bg-surface-container-lowest p-4">
            <div className="grid min-w-0 max-w-full gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] lg:items-end">
              <div className="min-w-0 max-w-full space-y-1">
                <label className="text-xs font-semibold text-on-surface-variant">{t('blueprintBuilder.recovery.matchSelect')}</label>
                <Dropdown
                  aria-label={t('blueprintBuilder.recovery.matchLabel', { index: index + 1 })}
                  value={row.match}
                  options={commonMatches}
                  onChange={next => onChange(value.map((item, itemIndex) => itemIndex === index ? { ...item, match: next } : item))}
                />
              </div>
              <div className="min-w-0 max-w-full space-y-1">
                <label className="text-xs font-semibold text-on-surface-variant">{t('blueprintBuilder.recovery.actionSelect')}</label>
                <Dropdown
                  aria-label={t('blueprintBuilder.recovery.actionLabel', { index: index + 1 })}
                  value={row.action}
                  options={commonActions}
                  onChange={next => onChange(value.map((item, itemIndex) => itemIndex === index
                    ? { ...item, action: next as GuardianRecoveryAction }
                    : item))}
                />
              </div>
              <Button
                variant="ghost"
                aria-label={t('blueprintBuilder.recovery.removeLabel', { index: index + 1 })}
                onClick={() => onChange(value.filter((_, itemIndex) => itemIndex !== index))}
                className="justify-self-start lg:self-end"
              >
                <Trash2 className="h-4 w-4" aria-hidden="true" />
              </Button>
            </div>
            
          </div>
        )
      })}
    </div>
  )
}
