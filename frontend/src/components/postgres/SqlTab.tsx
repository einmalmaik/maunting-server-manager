import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Gauge, History, Play } from 'lucide-react'
import { Badge, Button, Switch, Textarea } from '@/Singra/UI'
import { useStudio } from './StudioContext'
import { PlanTree } from './PlanTree'
import { Empty, ErrorBox, Section } from './shared'
import { cellText, type Json, type StudioRunResult, type StudioSqlResponse } from './studioApi'

const HISTORY_LIMIT = 30

function historyKey(serverId: number, databaseId: number) {
  return `msm-pg-studio:verlauf:${serverId}:${databaseId}`
}

function readHistory(key: string): string[] {
  try {
    const parsed = JSON.parse(localStorage.getItem(key) || '[]')
    return Array.isArray(parsed) ? parsed.filter((entry): entry is string => typeof entry === 'string') : []
  } catch {
    return []
  }
}

/** SQL-Editor: läuft als Owner, in einer Transaktion oder mit Rollback (Funktionstest). */
export function SqlTab({ initialSql = '', initialRollback = false }: { initialSql?: string; initialRollback?: boolean }) {
  const { t } = useTranslation()
  const { api, serverId, databaseId, bump } = useStudio()
  const key = historyKey(serverId, databaseId)
  const [sql, setSql] = useState(initialSql || 'SELECT now();')
  const [rollback, setRollback] = useState(initialRollback)
  const [autocommit, setAutocommit] = useState(false)
  const [result, setResult] = useState<StudioSqlResponse | null>(null)
  const [plan, setPlan] = useState<{ plan: Json; analyzed: boolean } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [history, setHistory] = useState<string[]>(() => readHistory(key))

  const remember = (text: string) => {
    const next = [text, ...history.filter((entry) => entry !== text)].slice(0, HISTORY_LIMIT)
    setHistory(next)
    try {
      localStorage.setItem(key, JSON.stringify(next))
    } catch {
      /* Speicher voll oder gesperrt: Verlauf bleibt nur in der Sitzung */
    }
  }

  const run = async () => {
    if (!sql.trim() || busy) return
    setBusy(true)
    setError(null)
    setPlan(null)
    try {
      const response = await api.sql({ sql, rollback, autocommit, row_limit: 500 })
      setResult(response)
      remember(sql)
      if (!rollback) bump()
    } catch (err) {
      setResult(null)
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const explain = async (analyze: boolean) => {
    if (!sql.trim() || busy) return
    setBusy(true)
    setError(null)
    try {
      setPlan(await api.explain(sql, analyze))
      setResult(null)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="grid gap-4 xl:grid-cols-12">
      <div className="space-y-4 xl:col-span-9">
        <Section title={t('postgresStudio.sql.title')}>
          <Textarea
            mono
            rows={12}
            value={sql}
            onChange={(e) => setSql(e.target.value)}
            onKeyDown={(e) => {
              if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
                e.preventDefault()
                void run()
              }
            }}
            aria-label={t('postgresStudio.sql.title')}
            data-testid="sql-editor"
          />
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <Button onClick={() => void run()} disabled={busy} data-testid="sql-run">
              <Play className="h-4 w-4" />
              {rollback ? t('postgresStudio.sql.runTest') : t('postgresStudio.sql.run')}
            </Button>
            <Button variant="secondary" onClick={() => void explain(false)} disabled={busy}>
              <Gauge className="h-4 w-4" />
              EXPLAIN
            </Button>
            <Button variant="secondary" onClick={() => void explain(true)} disabled={busy} data-testid="sql-explain-analyze">
              <Gauge className="h-4 w-4" />
              EXPLAIN ANALYZE
            </Button>
            <label className="flex items-center gap-2 text-xs text-on-surface">
              <Switch checked={rollback} onCheckedChange={(v) => { setRollback(v); if (v) setAutocommit(false) }} />
              {t('postgresStudio.sql.rollback')}
            </label>
            <label className="flex items-center gap-2 text-xs text-on-surface">
              <Switch checked={autocommit} onCheckedChange={(v) => { setAutocommit(v); if (v) setRollback(false) }} />
              {t('postgresStudio.sql.autocommit')}
            </label>
            <span className="text-xs text-on-surface-variant">{t('postgresStudio.sql.hint')}</span>
          </div>
        </Section>
        <ErrorBox message={error} />
        {plan && (
          <Section title={plan.analyzed ? t('postgresStudio.explain.titleAnalyze') : t('postgresStudio.explain.title')}>
            {plan.analyzed && <p className="mb-2 text-xs text-on-surface-variant">{t('postgresStudio.explain.rolledBack')}</p>}
            <PlanTree plan={plan.plan} />
          </Section>
        )}
        {result && <SqlResults response={result} />}
      </div>
      <aside className="xl:col-span-3">
        <Section title={<span className="inline-flex items-center gap-2"><History className="h-4 w-4" />{t('postgresStudio.sql.history')}</span>}>
          {history.length === 0 ? (
            <Empty>{t('postgresStudio.sql.noHistory')}</Empty>
          ) : (
            <div className="max-h-[60vh] space-y-1.5 overflow-y-auto">
              {history.map((entry) => (
                <button
                  key={entry}
                  type="button"
                  onClick={() => setSql(entry)}
                  className="w-full truncate rounded-md border border-outline-variant bg-surface-container-high p-2 text-left font-mono text-label-sm text-on-surface-variant transition hover:border-secondary/40 hover:text-on-surface"
                >
                  {entry}
                </button>
              ))}
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  setHistory([])
                  try {
                    localStorage.removeItem(key)
                  } catch {
                    /* nichts zu tun */
                  }
                }}
              >
                {t('common.clear')}
              </Button>
            </div>
          )}
        </Section>
      </aside>
    </div>
  )
}

export function SqlResults({ response }: { response: StudioSqlResponse }) {
  const { t } = useTranslation()
  return (
    <div className="space-y-3" data-testid="sql-results">
      <div className="flex flex-wrap items-center gap-2 text-xs text-on-surface-variant">
        <span>{t('postgresStudio.sql.duration', { ms: response.duration_ms })}</span>
        {response.rolled_back && <Badge variant="warning">{t('postgresStudio.sql.rolledBack')}</Badge>}
      </div>
      {response.notices.length > 0 && (
        <pre className="whitespace-pre-wrap rounded-lg border border-outline-variant bg-surface-container-lowest p-3 font-mono text-xs text-on-surface-variant">{response.notices.join('\n')}</pre>
      )}
      {response.results.map((result, index) => (
        <ResultTable key={index} index={index} result={result} />
      ))}
    </div>
  )
}

function ResultTable({ index, result }: { index: number; result: StudioRunResult }) {
  const { t } = useTranslation()
  return (
    <Section
      title={
        <span className="text-sm">
          {t('postgresStudio.sql.statement', { number: index + 1 })} · <span className="font-mono">{result.status}</span>
        </span>
      }
      actions={
        <span className="text-xs text-on-surface-variant">
          {result.columns.length ? t('postgresStudio.sql.rowCount', { count: result.rows.length }) : t('postgresStudio.sql.affected', { count: Math.max(0, result.row_count) })}
          {result.truncated && ` · ${t('postgresStudio.sql.truncated')}`}
        </span>
      }
    >
      {result.columns.length > 0 && (
        <div className="max-h-96 overflow-auto rounded-lg border border-outline-variant">
          <table className="w-full text-left font-mono text-xs">
            <thead className="sticky top-0 bg-surface-container-high text-on-surface-variant">
              <tr>
                {result.columns.map((column, i) => (
                  <th key={`${column}-${i}`} className="whitespace-nowrap px-3 py-2 font-medium">{column}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {result.rows.map((row, r) => (
                <tr key={r} className="border-t border-outline-variant/50">
                  {row.map((value, c) => (
                    <td key={c} className={`max-w-72 truncate px-3 py-1.5 ${value === null ? 'italic text-on-surface-variant/60' : 'text-on-surface'}`}>{cellText(value)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  )
}
