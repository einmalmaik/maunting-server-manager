import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import { AlertTriangle, Check, ChevronLeft, ChevronRight, Code2, Download, Plus, Save, Trash2, X } from 'lucide-react'
import { Button, Dropdown, NumberStepper } from '@/Singra/UI'
import { api } from '@/api/client'
import { confirm } from '@/stores/confirmStore'
import { toast } from '@/stores/toastStore'
import type { BlueprintListEntry } from '@/types'
import {
  changeBlueprintSource,
  createBlueprintDraft,
  getBlueprintCollision,
  normalizeBlueprintDraft,
  validateBlueprintDraft,
  type BlueprintDraft,
  type BlueprintSourceType,
  type BlueprintValidationIssue,
} from './contract'
import {
  ConfigPatchEditor,
  EnvironmentEditor,
  Field,
  LinesField,
  PostInstallEditor,
  RecoveryPoliciesEditor,
  SetupCommandsEditor,
  StartupProfilesEditor,
} from './BlueprintBuilderEditors'

export type BlueprintBuilderMode = 'create' | 'edit' | 'clone'

interface BlueprintBuilderProps {
  mode: BlueprintBuilderMode
  sourceId?: string
  entries: BlueprintListEntry[]
  onClose: () => void
  onSaved: () => Promise<void>
}

const sectionIds = ['basics', 'runtime', 'ports', 'source', 'mods', 'backup', 'guardian', 'review'] as const
type SectionId = (typeof sectionIds)[number]

function sectionForIssue(path: string): SectionId {
  if (path.startsWith('meta.')) return 'basics'
  if (path.startsWith('runtime.') || path === 'paths') return 'runtime'
  if (path.startsWith('ports')) return 'ports'
  if (path.startsWith('source.')) return 'source'
  if (path.startsWith('mods.')) return 'mods'
  if (path.startsWith('backup.')) return 'backup'
  if (
    path.startsWith('health') ||
    path.startsWith('logs') ||
    path.startsWith('diagnostics') ||
    path.startsWith('recovery') ||
    path.startsWith('updates') ||
    path.startsWith('backups')
  ) {
    return 'guardian'
  }
  return 'backup'
}

export function BlueprintBuilder({ mode, sourceId, entries, onClose, onSaved }: BlueprintBuilderProps) {
  const { t } = useTranslation()
  const [draft, setDraft] = useState<BlueprintDraft>(() => createBlueprintDraft())
  const [section, setSection] = useState<SectionId>('basics')
  const [environmentIssues, setEnvironmentIssues] = useState<BlueprintValidationIssue[]>([])
  const [loading, setLoading] = useState(mode !== 'create')
  const [saving, setSaving] = useState(false)
  const closeRef = useRef<HTMLButtonElement>(null)
  const previousFocusRef = useRef<HTMLElement | null>(document.activeElement as HTMLElement | null)

  useEffect(() => {
    closeRef.current?.focus()
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
      if (event.key !== 'Tab') return
      const dialog = closeRef.current?.closest('[role="dialog"]')
      const focusable = Array.from(dialog?.querySelectorAll<HTMLElement>(
        'button:not([disabled]),input:not([disabled]),textarea:not([disabled]),select:not([disabled]),a[href],[tabindex="0"]',
      ) ?? [])
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last?.focus()
      }
      if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first?.focus()
      }
    }
    document.addEventListener('keydown', handleKey)

    if (mode === 'create' || !sourceId) {
      return () => {
        document.removeEventListener('keydown', handleKey)
        previousFocusRef.current?.focus()
      }
    }

    let active = true
    setLoading(true)
    api<BlueprintDraft>(`/blueprints/${encodeURIComponent(sourceId)}`)
      .then(value => {
        if (!active) return
        const next = structuredClone(value)
        if (mode === 'clone') {
          next.meta.id = ''
          next.meta.name = t('blueprintBuilder.cloneName', { name: next.meta.name })
        }
        setDraft(next)
      })
      .catch(() => toast.error(t('blueprintBuilder.loadFailed')))
      .finally(() => active && setLoading(false))

    return () => {
      active = false
      document.removeEventListener('keydown', handleKey)
      previousFocusRef.current?.focus()
    }
  }, [mode, sourceId, t])

  const normalized = useMemo(() => normalizeBlueprintDraft(draft), [draft])
  const issues = useMemo(
    () => [...validateBlueprintDraft(normalized), ...environmentIssues],
    [environmentIssues, normalized],
  )
  const issueFor = (path: string) => {
    const issue = issues.find(current => current.path === path)
    return issue ? t(issue.key, issue.values) : undefined
  }
  const currentIndex = sectionIds.indexOf(section)
  const sectionLabel = (id: SectionId) => t(`blueprintBuilder.sections.${id}`)
  const title = t(`blueprintBuilder.title.${mode}`)
  const dialogDescription = t(`blueprintBuilder.description.${mode}`)

  const updateSourceType = (type: BlueprintSourceType) => setDraft(current => changeBlueprintSource(current, type))
  const downloadDraft = () => {
    const blob = new Blob([`${JSON.stringify(normalized, null, 2)}\n`], { type: 'application/json' })
    const href = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = href
    anchor.download = `${draft.meta.id || 'msm-blueprint'}.blueprint.json`
    anchor.click()
    URL.revokeObjectURL(href)
  }

  const saveDraft = async () => {
    if (issues.length) {
      setSection('review')
      return
    }
    const collision = getBlueprintCollision(entries, draft.meta.id, mode === 'edit')
    if (collision === 'native-blocked') {
      toast.error(t('blueprintBuilder.collision.native'))
      setSection('basics')
      return
    }
    if (collision === 'community-confirm') {
      const approved = await confirm({
        title: t('blueprintBuilder.collision.title'),
        message: t('blueprintBuilder.collision.message', { id: draft.meta.id }),
        confirmText: t('blueprintBuilder.collision.confirm'),
        danger: true,
      })
      if (!approved) return
    }
    setSaving(true)
    try {
      await api<{ id: string }>('/blueprints/import', {
        method: 'POST',
        body: JSON.stringify(normalized),
      })
      toast.success(t(mode === 'edit' ? 'blueprintBuilder.saved' : 'blueprintBuilder.added'))
      await onSaved()
      onClose()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t('blueprintBuilder.saveFailed'))
    } finally {
      setSaving(false)
    }
  }

  const renderBasics = () => (
    <div className="grid gap-5 md:grid-cols-2">
      <Field id="bp-id" label={t('blueprintBuilder.fields.id.label')} help={t('blueprintBuilder.fields.id.help')} error={issueFor('meta.id')}>
        <input disabled={mode === 'edit'} className="msm-input font-mono" value={draft.meta.id} onChange={event => setDraft({ ...draft, meta: { ...draft.meta, id: event.target.value } })} />
      </Field>
      <Field id="bp-name" label={t('blueprintBuilder.fields.name.label')} help={t('blueprintBuilder.fields.name.help')} error={issueFor('meta.name')}>
        <input className="msm-input" value={draft.meta.name} onChange={event => setDraft({ ...draft, meta: { ...draft.meta, name: event.target.value } })} />
      </Field>
      <Field id="bp-category" label={t('blueprintBuilder.fields.category.label')} help={t('blueprintBuilder.fields.category.help')}>
        <Dropdown value={draft.meta.category} onChange={value => setDraft({ ...draft, meta: { ...draft.meta, category: value as BlueprintDraft['meta']['category'] } })} options={[
          { value: 'steam_game', label: t('blueprintBuilder.options.category.steamGame') },
          { value: 'non_steam_game', label: t('blueprintBuilder.options.category.nonSteamGame') },
          { value: 'voice_server', label: t('blueprintBuilder.options.category.voiceServer') },
          { value: 'bot', label: t('blueprintBuilder.options.category.bot') },
        ]} />
      </Field>
      <Field id="bp-author" label={t('blueprintBuilder.fields.author.label')} help={t('blueprintBuilder.fields.author.help')}>
        <input className="msm-input" maxLength={128} value={draft.meta.author ?? ''} onChange={event => setDraft({ ...draft, meta: { ...draft.meta, author: event.target.value } })} />
      </Field>
      <div className="md:col-span-2">
        <Field id="bp-description" label={t('blueprintBuilder.fields.description.label')} help={t('blueprintBuilder.fields.description.help')}>
          <textarea className="msm-input min-h-24" maxLength={1024} value={draft.meta.description ?? ''} onChange={event => setDraft({ ...draft, meta: { ...draft.meta, description: event.target.value } })} />
        </Field>
      </div>
    </div>
  )

  const renderRuntime = () => (
    <div className="grid gap-5 md:grid-cols-2">
      <Field id="bp-image" label={t('blueprintBuilder.fields.image.label')} help={t('blueprintBuilder.fields.image.help')} error={issueFor('runtime.image')}>
        <input className="msm-input font-mono" value={draft.runtime.image} onChange={event => setDraft({ ...draft, runtime: { ...draft.runtime, image: event.target.value } })} />
      </Field>
      <Field id="bp-workdir" label={t('blueprintBuilder.fields.workdir.label')} help={t('blueprintBuilder.fields.workdir.help')} error={issueFor('runtime.workdir')}>
        <input className="msm-input font-mono" value={draft.runtime.workdir ?? ''} onChange={event => setDraft({ ...draft, runtime: { ...draft.runtime, workdir: event.target.value } })} />
      </Field>
      <Field id="bp-user" label={t('blueprintBuilder.fields.user.label')} help={t('blueprintBuilder.fields.user.help')} error={issueFor('runtime.user')}>
        <input className="msm-input font-mono" value={draft.runtime.user ?? ''} onChange={event => setDraft({ ...draft, runtime: { ...draft.runtime, user: event.target.value } })} />
      </Field>
      <div className="md:col-span-2">
        <Field id="bp-startup" label={t('blueprintBuilder.fields.startup.label')} help={t('blueprintBuilder.fields.startup.help')} error={issueFor('runtime.startup')}>
          <textarea className="msm-input min-h-24 font-mono text-xs" value={draft.runtime.startup} onChange={event => setDraft({ ...draft, runtime: { ...draft.runtime, startup: event.target.value } })} />
        </Field>
      </div>
      <Field id="bp-stop-grace" label={t('blueprintBuilder.fields.stopGrace.label')} help={t('blueprintBuilder.fields.stopGrace.help')} error={issueFor('runtime.stopGracePeriodSeconds')}>
        <NumberStepper min={5} max={600} value={draft.runtime.stopGracePeriodSeconds} onValueChange={value => setDraft({ ...draft, runtime: { ...draft.runtime, stopGracePeriodSeconds: Number(value) } })} />
      </Field>
      <Field id="bp-start-check" label={t('blueprintBuilder.fields.startCheck.label')} help={t('blueprintBuilder.fields.startCheck.help')} error={issueFor('runtime.startupCheckSeconds')}>
        <NumberStepper min={0} max={300} value={draft.runtime.startupCheckSeconds} onValueChange={value => setDraft({ ...draft, runtime: { ...draft.runtime, startupCheckSeconds: Number(value) } })} />
      </Field>
      <Field id="bp-exec-timeout" label={t('blueprintBuilder.fields.execTimeout.label')} help={t('blueprintBuilder.fields.execTimeout.help')} error={issueFor('runtime.execTimeoutSeconds')}>
        <NumberStepper min={1} max={600} value={draft.runtime.execTimeoutSeconds} onValueChange={value => setDraft({ ...draft, runtime: { ...draft.runtime, execTimeoutSeconds: Number(value) } })} />
      </Field>
      <LinesField id="bp-dirs" label={t('blueprintBuilder.fields.dirs.label')} help={t('blueprintBuilder.fields.dirs.help')} error={issueFor('runtime.ensureDirs')} value={draft.runtime.ensureDirs} onChange={ensureDirs => setDraft({ ...draft, runtime: { ...draft.runtime, ensureDirs } })} />
      <LinesField id="bp-files" label={t('blueprintBuilder.fields.files.label')} help={t('blueprintBuilder.fields.files.help')} error={issueFor('runtime.requiredFiles')} value={draft.runtime.requiredFiles} onChange={requiredFiles => setDraft({ ...draft, runtime: { ...draft.runtime, requiredFiles } })} />
      <label className="flex items-start gap-3 rounded-xl border border-status-warning/25 bg-status-warning/5 p-4 md:col-span-2">
        <input type="checkbox" className="mt-1" checked={draft.runtime.enableExec} onChange={event => setDraft({ ...draft, runtime: { ...draft.runtime, enableExec: event.target.checked } })} />
        <span>
          <strong className="block text-sm">{t('blueprintBuilder.exec.title')}</strong>
          <span className="msm-field-help block">{t('blueprintBuilder.exec.help')}</span>
        </span>
      </label>
      <div className="md:col-span-2">
        <EnvironmentEditor key={`${mode}:${sourceId ?? 'new'}:${loading}`} value={draft.runtime.env} onChange={env => setDraft(current => ({ ...current, runtime: { ...current.runtime, env } }))} onIssuesChange={setEnvironmentIssues} />
      </div>
      <div className="md:col-span-2">
        <StartupProfilesEditor value={draft.runtime.startupProfiles} onChange={startupProfiles => setDraft({ ...draft, runtime: { ...draft.runtime, startupProfiles } })} />
      </div>
      <div className="md:col-span-2">
        <ConfigPatchEditor value={draft.runtime.configPatches} onChange={configPatches => setDraft({ ...draft, runtime: { ...draft.runtime, configPatches } })} />
      </div>
    </div>
  )

  const renderPorts = () => (
    <div className="space-y-3">
      {draft.ports.map((port, index) => (
        <div key={index} className="grid gap-3 rounded-xl border border-outline-variant/50 bg-surface-container/55 p-3 sm:grid-cols-[1fr_1fr_auto]">
          <Dropdown aria-label={t('blueprintBuilder.ports.roleLabel', { index: index + 1 })} value={port.name} onChange={value => setDraft({ ...draft, ports: draft.ports.map((item, itemIndex) => itemIndex === index ? { ...item, name: value as typeof item.name } : item) })} options={['game', 'query', 'rcon', 'voice', 'web', 'custom'].map(value => ({ value, label: t(`blueprintBuilder.options.port.${value}`) }))} />
          <Dropdown aria-label={t('blueprintBuilder.ports.protocolLabel', { index: index + 1 })} value={port.protocol} onChange={value => setDraft({ ...draft, ports: draft.ports.map((item, itemIndex) => itemIndex === index ? { ...item, protocol: value as typeof item.protocol } : item) })} options={[{ value: 'tcp', label: 'TCP' }, { value: 'udp', label: 'UDP' }]} />
          <Button variant="ghost" aria-label={t('blueprintBuilder.ports.removeLabel', { index: index + 1 })} onClick={() => setDraft({ ...draft, ports: draft.ports.filter((_, itemIndex) => itemIndex !== index) })}>
            <Trash2 className="h-4 w-4" aria-hidden="true" />
          </Button>
        </div>
      ))}
      <Button variant="secondary" onClick={() => setDraft({ ...draft, ports: [...draft.ports, { name: 'custom', protocol: 'tcp' }] })}>
        <Plus className="h-4 w-4" aria-hidden="true" />
        {t('blueprintBuilder.ports.add')}
      </Button>
    </div>
  )

  const renderSource = () => (
    <div className="grid gap-5 md:grid-cols-2">
      <Field id="bp-source" label={t('blueprintBuilder.fields.source.label')} help={t('blueprintBuilder.fields.source.help')}>
        <Dropdown value={draft.source.type} onChange={value => updateSourceType(value as BlueprintSourceType)} options={[
          { value: 'steam', label: 'Steam' },
          { value: 'http', label: t('blueprintBuilder.options.source.http') },
          { value: 'github', label: 'GitHub' },
          { value: 'dockerOnly', label: t('blueprintBuilder.options.source.dockerOnly') },
          { value: 'custom', label: t('blueprintBuilder.options.source.custom') },
          { value: 'manualUpload', label: t('blueprintBuilder.options.source.manualUpload') },
        ]} />
      </Field>
      <Field id="bp-update" label={t('blueprintBuilder.fields.update.label')} help={t('blueprintBuilder.fields.update.help')}>
        <Dropdown value={draft.source.updateStrategy} onChange={value => setDraft({ ...draft, source: { ...draft.source, updateStrategy: value as BlueprintDraft['source']['updateStrategy'] } })} options={[
          { value: 'checkBased', label: t('blueprintBuilder.options.update.checkBased') },
          { value: 'alwaysValidate', label: t('blueprintBuilder.options.update.alwaysValidate') },
          { value: 'none', label: t('blueprintBuilder.options.update.none') },
        ]} />
      </Field>
      {draft.source.steam && (
        <>
          <Field id="bp-appid" label={t('blueprintBuilder.fields.appId.label')} help={t('blueprintBuilder.fields.appId.help')} error={issueFor('source.steam.appId')}>
            <input className="msm-input font-mono" value={draft.source.steam.appId} onChange={event => setDraft({ ...draft, source: { ...draft.source, steam: { ...draft.source.steam!, appId: event.target.value } } })} />
          </Field>
          <Field id="bp-platform" label={t('blueprintBuilder.fields.platform.label')} help={t('blueprintBuilder.fields.platform.help')}>
            <Dropdown value={draft.source.steam.platform} onChange={value => setDraft({ ...draft, source: { ...draft.source, steam: { ...draft.source.steam!, platform: value as 'linux' | 'windows', compatibility: value === 'windows' ? 'proton' : 'native' } } })} options={[{ value: 'linux', label: t('blueprintBuilder.options.platform.linux') }, { value: 'windows', label: t('blueprintBuilder.options.platform.windows') }]} />
          </Field>
          <Field id="bp-compat" label={t('blueprintBuilder.fields.compatibility.label')} help={t('blueprintBuilder.fields.compatibility.help')} error={issueFor('source.steam.compatibility')}>
            <Dropdown value={draft.source.steam.compatibility ?? ''} onChange={value => setDraft({ ...draft, source: { ...draft.source, steam: { ...draft.source.steam!, compatibility: value as 'native' | 'wine' | 'proton' } } })} options={[{ value: 'native', label: t('blueprintBuilder.options.compatibility.native') }, { value: 'wine', label: 'Wine' }, { value: 'proton', label: 'Proton' }]} />
          </Field>
          <Field id="bp-steam-branch" label={t('blueprintBuilder.fields.steamBranch.label')} help={t('blueprintBuilder.fields.steamBranch.help')}>
            <input className="msm-input font-mono" value={draft.source.steam.branch ?? ''} onChange={event => setDraft({ ...draft, source: { ...draft.source, steam: { ...draft.source.steam!, branch: event.target.value } } })} />
          </Field>
          <label className="flex items-center gap-3"><input type="checkbox" checked={draft.source.steam.requiresLogin} onChange={event => setDraft({ ...draft, source: { ...draft.source, steam: { ...draft.source.steam!, requiresLogin: event.target.checked } } })} />{t('blueprintBuilder.steam.requiresLogin')}</label>
          <label className="flex items-center gap-3"><input type="checkbox" checked={draft.source.steam.validate} onChange={event => setDraft({ ...draft, source: { ...draft.source, steam: { ...draft.source.steam!, validate: event.target.checked } } })} />{t('blueprintBuilder.steam.validate')}</label>
        </>
      )}
      {draft.source.http && (
        <>
          <Field id="bp-url" label={t('blueprintBuilder.fields.url.label')} help={t('blueprintBuilder.fields.url.help')} error={issueFor('source.http.url')}><input type="url" className="msm-input" value={draft.source.http.url} onChange={event => setDraft({ ...draft, source: { ...draft.source, http: { ...draft.source.http!, url: event.target.value } } })} /></Field>
          <Field id="bp-archive" label={t('blueprintBuilder.fields.archive.label')} help={t('blueprintBuilder.fields.archive.help')}><Dropdown value={draft.source.http.archiveType ?? ''} onChange={value => setDraft({ ...draft, source: { ...draft.source, http: { ...draft.source.http!, archiveType: (value || undefined) as NonNullable<BlueprintDraft['source']['http']>['archiveType'] } } })} placeholder={t('blueprintBuilder.options.archiveAuto')} options={['zip', 'tar.gz', 'tgz', 'tar.xz', 'txz', 'tar.bz2', 'tbz2', '7z'].map(value => ({ value, label: value }))} /></Field>
          <Field id="bp-extract" label={t('blueprintBuilder.fields.extract.label')} help={t('blueprintBuilder.fields.extract.help')}><input className="msm-input font-mono" value={draft.source.http.extractTo ?? ''} onChange={event => setDraft({ ...draft, source: { ...draft.source, http: { ...draft.source.http!, extractTo: event.target.value } } })} /></Field>
          <Field id="bp-sha" label={t('blueprintBuilder.fields.sha.label')} help={t('blueprintBuilder.fields.sha.help')} error={issueFor('source.http.sha256')}><input className="msm-input font-mono" maxLength={64} value={draft.source.http.sha256 ?? ''} onChange={event => setDraft({ ...draft, source: { ...draft.source, http: { ...draft.source.http!, sha256: event.target.value } } })} /></Field>
        </>
      )}
      {draft.source.github && (
        <>
          <Field id="bp-repo" label={t('blueprintBuilder.fields.repo.label')} help={t('blueprintBuilder.fields.repo.help')} error={issueFor('source.github.repo')}><input className="msm-input font-mono" value={draft.source.github.repo} onChange={event => setDraft({ ...draft, source: { ...draft.source, github: { ...draft.source.github!, repo: event.target.value } } })} /></Field>
          <Field id="bp-branch" label={t('blueprintBuilder.fields.branch.label')} help={t('blueprintBuilder.fields.branch.help')}><input className="msm-input font-mono" value={draft.source.github.branch} onChange={event => setDraft({ ...draft, source: { ...draft.source, github: { ...draft.source.github!, branch: event.target.value } } })} /></Field>
          <Field id="bp-subpath" label={t('blueprintBuilder.fields.subPath.label')} help={t('blueprintBuilder.fields.subPath.help')} error={issueFor('source.github.subPath')}><input className="msm-input font-mono" value={draft.source.github.subPath ?? ''} onChange={event => setDraft({ ...draft, source: { ...draft.source, github: { ...draft.source.github!, subPath: event.target.value } } })} /></Field>
          <SetupCommandsEditor value={draft.source.github.setupCommands} onChange={setupCommands => setDraft({ ...draft, source: { ...draft.source, github: { ...draft.source.github!, setupCommands } } })} />
        </>
      )}
      {draft.source.manual && (
        <>
          <LinesField id="bp-manual-files" label={t('blueprintBuilder.fields.manualFiles.label')} help={t('blueprintBuilder.fields.manualFiles.help')} error={issueFor('source.manual.requiredFiles')} value={draft.source.manual.requiredFiles} onChange={requiredFiles => setDraft({ ...draft, source: { ...draft.source, manual: { ...draft.source.manual!, requiredFiles } } })} />
          <Field id="bp-manual-instructions" label={t('blueprintBuilder.fields.instructions.label')} help={t('blueprintBuilder.fields.instructions.help')} error={issueFor('source.manual.instructions')}><textarea className="msm-input min-h-28" value={draft.source.manual.instructions} onChange={event => setDraft({ ...draft, source: { ...draft.source, manual: { ...draft.source.manual!, instructions: event.target.value } } })} /></Field>
          <Field id="bp-manual-url" label={t('blueprintBuilder.fields.instructionsUrl.label')} help={t('blueprintBuilder.fields.instructionsUrl.help')}><input type="url" className="msm-input" value={draft.source.manual.instructionsUrl ?? ''} onChange={event => setDraft({ ...draft, source: { ...draft.source, manual: { ...draft.source.manual!, instructionsUrl: event.target.value } } })} /></Field>
        </>
      )}
    </div>
  )

  const renderMods = () => {
    if (!draft.mods) return null
    return (
      <div className="space-y-5">
        <label className="flex items-center gap-3"><input type="checkbox" checked={draft.mods.supportsMods} onChange={event => setDraft({ ...draft, mods: { ...draft.mods!, supportsMods: event.target.checked } })} />{t('blueprintBuilder.mods.supportsMods')}</label>
        <label className="flex items-center gap-3"><input type="checkbox" checked={draft.mods.supportsSteamWorkshop} onChange={event => setDraft({ ...draft, mods: { ...draft.mods!, supportsSteamWorkshop: event.target.checked } })} />{t('blueprintBuilder.mods.supportsWorkshop')}</label>
        {draft.mods.supportsSteamWorkshop && <Field id="bp-workshop-id" label={t('blueprintBuilder.fields.workshopId.label')} help={t('blueprintBuilder.fields.workshopId.help')} error={issueFor('mods.workshopAppId')}><input className="msm-input font-mono" value={draft.mods.workshopAppId ?? ''} onChange={event => setDraft({ ...draft, mods: { ...draft.mods!, workshopAppId: event.target.value } })} /></Field>}
        <LinesField id="bp-filter-tags" label={t('blueprintBuilder.fields.filterTags.label')} help={t('blueprintBuilder.fields.filterTags.help')} value={draft.mods.filterTags} onChange={filterTags => setDraft({ ...draft, mods: { ...draft.mods!, filterTags } })} />
        <Field id="bp-injection" label={t('blueprintBuilder.fields.injection.label')} help={t('blueprintBuilder.fields.injection.help')}><Dropdown value={draft.mods.modInjection} options={[{ value: 'none', label: t('blueprintBuilder.options.injection.none') }, { value: 'startupArg', label: t('blueprintBuilder.options.injection.startupArg') }, { value: 'file', label: t('blueprintBuilder.options.injection.file') }]} onChange={value => setDraft({ ...draft, mods: { ...draft.mods!, modInjection: value as NonNullable<BlueprintDraft['mods']>['modInjection'] } })} /></Field>
        {draft.mods.modInjection === 'startupArg' && <Field id="bp-mod-format" label={t('blueprintBuilder.fields.modFormat.label')} help={t('blueprintBuilder.fields.modFormat.help')} error={issueFor('mods.modStartupArgumentFormat')}><input className="msm-input font-mono" value={draft.mods.modStartupArgumentFormat ?? ''} onChange={event => setDraft({ ...draft, mods: { ...draft.mods!, modStartupArgumentFormat: event.target.value } })} /></Field>}
        {draft.mods.modInjection === 'file' && <><Field id="bp-mod-file" label={t('blueprintBuilder.fields.modFile.label')} help={t('blueprintBuilder.fields.modFile.help')} error={issueFor('mods.modListFilePath')}><input className="msm-input font-mono" value={draft.mods.modListFilePath ?? ''} onChange={event => setDraft({ ...draft, mods: { ...draft.mods!, modListFilePath: event.target.value } })} /></Field><Field id="bp-mod-content" label={t('blueprintBuilder.fields.modContent.label')} help={t('blueprintBuilder.fields.modContent.help')}><Dropdown value={draft.mods.modListContent} options={[{ value: 'workshopIds', label: t('blueprintBuilder.options.modContent.workshopIds') }, { value: 'postInstallTargetBasenames', label: t('blueprintBuilder.options.modContent.targetNames') }]} onChange={value => setDraft({ ...draft, mods: { ...draft.mods!, modListContent: value as NonNullable<BlueprintDraft['mods']>['modListContent'] } })} /></Field></>}
        <PostInstallEditor value={draft.mods.postInstall} onChange={postInstall => setDraft({ ...draft, mods: { ...draft.mods!, postInstall } })} />
      </div>
    )
  }

  const renderGuardian = () => {
    const health = draft.health ?? {
      process: { required: true },
      port: { protocol: 'tcp', port: '{{SERVER_PORT}}', timeout: '3s' },
      application: { type: '', interval: '30s', failure_threshold: 3 },
      startup: { success_patterns: [], failure_patterns: [] },
    }
    const logs = draft.logs ?? { sources: [], redact: [] }
    const diagnostics = draft.diagnostics ?? { parsers: [] }
    const recovery = draft.recovery ?? { policies: [] }
    const updates = draft.updates ?? { strategy: 'snapshot-then-update', health_verification: 'required', rollback_on_failure: true }
    const backups = draft.backups ?? { before_risky_action: true, protected_paths: [] }

    const updateHealth = (next: Partial<typeof health>) => {
      setDraft(current => ({ ...current, health: { ...health, ...next } }))
    }

    const applyPreset = (presetId: string) => {
      if (presetId === 'minecraft') {
        setDraft(current => ({
          ...current,
          health: {
            process: { required: true },
            port: { protocol: 'tcp', port: '{{SERVER_PORT}}', timeout: '3s' },
            application: { type: 'minecraft-query', interval: '30s', failure_threshold: 3 },
            startup: { success_patterns: ['Done'], failure_patterns: ['Unable to access jarfile', 'Failed to bind to port'] }
          },
          logs: { sources: ['logs/latest.log'], redact: ['password', 'discord_token', 'api_key'] },
          diagnostics: { parsers: ['java-stacktrace', 'linux-oom', 'port-conflict', 'corrupted-config'] },
          recovery: {
            policies: [
              { match: 'port-conflict', action: 'clear_declared_lock_files' },
              { match: 'linux-oom', action: 'graceful_restart' }
            ]
          },
          updates: { strategy: 'snapshot-then-update', health_verification: 'required', rollback_on_failure: true },
          backups: { before_risky_action: true, protected_paths: ['world/', 'plugins/', 'config/'] }
        }))
      } else if (presetId === 'steamcmd') {
        setDraft(current => ({
          ...current,
          health: {
            process: { required: true },
            port: { protocol: 'udp', port: '{{SERVER_PORT}}', timeout: '5s' },
            application: { type: 'source-query', interval: '60s', failure_threshold: 3 },
            startup: { success_patterns: ['Connection to Steam servers successful', 'GC Connection established'], failure_patterns: ['Error checking out release', 'Failed to initialize network'] }
          },
          logs: { sources: ['logs/latest.log', 'stdout'], redact: ['steam_password', 'rcon_password', 'api_key'] },
          diagnostics: { parsers: ['linux-oom', 'port-conflict', 'missing-runtime'] },
          recovery: {
            policies: [
              { match: 'port-conflict', action: 'clear_declared_lock_files' },
              { match: 'linux-oom', action: 'graceful_restart' }
            ]
          },
          updates: { strategy: 'snapshot-then-update', health_verification: 'required', rollback_on_failure: true },
          backups: { before_risky_action: true, protected_paths: ['save/', 'config/'] }
        }))
      } else if (presetId === 'nodejs') {
        setDraft(current => ({
          ...current,
          health: {
            process: { required: true },
            port: { protocol: 'tcp', port: '{{SERVER_PORT}}', timeout: '3s' },
            application: { type: 'http-ping', path: '/api/healthz', interval: '30s', failure_threshold: 3 },
            startup: { success_patterns: ['App listening on port', 'Server started'], failure_patterns: ['npm ERR!', 'UnhandledPromiseRejectionWarning'] }
          },
          logs: { sources: ['stdout', 'stderr'], redact: ['discord_token', 'api_key', 'database_url'] },
          diagnostics: { parsers: ['linux-oom', 'port-conflict', 'nodejs-stacktrace'] },
          recovery: {
            policies: [
              { match: 'port-conflict', action: 'clear_declared_lock_files' },
              { match: 'linux-oom', action: 'graceful_restart' }
            ]
          },
          updates: { strategy: 'snapshot-then-update', health_verification: 'required', rollback_on_failure: true },
          backups: { before_risky_action: true, protected_paths: ['data/'] }
        }))
      } else if (presetId === 'generic') {
        setDraft(current => ({
          ...current,
          health: {
            process: { required: true },
            port: { protocol: 'tcp', port: '{{SERVER_PORT}}', timeout: '3s' },
            application: { type: '', interval: '30s', failure_threshold: 3 },
            startup: { success_patterns: [], failure_patterns: [] }
          },
          logs: { sources: [], redact: [] },
          diagnostics: { parsers: [] },
          recovery: { policies: [] },
          updates: { strategy: 'snapshot-then-update', health_verification: 'required', rollback_on_failure: true },
          backups: { before_risky_action: true, protected_paths: [] }
        }))
      }
    }

    const appQueryOptions = [
      { value: '', label: t('blueprintBuilder.guardian.appQueryNone') },
      { value: 'minecraft-query', label: 'Minecraft Query (minecraft-query)' },
      { value: 'source-query', label: 'Steam/Source Query (source-query)' },
      { value: 'http-ping', label: 'HTTP Ping / Health Check (http-ping)' },
      { value: 'custom', label: t('blueprintBuilder.recovery.customValue') }
    ]

    const selectedAppQuery = appQueryOptions.some(o => o.value === health.application?.type) ? health.application?.type ?? '' : 'custom'

    return (
      <div className="space-y-6">
        {/* Preset Loader */}
        <div className="rounded-xl border-2 border-primary/20 bg-primary/5 p-4 space-y-3">
          <div className="flex items-center gap-3">
            <div className="h-2 w-2 rounded-full bg-primary animate-pulse" />
            <h4 className="font-bold text-primary text-sm uppercase tracking-wider">{t('blueprintBuilder.guardian.presetsTitle')}</h4>
          </div>
          <p className="text-xs text-on-surface-variant leading-relaxed">
            {t('blueprintBuilder.guardian.presetsHelp')}
          </p>
          <div className="max-w-md">
            <Dropdown
              value={null}
              onChange={applyPreset}
              placeholder={t('blueprintBuilder.guardian.presetsSelect')}
              options={[
                { value: 'minecraft', label: 'Minecraft (Paper, Fabric, Forge)' },
                { value: 'steamcmd', label: 'SteamCMD Server (Palworld, Rust, Zomboid)' },
                { value: 'nodejs', label: 'Node.js (Discord Bots, Web Apps)' },
                { value: 'generic', label: t('blueprintBuilder.guardian.presetGeneric') }
              ]}
            />
          </div>
        </div>

        {/* Recovery Ladder Visualization */}
        <div className="rounded-xl border border-outline-variant/60 bg-surface-container-low p-4 space-y-3">
          <h4 className="font-semibold text-lg border-b border-outline-variant/40 pb-2">{t('blueprintBuilder.guardian.ladderTitle')}</h4>
          <p className="text-xs text-on-surface-variant leading-relaxed">
            {t('blueprintBuilder.guardian.ladderDescription')}
          </p>
          <div className="grid gap-2 sm:grid-cols-2 md:grid-cols-4 lg:grid-cols-7 pt-2">
            {[1, 2, 3, 4, 5, 6, 7].map((num) => (
              <div key={num} className="rounded-lg p-2.5 bg-surface-container-lowest border border-outline-variant/30 text-center space-y-1">
                <div className="text-xs font-bold text-primary">{t('blueprintBuilder.guardian.ladderStepNum', { num })}</div>
                <div className="font-semibold text-[11px] text-on-surface leading-tight">{t(`blueprintBuilder.guardian.ladderStepName.${num}`)}</div>
                <div className="text-[10px] text-on-surface-variant leading-normal">{t(`blueprintBuilder.guardian.ladderStepDesc.${num}`)}</div>
              </div>
            ))}
          </div>
        </div>

        {/* Health Probe Configuration */}
        <div className="rounded-xl border border-outline-variant/60 bg-surface-container-low p-4 space-y-4">
          <h4 className="font-semibold text-lg border-b border-outline-variant/40 pb-2">{t('blueprintBuilder.guardian.healthTitle')}</h4>
          <label className="flex items-center gap-3">
            <input
              type="checkbox"
              checked={health.process?.required ?? true}
              onChange={event => updateHealth({ process: { required: event.target.checked } })}
            />
            {t('blueprintBuilder.guardian.processRequired')}
          </label>
          <div className="grid gap-4 md:grid-cols-3">
            <Field id="bp-health-port-proto" label={t('blueprintBuilder.fields.healthPortProto.label')} help={t('blueprintBuilder.fields.healthPortProto.help')}>
              <Dropdown
                value={health.port?.protocol ?? 'tcp'}
                onChange={value => updateHealth({ port: { ...health.port, protocol: value as 'tcp' | 'udp', port: health.port?.port ?? '', timeout: health.port?.timeout ?? '' } })}
                options={[{ value: 'tcp', label: 'TCP' }, { value: 'udp', label: 'UDP' }]}
              />
            </Field>
            <Field id="bp-health-port" label={t('blueprintBuilder.fields.healthPort.label')} help={t('blueprintBuilder.fields.healthPort.help')} error={issueFor('health.port.port')}>
              <input
                className="msm-input font-mono"
                value={health.port?.port ?? ''}
                onChange={event => updateHealth({ port: { ...health.port, protocol: health.port?.protocol ?? 'tcp', port: event.target.value, timeout: health.port?.timeout ?? '' } })}
              />
            </Field>
            <Field id="bp-health-port-timeout" label={t('blueprintBuilder.fields.healthPortTimeout.label')} help={t('blueprintBuilder.fields.healthPortTimeout.help')} error={issueFor('health.port.timeout')}>
              <input
                className="msm-input font-mono"
                value={health.port?.timeout ?? ''}
                onChange={event => updateHealth({ port: { ...health.port, protocol: health.port?.protocol ?? 'tcp', port: health.port?.port ?? '', timeout: event.target.value } })}
              />
            </Field>
          </div>
          <div className="grid gap-4 md:grid-cols-3">
            <div className="space-y-1">
              <label className="mb-1.5 block font-label-md text-sm font-semibold text-on-surface">
                {t('blueprintBuilder.fields.healthAppType.label')}
              </label>
              <Dropdown
                value={selectedAppQuery}
                options={appQueryOptions}
                onChange={next => {
                  const val = next === 'custom' ? '' : next
                  updateHealth({ application: { ...health.application, type: val, interval: health.application?.interval ?? '', failure_threshold: health.application?.failure_threshold ?? 3 } })
                }}
              />
            </div>
            <Field id="bp-health-app-interval" label={t('blueprintBuilder.fields.healthAppInterval.label')} help={t('blueprintBuilder.fields.healthAppInterval.help')} error={issueFor('health.application.interval')}>
              <input
                className="msm-input font-mono"
                value={health.application?.interval ?? ''}
                onChange={event => updateHealth({ application: { ...health.application, type: health.application?.type ?? '', interval: event.target.value, failure_threshold: health.application?.failure_threshold ?? 3 } })}
              />
            </Field>
            <Field id="bp-health-app-threshold" label={t('blueprintBuilder.fields.healthAppThreshold.label')} help={t('blueprintBuilder.fields.healthAppThreshold.help')} error={issueFor('health.application.failure_threshold')}>
              <NumberStepper
                min={1}
                max={20}
                value={health.application?.failure_threshold ?? 3}
                onValueChange={value => updateHealth({ application: { ...health.application, type: health.application?.type ?? '', interval: health.application?.interval ?? '', failure_threshold: Number(value) } })}
              />
            </Field>
          </div>

          {selectedAppQuery === 'custom' && (
            <div className="pt-2 border-t border-outline-variant/30">
              <Field id="bp-health-app-type-custom" label={t('blueprintBuilder.fields.healthAppTypeCustom.label')} help={t('blueprintBuilder.fields.healthAppTypeCustom.help')} error={issueFor('health.application.type')}>
                <input
                  className="msm-input font-mono"
                  placeholder="e.g. valve-query-protocol"
                  value={health.application?.type ?? ''}
                  onChange={event => updateHealth({ application: { ...health.application, type: event.target.value, interval: health.application?.interval ?? '', failure_threshold: health.application?.failure_threshold ?? 3 } })}
                />
              </Field>
            </div>
          )}

          {health.application?.type === 'http-ping' && (
            <div className="grid gap-4 md:grid-cols-2 pt-2 border-t border-outline-variant/30">
              <Field id="bp-health-app-path" label={t('blueprintBuilder.fields.healthAppPath.label')} help={t('blueprintBuilder.fields.healthAppPath.help')} error={issueFor('health.application.path')}>
                <input
                  className="msm-input font-mono"
                  placeholder="e.g. /healthz"
                  value={health.application?.path ?? ''}
                  onChange={event => updateHealth({ application: { ...health.application, type: health.application?.type ?? '', interval: health.application?.interval ?? '', failure_threshold: health.application?.failure_threshold ?? 3, path: event.target.value } })}
                />
              </Field>
              <Field id="bp-health-app-port" label={t('blueprintBuilder.fields.healthAppPort.label')} help={t('blueprintBuilder.fields.healthAppPort.help')} error={issueFor('health.application.port')}>
                <input
                  className="msm-input font-mono"
                  placeholder="e.g. {{SERVER_PORT}} or 4000"
                  value={health.application?.port ?? ''}
                  onChange={event => updateHealth({ application: { ...health.application, type: health.application?.type ?? '', interval: health.application?.interval ?? '', failure_threshold: health.application?.failure_threshold ?? 3, port: event.target.value } })}
                />
              </Field>
            </div>
          )}

          <div className="grid gap-4 md:grid-cols-2">
            <LinesField
              id="bp-health-success-patterns"
              label={t('blueprintBuilder.fields.healthSuccessPatterns.label')}
              help={t('blueprintBuilder.fields.healthSuccessPatterns.help')}
              value={health.startup?.success_patterns ?? []}
              onChange={success_patterns => updateHealth({ startup: { ...health.startup, success_patterns, failure_patterns: health.startup?.failure_patterns ?? [] } })}
            />
            <LinesField
              id="bp-health-failure-patterns"
              label={t('blueprintBuilder.fields.healthFailurePatterns.label')}
              help={t('blueprintBuilder.fields.healthFailurePatterns.help')}
              value={health.startup?.failure_patterns ?? []}
              onChange={failure_patterns => updateHealth({ startup: { ...health.startup, success_patterns: health.startup?.success_patterns ?? [], failure_patterns } })}
            />
          </div>
        </div>

        {/* Logs & Diagnostics Configuration */}
        <div className="rounded-xl border border-outline-variant/60 bg-surface-container-low p-4 space-y-4">
          <h4 className="font-semibold text-lg border-b border-outline-variant/40 pb-2">{t('blueprintBuilder.guardian.logsTitle')}</h4>
          <div className="grid gap-4 md:grid-cols-2">
            <LinesField
              id="bp-logs-sources"
              label={t('blueprintBuilder.fields.logsSources.label')}
              help={t('blueprintBuilder.fields.logsSources.help')}
              value={logs.sources}
              onChange={sources => setDraft(current => ({ ...current, logs: { ...logs, sources } }))}
            />
            <LinesField
              id="bp-logs-redact"
              label={t('blueprintBuilder.fields.logsRedact.label')}
              help={t('blueprintBuilder.fields.logsRedact.help')}
              value={logs.redact}
              onChange={redact => setDraft(current => ({ ...current, logs: { ...logs, redact } }))}
            />
          </div>
          <LinesField
            id="bp-diagnostics-parsers"
            label={t('blueprintBuilder.fields.diagnosticsParsers.label')}
            help={t('blueprintBuilder.fields.diagnosticsParsers.help')}
            value={diagnostics.parsers}
            onChange={parsers => setDraft(current => ({ ...current, diagnostics: { parsers } }))}
          />
        </div>

        {/* Recovery policies config */}
        <div className="rounded-xl border border-outline-variant/60 bg-surface-container-low p-4 space-y-4">
          <h4 className="font-semibold text-lg border-b border-outline-variant/40 pb-2">{t('blueprintBuilder.guardian.recoveryTitle')}</h4>
          <RecoveryPoliciesEditor
            value={recovery.policies}
            onChange={policies => setDraft(current => ({ ...current, recovery: { policies } }))}
          />
        </div>

        {/* Updates and backups */}
        <div className="rounded-xl border border-outline-variant/60 bg-surface-container-low p-4 space-y-4">
          <h4 className="font-semibold text-lg border-b border-outline-variant/40 pb-2">{t('blueprintBuilder.guardian.updatesTitle')}</h4>
          <div className="grid gap-4 md:grid-cols-2">
            <Field id="bp-updates-strategy" label={t('blueprintBuilder.fields.updatesStrategy.label')} help={t('blueprintBuilder.fields.updatesStrategy.help')}>
              <input
                className="msm-input font-mono"
                value={updates.strategy}
                onChange={event => setDraft(current => ({ ...current, updates: { ...updates, strategy: event.target.value } }))}
              />
            </Field>
            <Field id="bp-updates-verification" label={t('blueprintBuilder.fields.updatesVerification.label')} help={t('blueprintBuilder.fields.updatesVerification.help')}>
              <input
                className="msm-input font-mono"
                value={updates.health_verification}
                onChange={event => setDraft(current => ({ ...current, updates: { ...updates, health_verification: event.target.value } }))}
              />
            </Field>
          </div>
          <label className="flex items-center gap-3">
            <input
              type="checkbox"
              checked={updates.rollback_on_failure}
              onChange={event => setDraft(current => ({ ...current, updates: { ...updates, rollback_on_failure: event.target.checked } }))}
            />
            {t('blueprintBuilder.guardian.rollbackOnFailure')}
          </label>
          <hr className="border-outline-variant/40" />
          <label className="flex items-center gap-3">
            <input
              type="checkbox"
              checked={backups.before_risky_action}
              onChange={event => setDraft(current => ({ ...current, backups: { ...backups, before_risky_action: event.target.checked } }))}
            />
            {t('blueprintBuilder.guardian.beforeRiskyAction')}
          </label>
          <LinesField
            id="bp-backups-protected"
            label={t('blueprintBuilder.fields.backupsProtected.label')}
            help={t('blueprintBuilder.fields.backupsProtected.help')}
            value={backups.protected_paths}
            onChange={protected_paths => setDraft(current => ({ ...current, backups: { ...backups, protected_paths } }))}
          />
        </div>
      </div>
    )
  }

  const renderReview = () => (
    <div className="grid gap-5 lg:grid-cols-[1fr_1.2fr]">
      <div>
        {issues.length ? (
          <div className="msm-alert-warning" role="alert">
            <div className="flex items-center gap-2 font-semibold"><AlertTriangle className="h-4 w-4" aria-hidden="true" />{t('blueprintBuilder.review.issueCount', { count: issues.length })}</div>
            <ul className="mt-3 space-y-2">
              {issues.map((issue, index) => <li key={`${issue.path}-${issue.key}-${index}`}><button type="button" className="text-left underline underline-offset-2" onClick={() => setSection(sectionForIssue(issue.path))}><code>{issue.path}</code>: {t(issue.key, issue.values)}</button></li>)}
            </ul>
          </div>
        ) : (
          <div className="msm-alert-success flex items-center gap-2" role="status"><Check className="h-4 w-4" aria-hidden="true" />{t('blueprintBuilder.review.ready')}</div>
        )}
        <p className="mt-4 text-sm leading-6 text-on-surface-variant">{t('blueprintBuilder.review.backend')}</p>
      </div>
      <div className="overflow-hidden rounded-xl border border-outline-variant bg-surface-container-lowest">
        <div className="flex items-center gap-2 border-b border-outline-variant px-4 py-2 text-xs text-on-surface-variant"><Code2 className="h-4 w-4" aria-hidden="true" />{t('blueprintBuilder.review.json')}</div>
        <pre className="max-h-[28rem] overflow-auto p-4 text-xs leading-5 text-on-surface-variant"><code>{JSON.stringify(normalized, null, 2)}</code></pre>
      </div>
    </div>
  )

  return createPortal(
    <div className="fixed inset-0 z-50 flex min-h-0 min-w-0 items-stretch bg-black/70 backdrop-blur-sm md:pl-64" role="dialog" aria-modal="true" aria-labelledby="blueprint-builder-title" aria-describedby="blueprint-builder-description" data-testid="blueprint-builder-overlay">
      <div className="flex h-[100dvh] max-h-[100dvh] min-w-0 w-full flex-col overflow-hidden border-l border-outline-variant bg-background shadow-panel-strong" data-testid="blueprint-builder-panel">
        <header className="flex min-w-0 shrink-0 items-start gap-3 border-b border-outline-variant/60 px-4 py-3 md:items-center md:gap-4 md:px-6">
          <div className="min-w-0 flex-1">
            <p className="text-xs font-semibold uppercase tracking-[.14em] text-primary/70">{t('blueprintBuilder.studio')}</p>
            <h2 id="blueprint-builder-title" className="font-headline text-xl font-bold leading-tight">{title}</h2>
            <p id="blueprint-builder-description" className="mt-1 max-w-3xl text-xs leading-5 text-on-surface-variant">{dialogDescription}</p>
          </div>
          <span className="hidden rounded-md border border-outline-variant px-2 py-1 font-mono text-xs text-on-surface-variant sm:inline">{mode === 'edit' ? draft.meta.id : t('blueprintBuilder.draft')}</span>
          <button ref={closeRef} type="button" onClick={onClose} className="grid min-h-11 min-w-11 place-items-center rounded-lg hover:bg-surface-container-high" aria-label={t('blueprintBuilder.close')}><X className="h-5 w-5" aria-hidden="true" /></button>
        </header>
        {loading ? (
          <div className="grid min-h-0 flex-1 place-items-center text-on-surface-variant" role="status">{t('blueprintBuilder.loading')}</div>
        ) : (
          <div className="grid min-h-0 min-w-0 flex-1 grid-rows-[auto_minmax(0,1fr)] overflow-hidden md:grid-cols-[14rem_minmax(0,1fr)] md:grid-rows-1">
            <nav className="min-w-0 overflow-x-auto border-b border-outline-variant/50 p-2 md:overflow-y-auto md:border-b-0 md:border-r md:p-3" aria-label={t('blueprintBuilder.sectionNavigation')}>
              <ol className="flex min-w-max gap-1 md:grid md:min-w-0 md:grid-cols-1">
                {sectionIds.map((item, index) => <li key={item}><button type="button" onClick={() => setSection(item)} aria-current={section === item ? 'step' : undefined} className={`flex min-h-11 w-full items-center gap-3 whitespace-nowrap rounded-lg px-3 text-left text-sm ${section === item ? 'bg-primary/10 text-primary ring-1 ring-primary/20' : 'text-on-surface-variant hover:bg-surface-container-high'}`}><span className="font-mono text-xs opacity-60">{String(index + 1).padStart(2, '0')}</span>{sectionLabel(item)}</button></li>)}
              </ol>
            </nav>
            <div className="min-h-0 min-w-0 overflow-auto" tabIndex={0} aria-label={t('blueprintBuilder.fieldsArea')}>
              <form className="mx-auto max-w-4xl space-y-6 p-4 md:p-7" onSubmit={event => event.preventDefault()}>
                <div><p className="text-xs font-semibold uppercase tracking-[.14em] text-primary/65">{t('blueprintBuilder.step', { current: currentIndex + 1, total: sectionIds.length })}</p><h3 className="mt-1 font-headline text-2xl font-bold">{sectionLabel(section)}</h3></div>
                {section === 'basics' && renderBasics()}
                {section === 'runtime' && renderRuntime()}
                {section === 'ports' && renderPorts()}
                {section === 'source' && renderSource()}
                {section === 'mods' && renderMods()}
                {section === 'backup' && <LinesField id="bp-backup" label={t('blueprintBuilder.fields.backup.label')} help={t('blueprintBuilder.fields.backup.help')} value={draft.backup?.includePaths ?? []} onChange={includePaths => setDraft({ ...draft, backup: { includePaths } })} />}
                {section === 'guardian' && renderGuardian()}
                {section === 'review' && renderReview()}
              </form>
            </div>
          </div>
        )}
        <footer className="grid shrink-0 grid-cols-1 gap-2 border-t border-outline-variant/60 bg-surface-container-low/80 px-4 py-3 sm:grid-cols-[auto_1fr] sm:items-center md:px-6" data-testid="blueprint-builder-actions">
          <Button variant="secondary" className="w-full sm:w-auto" disabled={currentIndex === 0} onClick={() => setSection(sectionIds[currentIndex - 1])}><ChevronLeft className="h-4 w-4" aria-hidden="true" />{t('common.back')}</Button>
          <div className="grid grid-cols-2 gap-2 sm:ml-auto sm:flex sm:flex-wrap sm:justify-end">
            <Button variant="secondary" className="w-full sm:w-auto" onClick={downloadDraft}><Download className="h-4 w-4" aria-hidden="true" />{t('blueprintBuilder.downloadJson')}</Button>
            {currentIndex < sectionIds.length - 1 ? (
              <Button className="w-full sm:w-auto" onClick={() => setSection(sectionIds[currentIndex + 1])}>{t('common.next')}<ChevronRight className="h-4 w-4" aria-hidden="true" /></Button>
            ) : (
              <Button className="w-full sm:w-auto" disabled={saving || issues.length > 0} onClick={saveDraft}><Save className="h-4 w-4" aria-hidden="true" />{saving ? t('blueprintBuilder.saving') : mode === 'edit' ? t('blueprintBuilder.saveChanges') : t('blueprintBuilder.addDirectly')}</Button>
            )}
          </div>
        </footer>
      </div>
    </div>,
    document.body,
  )
}
