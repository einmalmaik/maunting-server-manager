import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Wand2 } from 'lucide-react'
import { Button, Checkbox, Dropdown, Input, Textarea } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'
import { FormDialog } from './FormDialog'
import { useStudio } from './StudioContext'
import type { Json, StudioColumn, StudioTableDetails } from './studioApi'

const isJsonType = (type: string) => /^jsonb?$/.test(type)

function toText(value: Json | undefined): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'object') return JSON.stringify(value, null, 2)
  return String(value)
}

/** JSON(B)-Zelle bearbeiten: prüft die Syntax, bevor gespeichert wird. */
export function JsonEditorDialog({ column, value, onClose, onSave }: {
  column: string
  value: Json
  onClose: () => void
  onSave: (value: Json) => void
}) {
  const { t } = useTranslation()
  const [text, setText] = useState(() => (value === null ? '' : typeof value === 'string' ? value : JSON.stringify(value, null, 2)))
  const [error, setError] = useState<string | null>(null)
  const parse = (): Json | undefined => {
    if (!text.trim()) return null
    try {
      return JSON.parse(text) as Json
    } catch (err) {
      setError(t('postgresStudio.data.invalidJson', { message: (err as Error).message }))
      return undefined
    }
  }
  return (
    <FormDialog
      open
      wide
      title={t('postgresStudio.data.jsonTitle', { column })}
      error={error}
      onClose={onClose}
      submitLabel={t('common.save')}
      onSubmit={() => {
        const parsed = parse()
        if (parsed !== undefined) onSave(parsed)
      }}
      testId="json-editor"
    >
      <Textarea mono rows={14} value={text} onChange={(e) => { setText(e.target.value); setError(null) }} aria-label={column} />
      <Button
        type="button"
        size="sm"
        variant="ghost"
        onClick={() => {
          const parsed = parse()
          if (parsed !== undefined) setText(parsed === null ? '' : JSON.stringify(parsed, null, 2))
        }}
      >
        <Wand2 className="h-3.5 w-3.5" />
        {t('postgresStudio.data.formatJson')}
      </Button>
    </FormDialog>
  )
}

interface FieldState {
  text: string
  isNull: boolean
  /** Nicht gesetzt: die Datenbank nimmt ihren Standardwert. */
  useDefault: boolean
}

/** Neue Zeile oder Kopie einer Zeile. */
export function RowDialog({ table, mode, initial, onClose, onSaved }: {
  table: StudioTableDetails
  mode: 'insert' | 'duplicate'
  initial: Record<string, Json>
  onClose: () => void
  onSaved: () => void
}) {
  const { t } = useTranslation()
  const { api } = useStudio()
  const columns = useMemo(() => table.columns.filter((c) => !c.generated && c.identity !== 'always'), [table.columns])
  const [fields, setFields] = useState<Record<string, FieldState>>(() =>
    Object.fromEntries(
      columns.map((c) => {
        const has = Object.prototype.hasOwnProperty.call(initial, c.name)
        return [c.name, { text: has ? toText(initial[c.name]) : '', isNull: has && initial[c.name] === null, useDefault: !has && (Boolean(c.default) || Boolean(c.identity) || c.nullable) }]
      }),
    ),
  )
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const set = (name: string, patch: Partial<FieldState>) => setFields((f) => ({ ...f, [name]: { ...f[name], ...patch } }))

  const submit = async () => {
    const values: Record<string, Json> = {}
    for (const column of columns) {
      const field = fields[column.name]
      if (field.useDefault) continue
      if (field.isNull) {
        values[column.name] = null
        continue
      }
      if (isJsonType(column.type)) {
        try {
          values[column.name] = field.text.trim() ? (JSON.parse(field.text) as Json) : null
        } catch (err) {
          setError(t('postgresStudio.data.invalidJsonIn', { column: column.name, message: (err as Error).message }))
          return
        }
      } else {
        values[column.name] = field.text
      }
    }
    setBusy(true)
    setError(null)
    try {
      await api.insertRow(table.schema, table.table, values)
      toast.success(t('postgresStudio.data.inserted'))
      onSaved()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <FormDialog
      open
      wide
      title={mode === 'insert' ? t('postgresStudio.data.insertTitle', { table: table.table }) : t('postgresStudio.data.duplicateTitle', { table: table.table })}
      description={t('postgresStudio.data.insertHint')}
      error={error}
      onClose={onClose}
      submitLabel={busy ? t('common.saving') : t('common.save')}
      onSubmit={() => void submit()}
      testId="row-dialog"
    >
      {columns.map((column) => (
        <RowField key={column.name} column={column} state={fields[column.name]} onChange={(patch) => set(column.name, patch)} />
      ))}
    </FormDialog>
  )
}

function RowField({ column, state, onChange }: { column: StudioColumn; state: FieldState; onChange: (patch: Partial<FieldState>) => void }) {
  const { t } = useTranslation()
  // Nur NULL sperrt das Feld. „Weglassen" ist bloß der Ausgangszustand: wer
  // tippt, meint einen Wert — sonst schrieb die Zeile stumm den Standardwert.
  const disabled = state.isNull
  const edit = (text: string) => onChange({ text, useDefault: false })
  let editor
  if (column.enum_values) {
    editor = (
      <Dropdown value={state.text || null} onChange={edit} options={column.enum_values.map((v) => ({ value: v, label: v }))} disabled={disabled} aria-label={column.name} />
    )
  } else if (column.type === 'boolean') {
    editor = (
      <Dropdown value={state.text || null} onChange={edit} options={[{ value: 'true', label: 'true' }, { value: 'false', label: 'false' }]} disabled={disabled} aria-label={column.name} />
    )
  } else if (isJsonType(column.type)) {
    editor = <Textarea mono rows={4} value={state.text} onChange={(e) => edit(e.target.value)} disabled={disabled} aria-label={column.name} />
  } else {
    editor = <Input value={state.text} onChange={(e) => edit(e.target.value)} disabled={disabled} aria-label={column.name} className="font-mono text-xs" />
  }
  return (
    <div className="space-y-1.5 rounded-lg border border-outline-variant p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-mono text-xs text-on-surface">
          {column.name} <span className="text-secondary">{column.type}</span>
        </span>
        <div className="flex items-center gap-4 text-xs text-on-surface-variant">
          {(column.default || column.identity || column.nullable) && (
            <label className="flex items-center gap-1.5">
              <Checkbox checked={state.useDefault} onCheckedChange={(useDefault) => onChange({ useDefault, isNull: false })} />
              {column.default ? t('postgresStudio.data.useDefault', { value: column.default }) : t('postgresStudio.data.omit')}
            </label>
          )}
          {column.nullable && (
            <label className="flex items-center gap-1.5">
              <Checkbox checked={state.isNull} onCheckedChange={(isNull) => onChange({ isNull, useDefault: false })} />
              NULL
            </label>
          )}
        </div>
      </div>
      {editor}
    </div>
  )
}
