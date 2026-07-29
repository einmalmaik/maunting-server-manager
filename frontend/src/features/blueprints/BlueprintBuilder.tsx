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
  type GuardianApplicationProbeType,
  type GuardianDiagnosticParser,
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
  SeedFileEditor,
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
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = previousOverflow
    }
  }, [])

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
        next.runtime.seedFiles = next.runtime.seedFiles ?? []
        next.runtime.configPatches = next.runtime.configPatches ?? []
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
        <SeedFileEditor value={draft.runtime.seedFiles ?? []} onChange={seedFiles => setDraft({ ...draft, runtime: { ...draft.runtime, seedFiles } })} />
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
    const defaults = createBlueprintDraft()
    const health = draft.health ?? defaults.health!
    const logs = draft.logs ?? defaults.logs!
    const diagnostics = draft.diagnostics ?? defaults.diagnostics!
    const recovery = draft.recovery ?? defaults.recovery!
    const backups = draft.backups ?? defaults.backups!
    const guardianEnabled = Boolean(draft.health || draft.logs || draft.diagnostics || draft.recovery || draft.backups)

    const setGuardianEnabled = (enabled: boolean) => {
      setDraft(current => {
        if (enabled) {
          return {
            ...current,
            health: structuredClone(defaults.health!),
            logs: structuredClone(defaults.logs!),
            diagnostics: structuredClone(defaults.diagnostics!),
            recovery: structuredClone(defaults.recovery!),
            backups: structuredClone(defaults.backups!),
          }
        }
        const next = { ...current }
        delete next.health
        delete next.logs
        delete next.diagnostics
        delete next.recovery
        delete next.backups
        return next
      })
    }

    const updateProcess = (next: Partial<NonNullable<BlueprintDraft['health']>['process']>) => {
      setDraft(current => ({
        ...current,
        health: {
          ...current.health,
          process: { ...(current.health?.process ?? defaults.health!.process!), ...next },
        },
      }))
    }
    const updatePortHealth = (next: Partial<NonNullable<BlueprintDraft['health']>['port']>) => {
      setDraft(current => ({
        ...current,
        health: {
          ...current.health,
          port: { ...(current.health?.port ?? defaults.health!.port!), ...next },
        },
      }))
    }
    const updateApplication = (next: Partial<NonNullable<BlueprintDraft['health']>['application']>) => {
      setDraft(current => ({
        ...current,
        health: {
          ...current.health,
          application: { ...(current.health?.application ?? defaults.health!.application!), ...next },
        },
      }))
    }
    const updateApplicationType = (type: GuardianApplicationProbeType | '') => {
      setDraft(current => {
        const application = { ...(current.health?.application ?? defaults.health!.application!), type }
        if (type !== 'http-ping') delete application.path
        return { ...current, health: { ...current.health, application } }
      })
    }
    const updateStartup = (next: Partial<NonNullable<BlueprintDraft['health']>['startup']>) => {
      setDraft(current => ({
        ...current,
        health: {
          ...current.health,
          startup: { ...(current.health?.startup ?? defaults.health!.startup!), ...next },
        },
      }))
    }

    const applyPreset = (presetId: string) => {
      if (presetId === 'minecraft') {
        setDraft(current => ({
          ...current,
          health: {
            process: { ...defaults.health!.process! },
            port: { ...defaults.health!.port!, protocol: 'tcp', port: '{{SERVER_PORT}}' },
            application: { ...defaults.health!.application!, type: 'minecraft-query', port: '{{SERVER_PORT}}' },
            startup: { ...defaults.health!.startup!, success_patterns: ['Done'], failure_patterns: ['Unable to access jarfile', 'Failed to bind to port'] }
          },
          logs: { ...defaults.logs!, sources: ['logs/latest.log'], redact: ['discord_token', 'api_key'] },
          diagnostics: { parsers: ['java-stacktrace', 'linux-oom', 'port-conflict', 'corrupted-config'] },
          recovery: {
            ...defaults.recovery!,
            policies: [{ match: 'port-conflict', action: 'restart' }, { match: 'linux-oom', action: 'graceful_restart' }],
          },
          backups: { before_risky_action: true, protected_paths: ['world/', 'plugins/', 'config/'] }
        }))
      } else if (presetId === 'steamcmd') {
        setDraft(current => ({
          ...current,
          health: {
            process: { ...defaults.health!.process! },
            port: { ...defaults.health!.port!, protocol: 'udp', port: '{{SERVER_PORT}}', timeout: '5s' },
            application: { ...defaults.health!.application!, type: 'source-query', interval: '60s', port: '{{SERVER_PORT}}' },
            startup: { ...defaults.health!.startup!, success_patterns: ['Connection to Steam servers successful', 'GC Connection established'], failure_patterns: ['Error checking out release', 'Failed to initialize network'] }
          },
          logs: { ...defaults.logs!, sources: ['logs/latest.log', 'stdout'], redact: ['api_key'] },
          diagnostics: { parsers: ['linux-oom', 'port-conflict', 'missing-runtime'] },
          recovery: {
            ...defaults.recovery!,
            policies: [{ match: 'port-conflict', action: 'restart' }, { match: 'linux-oom', action: 'graceful_restart' }],
          },
          backups: { before_risky_action: true, protected_paths: ['save/', 'config/'] }
        }))
      } else if (presetId === 'nodejs') {
        setDraft(current => ({
          ...current,
          health: {
            process: { ...defaults.health!.process! },
            port: { ...defaults.health!.port!, protocol: 'tcp', port: '{{SERVER_PORT}}' },
            application: { ...defaults.health!.application!, type: 'http-ping', path: '/api/healthz', port: '{{SERVER_PORT}}' },
            startup: { ...defaults.health!.startup!, success_patterns: ['App listening on port', 'Server started'], failure_patterns: ['npm ERR!', 'UnhandledPromiseRejectionWarning'] }
          },
          logs: { ...defaults.logs!, sources: ['stdout'], redact: ['discord_token', 'api_key', 'database_url'] },
          diagnostics: { parsers: ['linux-oom', 'port-conflict', 'nodejs-stacktrace'] },
          recovery: {
            ...defaults.recovery!,
            policies: [{ match: 'port-conflict', action: 'restart' }, { match: 'linux-oom', action: 'graceful_restart' }],
          },
          backups: { before_risky_action: true, protected_paths: ['data/'] }
        }))
      } else if (presetId === 'generic') {
        setGuardianEnabled(false)
      }
    }

    const appQueryOptions = [
      { value: '', label: t('blueprintBuilder.guardian.appQueryNone') },
      { value: 'tcp', label: 'TCP' },
      { value: 'minecraft-status', label: 'Minecraft Status (minecraft-status)' },
      { value: 'minecraft-query', label: 'Minecraft Query (minecraft-query)' },
      { value: 'source-query', label: 'Steam/Source Query (source-query)' },
      { value: 'http-ping', label: 'HTTP Ping / Health Check (http-ping)' },
    ]

    const selectedAppQuery = health.application?.type ?? ''

    return (
      <div className="min-w-0 max-w-full space-y-6">
        <div className="rounded-xl border border-outline-variant/60 bg-surface-container-low p-4">
          <label className="flex items-start gap-3">
            <input className="mt-1" type="checkbox" checked={guardianEnabled} onChange={event => setGuardianEnabled(event.target.checked)} />
            <span>
              <strong className="block text-sm">{t('blueprintBuilder.guardian.enabled')}</strong>
              <small className="msm-field-help block">{t('blueprintBuilder.guardian.enabledHelp')}</small>
            </span>
          </label>
        </div>

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
              aria-label={t('blueprintBuilder.guardian.presetsSelect')}
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

        {guardianEnabled && (
          <>
            <div className="min-w-0 max-w-full rounded-xl border border-outline-variant/60 bg-surface-container-low p-4 space-y-3">
              <h4 className="font-semibold text-lg">{t('blueprintBuilder.guardian.actionsTitle')}</h4>
              <p className="msm-field-help">{t('blueprintBuilder.guardian.actionsHelp')}</p>
              <div className="grid gap-2 sm:grid-cols-2">
                {(['restart', 'graceful_restart', 'clear_declared_lock_files', 'quarantine'] as const).map(action => (
                  <code key={action} className="min-w-0 max-w-full break-all rounded-md border border-outline-variant/40 bg-surface-container-lowest px-3 py-2 text-xs text-primary">{action}</code>
                ))}
              </div>
            </div>

        {/* Health Probe Configuration */}
        <div className="min-w-0 max-w-full rounded-xl border border-outline-variant/60 bg-surface-container-low p-4 space-y-4">
          <h4 className="font-semibold text-lg border-b border-outline-variant/40 pb-2">{t('blueprintBuilder.guardian.healthTitle')}</h4>
          <label className="flex items-start gap-3">
            <input
              className="mt-1"
              type="checkbox"
              checked={Boolean(health.process)}
              onChange={event => setDraft(current => {
                if (event.target.checked) {
                  return { ...current, health: { ...current.health, process: structuredClone(defaults.health!.process!) } }
                }
                const nextHealth = { ...current.health }
                delete nextHealth.process
                return { ...current, health: nextHealth }
              })}
            />
            <span>{t('blueprintBuilder.guardian.processEnabled')}<small className="msm-field-help block">{t('blueprintBuilder.guardian.processEnabledHelp')}</small></span>
          </label>
          <label className="flex items-start gap-3">
            <input
              className="mt-1"
              type="checkbox"
              checked={health.process?.required ?? true}
              disabled={!health.process}
              onChange={event => updateProcess({ required: event.target.checked })}
            />
            <span>{t('blueprintBuilder.guardian.processRequired')}<small className="msm-field-help block">{t('blueprintBuilder.guardian.processRequiredHelp')}</small></span>
          </label>
          <div className="grid min-w-0 gap-4 lg:grid-cols-3">
            <Field id="bp-health-port-proto" label={t('blueprintBuilder.fields.healthPortProto.label')} help={t('blueprintBuilder.fields.healthPortProto.help')}>
              <Dropdown
                value={health.port?.protocol ?? 'tcp'}
                onChange={value => updatePortHealth({ protocol: value as 'tcp' | 'udp' })}
                options={[{ value: 'tcp', label: 'TCP' }, { value: 'udp', label: 'UDP' }]}
              />
            </Field>
            <Field id="bp-health-port" label={t('blueprintBuilder.fields.healthPort.label')} help={t('blueprintBuilder.fields.healthPort.help')} error={issueFor('health.port.port')}>
              <input
                className="msm-input font-mono"
                value={health.port?.port ?? ''}
                onChange={event => updatePortHealth({ port: event.target.value })}
              />
            </Field>
            <Field id="bp-health-port-timeout" label={t('blueprintBuilder.fields.healthPortTimeout.label')} help={t('blueprintBuilder.fields.healthPortTimeout.help')} error={issueFor('health.port.timeout')}>
              <input
                className="msm-input font-mono"
                value={health.port?.timeout ?? ''}
                onChange={event => updatePortHealth({ timeout: event.target.value })}
              />
            </Field>
          </div>
          <div className="grid min-w-0 gap-4 lg:grid-cols-3">
            <div className="space-y-1">
              <label className="mb-1.5 block font-label-md text-sm font-semibold text-on-surface">
                {t('blueprintBuilder.fields.healthAppType.label')}
              </label>
              <Dropdown
                aria-label={t('blueprintBuilder.fields.healthAppType.label')}
                value={selectedAppQuery}
                options={appQueryOptions}
                onChange={next => updateApplicationType(next as GuardianApplicationProbeType | '')}
              />
              <p className="msm-field-help">{t('blueprintBuilder.fields.healthAppType.help')}</p>
            </div>
            <Field id="bp-health-app-interval" label={t('blueprintBuilder.fields.healthAppInterval.label')} help={t('blueprintBuilder.fields.healthAppInterval.help')} error={issueFor('health.application.interval')}>
              <input
                className="msm-input font-mono"
                value={health.application?.interval ?? ''}
                onChange={event => updateApplication({ interval: event.target.value })}
              />
            </Field>
            <Field id="bp-health-app-threshold" label={t('blueprintBuilder.fields.healthAppThreshold.label')} help={t('blueprintBuilder.fields.healthAppThreshold.help')} error={issueFor('health.application.failure_threshold')}>
              <NumberStepper
                min={1}
                max={20}
                value={health.application?.failure_threshold ?? 3}
                onValueChange={value => updateApplication({ failure_threshold: Number(value) })}
              />
            </Field>
          </div>

          {health.application?.type === 'http-ping' && (
            <div className="pt-2 border-t border-outline-variant/30">
              <Field id="bp-health-app-path" label={t('blueprintBuilder.fields.healthAppPath.label')} help={t('blueprintBuilder.fields.healthAppPath.help')} error={issueFor('health.application.path')}>
                <input
                  className="msm-input font-mono"
                  placeholder="e.g. /healthz"
                  value={health.application?.path ?? ''}
                  onChange={event => updateApplication({ path: event.target.value })}
                />
              </Field>
            </div>
          )}

          <details className="min-w-0 max-w-full rounded-lg border border-outline-variant/40 bg-surface-container-lowest/55 p-3">
            <summary className="break-words cursor-pointer text-sm font-semibold text-primary">{t('blueprintBuilder.guardian.advancedHealth')}</summary>
            <p className="msm-field-help mt-2">{t('blueprintBuilder.guardian.advancedHealthHelp')}</p>

            <fieldset className="mt-4 min-w-0 max-w-full space-y-3 border-t border-outline-variant/30 pt-4">
              <legend className="pr-2 text-sm font-semibold">{t('blueprintBuilder.guardian.processProbe')}</legend>
              <div className="grid min-w-0 gap-4 lg:grid-cols-3">
                <Field id="bp-process-id" label={t('blueprintBuilder.fields.guardianId.label')} help={t('blueprintBuilder.fields.guardianId.help')}>
                  <input className="msm-input font-mono" value={health.process?.id ?? defaults.health!.process!.id} onChange={event => updateProcess({ id: event.target.value })} />
                </Field>
                <Field id="bp-process-interval" label={t('blueprintBuilder.fields.guardianInterval.label')} help={t('blueprintBuilder.fields.guardianInterval.help')}>
                  <input className="msm-input font-mono" value={health.process?.interval ?? defaults.health!.process!.interval} onChange={event => updateProcess({ interval: event.target.value })} />
                </Field>
                <Field id="bp-process-failure" label={t('blueprintBuilder.fields.guardianFailureThreshold.label')} help={t('blueprintBuilder.fields.guardianFailureThreshold.help')}>
                  <NumberStepper min={1} max={20} value={health.process?.failure_threshold ?? 1} onValueChange={value => updateProcess({ failure_threshold: Number(value) })} />
                </Field>
                <Field id="bp-process-success" label={t('blueprintBuilder.fields.guardianSuccessThreshold.label')} help={t('blueprintBuilder.fields.guardianSuccessThreshold.help')}>
                  <NumberStepper min={1} max={20} value={health.process?.success_threshold ?? 1} onValueChange={value => updateProcess({ success_threshold: Number(value) })} />
                </Field>
              </div>
              <div className="grid gap-2 md:grid-cols-2">
                <label className="flex items-start gap-3 text-sm"><input className="mt-1" type="checkbox" checked={health.process?.required_for_startup ?? true} onChange={event => updateProcess({ required_for_startup: event.target.checked })} /><span>{t('blueprintBuilder.fields.guardianRequiredStartup.label')}<small className="msm-field-help block">{t('blueprintBuilder.fields.guardianRequiredStartup.help')}</small></span></label>
                <label className="flex items-start gap-3 text-sm"><input className="mt-1" type="checkbox" checked={health.process?.required_for_verification ?? true} onChange={event => updateProcess({ required_for_verification: event.target.checked })} /><span>{t('blueprintBuilder.fields.guardianRequiredVerification.label')}<small className="msm-field-help block">{t('blueprintBuilder.fields.guardianRequiredVerification.help')}</small></span></label>
              </div>
            </fieldset>

            <fieldset className="mt-4 min-w-0 max-w-full space-y-3 border-t border-outline-variant/30 pt-4">
              <legend className="pr-2 text-sm font-semibold">{t('blueprintBuilder.guardian.portProbe')}</legend>
              <div className="grid min-w-0 gap-4 lg:grid-cols-3">
                <Field id="bp-port-id" label={t('blueprintBuilder.fields.guardianId.label')} help={t('blueprintBuilder.fields.guardianId.help')}><input className="msm-input font-mono" value={health.port?.id ?? defaults.health!.port!.id} onChange={event => updatePortHealth({ id: event.target.value })} /></Field>
                <Field id="bp-port-interval" label={t('blueprintBuilder.fields.guardianInterval.label')} help={t('blueprintBuilder.fields.guardianInterval.help')}><input className="msm-input font-mono" value={health.port?.interval ?? defaults.health!.port!.interval} onChange={event => updatePortHealth({ interval: event.target.value })} /></Field>
                <Field id="bp-port-failure" label={t('blueprintBuilder.fields.guardianFailureThreshold.label')} help={t('blueprintBuilder.fields.guardianFailureThreshold.help')}><NumberStepper min={1} max={20} value={health.port?.failure_threshold ?? 3} onValueChange={value => updatePortHealth({ failure_threshold: Number(value) })} /></Field>
                <Field id="bp-port-success" label={t('blueprintBuilder.fields.guardianSuccessThreshold.label')} help={t('blueprintBuilder.fields.guardianSuccessThreshold.help')}><NumberStepper min={1} max={20} value={health.port?.success_threshold ?? 1} onValueChange={value => updatePortHealth({ success_threshold: Number(value) })} /></Field>
              </div>
              <div className="grid gap-2 md:grid-cols-2">
                <label className="flex items-start gap-3 text-sm"><input className="mt-1" type="checkbox" checked={health.port?.required_for_startup ?? false} onChange={event => updatePortHealth({ required_for_startup: event.target.checked })} /><span>{t('blueprintBuilder.fields.guardianRequiredStartup.label')}<small className="msm-field-help block">{t('blueprintBuilder.fields.guardianRequiredStartup.help')}</small></span></label>
                <label className="flex items-start gap-3 text-sm"><input className="mt-1" type="checkbox" checked={health.port?.required_for_verification ?? true} onChange={event => updatePortHealth({ required_for_verification: event.target.checked })} /><span>{t('blueprintBuilder.fields.guardianRequiredVerification.label')}<small className="msm-field-help block">{t('blueprintBuilder.fields.guardianRequiredVerification.help')}</small></span></label>
              </div>
            </fieldset>

            <fieldset className="mt-4 min-w-0 max-w-full space-y-3 border-t border-outline-variant/30 pt-4">
              <legend className="pr-2 text-sm font-semibold">{t('blueprintBuilder.guardian.applicationProbe')}</legend>
              <div className="grid min-w-0 gap-4 lg:grid-cols-3">
                <Field id="bp-app-id" label={t('blueprintBuilder.fields.guardianId.label')} help={t('blueprintBuilder.fields.guardianId.help')}><input className="msm-input font-mono" value={health.application?.id ?? defaults.health!.application!.id} onChange={event => updateApplication({ id: event.target.value })} /></Field>
                <Field id="bp-health-app-port" label={t('blueprintBuilder.fields.healthAppPort.label')} help={t('blueprintBuilder.fields.healthAppPort.help')}><input className="msm-input font-mono" value={health.application?.port ?? ''} onChange={event => updateApplication({ port: event.target.value })} /></Field>
                <Field id="bp-app-timeout" label={t('blueprintBuilder.fields.guardianTimeout.label')} help={t('blueprintBuilder.fields.guardianTimeout.help')}><input className="msm-input font-mono" value={health.application?.timeout ?? defaults.health!.application!.timeout} onChange={event => updateApplication({ timeout: event.target.value })} /></Field>
                <Field id="bp-app-success" label={t('blueprintBuilder.fields.guardianSuccessThreshold.label')} help={t('blueprintBuilder.fields.guardianSuccessThreshold.help')}><NumberStepper min={1} max={20} value={health.application?.success_threshold ?? 1} onValueChange={value => updateApplication({ success_threshold: Number(value) })} /></Field>
                <Field id="bp-app-statuses" label={t('blueprintBuilder.fields.healthExpectedStatuses.label')} help={t('blueprintBuilder.fields.healthExpectedStatuses.help')}><input className="msm-input font-mono" value={(health.application?.expected_statuses ?? [200]).join(', ')} onChange={event => updateApplication({ expected_statuses: event.target.value.split(',').map(value => Number(value.trim())).filter(Number.isFinite) })} /></Field>
                <Field id="bp-app-response-bytes" label={t('blueprintBuilder.fields.healthResponseBytes.label')} help={t('blueprintBuilder.fields.healthResponseBytes.help')}><NumberStepper min={1} max={1_048_576} value={health.application?.max_response_bytes ?? 4096} onValueChange={value => updateApplication({ max_response_bytes: Number(value) })} /></Field>
              </div>
              <div className="grid gap-2 md:grid-cols-2">
                <label className="flex items-start gap-3 text-sm"><input className="mt-1" type="checkbox" checked={health.application?.required_for_startup ?? false} onChange={event => updateApplication({ required_for_startup: event.target.checked })} /><span>{t('blueprintBuilder.fields.guardianRequiredStartup.label')}<small className="msm-field-help block">{t('blueprintBuilder.fields.guardianRequiredStartup.help')}</small></span></label>
                <label className="flex items-start gap-3 text-sm"><input className="mt-1" type="checkbox" checked={health.application?.required_for_verification ?? true} onChange={event => updateApplication({ required_for_verification: event.target.checked })} /><span>{t('blueprintBuilder.fields.guardianRequiredVerification.label')}<small className="msm-field-help block">{t('blueprintBuilder.fields.guardianRequiredVerification.help')}</small></span></label>
                <label className="flex items-start gap-3 text-sm text-on-surface-variant"><input className="mt-1" type="checkbox" checked={false} disabled /><span>{t('blueprintBuilder.fields.healthFollowRedirects.label')}<small className="msm-field-help block">{t('blueprintBuilder.fields.healthFollowRedirects.help')}</small></span></label>
              </div>
            </fieldset>

            <fieldset className="mt-4 min-w-0 max-w-full space-y-3 border-t border-outline-variant/30 pt-4">
              <legend className="pr-2 text-sm font-semibold">{t('blueprintBuilder.guardian.startupWindow')}</legend>
              <div className="grid gap-4 md:grid-cols-2">
                <Field id="bp-startup-grace" label={t('blueprintBuilder.fields.healthStartupGrace.label')} help={t('blueprintBuilder.fields.healthStartupGrace.help')}><NumberStepper min={0} max={600} value={health.startup?.grace_period_seconds ?? 30} onValueChange={value => updateStartup({ grace_period_seconds: Number(value) })} /></Field>
                <Field id="bp-startup-timeout" label={t('blueprintBuilder.fields.healthStartupTimeout.label')} help={t('blueprintBuilder.fields.healthStartupTimeout.help')} error={issueFor('health.startup.timeout_seconds')}><NumberStepper min={1} max={3600} value={health.startup?.timeout_seconds ?? 300} onValueChange={value => updateStartup({ timeout_seconds: Number(value) })} /></Field>
              </div>
            </fieldset>
          </details>

          <div className="grid gap-4 md:grid-cols-2">
            <LinesField
              id="bp-health-success-patterns"
              label={t('blueprintBuilder.fields.healthSuccessPatterns.label')}
              help={t('blueprintBuilder.fields.healthSuccessPatterns.help')}
              value={health.startup?.success_patterns ?? []}
              onChange={success_patterns => updateStartup({ success_patterns })}
            />
            <LinesField
              id="bp-health-failure-patterns"
              label={t('blueprintBuilder.fields.healthFailurePatterns.label')}
              help={t('blueprintBuilder.fields.healthFailurePatterns.help')}
              value={health.startup?.failure_patterns ?? []}
              onChange={failure_patterns => updateStartup({ failure_patterns })}
            />
          </div>
        </div>

        {/* Logs & Diagnostics Configuration */}
        <div className="min-w-0 max-w-full rounded-xl border border-outline-variant/60 bg-surface-container-low p-4 space-y-4">
          <h4 className="font-semibold text-lg border-b border-outline-variant/40 pb-2">{t('blueprintBuilder.guardian.logsTitle')}</h4>
          <div className="grid gap-4 md:grid-cols-2">
            <LinesField
              id="bp-logs-sources"
              label={t('blueprintBuilder.fields.logsSources.label')}
              help={t('blueprintBuilder.fields.logsSources.help')}
              value={logs.sources}
              onChange={sources => setDraft(current => ({ ...current, logs: { ...(current.logs ?? defaults.logs!), sources } }))}
            />
            <LinesField
              id="bp-logs-redact"
              label={t('blueprintBuilder.fields.logsRegexRedact.label')}
              help={t('blueprintBuilder.fields.logsRegexRedact.help')}
              value={logs.redact.filter(value => value.startsWith('regex:')).map(value => value.slice(6))}
              onChange={patterns => setDraft(current => ({
                ...current,
                logs: {
                  ...(current.logs ?? defaults.logs!),
                  redact: [
                    ...(current.logs ?? defaults.logs!).redact.filter(value => !value.startsWith('regex:')),
                    ...patterns.map(value => `regex:${value}`),
                  ],
                },
              }))}
            />
          </div>
          <fieldset className="space-y-2">
            <legend className="text-sm font-semibold">{t('blueprintBuilder.fields.logsRedact.label')}</legend>
            <p className="msm-field-help">{t('blueprintBuilder.fields.logsRedact.help')}</p>
            <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
              {(['discord_token', 'api_key', 'authorization_header', 'database_url', 'jwt'] as const).map(redactor => (
                <label key={redactor} className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={logs.redact.includes(redactor)}
                    onChange={event => setDraft(current => {
                      const currentLogs = current.logs ?? defaults.logs!
                      return {
                        ...current,
                        logs: {
                          ...currentLogs,
                          redact: event.target.checked
                            ? [...currentLogs.redact, redactor]
                            : currentLogs.redact.filter(value => value !== redactor),
                        },
                      }
                    })}
                  />
                  <code>{redactor}</code>
                </label>
              ))}
            </div>
          </fieldset>
          <Field id="bp-logs-max-tail" label={t('blueprintBuilder.fields.logsMaxTail.label')} help={t('blueprintBuilder.fields.logsMaxTail.help')} error={issueFor('logs.max_tail_bytes')}>
            <NumberStepper min={1024} max={1_048_576} value={logs.max_tail_bytes} onValueChange={value => setDraft(current => ({ ...current, logs: { ...(current.logs ?? defaults.logs!), max_tail_bytes: Number(value) } }))} />
          </Field>
          <fieldset className="space-y-2">
            <legend className="text-sm font-semibold">{t('blueprintBuilder.fields.diagnosticsParsers.label')}</legend>
            <p className="msm-field-help">{t('blueprintBuilder.fields.diagnosticsParsers.help')}</p>
            <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
              {(['linux-oom', 'java-stacktrace', 'nodejs-stacktrace', 'port-conflict', 'missing-runtime', 'corrupted-config', 'startup-pattern'] as GuardianDiagnosticParser[]).map(parser => (
                <label key={parser} className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={diagnostics.parsers.includes(parser)}
                    onChange={event => setDraft(current => {
                      const currentDiagnostics = current.diagnostics ?? defaults.diagnostics!
                      return {
                        ...current,
                        diagnostics: {
                          ...currentDiagnostics,
                          parsers: event.target.checked
                            ? [...currentDiagnostics.parsers, parser]
                            : currentDiagnostics.parsers.filter(value => value !== parser),
                        },
                      }
                    })}
                  />
                  <code>{parser}</code>
                </label>
              ))}
            </div>
          </fieldset>
        </div>

        {/* Recovery policies config */}
        <div className="min-w-0 max-w-full rounded-xl border border-outline-variant/60 bg-surface-container-low p-4 space-y-4">
          <h4 className="font-semibold text-lg border-b border-outline-variant/40 pb-2">{t('blueprintBuilder.guardian.recoveryTitle')}</h4>
          <RecoveryPoliciesEditor
            value={recovery.policies}
            onChange={policies => setDraft(current => ({ ...current, recovery: { ...(current.recovery ?? defaults.recovery!), policies } }))}
          />
          <div className="min-w-0 max-w-full border-t border-outline-variant/30 pt-4">
            <div className="flex min-w-0 flex-col items-start gap-3 sm:flex-row sm:justify-between">
              <div className="min-w-0 max-w-full">
                <h5 className="text-sm font-semibold">{t('blueprintBuilder.guardian.safeLockFiles')}</h5>
                <p className="msm-field-help">{t('blueprintBuilder.guardian.safeLockFilesHelp')}</p>
              </div>
              <Button
                variant="secondary"
                className="h-auto min-h-10 max-w-full whitespace-normal text-left"
                disabled={(recovery.safe_lock_files?.length ?? 0) >= 32}
                onClick={() => setDraft(current => {
                  const currentRecovery = current.recovery ?? defaults.recovery!
                  return { ...current, recovery: { ...currentRecovery, safe_lock_files: [...(currentRecovery.safe_lock_files ?? []), { path: '', reason: '' }] } }
                })}
              >
                <Plus className="h-4 w-4" aria-hidden="true" />{t('blueprintBuilder.guardian.addSafeLockFile')}
              </Button>
            </div>
            <div className="mt-3 space-y-2">
              {(recovery.safe_lock_files ?? []).map((entry, index) => (
                <div key={index} className="grid min-w-0 gap-2 rounded-lg border border-outline-variant/40 p-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.5fr)_auto]">
                  <input aria-label={t('blueprintBuilder.guardian.safeLockPath', { index: index + 1 })} className="msm-input font-mono" value={entry.path} onChange={event => setDraft(current => {
                    const currentRecovery = current.recovery ?? defaults.recovery!
                    return { ...current, recovery: { ...currentRecovery, safe_lock_files: (currentRecovery.safe_lock_files ?? []).map((item, itemIndex) => itemIndex === index ? { ...item, path: event.target.value } : item) } }
                  })} />
                  <input aria-label={t('blueprintBuilder.guardian.safeLockReason', { index: index + 1 })} className="msm-input" value={entry.reason} onChange={event => setDraft(current => {
                    const currentRecovery = current.recovery ?? defaults.recovery!
                    return { ...current, recovery: { ...currentRecovery, safe_lock_files: (currentRecovery.safe_lock_files ?? []).map((item, itemIndex) => itemIndex === index ? { ...item, reason: event.target.value } : item) } }
                  })} />
                  <Button variant="ghost" aria-label={t('blueprintBuilder.guardian.removeSafeLockFile', { index: index + 1 })} onClick={() => setDraft(current => {
                    const currentRecovery = current.recovery ?? defaults.recovery!
                    return { ...current, recovery: { ...currentRecovery, safe_lock_files: (currentRecovery.safe_lock_files ?? []).filter((_, itemIndex) => itemIndex !== index) } }
                  })}><Trash2 className="h-4 w-4" aria-hidden="true" /></Button>
                </div>
              ))}
            </div>
          </div>
          <details className="min-w-0 max-w-full rounded-lg border border-outline-variant/40 bg-surface-container-lowest/55 p-3">
            <summary className="break-words cursor-pointer text-sm font-semibold text-primary">{t('blueprintBuilder.guardian.advancedRecovery')}</summary>
            <p className="msm-field-help mt-2">{t('blueprintBuilder.guardian.advancedRecoveryHelp')}</p>
            <div className="mt-4 grid min-w-0 gap-4 lg:grid-cols-3">
              <Field id="bp-recovery-max-attempts" label={t('blueprintBuilder.fields.recoveryMaxAttempts.label')} help={t('blueprintBuilder.fields.recoveryMaxAttempts.help')}><NumberStepper min={1} max={10} value={recovery.max_attempts ?? 3} onValueChange={value => setDraft(current => ({ ...current, recovery: { ...(current.recovery ?? defaults.recovery!), max_attempts: Number(value) } }))} /></Field>
              <Field id="bp-recovery-attempt-window" label={t('blueprintBuilder.fields.recoveryAttemptWindow.label')} help={t('blueprintBuilder.fields.recoveryAttemptWindow.help')}><NumberStepper min={60} max={86_400} value={recovery.attempt_window_seconds ?? 1800} onValueChange={value => setDraft(current => ({ ...current, recovery: { ...(current.recovery ?? defaults.recovery!), attempt_window_seconds: Number(value) } }))} /></Field>
              <Field id="bp-recovery-cooldown" label={t('blueprintBuilder.fields.recoveryCooldown.label')} help={t('blueprintBuilder.fields.recoveryCooldown.help')}><NumberStepper min={1} max={3600} value={recovery.cooldown_seconds ?? 300} onValueChange={value => setDraft(current => ({ ...current, recovery: { ...(current.recovery ?? defaults.recovery!), cooldown_seconds: Number(value) } }))} /></Field>
              <Field id="bp-verification-duration" label={t('blueprintBuilder.fields.verificationDuration.label')} help={t('blueprintBuilder.fields.verificationDuration.help')}><NumberStepper min={0} max={600} value={recovery.verification?.minimum_healthy_duration_seconds ?? 30} onValueChange={value => setDraft(current => {
                const currentRecovery = current.recovery ?? defaults.recovery!
                return { ...current, recovery: { ...currentRecovery, verification: { ...(currentRecovery.verification ?? defaults.recovery!.verification!), minimum_healthy_duration_seconds: Number(value) } } }
              })} /></Field>
              <Field id="bp-verification-successes" label={t('blueprintBuilder.fields.verificationSuccesses.label')} help={t('blueprintBuilder.fields.verificationSuccesses.help')}><NumberStepper min={1} max={20} value={recovery.verification?.required_consecutive_successes ?? 3} onValueChange={value => setDraft(current => {
                const currentRecovery = current.recovery ?? defaults.recovery!
                return { ...current, recovery: { ...currentRecovery, verification: { ...(currentRecovery.verification ?? defaults.recovery!.verification!), required_consecutive_successes: Number(value) } } }
              })} /></Field>
              <Field id="bp-verification-timeout" label={t('blueprintBuilder.fields.verificationTimeout.label')} help={t('blueprintBuilder.fields.verificationTimeout.help')}><NumberStepper min={5} max={3600} value={recovery.verification?.verification_timeout_seconds ?? 180} onValueChange={value => setDraft(current => {
                const currentRecovery = current.recovery ?? defaults.recovery!
                return { ...current, recovery: { ...currentRecovery, verification: { ...(currentRecovery.verification ?? defaults.recovery!.verification!), verification_timeout_seconds: Number(value) } } }
              })} /></Field>
            </div>
          </details>
        </div>

        {/* Backups */}
        <div className="min-w-0 max-w-full rounded-xl border border-outline-variant/60 bg-surface-container-low p-4 space-y-4">
          <h4 className="font-semibold text-lg border-b border-outline-variant/40 pb-2">{t('blueprintBuilder.guardian.backupsTitle')}</h4>
          <label className="flex items-center gap-3">
            <input
              type="checkbox"
              checked={backups.before_risky_action}
              onChange={event => setDraft(current => ({ ...current, backups: { ...(current.backups ?? defaults.backups!), before_risky_action: event.target.checked } }))}
            />
            <span>{t('blueprintBuilder.guardian.beforeRiskyAction')}<small className="msm-field-help block">{t('blueprintBuilder.guardian.beforeRiskyActionHelp')}</small></span>
          </label>
          <LinesField
            id="bp-backups-protected"
            label={t('blueprintBuilder.fields.backupsProtected.label')}
            help={t('blueprintBuilder.fields.backupsProtected.help')}
            value={backups.protected_paths}
            onChange={protected_paths => setDraft(current => ({ ...current, backups: { ...(current.backups ?? defaults.backups!), protected_paths } }))}
          />
        </div>
          </>
        )}
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
    <div className="fixed inset-0 z-50 flex min-h-0 min-w-0 items-stretch bg-black/70 backdrop-blur-sm lg:pl-64" role="dialog" aria-modal="true" aria-labelledby="blueprint-builder-title" aria-describedby="blueprint-builder-description" data-testid="blueprint-builder-overlay">
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
          <div className="grid min-h-0 min-w-0 flex-1 grid-rows-[auto_minmax(0,1fr)] overflow-hidden lg:grid-cols-[14rem_minmax(0,1fr)] lg:grid-rows-1">
            <nav className="min-w-0 overflow-x-auto border-b border-outline-variant/50 p-2 lg:overflow-y-auto lg:border-b-0 lg:border-r lg:p-3" aria-label={t('blueprintBuilder.sectionNavigation')}>
              <ol className="grid w-full grid-cols-4 gap-1 lg:grid-cols-1">
                {sectionIds.map((item, index) => <li key={item} className="min-w-0"><button type="button" onClick={() => setSection(item)} aria-current={section === item ? 'step' : undefined} className={`flex min-h-11 min-w-0 w-full flex-col items-center justify-center gap-0.5 rounded-lg px-1 text-center text-[11px] leading-tight lg:flex-row lg:justify-start lg:gap-3 lg:px-3 lg:text-left lg:text-sm ${section === item ? 'bg-primary/10 text-primary ring-1 ring-primary/20' : 'text-on-surface-variant hover:bg-surface-container-high'}`}><span className="font-mono text-[10px] opacity-60 lg:text-xs">{String(index + 1).padStart(2, '0')}</span><span className="min-w-0 break-words">{sectionLabel(item)}</span></button></li>)}
              </ol>
            </nav>
            <div className="min-h-0 min-w-0 overflow-auto" tabIndex={0} aria-label={t('blueprintBuilder.fieldsArea')}>
              <form className="mx-auto min-w-0 max-w-4xl space-y-6 p-4 md:p-7" onSubmit={event => event.preventDefault()}>
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
        <footer className="grid shrink-0 grid-cols-[auto_minmax(0,1fr)] items-center gap-2 border-t border-outline-variant/60 bg-surface-container-low/80 px-3 py-2 sm:px-4 sm:py-3 md:px-6" data-testid="blueprint-builder-actions">
          <Button
            variant="secondary"
            className="min-h-11 w-11 px-0 sm:w-auto sm:px-4"
            aria-label={t('common.back')}
            title={t('common.back')}
            disabled={currentIndex === 0}
            onClick={() => setSection(sectionIds[currentIndex - 1])}
          >
            <ChevronLeft className="h-4 w-4" aria-hidden="true" />
            <span className="hidden sm:inline">{t('common.back')}</span>
          </Button>
          <div className="grid min-w-0 grid-cols-[auto_minmax(0,1fr)] gap-2 sm:ml-auto sm:flex sm:flex-wrap sm:justify-end">
            <Button
              variant="secondary"
              className="min-h-11 w-11 px-0 sm:w-auto sm:px-4"
              aria-label={t('blueprintBuilder.downloadJson')}
              title={t('blueprintBuilder.downloadJson')}
              onClick={downloadDraft}
            >
              <Download className="h-4 w-4" aria-hidden="true" />
              <span className="hidden sm:inline">{t('blueprintBuilder.downloadJson')}</span>
            </Button>
            {currentIndex < sectionIds.length - 1 ? (
              <Button className="min-h-11 min-w-0 w-full sm:w-auto" onClick={() => setSection(sectionIds[currentIndex + 1])}><span className="min-w-0 truncate">{t('common.next')}</span><ChevronRight className="h-4 w-4 shrink-0" aria-hidden="true" /></Button>
            ) : (
              <Button className="min-h-11 min-w-0 w-full sm:w-auto" disabled={saving || issues.length > 0} onClick={saveDraft}><Save className="h-4 w-4 shrink-0" aria-hidden="true" /><span className="min-w-0 truncate">{saving ? t('blueprintBuilder.saving') : mode === 'edit' ? t('blueprintBuilder.saveChanges') : t('blueprintBuilder.addDirectly')}</span></Button>
            )}
          </div>
        </footer>
      </div>
    </div>,
    document.body,
  )
}
