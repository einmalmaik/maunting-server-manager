import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { FileUp } from 'lucide-react'
import { Dropdown, FileButton } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'
import { FormDialog } from './FormDialog'
import { useStudio } from './StudioContext'
import { cellText, type Json, type StudioTableDetails } from './studioApi'

const CHUNK = 5000
const SKIP = '__skip__'

interface Parsed {
  kind: 'csv' | 'json' | 'sql'
  headers: string[]
  rows: Json[][]
  sql?: string
}

/** CSV nach RFC 4180: Anführungszeichen, verdoppelte Quotes, Zeilenumbrüche im Feld. */
export function parseCsv(text: string, delimiter?: string): string[][] {
  const sep = delimiter || (text.split('\n', 1)[0].split(';').length > text.split('\n', 1)[0].split(',').length ? ';' : ',')
  const rows: string[][] = []
  let row: string[] = []
  let field = ''
  let quoted = false
  for (let i = 0; i < text.length; i++) {
    const ch = text[i]
    if (quoted) {
      if (ch === '"') {
        if (text[i + 1] === '"') {
          field += '"'
          i++
        } else {
          quoted = false
        }
      } else {
        field += ch
      }
    } else if (ch === '"' && field === '') {
      quoted = true
    } else if (ch === sep) {
      row.push(field)
      field = ''
    } else if (ch === '\n' || ch === '\r') {
      if (ch === '\r' && text[i + 1] === '\n') i++
      row.push(field)
      if (row.length > 1 || row[0] !== '') rows.push(row)
      row = []
      field = ''
    } else {
      field += ch
    }
  }
  if (field !== '' || row.length) {
    row.push(field)
    rows.push(row)
  }
  return rows
}

async function parseFile(file: File): Promise<Parsed> {
  const text = await file.text()
  const name = file.name.toLowerCase()
  if (name.endsWith('.sql')) return { kind: 'sql', headers: [], rows: [], sql: text }
  if (name.endsWith('.json')) {
    const data = JSON.parse(text) as unknown
    if (!Array.isArray(data)) throw new Error('JSON')
    const headers = [...new Set(data.flatMap((row) => (row && typeof row === 'object' ? Object.keys(row as object) : [])))]
    return { kind: 'json', headers, rows: data.map((row) => headers.map((h) => ((row as Record<string, Json>)[h] ?? null))) }
  }
  const [headers = [], ...rows] = parseCsv(text)
  return { kind: 'csv', headers, rows }
}

export function ImportWizard({ open, table, onClose, onImported }: {
  open: boolean
  table: StudioTableDetails
  onClose: () => void
  onImported: () => void
}) {
  const { t } = useTranslation()
  const { api, canAdmin } = useStudio()
  const [parsed, setParsed] = useState<Parsed | null>(null)
  const [fileName, setFileName] = useState('')
  const [mapping, setMapping] = useState<string[]>([])
  const [emptyIsNull, setEmptyIsNull] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (open) {
      setParsed(null)
      setFileName('')
      setMapping([])
      setError(null)
    }
  }, [open])

  const columns = table.columns.filter((c) => !c.generated).map((c) => c.name)

  const choose = async (file: File) => {
    setError(null)
    try {
      const result = await parseFile(file)
      setParsed(result)
      setFileName(file.name)
      setMapping(result.headers.map((h) => (columns.includes(h) ? h : SKIP)))
    } catch {
      setError(t('postgresStudio.import.unreadable'))
    }
  }

  const submit = async () => {
    if (!parsed) return
    setBusy(true)
    setError(null)
    try {
      if (parsed.kind === 'sql') {
        await api.sql({ sql: parsed.sql || '' })
        toast.success(t('postgresStudio.import.sqlDone'))
      } else {
        const picked = mapping.map((target, index) => ({ target, index })).filter((m) => m.target !== SKIP)
        if (!picked.length) {
          setError(t('postgresStudio.import.noMapping'))
          return
        }
        if (new Set(picked.map((m) => m.target)).size !== picked.length) {
          setError(t('postgresStudio.import.duplicateMapping'))
          return
        }
        const rows = parsed.rows.map((row) =>
          picked.map(({ index }) => {
            const value = row[index] ?? null
            return emptyIsNull && value === '' ? null : value
          }),
        )
        let inserted = 0
        for (let start = 0; start < rows.length; start += CHUNK) {
          const result = await api.importRows(table.schema, table.table, picked.map((m) => m.target), rows.slice(start, start + CHUNK))
          inserted += result.inserted
        }
        toast.success(t('postgresStudio.import.done', { count: inserted }))
      }
      onImported()
      onClose()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const sqlBlocked = parsed?.kind === 'sql' && !canAdmin
  return (
    <FormDialog
      open={open}
      wide
      title={t('postgresStudio.import.title', { table: table.table })}
      description={t('postgresStudio.import.description')}
      error={error || (sqlBlocked ? t('postgresStudio.import.sqlNeedsAdmin') : null)}
      onClose={onClose}
      submitLabel={busy ? t('common.working') : t('postgresStudio.import.start')}
      onSubmit={() => !busy && !sqlBlocked && void submit()}
      testId="import-wizard"
    >
      <div className="flex flex-wrap items-center gap-3">
        <FileButton accept=".csv,.json,.sql,text/csv,application/json" onFile={(file) => void choose(file)} data-testid="import-file">
          <FileUp className="h-4 w-4" />
          {t('postgresStudio.import.pick')}
        </FileButton>
        {fileName && <span className="font-mono text-xs text-on-surface-variant">{fileName}</span>}
      </div>
      {parsed && parsed.kind !== 'sql' && (
        <>
          <p className="text-sm text-on-surface">
            {t('postgresStudio.import.summary', { count: parsed.rows.length })}
            {parsed.rows.length > CHUNK && <span className="block text-xs text-status-warning">{t('postgresStudio.import.chunked', { size: CHUNK })}</span>}
          </p>
          <div className="space-y-2">
            {parsed.headers.map((header, index) => (
              <div key={`${header}-${index}`} className="grid grid-cols-2 items-center gap-3">
                <span className="truncate font-mono text-xs text-on-surface">{header || `#${index + 1}`}</span>
                <Dropdown
                  value={mapping[index]}
                  onChange={(value) => setMapping((m) => m.map((v, i) => (i === index ? value : v)))}
                  options={[{ value: SKIP, label: t('postgresStudio.import.skip') }, ...columns.map((c) => ({ value: c, label: c }))]}
                  aria-label={t('postgresStudio.import.target', { column: header })}
                />
              </div>
            ))}
          </div>
          <EmptyAsNull value={emptyIsNull} onChange={setEmptyIsNull} />
          <div className="overflow-x-auto rounded-lg border border-outline-variant">
            <table className="w-full text-left font-mono text-xs">
              <tbody>
                {parsed.rows.slice(0, 5).map((row, i) => (
                  <tr key={i} className="border-t border-outline-variant/50">
                    {row.map((value, j) => (
                      <td key={j} className="max-w-40 truncate px-2 py-1 text-on-surface-variant">{cellText(value)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      {parsed?.kind === 'sql' && <p className="text-sm text-on-surface">{t('postgresStudio.import.sqlHint')}</p>}
    </FormDialog>
  )
}

function EmptyAsNull({ value, onChange }: { value: boolean; onChange: (value: boolean) => void }) {
  const { t } = useTranslation()
  return (
    <div className="flex items-center justify-between gap-3 rounded-lg border border-outline-variant p-3">
      <span className="text-sm text-on-surface">{t('postgresStudio.import.emptyIsNull')}</span>
      <Dropdown
        value={value ? 'null' : 'text'}
        onChange={(v) => onChange(v === 'null')}
        options={[
          { value: 'null', label: 'NULL' },
          { value: 'text', label: t('postgresStudio.import.emptyText') },
        ]}
        className="w-40"
        aria-label={t('postgresStudio.import.emptyIsNull')}
      />
    </div>
  )
}
