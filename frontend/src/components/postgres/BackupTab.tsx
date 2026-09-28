import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Download, FileUp, RotateCcw, ShieldCheck, Trash2 } from 'lucide-react'
import { Badge, Button, Dropdown, FileButton, MultiSelect, Switch } from '@/Singra/UI'
import { prompt } from '@/stores/promptStore'
import { toast } from '@/stores/toastStore'
import { useStudio } from './StudioContext'
import { Empty, ErrorBox, Field, Section, formatDate, useLoad } from './shared'
import { fileToBase64, formatBytes, saveDownload } from './studioApi'

type Format = 'plain' | 'custom' | 'tar'
type Scope = 'all' | 'schema' | 'data'

function formatOf(fileName: string): Format {
  const lower = fileName.toLowerCase()
  if (lower.endsWith('.tar')) return 'tar'
  if (lower.endsWith('.dump') || lower.endsWith('.backup') || lower.endsWith('.pgdump')) return 'custom'
  return 'plain'
}

export function BackupTab() {
  const { dedicated } = useStudio()
  return (
    <div className="space-y-4">
      <DumpSection />
      <RestoreSection />
      {dedicated && <PendingSection />}
    </div>
  )
}

function DumpSection() {
  const { t, i18n } = useTranslation()
  const { api, overview } = useStudio()
  const [format, setFormat] = useState<Format>('custom')
  const [scope, setScope] = useState<Scope>('all')
  const [schemas, setSchemas] = useState<string[]>([])
  const [tables, setTables] = useState<string[]>([])
  const [tableOptions, setTableOptions] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [last, setLast] = useState<{ name: string; size: number; sha: string } | null>(null)

  useEffect(() => {
    let active = true
    Promise.all(overview.schemas.map((s) => api.objects(s.name).then((o) => o.relations.filter((r) => r.kind === 'table' || r.kind === 'partitioned_table').map((r) => `${s.name}.${r.name}`))))
      .then((lists) => active && setTableOptions(lists.flat()))
      .catch(() => active && setTableOptions([]))
    return () => {
      active = false
    }
  }, [api, overview.schemas])

  const dump = async () => {
    setBusy(true)
    setError(null)
    try {
      const file = await api.dump({
        format,
        schema_only: scope === 'schema',
        data_only: scope === 'data',
        schemas,
        tables: tables.map((entry) => {
          const [schema_name, ...rest] = entry.split('.')
          return { schema_name, name: rest.join('.') }
        }),
      })
      saveDownload(file)
      setLast({ name: file.filename, size: Number(file.headers.get('X-MSM-Dump-Size') || file.blob.size), sha: file.headers.get('X-MSM-Dump-SHA256') || '' })
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Section title={t('postgresStudio.backup.dumpTitle')}>
      <div className="grid gap-4 md:grid-cols-2">
        <Field label={t('postgresStudio.backup.format')} hint={t(`postgresStudio.backup.formatHint.${format}`)}>
          <Dropdown
            value={format}
            onChange={(v) => setFormat(v as Format)}
            options={[
              { value: 'custom', label: t('postgresStudio.backup.formats.custom') },
              { value: 'plain', label: t('postgresStudio.backup.formats.plain') },
              { value: 'tar', label: t('postgresStudio.backup.formats.tar') },
            ]}
            aria-label={t('postgresStudio.backup.format')}
          />
        </Field>
        <Field label={t('postgresStudio.backup.scope')}>
          <Dropdown
            value={scope}
            onChange={(v) => setScope(v as Scope)}
            options={(['all', 'schema', 'data'] as Scope[]).map((value) => ({ value, label: t(`postgresStudio.backup.scopes.${value}`) }))}
            aria-label={t('postgresStudio.backup.scope')}
          />
        </Field>
        <Field label={t('postgresStudio.backup.schemas')} hint={t('postgresStudio.backup.emptyMeansAll')}>
          <MultiSelect options={overview.schemas.map((s) => ({ value: s.name, label: s.name }))} values={schemas} onChange={setSchemas} placeholder={t('postgresStudio.backup.all')} aria-label={t('postgresStudio.backup.schemas')} />
        </Field>
        <Field label={t('postgresStudio.backup.tables')} hint={t('postgresStudio.backup.emptyMeansAll')}>
          <MultiSelect options={tableOptions.map((value) => ({ value, label: value }))} values={tables} onChange={setTables} placeholder={t('postgresStudio.backup.all')} aria-label={t('postgresStudio.backup.tables')} />
        </Field>
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <Button onClick={() => void dump()} disabled={busy} data-testid="dump-button">
          <Download className="h-4 w-4" />
          {busy ? t('common.working') : t('postgresStudio.backup.download')}
        </Button>
      </div>
      <div className="mt-3">
        <ErrorBox message={error} />
      </div>
      {last && (
        <div className="mt-3 rounded-lg border border-status-success/40 bg-status-success/5 p-3 text-xs" data-testid="dump-result">
          <p className="flex items-center gap-2 text-on-surface">
            <ShieldCheck className="h-4 w-4 text-status-success" />
            {last.name} · {formatBytes(last.size, i18n.language)}
          </p>
          <p className="mt-1 break-all font-mono text-on-surface-variant">SHA256 {last.sha}</p>
        </div>
      )}
    </Section>
  )
}

function RestoreSection() {
  const { t } = useTranslation()
  const { api, databaseName, bump } = useStudio()
  const [file, setFile] = useState<File | null>(null)
  const [format, setFormat] = useState<Format>('plain')
  const [clean, setClean] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const restore = async () => {
    if (!file) return
    const typed = await prompt({
      title: t('postgresStudio.backup.restoreConfirmTitle'),
      message: t('postgresStudio.backup.restoreConfirm', { database: databaseName, file: file.name }),
      expectedValue: databaseName,
      confirmText: t('postgresStudio.backup.restore'),
      danger: true,
    })
    if (!typed) return
    setBusy(true)
    setError(null)
    try {
      const result = await api.restore({ format, data_b64: await fileToBase64(file), clean: format !== 'plain' && clean, confirm_name: typed })
      toast.success(t('postgresStudio.backup.restored', { seconds: (result.duration_ms / 1000).toFixed(1) }))
      setFile(null)
      bump()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Section title={t('postgresStudio.backup.restoreTitle')}>
      <p className="mb-3 text-xs text-on-surface-variant">{t('postgresStudio.backup.restoreHint')}</p>
      <div className="flex flex-wrap items-center gap-3">
        <FileButton
          accept=".sql,.dump,.backup,.pgdump,.tar"
          onFile={(picked) => {
            setFile(picked)
            setFormat(formatOf(picked.name))
          }}
          data-testid="restore-file"
        >
          <FileUp className="h-4 w-4" />
          {t('postgresStudio.backup.pickFile')}
        </FileButton>
        {file && (
          <>
            <span className="font-mono text-xs text-on-surface">{file.name}</span>
            <Badge>{t(`postgresStudio.backup.formats.${format}`)}</Badge>
          </>
        )}
      </div>
      {file && format !== 'plain' && (
        <label className="mt-3 flex items-center gap-2 text-sm text-on-surface">
          <Switch checked={clean} onCheckedChange={setClean} />
          {t('postgresStudio.backup.clean')}
        </label>
      )}
      {file && (
        <Button className="mt-4" variant="destructive" onClick={() => void restore()} disabled={busy} data-testid="restore-button">
          <RotateCcw className="h-4 w-4" />
          {busy ? t('common.working') : t('postgresStudio.backup.restore')}
        </Button>
      )}
      <div className="mt-3">
        <ErrorBox message={error} />
      </div>
    </Section>
  )
}

/** Dumps, die ein Server-Backup-Restore des Datenbankservers liegen liess. */
function PendingSection() {
  const { t, i18n } = useTranslation()
  const { api, bump } = useStudio()
  const pending = useLoad(() => api.pending(), [api])
  const [busy, setBusy] = useState(false)
  const act = async (kind: 'apply' | 'discard') => {
    setBusy(true)
    try {
      if (kind === 'apply') {
        await api.applyPending()
        toast.success(t('postgresStudio.backup.pendingApplied'))
        bump()
      } else {
        await api.discardPending()
        toast.success(t('postgresStudio.backup.pendingDiscarded'))
      }
      await pending.reload()
    } catch (err) {
      toast.error((err as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Section title={t('postgresStudio.backup.pendingTitle')}>
      <p className="mb-3 text-xs text-on-surface-variant">{t('postgresStudio.backup.pendingHint')}</p>
      <ErrorBox message={pending.error} />
      {pending.data && pending.data.length === 0 && <Empty>{t('postgresStudio.backup.noPending')}</Empty>}
      {pending.data?.map((dump) => (
        <div key={dump.database} className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-status-warning/40 bg-status-warning/5 p-3">
          <span className="text-sm text-on-surface">
            <span className="font-mono">{dump.database}.sql</span> · {formatBytes(dump.size_bytes, i18n.language)} · {formatDate(new Date(dump.modified * 1000).toISOString(), i18n.language)}
          </span>
          <span className="flex gap-2">
            <Button size="sm" onClick={() => void act('apply')} disabled={busy}>{t('postgresStudio.backup.applyPending')}</Button>
            <Button size="sm" variant="ghost" onClick={() => void act('discard')} disabled={busy}>
              <Trash2 className="h-3.5 w-3.5" />
              {t('postgresStudio.backup.discardPending')}
            </Button>
          </span>
        </div>
      ))}
    </Section>
  )
}
