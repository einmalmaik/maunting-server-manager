import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { AlertCircle, Loader2 } from 'lucide-react'
import { Dropdown, Input, type DropdownOption } from '@/Singra/UI'
import type { FkAction } from './studioApi'

/** Lädt beim Einhängen und wenn sich `deps` ändern; `reload` lädt erneut. */
export function useLoad<T>(load: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const counter = useRef(0)

  const reload = useCallback(async () => {
    const id = ++counter.current
    setLoading(true)
    setError(null)
    try {
      const value = await load()
      if (id === counter.current) setData(value)
    } catch (err) {
      if (id === counter.current) setError((err as Error).message)
    } finally {
      if (id === counter.current) setLoading(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  useEffect(() => {
    void reload()
  }, [reload])

  return { data, error, loading, reload, setData }
}

export function ErrorBox({ message }: { message: string | null }) {
  if (!message) return null
  return (
    <p role="alert" className="flex items-start gap-2 whitespace-pre-wrap rounded-lg border border-status-destructive/40 bg-status-destructive/10 p-3 text-sm text-status-destructive">
      <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
      <span className="min-w-0 break-words">{message}</span>
    </p>
  )
}

export function Loading() {
  const { t } = useTranslation()
  return (
    <div className="flex items-center gap-2 p-4 text-sm text-on-surface-variant">
      <Loader2 className="h-4 w-4 animate-spin" />
      {t('common.loading')}
    </div>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="py-6 text-center text-sm italic text-on-surface-variant/80">{children}</p>
}

export function Section({ title, actions, children, className = '' }: {
  title: ReactNode
  actions?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <section className={`msm-card p-4 ${className}`}>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-headline text-base font-semibold text-on-surface">{title}</h3>
        {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
      </div>
      {children}
    </section>
  )
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-sm font-medium text-on-surface">{label}</span>
      {children}
      {hint && <span className="text-xs text-on-surface-variant">{hint}</span>}
    </div>
  )
}

export const COMMON_TYPES = [
  'bigint', 'integer', 'smallint', 'numeric(10,2)', 'real', 'double precision',
  'text', 'character varying(255)', 'boolean', 'uuid', 'jsonb', 'json',
  'timestamp with time zone', 'timestamp without time zone', 'date', 'time', 'interval',
  'bytea', 'inet', 'cidr', 'text[]', 'integer[]', 'tsvector',
]

const CUSTOM = '__custom__'

/** Datentyp aus einer Liste oder frei eingegeben (Länge, Enum, Array …). */
export function TypePicker({ value, onChange, enums = [], ariaLabel }: {
  value: string
  onChange: (value: string) => void
  enums?: string[]
  ariaLabel?: string
}) {
  const { t } = useTranslation()
  const known = [...COMMON_TYPES, ...enums]
  const [custom, setCustom] = useState(() => Boolean(value) && !known.includes(value))
  const options: DropdownOption[] = [
    ...COMMON_TYPES.map((type) => ({ value: type, label: type })),
    ...enums.map((name) => ({ value: name, label: name, hint: t('postgresStudio.types.enum') })),
    { value: CUSTOM, label: t('postgresStudio.types.custom') },
  ]
  return (
    <div className="flex min-w-0 gap-2">
      <Dropdown
        value={custom ? CUSTOM : value || null}
        onChange={(next) => {
          if (next === CUSTOM) {
            setCustom(true)
          } else {
            setCustom(false)
            onChange(next)
          }
        }}
        options={options}
        searchable
        className="min-w-0 flex-1"
        aria-label={ariaLabel || t('postgresStudio.columns.type')}
        placeholder={t('postgresStudio.types.pick')}
      />
      {custom && (
        <Input
          value={value}
          onChange={(event) => onChange(event.target.value)}
          placeholder="numeric(12,4)"
          className="font-mono text-xs"
          aria-label={t('postgresStudio.types.custom')}
        />
      )}
    </div>
  )
}

export function useFkActionOptions(): DropdownOption[] {
  const { t } = useTranslation()
  const actions: FkAction[] = ['no_action', 'restrict', 'cascade', 'set_null', 'set_default']
  return actions.map((value) => ({ value, label: t(`postgresStudio.fk.actions.${value}`) }))
}

export function formatDate(value: string | null | undefined, locale: string): string {
  if (!value) return '–'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString(locale)
}

export function formatNumber(value: number | null | undefined, locale: string): string {
  return value == null ? '–' : value.toLocaleString(locale)
}

export function formatRatio(value: number | null | undefined, locale: string): string {
  return value == null ? '–' : `${(value * 100).toLocaleString(locale, { maximumFractionDigits: 1 })} %`
}

/** Leere Namen, doppelte Namen: meldet den ersten Fehler oder null. */
export function checkNames(names: string[], t: (key: string, opts?: Record<string, unknown>) => string): string | null {
  const seen = new Set<string>()
  for (const name of names) {
    if (!name.trim()) return t('postgresStudio.validation.emptyName')
    if (seen.has(name)) return t('postgresStudio.validation.duplicateName', { name })
    seen.add(name)
  }
  return null
}
